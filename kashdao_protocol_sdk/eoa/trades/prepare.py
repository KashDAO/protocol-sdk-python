"""``client.trades.prepare_*_transaction()`` for EOA mode.

Mirrors ``src/eoa/trades/prepare.ts``.

Collapses build → estimate gas → estimate fees → recompute hash into a
single call. Returns a :class:`BuiltTransaction` with all gas + fee
fields populated and the hash recomputed against the final tx — the
staleness check on submit can never fail on this path.

Optional ``simulate=True`` runs an ``eth_call`` pre-flight before
returning, so the consumer doesn't pay signing-infra round-trips on a
tx that will revert.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

from eth_typing import HexStr
from web3 import AsyncWeb3
from web3.types import TxParams, Wei

from kashdao_protocol_sdk.eoa.trades.build import (
    build_approve_transaction,
    build_buy_transaction,
    build_close_position_transaction,
    build_sell_transaction,
)
from kashdao_protocol_sdk.eoa.trades.hash import compute_transaction_hash
from kashdao_protocol_sdk.eoa.trades.simulate import simulate_transaction
from kashdao_protocol_sdk.eoa.types import (
    BuiltTransaction,
    EoaTxOverrides,
    PrepareEoaFeeOverrides,
    PrepareEoaOptions,
)
from kashdao_protocol_sdk.shared.contracts.addresses import ProtocolAddresses
from kashdao_protocol_sdk.shared.errors import KashChainError, KashSimulationRevertedError
from kashdao_protocol_sdk.shared.fees import FeeEstimate
from kashdao_protocol_sdk.shared.types import (
    BuildApproveParams,
    BuildBuyParams,
    BuildClosePositionParams,
    BuildSellParams,
    Hex,
)

_FeeEstimator = Callable[[], Awaitable[FeeEstimate]]


async def prepare_buy_transaction(
    web3: AsyncWeb3,
    estimate_fees: _FeeEstimator,
    addresses: ProtocolAddresses,
    owner_address: Hex,
    market_address: Hex,
    params: BuildBuyParams,
    options: PrepareEoaOptions | None = None,
) -> BuiltTransaction:
    opts = options or PrepareEoaOptions()
    built = await build_buy_transaction(
        web3,
        addresses,
        owner_address,
        market_address,
        params,
        _build_overrides(opts),
    )
    return await _populate(web3, estimate_fees, owner_address, built, opts)


async def prepare_sell_transaction(
    web3: AsyncWeb3,
    estimate_fees: _FeeEstimator,
    addresses: ProtocolAddresses,
    owner_address: Hex,
    market_address: Hex,
    params: BuildSellParams,
    options: PrepareEoaOptions | None = None,
) -> BuiltTransaction:
    opts = options or PrepareEoaOptions()
    built = await build_sell_transaction(
        web3, addresses, owner_address, market_address, params, _build_overrides(opts)
    )
    return await _populate(web3, estimate_fees, owner_address, built, opts)


async def prepare_approve_transaction(
    web3: AsyncWeb3,
    estimate_fees: _FeeEstimator,
    addresses: ProtocolAddresses,
    owner_address: Hex,
    params: BuildApproveParams,
    options: PrepareEoaOptions | None = None,
) -> BuiltTransaction:
    opts = options or PrepareEoaOptions()
    built = await build_approve_transaction(
        web3, addresses, owner_address, params, _build_overrides(opts)
    )
    return await _populate(web3, estimate_fees, owner_address, built, opts)


async def prepare_close_position_transaction(
    web3: AsyncWeb3,
    estimate_fees: _FeeEstimator,
    addresses: ProtocolAddresses,
    owner_address: Hex,
    market_address: Hex,
    params: BuildClosePositionParams,
    options: PrepareEoaOptions | None = None,
) -> BuiltTransaction:
    opts = options or PrepareEoaOptions()
    built = await build_close_position_transaction(
        web3,
        addresses,
        owner_address,
        market_address,
        params,
        _build_overrides(opts),
    )
    return await _populate(web3, estimate_fees, owner_address, built, opts)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_overrides(opts: PrepareEoaOptions) -> EoaTxOverrides:
    """Pass nonce through to the builder; gas + fees we always populate here."""
    return EoaTxOverrides(nonce=opts.nonce) if opts.nonce is not None else EoaTxOverrides()


async def _populate(
    web3: AsyncWeb3,
    estimate_fees: _FeeEstimator,
    owner_address: Hex,
    built: BuiltTransaction,
    opts: PrepareEoaOptions,
) -> BuiltTransaction:
    gas, fees = await asyncio.gather(
        _estimate_gas_or_use_override(web3, owner_address, built, opts.gas),
        _estimate_fees_or_use_overrides(estimate_fees, opts.fee_overrides),
    )
    populated = built.transaction.model_copy(
        update={
            "gas": gas,
            "max_fee_per_gas": fees.max_fee_per_gas,
            "max_priority_fee_per_gas": fees.max_priority_fee_per_gas,
        }
    )
    transaction_hash = compute_transaction_hash(populated)

    if opts.simulate:
        result = await simulate_transaction(web3, populated)
        if not getattr(result, "will_succeed", False):
            reason = getattr(result, "revert_reason", "unknown")
            decoded = getattr(result, "decoded_error", None)
            raise KashSimulationRevertedError(
                f"prepared transaction will revert: {reason}",
                code="SIMULATION_REVERTED",
                context={
                    "from": owner_address,
                    "to": populated.to,
                    "revert_reason": reason,
                    "decoded_error": decoded,
                },
            )

    return BuiltTransaction(transaction=populated, transaction_hash=transaction_hash)


async def _estimate_gas_or_use_override(
    web3: AsyncWeb3,
    owner_address: Hex,
    built: BuiltTransaction,
    override: int | None,
) -> int:
    if override is not None:
        return override
    transaction = built.transaction
    if not transaction.to:
        raise KashChainError(
            "cannot estimate gas: transaction has no `to` field",
            code="GAS_ESTIMATE_NO_TO",
            context={"from": owner_address},
        )
    try:
        params: TxParams = {
            "from": AsyncWeb3.to_checksum_address(owner_address),
            "to": AsyncWeb3.to_checksum_address(transaction.to),
            "data": cast(HexStr, transaction.data or "0x"),
            "value": Wei(transaction.value or 0),
        }
        return int(await web3.eth.estimate_gas(params))
    except Exception as cause:
        raise KashChainError(
            f"eth_estimateGas failed for tx to {transaction.to}",
            code="GAS_ESTIMATE_FAILED",
            context={"from": owner_address, "to": transaction.to},
            cause=cause,
            is_retryable=True,
        ) from cause


async def _estimate_fees_or_use_overrides(
    estimate_fees: _FeeEstimator,
    overrides: PrepareEoaFeeOverrides | None,
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
    "prepare_approve_transaction",
    "prepare_buy_transaction",
    "prepare_close_position_transaction",
    "prepare_sell_transaction",
]
