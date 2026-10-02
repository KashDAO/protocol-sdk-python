"""Public types of the Solana client.

Mirrors ``src/solana/types.ts``. Amounts follow one rule, the program's
own: USDC is an ``int`` of atomic units (6 decimals, ``1_000_000`` = 1 USDC)
and outcome-token balances are an ``int`` WAD (18 decimals, ``10**18`` = 1
token). Positions are NOT SPL tokens on Solana — the program keeps each one
as a ``u128`` WAD balance in a ``Position`` account (one per market, owner
and outcome) — so 18 decimals is the native scale there too and
:func:`~kashdao_protocol_sdk.format_tokens` applies unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from solders.instruction import Instruction
from solders.pubkey import Pubkey

#: A market, named any of three ways: its ``Market`` account address (a
#: ``Pubkey`` or base58 string) or its numeric u64 market id.
SolanaMarketRef = Pubkey | str | int

#: Lifecycle of a Solana market. ``frozen`` is derived, as on chain: the
#: stored state is still ACTIVE but the clock has passed ``freeze_time``, so
#: the program refuses trades while it awaits resolution.
SolanaMarketStatus = Literal["active", "frozen", "resolved", "cancelled"]


@dataclass(frozen=True, slots=True)
class SolanaProgramConfig:
    """The singleton program ``Config``."""

    address: Pubkey
    admin: Pubkey
    guardian: Pubkey
    resolution_authority: Pubkey
    revenue_token_account: Pubkey
    #: Protocol fee charged on buys and sells, in basis points (mainnet: 100).
    default_protocol_fee_bps: int
    template_count: int
    #: When true the program refuses every trade.
    paused: bool


@dataclass(frozen=True, slots=True)
class SolanaTemplate:
    """A market-creation template."""

    address: Pubkey
    id: int
    tier: int
    max_outcomes: int
    #: The ceiling an admin may lower below ``max_outcomes``; what creation actually enforces.
    available_max_outcomes: int
    max_winning_outcomes: int
    offered: bool
    freeze_lead_seconds: int
    cancel_grace_seconds: int
    sell_fee_bps: int
    #: Minimum seed, atomic USDC.
    min_seed_usdc: int
    category_id: int


@dataclass(frozen=True, slots=True)
class SolanaMarket:
    """A decoded ``Market`` account plus its collateral balance and derived prices."""

    address: Pubkey
    market_id: int
    template_id: int
    num_outcomes: int
    status: SolanaMarketStatus
    #: Set by the guardian: trading is stopped regardless of status.
    halted: bool
    creator: Pubkey
    resolution_authority: Pubkey
    collateral_account: Pubkey
    #: USDC held by the market's collateral account, atomic.
    collateral_usdc: int
    #: Unix seconds.
    created_at: int
    freeze_time: int
    resolve_time: int
    #: ``0`` unless cancelled.
    cancelled_at: int
    protocol_fee_bps: int
    #: The AMM sell fee this market charges (retained in the reserve), basis points.
    sell_fee_bps: int
    #: Protocol fees accrued and not yet collected, atomic USDC.
    fees_owed_usdc: int
    reserve_wad: int
    #: Outstanding supply per outcome, WAD. Length ``num_outcomes``.
    supply_wad: tuple[int, ...]
    #: Marginal price of one more token of each outcome, in WAD USDC
    #: (``10**18`` = 1 USDC per token) — the program's E4 price.
    prices_wad: tuple[int, ...]
    #: ``prices_wad`` normalised to sum to ``10**18`` — the implied probabilities.
    probabilities_wad: tuple[int, ...]
    #: The winning outcome once resolved, otherwise ``None``.
    winning_outcome: int | None
    cumulative_volume_wad: int
    #: 32-byte hash of the off-chain market metadata.
    metadata_hash: bytes


@dataclass(frozen=True, slots=True)
class SolanaPosition:
    """One owner's balance in one outcome. A missing account is a balance of zero."""

    address: Pubkey
    market: Pubkey
    owner: Pubkey
    outcome: int
    #: Whether the ``Position`` account exists. ``buy`` opens it when it does not.
    exists: bool
    balance_wad: int
    #: Who funded the account's rent and is refunded by ``close_position``; ``None`` when absent.
    rent_payer: Pubkey | None


