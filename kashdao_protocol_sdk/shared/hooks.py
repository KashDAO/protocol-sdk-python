"""Lifecycle hooks — fire-and-forget observability.

Mirrors ``src/shared/hooks.ts``.

The SDK itself never logs anything. Hooks are the only telemetry
surface — wire them into your existing logger / metrics / tracing
when constructing the client.

Six callback events covering bundler RPC, signer calls, and
``send.*`` orphaned-wait fallbacks. Hooks are called via
:func:`safe_fire` — a misbehaving hook MUST NOT add latency to the
request path. Async hooks are scheduled but never awaited; sync
hooks are called inline; exceptions are caught and a single
:class:`UserWarning` is emitted on the first failure per process.

Designed for telemetry / structured logging / Prometheus counters.
**Never use hooks for control flow.**
"""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from kashdao_protocol_sdk.shared.types import Hex

# Strong references to in-flight async hook tasks. Without this, the
# garbage collector can collect a task object before its coroutine
# finishes (documented in :func:`asyncio.create_task`'s notes), which
# silently drops hook deliveries.
_in_flight_hook_tasks: set[asyncio.Task[None]] = set()

# Whether we've already emitted a "hook raised" warning on this process.
# We warn at most once so that a misbehaving hook can't drown
# observability output, while still surfacing the breakage on first
# occurrence so integrators can debug telemetry pipelines.
_hook_warning_emitted: bool = False


# ---------------------------------------------------------------------------
# Bundler events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BundlerRequestEvent:
    """Fired before every bundler JSON-RPC call."""

    method: str
    """Bundler JSON-RPC method, e.g. ``eth_sendUserOperation``."""

    url: str
    """Bundler URL the request is being sent to."""


@dataclass(frozen=True, slots=True)
class BundlerResponseEvent:
    """Fired on a successful bundler JSON-RPC response."""

    method: str
    url: str
    duration_ms: float
    """Round-trip duration (request build → response parsed)."""

    status: int
    """HTTP status code."""


@dataclass(frozen=True, slots=True)
class BundlerErrorEvent:
    """Fired on a bundler JSON-RPC error."""

    method: str
    url: str
    duration_ms: float
    error: BaseException
    """The error the bundler call is about to raise."""


# ---------------------------------------------------------------------------
# Signer events
# ---------------------------------------------------------------------------


SignerKind = Literal["userop-hash", "typed-data", "transaction"]
"""Discriminator for what a signer is being asked to sign.

* ``"userop-hash"`` — SA mode ``sign_user_op_hash(hash)``
* ``"typed-data"`` — SA mode optional ``sign_typed_data_v4(typed_data)``
* ``"transaction"`` — EOA mode ``sign_transaction(tx)``
"""


@dataclass(frozen=True, slots=True)
class SignerRequestEvent:
    """Fired before a signer adapter call."""

    owner_address: Hex
    """EOA address of the signer being asked to sign."""

    kind: SignerKind


@dataclass(frozen=True, slots=True)
class SignerErrorEvent:
    """Fired on a signer adapter failure."""

    owner_address: Hex
    kind: SignerKind
    error: BaseException


# ---------------------------------------------------------------------------
# Wait-orphaned event
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EoaWaitOrphanedEvent:
    """``send.*`` (EOA mode) gave up waiting for inclusion.

    Fired when the consumer aborted while ``wait_for_receipt`` was
    polling. The underlying receipt poll keeps running in the background
    until ``wait_timeout_ms`` elapses or the receipt arrives — but the
    consumer doesn't see it. Production deployments use this hook to
    count orphans and alert when accumulation suggests they should
    switch to ``wait=False`` + manual polling.
    """

    transaction_hash: Hex
    """Submission tx hash whose wait was aborted."""

    chain_id: int
    wait_timeout_ms: int | None
    """Upper bound on how long the orphaned poll keeps running."""

    reason: Literal["aborted"] = "aborted"
    mode: Literal["eoa"] = "eoa"


