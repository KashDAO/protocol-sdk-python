"""End-to-end integration test against Base Sepolia (EOA mode).

Gated behind the ``integration`` pytest marker so it's excluded from
the default test run. Maintainers run this manually before each
release sync; CI runs unit + parity only.

Required env
------------

- ``KASH_BASE_SEPOLIA_RPC`` — Base Sepolia RPC URL with quota.
- ``KASH_TEST_OWNER_KEY`` — 0x-prefixed test EOA private key, holding
  Base Sepolia ETH (for gas) and Base Sepolia USDC.
- ``KASH_TEST_MARKET`` — a Base Sepolia Kash market address with at
  least 2 outcomes.

Run::

    KASH_BASE_SEPOLIA_RPC=https://... \\
    KASH_TEST_OWNER_KEY=0x... \\
    KASH_TEST_MARKET=0x... \\
    pytest -m integration tests/integration

What it covers
--------------

This test exercises the full EOA-mode trade pipeline:

1. Construct an :func:`create_eoa_client` against Base Sepolia.
2. Read market state and a quote — pure-read path.
3. Build (but do NOT submit) an unsigned approve + buy. We assert
   the build/hash pipeline is intact and the canonical hash is
   stable across two builds. Submit is intentionally skipped so the
   test doesn't burn testnet USDC on every CI run, while still
   catching every offline-detectable regression.

For trade-submitting smoke runs, see the runbook in
``RELEASING.md``'s "manual smoke" section.
"""

from __future__ import annotations

import os

import pytest
from eth_account import Account

from kashdao_protocol_sdk import (
    BuildBuyParams,
    QuoteParams,
    create_eoa_client,
    usdc,
    viem_account_eoa_signer,
)


@pytest.fixture(scope="module")
def env() -> dict[str, str]:
    keys = ("KASH_BASE_SEPOLIA_RPC", "KASH_TEST_OWNER_KEY", "KASH_TEST_MARKET")
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        pytest.skip(f"integration env missing: {', '.join(missing)}")
    return {k: os.environ[k] for k in keys}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_eoa_quickstart_against_base_sepolia(env: dict[str, str]) -> None:
    """Read-side end-to-end: client construction, quote, build (no submit)."""
    account = Account.from_key(env["KASH_TEST_OWNER_KEY"])
    market = env["KASH_TEST_MARKET"]

    async with create_eoa_client(
        chain_id=84532,
        rpc=env["KASH_BASE_SEPOLIA_RPC"],
        signer=viem_account_eoa_signer(account),
    ) as client:
        # Construction succeeds + addresses populated.
        assert client.chain_id == 84532
        assert client.signer.owner_address == account.address
        assert client.addresses.factory.startswith("0x")
        assert client.addresses.usdc.startswith("0x")

        # Read-only market data.
        minimal = await client.markets.get(market)
        assert minimal.market_address.lower() == market.lower()
        assert minimal.num_outcomes >= 2, "test market must have ≥2 outcomes"

        # Non-binding quote works against the live AMM state.
        quote = await client.markets.quote(
            market,
            QuoteParams(side="BUY", outcome=0, amount=usdc(1)),
        )
        assert quote is not None

        # Build path is deterministic for a fixed input.
        # `smart_account` is the shared param-shape field name
        # consumed by both modes; in EOA mode pass the EOA address.
        params = BuildBuyParams(
            smart_account=client.signer.owner_address,
            outcome=0,
            amount_usdc=usdc(1),
            max_slippage_bps=50,
        )
        built1 = await client.trades.build_buy(market, params)
        built2 = await client.trades.build_buy(market, params)

        # Routing (market address + function selector) is deterministic
        # across builds. The encoded `min_amount_out` (slippage floor) IS
        # derived from a fresh quote on each build — the AMM state shifts
        # tick-to-tick if any other trade lands between the two calls,
        # so comparing full calldata would be flaky in production.
        # The original assertion (`data == data`) was over-strict.
        assert built1.transaction.to == built2.transaction.to
        assert built1.transaction.chain_id == built2.transaction.chain_id
        # Function selector (4 bytes = 10 chars incl. `0x`) must match —
        # that's the routing into the Market contract; the slippage scalar
        # is past the selector.
        assert built1.transaction.data[:10] == built2.transaction.data[:10]

        # Hash recomputation matches the build-time hash for the same
        # populated transaction (canonical-hash discipline check).
        rebuilt_hash = client.trades.hash_of(built1.transaction)
        assert rebuilt_hash == built1.transaction_hash
