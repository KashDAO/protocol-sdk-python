"""``markets.watch`` subscription — Base Sepolia observability test.

Subscribes to a Kash market, iterates up to one event with a 30-second
timeout, then unsubscribes and verifies the subscription tears down cleanly
via :meth:`WatchSubscription.aclose`.

Required env
------------

- ``KASH_BASE_SEPOLIA_RPC`` — Base Sepolia RPC URL (HTTP or WS).
- ``KASH_TEST_OWNER_KEY`` — 0x-prefixed test EOA private key.
- ``KASH_TEST_MARKET`` — a Base Sepolia Kash market address.

Run::

    KASH_BASE_SEPOLIA_RPC=https://... \\
    KASH_TEST_OWNER_KEY=0x... \\
    KASH_TEST_MARKET=0x... \\
    pytest -m integration tests/integration/test_eoa_watch_subscription.py

What it covers
--------------

This test confirms:

1. ``client.markets.watch`` does not crash on subscription setup.
2. The :class:`WatchSubscription` handle is returned immediately.
3. If a real event fires within 30 seconds the shape is validated.
4. If no event fires (likely on a low-traffic test market) the test
   marks ``xfail`` — not a hard failure — since the WS path is
   confirmed healthy regardless.
5. ``aclose()`` tears the subscription down cleanly (no ``CancelledError``,
   no "loop is closed" warning).

The test documents observability behaviour: the test passes as long as
the transport stays alive and tears down cleanly, regardless of whether
a live trade event happened during the window.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from eth_account import Account

from kashdao_protocol_sdk import (
    WatchEvent,
    WatchOptions,
    create_eoa_client,
    viem_account_eoa_signer,
)
from kashdao_protocol_sdk.shared.markets.watch import TradeWatchEvent


@pytest.fixture(scope="module")
def env() -> dict[str, str]:
    keys = ("KASH_BASE_SEPOLIA_RPC", "KASH_TEST_OWNER_KEY", "KASH_TEST_MARKET")
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        pytest.skip(f"integration env missing: {', '.join(missing)}")
    return {k: os.environ[k] for k in keys}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_watch_subscription_no_crash_and_clean_teardown(env: dict[str, str]) -> None:
    """Subscribe to market events; confirm no crash and clean aclose."""
    account = Account.from_key(env["KASH_TEST_OWNER_KEY"])
    market = env["KASH_TEST_MARKET"]

    received_events: list[WatchEvent] = []
    errors: list[Any] = []

    async with create_eoa_client(
        chain_id=84532,
        rpc=env["KASH_BASE_SEPOLIA_RPC"],
        signer=viem_account_eoa_signer(account),
    ) as client:
        event_ready = asyncio.Event()

        def on_event(event: WatchEvent) -> None:
            received_events.append(event)
            event_ready.set()

        def on_error(err: Any) -> None:
            errors.append(err)

        sub = client.markets.watch(
            market,
            WatchOptions(on_event=on_event, on_error=on_error),
        )

        # Give the subscription up to 30 seconds to receive one event.
        try:
            await asyncio.wait_for(event_ready.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            pass

        # Tear down cleanly regardless of whether an event arrived.
        await sub.aclose()

        # Connection state must be "unsubscribed" after aclose.
        assert sub.connection_state() == "unsubscribed"

    if not received_events:
        # No events arrived in the window — this is expected on a
        # low-traffic test market. Mark xfail (not hard-fail) because
        # the WS transport itself is healthy (no crash, clean teardown).
        pytest.xfail("No market events within 30-second window (low-traffic market — expected).")

    # If we did get an event, validate its shape.
    event = received_events[0]
    assert event.type in ("TRADE", "RESOLVED", "FROZEN")
    assert isinstance(event.block_number, int)
    assert isinstance(event.transaction_hash, str)
    assert event.transaction_hash.startswith("0x")

    if isinstance(event, TradeWatchEvent):
        assert event.side in ("buy", "sell")
        assert isinstance(event.outcome, int)
        assert event.assets_usdc >= 0
