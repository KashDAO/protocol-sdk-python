"""Integration-test fixtures.

These fixtures hit real Base Sepolia infrastructure. Every fixture
that requires an environment variable skips cleanly when the variable
is missing — the suite is opt-in, never noisy on a fresh checkout.

Required environment variables for the full integration suite:

- ``KASH_BASE_SEPOLIA_RPC`` — Base Sepolia RPC URL (HTTPS or WSS).
- ``KASH_TEST_OWNER_KEY``   — 0x-prefixed private key for the test
                              owner account. **Use a TESTNET key only.**
- ``KASH_TEST_MARKET``      — 0x-prefixed Base Sepolia market address
                              (only required by tests that target a
                              specific market).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from kashdao_protocol_sdk import (
    EoaClient,
    LocalEoaSigner,
    create_eoa_client,
)

#: Base Sepolia chain id. Hardcoded to keep the fixture self-contained.
_BASE_SEPOLIA_CHAIN_ID = 84532


def _require_env(name: str) -> str:
    """Return ``os.environ[name]`` or skip the test cleanly."""
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"integration tests require {name}")
    return value


@pytest.fixture(scope="session")
def base_sepolia_rpc() -> str:
    """Resolve the Base Sepolia RPC URL or skip."""
    return _require_env("KASH_BASE_SEPOLIA_RPC")


@pytest.fixture(scope="session")
def eoa_signer() -> LocalEoaSigner:
    """In-memory signer derived from ``KASH_TEST_OWNER_KEY``.

    Skips if the env var is missing. Session-scoped so we don't reload
    the key per test.
    """
    private_key = _require_env("KASH_TEST_OWNER_KEY")
    return LocalEoaSigner.from_private_key(private_key)


@pytest_asyncio.fixture
async def eoa_client(
    base_sepolia_rpc: str,
    eoa_signer: LocalEoaSigner,
) -> AsyncIterator[EoaClient]:
    """Constructed :class:`EoaClient` bound to Base Sepolia.

    Function-scoped (not session-scoped) because :meth:`EoaClient.aclose`
    releases the underlying ``AsyncWeb3`` provider's connection pool —
    leaking that across tests would produce false-positive socket
    accumulation in the suite.
    """
    client = create_eoa_client(
        chain_id=_BASE_SEPOLIA_CHAIN_ID,
        rpc=base_sepolia_rpc,
        signer=eoa_signer,
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture(scope="session")
def test_market_address() -> str:
    """0x-prefixed Base Sepolia market address from ``KASH_TEST_MARKET``."""
    return _require_env("KASH_TEST_MARKET")
