"""``client.trades.prepare_*`` for smart-account mode.

Mirrors ``src/smart-account/trades/prepare.ts``.

Collapses the five-step preparation sequence — build → estimate gas →
estimate fees → spread → recompute hash — into a single call. For 95 %
of trades this is the right entry point; the underlying ``build_*``
functions are preserved for advanced consumers who want to inject
custom orchestration between steps.

Critically, ``prepare_*`` recomputes the userOp hash AFTER populating
gas + fees. This makes the build-time-staleness bug structurally
impossible to hit on the recommended path: the hash returned to the
consumer is always the hash of the userOp the bundler will receive.

Optional ``simulate=True`` runs an ``eth_call`` pre-flight against the
Market contract before returning, so the consumer doesn't pay
signing-infra round-trips on a UserOp that will revert.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from web3 import AsyncWeb3

from kashdao_protocol_sdk.shared.contracts.addresses import ProtocolAddresses
from kashdao_protocol_sdk.shared.contracts.decoders import actionable_revert_hint
from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import (
    KashChainError,
    KashSimulationRevertedError,
)
from kashdao_protocol_sdk.shared.fees import FeeEstimate
from kashdao_protocol_sdk.shared.types import (
    BuildApproveParams,
    BuildBuyParams,
    BuildClosePositionParams,
    BuildSellParams,
    Hex,
    SimulationFailure,
)
from kashdao_protocol_sdk.smart_account.account.address import is_smart_account_deployed
from kashdao_protocol_sdk.smart_account.account.init_code import encode_create_account
from kashdao_protocol_sdk.smart_account.bundler.generic import BundlerClient
from kashdao_protocol_sdk.smart_account.trades.build import (
    PaymasterConfig,
    build_approve_user_op,
    build_buy_user_op,
    build_close_position_user_op,
    build_sell_user_op,
)
from kashdao_protocol_sdk.smart_account.trades.hash import (
    compute_user_op_hash,
    user_op_to_typed_data,
)
from kashdao_protocol_sdk.smart_account.trades.simulate import simulate_user_op
from kashdao_protocol_sdk.smart_account.types import (
    BuildOptions,
    BuiltUserOp,
    GasEstimate,
    GasOverrides,
    PrepareFeeOverrides,
    UnsignedUserOp,
)


@dataclass(frozen=True, slots=True)
class PrepareUserOpOptions:
    """Per-call options for the SA-mode ``prepare_*`` lifecycle.

    Mirrors the TS reference's ``PrepareOptions`` shape (Python-distinct
    name to avoid clashing with the EOA-mode ``PrepareEoaOptions``).

    ``simulate`` is tri-state: ``None`` means "use the default for this
    code path" (``send.*`` defaults to ``True``, direct ``prepare_*``
    callers default to ``False``); ``True`` / ``False`` are explicit.
    This avoids the silent-disable trap where a caller passing any
    other field (e.g. ``nonce_key=42``) would receive Pydantic's
    ``False`` default for ``simulate`` and lose the pre-flight
    guardrail unintentionally.
    """

    gas_overrides: GasOverrides | None = None
    fee_overrides: PrepareFeeOverrides | None = None
    simulate: bool | None = None
    paymaster: PaymasterConfig | None = None
    auto_deploy: bool = False
    owner_address: Hex | None = None
    salt: int = 0
    """CREATE2 salt for SimpleAccount address derivation. Distinct
    from ``nonce_key`` — see :class:`BuildOptions`."""

    nonce_key: int = 0
    """EntryPoint v0.7 nonce stream key (uint192). Default 0.
    Forwarded to :class:`BuildOptions.nonce_key`."""


_FeeEstimator = Callable[[], Awaitable[FeeEstimate]]


async def prepare_buy_user_op(
    web3: AsyncWeb3,
    bundler: BundlerClient,
    estimate_fees: _FeeEstimator,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildBuyParams,
    options: PrepareUserOpOptions | None = None,
) -> BuiltUserOp:
    """Build → estimate → recompute hash for a BUY UserOp."""
    opts = options or PrepareUserOpOptions()
    built = await build_buy_user_op(
        web3,
        addresses,
        market_address,
        params,
        options=BuildOptions(gas=opts.gas_overrides, nonce_key=opts.nonce_key),
        paymaster=opts.paymaster,
    )
    return await _populate(web3, bundler, estimate_fees, addresses, built, opts)


async def prepare_sell_user_op(
    web3: AsyncWeb3,
    bundler: BundlerClient,
    estimate_fees: _FeeEstimator,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildSellParams,
    options: PrepareUserOpOptions | None = None,
) -> BuiltUserOp:
    """Build → estimate → recompute hash for a SELL UserOp."""
    opts = options or PrepareUserOpOptions()
    built = await build_sell_user_op(
        web3,
        addresses,
        market_address,
        params,
        options=BuildOptions(gas=opts.gas_overrides, nonce_key=opts.nonce_key),
        paymaster=opts.paymaster,
    )
    return await _populate(web3, bundler, estimate_fees, addresses, built, opts)


async def prepare_approve_user_op(
    web3: AsyncWeb3,
    bundler: BundlerClient,
    estimate_fees: _FeeEstimator,
    addresses: ProtocolAddresses,
    params: BuildApproveParams,
    options: PrepareUserOpOptions | None = None,
) -> BuiltUserOp:
    """Build → estimate → recompute hash for an APPROVE UserOp."""
    opts = options or PrepareUserOpOptions()
    built = await build_approve_user_op(
        web3,
        addresses,
        params,
        options=BuildOptions(gas=opts.gas_overrides, nonce_key=opts.nonce_key),
        paymaster=opts.paymaster,
    )
    return await _populate(web3, bundler, estimate_fees, addresses, built, opts)


async def prepare_close_position_user_op(
    web3: AsyncWeb3,
    bundler: BundlerClient,
    estimate_fees: _FeeEstimator,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildClosePositionParams,
    options: PrepareUserOpOptions | None = None,
) -> BuiltUserOp:
    """Build → estimate → recompute hash for a close-position UserOp."""
    opts = options or PrepareUserOpOptions()
    built = await build_close_position_user_op(
        web3,
        addresses,
        market_address,
        params,
        options=BuildOptions(gas=opts.gas_overrides, nonce_key=opts.nonce_key),
        paymaster=opts.paymaster,
    )
    return await _populate(web3, bundler, estimate_fees, addresses, built, opts)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _populate(
    web3: AsyncWeb3,
    bundler: BundlerClient,
    estimate_fees: _FeeEstimator,
    addresses: ProtocolAddresses,
    built: BuiltUserOp,
    options: PrepareUserOpOptions,
) -> BuiltUserOp:
    init_fields = await _maybe_auto_deploy_fields(web3, addresses, built, options)
    user_op = built.user_op
    if init_fields is not None:
        user_op = user_op.model_copy(update=init_fields)

    # Gas estimation (bundler) + fee estimation (chain RPC) are
    # independent — run in parallel.
    gas_task = asyncio.create_task(
        _estimate_gas_or_use_overrides(bundler, user_op, options.gas_overrides)
    )
    fee_task = asyncio.create_task(
        _estimate_fees_or_use_overrides(estimate_fees, options.fee_overrides)
    )
    gas, fees = await asyncio.gather(gas_task, fee_task)

    populated = user_op.model_copy(
        update={
            "call_gas_limit": gas["call_gas_limit"],
            "verification_gas_limit": gas["verification_gas_limit"],
            "pre_verification_gas": gas["pre_verification_gas"],
            "max_fee_per_gas": fees.max_fee_per_gas,
            "max_priority_fee_per_gas": fees.max_priority_fee_per_gas,
        }
    )

    # Recompute hash AFTER population — the structural fix that makes
    # the build-time-staleness bug impossible on the prepare path.
    user_op_hash = compute_user_op_hash(
        addresses.chain_id,
        addresses.smart_account.entry_point_address,
        populated,
    )
    typed_data = user_op_to_typed_data(
        addresses.chain_id,
        addresses.smart_account.entry_point_address,
        populated,
    )

    if options.simulate:
        result = await simulate_user_op(web3, populated)
        if isinstance(result, SimulationFailure):
            decoded_name = result.decoded_error.name if result.decoded_error is not None else None
            hint = actionable_revert_hint(decoded_name) or actionable_revert_hint(
                result.revert_reason
            )
            message = (
                f"prepared UserOp will revert: {result.revert_reason} - {hint}"
                if hint is not None
                else f"prepared UserOp will revert: {result.revert_reason}"
            )
            context: dict[str, object] = {
                "sender": populated.sender,
                "revert_reason": result.revert_reason,
                "decoded_error": result.decoded_error,
            }
            if hint is not None:
                context["hint"] = hint
            raise KashSimulationRevertedError(
                message,
                code=ErrorCode.SIMULATION_REVERTED,
                context=context,
            )

    return BuiltUserOp(user_op=populated, user_op_hash=user_op_hash, typed_data=typed_data)


async def _maybe_auto_deploy_fields(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    built: BuiltUserOp,
    options: PrepareUserOpOptions,
) -> dict[str, str] | None:
    """If auto-deploy is enabled and the SA has no code, return ``factory`` + ``factoryData``."""
    if not options.auto_deploy:
        return None
    # Respect consumer-supplied init code if already populated.
    if built.user_op.factory is not None or built.user_op.factory_data is not None:
        return None
    if options.owner_address is None:
        raise KashChainError(
            "auto_deploy requires owner_address (the EOA the SimpleAccountFactory "
            "will deploy an SA for)",
            code=ErrorCode.AUTO_DEPLOY_OWNER_REQUIRED,
            context={"sender": built.user_op.sender},
        )
    deployed = await is_smart_account_deployed(web3, built.user_op.sender)
    if deployed:
        return None
    return {
        "factory": addresses.smart_account.factory_address,
        "factory_data": encode_create_account(options.owner_address, options.salt),
    }


async def _estimate_gas_or_use_overrides(
    bundler: BundlerClient,
    user_op: UnsignedUserOp,
    overrides: GasOverrides | None,
) -> dict[str, int]:
    """Collapse partial overrides + bundler estimate into final values."""
    if (
        overrides is not None
        and overrides.call_gas_limit is not None
        and overrides.verification_gas_limit is not None
        and overrides.pre_verification_gas is not None
    ):
        return {
            "call_gas_limit": overrides.call_gas_limit,
            "verification_gas_limit": overrides.verification_gas_limit,
            "pre_verification_gas": overrides.pre_verification_gas,
        }
    estimated: GasEstimate = await bundler.estimate_gas(user_op)
    return {
        "call_gas_limit": (
            overrides.call_gas_limit
            if overrides is not None and overrides.call_gas_limit is not None
            else estimated.call_gas_limit
        ),
        "verification_gas_limit": (
            overrides.verification_gas_limit
            if overrides is not None and overrides.verification_gas_limit is not None
            else estimated.verification_gas_limit
        ),
        "pre_verification_gas": (
            overrides.pre_verification_gas
            if overrides is not None and overrides.pre_verification_gas is not None
            else estimated.pre_verification_gas
        ),
    }


async def _estimate_fees_or_use_overrides(
    estimate_fees: _FeeEstimator,
    overrides: PrepareFeeOverrides | None,
) -> FeeEstimate:
    if (
        overrides is not None
        and overrides.max_fee_per_gas is not None
        and overrides.max_priority_fee_per_gas is not None
    ):
        return FeeEstimate(
            max_fee_per_gas=overrides.max_fee_per_gas,
            max_priority_fee_per_gas=overrides.max_priority_fee_per_gas,
        )
    estimated = await estimate_fees()
    return FeeEstimate(
        max_fee_per_gas=(
            overrides.max_fee_per_gas
            if overrides is not None and overrides.max_fee_per_gas is not None
            else estimated.max_fee_per_gas
        ),
        max_priority_fee_per_gas=(
            overrides.max_priority_fee_per_gas
            if overrides is not None and overrides.max_priority_fee_per_gas is not None
            else estimated.max_priority_fee_per_gas
        ),
    )


__all__ = [
    "PrepareUserOpOptions",
    "prepare_approve_user_op",
    "prepare_buy_user_op",
    "prepare_close_position_user_op",
    "prepare_sell_user_op",
]
