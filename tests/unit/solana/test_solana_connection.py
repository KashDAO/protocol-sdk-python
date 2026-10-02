"""``SolanaRpcConnection`` — the JSON-RPC transport, against a mocked HTTP endpoint.

Every request body is asserted (method + params), and every response is the
shape a Solana RPC actually returns, so the normalisation into the
:class:`SolanaConnection` protocol is pinned at the wire. No network.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from kashdao_protocol_sdk.solana import (
    InstructionContext,
    KashChainError,
    KashSimulationRevertedError,
    KashTransactionExpiredError,
    KashTransactionOutcomeUnknownError,
    MarketTarget,
    PreflightFailure,
    SolanaConnection,
    SolanaRpcConnection,
    SolanaRpcError,
    SolanaRpcTransportError,
    create_solana_client,
    keypair_signer,
    open_position_instruction,
)
from kashdao_protocol_sdk.solana.idl import program_error_names

RPC = "https://rpc.example"
OWNER = "Jr8Bd8efPfNYHW65vZrVzeLbYkzy1oo3QB3i8cYdDcy"
BLOCKHASH = "GHtXQBsoZHVnNFa9YevAzFr17DJjgHXk3ycTKD5xD3Zi"


def body(request: httpx.Request) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(request.content)
    return parsed


def ok(result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "result": result}


@pytest.fixture
def rpc() -> SolanaRpcConnection:
    return SolanaRpcConnection(RPC, poll_interval_seconds=0, confirm_timeout_seconds=5)


def test_it_satisfies_the_protocol(rpc) -> None:
    assert isinstance(rpc, SolanaConnection)


async def test_get_multiple_accounts_decodes_base64_and_keeps_absence(rpc, httpx_mock) -> None:
    present, absent = Pubkey.new_unique(), Pubkey.new_unique()
    httpx_mock.add_response(
        url=RPC,
        json=ok(
            {
                "context": {"slot": 1},
                "value": [
                    {
                        "data": [base64.b64encode(b"\x01\x02\x03").decode(), "base64"],
                        "owner": OWNER,
                        "lamports": 1,
                        "executable": False,
                        "rentEpoch": 0,
                    },
                    None,
                ],
            }
        ),
    )
    infos = await rpc.get_multiple_accounts([present, absent], "confirmed")
    assert infos[0] is not None
    assert (str(infos[0].owner), infos[0].data) == (OWNER, b"\x01\x02\x03")
    assert infos[1] is None
    sent = body(httpx_mock.get_request())
    assert sent["method"] == "getMultipleAccounts"
    assert sent["params"] == [
        [str(present), str(absent)],
        {"encoding": "base64", "commitment": "confirmed"},
    ]
    await rpc.aclose()


async def test_get_latest_blockhash(rpc, httpx_mock) -> None:
    httpx_mock.add_response(
        url=RPC,
        json=ok(
            {"context": {"slot": 1}, "value": {"blockhash": BLOCKHASH, "lastValidBlockHeight": 77}}
        ),
    )
    latest = await rpc.get_latest_blockhash("finalized")
    assert latest.blockhash == Hash.from_string(BLOCKHASH)
    assert latest.last_valid_block_height == 77
    assert body(httpx_mock.get_request())["params"] == [{"commitment": "finalized"}]


async def test_send_raw_transaction_posts_base64_with_preflight_options(rpc, httpx_mock) -> None:
    signature = str(Signature.default())
    httpx_mock.add_response(url=RPC, json=ok(signature))
    assert (
        await rpc.send_raw_transaction(
            b"\xff\x00", skip_preflight=False, preflight_commitment="confirmed"
        )
        == signature
    )
    sent = body(httpx_mock.get_request())
    assert sent["method"] == "sendTransaction"
    assert sent["params"] == [
        base64.b64encode(b"\xff\x00").decode(),
        {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"},
    ]


async def test_a_preflight_refusal_raises_preflight_failure_with_logs(rpc, httpx_mock) -> None:
    logs = [f"Program {OWNER} failed: custom program error: 0x1786"]
    httpx_mock.add_response(
        url=RPC,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32002,
                "message": "Transaction simulation failed",
                "data": {"err": {"InstructionError": [1, {"Custom": 6022}]}, "logs": logs},
            },
        },
    )
    with pytest.raises(PreflightFailure) as exc:
        await rpc.send_raw_transaction(
            b"\x00", skip_preflight=False, preflight_commitment="confirmed"
        )
    assert exc.value.logs == logs


async def test_any_other_rpc_error_is_a_solana_rpc_error(rpc, httpx_mock) -> None:
    httpx_mock.add_response(
        url=RPC, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "busy"}}
    )
    with pytest.raises(SolanaRpcError) as exc:
        await rpc.get_latest_blockhash("confirmed")
    assert exc.value.code == -32005
    assert not isinstance(exc.value, PreflightFailure)


async def test_an_envelope_with_neither_result_nor_error_is_no_definite_answer(
    rpc, httpx_mock
) -> None:
    httpx_mock.add_response(url=RPC, json={"jsonrpc": "2.0", "id": 1})
    with pytest.raises(SolanaRpcTransportError, match="neither result nor error"):
        await rpc.get_latest_blockhash("confirmed")


def status(
    confirmation: str | None, err: object = None, slot: int = 9, context_slot: int = 5
) -> dict[str, Any]:
    value = (
        None
        if confirmation is None
        else {"slot": slot, "confirmationStatus": confirmation, "err": err}
    )
    return ok({"context": {"slot": context_slot}, "value": [value]})


def epoch(block_height: int, absolute_slot: int = 5) -> dict[str, Any]:
    """A getEpochInfo answer: the block height and the slot it was observed at."""
    return ok(
        {
            "absoluteSlot": absolute_slot,
            "blockHeight": block_height,
            "epoch": 1,
            "slotIndex": 0,
            "slotsInEpoch": 432_000,
            "transactionCount": 0,
        }
    )


def methods(httpx_mock) -> list[str]:
    return [body(r)["method"] for r in httpx_mock.get_requests()]


async def test_confirm_polls_until_the_commitment_is_reached(rpc, httpx_mock) -> None:
    httpx_mock.add_response(url=RPC, json=status(None))
    httpx_mock.add_response(url=RPC, json=epoch(10))
    httpx_mock.add_response(url=RPC, json=status("processed"))
    httpx_mock.add_response(url=RPC, json=status("confirmed"))
    confirmation = await rpc.confirm_transaction(
        str(Signature.default()), last_valid_block_height=100, commitment="confirmed"
    )
    assert (confirmation.slot, confirmation.err) == (9, None)
    # Once the signature is seen on chain, the block height no longer matters.
    assert methods(httpx_mock) == [
        "getSignatureStatuses",
        "getEpochInfo",
        "getSignatureStatuses",
        "getSignatureStatuses",
    ]
    assert body(httpx_mock.get_requests()[0])["params"][1] == {"searchTransactionHistory": True}


async def test_confirm_returns_a_landed_failure_immediately(rpc, httpx_mock) -> None:
    err = {"InstructionError": [1, {"Custom": 6001}]}
    httpx_mock.add_response(
        url=RPC,
        json=ok(
            {
                "context": {"slot": 5},
                "value": [{"slot": 9, "confirmationStatus": "processed", "err": err}],
            }
        ),
    )
    confirmation = await rpc.confirm_transaction(
        str(Signature.default()), last_valid_block_height=100, commitment="finalized"
    )
    assert confirmation.err == err


async def test_expiry_rechecks_the_status_once_before_declaring_did_not_land(
    rpc, httpx_mock
) -> None:
    httpx_mock.add_response(url=RPC, json=status(None))
    httpx_mock.add_response(url=RPC, json=epoch(101))
    httpx_mock.add_response(url=RPC, json=status(None))
    with pytest.raises(KashTransactionExpiredError) as exc:
        await rpc.confirm_transaction(
            str(Signature.default()), last_valid_block_height=100, commitment="confirmed"
        )
    assert exc.value.signature == str(Signature.default())
    assert methods(httpx_mock) == ["getSignatureStatuses", "getEpochInfo", "getSignatureStatuses"]


async def test_a_no_trace_read_from_a_node_behind_the_expiry_slot_proves_nothing(
    rpc, httpx_mock
) -> None:
    """Load-balanced RPC: the silence must come from a node at/after the expiry slot."""
    httpx_mock.add_response(url=RPC, json=status(None, context_slot=40))
    httpx_mock.add_response(url=RPC, json=epoch(101, absolute_slot=50))
    httpx_mock.add_response(url=RPC, json=status(None, context_slot=49))  # behind: ignored
    httpx_mock.add_response(url=RPC, json=status(None, context_slot=50))  # at the slot: decisive
    with pytest.raises(KashTransactionExpiredError):
        await rpc.confirm_transaction(
            str(Signature.default()), last_valid_block_height=100, commitment="confirmed"
        )
    assert methods(httpx_mock) == [
        "getSignatureStatuses",
        "getEpochInfo",
        "getSignatureStatuses",
        "getSignatureStatuses",
    ]


async def test_only_lagging_reads_until_the_deadline_is_could_not_be_read(httpx_mock) -> None:
    rpc = SolanaRpcConnection(RPC, poll_interval_seconds=0, confirm_timeout_seconds=0)
    httpx_mock.add_response(url=RPC, json=status(None, context_slot=40))
    httpx_mock.add_response(url=RPC, json=epoch(101, absolute_slot=50))
    httpx_mock.add_response(url=RPC, json=status(None, context_slot=49))
    with pytest.raises(SolanaRpcTransportError):
        await rpc.confirm_transaction(
            str(Signature.default()), last_valid_block_height=100, commitment="confirmed"
        )


async def test_a_signature_that_appears_at_expiry_landed_and_is_followed(rpc, httpx_mock) -> None:
    httpx_mock.add_response(url=RPC, json=status(None))
    httpx_mock.add_response(url=RPC, json=epoch(101))
    httpx_mock.add_response(url=RPC, json=status("processed", slot=99))
    httpx_mock.add_response(url=RPC, json=status("confirmed", slot=99))
    confirmation = await rpc.confirm_transaction(
        str(Signature.default()), last_valid_block_height=100, commitment="confirmed"
    )
    assert confirmation.slot == 99


async def test_a_failure_that_appears_at_expiry_is_reported_not_expired(rpc, httpx_mock) -> None:
    err = {"InstructionError": [0, {"Custom": 6001}]}
    httpx_mock.add_response(url=RPC, json=status(None))
    httpx_mock.add_response(url=RPC, json=epoch(101))
    httpx_mock.add_response(url=RPC, json=status("processed", err=err))
    confirmation = await rpc.confirm_transaction(
        str(Signature.default()), last_valid_block_height=100, commitment="confirmed"
    )
    assert confirmation.err == err


@pytest.mark.parametrize(
    "transient",
    [
        {"status_code": 429},
        {"status_code": 503},
        {"status_code": 502},
        {"exception": httpx.ReadTimeout("slow")},
        {"exception": httpx.ConnectError("reset")},
        {"text": "<html>bad gateway</html>"},
    ],
    ids=["429", "503", "502", "timeout", "connect-error", "not-json"],
)
async def test_confirm_backs_off_through_transient_failures(rpc, httpx_mock, transient) -> None:
    if "exception" in transient:
        httpx_mock.add_exception(transient["exception"], url=RPC)
    else:
        httpx_mock.add_response(url=RPC, **transient)
    httpx_mock.add_response(url=RPC, json=status("confirmed"))
    confirmation = await rpc.confirm_transaction(
        str(Signature.default()), last_valid_block_height=100, commitment="confirmed"
    )
    assert confirmation.slot == 9


async def test_confirm_gives_up_on_transient_failures_at_the_deadline(httpx_mock) -> None:
    rpc = SolanaRpcConnection(RPC, poll_interval_seconds=0, confirm_timeout_seconds=0)
    httpx_mock.add_response(url=RPC, status_code=503)
    with pytest.raises(SolanaRpcTransportError):
        await rpc.confirm_transaction(
            str(Signature.default()), last_valid_block_height=100, commitment="confirmed"
        )


async def test_confirm_does_not_retry_a_definite_rpc_error(rpc, httpx_mock) -> None:
    httpx_mock.add_response(
        url=RPC, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad"}}
    )
    with pytest.raises(SolanaRpcError):
        await rpc.confirm_transaction(
            str(Signature.default()), last_valid_block_height=100, commitment="confirmed"
        )
    assert len(httpx_mock.get_requests()) == 1


async def test_simulate_skips_sig_verify_and_replaces_the_blockhash(rpc, httpx_mock) -> None:
    payer = Keypair()
    message = MessageV0.try_compile(payer.pubkey(), [], [], Hash.from_string(BLOCKHASH))
    tx = VersionedTransaction.populate(message, [Signature.default()])
    httpx_mock.add_response(
        url=RPC,
        json=ok(
            {
                "context": {"slot": 3},
                "value": {
                    "err": {"InstructionError": [0, {"Custom": 6001}]},
                    "logs": ["Program log: no"],
                    "unitsConsumed": 900,
                },
            }
        ),
    )
    value = await rpc.simulate_transaction(tx, commitment="confirmed")
    assert value.err == {"InstructionError": [0, {"Custom": 6001}]}
    assert (value.logs, value.units_consumed) == (["Program log: no"], 900)
    sent = body(httpx_mock.get_request())
    assert sent["method"] == "simulateTransaction"
    assert sent["params"][1] == {
        "encoding": "base64",
        "sigVerify": False,
        "replaceRecentBlockhash": True,
        "commitment": "confirmed",
    }
    assert base64.b64decode(sent["params"][0]) == bytes(tx)


async def test_an_http_failure_reaches_the_client_as_a_retryable_read_error(httpx_mock) -> None:
    httpx_mock.add_response(url=RPC, status_code=503)
    async with create_solana_client(rpc_url=RPC) as kash:
        with pytest.raises(KashChainError) as exc:
            await kash.account.usdc_balance(Keypair().pubkey())
    assert exc.value.code == "ACCOUNT_READ_FAILED"
    assert exc.value.is_retryable is True


async def test_end_to_end_send_through_the_rpc_maps_a_preflight_refusal(httpx_mock) -> None:
    """The client and the transport together: a real -32002 becomes a decoded refusal."""
    trader = Keypair()
    kash = create_solana_client(
        connection=SolanaRpcConnection(RPC, poll_interval_seconds=0, confirm_timeout_seconds=5)
    )
    ctx = InstructionContext(program_id=kash.program_id, usdc_mint=kash.usdc_mint)
    ix = open_position_instruction(
        ctx,
        MarketTarget(market_id=2, market=kash.pdas.market(2).address),
        owner=trader.pubkey(),
        outcome=0,
        payer=trader.pubkey(),
    )
    httpx_mock.add_response(
        url=RPC,
        json=ok(
            {"context": {"slot": 1}, "value": {"blockhash": BLOCKHASH, "lastValidBlockHeight": 77}}
        ),
    )
    httpx_mock.add_response(
        url=RPC,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "error": {
                "code": -32002,
                "message": "Transaction simulation failed",
                "data": {"logs": [f"Program {OWNER} failed: custom program error: 0x1771"]},
            },
        },
    )
    with pytest.raises(KashSimulationRevertedError) as exc:
        await kash.trades.send([ix], signer=keypair_signer(trader))
    assert exc.value.context is not None
    assert exc.value.context["program_error"] == program_error_names()[0x1771]
    sent = VersionedTransaction.from_bytes(
        base64.b64decode(body(httpx_mock.get_requests()[1])["params"][0])
    )
    assert sent.verify_with_results() == [True]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ({"exception": httpx.ReadTimeout("slow")}, SolanaRpcTransportError),
        ({"exception": httpx.ConnectError("reset by peer")}, SolanaRpcTransportError),
        ({"status_code": 500}, SolanaRpcTransportError),
        ({"status_code": 502}, SolanaRpcTransportError),
        ({"text": "not json"}, SolanaRpcTransportError),
        ({"json": ["not", "an", "envelope"]}, SolanaRpcTransportError),
        ({"status_code": 429}, SolanaRpcError),
        ({"status_code": 400}, SolanaRpcError),
        (
            {"json": {"jsonrpc": "2.0", "id": 1, "error": {"code": -32003, "message": "no"}}},
            SolanaRpcError,
        ),
    ],
    ids=["timeout", "reset", "500", "502", "not-json", "not-envelope", "429", "400", "rpc-error"],
)
async def test_send_failures_split_into_definite_refusals_and_unknown_outcomes(
    rpc, httpx_mock, failure, expected
) -> None:
    if "exception" in failure:
        httpx_mock.add_exception(failure["exception"], url=RPC)
    else:
        httpx_mock.add_response(url=RPC, **failure)
    with pytest.raises(expected) as exc:
        await rpc.send_raw_transaction(
            b"\x00", skip_preflight=False, preflight_commitment="confirmed"
        )
    # The two classes must never overlap: the client's money safety rests on it.
    assert isinstance(exc.value, SolanaRpcError) != isinstance(exc.value, SolanaRpcTransportError)


def _open_position(kash, trader):
    ctx = InstructionContext(program_id=kash.program_id, usdc_mint=kash.usdc_mint)
    return open_position_instruction(
        ctx,
        MarketTarget(market_id=2, market=kash.pdas.market(2).address),
        owner=trader.pubkey(),
        outcome=0,
        payer=trader.pubkey(),
    )


@pytest.mark.parametrize(
    "failure",
    [
        {"exception": httpx.ReadTimeout("slow")},
        {"exception": httpx.ConnectError("reset")},
        {"status_code": 503},
        {"text": "not json"},
    ],
    ids=["timeout", "reset", "503", "not-json"],
)
async def test_a_send_with_no_definite_answer_is_may_have_landed_with_the_signature(
    httpx_mock, failure
) -> None:
    """The double-trade guard: never retryable, and the signature is the SENT one."""
    trader = Keypair()
    kash = create_solana_client(connection=SolanaRpcConnection(RPC, confirm_timeout_seconds=5))
    httpx_mock.add_response(
        url=RPC,
        json=ok(
            {"context": {"slot": 1}, "value": {"blockhash": BLOCKHASH, "lastValidBlockHeight": 77}}
        ),
    )
    if "exception" in failure:
        httpx_mock.add_exception(failure["exception"], url=RPC)
    else:
        httpx_mock.add_response(url=RPC, **failure)
    with pytest.raises(KashChainError) as exc:
        await kash.trades.send([_open_position(kash, trader)], signer=keypair_signer(trader))
    assert exc.value.code == "WAIT_RECEIPT_FAILED"
    assert exc.value.is_retryable is False
    sent = VersionedTransaction.from_bytes(
        base64.b64decode(body(httpx_mock.get_requests()[1])["params"][0])
    )
    assert exc.value.context == {"signature": str(sent.signatures[0])}


def _rpc_error(code: int, data: object = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": "refused"}
    if data is not None:
        error["data"] = data
    return {"json": {"jsonrpc": "2.0", "id": 2, "error": error}}


async def _send_through(httpx_mock, answer: dict[str, Any]):
    trader = Keypair()
    kash = create_solana_client(connection=SolanaRpcConnection(RPC, confirm_timeout_seconds=5))
    httpx_mock.add_response(
        url=RPC,
        json=ok(
            {"context": {"slot": 1}, "value": {"blockhash": BLOCKHASH, "lastValidBlockHeight": 77}}
        ),
    )
    httpx_mock.add_response(url=RPC, **answer)
    with pytest.raises(KashChainError) as exc:
        await kash.trades.send([_open_position(kash, trader)], signer=keypair_signer(trader))
    sent = VersionedTransaction.from_bytes(
        base64.b64decode(body(httpx_mock.get_requests()[1])["params"][0])
    )
    return exc.value, str(sent.signatures[0])


@pytest.mark.parametrize(
    "answer",
    [
        {"status_code": 429},
        {"status_code": 408},
        {"status_code": 400},
        _rpc_error(-32603),
        _rpc_error(-32005),
        _rpc_error(-32429),
    ],
    ids=["429", "408", "400", "-32603", "-32005", "vendor"],
)
async def test_send_path_http_and_provider_errors_may_have_landed(httpx_mock, answer) -> None:
    err, signature = await _send_through(httpx_mock, answer)
    assert isinstance(err, KashTransactionOutcomeUnknownError)
    assert err.code == "WAIT_RECEIPT_FAILED"
    assert err.is_retryable is False
    assert err.signature == signature


@pytest.mark.parametrize(
    "answer",
    [
        _rpc_error(-32003),
        _rpc_error(-32602),
        _rpc_error(-32002, {"err": "BlockhashNotFound", "logs": []}),
    ],
    ids=["-32003", "-32602", "blockhash-not-found"],
)
async def test_only_pre_forward_refusals_are_retryable(httpx_mock, answer) -> None:
    err, signature = await _send_through(httpx_mock, answer)
    assert err.code == "TX_SEND_FAILED"
    assert err.is_retryable is True
    assert err.context is not None
    assert err.context["signature"] == signature


async def test_already_processed_through_the_rpc_is_confirmed_not_refused(httpx_mock) -> None:
    trader = Keypair()
    kash = create_solana_client(
        connection=SolanaRpcConnection(RPC, poll_interval_seconds=0, confirm_timeout_seconds=5)
    )
    httpx_mock.add_response(
        url=RPC,
        json=ok(
            {"context": {"slot": 1}, "value": {"blockhash": BLOCKHASH, "lastValidBlockHeight": 77}}
        ),
    )
    httpx_mock.add_response(url=RPC, **_rpc_error(-32002, {"err": "AlreadyProcessed", "logs": []}))
    httpx_mock.add_response(url=RPC, json=status("confirmed", slot=31))
    result = await kash.trades.send([_open_position(kash, trader)], signer=keypair_signer(trader))
    assert result.slot == 31
    assert methods(httpx_mock)[-1] == "getSignatureStatuses"


@pytest.mark.parametrize(
    "refusal",
    [{"status_code": 429}],
    ids=["429"],
)
async def test_a_send_path_429_is_never_a_refusal(httpx_mock, refusal) -> None:
    trader = Keypair()
    kash = create_solana_client(connection=SolanaRpcConnection(RPC, confirm_timeout_seconds=5))
    httpx_mock.add_response(
        url=RPC,
        json=ok(
            {"context": {"slot": 1}, "value": {"blockhash": BLOCKHASH, "lastValidBlockHeight": 77}}
        ),
    )
    httpx_mock.add_response(url=RPC, **refusal)
    with pytest.raises(KashTransactionOutcomeUnknownError) as exc:
        await kash.trades.send([_open_position(kash, trader)], signer=keypair_signer(trader))
    assert exc.value.is_retryable is False


async def test_an_expired_blockhash_through_the_rpc_is_tx_expired(httpx_mock) -> None:
    trader = Keypair()
    kash = create_solana_client(
        connection=SolanaRpcConnection(RPC, poll_interval_seconds=0, confirm_timeout_seconds=5)
    )
    httpx_mock.add_response(
        url=RPC,
        json=ok(
            {"context": {"slot": 1}, "value": {"blockhash": BLOCKHASH, "lastValidBlockHeight": 77}}
        ),
    )
    httpx_mock.add_response(url=RPC, json=ok(str(Signature.default())))
    httpx_mock.add_response(url=RPC, json=status(None))
    httpx_mock.add_response(url=RPC, json=epoch(78))
    httpx_mock.add_response(url=RPC, json=status(None))
    with pytest.raises(KashTransactionExpiredError) as exc:
        await kash.trades.send([_open_position(kash, trader)], signer=keypair_signer(trader))
    assert exc.value.code == "TX_EXPIRED"
    assert exc.value.is_retryable is True
