"""Shared JSON-RPC 2.0 envelope schema.

Mirrors ``src/shared/json-rpc.ts``.

Used by both the bundler client (``smart_account/bundler/generic.py``)
and the EOA JSON-RPC signer adapter (``eoa/signers/json_rpc.py``) so
they validate response shape the same way and surface malformed
responses with consistent error context.

Why a shared schema instead of ad-hoc casts: ``body.get("result")``
followed by raw access silently accepts any shape — a malformed body
slips past and surfaces as a confusing key-error later. With Pydantic
we get one clear error message at the point the malformed response
arrives.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class JsonRpcError(BaseModel):
    """JSON-RPC 2.0 ``error`` field shape."""

    model_config = ConfigDict(extra="allow", frozen=True)

    code: int
    message: str
    data: Any | None = None


class JsonRpcResponse(BaseModel):
    """Shape of a JSON-RPC 2.0 response envelope.

    Accepts both success (``result`` present) and error (``error``
    present) responses; exactly one of the two MUST be present per
    spec — callers branch.

    ``result`` is ``Any`` because each method returns its own shape;
    callers parse the result against a method-specific schema after
    confirming it's not an error response.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    jsonrpc: str | None = None
    id: str | int | None = None
    result: Any | None = None
    error: JsonRpcError | None = None


__all__ = ["JsonRpcError", "JsonRpcResponse"]
