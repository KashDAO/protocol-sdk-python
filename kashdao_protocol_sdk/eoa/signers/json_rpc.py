"""``JsonRpcEoaSigner`` — remote signer via ``eth_signTransaction``.

Mirrors ``src/eoa/signers/json-rpc.ts``.

Forwards ``UnsignedTransaction`` over JSON-RPC to a remote signer
(web3signer, Fireblocks-via-RPC, AWS-KMS-via-RPC, MetaMask, Frame, etc.)
and returns the signed serialized transaction. The library never sees
the underlying private key.

Unlike Smart Account mode's ``personal_sign`` flow, the EOA path uses
``eth_signTransaction`` because we need the full signed RLP-encoded
EIP-1559 envelope, not just a 65-byte signature.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import httpx

from kashdao_protocol_sdk.eoa.types import UnsignedTransaction
from kashdao_protocol_sdk.shared.errors import KashConfigError, KashSignerError
from kashdao_protocol_sdk.shared.types import Hex

_EMPTY_HEADERS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class JsonRpcEoaSignerConfig:
    """Inputs for :func:`json_rpc_eoa_signer`.

    The ``headers`` mapping is wrapped in a :class:`MappingProxyType`
    on construction so consumers can't mutate it after the signer is
    built (the dataclass itself is frozen, but the ``dict`` it holds
    isn't).
    """

    rpc: str
    """Remote JSON-RPC endpoint exposing ``eth_signTransaction``."""

    address: Hex
    """The EOA address that will sign. Forwarded as the ``from`` field."""

    headers: Mapping[str, str] = field(default_factory=lambda: _EMPTY_HEADERS)
    """Extra HTTP headers (e.g. authentication). Read-only after construction."""

    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.headers, MappingProxyType):
            object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(slots=True)
class JsonRpcEoaSigner:
    """Remote-RPC EOA signer.

    Owns an ``httpx.AsyncClient`` connection pool. Use as an async
    context manager (``async with json_rpc_eoa_signer(...) as signer:``)
    or call :meth:`aclose` explicitly when the strategy shuts down so
    the pool is released. Long-running consumers (Hummingbot strategies)
    that recreate a signer per-tick will leak connections without this.
    """

    _config: JsonRpcEoaSignerConfig
    _http: httpx.AsyncClient
    _owns_http: bool = False

    @property
    def owner_address(self) -> Hex:
        return self._config.address

    async def aclose(self) -> None:
        """Release the underlying ``httpx.AsyncClient`` if we own it.

        Idempotent. No-op when the consumer passed in their own
        externally-managed client (``http_client=...``).
        """
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> JsonRpcEoaSigner:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def sign_transaction(self, transaction: UnsignedTransaction) -> Hex:
        params = [
            {
                "from": self._config.address,
                "to": transaction.to,
                "data": transaction.data,
                "value": _hex(transaction.value),
                "nonce": _hex(transaction.nonce),
                "gas": _hex(transaction.gas),
                "maxFeePerGas": _hex(transaction.max_fee_per_gas),
                "maxPriorityFeePerGas": _hex(transaction.max_priority_fee_per_gas),
                "chainId": _hex(transaction.chain_id),
                "type": "0x2",
            }
        ]
        try:
            response = await self._http.post(
                self._config.rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_signTransaction",
                    "params": params,
                },
                headers=dict(self._config.headers) or None,
                timeout=self._config.timeout_seconds,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except Exception as cause:
            raise KashSignerError(
                f"json-rpc eth_signTransaction failed for {self._config.address}",
                code="SIGNER_RPC_FAILED",
                context={"rpc": self._config.rpc, "owner": self._config.address},
                cause=cause,
            ) from cause

        if "error" in payload:
            raise KashSignerError(
                f"json-rpc signer error: {payload['error'].get('message', '<unknown>')}",
                code="SIGNER_RPC_ERROR",
                context={
                    "rpc": self._config.rpc,
                    "owner": self._config.address,
                    "rpc_error": payload["error"],
                },
            )
        result = payload.get("result")
        # Some signers return `{ "raw": "0x..." }`; normalize.
        if isinstance(result, dict):
            raw = result.get("raw") or result.get("rawTransaction")
        else:
            raw = result
        if not isinstance(raw, str) or not raw.startswith("0x"):
            raise KashSignerError(
                "json-rpc signer returned non-hex result",
                code="SIGNER_BAD_RESULT",
                context={
                    "rpc": self._config.rpc,
                    "owner": self._config.address,
                    "result_type": type(result).__name__,
                },
            )
        return raw


def json_rpc_eoa_signer(
    config: JsonRpcEoaSignerConfig | None = None,
    *,
    rpc: str | None = None,
    address: Hex | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
    http_client: httpx.AsyncClient | None = None,
) -> JsonRpcEoaSigner:
    """Construct a :class:`JsonRpcEoaSigner` from explicit args or config object.

    Pass ``http_client`` to share a connection pool with other HTTP
    callers in the consumer process; the signer will not close it on
    shutdown. Otherwise the signer creates and owns its own
    :class:`httpx.AsyncClient` and releases it on
    :meth:`JsonRpcEoaSigner.aclose` (or via ``async with``).
    """
    if config is None:
        if rpc is None or address is None:
            missing = [
                name for name, value in (("rpc", rpc), ("address", address)) if value is None
            ]
            raise KashConfigError(
                "json_rpc_eoa_signer: rpc and address are required when config not provided",
                code="MISSING_SIGNER_CONFIG",
                context={"missing": missing},
            )
        config = JsonRpcEoaSignerConfig(
            rpc=rpc,
            address=address,
            headers=headers or {},
            timeout_seconds=timeout_seconds,
        )
    if http_client is not None:
        return JsonRpcEoaSigner(_config=config, _http=http_client, _owns_http=False)
    return JsonRpcEoaSigner(_config=config, _http=httpx.AsyncClient(), _owns_http=True)


def _hex(value: int) -> str:
    return hex(int(value))


__all__ = [
    "JsonRpcEoaSigner",
    "JsonRpcEoaSignerConfig",
    "json_rpc_eoa_signer",
]
