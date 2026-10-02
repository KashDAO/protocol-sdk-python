"""``create_solana_client`` over a fake connection serving the mainnet snapshot.

Reads, quotes, plan building, slippage enforcement and every stage of the
send path's error mapping. Mirrors the TS SDK's ``client.test.ts``. No network.
"""

from __future__ import annotations

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from kashdao_protocol_sdk.solana import (
    DEFAULT_TRADE_DEADLINE_SECONDS,
    AccountInfo,
    ErrorCode,
    InstructionContext,
    KashChainError,
    KashConfigError,
    KashSignerError,
    KashSimulationRevertedError,
    KashTransactionExpiredError,
    KashTransactionOutcomeUnknownError,
    KashValidationError,
    PreflightFailure,
    SimulationValue,
    SolanaRpcConnection,
    SolanaRpcError,
    SolanaRpcTransportError,
    create_solana_client,
    keypair_signer,
    usdc_ata,
)
from kashdao_protocol_sdk.solana.idl import (
    decode_account,
    decode_instruction,
    encode_account,
    program_error_names,
)

NOW = 1_790_950_000
MAINNET_PROGRAM = "Jr8Bd8efPfNYHW65vZrVzeLbYkzy1oo3QB3i8cYdDcy"


def ix_names(plan, program_id) -> list[str]:
    return [
        decode_instruction(bytes(ix.data)).name if ix.program_id == program_id else "external"
        for ix in plan.instructions
    ]


class TestConfig:
    def test_defaults_to_mainnet_beta_with_confirmed_commitment(self, make_client) -> None:
        kash, _ = make_client()
        assert kash.cluster == "mainnet-beta"
        assert str(kash.program_id) == MAINNET_PROGRAM
        assert kash.commitment == "confirmed"

    async def test_requires_exactly_one_of_connection_or_rpc_url(self, make_client) -> None:
        _, connection = make_client()
        with pytest.raises(KashConfigError, match="exactly one"):
            create_solana_client()
        with pytest.raises(KashConfigError, match="exactly one"):
            create_solana_client(connection=connection, rpc_url="https://x.example")
        with pytest.raises(KashConfigError, match="https"):
            create_solana_client(rpc_url="http://rpc.example.com")
        with pytest.raises(KashConfigError):
            create_solana_client(connection=connection, commitment="max")  # type: ignore[arg-type]
        with pytest.raises(KashConfigError, match="SolanaConnection"):
            create_solana_client(connection=object())  # type: ignore[arg-type]
        local = create_solana_client(rpc_url="http://127.0.0.1:8899", cluster="devnet")
        assert local.cluster == "devnet"
        assert isinstance(local.connection, SolanaRpcConnection)
        await local.aclose()

    async def test_an_owned_connection_closes_with_the_client(self) -> None:
        async with create_solana_client(rpc_url="https://rpc.example") as kash:
            assert isinstance(kash.connection, SolanaRpcConnection)
        assert kash.connection._http.is_closed  # type: ignore[attr-defined]


