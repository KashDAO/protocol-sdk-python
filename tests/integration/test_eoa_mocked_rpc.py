"""Network-free integration test for EOA-mode construction.

Two contractual claims of the protocol SDK that we want CI to enforce:

1. **Construction is offline.** Calling ``create_eoa_client(...)`` does
   not make any outbound HTTP request. The SDK is non-custodial and
   zero-Kash-dependency by construction; an outbound probe at
   construction would silently violate that.
2. **The configured RPC URL is the only host the SDK ever contacts.**
   No hard-coded Kash domain, no telemetry endpoint, no analytics
   collector — every byte the SDK sends out goes to the URL the
   consumer supplied.

Both claims are verified with ``pytest-httpx`` as a passive observer:
we register **zero** mocked responses, then assert the fixture saw
zero requests after a full client lifecycle. If the SDK ever leaked
an outbound call at construction, ``httpx_mock`` would surface it as
an unmatched-request error at fixture teardown.

Note on transport: the chain RPC layer goes through
``web3.AsyncHTTPProvider`` which uses ``aiohttp`` under the hood —
``httpx_mock`` only intercepts ``httpx``-based traffic. So this test
is **not** a comprehensive network probe; it specifically verifies
that the construction path doesn't have any ``httpx`` outbound call.
For chain-side traffic verification, see the env-gated live test in
``test_eoa_base_sepolia.py``.

What this DOES catch:

- A future refactor that adds a sneaky ``httpx`` call (e.g. a
  user-agent registration probe, a Sentry beacon, an analytics
  ping) at client construction.
- A misconfigured signer that hits ``httpx`` to fetch a remote key
  during ``viem_account_eoa_signer``.

What this does NOT catch (lives behind the ``integration`` marker):

- Chain RPC behaviour — covered by ``test_eoa_base_sepolia.py``.
- Bundler RPC — covered by unit tests in
  ``tests/unit/test_bundler.py`` (the bundler client uses ``httpx``
  directly, so ``httpx_mock`` works for those).

These tests run on every PR — they're NOT decorated with
``@pytest.mark.integration``. The marker is reserved for the
live-RPC tests that genuinely need ``KASH_BASE_SEPOLIA_RPC``.
"""

from __future__ import annotations

from typing import Any

import pytest
from eth_account import Account

from kashdao_protocol_sdk import (
    CustomChain,
    create_eoa_client,
    create_smart_account_client,
    viem_account_eoa_signer,
    viem_account_signer,
)
from kashdao_protocol_sdk.shared.custom_chain import (
    CustomChainAddresses,
    CustomSmartAccountConfig,
)

# Anvil/Hardhat default account #0 — never used on a real chain.
ANVIL_TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
RPC_URL = "https://mocked-rpc.test/anvil"
BUNDLER_URL = "https://mocked-bundler.test/v0_7"

CUSTOM_FACTORY = "0x1000000000000000000000000000000000000001"
CUSTOM_USDC = "0x2000000000000000000000000000000000000002"
CUSTOM_ORACLE = "0x3000000000000000000000000000000000000003"
CUSTOM_PARAM_REGISTRY = "0x4000000000000000000000000000000000000004"
CUSTOM_ENTRY_POINT = "0x0000000071727De22E5E9d8BAf0edAc6f37da032"
CUSTOM_SA_FACTORY = "0x9406Cc6185a346906296840746125a0E44976454"
CUSTOM_SA_IMPL = "0x5000000000000000000000000000000000000005"


def _custom_chain() -> CustomChain:
    """Synthetic Anvil-shaped custom chain. Pure construction inputs;
    nothing actually deployed at any of these addresses."""
    return CustomChain(
        chain_id=31337,
        name="anvil-mocked",
        addresses=CustomChainAddresses(
            factory=CUSTOM_FACTORY,
            usdc=CUSTOM_USDC,
            oracle=CUSTOM_ORACLE,
            param_registry=CUSTOM_PARAM_REGISTRY,
        ),
        smart_account=CustomSmartAccountConfig(
            factory_address=CUSTOM_SA_FACTORY,
            implementation_address=CUSTOM_SA_IMPL,
            entry_point_address=CUSTOM_ENTRY_POINT,
        ),
        is_testnet=True,
    )


