"""Smart account address derivation + deployment-status checks.

Mirrors ``src/smart-account/account/address.ts``.

The canonical ``SimpleAccountFactory`` deployed at
``0x91E60e0613810449d098b0b5Ec8b51A0FE8c8985`` (CREATE2-deterministic
across every EVM where it's deployed) exposes a view function
``getAddress(owner, salt)`` that returns the deterministic address the
factory will deploy for that pair. We use this on-chain view as the
source of truth — it always matches what ``createAccount`` will deploy
and is robust against any future changes to the factory's CREATE2
init-code derivation.

Most consumers want the synchronous-feeling top-level helper
(:func:`compute_smart_account_address`) that creates an
:class:`AsyncWeb3` internally; advanced consumers using their own
provider call :func:`compute_smart_account_address_via_web3` directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

from eth_typing import ChecksumAddress, HexStr
from eth_utils import keccak  # type: ignore[attr-defined]
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.providers.persistent import WebSocketProvider

from kashdao_protocol_sdk.shared.contracts.abis import SIMPLE_ACCOUNT_FACTORY_ABI
from kashdao_protocol_sdk.shared.contracts.addresses import get_protocol_addresses
from kashdao_protocol_sdk.shared.custom_chain import CustomChain, resolve_custom_chain
from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashChainError, KashConfigError
from kashdao_protocol_sdk.shared.types import Hex

_HEX_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


@dataclass(frozen=True, slots=True)
class ComputeSmartAccountAddressParams:
    """Parameters for the top-level :func:`compute_smart_account_address` helper.

    Includes RPC config so the helper is usable without constructing a
    full :class:`SmartAccountClient` — common during onboarding when
    the consumer doesn't yet have a signer.
    """

    chain_id: int
    """Chain id. Must be in the static registry unless ``custom_chain`` is supplied."""

    rpc: str
    """RPC endpoint URL (HTTPS, ``ws://``/``wss://``, or
    ``http://localhost`` / ``http://127.0.0.1`` for dev)."""

    owner_address: Hex
    """EOA owner address — the key that controls the smart account."""

    salt: int = 0
    """CREATE2 salt. Default ``0`` matches permissionless.js / web3auth
    defaults — the conventional choice for the first SA per owner. Use
    a different salt to derive multiple distinct SAs per owner."""

    timeout_ms: int = 10_000
    """Per-request timeout in milliseconds. Default 10 s."""

    custom_chain: CustomChain | None = None
    """Opt-in custom-chain config — for local Anvil / Hardhat / Tenderly
    forks / sidechains. When set, bypasses the static chain registry
    (and the mainnet-not-deployed guard). The SimpleAccount factory
    address comes from ``custom_chain.smart_account.factory_address``
    (defaults to canonical permissionless.js if omitted)."""


async def compute_smart_account_address(
    params: ComputeSmartAccountAddressParams,
) -> Hex:
    """Derive the deterministic smart account address for an owner.

    Returns the same address whether the SA is deployed yet or not —
    the factory's ``getAddress`` is a pure CREATE2 computation. Use
    :func:`is_smart_account_deployed` to check actual deployment
    status.

    Example::

        from kashdao_protocol_sdk import (
            ComputeSmartAccountAddressParams,
            compute_smart_account_address,
        )

        sa = await compute_smart_account_address(
            ComputeSmartAccountAddressParams(
                chain_id=84532,
                rpc="https://sepolia.base.org",
                owner_address="0xabc...",
            )
        )
        print("Send USDC + ETH to:", sa)
    """
    if not _HEX_ADDRESS_RE.match(params.owner_address):
        raise KashConfigError(
            f"owner_address must be a 0x-prefixed 20-byte address, got {params.owner_address!r}",
            code=ErrorCode.INVALID_OWNER_ADDRESS,
            context={"owner_address": params.owner_address},
        )

    addresses = (
        resolve_custom_chain(params.chain_id, params.custom_chain)
        if params.custom_chain is not None
        else get_protocol_addresses(params.chain_id)
    )

    timeout_seconds = params.timeout_ms / 1000.0
    if params.rpc.startswith(("ws://", "wss://")):
        provider = WebSocketProvider(params.rpc)
        web3 = AsyncWeb3(provider)
    else:
        provider = AsyncHTTPProvider(  # type: ignore[assignment]
            params.rpc, request_kwargs={"timeout": timeout_seconds}
        )
        web3 = AsyncWeb3(provider)

    return await compute_smart_account_address_via_web3(
        web3,
        addresses.smart_account.factory_address,
        params.owner_address,
        params.salt,
    )


async def compute_smart_account_address_via_web3(
    web3: AsyncWeb3,
    factory_address: Hex,
    owner_address: Hex,
    salt: int = 0,
) -> Hex:
    """Same as :func:`compute_smart_account_address` but takes a pre-built
    :class:`AsyncWeb3`. Used internally by ``client.account.compute_address``
    to share the consumer's already-bound RPC.
    """
    try:
        result = await web3.eth.call(
            {
                "to": AsyncWeb3.to_checksum_address(factory_address),
                "data": cast(HexStr, _encode_get_address_calldata(owner_address, salt)),
            }
        )
    except Exception as cause:
        raise KashChainError(
            f"failed to derive smart account address for owner {owner_address} (salt={salt})",
            code=ErrorCode.SA_DERIVATION_FAILED,
            context={
                "owner_address": owner_address,
                "salt": str(salt),
                "factory": factory_address,
            },
            is_retryable=True,
            cause=cause,
        ) from cause

    if len(result) < 32:
        raise KashChainError(
            f"factory.getAddress returned malformed result for owner {owner_address}",
            code=ErrorCode.SA_DERIVATION_FAILED,
            context={
                "owner_address": owner_address,
                "salt": str(salt),
                "factory": factory_address,
                "result_bytes": len(result),
            },
        )
    # The 20-byte address occupies the rightmost bytes of the 32-byte
    # word; left-padded with zeros per ABI encoding.
    address_bytes = bytes(result[-20:])
    return AsyncWeb3.to_checksum_address(address_bytes)


async def is_smart_account_deployed(
    web3: AsyncWeb3,
    address: Hex,
) -> bool:
    """Whether a smart account has been deployed on-chain.

    A return of ``False`` does NOT mean the address is invalid — it
    just means the SA has not been deployed yet. The very next UserOp
    targeting that SA can include ``factory`` + ``factory_data`` to
    deploy it on first use.
    """
    try:
        bytecode = await web3.eth.get_code(_to_checksum(address))
    except Exception as cause:
        raise KashChainError(
            f"failed to check deployment status for {address}",
            code=ErrorCode.SA_DEPLOYMENT_CHECK_FAILED,
            context={"address": address},
            is_retryable=True,
            cause=cause,
        ) from cause
    # web3.py returns HexBytes(b"") for accounts with no code.
    return bool(bytecode) and len(bytecode) > 0


def _to_checksum(address: Hex) -> ChecksumAddress:
    return AsyncWeb3.to_checksum_address(address)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_get_address_calldata(owner: Hex, salt: int) -> Hex:
    """Encode ``SimpleAccountFactory.getAddress(owner, salt)`` calldata.

    Selector + ABI-encoded args. We encode by hand so this helper has
    zero cost on import (no contract object construction).
    """
    from eth_abi import encode as abi_encode  # type: ignore[attr-defined]
    from hexbytes import HexBytes

    # getAddress(address,uint256) — selector 0x8cb84e18.
    selector = bytes(keccak(text="getAddress(address,uint256)")[:4])
    payload = abi_encode(["address", "uint256"], [owner, salt])
    return HexBytes(selector + payload).to_0x_hex()


# Reach into the ABI to assert the selector matches the canonical one
# at import time. This catches any future drift from the vendored
# SimpleAccountFactory ABI.
def _assert_selector_matches() -> None:
    for entry in SIMPLE_ACCOUNT_FACTORY_ABI:
        if entry.get("type") == "function" and entry.get("name") == "getAddress":
            inputs = entry.get("inputs", [])
            sig = f"getAddress({','.join(i['type'] for i in inputs)})" if inputs else "getAddress()"
            expected = bytes(keccak(text=sig)[:4])
            actual = bytes(keccak(text="getAddress(address,uint256)")[:4])
            if expected != actual:
                raise RuntimeError(
                    f"SimpleAccountFactory.getAddress signature drifted: "
                    f"vendored ABI says {sig!r}, hardcoded says "
                    "getAddress(address,uint256)"
                )
            return


_assert_selector_matches()


__all__ = [
    "ComputeSmartAccountAddressParams",
    "compute_smart_account_address",
    "compute_smart_account_address_via_web3",
    "is_smart_account_deployed",
]
