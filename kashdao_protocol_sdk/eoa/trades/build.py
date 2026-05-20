"""Unsigned EIP-1559 transaction builders for buy / sell / close / approve.

Mirrors ``src/eoa/trades/build.ts``.

Each builder:
1. Reads an on-chain quote via ``Market.quoteBuy/Sell…`` for
   slippage-protected ``min_out`` calculation.
2. Encodes calldata for ``Market.buyExactAssetsIn`` /
   ``Market.sellExactTokensIn`` directly (no SimpleAccount wrap; the
   EOA calls Market itself).
3. Reads the EOA's transaction count from the chain RPC for the
   next-sequential nonce.
4. Returns an unsigned EIP-1559 tx with consumer-supplied gas/fee
   defaults (zeroed unless overridden) plus the canonical
   ``transaction_hash`` for tracing.

**Build-time hash is stale once gas/fees populate.** Consumers must
call ``client.trades.hash_of(transaction)`` (or use
``prepare_*_transaction`` which handles it).
"""

from __future__ import annotations

import time
from typing import Any

from eth_abi import encode as abi_encode  # type: ignore[attr-defined]
from eth_utils import keccak, to_bytes  # type: ignore[attr-defined]
from hexbytes import HexBytes
from web3 import AsyncWeb3

from kashdao_protocol_sdk.eoa.trades.hash import compute_transaction_hash
from kashdao_protocol_sdk.eoa.types import (
    BuiltTransaction,
    EoaTxOverrides,
    UnsignedTransaction,
)
from kashdao_protocol_sdk.shared.account.allowance import encode_approve
from kashdao_protocol_sdk.shared.account.positions import get_position
from kashdao_protocol_sdk.shared.contracts.abis import MARKET_ABI
from kashdao_protocol_sdk.shared.contracts.addresses import ProtocolAddresses
from kashdao_protocol_sdk.shared.errors import KashChainError
from kashdao_protocol_sdk.shared.markets.quote import QuoteParams, get_quote
from kashdao_protocol_sdk.shared.types import (
    BuildApproveParams,
    BuildBuyParams,
    BuildClosePositionParams,
    BuildSellParams,
    Hex,
)

_DEFAULT_DEADLINE_SECONDS = 5 * 60  # 5 minutes


# Function signatures + arg types are derived from the vendored Market
# ABI at import time so we can never drift from the contract's actual
# parameter types (uint8 outcome, uint64 deadline, etc.). Hardcoding
# selectors with the wrong types was the bug caught in our self-review.
def _function_signature(abi_entry: dict[str, Any]) -> str:
    types = ",".join(_canonical_solidity_type(i) for i in abi_entry.get("inputs", []))
    return f"{abi_entry['name']}({types})"


def _canonical_solidity_type(inp: dict[str, Any]) -> str:
    t = str(inp["type"])
    if t.startswith("tuple"):
        comps = ",".join(_canonical_solidity_type(c) for c in inp.get("components", []))
        return f"({comps}){t[len('tuple') :]}"
    return t


def _ifn(name: str) -> dict[str, Any]:
    for entry in MARKET_ABI:
        if entry.get("type") == "function" and entry.get("name") == name:
            return entry
    raise RuntimeError(f"function {name!r} not in vendored MARKET_ABI")


_BUY_FN = _ifn("buyExactAssetsIn")
_SELL_FN = _ifn("sellExactTokensIn")
_BUY_SELECTOR = keccak(text=_function_signature(_BUY_FN))[:4]
_SELL_SELECTOR = keccak(text=_function_signature(_SELL_FN))[:4]
_BUY_ARG_TYPES = [_canonical_solidity_type(i) for i in _BUY_FN["inputs"]]
_SELL_ARG_TYPES = [_canonical_solidity_type(i) for i in _SELL_FN["inputs"]]


async def build_buy_transaction(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    owner_address: Hex,
    market_address: Hex,
    params: BuildBuyParams,
    overrides: EoaTxOverrides | None = None,
) -> BuiltTransaction:
    """Build an unsigned BUY transaction targeting ``Market.buyExactAssetsIn``."""
    deadline = params.deadline if params.deadline is not None else _default_deadline()
    quote = await get_quote(
        web3,
        market_address,
        QuoteParams(side="BUY", outcome=params.outcome, amount=params.amount_usdc),
    )
    min_tokens_out_wad = _apply_slippage(quote.amount_out, params.max_slippage_bps)

    encoded = abi_encode(
        _BUY_ARG_TYPES,
        [
            params.outcome,
            params.amount_usdc,
            min_tokens_out_wad,
            deadline,
            to_bytes(hexstr=params.account),
        ],
    )
    calldata = HexBytes(_BUY_SELECTOR + encoded).to_0x_hex()
    return await _assemble_transaction(
        web3,
        addresses,
        from_address=owner_address,
        to_address=market_address,
        data=calldata,
        overrides=overrides or EoaTxOverrides(),
    )


