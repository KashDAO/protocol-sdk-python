"""Unit tests for ``shared/units.py`` — usdc, tokens, format_usdc, format_tokens."""

from __future__ import annotations

import pytest

from kashdao_protocol_sdk import (
    KashConfigError,
    format_tokens,
    format_usdc,
    tokens,
    usdc,
)


class TestUsdc:
    def test_int_conversion(self) -> None:
        assert usdc(0) == 0
        assert usdc(1) == 1_000_000
        assert usdc(10) == 10_000_000

    def test_string_fractional(self) -> None:
        assert usdc("0.1") == 100_000
        assert usdc("0.5") == 500_000
        assert usdc("1.234567") == 1_234_567

    def test_truncates_beyond_decimals(self) -> None:
        # USDC has 6 decimals; trailing digits get truncated, matching viem.
        assert usdc("1.2345678") == 1_234_567

    def test_negative_int_raises(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            usdc(-1)
        assert exc_info.value.code == "INVALID_AMOUNT"

    def test_negative_string_raises(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            usdc("-1")
        assert exc_info.value.code == "INVALID_AMOUNT"

    def test_non_decimal_string_raises(self) -> None:
        for bad in ("abc", "", "1e6", "1.2.3", " 1 ", "+1"):
            with pytest.raises(KashConfigError):
                usdc(bad)

    def test_bool_rejected(self) -> None:
        # bool is a subclass of int in Python; we explicitly reject it.
        with pytest.raises(KashConfigError):
            usdc(True)


class TestTokens:
    def test_int_conversion(self) -> None:
        assert tokens(0) == 0
        assert tokens(1) == 10**18
        assert tokens(2) == 2 * 10**18

    def test_string_fractional(self) -> None:
        assert tokens("0.5") == 5 * 10**17
        assert tokens("0.1") == 10**17


class TestFormatUsdc:
    def test_default_2_decimals(self) -> None:
        assert format_usdc(0) == "0.00"
        assert format_usdc(10_000_000) == "10.00"
        assert format_usdc(1_234_567) == "1.23"

    def test_custom_decimals(self) -> None:
        assert format_usdc(1_234_567, decimals=6) == "1.234567"
        assert format_usdc(1_234_567, decimals=4) == "1.2345"
        assert format_usdc(1_234_567, decimals=0) == "1"

    def test_all_decimals(self) -> None:
        assert format_usdc(1_234_567, decimals="all") == "1.234567"

    def test_zero_pads(self) -> None:
        assert format_usdc(1_000_000, decimals=4) == "1.0000"


class TestFormatTokens:
    def test_default_4_decimals(self) -> None:
        assert format_tokens(10**18) == "1.0000"
        assert format_tokens(5 * 10**17) == "0.5000"

    def test_custom_decimals(self) -> None:
        assert format_tokens(5 * 10**17, decimals=2) == "0.50"
        assert format_tokens(10**18, decimals=18) == "1.000000000000000000"
