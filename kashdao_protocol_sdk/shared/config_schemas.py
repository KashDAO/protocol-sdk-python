"""Shared validators for ``create_smart_account_client`` / ``create_eoa_client`` config.

Mirrors ``src/shared/config-schemas.ts``.

Both factories validate identical surface for chain id, custom_chain,
RPC URL, and lifecycle hooks. Keeping the validators here eliminates
the three-way DRY violation that would otherwise exist between
``smart_account/config.py`` and ``eoa/config.py``.

Mode-specific validation (signer adapter shape, bundler config, etc.)
stays in the per-mode config files.
"""

from __future__ import annotations

import re
from typing import Any

from kashdao_protocol_sdk.shared.contracts.addresses import (
    KNOWN_CHAIN_IDS,
    SUPPORTED_CHAIN_IDS,
)
from kashdao_protocol_sdk.shared.custom_chain import CustomChain
from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashConfigError

_HEX_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def validate_https_url(url: str, field_name: str = "rpc") -> None:
    """Reject malformed RPC URLs.

    URL must be ``https://…`` OR ``http://localhost…`` /
    ``http://127.0.0.1…``. The plain-HTTP exception is for local dev
    (Anvil, Hardhat); the SDK refuses plain-HTTP to remote hosts on
    principle. ``ws://`` and ``wss://`` are also accepted for
    WebSocket providers.
    """
    if not url:
        raise KashConfigError(
            f"{field_name} URL is empty",
            code=ErrorCode.INVALID_CONFIG,
            context={"field": field_name},
        )
    if not (
        url.startswith("https://")
        or url.startswith("http://localhost")
        or url.startswith("http://127.0.0.1")
        or url.startswith("ws://")
        or url.startswith("wss://")
    ):
        raise KashConfigError(
            f"{field_name} URL must use https://, ws://, wss://, or http://localhost (got '{url}')",
            code=ErrorCode.INVALID_CONFIG,
            context={"field": field_name, "url": url},
        )


def validate_hex_address(address: str, field_name: str = "address") -> None:
    """Reject anything not matching the strict 0x-prefixed 20-byte regex."""
    if not isinstance(address, str) or not _HEX_ADDRESS_RE.match(address):
        raise KashConfigError(
            f"{field_name} must be a 0x-prefixed 20-byte address (got {address!r})",
            code=ErrorCode.INVALID_OWNER_ADDRESS,
            context={"field": field_name, "value": address},
        )


def validate_custom_chain_shape(custom: Any, field_name: str = "custom_chain") -> None:
    """Shape-check a :class:`CustomChain`.

    Validation is shallow — we check the consumer didn't mistype the
    field shape. Address correctness is the consumer's responsibility.
    Kept loose so future :class:`CustomChain` additions don't break
    consumers.
    """
    if not isinstance(custom, CustomChain):
        raise KashConfigError(
            f"{field_name} must be a CustomChain instance",
            code=ErrorCode.INVALID_CONFIG,
            context={"field": field_name, "type": type(custom).__name__},
        )
    if not custom.name:
        raise KashConfigError(
            f"{field_name}.name must be a non-empty string",
            code=ErrorCode.INVALID_CONFIG,
            context={"field": field_name},
        )
    if not isinstance(custom.chain_id, int) or custom.chain_id <= 0:
        raise KashConfigError(
            f"{field_name}.chain_id must be a positive int",
            code=ErrorCode.INVALID_CONFIG,
            context={"field": field_name, "chain_id": custom.chain_id},
        )
    if not _HEX_ADDRESS_RE.match(custom.addresses.factory):
        raise KashConfigError(
            f"{field_name}.addresses.factory must be a 0x-prefixed 20-byte address",
            code=ErrorCode.INVALID_CONFIG,
            context={"field": f"{field_name}.addresses.factory"},
        )
    if not _HEX_ADDRESS_RE.match(custom.addresses.usdc):
        raise KashConfigError(
            f"{field_name}.addresses.usdc must be a 0x-prefixed 20-byte address",
            code=ErrorCode.INVALID_CONFIG,
            context={"field": f"{field_name}.addresses.usdc"},
        )


