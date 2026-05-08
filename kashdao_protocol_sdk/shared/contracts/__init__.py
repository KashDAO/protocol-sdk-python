"""Vendored ABIs and per-chain protocol address registry.

ABIs ship as JSON under ``generated/`` (loaded via
:mod:`importlib.resources`). The address registry exposes the same
``getProtocolAddresses`` semantics as the TypeScript reference under
:func:`get_protocol_addresses`.
"""

from kashdao_protocol_sdk.shared.contracts.abis import (
    ENTRY_POINT_07_ABI,
    ERC20_ABI,
    ERC1155_OUTCOME_TOKENS_ABI,
    MARKET_ABI,
    MARKET_FACTORY_ABI,
    MULTICALL3_ABI,
    ORACLE_ABI,
    PARAM_REGISTRY_ABI,
    SIMPLE_ACCOUNT_ABI,
    SIMPLE_ACCOUNT_FACTORY_ABI,
    VAULT_ABI,
)
from kashdao_protocol_sdk.shared.contracts.addresses import (
    BASE_MAINNET,
    BASE_SEPOLIA,
    CANONICAL_SMART_ACCOUNT,
    ENTRY_POINT_07_ADDRESS,
    KNOWN_CHAIN_IDS,
    MULTICALL3_ADDRESS,
    SUPPORTED_CHAIN_IDS,
    ProtocolAddresses,
    get_protocol_addresses,
    is_known_chain_id,
    is_supported_chain_id,
)

__all__ = [
    "BASE_MAINNET",
    "BASE_SEPOLIA",
    "CANONICAL_SMART_ACCOUNT",
    "ENTRY_POINT_07_ABI",
    "ENTRY_POINT_07_ADDRESS",
    "ERC20_ABI",
    "ERC1155_OUTCOME_TOKENS_ABI",
    "KNOWN_CHAIN_IDS",
    "MARKET_ABI",
    "MARKET_FACTORY_ABI",
    "MULTICALL3_ABI",
    "MULTICALL3_ADDRESS",
    "ORACLE_ABI",
    "PARAM_REGISTRY_ABI",
    "SIMPLE_ACCOUNT_ABI",
    "SIMPLE_ACCOUNT_FACTORY_ABI",
    "SUPPORTED_CHAIN_IDS",
    "VAULT_ABI",
    "ProtocolAddresses",
    "get_protocol_addresses",
    "is_known_chain_id",
    "is_supported_chain_id",
]
