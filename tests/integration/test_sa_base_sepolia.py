"""Smart-account mode integration test against Base Sepolia.

SA-mode equivalent of ``test_eoa_base_sepolia.py``. Exercises SA address
derivation determinism, market reads, and the build pipeline for a buy
UserOp — no submission.

Required env
------------

- ``KASH_BASE_SEPOLIA_RPC`` — Base Sepolia RPC URL.
- ``KASH_TEST_OWNER_KEY`` — 0x-prefixed test EOA private key.
- ``KASH_TEST_BUNDLER_URL`` — Pimlico / Alchemy bundler URL for Base Sepolia.
- ``KASH_TEST_MARKET`` — a Base Sepolia Kash market address with ≥2 outcomes.

Run::

    KASH_BASE_SEPOLIA_RPC=https://... \\
    KASH_TEST_OWNER_KEY=0x... \\
    KASH_TEST_BUNDLER_URL=https://... \\
    KASH_TEST_MARKET=0x... \\
    pytest -m integration tests/integration/test_sa_base_sepolia.py

What it covers
--------------

1. Construct a :func:`create_smart_account_client` against Base Sepolia.
2. Verify SA address derivation is deterministic (two calls → same value).
3. Read market state — pure read path.
4. Build a buy UserOp (no submit); assert ``user_op_hash`` is stable across
   two calls with the same inputs. Catching every offline-detectable
   regression without burning testnet gas.
"""

from __future__ import annotations

import os

import pytest
from eth_account import Account

from kashdao_protocol_sdk import (
    BuildBuyParams,
    BundlerOptions,
    QuoteParams,
    create_smart_account_client,
    usdc,
    viem_account_signer,
)


@pytest.fixture(scope="module")
def env() -> dict[str, str]:
    keys = (
        "KASH_BASE_SEPOLIA_RPC",
        "KASH_TEST_OWNER_KEY",
        "KASH_TEST_BUNDLER_URL",
        "KASH_TEST_MARKET",
    )
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        pytest.skip(f"integration env missing: {', '.join(missing)}")
    return {k: os.environ[k] for k in keys}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sa_address_derivation_is_deterministic(env: dict[str, str]) -> None:
    """SA address is deterministic: two calls with the same signer return the same value."""
    account = Account.from_key(env["KASH_TEST_OWNER_KEY"])

    async with create_smart_account_client(
        chain_id=84532,
        rpc=env["KASH_BASE_SEPOLIA_RPC"],
        signer=viem_account_signer(account),
        bundler=BundlerOptions(provider="pimlico", url=env["KASH_TEST_BUNDLER_URL"]),
    ) as client:
        addr1 = await client.account.compute_address(client.signer.owner_address)
        addr2 = await client.account.compute_address(client.signer.owner_address)

        assert addr1 == addr2, "SA address derivation must be idempotent"
        assert addr1.startswith("0x"), "SA address must be 0x-prefixed"
        assert len(addr1) == 42, "SA address must be 20 bytes (42 hex chars with 0x)"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sa_market_read(env: dict[str, str]) -> None:
    """SA client can read market state and quote — pure read path."""
    account = Account.from_key(env["KASH_TEST_OWNER_KEY"])
    market = env["KASH_TEST_MARKET"]

    async with create_smart_account_client(
        chain_id=84532,
        rpc=env["KASH_BASE_SEPOLIA_RPC"],
        signer=viem_account_signer(account),
        bundler=BundlerOptions(provider="pimlico", url=env["KASH_TEST_BUNDLER_URL"]),
    ) as client:
        assert client.chain_id == 84532
        assert client.addresses.factory.startswith("0x")
        assert client.addresses.usdc.startswith("0x")

        minimal = await client.markets.get(market)
        assert minimal.market_address.lower() == market.lower()
        assert minimal.num_outcomes >= 2, "test market must have ≥2 outcomes"

        quote = await client.markets.quote(
            market,
            QuoteParams(side="BUY", outcome=0, amount=usdc(1)),
        )
        assert quote is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sa_build_buy_user_op_hash_is_stable(env: dict[str, str]) -> None:
    """Build a buy UserOp twice; assert user_op_hash is stable for the same inputs."""
    account = Account.from_key(env["KASH_TEST_OWNER_KEY"])
    market = env["KASH_TEST_MARKET"]

    async with create_smart_account_client(
        chain_id=84532,
        rpc=env["KASH_BASE_SEPOLIA_RPC"],
        signer=viem_account_signer(account),
        bundler=BundlerOptions(provider="pimlico", url=env["KASH_TEST_BUNDLER_URL"]),
    ) as client:
        sa_address = await client.account.compute_address(client.signer.owner_address)
        params = BuildBuyParams(
            smart_account=sa_address,
            outcome=0,
            amount_usdc=usdc(1),
            max_slippage_bps=50,
        )

        built1 = await client.trades.build_buy(market, params)
        built2 = await client.trades.build_buy(market, params)

        # Calldata encoding (the slippage-bearing part) must be deterministic.
        assert built1.user_op.call_data == built2.user_op.call_data
        assert built1.user_op.sender == built2.user_op.sender

        # The canonical UserOp hash is stable across builds with the same input.
        assert built1.user_op_hash == built2.user_op_hash