class TestReads:
    async def test_protocol_config_and_templates(self, make_client) -> None:
        kash, _ = make_client()
        config = await kash.protocol.config()
        assert config.template_count == 4
        assert config.default_protocol_fee_bps == 100
        templates = await kash.protocol.templates()
        assert [(t.id, t.max_outcomes) for t in templates] == [(0, 10), (1, 20), (2, 48), (3, 96)]
        assert (await kash.protocol.template(3)).sell_fee_bps == 50

    async def test_market_by_id_and_by_address_agree(self, make_client) -> None:
        kash, _ = make_client()
        by_id = await kash.markets.get(2)
        assert await kash.markets.get(str(by_id.address)) == by_id
        assert await kash.markets.get(by_id.address) == by_id
        assert by_id.status == "active"
        assert by_id.collateral_usdc == 111_000_000
        assert by_id.sell_fee_bps == 50
        assert 10**18 - 3 < sum(by_id.probabilities_wad) <= 10**18

    async def test_derives_frozen_from_the_clock(self, make_client) -> None:
        kash, _ = make_client(now=1_800_000_000)
        assert (await kash.markets.get(2)).status == "frozen"

    async def test_missing_market_is_not_found_and_a_foreign_account_is_refused(
        self, make_client
    ) -> None:
        kash, _ = make_client()
        with pytest.raises(KashChainError) as exc:
            await kash.markets.get(99)
        assert exc.value.code == ErrorCode.ACCOUNT_NOT_FOUND
        # The collateral account exists but is owned by the token program.
        with pytest.raises(KashChainError) as exc:
            await kash.markets.get(kash.pdas.collateral(0).address)
        assert exc.value.code == ErrorCode.ACCOUNT_OWNER_MISMATCH

    async def test_an_address_that_is_not_its_ids_pda_is_refused(self, make_client, store) -> None:
        kash, _ = make_client(store)
        impostor = Pubkey.new_unique()
        store[str(impostor)] = store[str(kash.pdas.market(2).address)]
        with pytest.raises(KashValidationError, match="not the PDA"):
            await kash.markets.get(impostor)

    async def test_an_rpc_failure_is_a_retryable_chain_error_never_a_zero(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client(read_error=OSError("503"))
        with pytest.raises(KashChainError) as exc:
            await kash.account.usdc_balance(trader.pubkey())
        assert exc.value.is_retryable is True
        assert exc.value.code == ErrorCode.ACCOUNT_READ_FAILED

    async def test_a_missing_position_or_usdc_account_reads_as_zero(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client()
        assert await kash.account.usdc_balance(trader.pubkey()) == 0
        position = await kash.account.position(market=2, outcome=1, owner=trader.pubkey())
        assert (position.exists, position.balance_wad, position.rent_payer) == (False, 0, None)

    async def test_usdc_balance_reads_the_spl_amount(self, make_client, store, trader) -> None:
        kash, _ = make_client(store)
        ctx = InstructionContext(program_id=kash.program_id, usdc_mint=kash.usdc_mint)
        data = bytes(kash.usdc_mint) + bytes(trader.pubkey()) + (12_345_678).to_bytes(8, "little")
        store[str(usdc_ata(ctx, trader.pubkey()))] = AccountInfo(
            owner=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
            data=data + bytes(165 - len(data)),
        )
        assert await kash.account.usdc_balance(str(trader.pubkey())) == 12_345_678

    async def test_positions_reads_every_outcome_in_one_request(
        self, make_client, store, planter, trader
    ) -> None:
        kash, connection = make_client(store)
        planter(store, kash, 2, trader.pubkey(), 1, 7 * 10**18)
        before = len(connection.reads)
        positions = await kash.account.positions(market=2, owner=trader.pubkey())
        assert [p.balance_wad for p in positions] == [0, 7 * 10**18]
        assert positions[1].rent_payer == trader.pubkey()
        # one round trip for the market + collateral, one for both positions
        assert len(connection.reads) - before == 2

    async def test_quotes_through_the_client_match_the_pinned_vectors(self, make_client) -> None:
        kash, _ = make_client()
        buy = await kash.markets.quote_buy(market=2, outcome=0, amount_usdc=1_000_000)
        assert buy.tokens_out_wad == 4_118_366_472_914_253_530
        sell = await kash.markets.quote_sell(
            market=2, outcome=0, tokens_in_wad=4_118_366_472_914_253_530
        )
        assert sell.amount_out_usdc == 907_501
        with pytest.raises(KashValidationError):
            await kash.markets.quote_buy(market=2, outcome=0, amount_usdc=0)
        with pytest.raises(KashValidationError) as exc:
            await kash.markets.quote_redeem(market=2, outcome=0, amount_wad=10**18)
        assert exc.value.program_error == "InvalidDomain"


class TestBuild:
    async def test_build_buy_opens_a_missing_position_then_buys_at_the_quoted_floor(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client()
        plan = await kash.trades.build_buy(
            market=2, outcome=0, amount_usdc=1_000_000, max_slippage_bps=100, trader=trader.pubkey()
        )
        assert plan.opens_position is True
        assert ix_names(plan, kash.program_id) == ["open_position", "buy"]
        assert plan.quote is not None
        assert plan.quote.tokens_out_wad == 4_118_366_472_914_253_530
        assert plan.min_tokens_out_wad == 4_118_366_472_914_253_530 * 9_900 // 10_000
        buy = decode_instruction(bytes(plan.instructions[1].data)).args
        assert buy["min_tokens_out_wad"] == plan.min_tokens_out_wad
        assert buy["deadline"] == NOW + DEFAULT_TRADE_DEADLINE_SECONDS

    async def test_build_buy_skips_open_position_when_the_receiver_holds_one(
        self, make_client, store, planter, trader
    ) -> None:
        kash, _ = make_client(store)
        planter(store, kash, 2, trader.pubkey(), 0, 0)
        plan = await kash.trades.build_buy(
            market=2, outcome=0, amount_usdc=1_000_000, min_tokens_out_wad=0, trader=trader.pubkey()
        )
        assert plan.opens_position is False
        assert plan.quote is None
        assert ix_names(plan, kash.program_id) == ["buy"]

    async def test_build_buy_credits_a_receiver_and_charges_a_rent_payer(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client()
        receiver, sponsor = Pubkey.new_unique(), Pubkey.new_unique()
        plan = await kash.trades.build_buy(
            market=2,
            outcome=1,
            amount_usdc=1_000_000,
            min_tokens_out_wad=5,
            deadline=1_800_000_123,
            trader=trader.pubkey(),
            receiver=receiver,
            payer=sponsor,
        )
        opened = decode_instruction(bytes(plan.instructions[0].data))
        assert opened.args["owner"] == receiver
        assert plan.instructions[0].accounts[1].pubkey == sponsor
        bought = decode_instruction(bytes(plan.instructions[1].data)).args
        assert (bought["receiver"], bought["min_tokens_out_wad"], bought["deadline"]) == (
            receiver,
            5,
            1_800_000_123,
        )

    async def test_build_sell_creates_the_receiver_ata_idempotently_then_sells(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client()
        plan = await kash.trades.build_sell(
            market=2, outcome=1, tokens_in_wad=10**18, max_slippage_bps=0, owner=trader.pubkey()
        )
        assert ix_names(plan, kash.program_id) == ["external", "sell"]
        assert plan.quote is not None
        assert plan.min_amount_out_usdc == plan.quote.amount_out_usdc

    async def test_the_sell_floor_uses_the_markets_own_sell_fee(
        self, make_client, store, trader
    ) -> None:
        kash, _ = make_client(store)
        key = str(kash.pdas.market(2).address)
        fields = decode_account("Market", store[key].data)
        default = await kash.trades.build_sell(
            market=2, outcome=0, tokens_in_wad=10**18, max_slippage_bps=0, owner=trader.pubkey()
        )
        store[key] = AccountInfo(
            owner=store[key].owner, data=encode_account("Market", {**fields, "sell_fee_bps": 300})
        )
        assert (await kash.markets.get(2)).sell_fee_bps == 300
        dearer = await kash.trades.build_sell(
            market=2, outcome=0, tokens_in_wad=10**18, max_slippage_bps=0, owner=trader.pubkey()
        )
        assert dearer.min_amount_out_usdc < default.min_amount_out_usdc
        encoded = decode_instruction(bytes(dearer.instructions[1].data)).args
        assert encoded["min_assets_out_usdc"] == dearer.min_amount_out_usdc

    async def test_build_redeem_refuses_a_market_in_the_wrong_state(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client()
        params = {
            "market": 2,
            "outcome": 0,
            "owner": trader.pubkey(),
            "amount_wad": 1,
            "min_amount_out_usdc": 0,
        }
        with pytest.raises(KashValidationError, match="is not resolved"):
            await kash.trades.build_redeem(**params)
        with pytest.raises(KashValidationError, match="is not cancelled"):
            await kash.trades.build_redeem_cancelled(**params)

    async def test_build_redeem_on_a_resolved_market_defaults_to_the_whole_position(
        self, make_client, store, planter, trader
    ) -> None:
        kash, _ = make_client(store)
        key = str(kash.pdas.market(2).address)
        fields = decode_account("Market", store[key].data)
        winning_supply = int.from_bytes(fields["supply_wad"][1], "little")
        store[key] = AccountInfo(
            owner=store[key].owner,
            data=encode_account(
                "Market",
                {
                    **fields,
                    "state": 2,
                    "winning_outcome": 1,
                    "sum_winning_supply": winning_supply.to_bytes(32, "little"),
                },
            ),
        )
        with pytest.raises(KashValidationError, match="nothing to redeem"):
            await kash.trades.build_redeem(
                market=2, outcome=1, owner=trader.pubkey(), max_slippage_bps=0
            )
        planter(store, kash, 2, trader.pubkey(), 1, 3 * 10**18)
        plan = await kash.trades.build_redeem(
            market=2, outcome=1, owner=trader.pubkey(), max_slippage_bps=50
        )
        assert plan.kind == "redeem"
        assert plan.amount_wad == 3 * 10**18
        assert plan.quote is not None and plan.quote.payout_usdc > 0
        assert plan.min_amount_out_usdc == plan.quote.payout_usdc * 9_950 // 10_000
        assert ix_names(plan, kash.program_id) == ["external", "redeem"]

    async def test_build_close_position_refuses_missing_or_non_empty_and_refunds_the_rent_payer(
        self, make_client, store, planter, trader
    ) -> None:
        kash, _ = make_client(store)
        sponsor = Pubkey.new_unique()
        with pytest.raises(KashValidationError, match="does not exist"):
            await kash.trades.build_close_position(market=2, outcome=0, owner=trader.pubkey())
        planter(store, kash, 2, trader.pubkey(), 1, 1)
        with pytest.raises(KashValidationError, match="not empty") as exc:
            await kash.trades.build_close_position(market=2, outcome=1, owner=trader.pubkey())
        assert exc.value.program_error == "PositionNotEmpty"
        planter(store, kash, 2, trader.pubkey(), 0, 0, sponsor)
        plan = await kash.trades.build_close_position(market=2, outcome=0, owner=trader.pubkey())
        # The program refunds Position.rent_payer, not the closer; the closer pays nothing.
        assert plan.rent_recipient == sponsor
        assert plan.payer == trader.pubkey()
        assert plan.instructions[0].accounts[3].pubkey == sponsor

    async def test_build_open_position_refuses_an_outcome_the_market_lacks(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client()
        with pytest.raises(KashValidationError):
            await kash.trades.build_open_position(market=2, outcome=2, owner=trader.pubkey())
        plan = await kash.trades.build_open_position(market=2, outcome=1, owner=trader.pubkey())
        assert plan.signer == plan.payer == trader.pubkey()


class TestSlippageIsMandatory:
    async def test_no_bound_is_refused(self, make_client, trader) -> None:
        kash, _ = make_client()
        with pytest.raises(KashConfigError) as exc:
            await kash.trades.build_buy(market=2, outcome=0, amount_usdc=1, trader=trader.pubkey())
        assert exc.value.code == ErrorCode.INVALID_SLIPPAGE
        with pytest.raises(KashConfigError) as exc:
            await kash.trades.build_sell(
                market=2, outcome=0, tokens_in_wad=1, owner=trader.pubkey()
            )
        assert exc.value.code == ErrorCode.INVALID_SLIPPAGE

    async def test_both_bounds_are_refused(self, make_client, trader) -> None:
        kash, _ = make_client()
        with pytest.raises(KashConfigError) as exc:
            await kash.trades.build_buy(
                market=2,
                outcome=0,
                amount_usdc=1,
                trader=trader.pubkey(),
                min_tokens_out_wad=1,
                max_slippage_bps=1,
            )
        assert exc.value.code == ErrorCode.INVALID_SLIPPAGE

    @pytest.mark.parametrize("bps", [-1, 10_001, True])
    async def test_out_of_range_bps_is_refused(self, make_client, trader, bps) -> None:
        kash, _ = make_client()
        with pytest.raises(KashConfigError) as exc:
            await kash.trades.build_buy(
                market=2, outcome=0, amount_usdc=1, trader=trader.pubkey(), max_slippage_bps=bps
            )
        assert exc.value.code == ErrorCode.INVALID_SLIPPAGE

    async def test_a_negative_floor_is_refused_and_a_zero_amount_too(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client()
        with pytest.raises(KashConfigError):
            await kash.trades.build_sell(
                market=2, outcome=0, tokens_in_wad=1, owner=trader.pubkey(), min_amount_out_usdc=-1
            )
        with pytest.raises(KashValidationError):
            await kash.trades.build_buy(
                market=2, outcome=0, amount_usdc=0, trader=trader.pubkey(), max_slippage_bps=1
            )

    async def test_the_floor_is_what_gets_encoded(self, make_client, trader) -> None:
        kash, _ = make_client()
        for bps in (0, 37, 10_000):
            plan = await kash.trades.build_buy(
                market=2,
                outcome=0,
                amount_usdc=1_000_000,
                trader=trader.pubkey(),
                max_slippage_bps=bps,
            )
            expected = 4_118_366_472_914_253_530 * (10_000 - bps) // 10_000
            assert plan.min_tokens_out_wad == expected
            encoded = decode_instruction(bytes(plan.instructions[-1].data)).args
            assert encoded["min_tokens_out_wad"] == expected


class TestSend:
    async def _plan(self, kash, trader):
        return await kash.trades.build_buy(
            market=2, outcome=0, amount_usdc=1_000_000, min_tokens_out_wad=1, trader=trader.pubkey()
        )

    async def test_signs_with_the_keypair_sends_v0_and_confirms(self, make_client, trader) -> None:
        kash, connection = make_client()
        result = await kash.trades.send(
            await self._plan(kash, trader), signer=keypair_signer(trader)
        )
        sent = VersionedTransaction.from_bytes(connection.sent[0])
        # The signature is computed from the signed transaction, not trusted from the RPC.
        assert (result.signature, result.slot) == (str(sent.signatures[0]), 42)
        assert sent.message.account_keys[0] == trader.pubkey()
        assert sent.signatures[0] != Signature.default()
        assert sent.verify_with_results() == [True]

    async def test_a_fee_payer_pays_and_both_sign(self, make_client, trader) -> None:
        kash, connection = make_client()
        sponsor = Keypair()
        await kash.trades.send(
            await self._plan(kash, trader),
            signer=keypair_signer(trader),
            fee_payer=keypair_signer(sponsor),
        )
        sent = VersionedTransaction.from_bytes(connection.sent[0])
        assert sent.message.account_keys[0] == sponsor.pubkey()
        assert sent.verify_with_results() == [True, True]

    async def test_prepends_compute_budget_instructions_when_asked(
        self, make_client, trader
    ) -> None:
        kash, connection = make_client()
        await kash.trades.send(
            await self._plan(kash, trader),
            signer=keypair_signer(trader),
            compute_unit_limit=400_000,
            compute_unit_price_micro_lamports=5_000,
        )
        sent = VersionedTransaction.from_bytes(connection.sent[0])
        assert len(sent.message.instructions) == 4

    async def test_refuses_before_signing_when_a_required_signer_is_missing(
        self, make_client, trader
    ) -> None:
        kash, connection = make_client()
        with pytest.raises(KashValidationError, match="to sign"):
            await kash.trades.send(await self._plan(kash, trader), signer=keypair_signer(Keypair()))
        assert connection.sent == []

    async def test_a_preflight_refusal_names_the_kash_market_error(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client(
            send_error=PreflightFailure(
                "Transaction simulation failed",
                err={"InstructionError": [1, {"Custom": 6022}]},
                logs=[
                    f"Program {MAINNET_PROGRAM} invoke [1]",
                    f"Program {MAINNET_PROGRAM} failed: custom program error: 0x1786",
                ],
            )
        )
        with pytest.raises(KashSimulationRevertedError) as exc:
            await kash.trades.send(await self._plan(kash, trader), signer=keypair_signer(trader))
        # 0x1786 = 6022 — read the name from the IDL rather than trusting a literal.
        expected = program_error_names()[6022]
        assert expected
        assert exc.value.context is not None
        assert exc.value.context["program_error"] == expected
        assert exc.value.code == ErrorCode.SIMULATION_REVERTED

    async def test_the_same_code_from_another_program_gets_no_kash_name(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client(
            send_error=PreflightFailure(
                "Transaction simulation failed",
                err=None,
                logs=[
                    "Program TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA failed: "
                    "custom program error: 0x1786"
                ],
            )
        )
        with pytest.raises(KashSimulationRevertedError) as exc:
            await kash.trades.send(await self._plan(kash, trader), signer=keypair_signer(trader))
        assert exc.value.context is not None
        assert exc.value.context["program_error"] is None
        assert exc.value.context["program_error_code"] == 0x1786

    async def test_a_definite_rpc_refusal_is_retryable_tx_send_failed(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client(
            send_error=SolanaRpcError("sendTransaction", -32003, "signature verification failure")
        )
        with pytest.raises(KashChainError) as exc:
            await kash.trades.send(await self._plan(kash, trader), signer=keypair_signer(trader))
        assert exc.value.code == ErrorCode.TX_SEND_FAILED
        assert exc.value.is_retryable is True
        assert exc.value.context is not None
        assert exc.value.context["signature"]

    @pytest.mark.parametrize(
        "failure",
        [
            OSError("fetch failed"),
            TimeoutError("timed out"),
            SolanaRpcTransportError("sendTransaction", "HTTP 503"),
            ValueError("unreadable answer"),
        ],
        ids=["os-error", "timeout", "transport-5xx", "unexpected"],
    )
    async def test_a_send_without_a_definite_answer_may_have_landed(
        self, make_client, trader, failure
    ) -> None:
        """The double-trade guard: not retryable, and it names the signature that was sent."""
        kash, connection = make_client(send_error=failure)
        plan = await self._plan(kash, trader)
        with pytest.raises(KashChainError) as exc:
            await kash.trades.send(plan, signer=keypair_signer(trader))
        assert exc.value.code == ErrorCode.WAIT_RECEIPT_FAILED
        assert exc.value.is_retryable is False
        assert exc.value.context is not None
        signature = exc.value.context["signature"]
        assert len(Signature.from_string(signature).to_bytes()) == 64
        assert connection.sent == []  # the fake raised before recording

    async def test_landed_and_failed_is_tx_reverted_decoded_from_the_instruction_error(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client(confirm_err={"InstructionError": [1, {"Custom": 6001}]})
        with pytest.raises(KashChainError) as exc:
            await kash.trades.send(await self._plan(kash, trader), signer=keypair_signer(trader))
        assert exc.value.code == ErrorCode.TX_REVERTED
        assert exc.value.is_retryable is False
        assert exc.value.context is not None
        assert exc.value.context["program_error"] == "Paused"

    async def test_expired_is_tx_expired_and_unreadable_keeps_the_signature(
        self, make_client, trader
    ) -> None:
        expired, _ = make_client(
            confirm_error=KashTransactionExpiredError("expired", signature="sig")
        )
        with pytest.raises(KashChainError) as exc:
            await expired.trades.send(
                await self._plan(expired, trader), signer=keypair_signer(trader)
            )
        assert exc.value.code == ErrorCode.TX_EXPIRED
        unknown, _ = make_client(confirm_error=OSError("socket hang up"))
        with pytest.raises(KashChainError) as exc:
            await unknown.trades.send(
                await self._plan(unknown, trader), signer=keypair_signer(trader)
            )
        assert exc.value.code == ErrorCode.WAIT_RECEIPT_FAILED
        assert exc.value.context is not None
        assert Signature.from_string(exc.value.context["signature"]) != Signature.default()
        assert exc.value.is_retryable is False

    async def test_only_the_typed_expiry_error_means_did_not_land(
        self, make_client, trader
    ) -> None:
        """A look-alike class name is not the contract: it reads as 'may have landed'."""
        look_alike = type("TransactionExpiredBlockheightExceededError", (Exception,), {})
        kash, _ = make_client(confirm_error=look_alike("expired?"))
        with pytest.raises(KashChainError) as exc:
            await kash.trades.send(await self._plan(kash, trader), signer=keypair_signer(trader))
        assert exc.value.code == ErrorCode.WAIT_RECEIPT_FAILED

    async def test_already_processed_with_empty_logs_is_landed_and_confirmed(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client(
            send_error=PreflightFailure(
                "This transaction has already been processed", err="AlreadyProcessed", logs=[]
            )
        )
        result = await kash.trades.send(
            await self._plan(kash, trader), signer=keypair_signer(trader)
        )
        assert result.slot == 42  # the fake's confirmation: it went on to confirm

    async def test_blockhash_not_found_is_a_labelled_retryable_refusal(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client(
            send_error=PreflightFailure("Blockhash not found", err="BlockhashNotFound", logs=[])
        )
        with pytest.raises(KashChainError, match="BlockhashNotFound") as exc:
            await kash.trades.send(await self._plan(kash, trader), signer=keypair_signer(trader))
        assert exc.value.code == ErrorCode.TX_SEND_FAILED
        assert exc.value.is_retryable is True
        assert not isinstance(exc.value, KashSimulationRevertedError)

    async def test_a_preflight_revert_with_empty_logs_is_decoded_from_err(
        self, make_client, trader
    ) -> None:
        kash, _ = make_client(
            send_error=PreflightFailure(
                "Transaction simulation failed",
                err={"InstructionError": [1, {"Custom": 6001}]},
                logs=[],
            )
        )
        with pytest.raises(KashSimulationRevertedError) as exc:
            await kash.trades.send(await self._plan(kash, trader), signer=keypair_signer(trader))
        assert exc.value.context is not None
        assert exc.value.context["program_error"] == "Paused"

    @pytest.mark.parametrize(
        "failure",
        [
            SolanaRpcError("sendTransaction", -32603, "Internal error"),
            SolanaRpcError("sendTransaction", -32005, "Node is behind"),
            SolanaRpcError("sendTransaction", None, "HTTP 429", http_status=429),
            SolanaRpcError("sendTransaction", -32003, "HTTP 400 dressed up", http_status=400),
        ],
        ids=["-32603", "-32005", "http-429", "http-4xx"],
    )
    async def test_provider_errors_and_http_statuses_may_have_landed(
        self, make_client, trader, failure
    ) -> None:
        kash, _ = make_client(send_error=failure)
        with pytest.raises(KashTransactionOutcomeUnknownError) as exc:
            await kash.trades.send(await self._plan(kash, trader), signer=keypair_signer(trader))
        assert exc.value.code == ErrorCode.WAIT_RECEIPT_FAILED
        assert exc.value.is_retryable is False
        assert Signature.from_string(exc.value.signature) != Signature.default()

    async def test_no_instructions_is_refused(self, make_client, trader) -> None:
        kash, _ = make_client()
        with pytest.raises(KashValidationError, match="no instructions"):
            await kash.trades.send([], signer=keypair_signer(trader))

    async def test_one_call_buy_builds_with_the_signer_as_trader(self, make_client, trader) -> None:
        kash, connection = make_client()
        result = await kash.trades.buy(
            market=2,
            outcome=0,
            amount_usdc=1_000_000,
            max_slippage_bps=50,
            signer=keypair_signer(trader),
        )
        assert result.plan.signer == trader.pubkey()
        assert result.plan.kind == "buy"
        assert result.signature == str(
            VersionedTransaction.from_bytes(connection.sent[0]).signatures[0]
        )
        assert len(connection.sent) == 1

    async def test_one_call_buy_without_slippage_sends_nothing(self, make_client, trader) -> None:
        kash, connection = make_client()
        with pytest.raises(KashConfigError):
            await kash.trades.buy(
                market=2, outcome=0, amount_usdc=1_000_000, signer=keypair_signer(trader)
            )
        assert connection.sent == []

    async def test_one_call_sell_open_and_close(self, make_client, store, planter, trader) -> None:
        kash, connection = make_client(store)
        signer = keypair_signer(trader)
        opened = await kash.trades.open_position(
            market=2, outcome=0, owner=trader.pubkey(), signer=signer
        )
        assert opened.plan.kind == "open_position"
        planter(store, kash, 2, trader.pubkey(), 0, 10**18)
        sold = await kash.trades.sell(
            market=2, outcome=0, tokens_in_wad=10**18, max_slippage_bps=100, signer=signer
        )
        assert sold.plan.kind == "sell"
        planter(store, kash, 2, trader.pubkey(), 0, 0)
        closed = await kash.trades.close_position(market=2, outcome=0, signer=signer)
        assert closed.plan.kind == "close_position"
        assert len(connection.sent) == 3

    async def test_simulate_reports_a_refusal_as_a_result(self, make_client, trader) -> None:
        kash, connection = make_client(
            simulation=SimulationValue(
                err={"InstructionError": [0, {"Custom": 6001}]}, logs=[], units_consumed=900
            )
        )
        result = await kash.trades.simulate(await self._plan(kash, trader), payer=trader.pubkey())
        assert result.error is not None
        assert result.error.name == "Paused"
        assert result.error.instruction_index == 0
        assert result.units_consumed == 900
        assert connection.simulated[0].message.account_keys[0] == trader.pubkey()

    async def test_simulate_success_carries_logs(self, make_client, trader) -> None:
        kash, _ = make_client()
        result = await kash.trades.simulate(await self._plan(kash, trader), payer=trader.pubkey())
        assert result.error is None
        assert result.logs == ("Program log: ok",)


class TestSigners:
    async def test_a_signer_that_raises_becomes_signer_sign_failed(
        self, make_client, trader
    ) -> None:
        class Refusing:
            pubkey = trader.pubkey()

            async def sign_message(self, message: bytes) -> Signature:
                raise RuntimeError("user rejected")

        kash, connection = make_client()
        plan = await kash.trades.build_buy(
            market=2, outcome=0, amount_usdc=1, min_tokens_out_wad=0, trader=trader.pubkey()
        )
        with pytest.raises(KashSignerError) as exc:
            await kash.trades.send(plan, signer=Refusing())
        assert exc.value.code == ErrorCode.SIGNER_SIGN_FAILED
        assert connection.sent == []

    def test_keypair_signer_names_its_key_and_hides_the_secret(self, trader) -> None:
        signer = keypair_signer(trader)
        assert signer.pubkey == trader.pubkey()
        assert str(trader.pubkey()) in repr(signer)
        assert str(trader) not in repr(signer)
