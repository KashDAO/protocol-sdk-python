"""``create_solana_client`` — the Kash market program on Solana, non-custodially.

Mirrors ``src/solana/client.ts``. Bring an RPC (a URL or any
:class:`SolanaConnection`) and a signer; the SDK
reads accounts, quotes with the program's exact integer curve, builds
instructions from the IDL and, if asked, signs, sends and confirms.
Mainnet-beta is the default cluster; devnet is supported; any other
deployment can be named by address.

Shape mirrors the EVM clients: ``markets.*`` (reads + quotes),
``account.*`` (positions + USDC), ``trades.*`` (build → simulate → send,
plus one-call ``buy``/``sell``/``redeem``/… that do all three).

Every write carries a mandatory slippage bound: pass exactly one of an
explicit floor (``min_tokens_out_wad`` / ``min_amount_out_usdc``; ``0``
accepts anything) or ``max_slippage_bps`` below a fresh quote — the same
option name the EVM half of this SDK uses.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Generic, Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from solders.instruction import Instruction
from solders.pubkey import Pubkey

from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashConfigError, KashValidationError
from kashdao_protocol_sdk.solana.accounts import (
    MARKET_STATE_ACTIVE,
    MARKET_STATE_CANCELLED,
    MARKET_STATE_RESOLVED,
    LoadedMarket,
    SolanaReader,
    require_outcome,
    to_pubkey,
)
from kashdao_protocol_sdk.solana.clusters import (
    DEFAULT_SOLANA_CLUSTER,
    CustomSolanaDeployment,
    SolanaCluster,
    SolanaDeployment,
    get_solana_deployment,
)
from kashdao_protocol_sdk.solana.connection import (
    Commitment,
    SolanaConnection,
    SolanaRpcConnection,
)
from kashdao_protocol_sdk.solana.instructions import (
    InstructionContext,
    MarketTarget,
    buy_instruction,
    close_position_instruction,
    ensure_usdc_ata_instruction,
    open_position_instruction,
    redeem_instruction,
    sell_instruction,
    usdc_ata,
)
from kashdao_protocol_sdk.solana.pda import KashMarketPdas
from kashdao_protocol_sdk.solana.quote import quote_buy, quote_redeem, quote_sell
from kashdao_protocol_sdk.solana.signer import SolanaSigner
from kashdao_protocol_sdk.solana.transaction import (
    SolanaComputeBudget,
    SolanaSendResult,
    SolanaSimulationResult,
    send_instructions,
    simulate_instructions,
)
from kashdao_protocol_sdk.solana.types import (
    SolanaBuyPlan,
    SolanaBuyQuote,
    SolanaClosePositionPlan,
    SolanaMarket,
    SolanaMarketRef,
    SolanaOpenPositionPlan,
    SolanaPlan,
    SolanaPosition,
    SolanaProgramConfig,
    SolanaRedeemPlan,
    SolanaRedeemQuote,
    SolanaSellPlan,
    SolanaSellQuote,
    SolanaTemplate,
)

#: Seconds a built trade stays valid on chain when no ``deadline`` is given.
DEFAULT_TRADE_DEADLINE_SECONDS: Final = 300

_BPS: Final = 10_000

_P = TypeVar(
    "_P",
    SolanaBuyPlan,
    SolanaSellPlan,
    SolanaRedeemPlan,
    SolanaOpenPositionPlan,
    SolanaClosePositionPlan,
)


@dataclass(frozen=True, slots=True)
class SolanaTradeResult(Generic[_P]):
    """A one-call trade: the confirmed send plus the plan that was sent."""

    signature: str
    slot: int
    plan: _P


class _ConfigSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rpc_url: str | None
    commitment: Literal["processed", "confirmed", "finalized"]
    has_connection: bool

    @field_validator("rpc_url")
    @classmethod
    def _validate_rpc_url(cls, value: str | None) -> str | None:
        if value is not None and not (
            value.startswith("https://")
            or value.startswith("http://localhost")
            or value.startswith("http://127.0.0.1")
        ):
            raise ValueError("must use https:// (http:// permitted only for localhost)")
        return value


def _require_positive(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise KashValidationError(
            f"{field} must be a positive int", field=field, value=repr(value), constraint="> 0"
        )
    return value


def _require_slippage(minimum: int | None, max_slippage_bps: int | None, field: str) -> None:
    if minimum is not None:
        if max_slippage_bps is not None:
            raise KashConfigError(
                f"pass either {field} or max_slippage_bps, not both",
                code=ErrorCode.INVALID_SLIPPAGE,
                context={"field": field},
            )
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
            raise KashConfigError(
                f"{field} must be a non-negative int",
                code=ErrorCode.INVALID_SLIPPAGE,
                context={"field": field},
            )
        return
    if max_slippage_bps is None:
        raise KashConfigError(
            f"a slippage bound is required: pass {field} (an explicit floor, 0 to accept "
            "anything) or max_slippage_bps",
            code=ErrorCode.INVALID_SLIPPAGE,
            context={"field": field},
        )
    if (
        isinstance(max_slippage_bps, bool)
        or not isinstance(max_slippage_bps, int)
        or not 0 <= max_slippage_bps <= _BPS
    ):
        raise KashConfigError(
            f"max_slippage_bps must be in [0, 10_000], got {max_slippage_bps}",
            code=ErrorCode.INVALID_SLIPPAGE,
            context={"max_slippage_bps": max_slippage_bps},
        )


def _less_slippage(quoted: int, bps: int) -> int:
    return quoted * (_BPS - bps) // _BPS


def _require_state(loaded: LoadedMarket, state: int, label: str) -> None:
    if loaded.account.state != state:
        raise KashValidationError(
            f"market {loaded.address} is not {label}",
            field="market",
            value=str(loaded.address),
            constraint=label,
            metadata={"state": loaded.account.state},
        )


def _require_tradeable(
    loaded: LoadedMarket,
    config: SolanaProgramConfig,
    side: Literal["buy", "sell"],
    now_seconds: int,
) -> None:
    """Refuse, before anything is signed, a trade the program would refuse.

    The caller gets a typed answer rather than a paid-for failed transaction.
    Where the program's own error is known it is named (``program_error``):
    ``Paused`` (``Config.paused``), ``HaltedEntries`` (a guardian-halted
    market — it blocks ENTRIES, so a sell is allowed) and ``InvalidState``
    (not ACTIVE). Frozen-ness is derived from the clock exactly as
    ``markets.get`` derives ``status == "frozen"``.
    """
    market = str(loaded.address)

    def refuse(message: str, code: str | None, field: str) -> NoReturn:
        raise KashValidationError(
            f"{message}; {side} refused before signing",
            field=field,
            value=market,
            program_error=code,
            metadata={"market": market},
        )

    if config.paused:
        refuse("the market program is paused", "Paused", "config.paused")
    if loaded.account.state != MARKET_STATE_ACTIVE:
        refuse(f"market {market} is not active", "InvalidState", "market")
    if now_seconds >= loaded.account.freeze_time:
        refuse(f"market {market} is frozen awaiting resolution", None, "market")
    if side == "buy" and loaded.account.halted:
        refuse(f"market {market} is halted for new entries", "HaltedEntries", "market")


def _target(loaded: LoadedMarket) -> MarketTarget:
    return MarketTarget(market_id=loaded.account.market_id, market=loaded.address)


def _instructions_of(plan: SolanaPlan | Sequence[Instruction]) -> Sequence[Instruction]:
    if isinstance(
        plan,
        (
            SolanaBuyPlan,
            SolanaSellPlan,
            SolanaRedeemPlan,
            SolanaOpenPositionPlan,
            SolanaClosePositionPlan,
        ),
    ):
        return plan.instructions
    return plan


class SolanaProtocol:
    """``client.protocol`` — program-level reads."""

    __slots__ = ("_reader",)

    def __init__(self, reader: SolanaReader) -> None:
        self._reader = reader

    async def config(self) -> SolanaProgramConfig:
        return await self._reader.config()

    async def template(self, template_id: int) -> SolanaTemplate:
        return await self._reader.template(template_id)

    async def templates(self) -> list[SolanaTemplate]:
        return await self._reader.templates()


class SolanaMarkets:
    """``client.markets`` — market reads and exact quotes."""

    __slots__ = ("_reader",)

    def __init__(self, reader: SolanaReader) -> None:
        self._reader = reader

    async def get(self, market: SolanaMarketRef) -> SolanaMarket:
        return await self._reader.market(market)

    async def quote_buy(
        self, *, market: SolanaMarketRef, outcome: int, amount_usdc: int
    ) -> SolanaBuyQuote:
        loaded = await self._reader.load_market(market)
        return quote_buy(
            loaded.account,
            loaded.collateral_usdc,
            outcome,
            _require_positive(amount_usdc, "amount_usdc"),
        )

    async def quote_sell(
        self, *, market: SolanaMarketRef, outcome: int, tokens_in_wad: int
    ) -> SolanaSellQuote:
        loaded = await self._reader.load_market(market)
        return quote_sell(
            loaded.account,
            loaded.collateral_usdc,
            outcome,
            _require_positive(tokens_in_wad, "tokens_in_wad"),
        )

    async def quote_redeem(
        self, *, market: SolanaMarketRef, outcome: int, amount_wad: int
    ) -> SolanaRedeemQuote:
        loaded = await self._reader.load_market(market)
        return quote_redeem(
            loaded.account,
            loaded.collateral_usdc,
            outcome,
            _require_positive(amount_wad, "amount_wad"),
        )


class SolanaAccount:
    """``client.account`` — positions and USDC balances."""

    __slots__ = ("_reader",)

    def __init__(self, reader: SolanaReader) -> None:
        self._reader = reader

    async def position(
        self, *, market: SolanaMarketRef, outcome: int, owner: Pubkey | str
    ) -> SolanaPosition:
        loaded = await self._reader.load_market(market)
        return await self._reader.position(
            loaded.address,
            to_pubkey(owner, "owner"),
            require_outcome(outcome, loaded.account.num_outcomes),
        )

    async def positions(
        self, *, market: SolanaMarketRef, owner: Pubkey | str
    ) -> list[SolanaPosition]:
        """Every outcome's position for ``owner``, in one request after the market read."""
        return await self._reader.positions(market, to_pubkey(owner, "owner"))

    async def usdc_balance(self, owner: Pubkey | str) -> int:
        """USDC in the owner's associated token account, atomic. A missing account is ``0``."""
        return await self._reader.usdc_balance(to_pubkey(owner, "owner"))