async def build_sell_transaction(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    owner_address: Hex,
    market_address: Hex,
    params: BuildSellParams,
    overrides: EoaTxOverrides | None = None,
) -> BuiltTransaction:
    """Build an unsigned SELL transaction targeting ``Market.sellExactTokensIn``."""
    deadline = params.deadline if params.deadline is not None else _default_deadline()
    quote = await get_quote(
        web3,
        market_address,
        QuoteParams(side="SELL", outcome=params.outcome, amount=params.amount_tokens),
    )
    min_assets_out_usdc = _apply_slippage(quote.amount_out, params.max_slippage_bps)

    encoded = abi_encode(
        _SELL_ARG_TYPES,
        [
            params.outcome,
            params.amount_tokens,
            min_assets_out_usdc,
            deadline,
            to_bytes(hexstr=params.account),
        ],
    )
    calldata = HexBytes(_SELL_SELECTOR + encoded).to_0x_hex()
    return await _assemble_transaction(
        web3,
        addresses,
        from_address=owner_address,
        to_address=market_address,
        data=calldata,
        overrides=overrides or EoaTxOverrides(),
    )


async def build_approve_transaction(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    owner_address: Hex,
    params: BuildApproveParams,
    overrides: EoaTxOverrides | None = None,
) -> BuiltTransaction:
    """Build an unsigned ``ERC20.approve(spender, amount)`` tx targeting USDC.

    EOA mode targets USDC directly — no ``SimpleAccount.execute``
    indirection. ``params.account`` MUST match the configured signer
    EOA in EOA mode (the field is shared with SA mode where they can
    differ).
    """
    if params.account.lower() != owner_address.lower():
        raise KashChainError(
            "EOA mode: BuildApproveParams.account must match the configured signer's "
            f"owner_address. Got account={params.account}, signer={owner_address}. "
            "(In EOA mode the signer EOA is always the approving account; the field "
            "is shared with SA mode where they can differ.)",
            code="APPROVE_ACCOUNT_MISMATCH",
            context={"account": params.account, "signer_owner": owner_address},
        )
    calldata = encode_approve(spender=params.spender, amount=params.amount)
    return await _assemble_transaction(
        web3,
        addresses,
        from_address=owner_address,
        to_address=addresses.usdc,
        data=calldata,
        overrides=overrides or EoaTxOverrides(),
    )


async def build_close_position_transaction(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    owner_address: Hex,
    market_address: Hex,
    params: BuildClosePositionParams,
    overrides: EoaTxOverrides | None = None,
) -> BuiltTransaction:
    """Sell the EOA's full balance for the given outcome.

    Shorthand for :func:`build_sell_transaction` with ``amount_tokens``
    resolved from the on-chain ERC-1155 balance.
    """
    position = await get_position(web3, addresses, params.account, market_address)
    holding = position.by_outcome.get(params.outcome)
    balance = holding.balance_wad if holding is not None else 0
    if balance == 0:
        raise KashChainError(
            f"EOA {params.account} has no balance for outcome {params.outcome}",
            code="CLOSE_POSITION_ZERO_BALANCE",
            context={
                "market_address": market_address,
                "outcome": params.outcome,
                "account": params.account,
            },
        )
    sell_kwargs: dict[str, Any] = {
        "account": params.account,
        "outcome": params.outcome,
        "amount_tokens": balance,
        "max_slippage_bps": params.max_slippage_bps,
    }
    if params.deadline is not None:
        sell_kwargs["deadline"] = params.deadline
    return await build_sell_transaction(
        web3,
        addresses,
        owner_address,
        market_address,
        BuildSellParams(**sell_kwargs),
        overrides,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _assemble_transaction(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    *,
    from_address: Hex,
    to_address: Hex,
    data: Hex,
    overrides: EoaTxOverrides,
) -> BuiltTransaction:
    if overrides.nonce is not None:
        nonce = overrides.nonce
    else:
        try:
            nonce = await web3.eth.get_transaction_count(
                AsyncWeb3.to_checksum_address(from_address),
                block_identifier="pending",
            )
        except Exception as cause:
            raise KashChainError(
                f"failed to read transaction count for {from_address}",
                code="NONCE_READ_FAILED",
                context={"from": from_address},
                cause=cause,
                is_retryable=True,
            ) from cause

    transaction = UnsignedTransaction(
        chain_id=addresses.chain_id,
        to=to_address,
        data=data,
        value=0,
        nonce=int(nonce),
        gas=overrides.gas if overrides.gas is not None else 0,
        max_fee_per_gas=overrides.max_fee_per_gas if overrides.max_fee_per_gas is not None else 0,
        max_priority_fee_per_gas=(
            overrides.max_priority_fee_per_gas
            if overrides.max_priority_fee_per_gas is not None
            else 0
        ),
    )
    return BuiltTransaction(
        transaction=transaction,
        transaction_hash=compute_transaction_hash(transaction),
    )


def _apply_slippage(amount: int, slippage_bps: int) -> int:
    if slippage_bps < 0 or slippage_bps > 10_000:
        raise KashChainError(
            f"max_slippage_bps must be in [0, 10_000], got {slippage_bps}",
            code="INVALID_SLIPPAGE",
            context={"slippage_bps": slippage_bps},
        )
    return (amount * (10_000 - slippage_bps)) // 10_000


def _default_deadline() -> int:
    return int(time.time()) + _DEFAULT_DEADLINE_SECONDS


__all__ = [
    "build_approve_transaction",
    "build_buy_transaction",
    "build_close_position_transaction",
    "build_sell_transaction",
]
