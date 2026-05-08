"""``client.trades.send.{buy,sell,close_position,approve}()`` for EOA mode.

Mirrors ``src/eoa/trades/send.ts``.

The all-in-one entry point: prepare → simulate → sign → submit →
optionally wait for inclusion. Recommended for 95% of trade flows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from hexbytes import HexBytes
from web3 import AsyncWeb3
from web3.exceptions import TransactionNotFound
from web3.types import TxReceipt

from kashdao_protocol_sdk.eoa.trades.prepare import (
    prepare_approve_transaction,
    prepare_buy_transaction,
    prepare_close_position_transaction,
    prepare_sell_transaction,
)
from kashdao_protocol_sdk.eoa.trades.submit import submit_transaction
from kashdao_protocol_sdk.eoa.types import (
    EoaSignerAdapter,
    EoaSubmitOptions,
    PrepareEoaOptions,
    SendEoaOptions,
    SendEoaResult,
    SendEoaResultFireAndForget,
    SendEoaResultWaited,
    UnsignedTransaction,
)
from kashdao_protocol_sdk.shared.contracts.addresses import ProtocolAddresses
from kashdao_protocol_sdk.shared.errors import (
    KashAbortedError,
    KashChainError,
    KashSignerError,
    throw_if_aborted,
    to_kash_aborted,
)
from kashdao_protocol_sdk.shared.fees import FeeEstimate
from kashdao_protocol_sdk.shared.types import (
    BuildApproveParams,
    BuildBuyParams,
    BuildClosePositionParams,
    BuildSellParams,
    Hex,
)

_FeeEstimator = Callable[[], Awaitable[FeeEstimate]]


async def send_buy_transaction(
    web3: AsyncWeb3,
    estimate_fees: _FeeEstimator,
    signer: EoaSignerAdapter,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildBuyParams,
    options: SendEoaOptions | None = None,
    *,
    signal: asyncio.Event | None = None,
) -> SendEoaResult:
    opts = options or SendEoaOptions()
    prepared = await prepare_buy_transaction(
        web3,
        estimate_fees,
        addresses,
        signer.owner_address,
        market_address,
        params,
        _to_prepare_options(opts),
    )
    return await _dispatch(web3, signer, addresses, prepared.transaction, opts, signal)


async def send_sell_transaction(
    web3: AsyncWeb3,
    estimate_fees: _FeeEstimator,
    signer: EoaSignerAdapter,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildSellParams,
    options: SendEoaOptions | None = None,
    *,
    signal: asyncio.Event | None = None,
) -> SendEoaResult:
    opts = options or SendEoaOptions()
    prepared = await prepare_sell_transaction(
        web3,
        estimate_fees,
        addresses,
        signer.owner_address,
        market_address,
        params,
        _to_prepare_options(opts),
    )
    return await _dispatch(web3, signer, addresses, prepared.transaction, opts, signal)


async def send_close_position_transaction(
    web3: AsyncWeb3,
    estimate_fees: _FeeEstimator,
    signer: EoaSignerAdapter,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildClosePositionParams,
    options: SendEoaOptions | None = None,
    *,
    signal: asyncio.Event | None = None,
) -> SendEoaResult:
    opts = options or SendEoaOptions()
    prepared = await prepare_close_position_transaction(
        web3,
        estimate_fees,
        addresses,
        signer.owner_address,
        market_address,
        params,
        _to_prepare_options(opts),
    )
    return await _dispatch(web3, signer, addresses, prepared.transaction, opts, signal)


async def send_approve_transaction(
    web3: AsyncWeb3,
    estimate_fees: _FeeEstimator,
    signer: EoaSignerAdapter,
    addresses: ProtocolAddresses,
    params: BuildApproveParams,
    options: SendEoaOptions | None = None,
    *,
    signal: asyncio.Event | None = None,
) -> SendEoaResult:
    opts = options or SendEoaOptions()
    prepared = await prepare_approve_transaction(
        web3,
        estimate_fees,
        addresses,
        signer.owner_address,
        params,
        _to_prepare_options(opts),
    )
    return await _dispatch(web3, signer, addresses, prepared.transaction, opts, signal)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _to_prepare_options(opts: SendEoaOptions) -> PrepareEoaOptions:
    """Map ``SendEoaOptions`` → ``PrepareEoaOptions`` (1:1 pass-through).

    ``SendEoaOptions.simulate`` already defaults to ``True`` (matching
    the TS reference); the caller's value is propagated as-is so opting
    out (``simulate=False``) is honored.
    """
    return PrepareEoaOptions(
        gas=opts.gas,
        fee_overrides=opts.fee_overrides,
        nonce=opts.nonce,
        simulate=opts.simulate,
    )


async def _dispatch(
    web3: AsyncWeb3,
    signer: EoaSignerAdapter,
    addresses: ProtocolAddresses,
    transaction: UnsignedTransaction,
    opts: SendEoaOptions,
    signal: asyncio.Event | None,
) -> SendEoaResult:
    throw_if_aborted(signal, "aborted before signing")

    try:
        signed = await signer.sign_transaction(transaction)
    except asyncio.CancelledError as cause:
        raise to_kash_aborted(cause, "signer aborted by signal") from cause
    except Exception as cause:
        if signal is not None and getattr(signal, "is_set", lambda: False)():
            raise to_kash_aborted(cause, "signer aborted by signal") from cause
        raise KashSignerError(
            f"signer.sign_transaction failed for owner {signer.owner_address}",
            code="SIGNER_SIGN_FAILED",
            context={"owner_address": signer.owner_address, "to": transaction.to},
            cause=cause,
        ) from cause

    throw_if_aborted(signal, "aborted before sendRawTransaction")

    submit_opts = opts.submit if opts.submit is not None else EoaSubmitOptions()
    submit_result = await submit_transaction(
        web3,
        addresses.chain_id,
        signer.owner_address,
        signed,
        submit_opts,
    )

    if not opts.wait:
        return SendEoaResultFireAndForget(transaction_hash=submit_result.transaction_hash)

    try:
        receipt = await _wait_for_receipt(
            web3,
            submit_result.transaction_hash,
            opts.wait_timeout_ms,
            opts.wait_confirmations,
            signal,
        )
    except KashAbortedError:
        raise
    except Exception as cause:
        if signal is not None and getattr(signal, "is_set", lambda: False)():
            raise to_kash_aborted(
                cause,
                f"aborted while waiting for tx {submit_result.transaction_hash}",
            ) from cause
        raise KashChainError(
            f"timed out waiting for tx {submit_result.transaction_hash} on "
            f"chain {addresses.chain_id}",
            code="WAIT_RECEIPT_FAILED",
            context={
                "transaction_hash": submit_result.transaction_hash,
                "chain_id": addresses.chain_id,
            },
            cause=cause,
            is_retryable=True,
        ) from cause

    return SendEoaResultWaited(
        transaction_hash=submit_result.transaction_hash,
        block_number=int(receipt["blockNumber"]),
        success=int(receipt.get("status", 0)) == 1,
        gas_used=int(receipt["gasUsed"]),
    )


async def _wait_for_receipt(
    web3: AsyncWeb3,
    tx_hash: str,
    timeout_ms: int,
    confirmations: int,
    signal: asyncio.Event | None,
) -> TxReceipt:
    """Poll for the receipt with cancellation awareness.

    Returns the raw web3.py ``TxReceipt`` TypedDict; callers are
    expected to read fields by name (``receipt["blockNumber"]`` etc.).

    Resilience contract:

    * ``TransactionNotFound`` is the only exception swallowed during
      receipt polling — every other RPC exception propagates so that
      misconfigured endpoints surface immediately.
    * Once we hold a receipt, transient RPC failures during the
      confirmations check are not fatal — we keep the receipt and
      retry the block-number read until the deadline.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (timeout_ms / 1000.0)
    poll_interval = 1.0
    tx_hash_bytes = HexBytes(tx_hash)
    receipt: TxReceipt | None = None

    while True:
        throw_if_aborted(signal, f"aborted while waiting for tx {tx_hash}")

        if receipt is None:
            try:
                receipt = await web3.eth.get_transaction_receipt(tx_hash_bytes)
            except TransactionNotFound:
                receipt = None

        if receipt is not None:
            if confirmations <= 1:
                return receipt
            try:
                current = await web3.eth.block_number
            except Exception:
                # Receipt is in hand; treat block-number RPC blips as
                # transient and retry until the deadline.
                current = None
            if current is not None and current - int(receipt["blockNumber"]) + 1 >= confirmations:
                return receipt

        if loop.time() >= deadline:
            raise TimeoutError(f"timed out waiting for tx {tx_hash}")

        try:
            wait_event = signal if signal is not None else asyncio.Event()
            await asyncio.wait_for(wait_event.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError as cause:
            raise to_kash_aborted(cause, f"aborted while waiting for tx {tx_hash}") from cause


__all__ = [
    "send_approve_transaction",
    "send_buy_transaction",
    "send_close_position_transaction",
    "send_sell_transaction",
]
