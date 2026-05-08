"""EOA mode — Example 01: Quickstart.

Builds a :func:`create_eoa_client`, reads market state on Base Sepolia,
and prints a non-binding quote for a hypothetical 10-USDC buy on
outcome 0. Pure on-chain — no Kash REST API in the path.

Compared to smart-account mode, EOA mode has:

- No bundler
- No SimpleAccount address derivation
- The signer's ``owner_address`` IS your trading address

Run::

    KASH_PRIVATE_KEY=0x... \\
    KASH_MARKET=0x... \\
    KASH_BASE_RPC_URL=https://sepolia.base.org \\
    python examples/eoa/01_quickstart.py

If ``KASH_BASE_RPC_URL`` is not set, the public Base Sepolia node is
used (rate-limited; fine for a quickstart).
"""

from __future__ import annotations

import asyncio
import os
import sys

from eth_account import Account

from kashdao_protocol_sdk import (
    QuoteParams,
    create_eoa_client,
    usdc,
    viem_account_eoa_signer,
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(f"missing required env var: {name}\n")
        sys.exit(2)
    return value


async def main() -> None:
    pk = _require_env("KASH_PRIVATE_KEY")
    market = _require_env("KASH_MARKET")
    rpc = os.environ.get("KASH_BASE_RPC_URL", "https://sepolia.base.org")

    account = Account.from_key(pk)
    signer = viem_account_eoa_signer(account)

    async with create_eoa_client(
        chain_id=84532,  # Base Sepolia
        rpc=rpc,
        signer=signer,
    ) as client:
        print(f"EOA client ready on chain {client.chain_id}")
        print(f"  EOA address:  {client.signer.owner_address}")
        print(f"  factory:      {client.addresses.factory}")
        print(f"  USDC:         {client.addresses.usdc}")

        # Read minimal market state.
        minimal = await client.markets.get(market)
        print(f"\nMarket {market}:")
        print(f"  outcomes: {len(minimal.outcomes)}")

        # Non-binding quote for a 10-USDC buy on outcome 0.
        quote = await client.markets.quote(
            market,
            QuoteParams(side="BUY", outcome=0, amount=usdc(10)),
        )
        print("\nQuote for 10 USDC buy on outcome 0:")
        print(f"  expected tokens out: {quote}")


if __name__ == "__main__":
    asyncio.run(main())
