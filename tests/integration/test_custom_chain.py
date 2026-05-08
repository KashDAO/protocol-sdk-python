"""``custom_chain`` escape-hatch — construction-time integration test.

Verifies that supplying a :class:`CustomChain` config bypasses the static
chain registry and that the resulting client exposes the supplied addresses
on ``client.addresses``.

No live RPC needed — this is a pure-Python construction test. It lives
under the ``integration`` marker because it exercises the full Pydantic
validation pipeline and the ``custom_chain`` path in
:func:`create_eoa_client`, including the ``resolve_custom_chain`` call
that converts a :class:`CustomChain` into a :class:`ProtocolAddresses`.

What it covers
--------------

1. ``create_eoa_client`` with a :class:`CustomChain` (chainId 31337,
   synthetic addresses) succeeds — the client is constructed, and the
   supplied addresses are visible on ``client.addresses``.
2. The client's ``chain_id`` matches the custom chain's ``chain_id``.
3. No live RPC is needed — the test tears down immediately after
   construction by calling ``aclose`` without waiting.
"""

from __future__ import annotations

import pytest
from eth_account import Account

from kashdao_protocol_sdk import (
    CustomChain,
    create_eoa_client,
    viem_account_eoa_signer,
)
from kashdao_protocol_sdk.shared.custom_chain import CustomChainAddresses

# Synthetic but structurally-valid addresses for Anvil / Hardhat.
_FACTORY = "0x1000000000000000000000000000000000000001"
_USDC = "0x2000000000000000000000000000000000000002"
_ORACLE = "0x3000000000000000000000000000000000000003"
_PARAM_REGISTRY = "0x4000000000000000000000000000000000000004"

# A throwaway private key — this test never signs anything.
_TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


@pytest.fixture(scope="module")
def custom_chain_config() -> CustomChain:
    """Return a valid :class:`CustomChain` for chainId 31337 (Anvil default)."""
    return CustomChain(
        chain_id=31337,
        name="anvil-local",
        addresses=CustomChainAddresses(
            factory=_FACTORY,
            usdc=_USDC,
            oracle=_ORACLE,
            param_registry=_PARAM_REGISTRY,
        ),
        is_testnet=True,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_custom_chain_construction_succeeds(custom_chain_config: CustomChain) -> None:
    """Client constructs without error and exposes correct addresses."""
    account = Account.from_key(_TEST_KEY)

    client = create_eoa_client(
        chain_id=31337,
        rpc="http://127.0.0.1:8545",
        signer=viem_account_eoa_signer(account),
        custom_chain=custom_chain_config,
    )
    try:
        assert client.chain_id == 31337
        assert client.addresses.factory == _FACTORY
        assert client.addresses.usdc == _USDC
        assert client.addresses.oracle == _ORACLE
        assert client.addresses.param_registry == _PARAM_REGISTRY
    finally:
        await client.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_custom_chain_async_context_manager(custom_chain_config: CustomChain) -> None:
    """Client is usable via ``async with``."""
    account = Account.from_key(_TEST_KEY)

    async with create_eoa_client(
        chain_id=31337,
        rpc="http://127.0.0.1:8545",
        signer=viem_account_eoa_signer(account),
        custom_chain=custom_chain_config,
    ) as client:
        assert client.chain_id == 31337
        assert client.addresses.factory == _FACTORY
        assert client.mode == "eoa"
