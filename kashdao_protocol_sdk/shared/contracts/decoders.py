"""Event + revert decoders.

Mirrors ``src/shared/contracts/decoders.ts``.

Used by ``client.markets.watch()`` and ``client.trades.simulate()`` to
surface human-readable error reasons. Selector-based: matches the first
4 bytes of revert data against custom errors declared in the vendored
Market / EntryPoint ABIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from eth_abi import decode as abi_decode  # type: ignore[attr-defined]
from eth_utils import keccak, to_bytes  # type: ignore[attr-defined]
from hexbytes import HexBytes

from kashdao_protocol_sdk.shared.contracts.abis import (
    ENTRY_POINT_07_ABI,
    MARKET_ABI,
)


@dataclass(frozen=True, slots=True)
class DecodedRevert:
    """A successfully-decoded custom error from a contract revert."""

    name: str
    args: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class _ErrorEntry:
    """Pre-computed error decoder for a single ABI ``error`` entry."""

    selector: bytes
    name: str
    types: tuple[str, ...]


def _canonical_type(inp: dict[str, Any]) -> str:
    """Return the canonical Solidity type for ABI signature hashing.

    Tuples need their components flattened into ``(t1,t2,t3)``; arrays
    of tuples become ``(t1,t2)[]`` etc. Required so the keccak of the
    error signature matches what Solidity emits.
    """
    t = cast(str, inp["type"])
    if t.startswith("tuple"):
        components = inp.get("components", [])
        flat = ",".join(_canonical_type(c) for c in components)
        suffix = t[len("tuple") :]  # "" or "[]" or "[N]" etc.
        return f"({flat}){suffix}"
    return t


def _build_error_table() -> dict[bytes, _ErrorEntry]:
    """One-time scan of vendored ABIs.

    Builds a ``selector → _ErrorEntry`` lookup so revert decoding is a
    constant-time dict miss (instead of N keccaks per call). The ABIs
    are immutable for the lifetime of the package, so we do this once
    at import and never recompute.
    """
    table: dict[bytes, _ErrorEntry] = {}
    for abi in (MARKET_ABI, ENTRY_POINT_07_ABI):
        for entry in abi:
            if entry.get("type") != "error":
                continue
            inputs = entry.get("inputs", [])
            types = tuple(_canonical_type(inp) for inp in inputs)
            sig = f"{entry['name']}({','.join(types)})"
            selector = bytes(keccak(text=sig)[:4])
            # If two errors share a selector across the two ABIs the
            # first wins; this matches the prior linear-scan order.
            table.setdefault(
                selector,
                _ErrorEntry(selector=selector, name=cast(str, entry["name"]), types=types),
            )
    return table


_ERROR_TABLE: dict[bytes, _ErrorEntry] = _build_error_table()


def decode_market_revert(data: str | bytes) -> DecodedRevert | None:
    """Decode a custom error from Market or EntryPoint v0.7 revert data.

    Looks up the 4-byte selector in a pre-computed table. Returns
    ``None`` if no selector matches.
    """
    raw = HexBytes(data) if not isinstance(data, HexBytes) else data
    if len(raw) < 4:
        return None

    selector = bytes(raw[:4])
    payload = bytes(raw[4:])

    entry = _ERROR_TABLE.get(selector)
    if entry is None:
        return None
    try:
        args = abi_decode(list(entry.types), payload) if entry.types else ()
    except Exception:
        return None
    return DecodedRevert(name=entry.name, args=tuple(args))


# Convenience wire-shape return for parity with TS callers that expect dict.
def decode_market_revert_dict(data: str | bytes) -> dict[str, Any] | None:
    """Same as :func:`decode_market_revert` but returns a dict for JSON output."""
    decoded = decode_market_revert(data)
    if decoded is None:
        return None
    return {"name": decoded.name, "args": list(decoded.args)}


_ACTIONABLE_REVERT_HINTS: dict[str, str] = {
    "SlippageExceeded": (
        "the market moved between quote and execution. Raise `max_slippage_bps` "
        "(try 100-200 for volatile markets) or re-quote immediately before sending."
    ),
    "DeadlinePassed": (
        "the trade took too long to reach the chain. Pass a longer `deadline` "
        "(default is now + 5 min; raise to 10-15 min for slow paths) or re-prepare "
        "immediately before sending."
    ),
    "InvalidOutcome": (
        "the outcome index is out of range. Check `client.markets.state(market).outcomes.length`."
    ),
    "InvalidState": (
        "the market is not in an actionable state (frozen / resolved / unseeded). "
        "Check `client.markets.state(market).status` before retrying."
    ),
    "MarketFrozen": ("the market is frozen. Check `client.markets.state(market).status`."),
    "MarketResolved": ("the market is resolved. Check `client.markets.state(market).status`."),
    "MarketNotActive": ("the market is not active. Check `client.markets.state(market).status`."),
    "NotSeeded": (
        "the market hasn't been seeded yet. Wait for the seed step or use a different market."
    ),
    "InsufficientReserve": (
        "the market reserve is too low for the requested trade size. Trade a smaller "
        "amount or pick a deeper market."
    ),
    "AA21 didn't pay prefund": (
        "the smart account doesn't have enough ETH to pay UserOp gas. Send a small "
        "amount of ETH to the SA address (check via `client.account.gas_balance(sa)`) "
        "or wire a paymaster."
    ),
    "AA23 reverted": (
        "the SA `validateUserOp` reverted - usually wrong factory addresses on Anvil "
        "(see TROUBLESHOOTING § AA23) or the SA implementation is mis-deployed."
    ),
    "AA23 reverted (or OOG)": (
        "the SA `validateUserOp` reverted - usually wrong factory addresses on Anvil "
        "(see TROUBLESHOOTING § AA23) or the SA implementation is mis-deployed."
    ),
    "AA24 signature error": (
        "the bundler rejected the signature. The SDK normally catches this "
        "pre-flight via `STALE_USEROP_HASH`; if you see AA24 from the bundler "
        "regardless, check that `custom_chain.smart_account.entry_point_address` "
        "matches your bundler's deployed EntryPoint."
    ),
}


def actionable_revert_hint(reason: str | None) -> str | None:
    """Return a one-line actionable hint for known revert reasons.

    Mirrors ``actionableRevertHint`` from the TS decoders module. Custom
    errors decoded by the ABI hit the exact-match table first; raw
    revert strings fall through to substring matching for the small set
    of ergonomic cases (USDC allowance, USDC balance) where the
    on-chain message is unambiguous.

    Returns ``None`` when no hint applies — caller should surface the
    raw ``reason`` to the consumer instead.
    """
    if not reason:
        return None
    exact = _ACTIONABLE_REVERT_HINTS.get(reason)
    if exact is not None:
        return exact
    lower = reason.lower()
    if "insufficient allowance" in lower or "transferfrom" in lower:
        return (
            "the account has not approved USDC to the Market contract. Call "
            "`client.trades.send.approve(spender=market_address, amount=MAX_UINT256)` "
            "once before your first trade. Use "
            "`client.account.usdc_allowance(account, market_address)` to check."
        )
    if "insufficient balance" in lower:
        return (
            "the account doesn't hold enough USDC for the trade. Check "
            "`client.account.usdc_balance(account)`."
        )
    return None


# Helpful for pre-flight tests when callers want to examine raw revert bytes.
def revert_bytes(data: str | bytes) -> bytes:
    """Coerce hex string or bytes-like to a raw bytes object."""
    if isinstance(data, bytes):
        return data
    return to_bytes(hexstr=data)


__all__ = [
    "DecodedRevert",
    "actionable_revert_hint",
    "decode_market_revert",
    "decode_market_revert_dict",
    "revert_bytes",
]
