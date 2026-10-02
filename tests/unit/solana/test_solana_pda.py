"""PDA derivation against MAINNET, not against itself.

The snapshot is what actually lives at each derived address on mainnet-beta.
An account existing at the derived address, owned by the Kash program and
decoding as the expected kind is the proof that the derivation matches the
chain. The literal addresses are pinned too (the same literals the TS SDK's
``pda.test.ts`` pins), so a seed change fails here even before anyone
regenerates the snapshot.
"""

from __future__ import annotations

import re

import pytest
from solders.pubkey import Pubkey

from kashdao_protocol_sdk.solana import (
    SOLANA_CLUSTERS,
    CustomSolanaDeployment,
    KashConfigError,
    KashValidationError,
    get_solana_deployment,
    kash_market_pdas,
)
from kashdao_protocol_sdk.solana.accounts import (
    decode_config_account,
    decode_market_account,
    decode_template_account,
)
from kashdao_protocol_sdk.solana.idl import kash_market_idl

MAINNET_PROGRAM = "Jr8Bd8efPfNYHW65vZrVzeLbYkzy1oo3QB3i8cYdDcy"
MAINNET_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
IDL_CANONICAL_PROGRAM = "4WFoPLragac369ctiiJMoSkrd2y9WL4LGH4vuX9hWs1K"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

mainnet = get_solana_deployment("mainnet-beta")
pdas = kash_market_pdas(mainnet.program_id)


class TestRegistry:
    def test_mainnet_is_the_deployed_program_not_the_idl_canonical_id(self) -> None:
        assert str(mainnet.program_id) == MAINNET_PROGRAM
        assert str(mainnet.usdc_mint) == MAINNET_USDC
        assert kash_market_idl()["address"] == IDL_CANONICAL_PROGRAM
        assert str(mainnet.program_id) != kash_market_idl()["address"]

    def test_mainnet_is_the_default(self) -> None:
        assert get_solana_deployment() == mainnet
        assert mainnet.cluster == "mainnet-beta"

    def test_devnet_has_its_own_program_and_mint(self) -> None:
        assert SOLANA_CLUSTERS == ("mainnet-beta", "devnet")
        devnet = get_solana_deployment("devnet")
        assert str(devnet.program_id) == "J3tSyyhaeokZc9VnogXR9anQMeA9FjKn8fnnjrLTkuN5"
        assert str(devnet.usdc_mint) == "6xNqPRTd9V71N8KBp1X3x87rEA2sLiLsDqU7gQ4MuwBe"

    def test_refuses_an_undeployed_cluster_and_a_malformed_custom_address(self) -> None:
        with pytest.raises(KashConfigError) as exc:
            get_solana_deployment("testnet")  # type: ignore[arg-type]
        assert exc.value.code == "UNSUPPORTED_CHAIN"
        with pytest.raises(KashConfigError, match="program_id is not a valid Solana address"):
            get_solana_deployment(CustomSolanaDeployment(program_id="nope", usdc_mint=MAINNET_USDC))
        custom = get_solana_deployment(
            CustomSolanaDeployment(program_id=IDL_CANONICAL_PROGRAM, usdc_mint=mainnet.usdc_mint)
        )
        assert custom.cluster == "custom"
        assert str(custom.program_id) == IDL_CANONICAL_PROGRAM


PINNED = [
    ("config", lambda: pdas.config().address, "BafXkWXNvhbW1fbtAgXfYVL1JUQG8QaxEoEq9aEV9x6Y"),
    ("counter", lambda: pdas.counter().address, "AHPXrKoKTULRJZoCHnRf1ApUXDddT1bmMnRKn4bH4AuN"),
    (
        "template:0",
        lambda: pdas.template(0).address,
        "HeLwsTUNufkaXbtywqzSZkL2c5gNv6dpdzSatrtxuiF9",
    ),
    (
        "template:1",
        lambda: pdas.template(1).address,
        "FkVCY6MvXbTqDV7G5e3sbqZ12Bh57s7sW3QqUsYRP8yT",
    ),
    (
        "template:2",
        lambda: pdas.template(2).address,
        "FDaJ4xPPzygUwVUSgF3By2REDq4Rc1o5pRi2PnvMjiZ3",
    ),
    (
        "template:3",
        lambda: pdas.template(3).address,
        "48Srp5yKfDweugTydH2wLj8v6zF4A38oqyfPb5SjQYmc",
    ),
    ("market:0", lambda: pdas.market(0).address, "F4araasrXLdv5g5qTHURN9ZX8ojeWRpkQzQ88KhXoWxR"),
    (
        "collateral:0",
        lambda: pdas.collateral(0).address,
        "5uqbfuu1rXXVgtEcLYqDkVL2ajSXhhU2umQfrADBraKH",
    ),
    ("market:3", lambda: pdas.market(3).address, "7U4yEJdfVzEh6VW8DwsdRnu548Dhgk1Uj7RfCgiKrR3p"),
    (
        "collateral:3",
        lambda: pdas.collateral(3).address,
        "7JCN57cPek6f72aoBG5w7HtvD3Voane6tK5R2CEdQ3Qp",
    ),
]


