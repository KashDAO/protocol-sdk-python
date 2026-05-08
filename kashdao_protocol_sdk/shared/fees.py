"""EIP-1559 fee estimation — used by both modes.

Mirrors ``src/shared/fees.ts``.

Returns ``max_fee_per_gas`` + ``max_priority_fee_per_gas`` derived from
the consumer's chain RPC via ``eth_feeHistory``. Implemented here
(rather than relying on web3.py's ``gas_strategies``) so consumers get
explicit control over the base-fee multiplier — both UserOps (SA mode)
and EOA txs benefit from a higher multiplier than typical defaults
when the chain is volatile or the bundler/mempool can delay inclusion.

**Strategy** (default; configurable via :class:`EstimateFeesOptions`):

1. ``eth_feeHistory(block_count=4, latest, [50])`` — sample the last 4
   blocks at the 50th percentile of priority fees.
2. ``max_priority_fee_per_gas = max(median(reward[*][0]), 1 gwei)`` —
   median across blocks, with a 1 gwei floor.
3. ``max_fee_per_gas = base_fee_next * base_multiplier + max_priority_fee_per_gas``
   — defensive 2x base by default, since UserOps may take 2+ blocks
   to bundle.

Defaults are tuned for Base.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from web3 import AsyncWeb3

from kashdao_protocol_sdk.shared.errors import KashChainError


@dataclass(frozen=True, slots=True)
class FeeEstimate:
    """Result of :func:`estimate_chain_fees`."""

    max_fee_per_gas: int
    max_priority_fee_per_gas: int


@dataclass(frozen=True, slots=True)
class EstimateFeesOptions:
    """Tuning knobs for :func:`estimate_chain_fees`."""

    block_count: int = 4
    reward_percentile: int = 50
    base_multiplier: float = 2.0
    min_priority_fee_wei: int = 1_000_000_000  # 1 gwei


async def estimate_chain_fees(
    web3: AsyncWeb3,
    options: EstimateFeesOptions | None = None,
) -> FeeEstimate:
    """Estimate ``max_fee_per_gas`` + ``max_priority_fee_per_gas`` for the next tx.

    Reads via ``eth_feeHistory`` on the consumer's RPC (not the bundler RPC).
    Defaults tuned for Base; override via :class:`EstimateFeesOptions` for
    other chains or custom fee strategies.
    """
    opts = options or EstimateFeesOptions()

    if opts.block_count <= 0:
        raise KashChainError(
            f"block_count must be positive, got {opts.block_count}",
            code="INVALID_FEE_OPTIONS",
            context={"block_count": opts.block_count},
        )
    if not (0 <= opts.reward_percentile <= 100):
        raise KashChainError(
            f"reward_percentile must be in [0, 100], got {opts.reward_percentile}",
            code="INVALID_FEE_OPTIONS",
            context={"reward_percentile": opts.reward_percentile},
        )
    if opts.base_multiplier <= 0:
        raise KashChainError(
            f"base_multiplier must be positive, got {opts.base_multiplier}",
            code="INVALID_FEE_OPTIONS",
            context={"base_multiplier": opts.base_multiplier},
        )

    try:
        # web3.py returns a `FeeHistory` TypedDict; we treat it as a
        # plain dict for forward compatibility (the keys we read are
        # stable across web3.py 6/7).
        history = cast(
            dict[str, Any],
            await web3.eth.fee_history(
                block_count=opts.block_count,
                newest_block="latest",
                reward_percentiles=[opts.reward_percentile],
            ),
        )
    except Exception as cause:
        raise KashChainError(
            "failed to read eth_feeHistory for fee estimation",
            code="FEE_HISTORY_FAILED",
            context={
                "block_count": opts.block_count,
                "reward_percentile": opts.reward_percentile,
            },
            cause=cause,
            is_retryable=True,
        ) from cause

    base_fees: list[int] = [int(x) for x in history.get("baseFeePerGas", [])]
    if not base_fees:
        raise KashChainError(
            "eth_feeHistory returned no base fees",
            code="FEE_HISTORY_EMPTY",
            context={"history_keys": list(history.keys())},
        )
    # web3.py / EIP-1559: baseFeePerGas[-1] is the predicted next-block fee.
    base_fee_next = base_fees[-1]

    # reward is List[List[int]] — one list per sampled block, one entry per
    # requested percentile. Skip null/empty/zero entries to avoid biasing
    # toward zero on idle blocks.
    rewards: list[int] = []
    for block in history.get("reward") or []:
        if not block:
            continue
        v = int(block[0]) if isinstance(block[0], (int, str)) else None
        if v is not None and v > 0:
            rewards.append(v)

    median_priority = _median(rewards) if rewards else 0
    max_priority_fee_per_gas = max(median_priority, opts.min_priority_fee_wei)

    base_with_margin = _scale_by_factor(base_fee_next, opts.base_multiplier)
    max_fee_per_gas = base_with_margin + max_priority_fee_per_gas

    return FeeEstimate(
        max_fee_per_gas=max_fee_per_gas,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
    )


def _median(values: list[int]) -> int:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) // 2


def _scale_by_factor(value: int, factor: float) -> int:
    """Multiply a non-negative int by a fractional factor, truncating."""
    if factor == int(factor):
        return value * int(factor)
    milli = round(factor * 1000)
    return (value * milli) // 1000


__all__ = [
    "EstimateFeesOptions",
    "FeeEstimate",
    "estimate_chain_fees",
]
