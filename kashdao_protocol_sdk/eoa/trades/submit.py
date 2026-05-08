"""``client.trades.submit(signed_transaction)`` — staleness guard + chain forward.

Mirrors ``src/eoa/trades/submit.ts``.

**Staleness guard.** Before forwarding, the SDK parses the
signed serialized tx and recovers the EOA address that produced the
signature. If the recovered EOA doesn't match the configured
``signer.owner_address``, we throw ``KashSignerError(STALE_SIGNED_TX)``
pre-flight.

**No retries** — failure is surfaced immediately. Retrying a tx
submission can cause double-spends.
"""

from __future__ import annotations

from typing import cast

import rlp
from eth_account import Account
from eth_typing import HexStr
from hexbytes import HexBytes
from web3 import AsyncWeb3

from kashdao_protocol_sdk.eoa.types import EoaSubmitOptions, EoaSubmitResult
from kashdao_protocol_sdk.shared.errors import KashChainError, KashSignerError
from kashdao_protocol_sdk.shared.types import Hex


async def submit_transaction(
    web3: AsyncWeb3,
    chain_id: int,
    expected_owner_address: Hex,
    signed_transaction: Hex,
    options: EoaSubmitOptions | None = None,
) -> EoaSubmitResult:
    """Forward a signed serialized EIP-1559 tx via ``eth_sendRawTransaction``."""
    opts = options or EoaSubmitOptions()
    if not opts.skip_staleness_check:
        _validate_signed_transaction(chain_id, expected_owner_address, signed_transaction)

    try:
        tx_hash_bytes = await web3.eth.send_raw_transaction(cast(HexStr, signed_transaction))
    except Exception as cause:
        raise KashChainError(
            "eth_sendRawTransaction failed",
            code="TX_SEND_FAILED",
            context={"sender": expected_owner_address},
            cause=cause,
        ) from cause
    return EoaSubmitResult(transaction_hash=AsyncWeb3.to_hex(tx_hash_bytes))


def _validate_signed_transaction(
    chain_id: int,
    expected_owner_address: Hex,
    signed_transaction: Hex,
) -> None:
    """Parse the EIP-1559 envelope and validate chain-id, then signer.

    Order matters: a chain-id mismatch produces a *recovered EOA that
    looks unrelated* (because the signature is over chainId-bearing
    bytes), so we check chainId FIRST and surface the precise
    diagnostic. Only if chainId matches do we recover and compare the
    EOA — at which point a recovered-mismatch is a true staleness
    signal, not a chain confusion.
    """
    parsed_chain_id = _parse_eip1559_chain_id(signed_transaction)
    if parsed_chain_id is not None and parsed_chain_id != chain_id:
        raise KashSignerError(
            f"signed transaction targets chainId {parsed_chain_id} but client is on {chain_id}",
            code="STALE_SIGNED_TX",
            context={
                "sender": expected_owner_address,
                "expected_chain_id": chain_id,
                "parsed_chain_id": parsed_chain_id,
            },
        )

    try:
        recovered = Account.recover_transaction(signed_transaction)
    except Exception as cause:
        raise KashSignerError(
            "signed transaction is malformed and cannot be parsed / recovered",
            code="SIGNED_TX_MALFORMED",
            context={"sender": expected_owner_address},
            cause=cause,
        ) from cause

    if recovered.lower() != expected_owner_address.lower():
        raise KashSignerError(
            "signed transaction was made by a different EOA than the configured "
            "signer. After populating gas + fee fields, recompute the hash via "
            "client.trades.hash_of(transaction) and re-sign. If your signer "
            "rewraps the serialized tx in a way that breaks recovery, pass "
            "EoaSubmitOptions(skip_staleness_check=True) to client.trades.submit().",
            code="STALE_SIGNED_TX",
            context={
                "expected_owner": expected_owner_address,
                "recovered_owner": recovered,
            },
        )


def _parse_eip1559_chain_id(signed_transaction: Hex) -> int | None:
    """Parse the chainId from an EIP-1559 (type-0x02) signed envelope.

    Returns ``None`` if the envelope is not type-0x02 or cannot be
    RLP-decoded (caller treats this as "unknown — let the chain
    reject"). No reliance on private ``eth_account`` internals.
    """
    raw = HexBytes(signed_transaction)
    if len(raw) < 2 or raw[0] != 0x02:
        return None
    try:
        decoded = rlp.decode(bytes(raw[1:]))
    except Exception:
        return None
    if not isinstance(decoded, list) or len(decoded) < 1:
        return None
    chain_id_bytes = decoded[0]
    if not isinstance(chain_id_bytes, bytes):
        return None
    if chain_id_bytes == b"":
        return 0
    return int.from_bytes(chain_id_bytes, "big")


__all__ = ["submit_transaction"]
