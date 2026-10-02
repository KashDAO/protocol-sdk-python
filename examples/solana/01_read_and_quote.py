"""Solana — Example 01: read a market and quote it. No signer, no funds.

Reads the program config and one market on Solana mainnet-beta (the
default cluster), then prints the program's exact integer quote for a
5 USDC buy of outcome 0.

Run::

    pip install 'kashdao-protocol-sdk[solana]'
    SOLANA_RPC_URL=https://api.mainnet-beta.solana.com \\
    KASH_MARKET_ID=2 \\
    python examples/solana/01_read_and_quote.py
"""

from __future__ import annotations

import asyncio
import os

from kashdao_protocol_sdk.solana import create_solana_client


async def main() -> None:
    rpc_url = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    market_id = int(os.environ.get("KASH_MARKET_ID", "0"))

    async with create_solana_client(rpc_url=rpc_url) as kash:  # mainnet-beta by default
        config = await kash.protocol.config()
        print(f"protocol fee {config.default_protocol_fee_bps} bps, paused={config.paused}")

        market = await kash.markets.get(market_id)
        print(f"market {market.market_id} ({market.address}) is {market.status}")
        print(
            f"  sell fee {market.sell_fee_bps} bps, collateral {market.collateral_usdc} atomic USDC"
        )
        for k, p in enumerate(market.probabilities_wad):
            print(f"  outcome {k}: {p / 10**16:.2f}%")

        # What would 5 USDC of outcome 0 buy? The program's exact integer answer.
        quote = await kash.markets.quote_buy(market=market_id, outcome=0, amount_usdc=5_000_000)
        print(
            f"5 USDC -> {quote.tokens_out_wad} WAD of outcome 0 "
            f"(fee {quote.protocol_fee_usdc} atomic USDC)"
        )


if __name__ == "__main__":
    asyncio.run(main())