@dataclass(frozen=True, slots=True)
class SolanaBuyQuote:
    """The program's exact quote for spending ``amount_in_usdc`` on one outcome."""

    outcome: int
    amount_in_usdc: int
    #: Protocol fee taken from the input, atomic USDC.
    protocol_fee_usdc: int
    tokens_out_wad: int
    #: Gross USDC paid per token received, in WAD USDC.
    average_price_wad: int
    #: Every outcome's marginal price after the trade, WAD USDC.
    prices_after_wad: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SolanaSellQuote:
    """The program's exact quote for selling ``tokens_in_wad`` of one outcome."""

    outcome: int
    tokens_in_wad: int
    #: USDC the seller receives, after the market's AMM sell fee and the protocol fee.
    amount_out_usdc: int
    #: Protocol fee withheld, atomic USDC.
    protocol_fee_usdc: int
    prices_after_wad: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SolanaRedeemQuote:
    """The payout for redeeming ``amount_wad`` of an outcome of a resolved or cancelled market."""

    outcome: int
    amount_wad: int
    #: Zero for a losing outcome of a resolved market.
    payout_usdc: int


@dataclass(frozen=True, slots=True)
class SolanaBuyPlan:
    """A buy, ready to send, with the slippage floor actually encoded."""

    instructions: tuple[Instruction, ...]
    #: The signer the program requires (the trader).
    signer: Pubkey
    #: Account that pays rent for any account the plan creates.
    payer: Pubkey
    market: Pubkey
    market_id: int
    outcome: int
    min_tokens_out_wad: int
    #: Present when the floor was derived from a quote (``max_slippage_bps``).
    quote: SolanaBuyQuote | None
    #: Whether the plan opens the receiver's position first.
    opens_position: bool
    kind: Literal["buy"] = "buy"


@dataclass(frozen=True, slots=True)
class SolanaSellPlan:
    """A sell, ready to send, with the slippage floor actually encoded."""

    instructions: tuple[Instruction, ...]
    signer: Pubkey
    payer: Pubkey
    market: Pubkey
    market_id: int
    outcome: int
    min_amount_out_usdc: int
    quote: SolanaSellQuote | None
    kind: Literal["sell"] = "sell"


@dataclass(frozen=True, slots=True)
class SolanaRedeemPlan:
    """A redeem (resolved market) or redeem-cancelled (cancelled market) plan."""

    kind: Literal["redeem", "redeem_cancelled"]
    instructions: tuple[Instruction, ...]
    signer: Pubkey
    payer: Pubkey
    market: Pubkey
    market_id: int
    outcome: int
    amount_wad: int
    min_amount_out_usdc: int
    quote: SolanaRedeemQuote | None


@dataclass(frozen=True, slots=True)
class SolanaOpenPositionPlan:
    """Open an empty position. ``payer`` funds its rent and signs."""

    instructions: tuple[Instruction, ...]
    signer: Pubkey
    payer: Pubkey
    market: Pubkey
    market_id: int
    outcome: int
    kind: Literal["open_position"] = "open_position"


@dataclass(frozen=True, slots=True)
class SolanaClosePositionPlan:
    """Close an empty position.

    Nothing is created, so ``payer`` is simply the owner; the reclaimed rent
    goes to ``rent_recipient`` — whoever funded the account (the program
    refunds ``Position.rent_payer``, not the closer).
    """

    instructions: tuple[Instruction, ...]
    signer: Pubkey
    payer: Pubkey
    rent_recipient: Pubkey
    market: Pubkey
    market_id: int
    outcome: int
    kind: Literal["close_position"] = "close_position"


SolanaPlan = (
    SolanaBuyPlan
    | SolanaSellPlan
    | SolanaRedeemPlan
    | SolanaOpenPositionPlan
    | SolanaClosePositionPlan
)


__all__ = [
    "SolanaBuyPlan",
    "SolanaBuyQuote",
    "SolanaMarket",
    "SolanaMarketRef",
    "SolanaMarketStatus",
    "SolanaPlan",
    "SolanaPosition",
    "SolanaClosePositionPlan",
    "SolanaOpenPositionPlan",
    "SolanaProgramConfig",
    "SolanaRedeemPlan",
    "SolanaRedeemQuote",
    "SolanaSellPlan",
    "SolanaSellQuote",
    "SolanaTemplate",
]
