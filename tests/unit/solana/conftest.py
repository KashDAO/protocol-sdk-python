"""Shared fixtures for the Solana tests.

``fixtures/mainnet-accounts.json`` is the TypeScript SDK's committed mainnet
snapshot (``packages/protocol-sdk/tests/unit/solana/fixtures``, written by
``scripts/generate-solana-fixtures.ts`` from ONE read-only
``getMultipleAccounts`` call at slot 452508961), copied byte for byte so both
SDKs are pinned to the same chain state. ``fixtures/curve-parity.jsonl`` is the
protocol's Python reference-model corpus (``@kashdao/pythag-math``).

Nothing here touches the network: :class:`FakeConnection` serves the snapshot.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from kashdao_protocol_sdk.solana import (
    AccountInfo,
    Commitment,
    Confirmation,
    LatestBlockhash,
    SimulationValue,
    SolanaClient,
    create_solana_client,
)
from kashdao_protocol_sdk.solana.idl import encode_account

FIXTURES = Path(__file__).parent / "fixtures"
MAINNET: dict[str, Any] = json.loads((FIXTURES / "mainnet-accounts.json").read_text("utf-8"))

#: Before every fixture market's freeze_time.
NOW = 1_790_950_000
SIGNATURE = "5ignature1111111111111111111111111111111111111111"
BLOCKHASH = "GHtXQBsoZHVnNFa9YevAzFr17DJjgHXk3ycTKD5xD3Zi"


def fixture_account(name: str) -> dict[str, Any]:
    for account in MAINNET["accounts"]:
        if account["name"] == name:
            found: dict[str, Any] = account
            return found
    raise KeyError(f"fixture has no account named {name}")


def fixture_data(name: str) -> bytes:
    account = fixture_account(name)["account"]
    if account is None:
        raise KeyError(f"fixture account {name} is absent on chain")
    return base64.b64decode(account["data"])


AccountStore = dict[str, AccountInfo]


def mainnet_store() -> AccountStore:
    store: AccountStore = {}
    for account in MAINNET["accounts"]:
        if account["account"] is None:
            continue
        store[account["address"]] = AccountInfo(
            owner=Pubkey.from_string(account["account"]["owner"]),
            data=base64.b64decode(account["account"]["data"]),
        )
    return store


@dataclass
class FakeConnection:
    """A :class:`SolanaConnection` over an in-memory account store.

    Send / confirm / simulate are overridable per test; by default sending
    succeeds and confirms cleanly.
    """

    store: AccountStore
    reads: list[list[Pubkey]] = field(default_factory=list)
    sent: list[bytes] = field(default_factory=list)
    simulated: list[VersionedTransaction] = field(default_factory=list)
    read_error: Exception | None = None
    send_error: Exception | None = None
    confirm_error: Exception | None = None
    confirm_err: object | None = None
    simulation: SimulationValue = field(
        default_factory=lambda: SimulationValue(
            err=None, logs=["Program log: ok"], units_consumed=1234
        )
    )

    async def get_multiple_accounts(
        self, addresses: Sequence[Pubkey], commitment: Commitment
    ) -> list[AccountInfo | None]:
        self.reads.append(list(addresses))
        if self.read_error is not None:
            raise self.read_error
        return [self.store.get(str(a)) for a in addresses]

    async def get_latest_blockhash(self, commitment: Commitment) -> LatestBlockhash:
        return LatestBlockhash(blockhash=Hash.from_string(BLOCKHASH), last_valid_block_height=1_000)

    async def send_raw_transaction(
        self, raw: bytes, *, skip_preflight: bool, preflight_commitment: Commitment
    ) -> str:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(raw)
        return SIGNATURE

    async def confirm_transaction(
        self, signature: str, *, last_valid_block_height: int, commitment: Commitment
    ) -> Confirmation:
        if self.confirm_error is not None:
            raise self.confirm_error
        return Confirmation(slot=42, err=self.confirm_err)

    async def simulate_transaction(
        self, transaction: VersionedTransaction, *, commitment: Commitment
    ) -> SimulationValue:
        self.simulated.append(transaction)
        return self.simulation


def plant_position(
    store: AccountStore,
    kash: SolanaClient,
    market_id: int,
    owner: Pubkey,
    outcome: int,
    balance: int,
    rent_payer: Pubkey | None = None,
) -> None:
    """Plant a Position account (balance in WAD) for ``owner``, encoded through the IDL."""
    market = kash.pdas.market(market_id).address
    pda = kash.pdas.position(market, owner, outcome)
    data = encode_account(
        "Position",
        {
            "market": bytes(market),
            "owner": bytes(owner),
            "rent_payer": bytes(rent_payer or owner),
            "balance": balance.to_bytes(16, "little"),
            "outcome": outcome,
            "bump": pda.bump,
            "_pad": bytes(6),
        },
    )
    store[str(pda.address)] = AccountInfo(owner=kash.program_id, data=data)


@pytest.fixture
def store() -> AccountStore:
    return mainnet_store()


@pytest.fixture
def trader() -> Keypair:
    return Keypair()


@pytest.fixture
def account_data() -> Callable[[str], bytes]:
    """Raw data of a snapshot account by fixture name (``market:2``, ``config`` …)."""
    return fixture_data


@pytest.fixture
def snapshot_account() -> Callable[[str], dict[str, Any]]:
    """A snapshot entry (``name``, ``address``, ``account``) by fixture name."""
    return fixture_account


@pytest.fixture
def planter() -> Callable[..., None]:
    return plant_position


@pytest.fixture
def make_client() -> Callable[..., tuple[SolanaClient, FakeConnection]]:
    def build(
        store: AccountStore | None = None, now: int = NOW, **overrides: Any
    ) -> tuple[SolanaClient, FakeConnection]:
        connection = FakeConnection(store if store is not None else mainnet_store(), **overrides)
        return create_solana_client(connection=connection, now=lambda: now), connection

    return build
