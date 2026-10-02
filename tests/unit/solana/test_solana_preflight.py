"""Refused before signing: program and market state, and verified collateral.

Mirrors the TS SDK's "preflight refuses what the program would refuse" and
"collateral is checked, not assumed" suites. Accounts are patched by
re-encoding them through the IDL codec and reading them back through the real
decoder, so the fixture is a shape the program could have written.
"""

from __future__ import annotations

from typing import Any

import pytest
from solders.pubkey import Pubkey

from kashdao_protocol_sdk.solana import (
    AccountInfo,
    ErrorCode,
    KashChainError,
    KashValidationError,
    SolanaClient,
    keypair_signer,
)
from kashdao_protocol_sdk.solana.accounts import decode_config_account, decode_market_account
from kashdao_protocol_sdk.solana.idl import decode_account, encode_account

FROZEN_NOW = 1_800_000_000


def patch(store, address: Pubkey, name: str, **fields: Any) -> None:
    info = store[str(address)]
    decoded = decode_account(name, info.data)
    store[str(address)] = AccountInfo(
        owner=info.owner, data=encode_account(name, {**decoded, **fields})
    )


async def buy_of(kash: SolanaClient, trader) -> Any:
    return await kash.trades.build_buy(
        market=2, outcome=0, amount_usdc=1_000_000, min_tokens_out_wad=0, trader=trader.pubkey()
    )


async def sell_of(kash: SolanaClient, trader) -> Any:
    return await kash.trades.build_sell(
        market=2, outcome=1, tokens_in_wad=10**18, min_amount_out_usdc=0, owner=trader.pubkey()
    )


class TestProgramAndMarketState:
    async def test_the_unpatched_market_is_tradeable_both_ways(self, make_client, trader) -> None:
        kash, _ = make_client()
        assert (await buy_of(kash, trader)).kind == "buy"
        assert (await sell_of(kash, trader)).kind == "sell"

    async def test_a_paused_program_refuses_buys_and_sells_as_paused(
        self, make_client, store, trader
    ) -> None:
        kash, _ = make_client(store)
        patch(store, kash.pdas.config().address, "Config", paused=1)
        assert decode_config_account(store[str(kash.pdas.config().address)].data).paused
        for build in (buy_of, sell_of):
            with pytest.raises(KashValidationError, match="refused before signing") as exc:
                await build(kash, trader)
            assert exc.value.program_error == "Paused"

    async def test_a_halted_market_refuses_buys_but_lets_a_holder_sell(
        self, make_client, store, trader
    ) -> None:
        kash, _ = make_client(store)
        patch(store, kash.pdas.market(2).address, "Market", halted=1)
        assert decode_market_account(store[str(kash.pdas.market(2).address)].data).halted
        with pytest.raises(KashValidationError) as exc:
            await buy_of(kash, trader)
        assert exc.value.program_error == "HaltedEntries"
        assert (await sell_of(kash, trader)).kind == "sell"

    async def test_a_frozen_market_refuses_both_sides(self, make_client, trader) -> None:
        kash, _ = make_client(now=FROZEN_NOW)
        for build in (buy_of, sell_of):
            with pytest.raises(KashValidationError, match="frozen") as exc:
                await build(kash, trader)
            assert exc.value.program_error is None

    async def test_a_non_active_market_refuses_as_invalid_state(
        self, make_client, store, trader
    ) -> None:
        kash, _ = make_client(store)
        patch(store, kash.pdas.market(2).address, "Market", state=3)
        with pytest.raises(KashValidationError) as exc:
            await buy_of(kash, trader)
        assert exc.value.program_error == "InvalidState"

    async def test_nothing_is_sent_when_a_one_call_trade_is_refused(
        self, make_client, store, trader
    ) -> None:
        kash, connection = make_client(store)
        patch(store, kash.pdas.config().address, "Config", paused=1)
        with pytest.raises(KashValidationError):
            await kash.trades.buy(
                market=2,
                outcome=0,
                amount_usdc=1_000_000,
                max_slippage_bps=100,
                signer=keypair_signer(trader),
            )
        assert connection.sent == []


class TestCollateralIsCheckedNotAssumed:
    async def test_a_missing_collateral_account_is_not_found_not_a_zero(
        self, make_client, store
    ) -> None:
        kash, _ = make_client(store)
        del store[str(kash.pdas.collateral(2).address)]
        for ref in (2, kash.pdas.market(2).address):
            with pytest.raises(KashChainError) as exc:
                await kash.markets.quote_buy(market=ref, outcome=0, amount_usdc=1)
            assert exc.value.code == ErrorCode.ACCOUNT_NOT_FOUND

    async def test_collateral_of_another_mint_is_refused_rather_than_priced(
        self, make_client, store
    ) -> None:
        kash, _ = make_client(store)
        key = str(kash.pdas.collateral(2).address)
        info = store[key]
        store[key] = AccountInfo(owner=info.owner, data=bytes(Pubkey.new_unique()) + info.data[32:])
        with pytest.raises(KashValidationError, match="not the cluster's USDC"):
            await kash.markets.get(2)

    async def test_a_market_naming_a_foreign_collateral_account_is_refused(
        self, make_client, store
    ) -> None:
        kash, _ = make_client(store)
        patch(store, kash.pdas.market(2).address, "Market", collateral_account=bytes(32))
        with pytest.raises(KashValidationError, match="other than its PDA"):
            await kash.markets.get(2)

    async def test_a_users_missing_usdc_account_is_still_a_measured_zero(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client()
        assert await kash.account.usdc_balance(trader.pubkey()) == 0
