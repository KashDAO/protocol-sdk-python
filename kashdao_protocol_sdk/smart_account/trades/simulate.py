"""Pre-flight UserOp simulation.

Mirrors ``src/smart-account/trades/simulate.ts``.

Issues an ``eth_call`` against the smart account's ``execute`` entry
point with the UserOp's ``call_data``, mimicking what the bundler /
EntryPoint would do. A successful simulation returns
:class:`SimulationSuccess`; reverts surface as
:class:`SimulationFailure` carrying the decoded reason.

Cheaper than estimating UserOp gas — no bundler RPC roundtrip — and
useful for surfacing predictable reverts (slippage, deadline,
insufficient balance) before the consumer pays for signing
infrastructure.
"""

from __future__ import annotations

from typing import Any, cast

from eth_abi import decode as abi_decode  # type: ignore[attr-defined]
from eth_typing import HexStr
from eth_utils import keccak, to_bytes  # type: ignore[attr-defined]
from hexbytes import HexBytes
from web3 import AsyncWeb3
from web3.exceptions import ContractCustomError, ContractLogicError
from web3.types import TxParams

from kashdao_protocol_sdk.shared.contracts.abis import SIMPLE_ACCOUNT_ABI
from kashdao_protocol_sdk.shared.contracts.decoders import (
    DecodedRevert,
    decode_market_revert,
)
from kashdao_protocol_sdk.shared.types import (
    SimulationDecodedError,
    SimulationFailure,
    SimulationResult,
    SimulationSuccess,
)
from kashdao_protocol_sdk.smart_account.types import UnsignedUserOp


def _execute_function() -> dict[str, Any]:
    for entry in SIMPLE_ACCOUNT_ABI:
        if entry.get("type") == "function" and entry.get("name") == "execute":
            return entry
    raise RuntimeError("SimpleAccount ABI is missing execute — vendored ABI is broken")


_EXECUTE_FN = _execute_function()
_EXECUTE_SELECTOR_BYTES: bytes = bytes(
    keccak(text=f"execute({','.join(i['type'] for i in _EXECUTE_FN['inputs'])})")[:4]
)
_EXECUTE_ARG_TYPES: list[str] = [str(i["type"]) for i in _EXECUTE_FN["inputs"]]


async def simulate_user_op(
    web3: AsyncWeb3,
    user_op: UnsignedUserOp,
) -> SimulationResult:
    """Simulate the inner trade by ``eth_call``-ing through the SA's ``execute``.

    Decodes the outer ``execute(target, value, data)`` tuple from the
    UserOp's ``call_data``, then routes the simulated call to the
    actual market contract (the inner target) using the smart account
    as the ``from`` address. Reverts surface as
    :class:`SimulationFailure` with the decoded Market / EntryPoint
    revert reason via :func:`decode_market_revert`.
    """
    try:
        target, _value, inner_calldata = _decode_execute_call(user_op.call_data)
    except _BadCallData as err:
        return SimulationFailure(
            revert_reason=err.reason,
            decoded_error=None,
        )
    if target == "0x":
        return SimulationFailure(
            revert_reason="UserOp callData has no inner target",
            decoded_error=None,
        )

    try:
        params: TxParams = {
            "from": cast(Any, AsyncWeb3.to_checksum_address(user_op.sender)),
            "to": AsyncWeb3.to_checksum_address(target),
            "data": cast(HexStr, inner_calldata or "0x"),
        }
        await web3.eth.call(params)
        return SimulationSuccess()
    except (ContractCustomError, ContractLogicError) as err:
        return _failure_from(err)
    except Exception as err:
        return _failure_from(err)


def _failure_from(err: object) -> SimulationFailure:
    revert_data = _extract_revert_data(err)
    decoded = decode_market_revert(revert_data) if revert_data else None
    return SimulationFailure(
        revert_reason=_derive_reason(err, decoded),
        decoded_error=_to_typed_decoded(decoded),
    )


def _to_typed_decoded(decoded: DecodedRevert | None) -> SimulationDecodedError | None:
    if decoded is None:
        return None
    return SimulationDecodedError(name=decoded.name, args=tuple(decoded.args))


def _decode_execute_call(call_data: str) -> tuple[str, int, str]:
    """Decode ``SimpleAccount.execute(target, value, data)`` from raw calldata.

    Returns ``(target, value, inner_data)``. Raises :class:`_BadCallData`
    if the selector or layout doesn't match.
    """
    raw = HexBytes(call_data)
    if len(raw) < 4:
        raise _BadCallData("UserOp callData too short to decode")
    selector = bytes(raw[:4])
    if selector != _EXECUTE_SELECTOR_BYTES:
        raise _BadCallData(
            f"unsupported callData selector {selector.hex()} (expected SimpleAccount.execute)"
        )
    try:
        decoded = abi_decode(_EXECUTE_ARG_TYPES, bytes(raw[4:]))
    except Exception as cause:
        raise _BadCallData(f"failed to ABI-decode execute() args: {cause}") from cause
    target = decoded[0]
    value = int(decoded[1])
    inner = HexBytes(decoded[2]).to_0x_hex() if decoded[2] else "0x"
    target_str = target if isinstance(target, str) else "0x" + bytes(target).hex()
    return target_str, value, inner


class _BadCallData(Exception):
    """Internal sentinel for unparseable UserOp ``call_data``.

    Mapped to a :class:`SimulationFailure` at the ``simulate_user_op``
    boundary so consumers don't need to catch a third exception type.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _extract_revert_data(err: object) -> str | None:
    """Walk the exception graph for a hex-encoded revert payload.

    web3.py shapes vary across versions; check the common attributes.
    """
    seen: set[int] = set()
    stack: list[object] = [err]
    while stack:
        node = stack.pop()
        if id(node) in seen or node is None:
            continue
        seen.add(id(node))
        for attr in ("data", "message"):
            v = getattr(node, attr, None)
            if isinstance(v, str) and v.startswith("0x"):
                return v
            if isinstance(v, dict):
                inner = v.get("data") if isinstance(v.get("data"), str) else None
                if isinstance(inner, str) and inner.startswith("0x"):
                    return inner
        cause = getattr(node, "__cause__", None)
        if cause is not None:
            stack.append(cause)
    return None


def _derive_reason(err: object, decoded: DecodedRevert | None) -> str:
    if decoded is not None:
        return str(decoded.name)
    if isinstance(err, BaseException):
        msg = str(err) or err.__class__.__name__
        return msg.split("\n", 1)[0]
    return "unknown revert"


# Suppress unused-import flake — to_bytes is provided in case future
# helpers need raw-bytes coercion for revert payloads.
_ = to_bytes


__all__ = ["simulate_user_op"]
