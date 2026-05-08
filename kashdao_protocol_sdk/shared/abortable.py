"""Abort-aware sleep primitive.

Mirrors ``src/shared/abortable.ts``.

Used wherever the SDK runs an in-process delay that must wake up
immediately on consumer abort:

* ``BundlerClient.wait_for_receipt`` — between polls
* ``with_retry`` — between retry attempts
* any future polling helper

Without this, an abort that fires during the inter-attempt delay
waits the full ``ms`` before the next abort check runs.

The ``signal`` parameter is intentionally typed as ``object | None``
for parity with the SDK's flexible signal handling — it accepts
:class:`asyncio.Event`, anything with a truthy ``aborted`` attribute,
or a custom flag class. ``None`` is a no-op so callers can pass
optional cancellation tokens uniformly.
"""

from __future__ import annotations

import asyncio

from kashdao_protocol_sdk.shared.errors import KashAbortedError


async def abortable_sleep(ms: int, signal: object | None) -> None:
    """Sleep ``ms`` milliseconds; raise :class:`KashAbortedError` on abort.

    If ``signal`` is ``None``, behaves like :func:`asyncio.sleep`.
    Otherwise polls the abort signal every iteration of the wait
    via :func:`asyncio.wait_for` against the signal's ``wait()``
    method, surfacing :class:`KashAbortedError` instead of the
    underlying :class:`asyncio.CancelledError` when the abort fires.

    The signal can be:

    * :class:`asyncio.Event` — waits on ``signal.wait()`` (preferred).
    * Anything with an ``aborted`` attribute — checked before sleep
      and raises immediately if already aborted.
    * Anything with ``is_set()`` — same shortcut as ``aborted``.
    """
    if ms <= 0:
        return
    if signal is None:
        await asyncio.sleep(ms / 1000.0)
        return

    # Fast-path: already aborted? Surface immediately.
    if _signal_aborted(signal):
        raise KashAbortedError("sleep aborted by signal", code="OPERATION_ABORTED")

    # ``asyncio.Event``: wake the wait when the event fires.
    wait_method = getattr(signal, "wait", None)
    if callable(wait_method):
        try:
            await asyncio.wait_for(wait_method(), timeout=ms / 1000.0)
        except asyncio.TimeoutError:
            return
        except asyncio.CancelledError as cause:
            raise KashAbortedError(
                "sleep aborted by signal", code="OPERATION_ABORTED", cause=cause
            ) from cause
        # ``wait()`` returned → event was set → abort.
        raise KashAbortedError("sleep aborted by signal", code="OPERATION_ABORTED")

    # Fallback: poll the abort flag in a tight loop.
    deadline = asyncio.get_running_loop().time() + (ms / 1000.0)
    poll_interval = min(0.05, ms / 1000.0)
    while asyncio.get_running_loop().time() < deadline:
        if _signal_aborted(signal):
            raise KashAbortedError("sleep aborted by signal", code="OPERATION_ABORTED")
        await asyncio.sleep(poll_interval)


def _signal_aborted(signal: object) -> bool:
    """Return True if ``signal`` is an aborted-shaped flag.

    Mirrors :func:`throw_if_aborted`'s shape detection without raising.
    """
    aborted = getattr(signal, "aborted", None)
    if aborted is True:
        return True
    is_set = getattr(signal, "is_set", None)
    if callable(is_set):
        return bool(is_set())
    return False


__all__ = ["abortable_sleep"]
