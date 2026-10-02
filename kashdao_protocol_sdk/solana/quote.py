"""Exact quotes for the Solana market program.

Mirrors ``src/solana/quote.ts``. The program has no view functions, so a
quote is computed off chain by :mod:`kashdao_protocol_sdk.solana.curve`, the
integer port of the program's arithmetic (parity-tested to the integer
against the protocol's Python reference model). The input is a decoded
``Market`` account plus its collateral balance — the balance matters,
because ``sell`` refuses a payout above ``balance - fees`` exactly as the
program does — and the market's own ``sell_fee_bps``.
"""

from __future__ import annotations

from kashdao_protocol_sdk.solana import curve
from kashdao_protocol_sdk.solana.accounts import (
    MarketAccount,
    marginal_prices,
    require_outcome,
    to_curve_market,
)
from kashdao_protocol_sdk.solana.types import SolanaBuyQuote, SolanaRedeemQuote, SolanaSellQuote


def quote_buy(
    account: MarketAccount, collateral_usdc: int, outcome: int, amount_in_usdc: int
) -> SolanaBuyQuote:
    """Quote spending ``amount_in_usdc`` (atomic, fee-inclusive) on ``outcome``."""
    k = require_outcome(outcome, account.num_outcomes)
    before = to_curve_market(account, collateral_usdc)
    result = curve.buy(before, k, amount_in_usdc)
    tokens = result.quote.tokens_wad
    return SolanaBuyQuote(
        outcome=k,
        amount_in_usdc=amount_in_usdc,
        protocol_fee_usdc=result.market.fees_usdc - before.fees_usdc,
        tokens_out_wad=tokens,
        average_price_wad=(
            0 if tokens == 0 else amount_in_usdc * curve.USDC_TO_WAD * curve.WAD // tokens
        ),
        prices_after_wad=marginal_prices(result.market),
    )


def quote_sell(
    account: MarketAccount, collateral_usdc: int, outcome: int, tokens_in_wad: int
) -> SolanaSellQuote:
    """Quote selling ``tokens_in_wad`` of ``outcome`` at the market's own sell fee."""
    k = require_outcome(outcome, account.num_outcomes)
    before = to_curve_market(account, collateral_usdc)
    result = curve.sell(before, k, tokens_in_wad)
    return SolanaSellQuote(
        outcome=k,
        tokens_in_wad=tokens_in_wad,
        amount_out_usdc=result.net_usdc,
        protocol_fee_usdc=result.market.fees_usdc - before.fees_usdc,
        prices_after_wad=marginal_prices(result.market),
    )


def quote_redeem(
    account: MarketAccount, collateral_usdc: int, outcome: int, amount_wad: int
) -> SolanaRedeemQuote:
    """Quote redeeming ``amount_wad`` of ``outcome`` from a resolved or cancelled market."""
    k = require_outcome(outcome, account.num_outcomes)
    result = curve.claim(to_curve_market(account, collateral_usdc), k, amount_wad)
    return SolanaRedeemQuote(outcome=k, amount_wad=amount_wad, payout_usdc=result.payout.usdc)


__all__ = ["quote_buy", "quote_redeem", "quote_sell"]
