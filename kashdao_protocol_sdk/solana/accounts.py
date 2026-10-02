"""Account decoding and reads for the Solana client.

Mirrors the TS SDK's ``accounts.ts`` plus the vendored
``protocol-adapter-solana`` ``zero-copy.ts``, ``state.ts`` and
``curve-market.ts``.

``Config``, ``Market`` and ``Position`` are ``#[account(zero_copy)]``
(bytemuck, ``repr(C)``), so the IDL renders ``u128`` as ``[u8;16]``, the
u256 ``A`` as ``[u8;32]`` and EVERY pubkey as ``[u8;32]``; this module
reassembles them into ``int`` and :class:`Pubkey`. ``Template`` is an
ordinary account and decodes with native types.

Two rules from the backend carry over unchanged:

- A ``Position`` (or an SPL token account) that does not exist is a balance
  of ZERO: the program never creates either until there is something to
  hold, so absence is the chain's answer, not a failed read.
- A ``Market``, ``Config`` or ``Template`` that does not exist is an error
  (``ACCOUNT_NOT_FOUND``). An RPC that FAILS is a retryable
  :class:`KashChainError`, never a zero.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Final, TypeVar

from solders.pubkey import Pubkey

from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import (
    KashChainError,
    KashProtocolError,
    KashValidationError,
)
from kashdao_protocol_sdk.solana import curve
from kashdao_protocol_sdk.solana.clusters import SolanaDeployment
from kashdao_protocol_sdk.solana.connection import AccountInfo, Commitment, SolanaConnection
from kashdao_protocol_sdk.solana.idl import decode_account
from kashdao_protocol_sdk.solana.pda import KashMarketPdas, kash_market_pdas
from kashdao_protocol_sdk.solana.types import (
    SolanaMarket,
    SolanaMarketRef,
    SolanaMarketStatus,
    SolanaPosition,
    SolanaProgramConfig,
    SolanaTemplate,
)

#: SPL Token program — owner of every collateral and USDC token account.
TOKEN_PROGRAM_ID: Final = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

#: Solana's ``getMultipleAccounts`` refuses more keys than this in one request.
GET_MULTIPLE_ACCOUNTS_LIMIT: Final = 100

#: ``kash_market``'s stored ``Market.state`` values (``programs/kash_market/src/state.rs``).
MARKET_STATE_ACTIVE: Final = 1
MARKET_STATE_RESOLVED: Final = 2
MARKET_STATE_CANCELLED: Final = 3

#: The program's outcome bound: ``Market`` lays out 100-slot supply arrays.
MAX_OUTCOMES: Final = 100

_SPL_AMOUNT_OFFSET: Final = 64


def _le(data: bytes) -> int:
    return int.from_bytes(data, "little")


@dataclass(frozen=True, slots=True)
class ConfigAccount:
    admin: Pubkey
    pending_admin: Pubkey
    guardian: Pubkey
    revenue_token_account: Pubkey
    resolution_authority: Pubkey
    default_protocol_fee_bps: int
    template_count: int
    paused: bool
    bump: int


@dataclass(frozen=True, slots=True)
class TemplateAccount:
    id: int
    tier: int
    max_outcomes: int
    available_max_outcomes: int
    max_winning_outcomes: int
    offered: bool
    bump: int
    freeze_lead_seconds: int
    cancel_grace_seconds: int
    sell_fee_bps: int
    min_seed_usdc: int
    category_id: int


@dataclass(frozen=True, slots=True)
class PositionAccount:
    market: Pubkey
    owner: Pubkey
    rent_payer: Pubkey
    balance: int
    outcome: int
    bump: int


@dataclass(frozen=True, slots=True)
class MarketAccount:
    lifecycle_version: int
    bump: int
    state: int
    halted: bool
    num_outcomes: int
    max_winning_outcomes: int
    winning_count: int
    winning_outcome: int
    template_id: int
    sell_fee_bps: int
    protocol_fee_bps: int
    category_id: int
    cancel_grace_seconds: int
    market_id: int
    creator: Pubkey
    resolution_authority: Pubkey
    collateral_account: Pubkey
    created_at: int
    freeze_time: int
    resolve_time: int
    freeze_observed_at: int
    cancelled_at: int
    fees_owed_usdc: int
    reserve_wad: int
    a: int
    winning_mask: int
    sum_winning_supply: int
    cancel_reserve_wad: int
    cancel_a: int
    genesis_leg_wad: int
    genesis_unharvested: int
    cumulative_volume_wad: int
    metadata_hash: bytes
    evidence_hash: bytes
    rent_payer: Pubkey
    #: Only the first ``num_outcomes`` entries are live; the array is always 100 long on-chain.
    supply_wad: tuple[int, ...]
    cancel_supply_wad: tuple[int, ...]


def decode_config_account(data: bytes) -> ConfigAccount:
    r = decode_account("Config", data)
    return ConfigAccount(
        admin=Pubkey.from_bytes(r["admin"]),
        pending_admin=Pubkey.from_bytes(r["pending_admin"]),
        guardian=Pubkey.from_bytes(r["guardian"]),
        revenue_token_account=Pubkey.from_bytes(r["revenue_token_account"]),
        resolution_authority=Pubkey.from_bytes(r["resolution_authority"]),
        default_protocol_fee_bps=r["default_protocol_fee_bps"],
        template_count=r["template_count"],
        paused=r["paused"] != 0,
        bump=r["bump"],
    )


def decode_template_account(data: bytes) -> TemplateAccount:
    r = decode_account("Template", data)
    return TemplateAccount(
        id=r["id"],
        tier=r["tier"],
        max_outcomes=r["max_outcomes"],
        available_max_outcomes=r["available_max_outcomes"],
        max_winning_outcomes=r["max_winning_outcomes"],
        offered=bool(r["offered"]),
        bump=r["bump"],
        freeze_lead_seconds=r["freeze_lead_seconds"],
        cancel_grace_seconds=r["cancel_grace_seconds"],
        sell_fee_bps=r["sell_fee_bps"],
        min_seed_usdc=r["min_seed_usdc"],
        category_id=r["category_id"],
    )


def decode_position_account(data: bytes) -> PositionAccount:
    r = decode_account("Position", data)
    return PositionAccount(
        market=Pubkey.from_bytes(r["market"]),
        owner=Pubkey.from_bytes(r["owner"]),
        rent_payer=Pubkey.from_bytes(r["rent_payer"]),
        balance=_le(r["balance"]),
        outcome=r["outcome"],
        bump=r["bump"],
    )


def decode_market_account(data: bytes) -> MarketAccount:
    r = decode_account("Market", data)
    return MarketAccount(
        lifecycle_version=r["lifecycle_version"],
        bump=r["bump"],
        state=r["state"],
        halted=r["halted"] != 0,
        num_outcomes=r["num_outcomes"],
        max_winning_outcomes=r["max_winning_outcomes"],
        winning_count=r["winning_count"],
        winning_outcome=r["winning_outcome"],
        template_id=r["template_id"],
        sell_fee_bps=r["sell_fee_bps"],
        protocol_fee_bps=r["protocol_fee_bps"],
        category_id=r["category_id"],
        cancel_grace_seconds=r["cancel_grace_seconds"],
        market_id=r["market_id"],
        creator=Pubkey.from_bytes(r["creator"]),
        resolution_authority=Pubkey.from_bytes(r["resolution_authority"]),
        collateral_account=Pubkey.from_bytes(r["collateral_account"]),
        created_at=r["created_at"],
        freeze_time=r["freeze_time"],
        resolve_time=r["resolve_time"],
        freeze_observed_at=r["freeze_observed_at"],
        cancelled_at=r["cancelled_at"],
        fees_owed_usdc=r["fees_owed_usdc"],
        reserve_wad=_le(r["reserve_wad"]),
        a=_le(r["A"]),
        winning_mask=_le(r["winning_mask"]),
        sum_winning_supply=_le(r["sum_winning_supply"]),
        cancel_reserve_wad=_le(r["cancel_reserve_wad"]),
        cancel_a=_le(r["cancel_A"]),
        genesis_leg_wad=_le(r["genesis_leg_wad"]),
        genesis_unharvested=_le(r["genesis_unharvested"]),
        cumulative_volume_wad=_le(r["cumulative_volume_wad"]),
        metadata_hash=bytes(r["metadata_hash"]),
        evidence_hash=bytes(r["evidence_hash"]),
        rent_payer=Pubkey.from_bytes(r["rent_payer"]),
        supply_wad=tuple(_le(b) for b in r["supply_wad"]),
        cancel_supply_wad=tuple(_le(b) for b in r["cancel_supply_wad"]),
    )


@dataclass(frozen=True, slots=True)
class TokenAccount:
    """The fields of an SPL token account the client reads."""

    mint: Pubkey
    owner: Pubkey
    amount: int


def decode_token_account(data: bytes) -> TokenAccount:
    """An SPL token account's leading fields: mint (32), owner (32), amount (u64 LE)."""
    end = _SPL_AMOUNT_OFFSET + 8
    if len(data) < end:
        raise KashValidationError(
            "token account data is too short", field="data", value=len(data), constraint=f">= {end}"
        )
    return TokenAccount(
        mint=Pubkey.from_bytes(data[0:32]),
        owner=Pubkey.from_bytes(data[32:64]),
        amount=_le(data[_SPL_AMOUNT_OFFSET:end]),
    )


