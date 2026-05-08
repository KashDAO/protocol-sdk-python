"""Mode-agnostic market reads — minimal projection, full state, quotes, real-time watch.

Used identically by :mod:`kashdao_protocol_sdk.eoa` and
:mod:`kashdao_protocol_sdk.smart_account` clients.
"""

from kashdao_protocol_sdk.shared.markets.get import get_market_minimal
from kashdao_protocol_sdk.shared.markets.quote import QuoteParams, get_quote
from kashdao_protocol_sdk.shared.markets.state import get_market_state
from kashdao_protocol_sdk.shared.markets.watch import (
    WatchConnectionState,
    WatchEvent,
    WatchOptions,
    WatchSubscription,
    watch_market,
)

__all__ = [
    "QuoteParams",
    "WatchConnectionState",
    "WatchEvent",
    "WatchOptions",
    "WatchSubscription",
    "get_market_minimal",
    "get_market_state",
    "get_quote",
    "watch_market",
]
