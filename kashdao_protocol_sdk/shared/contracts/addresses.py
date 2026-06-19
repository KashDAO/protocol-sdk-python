"""Per-chain protocol address registry.

Mirrors ``src/shared/contracts/addresses.ts``.
Static literal — the runtime library has zero internal dependencies.
The TS side has a drift test against the canonical Kash deployments;
the Python side stays in lockstep by being a pure structural copy
reviewed on every protocol-sdk surface change.

Both Base Sepolia (84532, testnet) and Base mainnet (8453) are deployed
and in :data:`SUPPORTED_CHAIN_IDS`. The zero-address guard in
``get_protocol_addresses`` remains as defence against a misconfigured
custom chain — it fails loudly instead of silently routing reads at the
zero address.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from kashdao_protocol_sdk.shared.errors import KashConfigError

# ---------------------------------------------------------------------------
# Canonical addresses (chain-agnostic)
# ---------------------------------------------------------------------------

#: ERC-4337 EntryPoint v0.7 — chain-agnostic CREATE2 address shared by
#: every EVM. Source: viem/account-abstraction ``entryPoint07Address``.
ENTRY_POINT_07_ADDRESS: Final[str] = "0x0000000071727De22E5E9d8BAf0edAc6f37da032"

#: Canonical SimpleAccount factory + implementation. CREATE2 addresses
#: are identical on every EVM chain. Sourced from permissionless.js.
#: Do **not** modify — these are the canonical permissionless.js
#: defaults used across the entire EVM ecosystem.
CANONICAL_SMART_ACCOUNT: Final[dict[str, str]] = {
    "factory_address": "0x91E60e0613810449d098b0b5Ec8b51A0FE8c8985",
    "implementation_address": "0xe6Cae83BdE06E4c305530e199D7217f42808555B",
    "entry_point_address": ENTRY_POINT_07_ADDRESS,
    "entry_point_version": "0.7",
}

#: Multicall3 canonical address — identical on every EVM chain.
MULTICALL3_ADDRESS: Final[str] = "0xca11bde05977b3631167028862be2a173976ca11"

ZERO_ADDRESS: Final[str] = "0x0000000000000000000000000000000000000000"


# ---------------------------------------------------------------------------
# ProtocolAddresses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SmartAccountConfig:
    """ERC-4337 SimpleAccount factory + implementation + EntryPoint addresses.

    Mirrors TS ``SmartAccountConfig``. Held inside
    :class:`ProtocolAddresses` so SA-mode code can resolve the right
    factory + EntryPoint per chain without importing canonical
    constants directly. Custom-chain support overrides this via
    ``CustomSmartAccountConfig``.
    """

    factory_address: str
    implementation_address: str
    entry_point_address: str
    entry_point_version: str = "0.7"


# Default SA config — same CREATE2 addresses on every standard EVM
# chain (sourced from permissionless.js v0.2.57 canonical defaults).
_DEFAULT_SMART_ACCOUNT: Final[SmartAccountConfig] = SmartAccountConfig(
    factory_address="0x91E60e0613810449d098b0b5Ec8b51A0FE8c8985",
    implementation_address="0xe6Cae83BdE06E4c305530e199D7217f42808555B",
    entry_point_address=ENTRY_POINT_07_ADDRESS,
    entry_point_version="0.7",
)


@dataclass(frozen=True, slots=True)
class ProtocolAddresses:
    """Per-chain protocol contract registry.

    Mirrors the TS ``ProtocolAddresses`` shape one-to-one. ``viem_chain``
    is dropped on the Python side — web3.py does not need a chain
    object; the chain id alone is enough.
    """

    chain_id: int
    name: str
    is_testnet: bool
    factory: str
    usdc: str
    oracle: str | None
    vault: str | None
    tokens1155: str | None
    param_registry: str | None
    smart_account: SmartAccountConfig = field(default_factory=lambda: _DEFAULT_SMART_ACCOUNT)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BASE_SEPOLIA: Final[ProtocolAddresses] = ProtocolAddresses(
    chain_id=84532,
    name="Base Sepolia",
    is_testnet=True,
    factory="0x4d3F803222316bFa1860788F2A80Bce25a404e7F",
    usdc="0x1F7268be50CfD7E2fF86b806E256B498C0db3A04",
    oracle="0xE068f310B70F9C6c7DCE081858E5354724dc936a",
    vault="0xa45Be09558b35ED35fC18371bFc7604bc2D930F5",
    tokens1155="0x561F73fCE117d60b057864F93fd92C48aA54928e",
    param_registry="0x811eee7C0F996F9BA2EfC2CAcA0368eF86e69fC9",
    smart_account=_DEFAULT_SMART_ACCOUNT,
)

BASE_MAINNET: Final[ProtocolAddresses] = ProtocolAddresses(
    chain_id=8453,
    name="Base",
    is_testnet=False,
    # Base mainnet launch deployment. Mirrors the TS registry
    # (src/shared/contracts/addresses.ts, chain 8453).
    factory="0x5aC139604CeAb5fcf8Af6f8a85c337adAd964087",
    usdc="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    oracle="0x16A5A01Dad652681F7b3c3C41A35905C57347399",
    vault="0x84ADF5B2B7948c64C1d3fDA3797e0291dF2B2889",
    tokens1155="0x00CD04410253A397F8f62A8dbd63e42EB8BD37eD",
    param_registry="0xf02E57D356dfb98C35e251DA4382Ea1c637D36A4",
    smart_account=_DEFAULT_SMART_ACCOUNT,
)

_REGISTRY: Final[dict[int, ProtocolAddresses]] = {
    BASE_SEPOLIA.chain_id: BASE_SEPOLIA,
    BASE_MAINNET.chain_id: BASE_MAINNET,
}

#: Chains where the Kash protocol contracts are deployed and usable
#: today. ``create_direct_client`` accepts only these. Both Base Sepolia
#: (84532, testnet) and Base mainnet (8453) are live — staging clients
#: pin 84532, production clients pin 8453.
SUPPORTED_CHAIN_IDS: Final[tuple[int, ...]] = (BASE_SEPOLIA.chain_id, BASE_MAINNET.chain_id)

#: Chains the library has metadata for. Currently identical to
#: :data:`SUPPORTED_CHAIN_IDS`; kept distinct so a future
#: registered-but-undeployed chain can be surfaced in UIs without
#: crashing ``get_protocol_addresses``.
KNOWN_CHAIN_IDS: Final[tuple[int, ...]] = (BASE_SEPOLIA.chain_id, BASE_MAINNET.chain_id)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def is_supported_chain_id(chain_id: int) -> bool:
    """Strict check — true only for chains where the protocol is live."""
    return chain_id in SUPPORTED_CHAIN_IDS


def is_known_chain_id(chain_id: int) -> bool:
    """Loose check — true for any chain with metadata, deployed or not."""
    return chain_id in KNOWN_CHAIN_IDS


def get_protocol_addresses(chain_id: int) -> ProtocolAddresses:
    """Resolve protocol addresses for a chain.

    Raises
    ------
    KashConfigError
        If the chain id is unknown, or if it is registered but the
        factory has not been deployed (``0x000…``). Both cases indicate
        misconfiguration that should fail loudly rather than silently
        route reads at the zero address.
    """
    entry = _REGISTRY.get(chain_id)
    if entry is None:
        raise KashConfigError(
            f"unsupported chainId {chain_id}",
            code="UNSUPPORTED_CHAIN",
            context={"chain_id": chain_id, "supported": list(SUPPORTED_CHAIN_IDS)},
        )
    if entry.factory == ZERO_ADDRESS:
        raise KashConfigError(
            f"chainId {chain_id} ({entry.name}) is registered but its factory "
            f"address is the zero address, so the Kash protocol cannot be "
            f"reached there. Use a supported chain "
            f"({', '.join(str(c) for c in SUPPORTED_CHAIN_IDS)}), or pass a "
            f"custom_chain= config with the correct deployed addresses. "
            f"(Detect this case programmatically with "
            f"is_known_chain_id({chain_id}) and not "
            f"is_supported_chain_id({chain_id}).)",
            code="CHAIN_NOT_DEPLOYED",
            context={
                "chain_id": chain_id,
                "name": entry.name,
                "supported_chain_ids": list(SUPPORTED_CHAIN_IDS),
            },
        )
    return entry


__all__ = [
    "BASE_MAINNET",
    "BASE_SEPOLIA",
    "CANONICAL_SMART_ACCOUNT",
    "ENTRY_POINT_07_ADDRESS",
    "KNOWN_CHAIN_IDS",
    "MULTICALL3_ADDRESS",
    "SUPPORTED_CHAIN_IDS",
    "ZERO_ADDRESS",
    "ProtocolAddresses",
    "SmartAccountConfig",
    "get_protocol_addresses",
    "is_known_chain_id",
    "is_supported_chain_id",
]
