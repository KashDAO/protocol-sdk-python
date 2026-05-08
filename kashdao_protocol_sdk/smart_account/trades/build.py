"""Unsigned ERC-4337 UserOperation builders for buy / sell / close / approve.

Mirrors ``src/smart-account/trades/build.ts``.

Each builder:

1. Encodes the protocol-side calldata (``Market.buyExactAssetsIn`` etc.).
2. Wraps it in ``SimpleAccount.execute(target, value, data)``.
3. Reads the smart-account nonce from the EntryPoint v0.7 contract.
4. Returns an unpacked :class:`BuiltUserOp` with consumer-supplied gas
   defaults (zeroed unless overridden) plus the canonical
   ``user_op_hash`` for signing.

**Important** — the ``user_op_hash`` returned by these builders is
computed against the UserOp at build time. After the consumer
populates gas + fee fields, the hash MUST be recomputed via
``client.trades.hash_of(user_op)`` (see :mod:`hash`). The submit-side
staleness check catches the mistake if forgotten, but the recompute is
the right pattern. The ``prepare_*`` helpers collapse the gas-population
+ recompute step.

Gas estimation is a separate concern — :class:`BundlerClient` exposes
:meth:`BundlerClient.estimate_gas`. Consumers either call that or pass
``gas`` overrides in the build params.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

from eth_abi import encode as abi_encode  # type: ignore[attr-defined]
from eth_typing import HexStr
from eth_utils import keccak, to_bytes  # type: ignore[attr-defined]
from hexbytes import HexBytes
from web3 import AsyncWeb3

from kashdao_protocol_sdk.shared.account.allowance import encode_approve
from kashdao_protocol_sdk.shared.account.positions import get_position
from kashdao_protocol_sdk.shared.contracts.abis import (
    ENTRY_POINT_07_ABI,
    MARKET_ABI,
    SIMPLE_ACCOUNT_ABI,
)
from kashdao_protocol_sdk.shared.contracts.addresses import ProtocolAddresses
from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashChainError, KashConfigError
from kashdao_protocol_sdk.shared.markets.quote import QuoteParams, get_quote
from kashdao_protocol_sdk.shared.types import (
    BuildApproveParams,
    BuildBuyParams,
    BuildClosePositionParams,
    BuildSellParams,
    Hex,
)
from kashdao_protocol_sdk.smart_account.trades.hash import (
    compute_user_op_hash,
    user_op_to_typed_data,
)
from kashdao_protocol_sdk.smart_account.types import (
    BuildOptions,
    BuiltUserOp,
    UnsignedUserOp,
)

_DEFAULT_DEADLINE_SECONDS = 5 * 60


# ---------------------------------------------------------------------------
# Selector derivation — bound at import time from the vendored ABIs
# so a future ABI rename surfaces as an import-time RuntimeError, not
# a silent on-chain revert. Same pattern as eoa/trades/build.py.
# ---------------------------------------------------------------------------


def _function_signature(abi_entry: dict[str, Any]) -> str:
    types = ",".join(_canonical_solidity_type(i) for i in abi_entry.get("inputs", []))
    return f"{abi_entry['name']}({types})"


def _canonical_solidity_type(inp: dict[str, Any]) -> str:
    t = str(inp["type"])
    if t.startswith("tuple"):
        comps = ",".join(_canonical_solidity_type(c) for c in inp.get("components", []))
        return f"({comps}){t[len('tuple') :]}"
    return t


def _ifn(abi: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for entry in abi:
        if entry.get("type") == "function" and entry.get("name") == name:
            return entry
    raise RuntimeError(f"function {name!r} not in vendored ABI")


_BUY_FN = _ifn(MARKET_ABI, "buyExactAssetsIn")
_SELL_FN = _ifn(MARKET_ABI, "sellExactTokensIn")
_EXECUTE_FN = _ifn(SIMPLE_ACCOUNT_ABI, "execute")
_GET_NONCE_FN = _ifn(ENTRY_POINT_07_ABI, "getNonce")

_BUY_SELECTOR: bytes = bytes(keccak(text=_function_signature(_BUY_FN))[:4])
_SELL_SELECTOR: bytes = bytes(keccak(text=_function_signature(_SELL_FN))[:4])
_EXECUTE_SELECTOR: bytes = bytes(keccak(text=_function_signature(_EXECUTE_FN))[:4])
_GET_NONCE_SELECTOR: bytes = bytes(keccak(text=_function_signature(_GET_NONCE_FN))[:4])

_BUY_ARG_TYPES: list[str] = [_canonical_solidity_type(i) for i in _BUY_FN["inputs"]]
_SELL_ARG_TYPES: list[str] = [_canonical_solidity_type(i) for i in _SELL_FN["inputs"]]
_EXECUTE_ARG_TYPES: list[str] = [_canonical_solidity_type(i) for i in _EXECUTE_FN["inputs"]]
_GET_NONCE_ARG_TYPES: list[str] = [_canonical_solidity_type(i) for i in _GET_NONCE_FN["inputs"]]


# ---------------------------------------------------------------------------
# PaymasterConfig — the SA-mode-specific build option
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaymasterConfig:
    """Optional paymaster injection for sponsored UserOps.

    The SDK does NOT provision paymasters — sponsorship configuration
    (which paymaster, what limits, what API key) is the consumer's
    responsibility. Pass the paymaster contract address + the
    paymaster's pre-computed ``data`` blob.

    When set, the SDK populates the ``paymaster*`` fields on the UserOp
    before submission. The bundler will route gas payment via the
    paymaster instead of the SA's ETH balance.

    Most consumers will get this from a Pimlico / Alchemy / Stackup
    paymaster client and pass it through.
    """

    address: Hex
    """Paymaster contract address."""

    data: Hex = "0x"
    """ABI-encoded paymaster-specific data (signature, validity window, etc.)."""

    verification_gas_limit: int | None = None
    """Gas limit for the paymaster's ``validatePaymasterUserOp``."""

    post_op_gas_limit: int | None = None
    """Gas limit for the paymaster's ``postOp`` callback."""


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


