"""Integer port of the Solana protocol's curve.

Mirrors ``@kashdao/pythag-math``'s ``kash-curve.ts`` (vendored into the TS
SDK verbatim), which is itself a port of the protocol's Python reference
model (``SVM/tests/support/reference_model/model.py``). The Solana program
has no view functions, so every quote is computed HERE from decoded account
state and must agree with the on-chain curve to the integer.
``tests/unit/solana/test_solana_curve_parity.py`` asserts exact equality
against the reference model's corpus (``curve-parity.jsonl``); a
disagreement is a blocking defect, never a tolerance.

Function names, argument order, checks and error codes follow the model one
for one. A failed check raises :class:`KashValidationError` whose
``program_error`` is the model's code (``ExceedsSupply``,
``InsufficientCollateral`` …).

:class:`CurveMarket` carries the market's own ``sell_fee_bps``: every
``Market`` account stores its sell fee (copied from its template at
creation) and the program charges THAT fee, so ``sell`` never assumes the
50 bps default, which only names what the shipped templates happen to set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Final, Literal, NoReturn

from kashdao_protocol_sdk.shared.errors import KashValidationError

WAD: Final = 10**18
BPS: Final = 10_000
#: USDC (6dp) → WAD (18dp).
USDC_TO_WAD: Final = 10**12
MAX_OUTCOMES: Final = 100
MIN_SEED_USDC: Final = 20_000_000
U64_MAX: Final = 2**64 - 1
U128_MAX: Final = 2**128 - 1
U256_MAX: Final = 2**256 - 1
SUPPLY_CAP_WAD: Final = 2**127 - 1
PROTOCOL_FEE_BPS: Final = 100
MAX_PROTOCOL_FEE_BPS: Final = 500
SELL_FEE_BPS: Final = 50

KashCurveErrorCode = Literal[
    "InvalidDomain",
    "Overflow",
    "ZeroDivisor",
    "ZeroAmount",
    "ExceedsSupply",
    "PostBurnAZero",
    "SeedOverflow",
    "InsufficientCollateral",
]


def _fail(code: KashCurveErrorCode, field: str) -> NoReturn:
    raise KashValidationError(f"{code}: {field}", field=field, program_error=code)


def _natural(value: int, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail("InvalidDomain", field)
    if maximum is not None and value > maximum:
        _fail("Overflow", field)
    return value


def _positive(value: int, field: str) -> int:
    _natural(value, field)
    if value == 0:
        _fail("ZeroDivisor", field)
    return value


def mul_div(left: int, right: int, divisor: int) -> int:
    """Full product, then one floor; checks the U256 quotient, not the product."""
    _natural(left, "left", U256_MAX)
    _natural(right, "right", U256_MAX)
    _positive(divisor, "divisor")
    _natural(divisor, "divisor", U256_MAX)
    return _natural((left * right) // divisor, "quotient", U256_MAX)


def root(value: int) -> int:
    """Floor integer square root (Python ``isqrt``); the root includes zero."""
    _natural(value, "radicand", U256_MAX)
    return math.isqrt(value)


def weight(n: int, outcome: int) -> int:
    """Outcome weight in WAD: ``WAD // n``, with the remainder on the last outcome."""
    _natural(n, "n", MAX_OUTCOMES)
    if n < 2:
        _fail("InvalidDomain", "n")
    _natural(outcome, "outcome")
    if outcome >= n:
        _fail("InvalidDomain", "outcome")
    base = WAD // n
    return base if outcome < n - 1 else WAD - base * (n - 1)


def term(coefficient: int, supply: int) -> int:
    _positive(coefficient, "weight")
    _natural(coefficient, "weight", WAD)
    _natural(supply, "supply", SUPPLY_CAP_WAD)
    return mul_div(coefficient, supply * supply, WAD)


def aggregate(supplies: list[int] | tuple[int, ...]) -> int:
    """E1: floor every complete term before summing."""
    weight(len(supplies), 0)
    total = 0
    for i, s in enumerate(supplies):
        total += term(weight(len(supplies), i), s)
    return _natural(total, "aggregate", U256_MAX)


