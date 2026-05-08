"""Local Anvil mode — Example 01: Quickstart against a local fork.

Demonstrates the ``custom_chain`` escape hatch for running the SDK
against a local Anvil node (or any non-canonical EVM deployment).
The static chain registry is bypassed; you supply the chain id, RPC
URL, and the contract addresses yourself.

This is the recommended dev loop: spin up Anvil + the protocol's
contracts (see the ``AMM`` repo's ``forge script`` deployment), drop
your addresses into the env, and run this script. No mainnet keys,
no testnet faucets.

Required env
------------

- ``KASH_PRIVATE_KEY`` — any 0x-prefixed test key (Anvil's default
  account #0 works fine: ``0xac0974b...80``).
- ``KASH_ANVIL_RPC`` — defaults to ``http://127.0.0.1:8545``.
- ``KASH_CHAIN_ID`` — defaults to ``31337`` (Anvil's default).
- ``KASH_FACTORY_ADDR`` — the deployed MarketFactory address.
- ``KASH_USDC_ADDR`` — the deployed mock USDC token.
- ``KASH_PARAM_REGISTRY_ADDR`` — the deployed ParamRegistry.
- ``KASH_ORACLE_ADDR`` — the deployed Oracle.

Run::

    anvil &
    forge script Deploy --rpc-url http://127.0.0.1:8545 --broadcast \\
      ../AMM/script/Deploy.s.sol  # populates the addresses below
    KASH_FACTORY_ADDR=0x... KASH_USDC_ADDR=0x... \\
    KASH_PARAM_REGISTRY_ADDR=0x... KASH_ORACLE_ADDR=0x... \\
    python examples/local-anvil/01_quickstart.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from eth_account import Account

from kashdao_protocol_sdk import (
    CustomChain,
    create_eoa_client,
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
    factory = _require_env("KASH_FACTORY_ADDR")
    usdc_addr = _require_env("KASH_USDC_ADDR")
    param_registry = _require_env("KASH_PARAM_REGISTRY_ADDR")
    oracle = _require_env("KASH_ORACLE_ADDR")
    rpc = os.environ.get("KASH_ANVIL_RPC", "http://127.0.0.1:8545")
    chain_id = int(os.environ.get("KASH_CHAIN_ID", "31337"))

    account = Account.from_key(pk)

    custom_chain = CustomChain(
        chain_id=chain_id,
        name="anvil-local",
        rpc_url=rpc,
        addresses={
            "factory": factory,
            "usdc": usdc_addr,
            "param_registry": param_registry,
            "oracle": oracle,
            # Vault / multicall3 are optional — leave empty unless your
            # local deploy includes them.
        },
    )

    async with create_eoa_client(
        chain_id=chain_id,
        rpc=rpc,
        signer=viem_account_eoa_signer(account),
        custom_chain=custom_chain,
    ) as client:
        print(f"Local-Anvil EOA client ready on chain {client.chain_id}")
        print(f"  EOA address: {client.signer.owner_address}")
        print(f"  factory:     {client.addresses.factory}")
        print(f"  USDC:        {client.addresses.usdc}")

        # Verify the chain is reachable.
        block = await client.web3.eth.block_number
        print(f"  current block: {block}")


if __name__ == "__main__":
    asyncio.run(main())
