"""Unit tests for ``eoa/trades/build.py``.

Most importantly: pin the function selectors + arg types against the
vendored ABI. The build.py module derives both at import time from
``MARKET_ABI`` so we never drift from the contract's actual parameter
types — but a regression test makes the invariant explicit.
"""

from __future__ import annotations

from eth_utils import keccak

from kashdao_protocol_sdk.eoa.trades.build import (
    _BUY_ARG_TYPES,
    _BUY_SELECTOR,
    _SELL_ARG_TYPES,
    _SELL_SELECTOR,
)

# Hardcoded reference signatures derived from the vendored Market ABI.
# DO NOT update these casually — they pin the function selectors that
# every signed trade transaction must encode against. If Solidity
# changes a type (e.g. uint8 → uint16 for outcome), regenerate the
# ABI, then update these expected signatures here AND verify on-chain
# trades still execute (the parity-fixture suite, when shipped, will
# catch this automatically).
_EXPECTED_BUY_SIGNATURE = "buyExactAssetsIn(uint8,uint256,uint256,uint64,address)"
_EXPECTED_SELL_SIGNATURE = "sellExactTokensIn(uint8,uint256,uint256,uint64,address)"


class TestBuySelector:
    def test_selector_matches_expected_signature(self) -> None:
        expected = keccak(text=_EXPECTED_BUY_SIGNATURE)[:4]
        assert _BUY_SELECTOR == expected, (
            f"BUY selector mismatch: got {_BUY_SELECTOR.hex()}, "
            f"expected {expected.hex()} (signature: {_EXPECTED_BUY_SIGNATURE})"
        )

    def test_arg_types_match_expected_signature(self) -> None:
        # Argument types must be the canonical Solidity types, in order.
        # If this changes, update _EXPECTED_BUY_SIGNATURE above too.
        assert _BUY_ARG_TYPES == ["uint8", "uint256", "uint256", "uint64", "address"]


class TestSellSelector:
    def test_selector_matches_expected_signature(self) -> None:
        expected = keccak(text=_EXPECTED_SELL_SIGNATURE)[:4]
        assert _SELL_SELECTOR == expected, (
            f"SELL selector mismatch: got {_SELL_SELECTOR.hex()}, "
            f"expected {expected.hex()} (signature: {_EXPECTED_SELL_SIGNATURE})"
        )

    def test_arg_types_match_expected_signature(self) -> None:
        assert _SELL_ARG_TYPES == ["uint8", "uint256", "uint256", "uint64", "address"]


class TestSelectorsDistinct:
    def test_buy_and_sell_selectors_differ(self) -> None:
        # Sanity: it would be a serious bug if these collided.
        assert _BUY_SELECTOR != _SELL_SELECTOR
