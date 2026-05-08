"""Alchemy bundler preset.

Mirrors ``src/smart-account/bundler/alchemy.ts``.

Alchemy's account-abstraction endpoints typically live at
``https://<chain>.g.alchemy.com/v2/<key>``. The consumer either embeds
the key in the URL or supplies it via ``api_key`` for ``Authorization:
Bearer``-style auth on enterprise deployments.
"""

from __future__ import annotations

from kashdao_protocol_sdk.smart_account.bundler.generic import (
    BundlerClient,
    BundlerClientConfig,
    create_generic_bundler_client,
)

AlchemyBundlerConfig = BundlerClientConfig
"""Alias matching the TS export shape; identical to :class:`BundlerClientConfig`."""


def create_alchemy_bundler_client(config: AlchemyBundlerConfig) -> BundlerClient:
    """Build an Alchemy-flavored :class:`BundlerClient`."""
    return create_generic_bundler_client(config)


__all__ = ["AlchemyBundlerConfig", "create_alchemy_bundler_client"]
