"""Configuration schema for :func:`create_smart_account_client`.

Mirrors ``src/smart-account/config.ts``.

Validated at the factory entry point so misconfiguration fails fast
and loud at construction time rather than as a confusing runtime
error mid-trade.

Mode-shared validators (URL, address, custom_chain, hooks, chain-id
guard) live in :mod:`shared.config_schemas`. Only SA-specific pieces
(signer adapter shape, bundler config) are defined here.
"""

from __future__ import annotations

from dataclasses import dataclass

from kashdao_protocol_sdk.shared.config_schemas import (
    validate_chain_id_with_optional_custom_chain,
    validate_custom_chain_shape,
    validate_https_url,
    validate_signer_shape,
    validate_timeout_ms,
)
from kashdao_protocol_sdk.shared.custom_chain import CustomChain
from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashConfigError
from kashdao_protocol_sdk.shared.hooks import KashProtocolHooks
from kashdao_protocol_sdk.smart_account.types import SmartAccountSignerAdapter


@dataclass(frozen=True, slots=True)
class BundlerOptions:
    """Structured bundler config for vendor-specific header injection.

    Pass either a :class:`BundlerOptions` (named provider preset) or a
    plain URL string when constructing a client. The provider value is
    informational; the actual vendor preset (Alchemy / Pimlico /
    Flashbots / generic) is chosen by the consumer when wiring the
    client.
    """

    provider: str
    """One of ``"alchemy"``, ``"pimlico"``, ``"flashbots"``, ``"generic"``."""

    url: str
    """Bundler RPC URL (``https://...``)."""

    api_key: str | None = None
    """Optional API key — set as ``Authorization: Bearer <key>``."""


@dataclass(slots=True)
class SmartAccountClientConfig:
    """Validated configuration handed to :func:`create_smart_account_client`."""

    chain_id: int
    rpc: str
    """Chain-RPC endpoint URL. Consumer's own RPC; the SDK never uses
    Kash infrastructure."""

    signer: SmartAccountSignerAdapter
    """Smart-account signer adapter — see
    :class:`SmartAccountSignerAdapter`."""

    bundler: BundlerOptions | str | None = None
    """Bundler URL or structured preset. Optional at construction time
    (read-only consumers don't need one). The first call into any
    ``trades.*`` surface raises
    :class:`KashConfigError(code=ErrorCode.INVALID_CONFIG)` with
    ``context['missing']="bundler"`` if omitted."""

    custom_chain: CustomChain | None = None
    """Opt-in custom chain config — for local dev / forks. When set,
    bypasses the static chain registry."""

    timeout_ms: int = 30_000
    """Per-request timeout for the bundler RPC. Default 30 s."""

    hooks: KashProtocolHooks | None = None
    """Optional lifecycle hooks for telemetry/logging. Hook errors are
    swallowed; the SDK never blocks on hook completion."""


SmartAccountClientConfigInput = SmartAccountClientConfig
"""Alias matching the TS export shape (``SmartAccountClientConfigInput``)."""


def parse_smart_account_client_config(
    config: SmartAccountClientConfig,
) -> SmartAccountClientConfig:
    """Validate :class:`SmartAccountClientConfig` shape.

    Raises :class:`KashConfigError` on any failure with the same code
    catalog used by other config validators
    (``INVALID_CONFIG``, ``UNSUPPORTED_CHAIN``, ``CHAIN_ID_MISMATCH``,
    ``INVALID_SIGNER``).
    """
    validate_https_url(config.rpc, field_name="rpc")
    validate_timeout_ms(config.timeout_ms, field_name="timeout_ms")
    validate_signer_shape(config.signer, kind="smart-account")
    if config.custom_chain is not None:
        validate_custom_chain_shape(config.custom_chain)
    validate_chain_id_with_optional_custom_chain(config.chain_id, config.custom_chain)
    if config.bundler is not None:
        bundler_url = (
            config.bundler.url if isinstance(config.bundler, BundlerOptions) else config.bundler
        )
        validate_https_url(bundler_url, field_name="bundler.url")
        if isinstance(config.bundler, BundlerOptions):
            allowed = {"alchemy", "pimlico", "flashbots", "generic"}
            if config.bundler.provider not in allowed:
                raise KashConfigError(
                    f"bundler.provider must be one of {sorted(allowed)}, "
                    f"got {config.bundler.provider!r}",
                    code=ErrorCode.INVALID_CONFIG,
                    context={"provider": config.bundler.provider},
                )
    return config


__all__ = [
    "BundlerOptions",
    "SmartAccountClientConfig",
    "SmartAccountClientConfigInput",
    "parse_smart_account_client_config",
]