class SolanaTrades:
    """``client.trades`` — build → simulate → send, plus one-call trades."""

    def __init__(
        self,
        *,
        reader: SolanaReader,
        ctx: InstructionContext,
        connection: SolanaConnection,
        commitment: Commitment,
        now: Callable[[], int],
    ) -> None:
        self._reader = reader
        self._ctx = ctx
        self._connection = connection
        self._commitment = commitment
        self._now = now

    def _deadline(self, deadline: int | None) -> int:
        return deadline if deadline is not None else self._now() + DEFAULT_TRADE_DEADLINE_SECONDS

    # -- build ---------------------------------------------------------------

    async def build_open_position(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        owner: Pubkey | str,
        payer: Pubkey | str | None = None,
    ) -> SolanaOpenPositionPlan:
        """Open an empty position for ``owner``; ``payer`` (default: the owner) pays rent and signs."""
        owner_key = to_pubkey(owner, "owner")
        payer_key = owner_key if payer is None else to_pubkey(payer, "payer")
        loaded = await self._reader.load_market(market)
        k = require_outcome(outcome, loaded.account.num_outcomes)
        return SolanaOpenPositionPlan(
            instructions=(
                open_position_instruction(
                    self._ctx, _target(loaded), owner=owner_key, outcome=k, payer=payer_key
                ),
            ),
            signer=payer_key,
            payer=payer_key,
            market=loaded.address,
            market_id=loaded.account.market_id,
            outcome=k,
        )

    async def build_buy(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        amount_usdc: int,
        trader: Pubkey | str,
        receiver: Pubkey | str | None = None,
        payer: Pubkey | str | None = None,
        min_tokens_out_wad: int | None = None,
        max_slippage_bps: int | None = None,
        deadline: int | None = None,
    ) -> SolanaBuyPlan:
        """Spend ``amount_usdc`` (gross, atomic; the protocol fee comes out of it) on ``outcome``.

        Opens the receiver's position in the same transaction when it does
        not exist yet (``buy`` requires it).
        """
        _require_positive(amount_usdc, "amount_usdc")
        _require_slippage(min_tokens_out_wad, max_slippage_bps, "min_tokens_out_wad")
        trader_key = to_pubkey(trader, "trader")
        receiver_key = trader_key if receiver is None else to_pubkey(receiver, "receiver")
        payer_key = trader_key if payer is None else to_pubkey(payer, "payer")
        loaded, config = await asyncio.gather(
            self._reader.load_market(market), self._reader.config()
        )
        _require_tradeable(loaded, config, "buy", self._now())
        k = require_outcome(outcome, loaded.account.num_outcomes)
        quote: SolanaBuyQuote | None = None
        if min_tokens_out_wad is None:
            quote = quote_buy(loaded.account, loaded.collateral_usdc, k, amount_usdc)
            floor = _less_slippage(quote.tokens_out_wad, max_slippage_bps or 0)
        else:
            floor = min_tokens_out_wad
        position = await self._reader.position(loaded.address, receiver_key, k)
        target = _target(loaded)
        instructions: list[Instruction] = []
        if not position.exists:
            instructions.append(
                open_position_instruction(
                    self._ctx, target, owner=receiver_key, outcome=k, payer=payer_key
                )
            )
        instructions.append(
            buy_instruction(
                self._ctx,
                target,
                trader=trader_key,
                receiver=receiver_key,
                outcome=k,
                amount_in_usdc=amount_usdc,
                min_tokens_out_wad=floor,
                deadline=self._deadline(deadline),
            )
        )
        return SolanaBuyPlan(
            instructions=tuple(instructions),
            signer=trader_key,
            payer=payer_key,
            market=loaded.address,
            market_id=loaded.account.market_id,
            outcome=k,
            min_tokens_out_wad=floor,
            quote=quote,
            opens_position=not position.exists,
        )

    async def build_sell(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        tokens_in_wad: int,
        owner: Pubkey | str,
        receiver: Pubkey | str | None = None,
        payer: Pubkey | str | None = None,
        min_amount_out_usdc: int | None = None,
        max_slippage_bps: int | None = None,
        deadline: int | None = None,
    ) -> SolanaSellPlan:
        """Sell ``tokens_in_wad`` of ``outcome``; creates the receiver's USDC ATA idempotently."""
        _require_positive(tokens_in_wad, "tokens_in_wad")
        _require_slippage(min_amount_out_usdc, max_slippage_bps, "min_amount_out_usdc")
        owner_key = to_pubkey(owner, "owner")
        receiver_key = owner_key if receiver is None else to_pubkey(receiver, "receiver")
        payer_key = owner_key if payer is None else to_pubkey(payer, "payer")
        loaded, config = await asyncio.gather(
            self._reader.load_market(market), self._reader.config()
        )
        _require_tradeable(loaded, config, "sell", self._now())
        k = require_outcome(outcome, loaded.account.num_outcomes)
        quote: SolanaSellQuote | None = None
        if min_amount_out_usdc is None:
            quote = quote_sell(loaded.account, loaded.collateral_usdc, k, tokens_in_wad)
            floor = _less_slippage(quote.amount_out_usdc, max_slippage_bps or 0)
        else:
            floor = min_amount_out_usdc
        return SolanaSellPlan(
            instructions=(
                ensure_usdc_ata_instruction(self._ctx, payer_key, receiver_key),
                sell_instruction(
                    self._ctx,
                    _target(loaded),
                    owner=owner_key,
                    receiver=receiver_key,
                    outcome=k,
                    tokens_in_wad=tokens_in_wad,
                    min_amount_out_usdc=floor,
                    deadline=self._deadline(deadline),
                ),
            ),
            signer=owner_key,
            payer=payer_key,
            market=loaded.address,
            market_id=loaded.account.market_id,
            outcome=k,
            min_amount_out_usdc=floor,
            quote=quote,
        )

    async def _build_redeem_kind(
        self,
        kind: Literal["redeem", "redeem_cancelled"],
        *,
        market: SolanaMarketRef,
        outcome: int,
        owner: Pubkey | str,
        amount_wad: int | None,
        receiver: Pubkey | str | None,
        payer: Pubkey | str | None,
        min_amount_out_usdc: int | None,
        max_slippage_bps: int | None,
    ) -> SolanaRedeemPlan:
        _require_slippage(min_amount_out_usdc, max_slippage_bps, "min_amount_out_usdc")
        owner_key = to_pubkey(owner, "owner")
        receiver_key = owner_key if receiver is None else to_pubkey(receiver, "receiver")
        payer_key = owner_key if payer is None else to_pubkey(payer, "payer")
        loaded = await self._reader.load_market(market)
        if kind == "redeem":
            _require_state(loaded, MARKET_STATE_RESOLVED, "resolved")
        else:
            _require_state(loaded, MARKET_STATE_CANCELLED, "cancelled")
        k = require_outcome(outcome, loaded.account.num_outcomes)
        amount = amount_wad
        if amount is None:
            amount = (await self._reader.position(loaded.address, owner_key, k)).balance_wad
            if amount == 0:
                raise KashValidationError(
                    "position is empty: nothing to redeem",
                    field="amount_wad",
                    value="0",
                    constraint="> 0",
                )
        _require_positive(amount, "amount_wad")
        quote: SolanaRedeemQuote | None = None
        if min_amount_out_usdc is None:
            quote = quote_redeem(loaded.account, loaded.collateral_usdc, k, amount)
            floor = _less_slippage(quote.payout_usdc, max_slippage_bps or 0)
        else:
            floor = min_amount_out_usdc
        return SolanaRedeemPlan(
            kind=kind,
            instructions=(
                ensure_usdc_ata_instruction(self._ctx, payer_key, receiver_key),
                redeem_instruction(
                    self._ctx,
                    _target(loaded),
                    kind=kind,
                    owner=owner_key,
                    receiver=receiver_key,
                    outcome=k,
                    amount_wad=amount,
                    min_amount_out_usdc=floor,
                ),
            ),
            signer=owner_key,
            payer=payer_key,
            market=loaded.address,
            market_id=loaded.account.market_id,
            outcome=k,
            amount_wad=amount,
            min_amount_out_usdc=floor,
            quote=quote,
        )

    async def build_redeem(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        owner: Pubkey | str,
        amount_wad: int | None = None,
        receiver: Pubkey | str | None = None,
        payer: Pubkey | str | None = None,
        min_amount_out_usdc: int | None = None,
        max_slippage_bps: int | None = None,
    ) -> SolanaRedeemPlan:
        """Redeem from a RESOLVED market. ``amount_wad`` defaults to the whole position."""
        return await self._build_redeem_kind(
            "redeem",
            market=market,
            outcome=outcome,
            owner=owner,
            amount_wad=amount_wad,
            receiver=receiver,
            payer=payer,
            min_amount_out_usdc=min_amount_out_usdc,
            max_slippage_bps=max_slippage_bps,
        )

    async def build_redeem_cancelled(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        owner: Pubkey | str,
        amount_wad: int | None = None,
        receiver: Pubkey | str | None = None,
        payer: Pubkey | str | None = None,
        min_amount_out_usdc: int | None = None,
        max_slippage_bps: int | None = None,
    ) -> SolanaRedeemPlan:
        """Redeem from a CANCELLED market at the cancellation snapshot's price."""
        return await self._build_redeem_kind(
            "redeem_cancelled",
            market=market,
            outcome=outcome,
            owner=owner,
            amount_wad=amount_wad,
            receiver=receiver,
            payer=payer,
            min_amount_out_usdc=min_amount_out_usdc,
            max_slippage_bps=max_slippage_bps,
        )

    async def build_close_position(
        self, *, market: SolanaMarketRef, outcome: int, owner: Pubkey | str
    ) -> SolanaClosePositionPlan:
        """Close an EMPTY position; its rent returns to the account that paid it."""
        owner_key = to_pubkey(owner, "owner")
        loaded = await self._reader.load_market(market)
        k = require_outcome(outcome, loaded.account.num_outcomes)
        position = await self._reader.position(loaded.address, owner_key, k)
        if not position.exists or position.rent_payer is None:
            raise KashValidationError(
                "position does not exist: nothing to close",
                field="outcome",
                value=k,
                metadata={"position": str(position.address)},
            )
        if position.balance_wad != 0:
            raise KashValidationError(
                "position is not empty: sell or redeem it before closing",
                field="balance_wad",
                value=str(position.balance_wad),
                constraint="0",
                program_error="PositionNotEmpty",
            )
        return SolanaClosePositionPlan(
            instructions=(
                close_position_instruction(
                    self._ctx,
                    _target(loaded),
                    owner=owner_key,
                    outcome=k,
                    rent_payer=position.rent_payer,
                ),
            ),
            signer=owner_key,
            payer=owner_key,
            rent_recipient=position.rent_payer,
            market=loaded.address,
            market_id=loaded.account.market_id,
            outcome=k,
        )

    # -- simulate / send -----------------------------------------------------

    async def simulate(
        self,
        plan: SolanaPlan | Sequence[Instruction],
        *,
        payer: Pubkey | str,
        compute_unit_limit: int | None = None,
        compute_unit_price_micro_lamports: int | None = None,
    ) -> SolanaSimulationResult:
        """Simulate without signing. A program refusal is a result, not a raised error."""
        return await simulate_instructions(
            self._connection,
            self._ctx.program_id,
            _instructions_of(plan),
            payer=to_pubkey(payer, "payer"),
            budget=SolanaComputeBudget(compute_unit_limit, compute_unit_price_micro_lamports),
            commitment=self._commitment,
        )

    async def send(
        self,
        plan: SolanaPlan | Sequence[Instruction],
        *,
        signer: SolanaSigner,
        fee_payer: SolanaSigner | None = None,
        compute_unit_limit: int | None = None,
        compute_unit_price_micro_lamports: int | None = None,
        skip_preflight: bool = False,
        commitment: Commitment | None = None,
    ) -> SolanaSendResult:
        """Sign (signer, plus ``fee_payer`` when given), send and confirm.

        Preflight is on by default: it is what decodes a refusal before it
        costs fees.
        """
        return await send_instructions(
            self._connection,
            self._ctx.program_id,
            _instructions_of(plan),
            signer=signer,
            fee_payer=fee_payer,
            budget=SolanaComputeBudget(compute_unit_limit, compute_unit_price_micro_lamports),
            skip_preflight=skip_preflight,
            commitment=commitment or self._commitment,
        )

    async def _execute(
        self,
        plan: _P,
        *,
        signer: SolanaSigner,
        fee_payer: SolanaSigner | None,
        compute_unit_limit: int | None,
        compute_unit_price_micro_lamports: int | None,
        skip_preflight: bool,
        commitment: Commitment | None,
    ) -> SolanaTradeResult[_P]:
        sent = await self.send(
            plan,
            signer=signer,
            fee_payer=fee_payer,
            compute_unit_limit=compute_unit_limit,
            compute_unit_price_micro_lamports=compute_unit_price_micro_lamports,
            skip_preflight=skip_preflight,
            commitment=commitment,
        )
        return SolanaTradeResult(signature=sent.signature, slot=sent.slot, plan=plan)

    # -- one-call trades -----------------------------------------------------

    async def buy(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        amount_usdc: int,
        signer: SolanaSigner,
        receiver: Pubkey | str | None = None,
        min_tokens_out_wad: int | None = None,
        max_slippage_bps: int | None = None,
        deadline: int | None = None,
        fee_payer: SolanaSigner | None = None,
        compute_unit_limit: int | None = None,
        compute_unit_price_micro_lamports: int | None = None,
        skip_preflight: bool = False,
        commitment: Commitment | None = None,
    ) -> SolanaTradeResult[SolanaBuyPlan]:
        """Build, sign, send and confirm a buy. The signer is the trader; a fee payer pays rent."""
        plan = await self.build_buy(
            market=market,
            outcome=outcome,
            amount_usdc=amount_usdc,
            trader=signer.pubkey,
            receiver=receiver,
            payer=(fee_payer or signer).pubkey,
            min_tokens_out_wad=min_tokens_out_wad,
            max_slippage_bps=max_slippage_bps,
            deadline=deadline,
        )
        return await self._execute(
            plan,
            signer=signer,
            fee_payer=fee_payer,
            compute_unit_limit=compute_unit_limit,
            compute_unit_price_micro_lamports=compute_unit_price_micro_lamports,
            skip_preflight=skip_preflight,
            commitment=commitment,
        )

    async def sell(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        tokens_in_wad: int,
        signer: SolanaSigner,
        receiver: Pubkey | str | None = None,
        min_amount_out_usdc: int | None = None,
        max_slippage_bps: int | None = None,
        deadline: int | None = None,
        fee_payer: SolanaSigner | None = None,
        compute_unit_limit: int | None = None,
        compute_unit_price_micro_lamports: int | None = None,
        skip_preflight: bool = False,
        commitment: Commitment | None = None,
    ) -> SolanaTradeResult[SolanaSellPlan]:
        """Build, sign, send and confirm a sell. The signer is the position owner."""
        plan = await self.build_sell(
            market=market,
            outcome=outcome,
            tokens_in_wad=tokens_in_wad,
            owner=signer.pubkey,
            receiver=receiver,
            payer=(fee_payer or signer).pubkey,
            min_amount_out_usdc=min_amount_out_usdc,
            max_slippage_bps=max_slippage_bps,
            deadline=deadline,
        )
        return await self._execute(
            plan,
            signer=signer,
            fee_payer=fee_payer,
            compute_unit_limit=compute_unit_limit,
            compute_unit_price_micro_lamports=compute_unit_price_micro_lamports,
            skip_preflight=skip_preflight,
            commitment=commitment,
        )

    async def redeem(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        signer: SolanaSigner,
        amount_wad: int | None = None,
        receiver: Pubkey | str | None = None,
        min_amount_out_usdc: int | None = None,
        max_slippage_bps: int | None = None,
        fee_payer: SolanaSigner | None = None,
        compute_unit_limit: int | None = None,
        compute_unit_price_micro_lamports: int | None = None,
        skip_preflight: bool = False,
        commitment: Commitment | None = None,
    ) -> SolanaTradeResult[SolanaRedeemPlan]:
        """Build, sign, send and confirm a redeem from a resolved market."""
        plan = await self.build_redeem(
            market=market,
            outcome=outcome,
            owner=signer.pubkey,
            amount_wad=amount_wad,
            receiver=receiver,
            payer=(fee_payer or signer).pubkey,
            min_amount_out_usdc=min_amount_out_usdc,
            max_slippage_bps=max_slippage_bps,
        )
        return await self._execute(
            plan,
            signer=signer,
            fee_payer=fee_payer,
            compute_unit_limit=compute_unit_limit,
            compute_unit_price_micro_lamports=compute_unit_price_micro_lamports,
            skip_preflight=skip_preflight,
            commitment=commitment,
        )

    async def redeem_cancelled(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        signer: SolanaSigner,
        amount_wad: int | None = None,
        receiver: Pubkey | str | None = None,
        min_amount_out_usdc: int | None = None,
        max_slippage_bps: int | None = None,
        fee_payer: SolanaSigner | None = None,
        compute_unit_limit: int | None = None,
        compute_unit_price_micro_lamports: int | None = None,
        skip_preflight: bool = False,
        commitment: Commitment | None = None,
    ) -> SolanaTradeResult[SolanaRedeemPlan]:
        """Build, sign, send and confirm a redeem from a cancelled market."""
        plan = await self.build_redeem_cancelled(
            market=market,
            outcome=outcome,
            owner=signer.pubkey,
            amount_wad=amount_wad,
            receiver=receiver,
            payer=(fee_payer or signer).pubkey,
            min_amount_out_usdc=min_amount_out_usdc,
            max_slippage_bps=max_slippage_bps,
        )
        return await self._execute(
            plan,
            signer=signer,
            fee_payer=fee_payer,
            compute_unit_limit=compute_unit_limit,
            compute_unit_price_micro_lamports=compute_unit_price_micro_lamports,
            skip_preflight=skip_preflight,
            commitment=commitment,
        )

    async def open_position(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        owner: Pubkey | str,
        signer: SolanaSigner,
        fee_payer: SolanaSigner | None = None,
        compute_unit_limit: int | None = None,
        compute_unit_price_micro_lamports: int | None = None,
        skip_preflight: bool = False,
        commitment: Commitment | None = None,
    ) -> SolanaTradeResult[SolanaOpenPositionPlan]:
        """Open ``owner``'s position; the fee payer (or the signer) pays the rent."""
        plan = await self.build_open_position(
            market=market, outcome=outcome, owner=owner, payer=(fee_payer or signer).pubkey
        )
        return await self._execute(
            plan,
            signer=signer,
            fee_payer=fee_payer,
            compute_unit_limit=compute_unit_limit,
            compute_unit_price_micro_lamports=compute_unit_price_micro_lamports,
            skip_preflight=skip_preflight,
            commitment=commitment,
        )

    async def close_position(
        self,
        *,
        market: SolanaMarketRef,
        outcome: int,
        signer: SolanaSigner,
        fee_payer: SolanaSigner | None = None,
        compute_unit_limit: int | None = None,
        compute_unit_price_micro_lamports: int | None = None,
        skip_preflight: bool = False,
        commitment: Commitment | None = None,
    ) -> SolanaTradeResult[SolanaClosePositionPlan]:
        """Close the signer's EMPTY position and reclaim its rent."""
        plan = await self.build_close_position(market=market, outcome=outcome, owner=signer.pubkey)
        return await self._execute(
            plan,
            signer=signer,
            fee_payer=fee_payer,
            compute_unit_limit=compute_unit_limit,
            compute_unit_price_micro_lamports=compute_unit_price_micro_lamports,
            skip_preflight=skip_preflight,
            commitment=commitment,
        )


