"""Unit tests for ``eoa/trades/hash.py`` — EIP-1559 unsigned tx hash.

These tests anchor the encoding shape so the byte-level parity test
(``tests/parity/test_eoa_parity.py``, lands when the TS-side parity
fixture generator ships) has a known-good baseline.
"""

from __future__ import annotations

from kashdao_protocol_sdk import UnsignedTransaction
from kashdao_protocol_sdk.eoa.trades.hash import (
    compute_transaction_hash,
    serialize_unsigned,
)


def _sample_tx() -> UnsignedTransaction:
    """A deterministic EIP-1559 tx for hash tests."""
    return UnsignedTransaction(
        chain_id=84532,
        to="0xcccccccccccccccccccccccccccccccccccccccc",
        data="0xdeadbeef",
        value=0,
        nonce=42,
        gas=200_000,
        max_fee_per_gas=1_500_000_000,
        max_priority_fee_per_gas=1_000_000_000,
    )


class TestComputeTransactionHash:
    def test_returns_32_byte_hash(self) -> None:
        h = compute_transaction_hash(_sample_tx())
        assert h.startswith("0x")
        assert len(h) == 66  # "0x" + 64 hex chars = 32 bytes

    def test_deterministic(self) -> None:
        h1 = compute_transaction_hash(_sample_tx())
        h2 = compute_transaction_hash(_sample_tx())
        assert h1 == h2

    def test_changes_on_gas_modification(self) -> None:
        tx1 = _sample_tx()
        tx2 = tx1.model_copy(update={"gas": 300_000})
        assert compute_transaction_hash(tx1) != compute_transaction_hash(tx2)

    def test_changes_on_nonce(self) -> None:
        tx1 = _sample_tx()
        tx2 = tx1.model_copy(update={"nonce": 43})
        assert compute_transaction_hash(tx1) != compute_transaction_hash(tx2)

    def test_changes_on_chain_id(self) -> None:
        tx1 = _sample_tx()
        tx2 = tx1.model_copy(update={"chain_id": 8453})
        assert compute_transaction_hash(tx1) != compute_transaction_hash(tx2)

    def test_changes_on_calldata(self) -> None:
        tx1 = _sample_tx()
        tx2 = tx1.model_copy(update={"data": "0xcafebabe"})
        assert compute_transaction_hash(tx1) != compute_transaction_hash(tx2)


class TestSerializeUnsigned:
    def test_starts_with_eip1559_envelope(self) -> None:
        # 0x02 envelope byte for type-2 (EIP-1559).
        encoded = serialize_unsigned(_sample_tx())
        assert encoded[0:1] == b"\x02"

    def test_round_trip_fields_present_in_rlp(self) -> None:
        # Sanity: every nonzero field appears in the RLP payload.
        # (Not a parity test — just confirms the encoder isn't dropping fields.)
        encoded = serialize_unsigned(_sample_tx())
        # Calldata hex bytes must appear after the envelope/RLP header.
        assert b"\xde\xad\xbe\xef" in encoded
        # `to` address bytes must appear too.
        assert bytes.fromhex("cc" * 20) in encoded
