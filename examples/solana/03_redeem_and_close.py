"""Solana — Example 03: redeem after resolution (or cancellation), then close.

**Sends transactions when run with ``--confirm``.** Without it the script
builds and simulates the redeem only.

Uses the build -> simulate -> send lifecycle, so the plan can be inspected
(or handed to another signer) before anything is sent, then closes the
now-empty position account to reclaim its rent — which returns to whoever
funded the account (``plan.rent_recipient``), not necessarily the owner.

Run::

    pip install 'kashdao-protocol-sdk[solana]'
    SOLANA_RPC_URL=https://... \\
    SOLANA_SECRET_KEY='[12,34,...]' \\
    KASH_MARKET_ID=2 OUTCOME=1 \\
    python examples/solana/03_redeem_and_close.py --confirm
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from solders.keypair import Keypair

from kashdao_protocol_sdk.solana import create_solana_client, keypair_signer


def load_keypair(secret: str) -> Keypair:
    if secret.lstrip().startswith("["):
        return Keypair.from_bytes(bytes(json.loads(secret)))
    return Keypair.from_base58_string(secret.strip())


async def main() -> None:
    rpc_url = os.environ.get("SOLANA_RPC_URL")
    secret = os.environ.get("SOLANA_SECRET_KEY")
    if not rpc_url or not secret:
        sys.exit("SOLANA_RPC_URL and SOLANA_SECRET_KEY are required")
    market_id = int(os.environ.get("KASH_MARKET_ID", "0"))
    outcome = int(os.environ.get("OUTCOME", "0"))
    confirm = "--confirm" in sys.argv[1:]

    keypair = load_keypair(secret)
    signer = keypair_signer(keypair)
    async with create_solana_client(rpc_url=rpc_url) as kash:
        market = await kash.markets.get(market_id)
        if market.status not in ("resolved", "cancelled"):
            sys.exit(f"market {market_id} is {market.status}; nothing to redeem yet")

        # Redeem the whole position (amount_wad defaults to the balance). The
        # floor is the quote less 0.5%; a losing outcome of a resolved market pays 0.
        build = (
            kash.trades.build_redeem
            if market.status == "resolved"
            else kash.trades.build_redeem_cancelled
        )
        redeem = await build(
            market=market_id, outcome=outcome, owner=keypair.pubkey(), max_slippage_bps=50
        )
        print(f"redeeming {redeem.amount_wad} WAD for >= {redeem.min_amount_out_usdc} atomic USDC")

        simulation = await kash.trades.simulate(redeem, payer=keypair.pubkey())
        if simulation.error is not None:
            sys.exit(f"simulation refused: {simulation.error.name or simulation.error.raw}")
        if not confirm:
            print("simulation passed; re-run with --confirm to redeem and close")
            return

        print(f"redeemed: {(await kash.trades.send(redeem, signer=signer)).signature}")

        # The position is now empty: close it and reclaim the rent.
        closed = await kash.trades.close_position(market=market_id, outcome=outcome, signer=signer)
        print(f"closed: {closed.signature}; rent returned to {closed.plan.rent_recipient}")


if __name__ == "__main__":
    asyncio.run(main())