def replace_term(total: int, coefficient: int, before: int, after: int) -> int:
    """Section 5.1; never uses an algebraic delta or a quote aggregate."""
    _natural(total, "aggregate", U256_MAX)
    old = term(coefficient, before)
    if old > total:
        _fail("InvalidDomain", "aggregate below old term")
    return _natural(total - old + term(coefficient, after), "aggregate", U256_MAX)


def liquidity_coefficient(reserve: int, total: int) -> int:
    """E3, view only."""
    _natural(reserve, "reserve", SUPPLY_CAP_WAD)
    return mul_div(reserve, WAD, _positive(root(total), "norm"))


def marginal_price(reserve: int, total: int, coefficient: int, supply: int) -> int:
    """E4: form ``a*s`` before the sole division."""
    _natural(reserve, "reserve", SUPPLY_CAP_WAD)
    _positive(total, "aggregate")
    _positive(coefficient, "weight")
    _natural(coefficient, "weight", WAD)
    _natural(supply, "supply", SUPPLY_CAP_WAD)
    return mul_div(reserve, coefficient * supply, total)


def capital_shares(supplies: list[int] | tuple[int, ...]) -> list[int]:
    """E5, view only. Ties for largest component use the lowest index."""
    total = _positive(aggregate(supplies), "aggregate")
    parts = [mul_div(weight(len(supplies), i), s * s, total) for i, s in enumerate(supplies)]
    largest = 0
    for i, p in enumerate(parts):
        if p > parts[largest]:
            largest = i
    summed = sum(parts)
    if summed > WAD:
        _fail("InvalidDomain", "capital share dust underflow")
    parts[largest] += WAD - summed
    for part in parts:
        _natural(part, "capital share", WAD)
    return parts


def seed_wad(raw_seed: int) -> int:
    _natural(raw_seed, "raw seed", U64_MAX)
    if raw_seed < MIN_SEED_USDC:
        _fail("InvalidDomain", "seed below minimum")
    converted = raw_seed * USDC_TO_WAD
    if converted > SUPPLY_CAP_WAD:
        _fail("SeedOverflow", "seed wad")
    return converted


@dataclass(frozen=True, slots=True)
class BuyQuote:
    quote_aggregate: int
    discriminant: int
    tokens_wad: int


def buy_quote(total: int, reserve: int, coefficient: int, supply: int, input_wad: int) -> BuyQuote:
    """E7 to E9, preserving both chained floors and the floor root."""
    _natural(input_wad, "input wad", SUPPLY_CAP_WAD)
    if input_wad == 0:
        _fail("ZeroAmount", "input wad")
    _positive(total, "aggregate")
    _natural(total, "aggregate", U256_MAX)
    _positive(reserve, "reserve")
    _natural(reserve, "reserve", SUPPLY_CAP_WAD)
    _positive(coefficient, "weight")
    _natural(coefficient, "weight", WAD)
    _natural(supply, "supply", SUPPLY_CAP_WAD)
    enlarged = _natural(reserve + input_wad, "reserve", SUPPLY_CAP_WAD)
    first = mul_div(total, enlarged, reserve)
    quoted = mul_div(first, enlarged, reserve)
    disc = _natural(
        supply * supply + mul_div(quoted - total, WAD, coefficient), "discriminant", U256_MAX
    )
    tokens = root(disc) - supply
    _natural(supply + tokens, "supply", SUPPLY_CAP_WAD)
    return BuyQuote(quote_aggregate=quoted, discriminant=disc, tokens_wad=tokens)


@dataclass(frozen=True, slots=True)
class SellQuote:
    quote_aggregate: int
    release_wad: int
    net_wad: int


def sell_quote(
    total: int,
    reserve: int,
    coefficient: int,
    supply: int,
    tokens_wad: int,
    sell_fee_bps: int = SELL_FEE_BPS,
) -> SellQuote:
    """E10 to E12. The AMM sell fee is retained inside the reserve."""
    _natural(tokens_wad, "tokens wad")
    if tokens_wad == 0:
        _fail("ZeroAmount", "tokens wad")
    _positive(total, "aggregate")
    _natural(total, "aggregate", U256_MAX)
    _positive(reserve, "reserve")
    _natural(reserve, "reserve", SUPPLY_CAP_WAD)
    _positive(coefficient, "weight")
    _natural(coefficient, "weight", WAD)
    _natural(supply, "supply", SUPPLY_CAP_WAD)
    _natural(sell_fee_bps, "sell fee bps", BPS)
    if tokens_wad > supply:
        _fail("ExceedsSupply", "tokens wad")
    removed = mul_div(coefficient, tokens_wad * (2 * supply - tokens_wad), WAD)
    if removed > total:
        _fail("InvalidDomain", "quote aggregate underflow")
    quoted = total - removed
    if quoted == 0:
        _fail("PostBurnAZero", "quote aggregate")
    norm = _positive(root(total), "norm")
    released = mul_div(reserve, norm - root(quoted), norm)
    net = released - mul_div(released, sell_fee_bps, BPS)
    return SellQuote(quote_aggregate=quoted, release_wad=released, net_wad=net)


