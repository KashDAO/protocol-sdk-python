"""Instruction encoding, against three independent instruments.

1. A hand-assembled ``buy`` byte layout that goes through no codec at all —
   the same vector the TS SDK's ``instructions.test.ts`` pins (discriminator,
   then little-endian args in IDL order; 81 bytes).
2. Every discriminator recomputed as Anchor defines it,
   ``sha256("global:<name>")[:8]``, rather than read back from the IDL.
3. A round-trip through the IDL decoder plus the account list's order and
   signer/writable flags against the IDL's own declaration.
"""

from __future__ import annotations

import hashlib

import pytest
from solders.pubkey import Pubkey

from kashdao_protocol_sdk.solana import (
    InstructionContext,
    KashValidationError,
    MarketTarget,
    buy_instruction,
    close_position_instruction,
    ensure_usdc_ata_instruction,
    get_solana_deployment,
    kash_market_pdas,
    open_position_instruction,
    redeem_instruction,
    sell_instruction,
    usdc_ata,
)
from kashdao_protocol_sdk.solana.idl import decode_instruction, idl_instruction_accounts

deployment = get_solana_deployment("mainnet-beta")
ctx = InstructionContext(program_id=deployment.program_id, usdc_mint=deployment.usdc_mint)
pdas = kash_market_pdas(deployment.program_id)

MARKET_ID = 2
target = MarketTarget(market_id=MARKET_ID, market=pdas.market(MARKET_ID).address)
TRADER = Pubkey.from_string("8ubeSrVr6qxiarBFH4sA5KEw45DUftof9vBxRRiuL6ZM")
RECEIVER = Pubkey.from_string("AHfEu5FrFM16QsAjtqZBHaLBXLYLeHUv1o699hZZtZt3")
DEADLINE = 1_790_990_000
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SYSTEM_PROGRAM = "11111111111111111111111111111111"


def le(value: int, width: int) -> bytes:
    return value.to_bytes(width, "little")


def anchor_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


def expect_idl_account_shape(ix, name: str) -> None:
    spec = idl_instruction_accounts(name)
    assert [(m.is_signer, m.is_writable) for m in ix.accounts] == [
        (a.get("signer") is True, a.get("writable") is True) for a in spec
    ]
    assert ix.program_id == deployment.program_id
    assert bytes(ix.data[:8]) == anchor_discriminator(name)


class TestBuyLayoutByteForByte:
    def test_discriminator_then_le_args_in_idl_order(self) -> None:
        ix = buy_instruction(
            ctx,
            target,
            trader=TRADER,
            receiver=RECEIVER,
            outcome=1,
            amount_in_usdc=5_000_000,
            min_tokens_out_wad=4_118_366_472_914_253_530,
            deadline=DEADLINE,
        )
        expected = (
            bytes([102, 6, 61, 18, 1, 218, 235, 234])  # sha256("global:buy")[0..8]
            + le(MARKET_ID, 8)
            + bytes([1])
            + le(5_000_000, 8)
            + le(4_118_366_472_914_253_530, 16)
            + le(DEADLINE, 8)
            + bytes(RECEIVER)
        )
        assert len(ix.data) == 81
        assert bytes(ix.data) == expected
        assert bytes(ix.data).hex() == (
            "66063d1201daebea"
            "0200000000000000"
            "01"
            "404b4c0000000000"
            "dab6eeb07c602739"
            "0000000000000000"
            "b056c06a00000000" + bytes(RECEIVER).hex()
        )

    def test_buy_account_list_is_the_idl_order(self) -> None:
        ix = buy_instruction(
            ctx,
            target,
            trader=TRADER,
            receiver=RECEIVER,
            outcome=1,
            amount_in_usdc=5_000_000,
            min_tokens_out_wad=2**100 + 7,
            deadline=DEADLINE,
        )
        assert [str(m.pubkey) for m in ix.accounts] == [
            str(pdas.config().address),
            str(target.market),
            str(pdas.collateral(MARKET_ID).address),
            str(TRADER),
            str(usdc_ata(ctx, TRADER)),
            str(pdas.position(target.market, RECEIVER, 1).address),
            TOKEN_PROGRAM,
            str(pdas.event_authority().address),
            str(deployment.program_id),
        ]
        expect_idl_account_shape(ix, "buy")
        decoded = decode_instruction(bytes(ix.data))
        assert decoded.name == "buy"
        assert decoded.args == {
            "market_id": 2,
            "outcome": 1,
            "assets_in_usdc": 5_000_000,
            "min_tokens_out_wad": 2**100 + 7,
            "deadline": DEADLINE,
            "receiver": RECEIVER,
        }

    def test_a_corrupted_byte_no_longer_decodes_to_the_same_arguments(self) -> None:
        ix = buy_instruction(
            ctx,
            target,
            trader=TRADER,
            receiver=RECEIVER,
            outcome=1,
            amount_in_usdc=5_000_000,
            min_tokens_out_wad=1,
            deadline=DEADLINE,
        )
        corrupt = bytearray(ix.data)
        corrupt[16] = 0  # the outcome byte
        assert decode_instruction(bytes(corrupt)).args["outcome"] == 0