# ---------------------------------------------------------------------------
# Market state projections
# ---------------------------------------------------------------------------


def _unknown_state(account: MarketAccount) -> KashValidationError:
    return KashValidationError(
        "Unknown kash_market state",
        field="state",
        value=account.state,
        constraint="one of 1, 2, 3",
        metadata={"market_id": str(account.market_id)},
    )


def market_status(account: MarketAccount, now_seconds: int) -> SolanaMarketStatus:
    """Lifecycle status, with ``frozen`` derived from the clock as the program derives it."""
    if account.state == MARKET_STATE_ACTIVE:
        return "frozen" if now_seconds >= account.freeze_time else "active"
    if account.state == MARKET_STATE_RESOLVED:
        return "resolved"
    if account.state == MARKET_STATE_CANCELLED:
        return "cancelled"
    raise _unknown_state(account)


def _curve_status(account: MarketAccount) -> curve.MarketStatus:
    if account.state == MARKET_STATE_ACTIVE:
        return "active"
    if account.state == MARKET_STATE_RESOLVED:
        return "resolved"
    if account.state == MARKET_STATE_CANCELLED:
        return "cancelled"
    raise _unknown_state(account)


def _require_outcome_count(account: MarketAccount) -> None:
    n = account.num_outcomes
    if n < 2 or n > MAX_OUTCOMES:
        raise KashValidationError(
            "num_outcomes is not a value this program can have written",
            field="num_outcomes",
            value=n,
            constraint=f"an integer in 2..{MAX_OUTCOMES}",
            metadata={"market_id": str(account.market_id)},
        )
    if len(account.supply_wad) < n:
        raise KashValidationError(
            "supply_wad is shorter than num_outcomes",
            field="num_outcomes",
            value=n,
            metadata={"market_id": str(account.market_id)},
        )
    if account.state == MARKET_STATE_CANCELLED and len(account.cancel_supply_wad) < n:
        raise KashValidationError(
            "cancel_supply_wad is shorter than num_outcomes",
            field="num_outcomes",
            value=n,
            metadata={"market_id": str(account.market_id)},
        )


