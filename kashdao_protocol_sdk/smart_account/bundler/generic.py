"""Generic ERC-4337 bundler RPC client.

Mirrors ``src/smart-account/bundler/generic.ts``.

JSON-RPC 2.0 over ``httpx``. Implements the standard bundler RPC
surface:

* ``eth_sendUserOperation`` — submit a signed UserOp; returns the
  bundler-computed UserOp hash.
* ``eth_estimateUserOperationGas`` — returns ``GasEstimate`` for an
  unsigned UserOp.
* ``eth_getUserOperationReceipt`` — poll for inclusion; returns
  ``UserOpReceipt | None``.
* ``eth_getUserOperationByHash`` — fetch a (possibly pending) UserOp.
* ``eth_chainId`` — sanity check / readiness probe.

Provider presets (``flashbots.py``, ``pimlico.py``, ``alchemy.py``) are
thin wrappers over this client that supply provider-specific defaults
(URL hints, header shapes). No Kash credentials live anywhere in this
package — every key/URL is consumer-provided.

**No retries on submission.** Surfacing failure to the consumer is
preferable to retrying a transaction (silent retries can cause
double-spends).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from itertools import count
from typing import Any, cast

import httpx
from pydantic import ValidationError

from kashdao_protocol_sdk.shared.abortable import abortable_sleep
from kashdao_protocol_sdk.shared.contracts.addresses import ENTRY_POINT_07_ADDRESS
from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import (
    KashAbortedError,
    KashBundlerError,
    throw_if_aborted,
    to_kash_aborted,
)
from kashdao_protocol_sdk.shared.hooks import (
    BundlerErrorEvent,
    BundlerRequestEvent,
    BundlerResponseEvent,
    KashProtocolHooks,
    safe_fire,
)
from kashdao_protocol_sdk.shared.json_rpc import JsonRpcResponse
from kashdao_protocol_sdk.shared.types import Hex
from kashdao_protocol_sdk.smart_account.types import (
    BundlerHealth,
    BundlerHealthError,
    BundlerHealthOk,
    GasEstimate,
    SignedUserOp,
    UnsignedUserOp,
    UserOpReceipt,
)

_EMPTY_HEADERS: dict[str, str] = {}
_rpc_id_counter = count(1)


@dataclass(frozen=True, slots=True)
class BundlerCallOptions:
    """Per-call options accepted by every :class:`BundlerClient` async method.

    ``signal`` integrates with the consumer's cancellation surface — a
    set :class:`asyncio.Event` (or anything with an ``aborted`` /
    ``is_set()`` truthy state) raises :class:`KashAbortedError`
    pre-call. Mid-fetch cancellation honors :class:`asyncio.Event` via
    racing the request against ``signal.wait()``.
    """

    signal: object | None = None


@dataclass(frozen=True, slots=True)
class BundlerClientConfig:
    """Inputs for :func:`create_generic_bundler_client`.

    Pass ``http_client`` to share an ``httpx.AsyncClient`` connection
    pool with other HTTP callers in the consumer process; the bundler
    client will not close it on shutdown. Otherwise the bundler creates
    and owns its own client and releases it on
    :meth:`BundlerClient.aclose`.
    """

    url: str
    """Bundler RPC URL."""

    api_key: str | None = None
    """Optional API key — set as ``Authorization: Bearer <key>`` if provided."""

    headers: dict[str, str] = field(default_factory=lambda: dict(_EMPTY_HEADERS))
    """Extra headers (provider-specific, e.g. Pimlico's gas-policy hints)."""

    timeout_seconds: float = 30.0
    """Per-request timeout. Default 30 s. Mirrors the TS ``timeoutMs`` of 30 000 ms."""

    entry_point_address: Hex = ENTRY_POINT_07_ADDRESS
    """EntryPoint address. Override only on chains where the canonical
    address isn't deployed (Anvil — see ``CustomChain``)."""

    hooks: KashProtocolHooks | None = None
    """Lifecycle hooks for observability — see :mod:`shared.hooks`."""

    http_client: httpx.AsyncClient | None = None
    """Externally-managed ``httpx.AsyncClient`` to use for bundler
    requests. When supplied, the bundler does NOT close it on
    :meth:`BundlerClient.aclose`."""


@dataclass(slots=True)
class BundlerClient:
    """Bundler RPC client. Construct via :func:`create_generic_bundler_client`."""

    _config: BundlerClientConfig
    _http: httpx.AsyncClient
    _owns_http: bool

    @property
    def entry_point_address(self) -> Hex:
        return self._config.entry_point_address

    async def aclose(self) -> None:
        """Release the underlying ``httpx.AsyncClient`` if we own it.

        Idempotent. No-op when the consumer passed in their own
        externally-managed client.
        """
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> BundlerClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ---- public RPC surface ---------------------------------------------

    async def send(
        self,
        user_op: SignedUserOp,
        options: BundlerCallOptions | None = None,
    ) -> Hex:
        """Submit a signed UserOp; return the bundler-computed UserOp hash."""
        result = await self._call(
            "eth_sendUserOperation",
            [_serialize_user_op_signed(user_op), self.entry_point_address],
            options,
        )
        return cast(Hex, result)

    async def estimate_gas(
        self,
        user_op: UnsignedUserOp,
        options: BundlerCallOptions | None = None,
    ) -> GasEstimate:
        """Bundler-side gas estimate for an unsigned UserOp."""
        raw = await self._call(
            "eth_estimateUserOperationGas",
            [_serialize_user_op_unsigned(user_op), self.entry_point_address],
            options,
        )
        if not isinstance(raw, dict):
            raise KashBundlerError(
                "eth_estimateUserOperationGas returned non-object",
                code=ErrorCode.BUNDLER_INVALID_ENVELOPE,
                context={"method": "eth_estimateUserOperationGas", "url": self._config.url},
            )
        return GasEstimate(
            pre_verification_gas=_hex_to_int(raw["preVerificationGas"]),
            verification_gas_limit=_hex_to_int(raw["verificationGasLimit"]),
            call_gas_limit=_hex_to_int(raw["callGasLimit"]),
            paymaster_verification_gas_limit=(
                _hex_to_int(raw["paymasterVerificationGasLimit"])
                if raw.get("paymasterVerificationGasLimit")
                else None
            ),
            paymaster_post_op_gas_limit=(
                _hex_to_int(raw["paymasterPostOpGasLimit"])
                if raw.get("paymasterPostOpGasLimit")
                else None
            ),
        )

    async def get_receipt(
        self,
        user_op_hash: Hex,
        options: BundlerCallOptions | None = None,
    ) -> UserOpReceipt | None:
        """Poll for a UserOp receipt; returns ``None`` until included."""
        raw = await self._call("eth_getUserOperationReceipt", [user_op_hash], options)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise KashBundlerError(
                "eth_getUserOperationReceipt returned non-object",
                code=ErrorCode.BUNDLER_INVALID_ENVELOPE,
                context={
                    "method": "eth_getUserOperationReceipt",
                    "url": self._config.url,
                },
            )
        try:
            return UserOpReceipt.model_validate(raw)
        except ValidationError as cause:
            raise KashBundlerError(
                "eth_getUserOperationReceipt returned malformed receipt",
                code=ErrorCode.BUNDLER_INVALID_ENVELOPE,
                context={
                    "method": "eth_getUserOperationReceipt",
                    "url": self._config.url,
                    "errors": cause.errors(),
                },
                cause=cause,
            ) from cause

    async def wait_for_receipt(
        self,
        user_op_hash: Hex,
        timeout_seconds: float = 60.0,
        interval_seconds: float = 2.0,
        signal: object | None = None,
    ) -> UserOpReceipt:
        """Wait for inclusion with bounded backoff.

        Honors ``signal`` between polls AND on the underlying
        ``get_receipt`` fetch — long-running ``wait_for_receipt`` calls
        cancel cleanly on consumer abort. Raises
        :class:`KashBundlerError` (``BUNDLER_RECEIPT_TIMEOUT``,
        retryable) when ``timeout_seconds`` elapses without inclusion.
        """
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            throw_if_aborted(signal, f"aborted while waiting for UserOp {user_op_hash}")
            opts = BundlerCallOptions(signal=signal) if signal is not None else None
            receipt = await self.get_receipt(user_op_hash, opts)
            if receipt is not None:
                return receipt
            await abortable_sleep(int(interval_seconds * 1000), signal)
        throw_if_aborted(signal, f"aborted while waiting for UserOp {user_op_hash}")
        raise KashBundlerError(
            f"timed out waiting for UserOp {user_op_hash}",
            code=ErrorCode.BUNDLER_RECEIPT_TIMEOUT,
            context={
                "user_op_hash": user_op_hash,
                "timeout_seconds": timeout_seconds,
            },
            is_retryable=True,
        )

    async def chain_id(self, options: BundlerCallOptions | None = None) -> int:
        """Fetch the bundler's reported chain id."""
        raw = await self._call("eth_chainId", [], options)
        return _hex_to_int(raw)

    async def health(self, options: BundlerCallOptions | None = None) -> BundlerHealth:
        """Liveness probe. Never raises.

        Returns :class:`BundlerHealthOk` with the chain id + measured
        round-trip on success, or :class:`BundlerHealthError` carrying
        the underlying exception on failure. Designed for status pages
        and pre-flight checks alongside
        :func:`check_chain_rpc_health`.
        """
        started_at = time.perf_counter()
        try:
            chain_id_value = await self.chain_id(options)
        except Exception as err:
            return BundlerHealthError(
                error=err,
                latency_ms=int((time.perf_counter() - started_at) * 1000),
            )
        return BundlerHealthOk(
            chain_id=chain_id_value,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
        )

    # ---- internals -------------------------------------------------------

    async def _call(
        self,
        method: str,
        params: list[Any],
        options: BundlerCallOptions | None,
    ) -> Any:
        signal = options.signal if options is not None else None
        throw_if_aborted(signal, f"aborted before bundler {method}")

        url = self._config.url
        hooks = self._config.hooks
        safe_fire(
            getattr(hooks, "on_bundler_request", None),
            BundlerRequestEvent(method=method, url=url),
        )
        started_at = time.perf_counter()
        body = {
            "jsonrpc": "2.0",
            "id": _next_rpc_id(),
            "method": method,
            "params": params,
        }
        headers = _build_headers(self._config)

        try:
            response = await _post_with_abort(
                self._http,
                url=url,
                json_body=body,
                headers=headers,
                timeout_seconds=self._config.timeout_seconds,
                signal=signal,
            )
        except KashAbortedError as cause:
            self._fire_error(method, url, started_at, cause)
            raise
        except Exception as cause:
            error: KashBundlerError
            if signal is not None and _signal_is_set(signal):
                aborted = to_kash_aborted(cause, f"bundler {method} aborted by signal")
                self._fire_error(method, url, started_at, aborted)
                raise aborted from cause
            error = KashBundlerError(
                f"bundler {method} request failed",
                code=ErrorCode.BUNDLER_NETWORK_ERROR,
                context={"method": method, "url": url},
                is_retryable=True,
                cause=cause,
            )
            self._fire_error(method, url, started_at, error)
            raise error from cause

        if response.status_code >= 400:
            error = KashBundlerError(
                f"bundler {method} returned HTTP {response.status_code}",
                code=f"BUNDLER_HTTP_{response.status_code}",
                context={
                    "method": method,
                    "url": url,
                    "status": response.status_code,
                },
                is_retryable=response.status_code >= 500,
            )
            self._fire_error(method, url, started_at, error)
            raise error

        try:
            payload = response.json()
        except Exception as cause:
            error = KashBundlerError(
                f"bundler {method} returned non-JSON response",
                code=ErrorCode.BUNDLER_INVALID_JSON,
                context={"method": method, "url": url},
                cause=cause,
            )
            self._fire_error(method, url, started_at, error)
            raise error from cause

        try:
            envelope = JsonRpcResponse.model_validate(payload)
        except ValidationError as cause:
            error = KashBundlerError(
                f"bundler {method} returned a non-JSON-RPC-2.0 response",
                code=ErrorCode.BUNDLER_INVALID_ENVELOPE,
                context={
                    "method": method,
                    "url": url,
                    "issues": cause.errors(),
                },
                cause=cause,
            )
            self._fire_error(method, url, started_at, error)
            raise error from cause

        if envelope.error is not None:
            error = KashBundlerError(
                f"bundler {method}: {envelope.error.message}",
                code=ErrorCode.BUNDLER_RPC_ERROR,
                context={
                    "method": method,
                    "url": url,
                    "rpc_code": envelope.error.code,
                    "rpc_data": envelope.error.data,
                },
            )
            self._fire_error(method, url, started_at, error)
            raise error
        if envelope.result is None and "result" not in payload:
            # Distinguish "result key absent" (BUNDLER_EMPTY_RESULT) from
            # "result key present and explicitly null" (legitimate for
            # eth_getUserOperationReceipt before inclusion).
            error = KashBundlerError(
                f"bundler {method} returned empty result",
                code=ErrorCode.BUNDLER_EMPTY_RESULT,
                context={"method": method, "url": url},
            )
            self._fire_error(method, url, started_at, error)
            raise error

        safe_fire(
            getattr(hooks, "on_bundler_response", None),
            BundlerResponseEvent(
                method=method,
                url=url,
                duration_ms=(time.perf_counter() - started_at) * 1000,
                status=response.status_code,
            ),
        )
        return envelope.result

    def _fire_error(
        self,
        method: str,
        url: str,
        started_at: float,
        error: BaseException,
    ) -> None:
        hooks = self._config.hooks
        safe_fire(
            getattr(hooks, "on_bundler_error", None),
            BundlerErrorEvent(
                method=method,
                url=url,
                duration_ms=(time.perf_counter() - started_at) * 1000,
                error=error,
            ),
        )


def create_generic_bundler_client(config: BundlerClientConfig) -> BundlerClient:
    """Build a generic :class:`BundlerClient` over a JSON-RPC URL.

    When ``config.http_client`` is not supplied, the returned client
    creates and owns its own ``httpx.AsyncClient``; call
    :meth:`BundlerClient.aclose` (or use ``async with``) to release it.
    """
    if config.http_client is not None:
        return BundlerClient(_config=config, _http=config.http_client, _owns_http=False)
    return BundlerClient(_config=config, _http=httpx.AsyncClient(), _owns_http=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _next_rpc_id() -> int:
    return next(_rpc_id_counter)


def _build_headers(config: BundlerClientConfig) -> dict[str, str]:
    headers: dict[str, str] = {
        "content-type": "application/json",
        "accept": "application/json",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    if config.headers:
        # Cast through dict() so any read-only mapping (e.g. a
        # ``types.MappingProxyType`` from a frozen config) is accepted
        # without aliasing the caller's mapping into our header set.
        headers.update(dict(config.headers))
    return headers


def _hex_to_int(value: Any) -> int:
    """Decode a JSON-RPC ``0x...`` hex string to an int."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value.startswith("0x"):
        raise KashBundlerError(
            f"expected hex-encoded integer, got {value!r}",
            code=ErrorCode.BUNDLER_INVALID_ENVELOPE,
            context={"value": str(value)},
        )
    return int(value, 16)


def _to_hex(value: int) -> str:
    return hex(value)


def _signal_is_set(signal: object) -> bool:
    aborted = getattr(signal, "aborted", None)
    if aborted is True:
        return True
    is_set = getattr(signal, "is_set", None)
    if callable(is_set):
        return bool(is_set())
    return False


async def _post_with_abort(
    http: httpx.AsyncClient,
    *,
    url: str,
    json_body: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
    signal: object | None,
) -> httpx.Response:
    """``http.post`` with consumer-abort support.

    For :class:`asyncio.Event`-shaped signals we race the request
    against ``signal.wait()`` so a consumer abort cancels the in-flight
    fetch immediately. For other shapes the request runs to completion
    (or its own timeout); abort is checked again after the response
    returns by the caller.
    """
    request_coro = http.post(
        url,
        json=json_body,
        headers=headers,
        timeout=timeout_seconds,
    )

    wait_method = getattr(signal, "wait", None) if signal is not None else None
    if not callable(wait_method):
        return await request_coro

    fetch_task = asyncio.ensure_future(request_coro)
    abort_task = asyncio.ensure_future(wait_method())
    try:
        done, _pending = await asyncio.wait(
            {fetch_task, abort_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        # Always cancel the abort watcher; cancel the fetch only if it
        # didn't complete.
        if not abort_task.done():
            abort_task.cancel()
    if fetch_task in done:
        return fetch_task.result()
    # Abort fired first — cancel the fetch and surface KashAbortedError.
    if not fetch_task.done():
        fetch_task.cancel()
        try:
            await fetch_task
        except (asyncio.CancelledError, Exception):
            # Swallow either the cancellation acknowledgement or a
            # latent fetch error; the abort takes precedence.
            pass
    raise KashAbortedError(
        "bundler request aborted by signal",
        code=ErrorCode.OPERATION_ABORTED,
    )


# ---------------------------------------------------------------------------
# UserOp wire serialization
# ---------------------------------------------------------------------------


def _serialize_user_op_unsigned(user_op: UnsignedUserOp) -> dict[str, Any]:
    """Wire shape for ``eth_estimateUserOperationGas``.

    EntryPoint v0.7 unpacked representation. Bigints become 0x-prefixed
    hex strings; optional fields (factory, paymaster) are emitted only
    when populated.
    """
    out: dict[str, Any] = {
        "sender": user_op.sender,
        "nonce": _to_hex(user_op.nonce),
        "callData": user_op.call_data,
        "callGasLimit": _to_hex(user_op.call_gas_limit),
        "verificationGasLimit": _to_hex(user_op.verification_gas_limit),
        "preVerificationGas": _to_hex(user_op.pre_verification_gas),
        "maxFeePerGas": _to_hex(user_op.max_fee_per_gas),
        "maxPriorityFeePerGas": _to_hex(user_op.max_priority_fee_per_gas),
        "signature": user_op.signature,
    }
    if user_op.factory and user_op.factory_data:
        out["factory"] = user_op.factory
        out["factoryData"] = user_op.factory_data
    if user_op.paymaster:
        out["paymaster"] = user_op.paymaster
        out["paymasterData"] = user_op.paymaster_data or "0x"
        if user_op.paymaster_verification_gas_limit is not None:
            out["paymasterVerificationGasLimit"] = _to_hex(user_op.paymaster_verification_gas_limit)
        if user_op.paymaster_post_op_gas_limit is not None:
            out["paymasterPostOpGasLimit"] = _to_hex(user_op.paymaster_post_op_gas_limit)
    return out


def _serialize_user_op_signed(signed: SignedUserOp) -> dict[str, Any]:
    """Wire shape for ``eth_sendUserOperation``.

    Re-uses :func:`_serialize_user_op_unsigned` but overrides the
    ``signature`` field with the populated 65-byte ECDSA signature.
    """
    out = _serialize_user_op_unsigned(signed.user_op)
    out["signature"] = signed.signature
    return out
