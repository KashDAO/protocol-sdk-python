"""SimpleAccount init-code helpers — ``factory`` + ``factory_data`` for
the deploy-on-first-use UserOp pattern.

Mirrors ``src/smart-account/account/init-code.ts``.

The very first UserOp targeting an SA that hasn't been deployed yet
MUST carry ``factory`` (the SimpleAccountFactory address) and
``factory_data`` (calldata for ``factory.createAccount(owner, salt)``).
Subsequent UserOps omit both fields — the SA is already deployed.

Without these fields, the EntryPoint reverts validation because the
sender address has no bytecode. The error consumers see is usually a
confusing AA20 / AA10 from the bundler. The ``prepare_*`` lifecycle
detects the undeployed case via ``get_code`` and populates these
fields automatically.
"""

from __future__ import annotations

from typing import Any, cast

from eth_abi import encode as abi_encode  # type: ignore[attr-defined]
from eth_utils import keccak  # type: ignore[attr-defined]
from hexbytes import HexBytes

from kashdao_protocol_sdk.shared.contracts.abis import SIMPLE_ACCOUNT_FACTORY_ABI
from kashdao_protocol_sdk.shared.types import Hex


def _create_account_function() -> dict[str, Any]:
    for entry in SIMPLE_ACCOUNT_FACTORY_ABI:
        if entry.get("type") == "function" and entry.get("name") == "createAccount":
            return entry
    raise RuntimeError("SimpleAccountFactory ABI is missing createAccount — vendored ABI is broken")


def _function_signature(entry: dict[str, Any]) -> str:
    inputs = entry.get("inputs", [])
    flat = ",".join(_canonical_solidity_type(i) for i in inputs)
    return f"{entry['name']}({flat})"


def _canonical_solidity_type(inp: dict[str, Any]) -> str:
    t = cast(str, inp["type"])
    if t.startswith("tuple"):
        components = inp.get("components", [])
        flat = ",".join(_canonical_solidity_type(c) for c in components)
        suffix = t[len("tuple") :]
        return f"({flat}){suffix}"
    return t


_CREATE_ACCOUNT_FN = _create_account_function()
_CREATE_ACCOUNT_SELECTOR: bytes = bytes(keccak(text=_function_signature(_CREATE_ACCOUNT_FN))[:4])
_CREATE_ACCOUNT_ARG_TYPES: list[str] = [
    _canonical_solidity_type(i) for i in _CREATE_ACCOUNT_FN["inputs"]
]


def encode_create_account(owner: Hex, salt: int = 0) -> Hex:
    """Encode ``SimpleAccountFactory.createAccount(owner, salt)`` calldata.

    Used as the ``factory_data`` field on a deploy-on-first-use UserOp.

    The selector and argument types are derived from
    :data:`SIMPLE_ACCOUNT_FACTORY_ABI` at import time — hardcoding
    them would break silently if the canonical factory ABI ever
    diverged from the vendored copy.
    """
    payload = abi_encode(_CREATE_ACCOUNT_ARG_TYPES, [owner, salt])
    return HexBytes(_CREATE_ACCOUNT_SELECTOR + payload).to_0x_hex()


__all__ = ["encode_create_account"]