def to_curve_market(account: MarketAccount, collateral_usdc: int) -> curve.CurveMarket:
    """A decoded ``Market`` (plus its collateral balance) as the curve's state.

    The collateral balance matters: ``sell`` refuses a payout above
    ``balance - fees`` exactly as the program does, so a quote computed
    without it could promise a sell the chain would reject. The market's own
    ``sell_fee_bps`` is carried through, because that is the fee the program
    charges.
    """
    n = account.num_outcomes
    _require_outcome_count(account)
    status = _curve_status(account)
    return curve.CurveMarket(
        supplies=account.supply_wad[:n],
        reserve=account.reserve_wad,
        aggregate=account.a,
        genesis_leg_wad=account.genesis_leg_wad,
        genesis_unharvested=account.genesis_unharvested,
        balance_usdc=collateral_usdc,
        fees_usdc=account.fees_owed_usdc,
        volume_wad=account.cumulative_volume_wad,
        fee_bps=account.protocol_fee_bps,
        sell_fee_bps=account.sell_fee_bps,
        status=status,
        snapshot=(
            curve.Snapshot(
                reserve=account.cancel_reserve_wad,
                aggregate=account.cancel_a,
                supplies=account.cancel_supply_wad[:n],
            )
            if status == "cancelled"
            else None
        ),
        winning_outcome=account.winning_outcome if status == "resolved" else None,
        winning_supply=account.sum_winning_supply,
    )


def marginal_prices(market: curve.CurveMarket) -> tuple[int, ...]:
    """Every outcome's E4 marginal price for a curve state."""
    n = len(market.supplies)
    return tuple(
        curve.marginal_price(market.reserve, market.aggregate, curve.weight(n, i), s)
        for i, s in enumerate(market.supplies)
    )


