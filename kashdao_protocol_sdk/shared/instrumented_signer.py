"""Hook-instrumented wrappers for the SA + EOA signer adapters.

Mirrors ``src/shared/instrumented-signer.ts``.

Every signing call fires ``on_signer_request`` before the call and
``on_signer_error`` if the call raises. Success doesn't fire its own
event — callers can derive timing from the matching response/error
window if they wire it externally. This keeps the hook surface
minimal and matches the "log on event" pattern most consumers want.

Hook errors are swallowed via :func:`safe_fire` — a misbehaving
logger never breaks a trade.

The wrappers return the exact same Protocol shape as the input and
short-circuit when no hooks are configured, so the per-call overhead
is zero in the common case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kashdao_protocol_sdk.eoa.types import EoaSignerAdapter, UnsignedTransaction
from kashdao_protocol_sdk.shared.hooks import (
    KashProtocolHooks,
    SignerErrorEvent,
    SignerRequestEvent,
    safe_fire,
)
from kashdao_protocol_sdk.shared.types import Hex


def instrument_smart_account_signer(
    signer: Any,
    hooks: KashProtocolHooks | None,
) -> Any:
    """Wrap an SA signer with hook instrumentation.

    Returns the same Protocol shape; if no hooks are configured (or
    the config has neither ``on_signer_request`` nor ``on_signer_error``
    callbacks), returns the input unchanged so there's no per-call
    overhead.

    The wrapper fires ``kind="userop-hash"`` on
    :py:meth:`SmartAccountSignerAdapter.sign_user_op_hash` and
    ``kind="typed-data"`` on the optional
    :py:meth:`SmartAccountSignerAdapter.sign_typed_data_v4`.
    """
    if not _has_signer_hook(hooks):
        return signer
    assert hooks is not None  # narrowed by _has_signer_hook
    return _InstrumentedSmartAccountSigner(_inner=signer, _hooks=hooks)


def instrument_eoa_signer(
    signer: EoaSignerAdapter,
    hooks: KashProtocolHooks | None,
) -> EoaSignerAdapter:
    """Wrap an EOA signer with hook instrumentation.

    Same short-circuit behavior as
    :func:`instrument_smart_account_signer`. Fires ``kind="transaction"``
    on every :py:meth:`EoaSignerAdapter.sign_transaction`.
    """
    if not _has_signer_hook(hooks):
        return signer
    assert hooks is not None  # narrowed by _has_signer_hook
    wrapper: EoaSignerAdapter = _InstrumentedEoaSigner(_inner=signer, _hooks=hooks)
    return wrapper


def _has_signer_hook(hooks: KashProtocolHooks | None) -> bool:
    if hooks is None:
        return False
    return (
        getattr(hooks, "on_signer_request", None) is not None
        or getattr(hooks, "on_signer_error", None) is not None
    )


@dataclass(slots=True)
class _InstrumentedEoaSigner:
    """Hook-instrumented EOA signer wrapper."""

    _inner: EoaSignerAdapter
    _hooks: KashProtocolHooks

    @property
    def owner_address(self) -> Hex:
        return self._inner.owner_address

    async def sign_transaction(self, transaction: UnsignedTransaction) -> Hex:
        safe_fire(
            getattr(self._hooks, "on_signer_request", None),
            SignerRequestEvent(owner_address=self._inner.owner_address, kind="transaction"),
        )
        try:
            return await self._inner.sign_transaction(transaction)
        except Exception as error:
            safe_fire(
                getattr(self._hooks, "on_signer_error", None),
                SignerErrorEvent(
                    owner_address=self._inner.owner_address,
                    kind="transaction",
                    error=error,
                ),
            )
            raise


@dataclass(slots=True)
class _InstrumentedSmartAccountSigner:
    """Hook-instrumented SA signer wrapper.

    Forwards ``sign_user_op_hash`` (required) and ``sign_typed_data_v4``
    (optional — present only when the inner signer exposes it).
    """

    _inner: Any
    _hooks: KashProtocolHooks

    @property
    def owner_address(self) -> Hex:
        return self._inner.owner_address  # type: ignore[no-any-return]

    async def sign_user_op_hash(self, user_op_hash: Hex) -> Hex:
        safe_fire(
            getattr(self._hooks, "on_signer_request", None),
            SignerRequestEvent(owner_address=self._inner.owner_address, kind="userop-hash"),
        )
        try:
            return await self._inner.sign_user_op_hash(user_op_hash)  # type: ignore[no-any-return]
        except Exception as error:
            safe_fire(
                getattr(self._hooks, "on_signer_error", None),
                SignerErrorEvent(
                    owner_address=self._inner.owner_address,
                    kind="userop-hash",
                    error=error,
                ),
            )
            raise

    async def sign_typed_data_v4(self, typed_data: Any) -> Hex:
        if not hasattr(self._inner, "sign_typed_data_v4"):
            raise AttributeError("underlying signer does not implement sign_typed_data_v4")
        safe_fire(
            getattr(self._hooks, "on_signer_request", None),
            SignerRequestEvent(owner_address=self._inner.owner_address, kind="typed-data"),
        )
        try:
            return await self._inner.sign_typed_data_v4(typed_data)  # type: ignore[no-any-return]
        except Exception as error:
            safe_fire(
                getattr(self._hooks, "on_signer_error", None),
                SignerErrorEvent(
                    owner_address=self._inner.owner_address,
                    kind="typed-data",
                    error=error,
                ),
            )
            raise


__all__ = [
    "instrument_eoa_signer",
    "instrument_smart_account_signer",
]
