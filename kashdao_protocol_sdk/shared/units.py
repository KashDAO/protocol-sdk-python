"""Unit helpers for human-readable amounts.

Mirrors ``src/shared/units.ts``.

USDC is **6 decimal** (atomic units = 1e6 per 1 USDC). Outcome tokens
are **18 decimal WAD** (atomic units = 1e18 per 1 token), matching the
on-chain Market math precision. Hand-coding ``10_000_000`` for "10
USDC" or ``500_000_000_000_000_000`` for 0.5 tokens is hostile and
error-prone — these helpers wrap the protocol's fixed decimals.

**String input is preferred for fractional values.** Passing ``0.1``
as a Python float can introduce floating-point error before parsing
(``0.1 + 0.2 != 0.3``). String input avoids the round-trip:
``usdc('0.1') == 100_000`` exactly. Integer inputs are safe.

Truncation, not rounding: a string like ``'0.1234567'`` passed to
:func:`usdc` (which has 6 decimals) becomes ``123_456`` (the trailing
``7`` is dropped). Mirrors viem's ``parseUnits`` behaviour. For tighter
control, pre-quantize before passing.

Mode-agnostic — both EOA and Smart Account modes use these.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from kashdao_protocol_sdk.shared.errors import KashConfigError

USDC_DECIMALS = 6
TOKEN_DECIMALS = 18

_DECIMAL_RE = re.compile(r"^\d+(\.\d+)?$")


def usdc(value: int | str | float) -> int:
    """Convert a human-readable USDC amount to atomic 6-decimal units.

    >>> usdc(10)
    10000000
    >>> usdc('0.5')
    500000
    >>> usdc('1.234567')
    1234567

    Truncates beyond 6 decimals (``'1.2345678'`` → ``1_234_567``).
    Raises :class:`KashConfigError` on negative inputs, non-finite
    numbers, or strings that don't parse as a non-negative decimal.
    """
    return _to_atomic(value, USDC_DECIMALS, "usdc")


def tokens(value: int | str | float) -> int:
    """Convert a human-readable outcome-token amount to atomic 18-decimal (WAD) units.

    >>> tokens(1)
    1000000000000000000
    >>> tokens('0.5')
    500000000000000000
    """
    return _to_atomic(value, TOKEN_DECIMALS, "tokens")


def format_usdc(atomic: int, *, decimals: int | Literal["all"] = 2) -> str:
    """Format atomic 6-decimal USDC units as a human-readable string.

    Defaults to fixed 2-decimal display ("10.00"); pass ``decimals=N``
    to override or ``decimals='all'`` to keep full precision.

    >>> format_usdc(10_000_000)
    '10.00'
    >>> format_usdc(1_234_567)
    '1.23'
    >>> format_usdc(1_234_567, decimals=6)
    '1.234567'
    """
    return _format_atomic(atomic, USDC_DECIMALS, decimals)


def format_tokens(atomic: int, *, decimals: int | Literal["all"] = 4) -> str:
    """Format atomic 18-decimal (WAD) outcome-token units.

    Defaults to fixed 4-decimal display.

    >>> format_tokens(1_000_000_000_000_000_000)
    '1.0000'
    >>> format_tokens(500_000_000_000_000_000, decimals=2)
    '0.50'
    """
    return _format_atomic(atomic, TOKEN_DECIMALS, decimals)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _to_atomic(value: int | str | float, decimals: int, fn_name: str) -> int:
    if isinstance(value, bool):  # bool is a subclass of int in Python — reject
        raise KashConfigError(
            f"{fn_name}(): value must be a number or string, got bool",
            code="INVALID_AMOUNT",
            context={"value": value, "fn": fn_name},
        )
    if isinstance(value, int):
        if value < 0:
            raise KashConfigError(
                f"{fn_name}(): value must be non-negative, got {value}",
                code="INVALID_AMOUNT",
                context={"value": value, "fn": fn_name},
            )
        result: int = value * (10**decimals)
        return result
    if isinstance(value, float):
        if not (value == value) or value in (float("inf"), float("-inf")):
            raise KashConfigError(
                f"{fn_name}(): value must be finite, got {value}",
                code="INVALID_AMOUNT",
                context={"value": value, "fn": fn_name},
            )
        if value < 0:
            raise KashConfigError(
                f"{fn_name}(): value must be non-negative, got {value}",
                code="INVALID_AMOUNT",
                context={"value": value, "fn": fn_name},
            )
        # Round-trip via str to defeat the obvious 0.1 + 0.2 traps.
        # Consumers wanting exact fractional precision should pass a string.
        raw = repr(value)
    elif isinstance(value, str):
        if not _DECIMAL_RE.match(value):
            raise KashConfigError(
                f"{fn_name}(): string value must be a non-negative decimal "
                f'(e.g. "10" or "10.5"), got "{value}"',
                code="INVALID_AMOUNT",
                context={"value": value, "fn": fn_name},
            )
        raw = value
    else:
        raise KashConfigError(
            f"{fn_name}(): value must be a number or string, got {type(value).__name__}",
            code="INVALID_AMOUNT",
            context={"value": value, "fn": fn_name},
        )

    # Parse via Decimal for exact base-10 truncation behaviour.
    try:
        dec = Decimal(raw)
    except Exception as exc:
        raise KashConfigError(
            f"{fn_name}(): could not parse '{raw}' as a decimal",
            code="INVALID_AMOUNT",
            context={"value": raw, "fn": fn_name},
            cause=exc,
        ) from exc
    # Multiply by 10**decimals and truncate (matches viem.parseUnits).
    scaled = dec * (Decimal(10) ** decimals)
    return int(scaled)


def _format_atomic(atomic: int, decimals: int, display: int | Literal["all"]) -> str:
    if not isinstance(atomic, int) or isinstance(atomic, bool):
        raise KashConfigError(
            f"format atomic value must be int, got {type(atomic).__name__}",
            code="INVALID_FORMAT_ARG",
            context={"value": atomic},
        )
    sign = "-" if atomic < 0 else ""
    abs_atomic = abs(atomic)
    divisor = 10**decimals
    int_part = abs_atomic // divisor
    frac_part = abs_atomic % divisor
    full_frac = str(frac_part).zfill(decimals)
    full = f"{sign}{int_part}" if frac_part == 0 else f"{sign}{int_part}.{full_frac}"

    if display == "all":
        return full
    if not isinstance(display, int) or display < 0:
        raise KashConfigError(
            f"formatXxx(decimals=...): must be a non-negative int or 'all', got {display}",
            code="INVALID_FORMAT_DECIMALS",
            context={"decimals": display},
        )

    if display == 0:
        return f"{sign}{int_part}"

    if decimals == 0 or full_frac == "0" * decimals:
        return f"{sign}{int_part}." + ("0" * display)
    if len(full_frac) >= display:
        return f"{sign}{int_part}.{full_frac[:display]}"
    return f"{sign}{int_part}.{full_frac.ljust(display, '0')}"


__all__ = [
    "TOKEN_DECIMALS",
    "USDC_DECIMALS",
    "format_tokens",
    "format_usdc",
    "tokens",
    "usdc",
]
