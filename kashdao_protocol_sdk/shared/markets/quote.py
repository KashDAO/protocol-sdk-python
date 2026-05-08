"""``client.markets.quote()`` — on-chain quote.

Mirrors ``src/shared/markets/quote.ts``.

Calls ``quoteBuyExactAssetsIn`` / ``quoteSellExactTokensIn`` on the
Market contract via the consumer's RPC. Returns BigInt amounts in
atomic units; consumers format for display.
"""

from __future__ import annotations

from dataclasses import dataclass

from web3 import AsyncWeb3

from kashdao_protocol_sdk.shared.contracts.abis import MARKET_ABI
from kashdao_protocol_sdk.shared.errors import KashChainError, KashProtocolError
from kashdao_protocol_sdk.shared.types import Hex, Quote, QuoteSide


@dataclass(frozen=True, slots=True)
class QuoteParams:
    """Inputs for :func:`get_quote`."""

    side: QuoteSide
    outcome: int
    #: Atomic input: USDC (6dp) for BUY, outcome tokens (18dp) for SELL.
    amount: int


async def get_quote(
    web3: AsyncWeb3,
    market_address: Hex,
    params: QuoteParams,
) -> Quote:
    """Fetch an on-chain trade quote for the configured market."""
    contract = web3.eth.contract(
        address=AsyncWeb3.to_checksum_address(market_address),
        abi=MARKET_ABI,
    )
    try:
        if params.side == "BUY":
            # quoteBuyExactAssetsIn returns:
            #   tokensOutWad, reserveAfterWad, cAfterWad, pAfterWad[], qAfterWad[]
            result = await contract.functions.quoteBuyExactAssetsIn(
                params.outcome,
                params.amount,
            ).call()
            tokens_out_wad = int(result[0])
            reserve_after_wad = int(result[1])
            prices_after_wad = tuple(int(x) for x in result[3])
            return Quote(
                side="BUY",
                outcome_index=params.outcome,
                amount_in=params.amount,
                amount_out=tokens_out_wad,
                reserve_after_wad=reserve_after_wad,
                prices_after_wad=prices_after_wad,
            )
        # SELL: quoteSellExactTokensIn returns:
        #   assetsOutUsdc, grossReleaseWad, reserveAfterWad, cAfterWad, pAfterWad[], qAfterWad[]
        result = await contract.functions.quoteSellExactTokensIn(
            params.outcome,
            params.amount,
        ).call()
        assets_out_usdc = int(result[0])
        reserve_after_wad = int(result[2])
        prices_after_wad = tuple(int(x) for x in result[4])
        return Quote(
            side="SELL",
            outcome_index=params.outcome,
            amount_in=params.amount,
            amount_out=assets_out_usdc,
            reserve_after_wad=reserve_after_wad,
            prices_after_wad=prices_after_wad,
        )
    except Exception as cause:
        if KashProtocolError.is_(cause):
            raise
        raise KashChainError(
            f"failed to quote {params.side.lower()} on {market_address}",
            code="QUOTE_FAILED",
            context={
                "market_address": market_address,
                "side": params.side,
                "outcome": params.outcome,
                "amount": params.amount,
            },
            cause=cause,
        ) from cause


__all__ = ["QuoteParams", "get_quote"]