@dataclass(frozen=True, slots=True)
class SmartAccountWaitOrphanedEvent:
    """``send.*`` (SA mode) gave up waiting for inclusion.

    Two trigger conditions:

    * ``reason="aborted"`` — consumer cancelled mid-wait.
    * ``reason="timeout"`` — bundler poll exhausted ``wait_timeout_ms``
      without inclusion. The UserOp is still in the bundler / EntryPoint
      pipeline and may execute; the SDK has just given up watching.
    """

    user_op_hash: Hex
    chain_id: int
    wait_timeout_ms: int | None
    reason: Literal["aborted", "timeout"]
    mode: Literal["smart-account"] = "smart-account"


WaitOrphanedEvent = EoaWaitOrphanedEvent | SmartAccountWaitOrphanedEvent
"""Discriminated union over the two ``send.*`` orphaned-wait shapes."""


# ---------------------------------------------------------------------------
# Hook surface
# ---------------------------------------------------------------------------


_AnyEvent = (
    BundlerRequestEvent
    | BundlerResponseEvent
    | BundlerErrorEvent
    | SignerRequestEvent
    | SignerErrorEvent
    | EoaWaitOrphanedEvent
    | SmartAccountWaitOrphanedEvent
)
_HookCallable = Callable[[Any], Awaitable[None] | None]


class KashProtocolHooks(Protocol):
    """Pluggable observability callbacks.

    Pass an object satisfying this Protocol on ``create_*_client``
    config. All callbacks are optional. The SDK calls them via
    :func:`safe_fire` so exceptions never propagate into the request
    path. Async callbacks are scheduled with :func:`asyncio.create_task`
    and never awaited.
    """

    on_bundler_request: Callable[[BundlerRequestEvent], Awaitable[None] | None] | None
    on_bundler_response: Callable[[BundlerResponseEvent], Awaitable[None] | None] | None
    on_bundler_error: Callable[[BundlerErrorEvent], Awaitable[None] | None] | None
    on_signer_request: Callable[[SignerRequestEvent], Awaitable[None] | None] | None
    on_signer_error: Callable[[SignerErrorEvent], Awaitable[None] | None] | None
    on_wait_orphaned: Callable[[WaitOrphanedEvent], Awaitable[None] | None] | None


def safe_fire(callback: _HookCallable | None, event: _AnyEvent) -> None:
    """Invoke a hook callback without ever bubbling its errors.

    Sync callables run inline (their return value is discarded). Async
    callables are scheduled via :func:`asyncio.create_task` — fire-and-
    forget, never awaited. Strong references to scheduled tasks are
    held in a module-level set until completion to defeat task GC.

    Hook exceptions are swallowed to keep the request path stable;
    the *first* failure on a process emits a single
    :class:`UserWarning` with the exception so integrators can find
    bugs in their telemetry pipeline without taking on a logging
    framework dependency.
    """
    if callback is None:
        return
    try:
        result = callback(event)
        if asyncio.iscoroutine(result):
            try:
                task = asyncio.get_running_loop().create_task(_swallow(result))
            except RuntimeError:
                # No running loop (e.g. sync test context); drop the coro.
                result.close()
            else:
                _in_flight_hook_tasks.add(task)
                task.add_done_callback(_in_flight_hook_tasks.discard)
    except Exception as cause:
        _warn_once(cause)


async def _swallow(coro: Awaitable[None]) -> None:
    try:
        await coro
    except Exception as cause:
        _warn_once(cause)


def _warn_once(cause: BaseException) -> None:
    global _hook_warning_emitted
    if _hook_warning_emitted:
        return
    _hook_warning_emitted = True
    warnings.warn(
        f"kashdao-protocol-sdk: a hook callback raised {type(cause).__name__}: {cause!r}. "
        "Subsequent hook failures on this process will be silently swallowed.",
        UserWarning,
        stacklevel=3,
    )


__all__ = [
    "BundlerErrorEvent",
    "BundlerRequestEvent",
    "BundlerResponseEvent",
    "EoaWaitOrphanedEvent",
    "KashProtocolHooks",
    "SignerErrorEvent",
    "SignerKind",
    "SignerRequestEvent",
    "SmartAccountWaitOrphanedEvent",
    "WaitOrphanedEvent",
    "safe_fire",
]
