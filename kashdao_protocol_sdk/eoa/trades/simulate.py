"""Pre-flight EOA-tx simulation via ``eth_call``.

Mirrors ``src/eoa/trades/simulate.ts``.

A successful simulation returns ``SimulationSuccess``; reverts surface
as ``SimulationFailure`` with the Market contract's custom error
decoded via :func:`decode_market_revert`.
"""

from __future__ import annotations

from typing import cast

from eth_typing import HexStr
from web3 import AsyncWeb3
from web3.exceptions import ContractCustomError, ContractLogicError
from web3.types import TxParams, Wei

from kashdao_protocol_sdk.eoa.types import UnsignedTransaction
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


async def simulate_transaction(
    web3: AsyncWeb3,
    transaction: UnsignedTransaction,
) -> SimulationResult:
    """Pre-flight ``eth_call`` against the destination contract."""
    if not transaction.to:
        return SimulationFailure(
            revert_reason="transaction has no `to` field",
            decoded_error=None,
        )
    try:
        call_params: TxParams = {
            "to": AsyncWeb3.to_checksum_address(transaction.to),
            "data": cast(HexStr, transaction.data or "0x"),
            "value": Wei(transaction.value or 0),
        }
        await web3.eth.call(call_params)
        return SimulationSuccess()
    except (ContractCustomError, ContractLogicError) as err:
        return _failure_from(err)
    except Exception as err:
        return _failure_from(err)


def _failure_from(err: object) -> SimulationFailure:
    """Build a :class:`SimulationFailure` from any caught exception."""
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


__all__ = ["simulate_transaction"]
