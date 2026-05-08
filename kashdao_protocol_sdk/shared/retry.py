"""Bounded retry helper for chain reads.

Mirrors the ``RetryOptions`` type from the TS SDK. Used by
``client.markets.*`` and ``client.account.*`` to retry transient RPC
failures (typically socket timeouts or 5xx responses). **Never used
on the submit path** — submission retries can cause double-spends.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from kashdao_protocol_sdk.shared.errors import KashAbortedError, KashProtocolError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryOptions:
    """Bounded retry config for chain reads."""

    retries: int = 0
    """Number of retry attempts after the first call. ``0`` means no retry."""

    base_delay_ms: int = 100
    """Initial delay before the first retry, in milliseconds. Exponential growth thereafter."""

    max_delay_ms: int = 5_000
    """Cap on per-attempt delay (after backoff)."""

    jitter: bool = True
    """Add up to ±20% jitter to each delay."""


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    options: RetryOptions | None = None,
) -> T:
    """Call ``fn`` with bounded retries on retryable :class:`KashProtocolError`.

    A retryable error is one with ``is_retryable=True``. Non-retryable
    errors propagate immediately. :class:`KashAbortedError` always
    propagates immediately.
    """
    opts = options or RetryOptions()
    attempt = 0
    while True:
        try:
            return await fn()
        except KashAbortedError:
            raise
        except KashProtocolError as err:
            if not getattr(err, "is_retryable", False) or attempt >= opts.retries:
                raise
        except Exception:
            raise
        attempt += 1
        delay = min(
            opts.base_delay_ms * (2 ** (attempt - 1)),
            opts.max_delay_ms,
        )
        if opts.jitter:
            delay = int(delay * (1 + random.uniform(-0.2, 0.2)))
        delay = max(delay, 0)
        await asyncio.sleep(delay / 1000.0)


__all__ = ["RetryOptions", "with_retry"]
