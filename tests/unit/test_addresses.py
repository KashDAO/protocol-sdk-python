"""Unit tests for ``shared/contracts/addresses.py``."""

from __future__ import annotations

import pytest

from kashdao_protocol_sdk import (
    BASE_MAINNET,
    BASE_SEPOLIA,
    KNOWN_CHAIN_IDS,
    SUPPORTED_CHAIN_IDS,
    KashConfigError,
    get_protocol_addresses,
    is_known_chain_id,
    is_supported_chain_id,
)


class TestRegistry:
    def test_base_sepolia_supported(self) -> None:
        assert is_supported_chain_id(84532)
        assert is_known_chain_id(84532)
        assert 84532 in SUPPORTED_CHAIN_IDS
        assert 84532 in KNOWN_CHAIN_IDS

    def test_base_mainnet_known_but_not_supported(self) -> None:
        assert not is_supported_chain_id(8453)
        assert is_known_chain_id(8453)
        assert 8453 not in SUPPORTED_CHAIN_IDS
        assert 8453 in KNOWN_CHAIN_IDS

    def test_unknown_chain(self) -> None:
        assert not is_supported_chain_id(1)
        assert not is_known_chain_id(1)


class TestGetProtocolAddresses:
    def test_sepolia_resolves(self) -> None:
        addrs = get_protocol_addresses(84532)
        assert addrs.chain_id == 84532
        assert addrs.name == "Base Sepolia"
        assert addrs.is_testnet is True
        assert addrs.factory == BASE_SEPOLIA.factory
        assert addrs.usdc == BASE_SEPOLIA.usdc

    def test_mainnet_raises_chain_not_deployed(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            get_protocol_addresses(8453)
        assert exc_info.value.code == "CHAIN_NOT_DEPLOYED"
        # Sanity: factory in registry is zero address (the gating signal).
        assert int(BASE_MAINNET.factory, 16) == 0

    def test_unknown_chain_raises(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            get_protocol_addresses(1)
        assert exc_info.value.code == "UNSUPPORTED_CHAIN"


class TestSmartAccountConfig:
    def test_sepolia_has_canonical_sa_config(self) -> None:
        sa = BASE_SEPOLIA.smart_account
        assert sa.entry_point_address == "0x0000000071727De22E5E9d8BAf0edAc6f37da032"
        assert sa.entry_point_version == "0.7"

    def test_mainnet_has_canonical_sa_config(self) -> None:
        # Even though the protocol isn't deployed on mainnet, the SA config
        # uses the canonical CREATE2 addresses (which are identical across
        # all EVM chains).
        sa = BASE_MAINNET.smart_account
        assert sa.entry_point_address == "0x0000000071727De22E5E9d8BAf0edAc6f37da032"
        assert sa.entry_point_version == "0.7"
