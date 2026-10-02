"""Kash accumulator — a runnable Hummingbot strategy for the Kash protocol.

Unlike the `amm_arb_kash_uniswap.py` skeleton (which stubs its venue
quote and profitability logic), THIS script is complete and runnable as
shipped. It is a single-venue dollar-cost-averaging (DCA) accumulator:
it buys a fixed USDC notional of one market outcome on a fixed interval,
on Base mainnet (chain 8453) by default, until a total budget is filled
— with an optional price ceiling so it stops buying once the outcome
gets too expensive.

It exercises the full Kash trade path end-to-end via
`kashdao-protocol-sdk` (EOA mode): quote → (one-time) USDC approval →
buy → on-chain confirmation → position read. That makes it the
reference for "how a Hummingbot user actually trades on Kash," and a
template to fork into richer strategies.

Run it::

    start --script kash_accumulator.py --conf conf_kash_accumulator.yml

The private key is NEVER stored in the YAML. The config holds the NAME
of an environment variable that contains the key; the strategy reads it
at startup. Fund the trading EOA with USDC (to trade) and a little ETH
(for gas) on Base — EOA mode has no paymaster sponsorship.

Built for the Hummingbot V2 script API (`StrategyV2Base` /
`StrategyV2ConfigBase`), which replaced the older `ScriptStrategyBase`
removed in Hummingbot v2.13. Requires Hummingbot >= v2.13; the API was
source-verified against v2.15.0. Load-test it in your own Hummingbot
install and pin that version before production — the V2 surface still
moves between releases.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import TYPE_CHECKING

from eth_account import Account
from hummingbot.core.data_type.common import MarketDict
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from pydantic import Field

from kashdao_protocol_sdk import (
    MAX_UINT256,
    BuildApproveParams,
    BuildBuyParams,
    KashProtocolError,
    KashSimulationRevertedError,
    QuoteParams,
    SendEoaOptions,
    create_eoa_client,
    format_usdc,
    usdc,
    viem_account_eoa_signer,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from hummingbot.connector.connector_base import ConnectorBase

    from kashdao_protocol_sdk import EoaClient


_WAD = Decimal(10) ** 18


class KashAccumulatorConfig(StrategyV2ConfigBase):
    """Script config — supplied via ``--conf conf_kash_accumulator.yml``."""

    script_file_name: str = os.path.basename(__file__)
    # StrategyV2ConfigBase requires markets to be declared via update_markets.
    # We use no Hummingbot connectors (Kash is driven via the SDK), so it
    # stays empty — see update_markets below.

    # ── Network / market ────────────────────────────────────────────
    chain_id: int = Field(
        default=8453, description="EVM chain id. 8453 = Base mainnet, 84532 = Base Sepolia."
    )
    rpc_url: str = Field(
        default="https://mainnet.base.org",
        description="Base RPC URL (EIP-1559). wss:// enables real-time market watch.",
    )
    market_address: str = Field(
        default="",
        description="Kash market contract address. Find one via api.kash.bot/v1/markets.",
    )
    outcome_index: int = Field(default=0, description="Zero-based outcome to accumulate.")

    # ── Sizing / cadence ────────────────────────────────────────────
    order_amount_usdc: Decimal = Field(
        default=Decimal("10"), description="USDC notional bought per slice."
    )
    total_budget_usdc: Decimal = Field(
        default=Decimal("100"),
        description="Stop once cumulative filled USDC reaches this total.",
    )
    interval_seconds: int = Field(default=300, description="Seconds between slices.")
    max_slippage_bps: int = Field(
        default=100, description="Max slippage in basis points (100 = 1%)."
    )
    max_outcome_price: Decimal = Field(
        default=Decimal("1"),
        description="Skip a slice if the outcome's post-trade price (0-1) would exceed this.",
    )

    # ── Signing ─────────────────────────────────────────────────────
    signer_key_env: str = Field(
        default="KASH_TRADER_PK",
        description="NAME of the env var holding the EOA private key (key is NEVER stored in config).",
    )

    def update_markets(self, markets: MarketDict) -> MarketDict:
        # No Hummingbot connectors — Kash is driven directly via the SDK,
        # so we register no exchange/trading-pair. Returns markets unchanged.
        return markets


class KashAccumulator(StrategyV2Base):
    """Hummingbot V2 script that DCAs into a Kash market outcome via the SDK."""

    def __init__(
        self,
        connectors: Mapping[str, ConnectorBase],
        config: KashAccumulatorConfig,
    ) -> None:
        super().__init__(connectors, config)
        self.config = config

        pk = os.environ.get(config.signer_key_env)
        if not pk:
            raise ValueError(
                f"Env var {config.signer_key_env!r} is unset — it must hold the trading EOA "
                "private key. Set it before starting (the key is never read from the YAML)."
            )
        account = Account.from_key(pk)
        self._signer = viem_account_eoa_signer(account)
        self._owner: str = account.address
        self._kash: EoaClient = create_eoa_client(
            chain_id=config.chain_id,
            rpc=config.rpc_url,
            signer=self._signer,
        )

        # usdc() takes int/float/str — pass the Decimal as a string to keep
        # exact precision (and because Decimal isn't an accepted input type).
        self._budget_atomic = usdc(str(config.total_budget_usdc))
        self._slice_atomic = usdc(str(config.order_amount_usdc))
        self._filled_atomic = 0
        self._approved = False
        self._trade_in_flight = False
        self._last_slice_ts = 0.0
        self._last_tx: str | None = None
        self._last_skip_reason: str | None = None
        self._done = False

        self.logger().info(
            f"Kash accumulator ready — owner={self._owner} chain={config.chain_id} "
            f"market={config.market_address} outcome={config.outcome_index} "
            f"slice={config.order_amount_usdc} USDC budget={config.total_budget_usdc} USDC"
        )

    # ── Hummingbot lifecycle ────────────────────────────────────────

    def on_tick(self) -> None:
        """Clock tick. Schedule async work; never block the tick loop."""
        if self._done or self._trade_in_flight:
            return
        if not self.config.market_address:
            self._last_skip_reason = "market_address is empty — set it in the config"
            return
        if self._filled_atomic >= self._budget_atomic:
            self._finish()
            return
        if (self.current_timestamp - self._last_slice_ts) < self.config.interval_seconds:
            return
        # Guard BEFORE scheduling so overlapping ticks can't stack trades.
        self._trade_in_flight = True
        self._last_slice_ts = self.current_timestamp
        safe_ensure_future(self._execute_slice())

    def on_stop(self) -> None:
        """Release the SDK's web3 transport so we don't leak sockets."""
        safe_ensure_future(self._kash.aclose())

    # ── Trade path ──────────────────────────────────────────────────

    async def _execute_slice(self) -> None:
        try:
            await self._ensure_approval()

            remaining = self._budget_atomic - self._filled_atomic
            amount = min(self._slice_atomic, remaining)
            if amount <= 0:
                self._finish()
                return

            # Pre-trade quote: enforce the price ceiling before spending.
            quote = await self._kash.markets.quote(
                self.config.market_address,
                QuoteParams(side="BUY", outcome=self.config.outcome_index, amount=amount),
            )
            price_after = Decimal(quote.prices_after_wad[self.config.outcome_index]) / _WAD
            if price_after > self.config.max_outcome_price:
                self._last_skip_reason = (
                    f"post-trade price {price_after:.4f} > ceiling {self.config.max_outcome_price}"
                )
                self.logger().info(f"Skipping slice — {self._last_skip_reason}")
                return

            # Execute. send.buy runs its own pre-flight simulation, so a
            # would-be revert surfaces as KashSimulationRevertedError below
            # rather than burning gas.
            result = await self._kash.trades.send.buy(
                self.config.market_address,
                BuildBuyParams(
                    account=self._owner,
                    outcome=self.config.outcome_index,
                    amount_usdc=amount,
                    max_slippage_bps=self.config.max_slippage_bps,
                ),
                SendEoaOptions(wait=True),
            )
            self._filled_atomic += amount
            self._last_tx = result.transaction_hash
            self._last_skip_reason = None
            self.logger().info(
                f"Bought {format_usdc(amount)} USDC of outcome {self.config.outcome_index} "
                f"— tx {result.transaction_hash} "
                f"(filled {format_usdc(self._filled_atomic)}/{format_usdc(self._budget_atomic)} USDC)"
            )
            if self._filled_atomic >= self._budget_atomic:
                self._finish()
        except KashSimulationRevertedError as exc:
            # Pre-flight caught a revert — log the decoded reason and retry next interval.
            decoded = getattr(exc, "decoded_error", None)
            self._last_skip_reason = f"simulation reverted: {decoded or exc}"
            self.logger().warning(f"Slice skipped — {self._last_skip_reason}")
        except KashProtocolError as exc:
            # Operational error (RPC, signer, chain). Log and continue; don't crash the strategy.
            self._last_skip_reason = f"{type(exc).__name__}: {exc}"
            self.logger().error(f"Slice failed — {self._last_skip_reason}")
        except Exception as exc:  # last-resort guard — keep the strategy alive
            self._last_skip_reason = f"unexpected: {type(exc).__name__}: {exc}"
            self.logger().error(f"Slice errored — {self._last_skip_reason}", exc_info=True)
        finally:
            self._trade_in_flight = False

    async def _ensure_approval(self) -> None:
        """One-time USDC approval of the market as spender (EOA mode)."""
        if self._approved:
            return
        allowance = await self._kash.account.usdc_allowance(self._owner, self.config.market_address)
        if allowance < self._budget_atomic:
            self.logger().info("Approving USDC for the market (one-time)…")
            await self._kash.trades.send.approve(
                BuildApproveParams(
                    account=self._owner,
                    spender=self.config.market_address,
                    amount=MAX_UINT256,
                ),
                SendEoaOptions(wait=True),
            )
        self._approved = True

    def _finish(self) -> None:
        if not self._done:
            self._done = True
            self.logger().info(
                f"Budget filled: {format_usdc(self._filled_atomic)} USDC across outcome "
                f"{self.config.outcome_index}. No further slices."
            )

    # ── Status panel ────────────────────────────────────────────────

    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "Strategy not ready."
        lines = [
            "  Kash accumulator (EOA mode)",
            f"    owner         : {self._owner}",
            f"    chain / market: {self.config.chain_id} / {self.config.market_address}",
            f"    outcome       : {self.config.outcome_index}",
            f"    filled        : {format_usdc(self._filled_atomic)} / "
            f"{format_usdc(self._budget_atomic)} USDC",
            f"    slice / every : {self.config.order_amount_usdc} USDC / "
            f"{self.config.interval_seconds}s",
            f"    price ceiling : {self.config.max_outcome_price}",
            f"    status        : {'DONE' if self._done else 'running'}"
            f"{' (trade in flight)' if self._trade_in_flight else ''}",
            f"    last tx       : {self._last_tx or '—'}",
        ]
        if self._last_skip_reason:
            lines.append(f"    last skip     : {self._last_skip_reason}")
        return "\n".join(lines)
