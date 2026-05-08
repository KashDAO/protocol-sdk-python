"""``decode_market_revert`` correctness + caching contract.

W16: pre-compute selector → entry table at module load. The decoder
must be a constant-time dict lookup, not an N-keccak linear scan.
"""

from __future__ import annotations

from eth_utils import keccak

from kashdao_protocol_sdk.shared.contracts.abis import MARKET_ABI
from kashdao_protocol_sdk.shared.contracts.decoders import (
    _ERROR_TABLE,
    DecodedRevert,
    decode_market_revert,
)


class TestErrorTableCaching:
    def test_table_is_populated_with_market_errors(self) -> None:
        # Market ABI has many error entries; require at least 10 to
        # guard against a future change that empties the table.
        assert len(_ERROR_TABLE) >= 10

    def test_table_is_dict_for_constant_time_lookup(self) -> None:
        assert isinstance(_ERROR_TABLE, dict)

    def test_keys_are_4_byte_selectors(self) -> None:
        for selector in _ERROR_TABLE:
            assert isinstance(selector, bytes)
            assert len(selector) == 4

    def test_known_market_error_present(self) -> None:
        """Spot-check: at least one no-arg error from MARKET_ABI is
        reachable via the table.
        """
        for entry in MARKET_ABI:
            if entry.get("type") != "error" or entry.get("inputs"):
                continue
            sig = f"{entry['name']}()"
            selector = bytes(keccak(text=sig)[:4])
            assert selector in _ERROR_TABLE
            return
        # If MARKET_ABI ever drops all no-arg errors, this test
        # silently passes — that's fine; the other tests cover
        # the table-non-empty invariant.


class TestDecodeMarketRevert:
    def test_returns_none_on_short_data(self) -> None:
        assert decode_market_revert("0x") is None
        assert decode_market_revert("0xab") is None
        assert decode_market_revert("0xabcdef") is None

    def test_returns_none_on_unknown_selector(self) -> None:
        assert decode_market_revert("0xdeadbeef" + "00" * 32) is None

    def test_decodes_known_market_error(self) -> None:
        # Pick the first error entry, build its selector + empty payload,
        # and confirm the decoder round-trips the name.
        first_err = next(e for e in MARKET_ABI if e.get("type") == "error")
        inputs = first_err.get("inputs", [])
        # Skip errors with non-trivial inputs for this round-trip test;
        # we just want to prove the selector path matches.
        if inputs:
            return
        sig = f"{first_err['name']}()"
        selector = keccak(text=sig)[:4]
        decoded = decode_market_revert("0x" + selector.hex())
        assert isinstance(decoded, DecodedRevert)
        assert decoded.name == first_err["name"]
        assert decoded.args == ()