def redemption_wad(reserve: int, tokens_wad: int, winning_supply: int) -> int:
    """E16; final-claim substitution is applied by :func:`resolved_payout`."""
    _natural(reserve, "reserve", SUPPLY_CAP_WAD)
    _natural(tokens_wad, "tokens wad", SUPPLY_CAP_WAD)
    _positive(winning_supply, "winning supply")
    _natural(winning_supply, "winning supply", U256_MAX)
    if tokens_wad > winning_supply:
        _fail("ExceedsSupply", "tokens wad")
    return mul_div(reserve, tokens_wad, winning_supply)


def to_usdc(wad: int) -> int:
    """E17, with checked SPL transfer narrowing."""
    _natural(wad, "wad", U256_MAX)
    return _natural(wad // USDC_TO_WAD, "usdc payout", U64_MAX)


def protocol_fee(amount_usdc: int, fee_bps: int = PROTOCOL_FEE_BPS) -> int:
    """E18: the fee is floored on raw USDC, never on WAD."""
    _natural(amount_usdc, "amount usdc", U64_MAX)
    _natural(fee_bps, "protocol fee bps", MAX_PROTOCOL_FEE_BPS)
    return mul_div(amount_usdc, fee_bps, BPS)


@dataclass(frozen=True, slots=True)
class Payout:
    usdc: int
    reserve_after: int


def resolved_payout(
    reserve: int, tokens_wad: int, winning_supply: int, balance_usdc: int, fees_usdc: int
) -> Payout:
    calculated = redemption_wad(reserve, tokens_wad, winning_supply)
    _natural(balance_usdc, "balance usdc", U64_MAX)
    _natural(fees_usdc, "fees usdc", balance_usdc)
    available = balance_usdc - fees_usdc
    if tokens_wad == winning_supply:
        return Payout(usdc=available, reserve_after=0)
    payout = to_usdc(calculated)
    if payout > available:
        _fail("InsufficientCollateral", "resolved payout")
    return Payout(usdc=payout, reserve_after=reserve - payout * USDC_TO_WAD)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The cancellation snapshot: reserve, aggregate and supplies frozen at ``cancel``."""

    reserve: int
    aggregate: int
    supplies: tuple[int, ...]


def snapshot_refund_wad(snapshot: Snapshot, outcome: int, tokens_wad: int) -> int:
    coefficient = weight(len(snapshot.supplies), outcome)
    _natural(tokens_wad, "tokens wad", snapshot.supplies[outcome])
    price = marginal_price(
        snapshot.reserve, snapshot.aggregate, coefficient, snapshot.supplies[outcome]
    )
    return mul_div(tokens_wad, price, WAD)


def cancelled_payout(
    snapshot: Snapshot,
    outcome: int,
    tokens_wad: int,
    current_reserve: int,
    balance_usdc: int,
    fees_usdc: int,
) -> Payout:
    _natural(current_reserve, "reserve", SUPPLY_CAP_WAD)
    _natural(balance_usdc, "balance usdc", U64_MAX)
    _natural(fees_usdc, "fees usdc", balance_usdc)
    payout = to_usdc(snapshot_refund_wad(snapshot, outcome, tokens_wad))
    if payout > balance_usdc - fees_usdc:
        _fail("InsufficientCollateral", "cancelled payout")
    after = current_reserve - payout * USDC_TO_WAD
    return Payout(usdc=payout, reserve_after=max(after, 0))


MarketStatus = Literal["active", "resolved", "cancelled"]


@dataclass(frozen=True, slots=True)
class CurveMarket:
    """Immutable numeric market state, mirroring the model's ``Market`` dataclass."""

    supplies: tuple[int, ...]
    reserve: int
    aggregate: int
    genesis_leg_wad: int
    genesis_unharvested: int
    balance_usdc: int
    fees_usdc: int
    volume_wad: int
    fee_bps: int
    #: The AMM sell fee this market charges, retained inside the reserve (E10 to E12).
    #: PER MARKET: the account's ``sell_fee_bps``, never the 50 bps default.
    sell_fee_bps: int
    status: MarketStatus
    snapshot: Snapshot | None
    winning_outcome: int | None
    winning_supply: int


def _require_status(market: CurveMarket, status: MarketStatus) -> None:
    if market.status != status:
        _fail("InvalidDomain", "market status")


def genesis(
    n: int,
    raw_seed: int,
    fee_bps: int = PROTOCOL_FEE_BPS,
    sell_fee_bps: int = SELL_FEE_BPS,
) -> CurveMarket:
    weight(n, 0)
    protocol_fee(0, fee_bps)
    _natural(sell_fee_bps, "sell fee bps", BPS)
    reserve = seed_wad(raw_seed)
    supplies = tuple(reserve for _ in range(n))
    return CurveMarket(
        supplies=supplies,
        reserve=reserve,
        aggregate=aggregate(supplies),
        genesis_leg_wad=reserve,
        genesis_unharvested=(1 << n) - 1,
        balance_usdc=raw_seed,
        fees_usdc=0,
        volume_wad=0,
        fee_bps=fee_bps,
        sell_fee_bps=sell_fee_bps,
        status="active",
        snapshot=None,
        winning_outcome=None,
        winning_supply=0,
    )


@dataclass(frozen=True, slots=True)
class BuyResult:
    market: CurveMarket
    quote: BuyQuote


def buy(market: CurveMarket, outcome: int, gross_usdc: int) -> BuyResult:
    _require_status(market, "active")
    coefficient = weight(len(market.supplies), outcome)
    fee = protocol_fee(gross_usdc, market.fee_bps)
    incoming = (gross_usdc - fee) * USDC_TO_WAD
    quote = buy_quote(
        market.aggregate, market.reserve, coefficient, market.supplies[outcome], incoming
    )
    supplies = list(market.supplies)
    supplies[outcome] = supplies[outcome] + quote.tokens_wad
    total = replace_term(market.aggregate, coefficient, market.supplies[outcome], supplies[outcome])
    return BuyResult(
        market=replace(
            market,
            supplies=tuple(supplies),
            aggregate=total,
            reserve=market.reserve + incoming,
            balance_usdc=_natural(market.balance_usdc + gross_usdc, "balance usdc", U64_MAX),
            fees_usdc=_natural(market.fees_usdc + fee, "fees usdc", U64_MAX),
            volume_wad=_natural(
                market.volume_wad + gross_usdc * USDC_TO_WAD, "volume wad", U128_MAX
            ),
        ),
        quote=quote,
    )


@dataclass(frozen=True, slots=True)
class SellResult:
    market: CurveMarket
    quote: SellQuote
    net_usdc: int


def sell(market: CurveMarket, outcome: int, tokens_wad: int) -> SellResult:
    _require_status(market, "active")
    coefficient = weight(len(market.supplies), outcome)
    _natural(tokens_wad, "tokens wad")
    if tokens_wad > market.supplies[outcome] - market.genesis_leg_wad:
        _fail("ExceedsSupply", "user tokens wad")
    quote = sell_quote(
        market.aggregate,
        market.reserve,
        coefficient,
        market.supplies[outcome],
        tokens_wad,
        market.sell_fee_bps,
    )
    gross = to_usdc(quote.net_wad)
    fee = protocol_fee(gross, market.fee_bps)
    net = gross - fee
    supplies = list(market.supplies)
    supplies[outcome] = supplies[outcome] - tokens_wad
    total = replace_term(market.aggregate, coefficient, market.supplies[outcome], supplies[outcome])
    if gross > market.balance_usdc - market.fees_usdc:
        _fail("InsufficientCollateral", "sell payout")
    return SellResult(
        market=replace(
            market,
            supplies=tuple(supplies),
            aggregate=total,
            reserve=market.reserve - gross * USDC_TO_WAD,
            balance_usdc=market.balance_usdc - net,
            fees_usdc=_natural(market.fees_usdc + fee, "fees usdc", U64_MAX),
            volume_wad=_natural(market.volume_wad + net * USDC_TO_WAD, "volume wad", U128_MAX),
        ),
        quote=quote,
        net_usdc=net,
    )


def cancel(market: CurveMarket) -> CurveMarket:
    _require_status(market, "active")
    return replace(
        market,
        status="cancelled",
        snapshot=Snapshot(
            reserve=market.reserve, aggregate=market.aggregate, supplies=market.supplies
        ),
    )


def resolve(market: CurveMarket, outcome: int) -> CurveMarket:
    _require_status(market, "active")
    weight(len(market.supplies), outcome)
    winning = _positive(market.supplies[outcome], "winning supply")
    return replace(market, status="resolved", winning_outcome=outcome, winning_supply=winning)


@dataclass(frozen=True, slots=True)
class PayoutResult:
    market: CurveMarket
    payout: Payout


def collect_fees(market: CurveMarket) -> PayoutResult:
    _natural(market.balance_usdc, "balance usdc", U64_MAX)
    collected = _natural(market.fees_usdc, "fees usdc", market.balance_usdc)
    return PayoutResult(
        market=replace(market, balance_usdc=market.balance_usdc - collected, fees_usdc=0),
        payout=Payout(usdc=collected, reserve_after=market.reserve),
    )


def claim(
    market: CurveMarket, outcome: int, tokens_wad: int, *, genesis_leg: bool = False
) -> PayoutResult:
    """Numeric settlement, including a single genesis leg; no account ledger."""
    weight(len(market.supplies), outcome)
    _natural(tokens_wad, "tokens wad", market.supplies[outcome])
    mask = market.genesis_unharvested
    bit = 1 << outcome
    if genesis_leg:
        if (mask & bit) == 0 or tokens_wad != market.genesis_leg_wad:
            _fail("InvalidDomain", "genesis claim")
        mask &= ~bit
    elif tokens_wad > market.supplies[outcome] - (
        market.genesis_leg_wad if (mask & bit) != 0 else 0
    ):
        _fail("ExceedsSupply", "user tokens wad")
    winning = market.winning_supply
    if market.status == "cancelled":
        if market.snapshot is None:
            _fail("InvalidDomain", "cancellation snapshot")
        payout = cancelled_payout(
            market.snapshot,
            outcome,
            tokens_wad,
            market.reserve,
            market.balance_usdc,
            market.fees_usdc,
        )
    elif market.status == "resolved":
        if outcome == market.winning_outcome:
            payout = resolved_payout(
                market.reserve, tokens_wad, winning, market.balance_usdc, market.fees_usdc
            )
            winning -= tokens_wad
        else:
            payout = Payout(usdc=0, reserve_after=market.reserve)
    else:
        _fail("InvalidDomain", "market status")
    supplies = list(market.supplies)
    supplies[outcome] = supplies[outcome] - tokens_wad
    return PayoutResult(
        market=replace(
            market,
            supplies=tuple(supplies),
            reserve=payout.reserve_after,
            balance_usdc=market.balance_usdc - payout.usdc,
            winning_supply=winning,
            genesis_unharvested=mask,
        ),
        payout=payout,
    )


__all__ = [
    "BPS",
    "MAX_OUTCOMES",
    "SELL_FEE_BPS",
    "USDC_TO_WAD",
    "WAD",
    "BuyQuote",
    "BuyResult",
    "CurveMarket",
    "KashCurveErrorCode",
    "MarketStatus",
    "Payout",
    "PayoutResult",
    "SellQuote",
    "SellResult",
    "Snapshot",
    "aggregate",
    "buy",
    "buy_quote",
    "cancel",
    "cancelled_payout",
    "capital_shares",
    "claim",
    "collect_fees",
    "genesis",
    "liquidity_coefficient",
    "marginal_price",
    "mul_div",
    "protocol_fee",
    "redemption_wad",
    "replace_term",
    "resolve",
    "resolved_payout",
    "root",
    "seed_wad",
    "sell",
    "sell_quote",
    "snapshot_refund_wad",
    "term",
    "to_usdc",
    "weight",
]
