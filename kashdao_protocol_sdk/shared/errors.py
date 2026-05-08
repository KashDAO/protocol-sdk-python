"""Standalone error hierarchy for ``kashdao-protocol-sdk``.

Mirrors ``src/shared/errors.ts``. Decoupled from
any private Kash error packages so this library ships with zero
internal runtime dependencies. Consumers branch on ``isinstance``
checks and on the stable ``code`` string.

Cross-class safety: each instance carries an attribute
``__kashdao_protocol_error__ = True`` which works as a brand even when
the class identity differs (e.g. two installed versions in different
virtualenvs that talk to each other). Use :meth:`KashProtocolError.is_`
rather than raw ``isinstance`` when extracting from foreign code paths.
This is the Python equivalent of TS's ``Symbol.for`` brand.
"""

from __future__ import annotations

from typing import Any


class KashProtocolError(Exception):
    """Base class for every error this library raises.

    Always inherits from :class:`Exception`; never instantiated
    directly — see typed subclasses below.

    Attributes
    ----------
    code:
        Stable, machine-readable code (``"UNSUPPORTED_CHAIN"``,
        ``"BUNDLER_RPC_ERROR"``, ``"SIMULATION_REVERTED"`` …). Codes are
        part of the package's compatibility surface — additions are
        non-breaking, renames are.
    context:
        Free-form context dict for logs / telemetry. Not part of the
        equality contract.
    is_retryable:
        Whether the caller may safely retry. Subclasses override.
    is_operational:
        Whether the error is expected operational failure (network blip,
        slippage revert, insufficient balance) versus programmer error
        (bad config, malformed input). Subclasses override to ``False``
        for programmer errors.
    """

    __kashdao_protocol_error__: bool = True

    is_retryable: bool = False
    is_operational: bool = True

    def __init__(
        self,
        message: str,
        *,
        code: str,
        context: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.context = dict(context) if context is not None else None
        if cause is not None:
            self.__cause__ = cause

    @staticmethod
    def is_(value: object) -> bool:
        """Cross-class safe identity check.

        ``True`` for any object thrown by this library, including
        instances created by a different version that happens to share
        the brand attribute. Equivalent to TS's ``KashProtocolError.is``.
        """
        return getattr(value, "__kashdao_protocol_error__", False) is True


class KashConfigError(KashProtocolError):
    """Configuration is invalid.

    Raised synchronously from ``create_direct_client`` / address lookup
    on Pydantic validation failures, unsupported chain ids, or
    addresses that exist in the registry but have not been deployed
    (``factory == 0x000…``). By the time the consumer sees this, no
    network call has been attempted.
    """

    is_operational: bool = False


class KashChainError(KashProtocolError):
    """Chain read failed.

    RPC unreachable, invalid response, or contract reverted on a
    ``view`` call. Always wraps the underlying web3 / httpx error in
    ``__cause__``.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        context: dict[str, Any] | None = None,
        cause: BaseException | None = None,
        is_retryable: bool = False,
    ) -> None:
        super().__init__(message, code=code, context=context, cause=cause)
        self.is_retryable = is_retryable


class KashBundlerError(KashProtocolError):
    """Bundler RPC failed.

    Wraps ``eth_sendUserOperation``, ``eth_estimateUserOperationGas``,
    receipt-poll failures. The underlying JSON-RPC error code (when
    present) lives in ``context["rpc_code"]``.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        context: dict[str, Any] | None = None,
        cause: BaseException | None = None,
        is_retryable: bool = False,
    ) -> None:
        super().__init__(message, code=code, context=context, cause=cause)
        self.is_retryable = is_retryable


class KashSignerError(KashProtocolError):
    """Signer adapter failed.

    The signer rejected the request, lost connectivity, or returned a
    malformed signature. Never retryable — the library surfaces the
    failure rather than re-prompting.
    """


class KashSimulationRevertedError(KashProtocolError):
    """Pre-flight simulation indicated the trade will revert.

    Carries the decoded revert reason in ``context["revert_reason"]``
    when available.
    """


class KashAbortedError(KashProtocolError):
    """The caller aborted the operation via a cancellation signal.

    Raised when an async method honors a cancellation request (the
    Python equivalent of TS's ``AbortSignal``). Concretely, this is
    raised from inside the library's async methods when they observe
    ``asyncio.CancelledError`` and want to surface a structured Kash
    error to the consumer.

    Operational (not a programmer error), but never retryable — the
    consumer asked to stop, so retrying contradicts intent.
    """

    is_retryable: bool = False
    is_operational: bool = True


def throw_if_aborted(signal: object | None, message: str = "operation aborted") -> None:
    """Raise :class:`KashAbortedError` if the cancellation signal is set.

    Mirrors TS ``throwIfAborted``. ``signal`` may be any object with an
    ``aborted`` attribute (``asyncio.Event``, ``threading.Event``,
    custom flag class). ``None`` is a no-op so callers can pass through
    optional cancellation tokens uniformly.
    """
    if signal is None:
        return
    aborted = getattr(signal, "aborted", None)
    if aborted is None:
        # Fallback — asyncio.Event uses ``is_set()``.
        is_set = getattr(signal, "is_set", None)
        aborted = is_set() if callable(is_set) else False
    if aborted:
        raise KashAbortedError(
            message,
            code="OPERATION_ABORTED",
        )


def to_kash_aborted(cause: BaseException, message: str = "operation aborted") -> KashAbortedError:
    """Wrap a foreign cancellation into :class:`KashAbortedError`.

    Useful when bridging :class:`asyncio.CancelledError` or
    DOMException-shaped abort signals into the Kash error hierarchy.
    """
    return KashAbortedError(
        message,
        code="OPERATION_ABORTED",
        cause=cause,
    )


__all__ = [
    "KashAbortedError",
    "KashBundlerError",
    "KashChainError",
    "KashConfigError",
    "KashProtocolError",
    "KashSignerError",
    "KashSimulationRevertedError",
    "throw_if_aborted",
    "to_kash_aborted",
]
