"""Smart-account mode — Example 01: Quickstart.

Builds a :func:`create_smart_account_client` against Base Sepolia,
derives the (counterfactual) SimpleAccount address, and reads market
state. Pure on-chain — no Kash REST API in the path.

Compared to EOA mode, smart-account mode adds:

- A SimpleAccount v0.7 sender address derived deterministically from
  ``signer.owner_address`` + a salt. The trader is the SmartAccount,
  not the signer.
- A bundler relay (Pimlico / Alchemy / Flashbots / generic) for
  ERC-4337 v0.7 UserOp delivery.
- Optional paymaster sponsorship.

Run::

    KASH_PRIVATE_KEY=0x... \\
    KASH_BUNDLER_URL=https://api.pimlico.io/v2/84532/rpc?apikey=... \\
    KASH_BASE_RPC_URL=https://sepolia.base.org \\
    python examples/smart-account/01_quickstart.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from eth_account import Account

from kashdao_protocol_sdk import (
    BundlerOptions,
    create_smart_account_client,
    viem_account_signer,
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(f"missing required env var: {name}\n")
        sys.exit(2)
    return value


async def main() -> None:
    pk = _require_env("KASH_PRIVATE_KEY")
    bundler_url = _require_env("KASH_BUNDLER_URL")
    rpc = os.environ.get("KASH_BASE_RPC_URL", "https://sepolia.base.org")

    account = Account.from_key(pk)
    signer = viem_account_signer(account)

    async with create_smart_account_client(
        chain_id=84532,
        rpc=rpc,
        signer=signer,
        bundler=BundlerOptions(provider="pimlico", url=bundler_url),
    ) as client:
        sa_address = await client.account.compute_address(client.signer.owner_address)
        print(f"SA client ready on chain {client.chain_id}")
        print(f"  Owner (EOA):   {client.signer.owner_address}")
        print(f"  SmartAccount:  {sa_address}")
        print(f"  Factory:       {client.addresses.factory}")
        print(f"  EntryPoint:    {client.bundler.entry_point_address}")
        print(f"  USDC:          {client.addresses.usdc}")

        # Health-check the bundler before doing anything trade-shaped.
        health = await client.bundler.health()
        print(f"\nBundler health: {health}")


if __name__ == "__main__":
    asyncio.run(main())
