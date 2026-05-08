"""``JsonRpcSigner`` — remote SA signer via ``personal_sign`` / ``eth_signTypedData_v4``.

Mirrors ``src/smart-account/signers/json-rpc.ts``.

Reference :class:`SmartAccountSignerAdapter` for any compliant
JSON-RPC signer: web3signer, Fireblocks-via-RPC, Frame, MetaMask, or
a remote HSM that exposes the standard ``personal_sign`` /
``eth_signTypedData_v4`` methods.

The signer is identified by its EOA address; the SDK never receives
or persists the underlying private key. Authentication to the RPC
endpoint (mTLS, bearer token, IP allowlist, etc.) is the consumer's
responsibility — pass appropriate headers via the ``headers`` config.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import count
from types import MappingProxyType
from typing import Any

import httpx
from pydantic import ValidationError

from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashConfigError, KashSignerError
from kashdao_protocol_sdk.shared.json_rpc import JsonRpcResponse
from kashdao_protocol_sdk.shared.types import Hex
from kashdao_protocol_sdk.smart_account.types import UserOpTypedData

_EMPTY_HEADERS: Mapping[str, str] = MappingProxyType({})
_rpc_id_counter = count(1)
_SIGNATURE_RE = re.compile(r"^0x[0-9a-fA-F]{130}$")


@dataclass(frozen=True, slots=True)
class JsonRpcSignerConfig:
    """Inputs for :func:`json_rpc_signer`.

    The ``headers`` mapping is wrapped in :class:`MappingProxyType` on
    construction so consumers can't mutate it after the signer is built.
    """

    rpc: str
    """Signer JSON-RPC endpoint URL."""

    address: Hex
    """EOA address to sign with."""

    headers: Mapping[str, str] = field(default_factory=lambda: _EMPTY_HEADERS)
    """Extra HTTP headers (auth tokens, mTLS hints, etc.)."""

    timeout_seconds: float = 30.0
    """Per-request timeout. Default 30 s."""

    def __post_init__(self) -> None:
        if not isinstance(self.headers, MappingProxyType):
            object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(slots=True)
class JsonRpcSigner:
    """Remote-RPC SA signer.

    Owns an ``httpx.AsyncClient`` connection pool. Use as an async
    context manager (``async with json_rpc_signer(...) as signer:``)
    or call :meth:`aclose` explicitly when the strategy shuts down.
    """

    _config: JsonRpcSignerConfig
    _http: httpx.AsyncClient
    _owns_http: bool = False

    @property
    def owner_address(self) -> Hex:
        return self._config.address

    async def aclose(self) -> None:
        """Release the underlying ``httpx.AsyncClient`` if we own it."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> JsonRpcSigner:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def sign_user_op_hash(self, user_op_hash: Hex) -> Hex:
        """``personal_sign(message, account)`` — message is hex-encoded."""
        return await self._rpc_call("personal_sign", [user_op_hash, self._config.address])

    async def sign_typed_data_v4(self, typed_data: UserOpTypedData) -> Hex:
        """``eth_signTypedData_v4(account, typed_data_json)``."""
        encoded = json.dumps(
            {
                "domain": dict(typed_data.domain),
                "types": {k: list(v) for k, v in typed_data.types.items()},
                "primaryType": typed_data.primary_type,
                "message": _serialize_typed_data_message(
                    typed_data.message.model_dump(by_alias=True)
                ),
            },
            default=_int_to_str_default,
        )
        return await self._rpc_call("eth_signTypedData_v4", [self._config.address, encoded])

    async def _rpc_call(self, method: str, params: list[Any]) -> Hex:
        body = {
            "jsonrpc": "2.0",
            "id": _next_rpc_id(),
            "method": method,
            "params": params,
        }
        headers: dict[str, str] = {"content-type": "application/json"}
        headers.update(dict(self._config.headers))

        try:
            response = await self._http.post(
                self._config.rpc,
                json=body,
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        except Exception as cause:
            raise KashSignerError(
                f"signer RPC {method} request failed",
                code=ErrorCode.SIGNER_RPC_NETWORK_ERROR,
                context={"method": method, "rpc": self._config.rpc},
                cause=cause,
            ) from cause

        if response.status_code >= 400:
            raise KashSignerError(
                f"signer RPC {method} returned HTTP {response.status_code}",
                code=f"SIGNER_RPC_HTTP_{response.status_code}",
                context={
                    "method": method,
                    "rpc": self._config.rpc,
                    "status": response.status_code,
                },
            )

        try:
            payload = response.json()
        except Exception as cause:
            raise KashSignerError(
                f"signer RPC {method} returned non-JSON",
                code=ErrorCode.SIGNER_RPC_INVALID_ENVELOPE,
                context={"method": method, "rpc": self._config.rpc},
                cause=cause,
            ) from cause

        try:
            envelope = JsonRpcResponse.model_validate(payload)
        except ValidationError as cause:
            raise KashSignerError(
                f"signer RPC {method} returned a non-JSON-RPC-2.0 response",
                code=ErrorCode.SIGNER_RPC_INVALID_ENVELOPE,
                context={
                    "method": method,
                    "rpc": self._config.rpc,
                    "issues": cause.errors(),
                },
                cause=cause,
            ) from cause

        if envelope.error is not None:
            raise KashSignerError(
                f"signer RPC {method}: {envelope.error.message}",
                code=ErrorCode.SIGNER_RPC_ERROR,
                context={
                    "method": method,
                    "rpc": self._config.rpc,
                    "rpc_code": envelope.error.code,
                },
            )

        result = envelope.result
        if not isinstance(result, str) or not _SIGNATURE_RE.match(result):
            raise KashSignerError(
                f"signer RPC {method} returned an invalid signature result "
                "(must be 0x-prefixed 130-hex-char ECDSA signature)",
                code=ErrorCode.SIGNER_RPC_INVALID_RESULT,
                context={
                    "method": method,
                    "rpc": self._config.rpc,
                    "result": str(result),
                },
            )
        return result


def json_rpc_signer(
    config: JsonRpcSignerConfig | None = None,
    *,
    rpc: str | None = None,
    address: Hex | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
    http_client: httpx.AsyncClient | None = None,
) -> JsonRpcSigner:
    """Construct a :class:`JsonRpcSigner` from explicit args or config object.

    Pass ``http_client`` to share a connection pool with other HTTP
    callers; the signer will not close it on shutdown. Otherwise the
    signer creates and owns its own :class:`httpx.AsyncClient`.
    """
    if config is None:
        if rpc is None or address is None:
            missing = [
                name for name, value in (("rpc", rpc), ("address", address)) if value is None
            ]
            raise KashConfigError(
                "json_rpc_signer: rpc and address are required when config not provided",
                code=ErrorCode.MISSING_SIGNER_CONFIG,
                context={"missing": missing},
            )
        config = JsonRpcSignerConfig(
            rpc=rpc,
            address=address,
            headers=headers or {},
            timeout_seconds=timeout_seconds,
        )
    if http_client is not None:
        return JsonRpcSigner(_config=config, _http=http_client, _owns_http=False)
    return JsonRpcSigner(_config=config, _http=httpx.AsyncClient(), _owns_http=True)


def _next_rpc_id() -> int:
    return next(_rpc_id_counter)


def _serialize_typed_data_message(message: dict[str, Any]) -> dict[str, Any]:
    """Recursively encode bigints as decimal strings for JSON output.

    EIP-712 over RPC requires bigint-free JSON. Today the typed-data
    message is flat, but this stays correct as message shapes evolve.
    """
    return {k: _to_jsonable(v) for k, v in message.items()}


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool):
        # uint256s sometimes overflow JS Number; encode as strings.
        if value > 2**53 - 1 or value < -(2**53):
            return str(value)
        return value
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _int_to_str_default(value: Any) -> Any:
    """``json.dumps`` ``default`` hook for any leftover int-like that
    overflows uint256 boundaries (defensive — should not fire in
    practice given :func:`_serialize_typed_data_message`).
    """
    if isinstance(value, int):
        return str(value)
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


__all__ = ["JsonRpcSigner", "JsonRpcSignerConfig", "json_rpc_signer"]