def validate_chain_id_with_optional_custom_chain(
    chain_id: int,
    custom_chain: CustomChain | None,
) -> None:
    """Conditional chain-id validation.

    When ``custom_chain`` is omitted, the chain id MUST be in the
    static registry. When set, any positive integer is allowed AND
    ``chain_id`` MUST equal ``custom_chain.chain_id`` (financial-path
    guard — see CHAIN_ID_MISMATCH).
    """
    if not isinstance(chain_id, int) or chain_id <= 0:
        raise KashConfigError(
            f"chain_id must be a positive int (got {chain_id!r})",
            code=ErrorCode.INVALID_CONFIG,
            context={"chain_id": chain_id},
        )
    if custom_chain is None:
        if chain_id not in KNOWN_CHAIN_IDS:
            raise KashConfigError(
                f"chain_id {chain_id} is not in the static registry. Pass a "
                "custom_chain=CustomChain(...) to bypass the registry for "
                "Anvil / Hardhat / Tenderly forks / sidechains.",
                code=ErrorCode.UNSUPPORTED_CHAIN,
                context={
                    "chain_id": chain_id,
                    "supported_chain_ids": list(SUPPORTED_CHAIN_IDS),
                },
            )
    else:
        if custom_chain.chain_id != chain_id:
            raise KashConfigError(
                f"chain_id ({chain_id}) does not match "
                f"custom_chain.chain_id ({custom_chain.chain_id}). The two MUST "
                "agree — txs are signed for `chain_id` and the addresses in "
                "`custom_chain` belong to `custom_chain.chain_id`. Mismatched "
                "values are nearly always a configuration bug.",
                code=ErrorCode.CHAIN_ID_MISMATCH,
                context={
                    "chain_id": chain_id,
                    "custom_chain_id": custom_chain.chain_id,
                    "custom_chain_name": custom_chain.name,
                },
            )


def validate_timeout_ms(value: int, field_name: str = "timeout_ms") -> None:
    """Bounded validation: ``(0, 120_000]`` ms."""
    if not isinstance(value, int) or value <= 0 or value > 120_000:
        raise KashConfigError(
            f"{field_name} must be in (0, 120_000], got {value!r}",
            code=ErrorCode.INVALID_CONFIG,
            context={"field": field_name, "value": value},
        )


def validate_signer_shape(signer: object, *, kind: str) -> None:
    """Structural check that a signer adapter exposes the required methods.

    ``kind`` is ``"eoa"`` or ``"smart-account"``. Errors surface as
    :class:`KashConfigError(code="INVALID_SIGNER")`.
    """
    if not hasattr(signer, "owner_address"):
        raise KashConfigError(
            "signer must expose an `owner_address` property",
            code=ErrorCode.INVALID_SIGNER,
            context={"signer_type": type(signer).__name__, "kind": kind},
        )
    if kind == "eoa":
        if not callable(getattr(signer, "sign_transaction", None)):
            raise KashConfigError(
                "EOA signer must expose an awaitable `sign_transaction(transaction)` method",
                code=ErrorCode.INVALID_SIGNER,
                context={"signer_type": type(signer).__name__, "kind": kind},
            )
    elif kind == "smart-account":
        if not callable(getattr(signer, "sign_user_op_hash", None)):
            raise KashConfigError(
                "smart-account signer must expose an awaitable "
                "`sign_user_op_hash(user_op_hash)` method",
                code=ErrorCode.INVALID_SIGNER,
                context={"signer_type": type(signer).__name__, "kind": kind},
            )
    else:
        raise KashConfigError(
            f"validate_signer_shape: unknown kind {kind!r}",
            code=ErrorCode.INVALID_CONFIG,
            context={"kind": kind},
        )


__all__ = [
    "validate_chain_id_with_optional_custom_chain",
    "validate_custom_chain_shape",
    "validate_hex_address",
    "validate_https_url",
    "validate_signer_shape",
    "validate_timeout_ms",
]
