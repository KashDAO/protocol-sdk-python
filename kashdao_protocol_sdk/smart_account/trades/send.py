"""``client.trades.send.{buy,sell,close_position,approve}`` for SA mode.

Mirrors ``src/smart-account/trades/send.ts``.

The all-in-one entry point: prepare → optionally simulate → sign →
submit → wait for inclusion. For consumers who don't need to inject
orchestration between steps, this is the one-line trade.

The flow:

1. ``prepare_*`` — build, populate gas + fees, recompute hash.
2. (Optional) ``simulate=True`` already runs inside ``prepare``.
3. ``signer.sign_user_op_hash(user_op_hash)`` — typed errors on failure.
4. ``submit(signed_user_op)`` — staleness guard + bundler forward.
5. (Default) ``bundler.wait_for_receipt(user_op_hash)`` — poll until
   inclusion or timeout.

Returns the ``user_op_hash`` and (when ``wait=True``) the
:class:`UserOpReceipt`. With ``wait=False``, returns just
:class:`SendResultFireAndForget`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from kashdao_protocol_sdk.shared.contracts.addresses import ProtocolAddresses
from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import (
    KashAbortedError,
    KashBundlerError,
    KashSignerError,
    throw_if_aborted,
    to_kash_aborted,
)
from kashdao_protocol_sdk.shared.fees import FeeEstimate
from kashdao_protocol_sdk.shared.hooks import (
    KashProtocolHooks,
    SmartAccountWaitOrphanedEvent,
    safe_fire,
)
from kashdao_protocol_sdk.shared.types import (
    BuildApproveParams,
    BuildBuyParams,
    BuildClosePositionParams,
    BuildSellParams,
    Hex,
)
from kashdao_protocol_sdk.smart_account.bundler.generic import BundlerClient
from kashdao_protocol_sdk.smart_account.trades.prepare import (
    PrepareUserOpOptions,
    prepare_approve_user_op,
    prepare_buy_user_op,
    prepare_close_position_user_op,
    prepare_sell_user_op,
)
from kashdao_protocol_sdk.smart_account.trades.submit import submit_user_op
from kashdao_protocol_sdk.smart_account.types import (
    BuiltUserOp,
    SendResult,
    SendResultFireAndForget,
    SendResultWaited,
    SignedUserOp,
    SmartAccountSignerAdapter,
    SubmitOptions,
)

_FeeEstimator = Callable[[], Awaitable[FeeEstimate]]


# Send-path kwargs (wait / wait_timeout_seconds / submit_options /
# signal / hooks) are accepted individually at the function boundary
# rather than wrapped in a Pydantic ``SendOptions`` model — fewer
# layers, cleaner Hummingbot strategy call sites. The TS reference
# uses ``SendOptions`` because it composes via spread (`{ ...rest }`);
# Python's keyword args fill the same role with explicit signatures.


async def send_buy_user_op(
    web3: Any,
    bundler: BundlerClient,
    estimate_fees: _FeeEstimator,
    signer: SmartAccountSignerAdapter,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildBuyParams,
    *,
    prepare_options: PrepareUserOpOptions | None = None,
    submit_options: SubmitOptions | None = None,
    wait: bool = True,
    wait_timeout_seconds: float = 90.0,
    wait_interval_seconds: float = 2.0,
    signal: object | None = None,
    hooks: KashProtocolHooks | None = None,
) -> SendResult:
    """Build → simulate → sign → submit → wait, for a BUY UserOp."""
    prepared = await prepare_buy_user_op(
        web3,
        bundler,
        estimate_fees,
        addresses,
        market_address,
        params,
        options=_with_simulate(prepare_options),
    )
    return await _dispatch(
        bundler,
        signer,
        addresses,
        prepared,
        submit_options=submit_options,
        wait=wait,
        wait_timeout_seconds=wait_timeout_seconds,
        wait_interval_seconds=wait_interval_seconds,
        signal=signal,
        hooks=hooks,
    )


async def send_sell_user_op(
    web3: Any,
    bundler: BundlerClient,
    estimate_fees: _FeeEstimator,
    signer: SmartAccountSignerAdapter,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildSellParams,
    *,
    prepare_options: PrepareUserOpOptions | None = None,
    submit_options: SubmitOptions | None = None,
    wait: bool = True,
    wait_timeout_seconds: float = 90.0,
    wait_interval_seconds: float = 2.0,
    signal: object | None = None,
    hooks: KashProtocolHooks | None = None,
) -> SendResult:
    """Build → simulate → sign → submit → wait, for a SELL UserOp."""
    prepared = await prepare_sell_user_op(
        web3,
        bundler,
        estimate_fees,
        addresses,
        market_address,
        params,
        options=_with_simulate(prepare_options),
    )
    return await _dispatch(
        bundler,
        signer,
        addresses,
        prepared,
        submit_options=submit_options,
        wait=wait,
        wait_timeout_seconds=wait_timeout_seconds,
        wait_interval_seconds=wait_interval_seconds,
        signal=signal,
        hooks=hooks,
    )


async def send_approve_user_op(
    web3: Any,
    bundler: BundlerClient,
    estimate_fees: _FeeEstimator,
    signer: SmartAccountSignerAdapter,
    addresses: ProtocolAddresses,
    params: BuildApproveParams,
    *,
    prepare_options: PrepareUserOpOptions | None = None,
    submit_options: SubmitOptions | None = None,
    wait: bool = True,
    wait_timeout_seconds: float = 90.0,
    wait_interval_seconds: float = 2.0,
    signal: object | None = None,
    hooks: KashProtocolHooks | None = None,
) -> SendResult:
    """Build → simulate → sign → submit → wait, for an APPROVE UserOp."""
    prepared = await prepare_approve_user_op(
        web3,
        bundler,
        estimate_fees,
        addresses,
        params,
        options=_with_simulate(prepare_options),
    )
    return await _dispatch(
        bundler,
        signer,
        addresses,
        prepared,
        submit_options=submit_options,
        wait=wait,
        wait_timeout_seconds=wait_timeout_seconds,
        wait_interval_seconds=wait_interval_seconds,
        signal=signal,
        hooks=hooks,
    )


async def send_close_position_user_op(
    web3: Any,
    bundler: BundlerClient,
    estimate_fees: _FeeEstimator,
    signer: SmartAccountSignerAdapter,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildClosePositionParams,
    *,
    prepare_options: PrepareUserOpOptions | None = None,
    submit_options: SubmitOptions | None = None,
    wait: bool = True,
    wait_timeout_seconds: float = 90.0,
    wait_interval_seconds: float = 2.0,
    signal: object | None = None,
    hooks: KashProtocolHooks | None = None,
) -> SendResult:
    """Build → simulate → sign → submit → wait, for a close-position UserOp."""
    prepared = await prepare_close_position_user_op(
        web3,
        bundler,
        estimate_fees,
        addresses,
        market_address,
        params,
        options=_with_simulate(prepare_options),
    )
    return await _dispatch(
        bundler,
        signer,
        addresses,
        prepared,
        submit_options=submit_options,
        wait=wait,
        wait_timeout_seconds=wait_timeout_seconds,
        wait_interval_seconds=wait_interval_seconds,
        signal=signal,
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _with_simulate(opts: PrepareUserOpOptions | None) -> PrepareUserOpOptions:
    """``simulate`` defaults to True on the send path (TS parity).

    ``PrepareUserOpOptions.simulate`` is tri-state (``bool | None``):
    only ``False`` is treated as "explicit opt-out". ``None`` (the
    default) resolves to ``True`` here — matching TS's
    ``{ simulate: true, ...rest }`` spread. This makes it impossible
    for an unrelated tweak (e.g. ``nonce_key=42``) to silently disable
    the pre-flight guardrail.
    """
    from dataclasses import replace as dc_replace

    if opts is None:
        return PrepareUserOpOptions(simulate=True)
    if opts.simulate is None:
        # Caller didn't set simulate explicitly — apply the send-path
        # default (True). Use ``replace`` so other fields are preserved.
        return dc_replace(opts, simulate=True)
    return opts


async def _dispatch(
    bundler: BundlerClient,
    signer: SmartAccountSignerAdapter,
    addresses: ProtocolAddresses,
    prepared: BuiltUserOp,
    *,
    submit_options: SubmitOptions | None,
    wait: bool,
    wait_timeout_seconds: float,
    wait_interval_seconds: float,
    signal: object | None,
    hooks: KashProtocolHooks | None,
) -> SendResult:
    throw_if_aborted(signal, "aborted before signing")

    try:
        signature = await signer.sign_user_op_hash(prepared.user_op_hash)
    except asyncio.CancelledError as cause:
        raise to_kash_aborted(cause, "signer aborted by signal") from cause
    except Exception as cause:
        if signal is not None and _signal_is_set(signal):
            raise to_kash_aborted(cause, "signer aborted by signal") from cause
        raise KashSignerError(
            f"signer.sign_user_op_hash failed for owner {signer.owner_address}",
            code=ErrorCode.SIGNER_SIGN_FAILED,
            context={
                "owner_address": signer.owner_address,
                "user_op_hash": prepared.user_op_hash,
            },
            cause=cause,
        ) from cause

    throw_if_aborted(signal, "aborted before bundler submit")

    signed = SignedUserOp(user_op=prepared.user_op, signature=signature)
    submit_result = await submit_user_op(
        bundler,
        addresses.chain_id,
        addresses.smart_account.entry_point_address,
        signer.owner_address,
        signed,
        submit_options,
    )

    if not wait:
        return SendResultFireAndForget(user_op_hash=submit_result.user_op_hash)

    # When the consumer supplies a signal but no explicit wait timeout,
    # shorten the default to keep the orphan window bounded under
    # high-cancel-rate workloads (matches the TS reference).
    effective_timeout = wait_timeout_seconds if signal is None else min(wait_timeout_seconds, 15.0)

    try:
        receipt = await bundler.wait_for_receipt(
            submit_result.user_op_hash,
            timeout_seconds=effective_timeout,
            interval_seconds=wait_interval_seconds,
            signal=signal,
        )
    except KashAbortedError:
        if hooks is not None:
            safe_fire(
                getattr(hooks, "on_wait_orphaned", None),
                SmartAccountWaitOrphanedEvent(
                    user_op_hash=submit_result.user_op_hash,
                    chain_id=addresses.chain_id,
                    wait_timeout_ms=int(effective_timeout * 1000),
                    reason="aborted",
                ),
            )
        raise
    except KashBundlerError as cause:
        if cause.code == ErrorCode.BUNDLER_RECEIPT_TIMEOUT and hooks is not None:
            safe_fire(
                getattr(hooks, "on_wait_orphaned", None),
                SmartAccountWaitOrphanedEvent(
                    user_op_hash=submit_result.user_op_hash,
                    chain_id=addresses.chain_id,
                    wait_timeout_ms=int(effective_timeout * 1000),
                    reason="timeout",
                ),
            )
        raise

    return SendResultWaited(
        user_op_hash=submit_result.user_op_hash,
        receipt=receipt,
    )


def _signal_is_set(signal: object) -> bool:
    aborted = getattr(signal, "aborted", None)
    if aborted is True:
        return True
    is_set = getattr(signal, "is_set", None)
    if callable(is_set):
        return bool(is_set())
    return False


__all__ = [
    "send_approve_user_op",
    "send_buy_user_op",
    "send_close_position_user_op",
    "send_sell_user_op",
]