class TestRoundTrips:
    def test_open_position(self) -> None:
        ix = open_position_instruction(ctx, target, owner=RECEIVER, outcome=1, payer=TRADER)
        decoded = decode_instruction(bytes(ix.data))
        assert decoded.name == "open_position"
        assert decoded.args == {"market_id": 2, "owner": RECEIVER, "outcome": 1}
        expect_idl_account_shape(ix, "open_position")
        assert ix.accounts[0].pubkey == target.market
        assert ix.accounts[1].pubkey == TRADER
        assert ix.accounts[2].pubkey == pdas.position(target.market, RECEIVER, 1).address
        assert str(ix.accounts[3].pubkey) == SYSTEM_PROGRAM

    def test_sell(self) -> None:
        ix = sell_instruction(
            ctx,
            target,
            owner=TRADER,
            receiver=RECEIVER,
            outcome=0,
            tokens_in_wad=123_456_789_000_000_000_000,
            min_amount_out_usdc=99,
            deadline=DEADLINE,
        )
        decoded = decode_instruction(bytes(ix.data))
        assert decoded.name == "sell"
        assert decoded.args == {
            "market_id": 2,
            "outcome": 0,
            "tokens_in_wad": 123_456_789_000_000_000_000,
            "min_assets_out_usdc": 99,
            "deadline": DEADLINE,
        }
        expect_idl_account_shape(ix, "sell")
        assert ix.accounts[4].pubkey == pdas.position(target.market, TRADER, 0).address
        assert ix.accounts[5].pubkey == usdc_ata(ctx, RECEIVER)

    @pytest.mark.parametrize("kind", ["redeem", "redeem_cancelled"])
    def test_redeem(self, kind) -> None:
        ix = redeem_instruction(
            ctx,
            target,
            kind=kind,
            owner=TRADER,
            receiver=TRADER,
            outcome=1,
            amount_wad=10**18,
            min_amount_out_usdc=1,
        )
        decoded = decode_instruction(bytes(ix.data))
        assert decoded.name == kind
        assert decoded.args == {
            "market_id": 2,
            "outcome": 1,
            "amount_wad": 10**18,
            "min_out_usdc": 1,
        }
        expect_idl_account_shape(ix, kind)

    def test_close_position_refunds_the_named_rent_payer(self) -> None:
        ix = close_position_instruction(ctx, target, owner=TRADER, outcome=0, rent_payer=RECEIVER)
        decoded = decode_instruction(bytes(ix.data))
        assert decoded.name == "close_position"
        assert decoded.args == {"market_id": 2, "outcome": 0}
        expect_idl_account_shape(ix, "close_position")
        assert ix.accounts[3].pubkey == RECEIVER


class TestDiscriminators:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("buy", [102, 6, 61, 18, 1, 218, 235, 234]),
            ("sell", [51, 230, 133, 164, 1, 127, 131, 173]),
            ("open_position", [135, 128, 47, 77, 15, 152, 240, 49]),
            ("close_position", [123, 134, 81, 0, 49, 68, 98, 98]),
            ("redeem", [184, 12, 86, 149, 70, 196, 97, 225]),
            ("redeem_cancelled", [117, 219, 155, 173, 98, 166, 50, 100]),
        ],
    )
    def test_each_trader_instruction_carries_the_anchor_discriminator(self, name, expected) -> None:
        assert anchor_discriminator(name) == bytes(expected)


class TestAssociatedTokenAccount:
    def test_usdc_ata_is_the_spl_derivation(self) -> None:
        ata_program = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
        expected, _ = Pubkey.find_program_address(
            [bytes(TRADER), bytes(Pubkey.from_string(TOKEN_PROGRAM)), bytes(deployment.usdc_mint)],
            ata_program,
        )
        assert usdc_ata(ctx, TRADER) == expected

    def test_ensure_ata_is_create_idempotent_paid_by_the_payer(self) -> None:
        ix = ensure_usdc_ata_instruction(ctx, TRADER, RECEIVER)
        assert str(ix.program_id) == "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
        assert bytes(ix.data) == bytes([1])
        assert [(str(m.pubkey), m.is_signer, m.is_writable) for m in ix.accounts] == [
            (str(TRADER), True, True),
            (str(usdc_ata(ctx, RECEIVER)), False, True),
            (str(RECEIVER), False, False),
            (str(deployment.usdc_mint), False, False),
            (SYSTEM_PROGRAM, False, False),
            (TOKEN_PROGRAM, False, False),
        ]


class TestIntegerWidths:
    def test_u64_u128_and_u8_bounds_come_from_the_idl(self) -> None:
        base = {
            "trader": TRADER,
            "receiver": RECEIVER,
            "outcome": 1,
            "amount_in_usdc": 1,
            "min_tokens_out_wad": 0,
            "deadline": DEADLINE,
        }
        with pytest.raises(KashValidationError, match="assets_in_usdc is outside u64"):
            buy_instruction(ctx, target, **{**base, "amount_in_usdc": 2**64})
        with pytest.raises(KashValidationError, match="min_tokens_out_wad is outside u128"):
            buy_instruction(ctx, target, **{**base, "min_tokens_out_wad": 2**128})
        with pytest.raises(KashValidationError):
            buy_instruction(ctx, target, **{**base, "outcome": 256})
        with pytest.raises(KashValidationError, match="deadline is outside i64"):
            buy_instruction(ctx, target, **{**base, "deadline": 2**63})
        with pytest.raises(KashValidationError, match="must be an integer"):
            buy_instruction(ctx, target, **{**base, "amount_in_usdc": True})