async def build_buy_user_op(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildBuyParams,
    options: BuildOptions | None = None,
    paymaster: PaymasterConfig | None = None,
) -> BuiltUserOp:
    """Build an unsigned BUY UserOp wrapping ``Market.buyExactAssetsIn``."""
    opts = options or BuildOptions()
    deadline = params.deadline if params.deadline is not None else _default_deadline()
    quote = await get_quote(
        web3,
        market_address,
        QuoteParams(side="BUY", outcome=params.outcome, amount=params.amount_usdc),
    )
    min_out = _apply_slippage(quote.amount_out, params.max_slippage_bps)
    inner = _encode_call(
        _BUY_SELECTOR,
        _BUY_ARG_TYPES,
        [
            params.outcome,
            params.amount_usdc,
            min_out,
            deadline,
            params.smart_account,
        ],
    )
    return await _assemble_user_op(
        web3,
        addresses,
        smart_account=params.smart_account,
        target=market_address,
        inner_calldata=inner,
        options=opts,
        paymaster=paymaster,
    )


async def build_sell_user_op(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildSellParams,
    options: BuildOptions | None = None,
    paymaster: PaymasterConfig | None = None,
) -> BuiltUserOp:
    """Build an unsigned SELL UserOp wrapping ``Market.sellExactTokensIn``."""
    opts = options or BuildOptions()
    deadline = params.deadline if params.deadline is not None else _default_deadline()
    quote = await get_quote(
        web3,
        market_address,
        QuoteParams(side="SELL", outcome=params.outcome, amount=params.amount_tokens),
    )
    min_out = _apply_slippage(quote.amount_out, params.max_slippage_bps)
    inner = _encode_call(
        _SELL_SELECTOR,
        _SELL_ARG_TYPES,
        [
            params.outcome,
            params.amount_tokens,
            min_out,
            deadline,
            params.smart_account,
        ],
    )
    return await _assemble_user_op(
        web3,
        addresses,
        smart_account=params.smart_account,
        target=market_address,
        inner_calldata=inner,
        options=opts,
        paymaster=paymaster,
    )


