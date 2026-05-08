"""Best-effort close for an :class:`AsyncWeb3`'s underlying provider.

Shared between EOA mode (:func:`create_eoa_client`) and SA mode
(:func:`create_smart_account_client`) so both clients have identical
shutdown semantics. Called from each client's ``aclose()`` inside a
``try/finally``, so this helper MUST honor a strict invariant.
"""

from __future__ import annotations

import asyncio

from web3 import AsyncWeb3


async def close_web3_provider(web3: AsyncWeb3) -> None:
    """Best-effort close of an :class:`AsyncWeb3`'s underlying provider.

    **MUST NOT raise** for the realistic idempotent-close error set
    (``RuntimeError`` "event loop is closed", ``OSError`` broken pipe,
    ``ConnectionError`` already disconnected). Both ``EoaClient.aclose``
    and ``SmartAccountClient.aclose`` run this in a ``finally`` clause
    after closing the bundler / signer; if it raised inside the
    finally, it would mask the original exception. ``CancelledError``
    is intentionally NOT swallowed — cancellation must propagate.

    web3.py 7's ``AsyncHTTPProvider`` and ``WebSocketProvider`` both
    expose ``disconnect()`` (async); calling it releases the underlying
    aiohttp / websocket session. Idempotent.

    For consumers who want to share a connection pool across multiple
    clients, construct an ``AsyncWeb3`` externally and pass it in via
    a future ``web3=`` injection point (not yet supported; today both
    EOA and SA clients always own the web3 they construct).
    """
    provider = getattr(web3, "provider", None)
    if provider is None:
        return
    disconnect = getattr(provider, "disconnect", None)
    if not callable(disconnect):
        return
    try:
        result = disconnect()
        if asyncio.iscoroutine(result):
            await result
    except (RuntimeError, OSError, ConnectionError):
        # Idempotent close — these are the failure modes a "second
        # close" surfaces (event-loop-closed, broken pipe, already-
        # disconnected). Anything narrower would also need to surface;
        # this set was chosen because they don't indicate a
        # user-actionable problem at teardown time.
        pass


__all__ = ["close_web3_provider"]
