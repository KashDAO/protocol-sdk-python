"""Quotes against what the program ACTUALLY DID on mainnet.

The snapshot holds four real markets (the TS SDK's ``quote.test.ts`` vectors):

- market 3 is a fresh 2-outcome genesis (20 USDC seed, zero volume);
- market 1 is the same template and seed after exactly ONE 1-USDC buy of
  outcome 0;
- market 0 is a 10-outcome genesis after exactly one 1-USDC buy of outcome 0.

So quoting "1 USDC of outcome 0" on market 3 must land on market 1's on-chain
supply, reserve and prices to the integer — the program's own output — and
replaying genesis + that buy for 10 outcomes must land on market 0. The
remaining literals pin the quote surface for market 2 (mixed history), and
are the same integers the TS SDK asserts.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from kashdao_protocol_sdk.solana import KashProtocolError, KashValidationError, curve
from kashdao_protocol_sdk.solana.accounts import (
    MarketAccount,
    decode_market_account,
    decode_token_account,
    to_curve_market,
)
from kashdao_protocol_sdk.solana.quote import quote_buy, quote_redeem, quote_sell


@pytest.fixture
def market(account_data):
    def load(i: int) -> MarketAccount:
        return decode_market_account(account_data(f"market:{i}"))

    return load


@pytest.fixture
def collateral(account_data):
    def load(i: int) -> int:
        return decode_token_account(account_data(f"collateral:{i}")).amount

    return load


class TestQuoteReproducesTheProgramsTransition:
    def test_one_usdc_on_fresh_market_3_lands_exactly_on_market_1(self, market, collateral) -> None:
        fresh, traded = market(3), market(1)
        assert fresh.cumulative_volume_wad == 0
        assert traded.cumulative_volume_wad == 10**18
        assert fresh.template_id == traded.template_id

        quote = quote_buy(fresh, collateral(3), 0, 1_000_000)
        assert fresh.supply_wad[0] + quote.tokens_out_wad == traded.supply_wad[0]
        assert quote.tokens_out_wad == 1_935_364_141_039_464_403
        assert quote.protocol_fee_usdc == traded.fees_owed_usdc

        after = curve.buy(to_curve_market(fresh, collateral(3)), 0, 1_000_000).market
        assert after.reserve == traded.reserve_wad
        assert after.aggregate == traded.a
        assert after.balance_usdc == collateral(1)
        assert quote.prices_after_wad == (522_519_393_545_485_097, 476_417_341_591_233_920)

    def test_genesis_10_outcomes_then_one_usdc_lands_exactly_on_market_0(self, market) -> None:
        on_chain = market(0)
        assert on_chain.num_outcomes == 10
        replayed = curve.buy(curve.genesis(10, 20_000_000), 0, 1_000_000).market
        assert replayed.supplies == on_chain.supply_wad[:10]
        assert replayed.aggregate == on_chain.a
        assert replayed.reserve == on_chain.reserve_wad
        assert replayed.fees_usdc == on_chain.fees_owed_usdc


class TestQuoteSurfaceMarket2:
    def test_buy(self, market, collateral) -> None:
        q = quote_buy(market(2), collateral(2), 0, 1_000_000)
        assert q.outcome == 0
        assert q.amount_in_usdc == 1_000_000
        assert q.protocol_fee_usdc == 10_000
        assert q.tokens_out_wad == 4_118_366_472_914_253_530
        assert q.average_price_wad == 242_814_719_519_697_416
        assert q.prices_after_wad == (248_584_270_064_218_984, 661_971_193_237_771_820)

    def test_sell_retains_the_amm_fee_and_withholds_the_protocol_fee(
        self, market, collateral
    ) -> None:
        m = market(2)
        assert m.sell_fee_bps == 50
        q = quote_sell(m, collateral(2), 0, 4_118_366_472_914_253_530)
        assert q.amount_out_usdc == 907_501
        assert q.protocol_fee_usdc == 9_166
        # A round trip loses the two fees and the spread — never gains.
        assert q.amount_out_usdc < 1_000_000

    def test_refuses_what_the_program_refuses_naming_the_program_error(
        self, market, collateral
    ) -> None:
        # The genesis leg is not sellable: market 3 holds only genesis supply.
        with pytest.raises(KashValidationError) as exc:
            quote_sell(market(3), collateral(3), 0, 1)
        assert KashProtocolError.is_(exc.value)
        assert exc.value.program_error == "ExceedsSupply"
        with pytest.raises(KashValidationError, match="outcome is not an outcome of this market"):
            quote_buy(market(2), collateral(2), 2, 1_000_000)
        with pytest.raises(KashValidationError):
            quote_buy(market(2), collateral(2), 0, 0)

    def test_redeem_refuses_an_active_market(self, market, collateral) -> None:
        with pytest.raises(KashValidationError) as exc:
            quote_redeem(market(2), collateral(2), 0, 10**18)
        assert exc.value.program_error == "InvalidDomain"

    def test_redeem_pays_the_winner_pro_rata_and_the_loser_nothing(
        self, market, collateral
    ) -> None:
        m = market(2)
        resolved = replace(m, state=2, winning_outcome=1, sum_winning_supply=m.supply_wad[1])
        winner = quote_redeem(resolved, collateral(2), 1, 10**18)
        assert winner.payout_usdc == curve.to_usdc(
            curve.redemption_wad(m.reserve_wad, 10**18, m.supply_wad[1])
        )
        assert quote_redeem(resolved, collateral(2), 0, 10**18).payout_usdc == 0

    def test_redeem_cancelled_pays_the_snapshot_price(self, market, collateral) -> None:
        m = market(2)
        cancelled = replace(
            m,
            state=3,
            cancel_reserve_wad=m.reserve_wad,
            cancel_a=m.a,
            cancel_supply_wad=m.supply_wad,
        )
        q = quote_redeem(cancelled, collateral(2), 1, 10**18)
        snapshot = curve.Snapshot(m.reserve_wad, m.a, m.supply_wad[:2])
        assert q.payout_usdc == curve.to_usdc(curve.snapshot_refund_wad(snapshot, 1, 10**18))
        assert q.payout_usdc > 0


class TestSellUsesTheMarketsOwnFee:
    """Each ``Market`` stores its own ``sell_fee_bps``; a quote must use it, never 50."""

    TOKENS = 4_118_366_472_914_253_530

    def test_a_200_bps_market_quotes_at_200_bps(self, market, collateral) -> None:
        m = replace(market(2), sell_fee_bps=200)
        state = to_curve_market(m, collateral(2))
        assert state.sell_fee_bps == 200
        expected = curve.sell_quote(
            state.aggregate,
            state.reserve,
            curve.weight(2, 0),
            state.supplies[0],
            self.TOKENS,
            sell_fee_bps=200,
        )
        gross = curve.to_usdc(expected.net_wad)
        expected_net = gross - curve.protocol_fee(gross, 100)
        q = quote_sell(m, collateral(2), 0, self.TOKENS)
        assert q.amount_out_usdc == expected_net
        assert q.amount_out_usdc == 893_820
        assert (
            q.amount_out_usdc < quote_sell(market(2), collateral(2), 0, self.TOKENS).amount_out_usdc
        )

    def test_a_zero_fee_market_pays_more_than_the_50_bps_default(self, market, collateral) -> None:
        free = quote_sell(replace(market(2), sell_fee_bps=0), collateral(2), 0, self.TOKENS)
        default = quote_sell(market(2), collateral(2), 0, self.TOKENS)
        assert free.amount_out_usdc > default.amount_out_usdc

    def test_a_250_bps_account_matches_the_curve_at_250(self, market, collateral) -> None:
        """The TS SDK's vector: the account's field, carried to ``curve.sell``."""
        m = market(2)
        at50 = quote_sell(m, collateral(2), 0, self.TOKENS)
        at250 = quote_sell(replace(m, sell_fee_bps=250), collateral(2), 0, self.TOKENS)
        expected = curve.sell(
            replace(to_curve_market(m, collateral(2)), sell_fee_bps=250), 0, self.TOKENS
        ).net_usdc
        assert m.sell_fee_bps == 50
        assert at250.amount_out_usdc == expected
        assert at250.amount_out_usdc < at50.amount_out_usdc


def _traded_market(sell_fee_bps: int | None = None) -> curve.CurveMarket:
    """A 2-outcome market after one 5 USDC buy of outcome 0, so outcome 0 is sellable."""
    fresh = (
        curve.genesis(2, 20_000_000)
        if sell_fee_bps is None
        else curve.genesis(2, 20_000_000, curve.PROTOCOL_FEE_BPS, sell_fee_bps)
    )
    return curve.buy(fresh, 0, 5_000_000).market


class TestCurveSellFee:
    """Mirrors ``@kashdao/pythag-math``'s ``kash-curve-sell-fee.test.ts``."""

    def test_genesis_defaults_to_50_and_stores_the_fee_on_the_market(self) -> None:
        assert _traded_market().sell_fee_bps == 50
        assert _traded_market(200).sell_fee_bps == 200

    def test_a_200_bps_market_releases_what_sell_quote_computes_at_200(self) -> None:
        at200, at50 = _traded_market(200), _traded_market(50)
        tokens = at200.supplies[0] - at200.genesis_leg_wad
        sold200, sold50 = curve.sell(at200, 0, tokens), curve.sell(at50, 0, tokens)
        assert sold200.quote == curve.sell_quote(
            at200.aggregate, at200.reserve, curve.weight(2, 0), at200.supplies[0], tokens, 200
        )
        # Same release, different fee: the 200 bps seller nets strictly less.
        assert sold200.quote.release_wad == sold50.quote.release_wad
        assert sold200.net_usdc < sold50.net_usdc
        # The retained fee stays in the reserve.
        assert sold200.market.reserve > sold50.market.reserve

    def test_a_zero_fee_market_nets_the_release_less_only_the_protocol_fee(self) -> None:
        free = _traded_market(0)
        tokens = free.supplies[0] - free.genesis_leg_wad
        sold = curve.sell(free, 0, tokens)
        assert sold.quote.net_wad == sold.quote.release_wad
        gross = curve.to_usdc(sold.quote.release_wad)
        assert sold.net_usdc == gross - curve.protocol_fee(gross)

    def test_refuses_a_sell_fee_above_100_percent(self) -> None:
        with pytest.raises(KashValidationError, match="sell fee bps"):
            curve.genesis(2, 20_000_000, 100, 10_001)
