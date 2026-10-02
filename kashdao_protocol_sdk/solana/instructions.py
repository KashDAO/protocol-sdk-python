"""Instruction builders for the six trader-facing ``kash_market`` instructions.

Mirrors ``src/solana/instructions.ts``. Every instruction goes through
:func:`~kashdao_protocol_sdk.solana.idl.build_program_instruction`, which
derives the account list, order and signer/writable flags from the IDL and
resolves every const/arg-seeded PDA (``config``, ``market``,
``collateral``), the token program and Anchor's
``event_authority``/``program``. A builder here only names the accounts
that depend on WHO signs: the trader, their USDC ATA and the position —
exactly the set the backend's ``createSolanaWrites`` passes.

Builders are pure: decoded chain state in, solders :class:`Instruction` out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solders.token.associated import get_associated_token_address

from kashdao_protocol_sdk.solana.accounts import TOKEN_PROGRAM_ID
from kashdao_protocol_sdk.solana.idl import build_program_instruction
from kashdao_protocol_sdk.solana.pda import kash_market_pdas

#: The SPL Associated Token Account program.
ASSOCIATED_TOKEN_PROGRAM_ID: Final = Pubkey.from_string(
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
)

#: ``CreateIdempotent`` — the ATA program's instruction index 1.
_ATA_CREATE_IDEMPOTENT: Final = bytes([1])


@dataclass(frozen=True, slots=True)
class InstructionContext:
    """The program and mint a builder addresses."""

    program_id: Pubkey
    usdc_mint: Pubkey


@dataclass(frozen=True, slots=True)
class MarketTarget:
    """The market a builder acts on: its id (an instruction ARGUMENT) and PDA."""

    market_id: int
    market: Pubkey


def usdc_ata(ctx: InstructionContext, owner: Pubkey) -> Pubkey:
    """The owner's USDC associated token account (off-curve owners allowed: multisig vaults)."""
    return get_associated_token_address(owner, ctx.usdc_mint, TOKEN_PROGRAM_ID)


def _position(ctx: InstructionContext, market: Pubkey, owner: Pubkey, outcome: int) -> Pubkey:
    return kash_market_pdas(ctx.program_id).position(market, owner, outcome).address


def open_position_instruction(
    ctx: InstructionContext,
    target: MarketTarget,
    *,
    owner: Pubkey,
    outcome: int,
    payer: Pubkey,
) -> Instruction:
    """``open_position``: create an empty position for ``owner``. Any signer may pay its rent."""
    return build_program_instruction(
        program_id=ctx.program_id,
        name="open_position",
        args={"market_id": target.market_id, "owner": owner, "outcome": outcome},
        accounts={
            "payer": payer,
            "position": _position(ctx, target.market, owner, outcome),
        },
    )


def buy_instruction(
    ctx: InstructionContext,
    target: MarketTarget,
    *,
    trader: Pubkey,
    receiver: Pubkey,
    outcome: int,
    amount_in_usdc: int,
    min_tokens_out_wad: int,
    deadline: int,
) -> Instruction:
    """``buy``: spend ``amount_in_usdc`` from the trader's USDC ATA; tokens credit ``receiver``."""
    return build_program_instruction(
        program_id=ctx.program_id,
        name="buy",
        args={
            "market_id": target.market_id,
            "outcome": outcome,
            "assets_in_usdc": amount_in_usdc,
            "min_tokens_out_wad": min_tokens_out_wad,
            "deadline": deadline,
            "receiver": receiver,
        },
        accounts={
            "trader": trader,
            "trader_ata": usdc_ata(ctx, trader),
            "receiver_position": _position(ctx, target.market, receiver, outcome),
        },
    )


def sell_instruction(
    ctx: InstructionContext,
    target: MarketTarget,
    *,
    owner: Pubkey,
    receiver: Pubkey,
    outcome: int,
    tokens_in_wad: int,
    min_amount_out_usdc: int,
    deadline: int,
) -> Instruction:
    """``sell``: burn ``tokens_in_wad`` from the owner's position; USDC pays ``receiver``'s ATA."""
    return build_program_instruction(
        program_id=ctx.program_id,
        name="sell",
        args={
            "market_id": target.market_id,
            "outcome": outcome,
            "tokens_in_wad": tokens_in_wad,
            "min_assets_out_usdc": min_amount_out_usdc,
            "deadline": deadline,
        },
        accounts={
            "owner": owner,
            "position": _position(ctx, target.market, owner, outcome),
            "receiver_ata": usdc_ata(ctx, receiver),
        },
    )


def redeem_instruction(
    ctx: InstructionContext,
    target: MarketTarget,
    *,
    kind: Literal["redeem", "redeem_cancelled"],
    owner: Pubkey,
    receiver: Pubkey,
    outcome: int,
    amount_wad: int,
    min_amount_out_usdc: int,
) -> Instruction:
    """``redeem`` (resolved market) or ``redeem_cancelled`` (cancelled market)."""
    return build_program_instruction(
        program_id=ctx.program_id,
        name=kind,
        args={
            "market_id": target.market_id,
            "outcome": outcome,
            "amount_wad": amount_wad,
            "min_out_usdc": min_amount_out_usdc,
        },
        accounts={
            "owner": owner,
            "position": _position(ctx, target.market, owner, outcome),
            "receiver_ata": usdc_ata(ctx, receiver),
        },
    )


def close_position_instruction(
    ctx: InstructionContext,
    target: MarketTarget,
    *,
    owner: Pubkey,
    outcome: int,
    rent_payer: Pubkey,
) -> Instruction:
    """``close_position``: close an EMPTY position; its rent returns to whoever paid it."""
    return build_program_instruction(
        program_id=ctx.program_id,
        name="close_position",
        args={"market_id": target.market_id, "outcome": outcome},
        accounts={
            "owner": owner,
            "position": _position(ctx, target.market, owner, outcome),
            "rent_payer": rent_payer,
        },
    )


def ensure_usdc_ata_instruction(
    ctx: InstructionContext, payer: Pubkey, owner: Pubkey
) -> Instruction:
    """Create ``owner``'s USDC ATA if it does not exist (a no-op when it does)."""
    return Instruction(
        ASSOCIATED_TOKEN_PROGRAM_ID,
        _ATA_CREATE_IDEMPOTENT,
        [
            AccountMeta(payer, is_signer=True, is_writable=True),
            AccountMeta(usdc_ata(ctx, owner), is_signer=False, is_writable=True),
            AccountMeta(owner, is_signer=False, is_writable=False),
            AccountMeta(ctx.usdc_mint, is_signer=False, is_writable=False),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )


__all__ = [
    "ASSOCIATED_TOKEN_PROGRAM_ID",
    "InstructionContext",
    "MarketTarget",
    "buy_instruction",
    "close_position_instruction",
    "ensure_usdc_ata_instruction",
    "open_position_instruction",
    "redeem_instruction",
    "sell_instruction",
    "usdc_ata",
]
