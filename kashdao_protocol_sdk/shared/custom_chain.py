"""Custom-chain support — opt-in path for local development and forks.

Mirrors ``src/shared/custom-chain.ts``.

The SDK ships with a static registry of supported chains (Base Sepolia
today; Base mainnet when deployed). Pydantic / dataclass validation
rejects any other chain id.

For local development against Anvil / Hardhat / Tenderly forks /
sidechains, consumers pass a :class:`CustomChain` config field. When
set, the SDK uses the supplied addresses verbatim and bypasses the
registry entirely. The mainnet-not-deployed guard does NOT apply — the
consumer takes full ownership of address correctness.
"""

from __future__ import annotations

from dataclasses import dataclass

from kashdao_protocol_sdk.shared.contracts.addresses import (
    CANONICAL_SMART_ACCOUNT,
    ProtocolAddresses,
    SmartAccountConfig,
)
from kashdao_protocol_sdk.shared.types import Hex


@dataclass(frozen=True, slots=True)
class CustomChainAddresses:
    """Per-chain Kash protocol contract addresses for a custom chain.

    Optional fields (oracle / vault / tokens1155 / param_registry) may
    be omitted for chains where those contracts aren't deployed —
    methods that need them throw ``KashChainError(...)`` at call time.
    """

    factory: Hex
    usdc: Hex
    oracle: Hex | None = None
    vault: Hex | None = None
    tokens1155: Hex | None = None
    param_registry: Hex | None = None


@dataclass(frozen=True, slots=True)
class CustomSmartAccountConfig:
    """Per-chain SimpleAccount + EntryPoint addresses.

    Smart Account mode only; ignored by EOA mode.

    On Anvil and similar local environments, addresses differ from the
    canonical permissionless.js defaults because ``anvil_setCode``
    cannot copy immutable variables. Consumers running locally must
    read the deployed addresses from their Anvil deploy file and pass
    them in here.
    """

    factory_address: Hex
    implementation_address: Hex
    entry_point_address: Hex


@dataclass(frozen=True, slots=True)
class CustomChain:
    """Custom-chain config — the consumer-supplied alternative to the static registry.

    Pass via ``customChain=`` on ``create_eoa_client`` /
    ``create_smart_account_client`` to bypass the registry.
    """

    name: str
    chain_id: int
    addresses: CustomChainAddresses
    smart_account: CustomSmartAccountConfig | None = None
    is_testnet: bool = False


def resolve_custom_chain(chain_id: int, custom: CustomChain) -> ProtocolAddresses:
    """Convert a :class:`CustomChain` into the canonical :class:`ProtocolAddresses`.

    No address validation beyond the dataclass shape — the consumer is
    responsible for the addresses being correct for their target chain.
    """
    sa = (
        SmartAccountConfig(
            factory_address=custom.smart_account.factory_address,
            implementation_address=custom.smart_account.implementation_address,
            entry_point_address=custom.smart_account.entry_point_address,
        )
        if custom.smart_account is not None
        else SmartAccountConfig(
            factory_address=CANONICAL_SMART_ACCOUNT["factory_address"],
            implementation_address=CANONICAL_SMART_ACCOUNT["implementation_address"],
            entry_point_address=CANONICAL_SMART_ACCOUNT["entry_point_address"],
        )
    )
    return ProtocolAddresses(
        chain_id=chain_id,
        name=custom.name,
        is_testnet=custom.is_testnet,
        factory=custom.addresses.factory,
        usdc=custom.addresses.usdc,
        oracle=custom.addresses.oracle,
        vault=custom.addresses.vault,
        tokens1155=custom.addresses.tokens1155,
        param_registry=custom.addresses.param_registry,
        smart_account=sa,
    )


__all__ = [
    "CustomChain",
    "CustomChainAddresses",
    "CustomSmartAccountConfig",
    "resolve_custom_chain",
]
