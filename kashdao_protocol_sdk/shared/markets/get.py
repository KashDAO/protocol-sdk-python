"""On-chain minimal market read.

Mirrors ``src/shared/markets/get.ts``.
"""

from __future__ import annotations

from typing import Final

from web3 import AsyncWeb3

from kashdao_protocol_sdk.shared.contracts.abis import MARKET_ABI
from kashdao_protocol_sdk.shared.errors import KashChainError, KashProtocolError
from kashdao_protocol_sdk.shared.types import Hex, MarketStatus, MinimalMarketRead

#: ``Market.StateLike`` enum mirror — must stay in lockstep with
#: ``kash_amm_contracts/src/market/Market.sol``::
#:
#:     enum StateLike { Unseeded, Active, Frozen, Resolved }
_STATE_LIKE: Final[tuple[MarketStatus, ...]] = (
    "unseeded",
    "active",
    "frozen",
    "resolved",
)


async def get_market_minimal(web3: AsyncWeb3, market_address: Hex) -> MinimalMarketRead:
    """Read the minimal status projection for a market.

    Reads ``cfg`` (for ``num_outcomes``) and ``currentState`` (for status).
    Raises :class:`KashChainError` on RPC or contract failure.
    """
    contract = web3.eth.contract(
        address=AsyncWeb3.to_checksum_address(market_address),
        abi=MARKET_ABI,
    )
    try:
        cfg = await contract.functions.cfg().call()
        state = int(await contract.functions.currentState().call())
    except Exception as cause:
        if KashProtocolError.is_(cause):
            raise
        raise KashChainError(
            f"failed to read market {market_address}",
            code="MARKET_READ_FAILED",
            context={"market_address": market_address},
            cause=cause,
        ) from cause

    if state < 0 or state >= len(_STATE_LIKE):
        raise KashChainError(
            f"unknown market state value {state}",
            code="MARKET_UNKNOWN_STATE",
            context={"market_address": market_address, "state_index": state},
        )
    status: MarketStatus = _STATE_LIKE[state]
    # cfg is a Solidity tuple returned positionally; first slot is uint8 numOutcomes.
    num_outcomes = int(cfg[0])
    return MinimalMarketRead(
        market_address=market_address,
        num_outcomes=num_outcomes,
        status=status,
    )


__all__ = ["get_market_minimal"]
