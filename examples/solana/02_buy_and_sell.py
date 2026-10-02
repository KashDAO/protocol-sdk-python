"""Solana — Example 02: buy, then sell back, from a local Keypair.

**Places real trades when run with ``--confirm``.** Without it the script
only builds the buy plan and simulates it.

The keypair must hold SOL (fees, plus the position account's rent on the
first buy) and USDC in its associated token account.

``SOLANA_SECRET_KEY`` accepts either a base58 secret (a wallet export) or
the JSON byte array ``solana-keygen`` writes.

Run::

    pip install 'kashdao-protocol-sdk[solana]'
    SOLANA_RPC_URL=https://... \\
    SOLANA_SECRET_KEY='[12,34,...]' \\
    KASH_MARKET_ID=2 \\
    python examples/solana/02_buy_and_sell.py            # build + simulate only
    python examples/solana/02_buy_and_sell.py --confirm  # buy 1 USDC, then sell it back
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from solders.keypair import Keypair

from kashdao_protocol_sdk.solana import (
    ErrorCode,
    KashChainError,
    KashSimulationRevertedError,
    KashValidationError,
    create_solana_client,
    keypair_signer,
)


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
    confirm = "--confirm" in sys.argv[1:]

    keypair = load_keypair(secret)
    signer = keypair_signer(keypair)
    async with create_solana_client(rpc_url=rpc_url) as kash:
        print(f"USDC balance: {await kash.account.usdc_balance(keypair.pubkey())} (atomic)")

        if not confirm:
            plan = await kash.trades.build_buy(
                market=market_id,
                outcome=0,
                amount_usdc=1_000_000,
                max_slippage_bps=100,
                trader=keypair.pubkey(),
            )
            simulation = await kash.trades.simulate(plan, payer=keypair.pubkey())
            print(f"floor {plan.min_tokens_out_wad} WAD; simulation error: {simulation.error}")
            print("re-run with --confirm to trade")
            return

        try:
            # 1 USDC of outcome 0, accepting at most 1% below the quote. The first
            # buy also opens the position account in the same transaction.
            bought = await kash.trades.buy(
                market=market_id,
                outcome=0,
                amount_usdc=1_000_000,
                max_slippage_bps=100,
                signer=signer,
                compute_unit_price_micro_lamports=10_000,  # priority fee
            )
            print(f"bought: {bought.signature} (floor {bought.plan.min_tokens_out_wad} WAD)")

            position = await kash.account.position(
                market=market_id, outcome=0, owner=keypair.pubkey()
            )
            sold = await kash.trades.sell(
                market=market_id,
                outcome=0,
                tokens_in_wad=position.balance_wad,
                max_slippage_bps=100,
                signer=signer,
            )
            print(
                f"sold: {sold.signature} for at least {sold.plan.min_amount_out_usdc} atomic USDC"
            )
        except KashValidationError as err:
            print(f"refused before signing: {err} (program error: {err.program_error})")
        except KashSimulationRevertedError as err:
            program_error = (err.context or {}).get("program_error")
            print(f"the program refused the trade: {program_error or err}")
        except KashChainError as err:
            if err.code != ErrorCode.WAIT_RECEIPT_FAILED:
                raise
            signature = (err.context or {}).get("signature")
            print(f"confirmation unknown - look up {signature} before retrying")


if __name__ == "__main__":
    asyncio.run(main())
