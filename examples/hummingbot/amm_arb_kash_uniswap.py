"""Hummingbot reference strategy — AMM arbitrage between Kash and Uniswap.

This is the working strategy file referenced by ``HUMMINGBOT_INTEGRATION.md``.
It demonstrates the canonical pattern for using ``kashdao-protocol-sdk``
inside a Hummingbot ``ScriptStrategyBase``: keep a single
``create_eoa_client`` instance for the strategy's lifetime, drive trades
through it on each tick, and never let Kash-specific code leak out of
the SDK boundary.

Strategy logic
--------------

This is a SKELETON. The arbitrage detection and Uniswap leg are
deliberately stubbed — the goal of this file is to show the SDK
integration shape, not a profitable strategy. To productionize:

1. Replace ``_get_uniswap_price`` with a real Uniswap quote
   (e.g. via the Hummingbot ``GatewayHttpClient`` or ``web3.py``
   directly against the Uniswap V3 quoter).
2. Replace ``_is_profitable`` with your spread + fees model.
3. Tune ``_TICK_INTERVAL_S`` for the chain's block time.
4. Add proper position-size sizing — the example trades 10 USDC per
   tick which is just a placeholder.

Required env
------------

- ``KASH_TRADER_PK`` — 0x-prefixed private key for the EOA driving the
  Kash side. The address must hold USDC on Base + have approved the
  market for spending (see ``02_one_line_trade.py`` for the approval
  flow if needed).
- ``KASH_MARKET_ADDRESS`` — the Kash market contract address.
- ``KASH_BASE_SEPOLIA_RPC`` (or ``BASE_SEPOLIA_RPC``) — chain RPC URL.
- ``KASH_OUTCOME_INDEX`` — outcome to arb (default ``0``).

How to run
----------

This file is meant to be loaded by Hummingbot, not invoked directly.
With Hummingbot installed::

    hummingbot start
    > import_script_file kash_amm_arb examples/hummingbot/amm_arb_kash_uniswap.py
    > start --script kash_amm_arb

For a standalone smoke-run that exercises the Kash leg only (without
Hummingbot's ``ScriptStrategyBase`` machinery), see
``examples/eoa/02_one_line_trade.py``.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, ClassVar

from eth_account import Account

from kashdao_protocol_sdk import (
    MAX_UINT256,
    BuildApproveParams,
    BuildBuyParams,
    QuoteParams,
    create_eoa_client,
    usdc,
    viem_account_eoa_signer,
)

# Hummingbot is an optional runtime — import lazily so this file remains
# importable in standalone Python (e.g. for ``ruff check`` /
# ``pytest --collect-only``).
try:
    from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
except ImportError:  # pragma: no cover — only when run outside Hummingbot
    ScriptStrategyBase = object  # type: ignore[misc, assignment]

_LOG = logging.getLogger(__name__)
_TICK_INTERVAL_S = 5.0
_TRADE_NOTIONAL_USDC = 10  # placeholder; set via your sizing model
_MAX_SLIPPAGE_BPS = 50


def _env(name: str, *, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"{name} is required for the kash AMM arb strategy")
    return value or ""


class KashAmmArbStrategy(ScriptStrategyBase):  # type: ignore[misc, valid-type]
    """Arbitrage outcome-token prices between Kash and Uniswap.

    The strategy maintains one Kash client for its lifetime. Each
    Hummingbot tick: read both venues, decide if a profitable spread
    exists, and trade if so.
    """

    # No Hummingbot connectors — we drive Kash via the SDK directly,
    # so the connector dict is intentionally empty. ClassVar declares
    # this as class-level metadata (not per-instance state) which
    # matches Hummingbot's conventions and silences ruff's RUF012.
    markets: ClassVar[dict[str, set[str]]] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        market = _env("KASH_MARKET_ADDRESS", required=True)
        rpc = _env(
            "KASH_BASE_SEPOLIA_RPC",
            default=_env("BASE_SEPOLIA_RPC", default="https://sepolia.base.org"),
        )
        pk = _env("KASH_TRADER_PK", required=True)
        outcome = int(_env("KASH_OUTCOME_INDEX", default="0"))

        account = Account.from_key(pk)
        self._kash_market = market
        self._outcome = outcome
        self._signer = viem_account_eoa_signer(account)
        self._owner = account.address
        self._kash = create_eoa_client(
            chain_id=84532,
            rpc=rpc,
            signer=self._signer,
        )
        self._approved = False
        _LOG.info(
            "kash AMM arb strategy initialized: trader=%s market=%s outcome=%d",
            self._owner,
            market,
            outcome,
        )

    # ---- Hummingbot lifecycle ---------------------------------------

    def on_tick(self) -> None:
        """Hummingbot calls this on every tick. Kick off async work."""
        from hummingbot.core.utils.async_utils import safe_ensure_future

        safe_ensure_future(self._tick_async())

    async def on_stop(self) -> None:
        """Release the SDK's HTTP/WS sessions cleanly on stop."""
        await self._kash.aclose()

    # ---- Strategy core ----------------------------------------------

    async def _tick_async(self) -> None:
        try:
            await self._ensure_approval()

            kash_quote = await self._kash.markets.quote(
                self._kash_market,
                QuoteParams(side="BUY", outcome=self._outcome, amount=usdc(_TRADE_NOTIONAL_USDC)),
            )
            uni_quote = await self._get_uniswap_price()

            if self._is_profitable(kash_quote, uni_quote):
                _LOG.info("profitable spread detected; placing kash buy")
                receipt = await self._kash.trades.send.buy(
                    self._kash_market,
                    BuildBuyParams(
                        # The buy/sell/close param shape uses
                        # `smart_account` as the field name in both
                        # modes; in EOA mode this is the EOA address
                        # itself (the trader IS the signer).
                        smart_account=self._owner,
                        outcome=self._outcome,
                        amount_usdc=usdc(_TRADE_NOTIONAL_USDC),
                        max_slippage_bps=_MAX_SLIPPAGE_BPS,
                    ),
                )
                _LOG.info(
                    "kash buy landed: tx=%s gas=%d",
                    receipt.transaction_hash,
                    receipt.gas_used,
                )
                # Hedge / unwind on Uniswap goes here in a real strategy.
        except Exception:
            # Per Hummingbot strategy convention, never let an unhandled
            # exception kill the script — log and try again next tick.
            _LOG.exception("kash AMM arb tick failed")

    async def _ensure_approval(self) -> None:
        """Idempotent USDC allowance check for the market contract."""
        if self._approved:
            return
        current = await self._kash.account.usdc_allowance(self._owner, self._kash_market)
        if current < usdc(1_000_000):  # arbitrary "enough" threshold
            _LOG.info("approving USDC for market spend")
            await self._kash.trades.send.approve(
                BuildApproveParams(
                    account=self._owner,
                    spender=self._kash_market,
                    amount=MAX_UINT256,
                ),
            )
        self._approved = True

    # ---- Stubs to be replaced by your real strategy logic -----------

    async def _get_uniswap_price(self) -> Decimal:
        """STUB: replace with a real Uniswap quote.

        For example, route via the Hummingbot ``GatewayHttpClient``
        (gateway connector) or call the Uniswap V3 quoter directly via
        ``web3.py``. The Kash SDK has no opinion on which.
        """
        return Decimal(0)

    def _is_profitable(self, _kash_quote: Any, _uni_quote: Decimal) -> bool:
        """STUB: replace with your spread + fees model."""
        return False