class TestDerivationMatchesMainnet:
    @pytest.mark.parametrize(("name", "derive", "pinned"), PINNED, ids=[p[0] for p in PINNED])
    def test_derives_to_the_pinned_mainnet_address(
        self, name, derive, pinned, snapshot_account
    ) -> None:
        assert str(derive()) == pinned
        assert snapshot_account(name)["address"] == pinned

    def test_every_probed_program_account_exists_owned_by_the_kash_program(
        self, snapshot_account
    ) -> None:
        names = ["config", "counter"] + [f"template:{i}" for i in range(4)]
        names += [f"market:{i}" for i in range(4)]
        assert len(names) == 10
        for name in names:
            assert snapshot_account(name)["account"]["owner"] == MAINNET_PROGRAM, name
        # Collateral accounts are SPL token accounts the program owns BY AUTHORITY.
        for i in range(4):
            assert snapshot_account(f"collateral:{i}")["account"]["owner"] == TOKEN_PROGRAM

    def test_the_derived_config_decodes_with_template_count_4(self, account_data) -> None:
        config = decode_config_account(account_data("config"))
        assert config.template_count == 4
        assert config.default_protocol_fee_bps == 100
        assert config.paused is False

    def test_templates_0_to_3_exist_and_carry_their_ids_and_4_does_not(
        self, account_data, snapshot_account
    ) -> None:
        for template_id in range(4):
            template = decode_template_account(account_data(f"template:{template_id}"))
            assert template.id == template_id
            assert template.offered is True
        assert snapshot_account("template:4")["account"] is None
        assert str(pdas.template(4).address) == snapshot_account("template:4")["address"]

    def test_each_market_pda_holds_its_id_and_points_at_the_derived_collateral(
        self, account_data
    ) -> None:
        for market_id in range(4):
            market = decode_market_account(account_data(f"market:{market_id}"))
            assert market.market_id == market_id
            assert market.collateral_account == pdas.collateral(market_id).address

    def test_position_pdas_are_distinct_per_owner_and_outcome_and_stable(self) -> None:
        market = pdas.market(0).address
        alice = Pubkey.from_string("CrMb7wU8uDcoHtwvPYXtLkpsM3AtEuLXGAoXGqLnRBqg")
        bob = Pubkey.from_string("6Az6KtiDt5ZNMzgxvzE7K2nwrEum9Ry7Kmdjnzv3PsMw")
        seen = {str(pdas.position(market, o, k).address) for o in (alice, bob) for k in (0, 1, 9)}
        assert len(seen) == 6
        assert pdas.position(market, alice, 1) == pdas.position(market, alice, 1)

    def test_event_authority_is_the_anchor_seed(self) -> None:
        expected, _ = Pubkey.find_program_address([b"__event_authority"], mainnet.program_id)
        assert pdas.event_authority().address == expected

    @pytest.mark.parametrize(
        ("derive", "message"),
        [
            (lambda: pdas.market(-1), "u64 out of range"),
            (lambda: pdas.market(2**64), "u64 out of range"),
            (lambda: pdas.collateral(2**64), "u64 out of range"),
            (lambda: pdas.template(70_000), "u16 out of range"),
            (lambda: pdas.position(pdas.market(0).address, mainnet.program_id, 256), "outcome"),
        ],
    )
    def test_refuses_ids_outside_the_seed_width_rather_than_wrapping(self, derive, message) -> None:
        with pytest.raises(KashValidationError, match=re.escape(message)):
            derive()
