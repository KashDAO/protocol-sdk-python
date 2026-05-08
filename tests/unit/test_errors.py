"""Error hierarchy contract tests.

The ``KashProtocolError`` brand and ``code`` field are public surface;
consumer code branches on them. A change to either is a breaking change
to the package, so we pin both.
"""

from __future__ import annotations

import asyncio

import pytest

from kashdao_protocol_sdk import (
    KashAbortedError,
    KashBundlerError,
    KashChainError,
    KashConfigError,
    KashProtocolError,
    KashSignerError,
    KashSimulationRevertedError,
)
from kashdao_protocol_sdk.shared.errors import throw_if_aborted, to_kash_aborted


class TestBrand:
    def test_kash_errors_carry_brand(self) -> None:
        for cls in (
            KashConfigError,
            KashChainError,
            KashBundlerError,
            KashSignerError,
            KashSimulationRevertedError,
            KashAbortedError,
        ):
            err = cls("x", code="X")
            assert KashProtocolError.is_(err) is True

    def test_plain_exception_is_not_branded(self) -> None:
        assert KashProtocolError.is_(Exception("plain")) is False
        assert KashProtocolError.is_(ValueError("nope")) is False
        assert KashProtocolError.is_(None) is False
        assert KashProtocolError.is_("string") is False

    def test_foreign_branded_object_passes(self) -> None:
        """Cross-class identity: any object with the brand attr is treated as one of ours."""

        class FakeKash:
            __kashdao_protocol_error__ = True

        assert KashProtocolError.is_(FakeKash()) is True


class TestOperationalFlags:
    def test_config_error_is_not_operational(self) -> None:
        assert KashConfigError("x", code="X").is_operational is False

    def test_chain_error_is_operational_by_default(self) -> None:
        assert KashChainError("x", code="X").is_operational is True

    def test_chain_error_retryable_opt_in(self) -> None:
        assert KashChainError("x", code="X").is_retryable is False
        assert KashChainError("x", code="X", is_retryable=True).is_retryable is True

    def test_bundler_error_retryable_opt_in(self) -> None:
        assert KashBundlerError("x", code="X").is_retryable is False
        assert KashBundlerError("x", code="X", is_retryable=True).is_retryable is True


class TestCauseChaining:
    def test_cause_propagates_to_dunder_cause(self) -> None:
        cause = ValueError("underlying")
        err = KashConfigError("wrap", code="X", cause=cause)
        assert err.__cause__ is cause


class TestContextDefensiveCopy:
    def test_context_is_defensively_copied(self) -> None:
        ctx = {"a": 1}
        err = KashConfigError("x", code="X", context=ctx)
        ctx["a"] = 999
        assert err.context == {"a": 1}


class TestAbortHelpers:
    def test_throw_if_aborted_noop_for_none(self) -> None:
        throw_if_aborted(None)

    def test_throw_if_aborted_noop_for_unset_event(self) -> None:
        throw_if_aborted(asyncio.Event())

    def test_throw_if_aborted_raises_on_set_event(self) -> None:
        ev = asyncio.Event()
        ev.set()
        with pytest.raises(KashAbortedError) as exc_info:
            throw_if_aborted(ev, "stopped")
        assert exc_info.value.code == "OPERATION_ABORTED"

    def test_throw_if_aborted_handles_aborted_attribute(self) -> None:
        class Flag:
            aborted = True

        with pytest.raises(KashAbortedError):
            throw_if_aborted(Flag())

    def test_to_kash_aborted_wraps_cause(self) -> None:
        cancel = asyncio.CancelledError()
        err = to_kash_aborted(cancel, "interrupted")
        assert err.code == "OPERATION_ABORTED"
        assert err.__cause__ is cancel