def _probabilities(prices: tuple[int, ...]) -> tuple[int, ...]:
    total = sum(prices)
    if total == 0:
        return tuple(0 for _ in prices)
    return tuple(p * curve.WAD // total for p in prices)


def require_outcome(outcome: int, num_outcomes: int) -> int:
    """Require an integer outcome index inside ``0..num_outcomes-1``."""
    if isinstance(outcome, bool) or not isinstance(outcome, int) or not 0 <= outcome < num_outcomes:
        raise KashValidationError(
            "outcome is not an outcome of this market",
            field="outcome",
            value=outcome,
            constraint=f"an integer in 0..{num_outcomes - 1}",
        )
    return outcome


def to_pubkey(value: Pubkey | str, field: str) -> Pubkey:
    """Parse an address argument."""
    if isinstance(value, Pubkey):
        return value
    try:
        return Pubkey.from_string(value)
    except (ValueError, TypeError) as cause:
        raise KashValidationError(
            f"{field} is not a Solana address", field=field, value=value, cause=cause
        ) from cause


def to_solana_market(
    address: Pubkey, account: MarketAccount, collateral_usdc: int, now_seconds: int
) -> SolanaMarket:
    """Shape a decoded market for callers. Pure."""
    state = to_curve_market(account, collateral_usdc)
    prices = marginal_prices(state)
    return SolanaMarket(
        address=address,
        market_id=account.market_id,
        template_id=account.template_id,
        num_outcomes=account.num_outcomes,
        status=market_status(account, now_seconds),
        halted=account.halted,
        creator=account.creator,
        resolution_authority=account.resolution_authority,
        collateral_account=account.collateral_account,
        collateral_usdc=collateral_usdc,
        created_at=account.created_at,
        freeze_time=account.freeze_time,
        resolve_time=account.resolve_time,
        cancelled_at=account.cancelled_at,
        protocol_fee_bps=account.protocol_fee_bps,
        sell_fee_bps=account.sell_fee_bps,
        fees_owed_usdc=account.fees_owed_usdc,
        reserve_wad=account.reserve_wad,
        supply_wad=state.supplies,
        prices_wad=prices,
        probabilities_wad=_probabilities(prices),
        winning_outcome=state.winning_outcome,
        cumulative_volume_wad=account.cumulative_volume_wad,
        metadata_hash=account.metadata_hash,
    )


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoadedMarket:
    """A decoded market with what a quote or a write needs alongside it."""

    address: Pubkey
    account: MarketAccount
    collateral_usdc: int


_T = TypeVar("_T")


class SolanaReader:
    """Owner-checked, chunked account reads for one deployment."""

    def __init__(
        self,
        *,
        connection: SolanaConnection,
        deployment: SolanaDeployment,
        commitment: Commitment,
        now: Callable[[], int],
        ata: Callable[[Pubkey], Pubkey],
    ) -> None:
        self._connection = connection
        self._deployment = deployment
        self._commitment = commitment
        self._now = now
        self._ata = ata
        self.pdas: KashMarketPdas = kash_market_pdas(deployment.program_id)

    @property
    def _program_id(self) -> Pubkey:
        return self._deployment.program_id

    async def _fetch_many(self, addresses: Sequence[Pubkey], what: str) -> list[AccountInfo | None]:
        out: list[AccountInfo | None] = []
        for i in range(0, len(addresses), GET_MULTIPLE_ACCOUNTS_LIMIT):
            chunk = list(addresses[i : i + GET_MULTIPLE_ACCOUNTS_LIMIT])
            try:
                infos = await self._connection.get_multiple_accounts(chunk, self._commitment)
            except Exception as cause:
                raise KashChainError(
                    f"Failed to read {what}",
                    code=ErrorCode.ACCOUNT_READ_FAILED,
                    context={"what": what, "addresses": [str(a) for a in chunk]},
                    cause=cause,
                    is_retryable=True,
                ) from cause
            if len(infos) != len(chunk):
                raise KashChainError(
                    f"RPC returned {len(infos)} accounts for {len(chunk)} requested",
                    code=ErrorCode.ACCOUNT_READ_FAILED,
                    context={"what": what},
                    is_retryable=True,
                )
            out.extend(infos)
        return out

    def _decode_owned(
        self,
        kind: str,
        address: Pubkey,
        info: AccountInfo,
        owner: Pubkey,
        decode: Callable[[bytes], _T],
    ) -> _T:
        if info.owner != owner:
            raise KashChainError(
                f"{kind} {address} is not owned by {owner}",
                code=ErrorCode.ACCOUNT_OWNER_MISMATCH,
                context={"kind": kind, "address": str(address), "owner": str(info.owner)},
            )
        try:
            return decode(info.data)
        except KashProtocolError:
            raise
        except (ValueError, IndexError) as cause:
            raise KashValidationError(
                f"{address} does not decode as a {kind}",
                field="address",
                value=str(address),
                constraint=f"a kash_market {kind} account",
                cause=cause,
            ) from cause

    def _not_found(self, kind: str, address: Pubkey) -> KashChainError:
        return KashChainError(
            f"{kind} {address} does not exist",
            code=ErrorCode.ACCOUNT_NOT_FOUND,
            context={"kind": kind, "address": str(address), "cluster": self._deployment.cluster},
        )

    async def _require_one(self, kind: str, address: Pubkey, decode: Callable[[bytes], _T]) -> _T:
        (info,) = await self._fetch_many([address], kind)
        if info is None:
            raise self._not_found(kind, address)
        return self._decode_owned(kind, address, info, self._program_id, decode)

    def _spl_amount(self, address: Pubkey, info: AccountInfo | None) -> int:
        """A user's token balance: a missing account is a measured zero."""
        if info is None:
            return 0
        return self._decode_owned(
            "token account", address, info, TOKEN_PROGRAM_ID, decode_token_account
        ).amount

    def _collateral_usdc(
        self, account: MarketAccount, address: Pubkey, info: AccountInfo | None
    ) -> int:
        """The market's collateral balance, verified rather than assumed.

        It is an input to every quote, so a missing or foreign account is an
        error, never a zero: a quote run on a zero balance would refuse (or
        price) a sell the program treats differently. Only a user's own USDC
        balance, which decides nothing here, reads absence as ``0``.
        """
        if account.collateral_account != address:
            raise KashValidationError(
                "market names a collateral account other than its PDA",
                field="collateral_account",
                value=str(account.collateral_account),
                metadata={"expected": str(address), "market_id": str(account.market_id)},
            )
        if info is None:
            raise self._not_found("market collateral", address)
        token = self._decode_owned(
            "token account", address, info, TOKEN_PROGRAM_ID, decode_token_account
        )
        if token.mint != self._deployment.usdc_mint:
            raise KashValidationError(
                "market collateral is not the cluster's USDC",
                field="collateral_account",
                value=str(address),
                constraint=f"a token account of mint {self._deployment.usdc_mint}",
                metadata={"mint": str(token.mint)},
            )
        return token.amount

    async def config(self) -> SolanaProgramConfig:
        address = self.pdas.config().address
        c = await self._require_one("config", address, decode_config_account)
        return SolanaProgramConfig(
            address=address,
            admin=c.admin,
            guardian=c.guardian,
            resolution_authority=c.resolution_authority,
            revenue_token_account=c.revenue_token_account,
            default_protocol_fee_bps=c.default_protocol_fee_bps,
            template_count=c.template_count,
            paused=c.paused,
        )

    @staticmethod
    def _to_template(address: Pubkey, data: bytes) -> SolanaTemplate:
        t = decode_template_account(data)
        return SolanaTemplate(
            address=address,
            id=t.id,
            tier=t.tier,
            max_outcomes=t.max_outcomes,
            available_max_outcomes=t.available_max_outcomes,
            max_winning_outcomes=t.max_winning_outcomes,
            offered=t.offered,
            freeze_lead_seconds=t.freeze_lead_seconds,
            cancel_grace_seconds=t.cancel_grace_seconds,
            sell_fee_bps=t.sell_fee_bps,
            min_seed_usdc=t.min_seed_usdc,
            category_id=t.category_id,
        )

    async def template(self, template_id: int) -> SolanaTemplate:
        address = self.pdas.template(template_id).address
        return await self._require_one("template", address, partial(self._to_template, address))

    async def templates(self) -> list[SolanaTemplate]:
        """Every template; ids are dense from 0 below ``Config.template_count``."""
        config_address = self.pdas.config().address
        config = await self._require_one("config", config_address, decode_config_account)
        addresses = [self.pdas.template(i).address for i in range(config.template_count)]
        infos = await self._fetch_many(addresses, "templates")
        out: list[SolanaTemplate] = []
        for template_id, (address, info) in enumerate(zip(addresses, infos, strict=True)):
            if info is None:
                raise self._not_found(f"template {template_id}", address)
            out.append(
                self._decode_owned(
                    "template",
                    address,
                    info,
                    self._program_id,
                    partial(self._to_template, address),
                )
            )
        return out

    async def load_market(self, ref: SolanaMarketRef) -> LoadedMarket:
        if isinstance(ref, int) and not isinstance(ref, bool):
            # Both addresses derive from the id: one round trip.
            address = self.pdas.market(ref).address
            collateral = self.pdas.collateral(ref).address
            market_info, collateral_info = await self._fetch_many([address, collateral], "market")
            if market_info is None:
                raise self._not_found("market", address)
            account = self._decode_owned(
                "market", address, market_info, self._program_id, decode_market_account
            )
            return LoadedMarket(
                address=address,
                account=account,
                collateral_usdc=self._collateral_usdc(account, collateral, collateral_info),
            )
        if not isinstance(ref, (Pubkey, str)):
            raise KashValidationError(
                "market must be a Pubkey, base58 string or int market id",
                field="market",
                value=type(ref).__name__,
            )
        address = to_pubkey(ref, "market")
        account = await self._require_one("market", address, decode_market_account)
        # An address the caller handed over must be the PDA its own id derives:
        # anything else is a foreign account that merely decodes as a Market.
        if self.pdas.market(account.market_id).address != address:
            raise KashValidationError(
                "market address is not the PDA of the market id it holds",
                field="market",
                value=str(address),
                metadata={"market_id": str(account.market_id)},
            )
        collateral = self.pdas.collateral(account.market_id).address
        (collateral_info,) = await self._fetch_many([collateral], "market collateral")
        return LoadedMarket(
            address=address,
            account=account,
            collateral_usdc=self._collateral_usdc(account, collateral, collateral_info),
        )

    async def market(self, ref: SolanaMarketRef) -> SolanaMarket:
        loaded = await self.load_market(ref)
        return to_solana_market(loaded.address, loaded.account, loaded.collateral_usdc, self._now())

    def _to_position(
        self,
        address: Pubkey,
        market: Pubkey,
        owner: Pubkey,
        outcome: int,
        info: AccountInfo | None,
    ) -> SolanaPosition:
        if info is None:
            return SolanaPosition(
                address=address,
                market=market,
                owner=owner,
                outcome=outcome,
                exists=False,
                balance_wad=0,
                rent_payer=None,
            )
        decoded = self._decode_owned(
            "position", address, info, self._program_id, decode_position_account
        )
        return SolanaPosition(
            address=address,
            market=market,
            owner=owner,
            outcome=outcome,
            exists=True,
            balance_wad=decoded.balance,
            rent_payer=decoded.rent_payer,
        )

    async def position(self, market: Pubkey, owner: Pubkey, outcome: int) -> SolanaPosition:
        address = self.pdas.position(market, owner, outcome).address
        (info,) = await self._fetch_many([address], "position")
        return self._to_position(address, market, owner, outcome, info)

    async def positions(self, ref: SolanaMarketRef, owner: Pubkey) -> list[SolanaPosition]:
        loaded = await self.load_market(ref)
        addresses = [
            self.pdas.position(loaded.address, owner, k).address
            for k in range(loaded.account.num_outcomes)
        ]
        infos = await self._fetch_many(addresses, "positions")
        return [
            self._to_position(address, loaded.address, owner, k, info)
            for k, (address, info) in enumerate(zip(addresses, infos, strict=True))
        ]

    async def usdc_balance(self, owner: Pubkey) -> int:
        ata = self._ata(owner)
        (info,) = await self._fetch_many([ata], "USDC token account")
        return self._spl_amount(ata, info)


__all__ = [
    "MARKET_STATE_ACTIVE",
    "MARKET_STATE_CANCELLED",
    "MARKET_STATE_RESOLVED",
    "TOKEN_PROGRAM_ID",
    "ConfigAccount",
    "LoadedMarket",
    "MarketAccount",
    "PositionAccount",
    "SolanaReader",
    "TemplateAccount",
    "TokenAccount",
    "decode_config_account",
    "decode_market_account",
    "decode_position_account",
    "decode_template_account",
    "decode_token_account",
    "marginal_prices",
    "market_status",
    "require_outcome",
    "to_curve_market",
    "to_pubkey",
    "to_solana_market",
]