class SolanaClient:
    """A Kash client bound to one Solana deployment. Build with :func:`create_solana_client`."""

    def __init__(
        self,
        *,
        deployment: SolanaDeployment,
        connection: SolanaConnection,
        commitment: Commitment,
        now: Callable[[], int],
        owned_connection: SolanaRpcConnection | None,
    ) -> None:
        self.cluster = deployment.cluster
        self.program_id = deployment.program_id
        self.usdc_mint = deployment.usdc_mint
        self.connection = connection
        self.commitment: Commitment = commitment
        self._owned_connection = owned_connection
        ctx = InstructionContext(program_id=deployment.program_id, usdc_mint=deployment.usdc_mint)
        reader = SolanaReader(
            connection=connection,
            deployment=deployment,
            commitment=commitment,
            now=now,
            ata=lambda owner: usdc_ata(ctx, owner),
        )
        #: PDA derivations for this deployment.
        self.pdas: KashMarketPdas = reader.pdas
        self.protocol = SolanaProtocol(reader)
        self.markets = SolanaMarkets(reader)
        self.account = SolanaAccount(reader)
        self.trades = SolanaTrades(
            reader=reader, ctx=ctx, connection=connection, commitment=commitment, now=now
        )

    async def aclose(self) -> None:
        """Close the RPC connection the SDK created from ``rpc_url``. A caller's is theirs."""
        if self._owned_connection is not None:
            await self._owned_connection.aclose()

    async def __aenter__(self) -> SolanaClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


