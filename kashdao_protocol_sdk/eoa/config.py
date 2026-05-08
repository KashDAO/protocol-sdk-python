"""Configuration schema for :func:`create_eoa_client`.

Mirrors ``src/eoa/config.ts``. Pydantic v2
validation rejects misconfigured chain ids and malformed RPC URLs at
client-creation time.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, field_validator

from kashdao_protocol_sdk.eoa.types import EoaSignerAdapter
from kashdao_protocol_sdk.shared.contracts.addresses import (
    KNOWN_CHAIN_IDS,
)
from kashdao_protocol_sdk.shared.custom_chain import CustomChain
from kashdao_protocol_sdk.shared.errors import KashConfigError
from kashdao_protocol_sdk.shared.hooks import KashProtocolHooks


@dataclass(slots=True)
class EoaClientConfig:
    """Validated configuration handed to :func:`create_eoa_client`.

    The dataclass form is what consumers pass; it's structurally
    validated via :class:`_EoaClientConfigSchema` before being accepted.
    """

    chain_id: int
    rpc: str
    signer: EoaSignerAdapter
    custom_chain: CustomChain | None = None
    timeout_ms: int = 30_000
    hooks: KashProtocolHooks | None = None


# Pydantic schema enforces the chain_id + rpc format constraints.
class _EoaClientConfigSchema(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    chain_id: int
    rpc: str
    timeout_ms: int = 30_000

    @field_validator("chain_id")
    @classmethod
    def _validate_chain_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(f"chain_id must be positive, got {value}")
        return value

    @field_validator("rpc")
    @classmethod
    def _validate_rpc(cls, value: str) -> str:
        if not value or not (
            value.startswith("https://")
            or value.startswith("http://localhost")
            or value.startswith("http://127.0.0.1")
            or value.startswith("ws://")
            or value.startswith("wss://")
        ):
            raise ValueError(
                f"rpc URL must use https://, ws://, wss://, or http://localhost (got '{value}')"
            )
        return value

    @field_validator("timeout_ms")
    @classmethod
    def _validate_timeout(cls, value: int) -> int:
        if value <= 0 or value > 120_000:
            raise ValueError(f"timeout_ms must be in (0, 120_000], got {value}")
        return value


EoaClientConfigInput = EoaClientConfig
"""Alias matching the TS export shape (``EoaClientConfigInput``)."""


def parse_eoa_client_config(config: EoaClientConfig) -> EoaClientConfig:
    """Validate :class:`EoaClientConfig` shape, raising :class:`KashConfigError` on failure.

    Returns the same object on success.
    """
    try:
        _EoaClientConfigSchema.model_validate(
            {
                "chain_id": config.chain_id,
                "rpc": config.rpc,
                "timeout_ms": config.timeout_ms,
            }
        )
    except Exception as cause:
        raise KashConfigError(
            "invalid create_eoa_client config",
            code="INVALID_CONFIG",
            context={"chain_id": config.chain_id, "rpc": config.rpc},
            cause=cause,
        ) from cause

    if not _has_signer_shape(config.signer):
        raise KashConfigError(
            "signer must have an `owner_address` property and an awaitable "
            "`sign_transaction(transaction)` method",
            code="INVALID_SIGNER",
            context={"signer_type": type(config.signer).__name__},
        )
    if config.custom_chain is None and config.chain_id not in KNOWN_CHAIN_IDS:
        raise KashConfigError(
            f"chain_id {config.chain_id} is not in the static registry. Pass a "
            "`custom_chain=CustomChain(...)` to bypass the registry for "
            "Anvil / Hardhat / Tenderly forks / sidechains.",
            code="UNKNOWN_CHAIN",
            context={
                "chain_id": config.chain_id,
                "known_chain_ids": list(KNOWN_CHAIN_IDS),
            },
        )
    if config.custom_chain is not None and config.custom_chain.chain_id != config.chain_id:
        # Financial-path guard: a mismatch would sign txs for
        # ``config.chain_id`` while reading addresses from a deploy
        # targeting a different chain. Surface immediately.
        raise KashConfigError(
            f"EoaClientConfig.chain_id ({config.chain_id}) does not match "
            f"custom_chain.chain_id ({config.custom_chain.chain_id}). The two "
            "MUST agree — txs are signed for `chain_id` and the addresses "
            "in `custom_chain` belong to `custom_chain.chain_id`. Mismatched "
            "values are nearly always a configuration bug.",
            code="CHAIN_ID_MISMATCH",
            context={
                "chain_id": config.chain_id,
                "custom_chain_id": config.custom_chain.chain_id,
                "custom_chain_name": config.custom_chain.name,
            },
        )
    return config


def _has_signer_shape(obj: object) -> bool:
    return hasattr(obj, "owner_address") and callable(getattr(obj, "sign_transaction", None))


__all__ = [
    "EoaClientConfig",
    "EoaClientConfigInput",
    "parse_eoa_client_config",
]
