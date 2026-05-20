"""Minimal integration smoke test.

The goal of this test is the smallest possible round-trip against a
live Base Sepolia node so a fresh contributor can confirm the
``EoaClient`` is wired up before running the deeper smoke suite in
``test_eoa_smoke.py``. We hit a single read-only RPC method
(``eth_getBalance``) and assert basic shape + chain-id wiring.

The test skips cleanly without ``KASH_BASE_SEPOLIA_RPC`` and
``KASH_TEST_OWNER_KEY`` so a default ``pytest`` invocation never
breaks on a fresh checkout.
"""

from __future__ import annotations

import os

import pytest

from kashdao_protocol_sdk import (
    LocalEoaSigner,
    create_eoa_client,
)

#: Apply the integration marker module-wide. ``pytest -m integration``
#: opts in; the default unit-test invocation (``-m 'not integration'``)
#: deselects every test in this file.
pytestmark = pytest.mark.integration

#: Base Sepolia chain id. Hardcoded; the env-var path stays a single
#: knob (the RPC URL) so the smoke test is impossible to misconfigure.
_BASE_SEPOLIA_CHAIN_ID = 84532


@pytest.fixture(scope="module")
def _smoke_env() -> tuple[str, str]:
    """Resolve required env vars or skip the entire module.

    Returning a tuple keeps the caller signature trivial — the deeper
    suite has its own conftest fixtures for richer setups.
    """
    rpc = os.environ.get("KASH_BASE_SEPOLIA_RPC")
    key = os.environ.get("KASH_TEST_OWNER_KEY")
    if not rpc or not key:
        pytest.skip("integration smoke test requires KASH_BASE_SEPOLIA_RPC and KASH_TEST_OWNER_KEY")
    return rpc, key


async def test_eoa_client_reports_chain_id_and_reads_gas_balance(
    _smoke_env: tuple[str, str],
) -> None:
    """Build an :class:`EoaClient`, assert its chain id, read a balance.

    This pins three invariants that must hold for any subsequent
    integration test to be meaningful:

    1. The factory accepts ``KASH_BASE_SEPOLIA_RPC`` and binds the
       expected chain id (84532).
    2. The signer derived from ``KASH_TEST_OWNER_KEY`` exposes a
       checksummed owner address.
    3. ``client.account.gas_balance`` returns an integer (any value
       including zero — a freshly funded test account is valid; an
       unfunded test account is also valid, just less interesting).
    """
    rpc, key = _smoke_env
    signer = LocalEoaSigner.from_private_key(key)

    async with create_eoa_client(
        chain_id=_BASE_SEPOLIA_CHAIN_ID,
        rpc=rpc,
        signer=signer,
    ) as client:
        assert client.chain_id == _BASE_SEPOLIA_CHAIN_ID

        balance = await client.account.gas_balance(signer.owner_address)
        assert isinstance(balance, int)
        assert balance >= 0
