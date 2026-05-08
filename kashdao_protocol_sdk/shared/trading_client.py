"""``TradingClient`` — discriminated union over the two trading modes.

Mirrors ``src/shared/trading-client.ts``.

Lives in :mod:`kashdao_protocol_sdk.shared` (not in either mode's
sub-package) because both mode files would have to mutually import
each other otherwise.

Most consumers don't need this — they pick one mode and type against
``EoaClient`` or ``SmartAccountClient`` directly. The union exists for
libraries that want to support both modes transparently (analytics
dashboards, multi-mode trading wrappers, CLI tools).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from kashdao_protocol_sdk.eoa.client import EoaClient
    from kashdao_protocol_sdk.smart_account.client import SmartAccountClient


TradingClient = Union["EoaClient", "SmartAccountClient"]


def is_eoa_client(client: TradingClient) -> bool:
    """``True`` when ``client.mode == "eoa"``."""
    return getattr(client, "mode", None) == "eoa"


def is_smart_account_client(client: TradingClient) -> bool:
    """``True`` when ``client.mode == "smart-account"``."""
    return getattr(client, "mode", None) == "smart-account"


__all__ = [
    "TradingClient",
    "is_eoa_client",
    "is_smart_account_client",
]
