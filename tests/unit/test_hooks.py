"""``safe_fire`` is on the hot path of every bundler / signer call.

A misbehaving hook MUST NOT add latency or surface exceptions to the
request path. These tests pin the fire-and-forget contract.
"""

from __future__ import annotations

import asyncio

import pytest

from kashdao_protocol_sdk import BundlerRequestEvent
from kashdao_protocol_sdk.shared import hooks as hooks_mod
from kashdao_protocol_sdk.shared.hooks import safe_fire


@pytest.fixture(autouse=True)
def _reset_warning_flag() -> None:
    """Each test gets a fresh ``_hook_warning_emitted`` flag.

    ``safe_fire`` emits at most one ``UserWarning`` per process when a
    hook misbehaves; without this reset, the second swallowed-exception
    test wouldn't observe the warning at all.
    """
    hooks_mod._hook_warning_emitted = False


def _event() -> BundlerRequestEvent:
    return BundlerRequestEvent(method="m", url="https://b")


class TestSafeFireSync:
    def test_none_callback_is_noop(self) -> None:
        safe_fire(None, _event())  # must not raise

    def test_sync_callback_is_called(self) -> None:
        seen: list[BundlerRequestEvent] = []
        safe_fire(seen.append, _event())
        assert len(seen) == 1

    def test_sync_callback_exception_is_swallowed_with_one_warning(self) -> None:
        """The first hook failure on a process emits a single UserWarning.

        Subsequent failures are swallowed silently to avoid drowning
        observability output. The exception itself never propagates.
        """

        def boom(_evt: BundlerRequestEvent) -> None:
            raise RuntimeError("hook broken")

        with pytest.warns(UserWarning, match="hook callback raised RuntimeError"):
            safe_fire(boom, _event())  # must not raise

        # Second failure: swallowed silently (no warning) — verified by
        # the absence of a warning under filterwarnings=['error'].
        safe_fire(boom, _event())

    def test_sync_callback_with_no_loop_closes_coroutine(self) -> None:
        """Async callback fired outside an event loop must close the coro
        (no RuntimeWarning) and must not raise.

        ``filterwarnings = ["error"]`` in pyproject.toml will turn any
        ``RuntimeWarning("coroutine '...' was never awaited")`` into a
        test failure; absence of failure here is the assertion.
        """

        async def hook(_evt: BundlerRequestEvent) -> None:
            return None

        # No event loop in this sync test → safe_fire must close() the coro.
        safe_fire(hook, _event())


class TestSafeFireAsync:
    @pytest.mark.asyncio
    async def test_async_callback_scheduled_but_not_awaited(self) -> None:
        """The hook is fire-and-forget — control returns instantly."""
        gate = asyncio.Event()

        async def slow_hook(_evt: BundlerRequestEvent) -> None:
            await gate.wait()

        # safe_fire returns immediately even though the hook is slow.
        # If safe_fire awaited the hook, this would deadlock.
        safe_fire(slow_hook, _event())
        # Yield once so the scheduled task gets a chance to run.
        await asyncio.sleep(0)
        # Release the gate so the background task completes cleanly.
        gate.set()
        # Yield again to let the task wrap up.
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_async_callback_exception_is_swallowed(self) -> None:
        async def broken(_evt: BundlerRequestEvent) -> None:
            raise RuntimeError("hook broken")

        with pytest.warns(UserWarning, match="hook callback raised RuntimeError"):
            safe_fire(broken, _event())
            # Yield to let the scheduled task run; its exception must
            # not propagate to this coroutine but should produce a
            # one-shot warning.
            await asyncio.sleep(0)