@pytest.mark.asyncio
async def test_eoa_client_construction_is_offline(httpx_mock: Any) -> None:
    """Constructing an EOA client must NOT hit any ``httpx`` endpoint.

    No mocked responses are registered. If the SDK leaked an outbound
    call at construction, ``httpx_mock`` would raise an unmatched-request
    error at teardown.
    """
    account = Account.from_key(ANVIL_TEST_KEY)
    client = create_eoa_client(
        chain_id=31337,
        rpc=RPC_URL,
        signer=viem_account_eoa_signer(account),
        custom_chain=_custom_chain(),
    )
    try:
        # Sanity: client construction populated the documented surface
        # without phoning anything.
        assert client.chain_id == 31337
        assert client.signer.owner_address == account.address
        assert client.addresses.factory.lower() == CUSTOM_FACTORY.lower()
        assert client.addresses.usdc.lower() == CUSTOM_USDC.lower()
    finally:
        await client.aclose()

    # The hard contract: zero httpx-side requests fired.
    requests = httpx_mock.get_requests()
    assert requests == [], (
        f"SDK construction issued {len(requests)} unexpected outbound httpx request(s): "
        f"{[str(r.url) for r in requests]}. The protocol SDK is contractually "
        "non-custodial and zero-Kash-dependency — no telemetry, no probe, no "
        "phone-home is permitted at construction."
    )


@pytest.mark.asyncio
async def test_smart_account_client_construction_is_offline(httpx_mock: Any) -> None:
    """SA-mode equivalent of the EOA test — same contract, same proof."""
    account = Account.from_key(ANVIL_TEST_KEY)
    client = create_smart_account_client(
        chain_id=31337,
        rpc=RPC_URL,
        signer=viem_account_signer(account),
        bundler=BUNDLER_URL,
        custom_chain=_custom_chain(),
    )
    try:
        assert client.chain_id == 31337
        assert client.signer.owner_address == account.address
        assert client.addresses.factory.lower() == CUSTOM_FACTORY.lower()
    finally:
        await client.aclose()

    requests = httpx_mock.get_requests()
    assert requests == [], (
        f"SA-mode construction leaked {len(requests)} httpx request(s): "
        f"{[str(r.url) for r in requests]}"
    )


@pytest.mark.asyncio
async def test_signer_construction_does_not_probe(httpx_mock: Any) -> None:
    """The :func:`viem_account_eoa_signer` factory must not make any
    outbound call. It wraps an ``eth_account.Account`` and exposes
    methods; nothing about wrapping a local key needs network."""
    account = Account.from_key(ANVIL_TEST_KEY)
    signer = viem_account_eoa_signer(account)
    assert signer.owner_address == account.address

    requests = httpx_mock.get_requests()
    assert requests == [], f"Local-key signer factory leaked {len(requests)} httpx request(s)"


@pytest.mark.asyncio
async def test_full_lifecycle_offline_with_aclose(httpx_mock: Any) -> None:
    """Constructing AND closing the client must both stay offline.

    A subtle bug class: a teardown path that flushes a buffered
    metric to a remote sink. ``async with`` / ``aclose`` are the
    documented ways to release SDK-owned resources; neither should
    ever fire an outbound call.
    """
    account = Account.from_key(ANVIL_TEST_KEY)

    async with create_eoa_client(
        chain_id=31337,
        rpc=RPC_URL,
        signer=viem_account_eoa_signer(account),
        custom_chain=_custom_chain(),
    ) as client:
        assert client.chain_id == 31337

    # `async with` exited and ran ``aclose`` for us.
    requests = httpx_mock.get_requests()
    assert requests == [], (
        f"Full lifecycle (construct + aclose) leaked {len(requests)} httpx request(s)"
    )