async def build_approve_user_op(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    params: BuildApproveParams,
    options: BuildOptions | None = None,
    paymaster: PaymasterConfig | None = None,
) -> BuiltUserOp:
    """Build an unsigned APPROVE UserOp wrapping ``ERC20.approve``.

    Every first-time consumer needs this once before their first BUY:
    the Market contract calls ``transferFrom`` on USDC, which fails
    without a prior approval. The conventional pattern is to approve
    ``MAX_UINT256`` once, so subsequent trades skip this step.
    """
    opts = options or BuildOptions()
    inner = encode_approve(spender=params.spender, amount=params.amount)
    return await _assemble_user_op(
        web3,
        addresses,
        smart_account=params.account,
        target=addresses.usdc,
        inner_calldata=inner,
        options=opts,
        paymaster=paymaster,
    )


async def build_close_position_user_op(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    market_address: Hex,
    params: BuildClosePositionParams,
    options: BuildOptions | None = None,
    paymaster: PaymasterConfig | None = None,
) -> BuiltUserOp:
    """Close a full position — sells the SA's entire balance for the outcome.

    Shorthand for :func:`build_sell_user_op` with ``amount_tokens``
    resolved from the on-chain ERC-1155 balance.
    """
    position = await get_position(web3, addresses, params.smart_account, market_address)
    holding = next((h for h in position.holdings if h.outcome_index == params.outcome), None)
    balance = holding.balance_wad if holding is not None else 0
    if balance == 0:
        raise KashConfigError(
            f"account {params.smart_account} has no balance for outcome {params.outcome}",
            code=ErrorCode.CLOSE_POSITION_ZERO_BALANCE,
            context={
                "market_address": market_address,
                "outcome": params.outcome,
                "account": params.smart_account,
            },
        )
    return await build_sell_user_op(
        web3,
        addresses,
        market_address,
        BuildSellParams(
            smart_account=params.smart_account,
            outcome=params.outcome,
            amount_tokens=balance,
            max_slippage_bps=params.max_slippage_bps,
            deadline=params.deadline,
        ),
        options=options,
        paymaster=paymaster,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _assemble_user_op(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    *,
    smart_account: Hex,
    target: Hex,
    inner_calldata: Hex,
    options: BuildOptions,
    paymaster: PaymasterConfig | None,
) -> BuiltUserOp:
    """Wrap ``inner_calldata`` in ``SimpleAccount.execute()`` and read nonce."""
    call_data = _encode_call(
        _EXECUTE_SELECTOR,
        _EXECUTE_ARG_TYPES,
        [target, 0, _hex_to_bytes(inner_calldata)],
    )

    entry_point_address = addresses.smart_account.entry_point_address
    # ``nonce_key`` is the EntryPoint nonce stream key (uint192), NOT
    # the CREATE2 salt — the two have unrelated semantics. Salt drives
    # SA address derivation; nonce_key selects a per-stream nonce slot.
    nonce = await _read_nonce(
        web3,
        entry_point_address,
        smart_account,
        nonce_key=options.nonce_key,
    )

    gas = options.gas
    user_op_kwargs: dict[str, Any] = {
        "sender": smart_account,
        "nonce": nonce,
        "callData": call_data,
        "callGasLimit": (gas.call_gas_limit if gas and gas.call_gas_limit is not None else 0),
        "verificationGasLimit": (
            gas.verification_gas_limit if gas and gas.verification_gas_limit is not None else 0
        ),
        "preVerificationGas": (
            gas.pre_verification_gas if gas and gas.pre_verification_gas is not None else 0
        ),
        "maxFeePerGas": (gas.max_fee_per_gas if gas and gas.max_fee_per_gas is not None else 0),
        "maxPriorityFeePerGas": (
            gas.max_priority_fee_per_gas if gas and gas.max_priority_fee_per_gas is not None else 0
        ),
        "signature": "0x",
    }

    if paymaster is not None:
        user_op_kwargs["paymaster"] = paymaster.address
        user_op_kwargs["paymasterData"] = paymaster.data
        if paymaster.verification_gas_limit is not None:
            user_op_kwargs["paymasterVerificationGasLimit"] = paymaster.verification_gas_limit
        if paymaster.post_op_gas_limit is not None:
            user_op_kwargs["paymasterPostOpGasLimit"] = paymaster.post_op_gas_limit

    user_op = UnsignedUserOp(**user_op_kwargs)
    user_op_hash = compute_user_op_hash(addresses.chain_id, entry_point_address, user_op)
    typed_data = user_op_to_typed_data(addresses.chain_id, entry_point_address, user_op)
    return BuiltUserOp(user_op=user_op, user_op_hash=user_op_hash, typed_data=typed_data)


async def _read_nonce(
    web3: AsyncWeb3,
    entry_point_address: Hex,
    smart_account: Hex,
    *,
    nonce_key: int,
) -> int:
    """Call ``EntryPoint.getNonce(sender, key)`` and decode the uint256 return."""
    calldata = HexBytes(
        _GET_NONCE_SELECTOR + abi_encode(_GET_NONCE_ARG_TYPES, [smart_account, nonce_key])
    ).to_0x_hex()
    try:
        result = await web3.eth.call(
            {
                "to": AsyncWeb3.to_checksum_address(entry_point_address),
                "data": cast(HexStr, calldata),
            }
        )
    except Exception as cause:
        raise KashChainError(
            f"failed to read EntryPoint nonce for {smart_account}",
            code=ErrorCode.NONCE_READ_FAILED,
            context={
                "smart_account": smart_account,
                "nonce_key": nonce_key,
                "entry_point_address": entry_point_address,
            },
            cause=cause,
        ) from cause
    if len(result) < 32:
        raise KashChainError(
            f"EntryPoint.getNonce returned malformed result for {smart_account}",
            code=ErrorCode.NONCE_READ_FAILED,
            context={
                "smart_account": smart_account,
                "result_bytes": len(result),
            },
        )
    return int.from_bytes(bytes(result[-32:]), "big")


def _encode_call(selector: bytes, arg_types: list[str], args: list[Any]) -> Hex:
    """Concatenate selector + ABI-encoded args into a 0x-prefixed hex string."""
    return HexBytes(selector + abi_encode(arg_types, args)).to_0x_hex()


def _apply_slippage(amount: int, slippage_bps: int) -> int:
    """``amount * (10_000 - slippage_bps) / 10_000`` with strict bounds check."""
    if slippage_bps < 0 or slippage_bps > 10_000:
        raise KashConfigError(
            f"max_slippage_bps must be in [0, 10_000], got {slippage_bps}",
            code=ErrorCode.INVALID_SLIPPAGE,
            context={"slippage_bps": slippage_bps},
        )
    return amount * (10_000 - slippage_bps) // 10_000


def _default_deadline() -> int:
    """Wall-clock now + 5 minutes (seconds since epoch)."""
    return int(time.time()) + _DEFAULT_DEADLINE_SECONDS


def _hex_to_bytes(value: Hex | str) -> bytes:
    if value in ("", "0x"):
        return b""
    return to_bytes(hexstr=value)


__all__ = [
    "PaymasterConfig",
    "build_approve_user_op",
    "build_buy_user_op",
    "build_close_position_user_op",
    "build_sell_user_op",
]
