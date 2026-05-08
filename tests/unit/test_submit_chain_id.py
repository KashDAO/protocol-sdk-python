"""``submit_transaction`` chain-id parsing & diagnostic order.

A chain-id mismatch must surface as the precise
``STALE_SIGNED_TX(parsed_chain_id=...)`` diagnostic — not a confusing
"recovered EOA mismatch" caused by signing over chainId-bearing bytes.
"""

from __future__ import annotations

import pytest
from eth_account import Account

from kashdao_protocol_sdk import KashSignerError
from kashdao_protocol_sdk.eoa.trades.submit import (
    _parse_eip1559_chain_id,
    _validate_signed_transaction,
)

# Deterministic 32-byte test private key (Anvil/Hardhat default account 0).
_TEST_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _sign_eip1559(chain_id: int) -> tuple[str, str]:
    """Sign a minimal EIP-1559 tx and return ``(serialized_hex, owner)``."""
    account = Account.from_key(_TEST_KEY)
    signed = account.sign_transaction(
        {
            "type": 2,
            "chainId": chain_id,
            "nonce": 0,
            "to": "0x" + "00" * 20,
            "value": 0,
            "data": "0x",
            "gas": 21_000,
            "maxFeePerGas": 1_000_000_000,
            "maxPriorityFeePerGas": 1_000_000_000,
        }
    )
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    return raw.to_0x_hex(), account.address


class TestParseEip1559ChainId:
    def test_parses_known_chain_id(self) -> None:
        signed, _ = _sign_eip1559(84532)  # Base Sepolia
        assert _parse_eip1559_chain_id(signed) == 84532

    def test_parses_low_chain_id(self) -> None:
        signed, _ = _sign_eip1559(1)
        assert _parse_eip1559_chain_id(signed) == 1

    def test_rejects_non_eip1559_envelope(self) -> None:
        # Legacy / 0x01 / 0x03 envelopes — not type-2 → return None
        assert _parse_eip1559_chain_id("0x01" + "ff" * 32) is None
        assert _parse_eip1559_chain_id("0x03") is None
        assert _parse_eip1559_chain_id("0x") is None

    def test_returns_none_on_garbage(self) -> None:
        assert _parse_eip1559_chain_id("0x02deadbeef") is None


class TestValidateSignedTransaction:
    def test_passes_when_chain_and_owner_match(self) -> None:
        signed, owner = _sign_eip1559(84532)
        # Should not raise.
        _validate_signed_transaction(84532, owner, signed)

    def test_raises_chain_mismatch_first(self) -> None:
        """Wrong chainId must surface the precise diagnostic — NOT
        the confusing recovered-owner mismatch that happens because
        the signature is over different bytes.
        """
        signed, owner = _sign_eip1559(99999)  # Sign for chain 99999
        with pytest.raises(KashSignerError) as exc_info:
            _validate_signed_transaction(84532, owner, signed)
        assert exc_info.value.code == "STALE_SIGNED_TX"
        ctx = exc_info.value.context
        assert ctx is not None
        assert ctx["expected_chain_id"] == 84532
        assert ctx["parsed_chain_id"] == 99999
        # The diagnostic must NOT mention recovered_owner — that would
        # be the wrong, confusing failure mode.
        assert "recovered_owner" not in ctx

    def test_raises_owner_mismatch_when_chain_matches(self) -> None:
        signed, _real_owner = _sign_eip1559(84532)
        wrong_owner = "0x" + "ab" * 20
        with pytest.raises(KashSignerError) as exc_info:
            _validate_signed_transaction(84532, wrong_owner, signed)
        assert exc_info.value.code == "STALE_SIGNED_TX"
        assert "recovered_owner" in exc_info.value.context  # type: ignore[operator]
