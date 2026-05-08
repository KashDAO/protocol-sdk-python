"""Pimlico bundler preset.

Mirrors ``src/smart-account/bundler/pimlico.ts``.

Pimlico's bundler URLs are typically
``https://api.pimlico.io/v2/<chain>/rpc?apikey=<key>``. Consumers pass
the URL (or ``api_key`` for header-based auth on private deployments).
"""

from __future__ import annotations

from kashdao_protocol_sdk.smart_account.bundler.generic import (
    BundlerClient,
    BundlerClientConfig,
    create_generic_bundler_client,
)

PimlicoBundlerConfig = BundlerClientConfig
"""Alias matching the TS export shape; identical to :class:`BundlerClientConfig`."""


def create_pimlico_bundler_client(config: PimlicoBundlerConfig) -> BundlerClient:
    """Build a Pimlico-flavored :class:`BundlerClient`."""
    return create_generic_bundler_client(config)


__all__ = ["PimlicoBundlerConfig", "create_pimlico_bundler_client"]