def create_solana_client(
    *,
    cluster: SolanaCluster | CustomSolanaDeployment = DEFAULT_SOLANA_CLUSTER,
    connection: SolanaConnection | None = None,
    rpc_url: str | None = None,
    commitment: Commitment = "confirmed",
    now: Callable[[], int] | None = None,
) -> SolanaClient:
    """Create a Solana client.

    Parameters
    ----------
    cluster:
        ``"mainnet-beta"`` (default), ``"devnet"``, or a
        :class:`CustomSolanaDeployment` for an unlisted deployment.
    connection:
        Any :class:`SolanaConnection` (e.g. a :class:`SolanaRpcConnection`
        you configured yourself). Exactly one of ``connection`` / ``rpc_url``.
    rpc_url:
        An RPC endpoint (``https://…``; ``http://`` only for localhost).
        The connection it creates is closed by :meth:`SolanaClient.aclose`.
    commitment:
        For reads, preflight and confirmation. Default ``confirmed``.
    now:
        Unix-seconds clock, injectable for tests. Default: the system clock.

    Raises
    ------
    KashConfigError
        ``INVALID_CONFIG`` for a malformed config, ``UNSUPPORTED_CHAIN`` for
        a cluster with no Kash deployment.
    """
    try:
        _ConfigSchema(rpc_url=rpc_url, commitment=commitment, has_connection=connection is not None)
    except ValidationError as exc:
        raise KashConfigError(
            f"Invalid Solana client config: {exc}",
            code=ErrorCode.INVALID_CONFIG,
            context={"errors": exc.errors(include_url=False)},
            cause=exc,
        ) from exc
    if (connection is None) == (rpc_url is None):
        raise KashConfigError(
            "Invalid Solana client config: pass exactly one of `connection` or `rpc_url`",
            code=ErrorCode.INVALID_CONFIG,
            context={"field": "connection"},
        )
    deployment = get_solana_deployment(cluster)
    owned: SolanaRpcConnection | None = None
    rpc: SolanaConnection
    if rpc_url is not None:
        owned = SolanaRpcConnection(rpc_url)
        rpc = owned
    else:
        if connection is None or not isinstance(connection, SolanaConnection):
            raise KashConfigError(
                "connection must implement SolanaConnection",
                code=ErrorCode.INVALID_CONFIG,
                context={"field": "connection", "type": type(connection).__name__},
            )
        rpc = connection
    return SolanaClient(
        deployment=deployment,
        connection=rpc,
        commitment=commitment,
        now=now or (lambda: int(time.time())),
        owned_connection=owned,
    )


__all__ = [
    "DEFAULT_TRADE_DEADLINE_SECONDS",
    "SolanaAccount",
    "SolanaClient",
    "SolanaMarkets",
    "SolanaProtocol",
    "SolanaTradeResult",
    "SolanaTrades",
    "create_solana_client",
]
