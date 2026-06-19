"""``client.markets.state()`` — aggregated on-chain market state.

Mirrors ``src/shared/markets/state.ts``.

Reads ``reserveWad``, ``weightWad(i)`` and ``supplyWad(i)`` per outcome,
then derives marginal probabilities off-chain via the Pythagorean
scoring rule.
"""

from __future__ import annotations

import asyncio
import time

from web3 import AsyncWeb3

from kashdao_protocol_sdk.shared.contracts.abis import MARKET_ABI
from kashdao_protocol_sdk.shared.errors import KashChainError, KashProtocolError
from kashdao_protocol_sdk.shared.markets.get import get_market_minimal
from kashdao_protocol_sdk.shared.types import (
    Hex,
    MarketIdentity,
    MarketOutcomeState,
    MarketState,
)

_WAD = 10**18


def _derive_probabilities(weights: list[int], supplies: list[int]) -> list[float]:
    """Compute marginal probabilities off-chain from weights + supplies.

    For outcome i, the unnormalized score is
    ``weight_wad[i] * supply_wad[i]**2 / WAD``; probability is
    ``score[i] / sum(score)``. Result sums to ~1.0; small drift is
    acceptable for display.
    """
    scores = [
        ((weights[i] if i < len(weights) else 0) * supplies[i] * supplies[i]) // _WAD
        for i in range(len(supplies))
    ]
    total = sum(scores)
    if total == 0:
        return [0.0 for _ in scores]
    # Convert to floating-point with reasonable precision for UI.
    return [(s * 10_000 // total) / 10_000.0 for s in scores]


async def get_market_state(web3: AsyncWeb3, market_address: Hex) -> MarketState:
    """Read aggregated on-chain market state."""
    minimal = await get_market_minimal(web3, market_address)
    num_outcomes = minimal.num_outcomes
    if num_outcomes <= 0:
        raise KashChainError(
            f"market {market_address} has no outcomes",
            code="MARKET_NO_OUTCOMES",
            context={"market_address": market_address},
        )

    contract = web3.eth.contract(
        address=AsyncWeb3.to_checksum_address(market_address),
        abi=MARKET_ABI,
    )
    try:
        reserve_call = contract.functions.reserveWad().call()
        weight_calls = [contract.functions.weightWad(i).call() for i in range(num_outcomes)]
        supply_calls = [contract.functions.supplyWad(i).call() for i in range(num_outcomes)]
        # Identity reads — surfaced via MarketState.identity so consumers
        # don't need a separate round-trip to render market metadata.
        # Mirrors the TS SDK's getMarketState composition. categoryId,
        # createdAt, freezeTime, resolveTime are fields of the cfg()
        # struct (not standalone getters on the contract).
        identity_calls = [
            contract.functions.marketId().call(),
            contract.functions.cfg().call(),
        ]
        results = await asyncio.gather(reserve_call, *weight_calls, *supply_calls, *identity_calls)
    except Exception as cause:
        if KashProtocolError.is_(cause):
            raise
        raise KashChainError(
            f"failed to read market state for {market_address}",
            code="MARKET_STATE_READ_FAILED",
            context={"market_address": market_address},
            cause=cause,
        ) from cause

    reserve_wad = int(results[0])
    weights = [int(r) for r in results[1 : 1 + num_outcomes]]
    supplies = [int(r) for r in results[1 + num_outcomes : 1 + 2 * num_outcomes]]
    identity_start = 1 + 2 * num_outcomes
    # cfg() returns the MarketConfig struct: (uint8 numOutcomes,
    # uint16 sellFeeBps, uint24 categoryId, uint40 createdAt,
    # uint40 freezeTime, uint40 resolveTime, bool vaultEligible).
    cfg_tuple = results[identity_start + 1]
    identity = MarketIdentity(
        market_id=int(results[identity_start]),
        category_id=int(cfg_tuple[2]),
        created_at=int(cfg_tuple[3]),
        freeze_time=int(cfg_tuple[4]),
        resolve_time=int(cfg_tuple[5]),
    )
    probs = _derive_probabilities(weights, supplies)

    outcomes = tuple(
        MarketOutcomeState(
            index=i,
            outstanding_tokens_wad=supplies[i],
            weight_wad=weights[i],
            probability=probs[i],
        )
        for i in range(num_outcomes)
    )
    # ``read_at`` is consumer-side wall-clock seconds. For chain time,
    # consumers can call ``web3.eth.get_block('latest')`` directly.
    read_at = int(time.time())

    return MarketState(
        market_address=market_address,
        outcomes=outcomes,
        reserve_wad=reserve_wad,
        status=minimal.status,
        read_at=read_at,
        identity=identity,
    )


__all__ = ["get_market_state"]
