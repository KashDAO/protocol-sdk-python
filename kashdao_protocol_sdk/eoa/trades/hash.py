"""Canonical EOA-tx hashing.

Mirrors ``src/eoa/trades/hash.ts``.

``keccak256(serialize_eip1559(tx))`` is the digest the EOA's signer
signs and the chain's signature-recovery validates against. Same
staleness story as smart-account ``compute_user_op_hash``: any field
change (gas, fees, nonce, calldata) changes the serialized bytes and
therefore the hash, so consumers MUST recompute after populating gas
+ fees and BEFORE signing.

Implementation: manual RLP encoding of the EIP-1559 (type-0x02)
envelope, no signature fields. Identical wire shape to viem's
``serializeTransaction`` for unsigned EIP-1559 txs:

    0x02 || rlp([
      chainId, nonce,
      maxPriorityFeePerGas, maxFeePerGas, gas,
      to, value, data,
      accessList  # always empty here — SDK never populates one
    ])

Then ``keccak256(...)`` produces the canonical 32-byte hash.
"""

from __future__ import annotations

import rlp
from eth_utils import keccak, to_bytes  # type: ignore[attr-defined]
from hexbytes import HexBytes

from kashdao_protocol_sdk.eoa.types import UnsignedTransaction
from kashdao_protocol_sdk.shared.types import Hex


def compute_transaction_hash(transaction: UnsignedTransaction) -> Hex:
    """Compute the canonical EIP-1559 transaction hash for an unsigned tx.

    Network-pure (deterministic). Must be called **after** populating
    gas + fees if the consumer modifies those fields post-build.
    """
    payload = b"\x02" + bytes(rlp.encode(_unsigned_payload(transaction)))
    return HexBytes(keccak(payload)).to_0x_hex()


def serialize_unsigned(transaction: UnsignedTransaction) -> bytes:
    """Return the EIP-1559 RLP-encoded unsigned tx bytes (with 0x02 envelope).

    Used by :func:`compute_transaction_hash`; exposed so consumers can
    inspect the exact bytes that will be signed (debug helper, not a
    typical hot path).
    """
    return b"\x02" + bytes(rlp.encode(_unsigned_payload(transaction)))


def _unsigned_payload(transaction: UnsignedTransaction) -> list[object]:
    to_bytes_value: bytes = b""
    if transaction.to and transaction.to != "0x":
        to_bytes_value = to_bytes(hexstr=transaction.to)
    data_bytes: bytes = b""
    if transaction.data and transaction.data != "0x":
        data_bytes = to_bytes(hexstr=transaction.data)
    return [
        transaction.chain_id,
        transaction.nonce,
        transaction.max_priority_fee_per_gas,
        transaction.max_fee_per_gas,
        transaction.gas,
        to_bytes_value,
        transaction.value,
        data_bytes,
        [],  # empty access list — SDK never populates one
    ]


__all__ = ["compute_transaction_hash", "serialize_unsigned"]
