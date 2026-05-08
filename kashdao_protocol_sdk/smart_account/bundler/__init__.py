"""Bundler RPC clients for ERC-4337 v0.7 Smart Account mode.

Mirrors ``src/smart-account/bundler/``.

Re-exports :func:`create_generic_bundler_client` plus three thin
provider presets (Alchemy, Pimlico, Flashbots) that wrap the generic
client with provider-specific defaults.
"""

from __future__ import annotations

from kashdao_protocol_sdk.smart_account.bundler.alchemy import (
    AlchemyBundlerConfig,
    create_alchemy_bundler_client,
)
from kashdao_protocol_sdk.smart_account.bundler.flashbots import (
    FlashbotsBundlerConfig,
    create_flashbots_bundler_client,
)
from kashdao_protocol_sdk.smart_account.bundler.generic import (
    BundlerCallOptions,
    BundlerClient,
    BundlerClientConfig,
    create_generic_bundler_client,
)
from kashdao_protocol_sdk.smart_account.bundler.pimlico import (
    PimlicoBundlerConfig,
    create_pimlico_bundler_client,
)

__all__ = [
    "AlchemyBundlerConfig",
    "BundlerCallOptions",
    "BundlerClient",
    "BundlerClientConfig",
    "FlashbotsBundlerConfig",
    "PimlicoBundlerConfig",
    "create_alchemy_bundler_client",
    "create_flashbots_bundler_client",
    "create_generic_bundler_client",
    "create_pimlico_bundler_client",
]
