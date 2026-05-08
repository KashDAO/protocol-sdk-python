"""Per-chain protocol address registry.

Mirrors ``src/shared/contracts/addresses.ts``.
Static literal — the runtime library has zero internal dependencies.
The TS side has a drift test against the canonical Kash deployments;
the Python side stays in lockstep by being a pure structural copy
reviewed on every protocol-sdk surface change.

Mainnet (8453) is registered with a zero factory and intentionally not
in :data:`SUPPORTED_CHAIN_IDS`. ``get_protocol_addresses(8453)`` raises
:class:`KashConfigError` so consumers fail loudly instead of silently
routing reads at the zero address.
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
    factory="0x16107196eb976a90B21cf84049CeA6ec0CE60148",
    usdc="0xD6F9b17fB20aB2E532ACBAB4C7eeCc0915278913",
    oracle="0x869CF1934FfA542DC2396F49AFdfb01cE445dC61",
    vault="0x822e2c5337Af726E1898ed80FBa85A5Da0Dc5a97",
    tokens1155="0x693D75A07ab4f11bd7C542A0d48fd5fE958a3Eb2",
    param_registry="0xdFEbec8D4E3C785Db02e6B5E8AF04Dc353682271",
    smart_account=_DEFAULT_SMART_ACCOUNT,
)

BASE_MAINNET: Final[ProtocolAddresses] = ProtocolAddresses(
    chain_id=8453,
    name="Base",
    is_testnet=False,
    # Mainnet not yet deployed — gated by get_protocol_addresses.
    factory=ZERO_ADDRESS,
    usdc="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    oracle=None,
    vault=None,
    tokens1155=None,
    param_registry=None,
    smart_account=_DEFAULT_SMART_ACCOUNT,
)

_REGISTRY: Final[dict[int, ProtocolAddresses]] = {
    BASE_SEPOLIA.chain_id: BASE_SEPOLIA,
    BASE_MAINNET.chain_id: BASE_MAINNET,
}

#: Chains where the Kash protocol contracts are deployed and usable
#: today. ``create_direct_client`` accepts only these. Mainnet (8453)
#: is intentionally NOT here yet — it lives in :data:`KNOWN_CHAIN_IDS`
#: until the protocol contracts deploy.
SUPPORTED_CHAIN_IDS: Final[tuple[int, ...]] = (BASE_SEPOLIA.chain_id,)

#: Chains the library has metadata for, including ones registered but
#: not yet deployed (e.g. Base mainnet pre-launch).
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
            f"chainId {chain_id} ({entry.name}) is registered but the Kash "
            f"protocol is not yet deployed there. Use chainId 84532 (Base "
            f"Sepolia) for testnet integration today; track mainnet "
            f"deployment status at "
            f"https://github.com/KashDAO/protocol-sdk-python/issues. "
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
