"""Flashbots Protect bundler preset.

Mirrors ``src/smart-account/bundler/flashbots.ts``.

MEV-protected: submission goes through Flashbots' bundler RPC; failed
transactions don't appear in the public mempool, reducing front-running
risk on profitable trades.

**Chain support.** As of writing, the canonical Flashbots Protect URL
(``https://rpc.flashbots.net/fast``) bundles for **Ethereum mainnet
only** — it returns chain id ``0x1`` and rejects UserOps targeted at
Base, Base Sepolia, or any other chain. There is no ``url`` default
here for that reason; pass an explicit URL whose chain matches the
``chain_id`` you configured on the client. For Base / Base Sepolia, use
Pimlico, Alchemy, or Stackup instead — see the matching presets.
"""

from __future__ import annotations

from kashdao_protocol_sdk.smart_account.bundler.generic import (
    BundlerClient,
    BundlerClientConfig,
    create_generic_bundler_client,
)

FlashbotsBundlerConfig = BundlerClientConfig
"""Alias matching the TS export shape.

The TS variant uses ``Partial<BundlerClientConfig> & { url: string }``
to make the URL the only required field. Python's
:class:`BundlerClientConfig` already requires ``url``, so the alias is
identical.
"""


def create_flashbots_bundler_client(config: FlashbotsBundlerConfig) -> BundlerClient:
    """Build a Flashbots-Protect-flavored :class:`BundlerClient`."""
    return create_generic_bundler_client(config)


__all__ = ["FlashbotsBundlerConfig", "create_flashbots_bundler_client"]
