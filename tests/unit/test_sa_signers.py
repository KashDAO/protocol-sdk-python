"""SA signer adapter contract tests.

Pins:

* ``LocalSigner.sign_user_op_hash`` produces a 65-byte ECDSA
  signature (r/s/v) that recovers the configured EOA address — i.e.
  signs over the EIP-191 prefixed UserOp hash exactly the way
  ``SimpleAccount.validateUserOp`` expects.
* Signing does not block the event loop (run_in_executor offload).
* ``JsonRpcSigner`` lifecycle: ``aclose`` releases the owned
  ``httpx.AsyncClient``; supplying an external ``http_client`` leaves
  it untouched.
* ``JsonRpcSigner.sign_user_op_hash`` round-trips a standard
  JSON-RPC ``personal_sign`` envelope and returns the raw 0x-prefixed
  ECDSA signature.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from kashdao_protocol_sdk import (
    ErrorCode,
    JsonRpcSigner,
    JsonRpcSignerConfig,
    KashConfigError,
    KashSignerError,
    LocalSigner,
    json_rpc_signer,
)

_TEST_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
_USER_OP_HASH = "0x" + "ab" * 32
_RPC_URL = "https://signer.example.com/rpc"


# ---------------------------------------------------------------------------
# LocalSigner
# ---------------------------------------------------------------------------


class TestLocalSigner:
    def test_owner_address_matches_account(self) -> None:
        signer = LocalSigner.from_private_key(_TEST_KEY)
        expected = Account.from_key(_TEST_KEY).address
        assert signer.owner_address == expected

    @pytest.mark.asyncio
    async def test_signature_recovers_to_owner(self) -> None:
        """The 65-byte signature must recover to the configured EOA
        when verified against the EIP-191 prefixed UserOp hash. This is
        what ``SimpleAccount.validateUserOp`` does on-chain.
        """
        signer = LocalSigner.from_private_key(_TEST_KEY)
        signature = await signer.sign_user_op_hash(_USER_OP_HASH)

        message = encode_defunct(hexstr=_USER_OP_HASH)
        recovered = Account.recover_message(message, signature=signature)
        assert recovered.lower() == signer.owner_address.lower()

    @pytest.mark.asyncio
    async def test_signature_is_65_bytes(self) -> None:
        signer = LocalSigner.from_private_key(_TEST_KEY)
        signature = await signer.sign_user_op_hash(_USER_OP_HASH)
        # 0x + 130 hex chars = 65 bytes
        assert signature.startswith("0x")
        assert len(signature) == 2 + 130

    @pytest.mark.asyncio
    async def test_sign_does_not_block_event_loop(self) -> None:
        """Replace the underlying ``_account`` with a fake whose
        ``sign_message`` does ``time.sleep(0.3)``. A concurrent ticker
        must observe ticks during the sign — proving offload-to-executor
        works.
        """

        @dataclass
        class _FakeSigned:
            signature: bytes = b"\x00" * 65

        @dataclass
        class _FakeAccount:
            address: str = "0x" + "ab" * 20

            def sign_message(self, _msg: Any) -> Any:
                time.sleep(0.3)
                return _FakeSigned()

        signer = LocalSigner(_account=_FakeAccount())  # type: ignore[arg-type]

        ticks = 0

        async def ticker(stop_at: float) -> None:
            nonlocal ticks
            loop = asyncio.get_running_loop()
            while loop.time() < stop_at:
                await asyncio.sleep(0.01)
                ticks += 1

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 0.3
        ticker_task = asyncio.create_task(ticker(deadline))
        await signer.sign_user_op_hash(_USER_OP_HASH)
        await ticker_task
        # Without run_in_executor the loop would be starved → 0 ticks.
        # With offload, expect ~30 ticks (10 ms each) over 300 ms.
        assert ticks >= 5, f"event loop blocked during sign — only {ticks} ticks observed"


# ---------------------------------------------------------------------------
# JsonRpcSigner
# ---------------------------------------------------------------------------


class TestJsonRpcSignerLifecycle:
    @pytest.mark.asyncio
    async def test_aclose_closes_owned_client(self) -> None:
        signer = json_rpc_signer(rpc=_RPC_URL, address="0x" + "ab" * 20)
        assert signer._http.is_closed is False
        await signer.aclose()
        assert signer._http.is_closed is True

    @pytest.mark.asyncio
    async def test_async_context_manager_closes(self) -> None:
        async with json_rpc_signer(rpc=_RPC_URL, address="0x" + "ab" * 20) as signer:
            assert signer._http.is_closed is False
        assert signer._http.is_closed is True

    @pytest.mark.asyncio
    async def test_external_client_not_closed(self) -> None:
        external = httpx.AsyncClient()
        signer = json_rpc_signer(rpc=_RPC_URL, address="0x" + "ab" * 20, http_client=external)
        await signer.aclose()
        assert external.is_closed is False
        await external.aclose()


class TestJsonRpcSignerConstructionErrors:
    def test_missing_rpc_raises_kash_config_error(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            json_rpc_signer(address="0x" + "ab" * 20)
        assert exc_info.value.code == ErrorCode.MISSING_SIGNER_CONFIG

    def test_missing_address_raises_kash_config_error(self) -> None:
        with pytest.raises(KashConfigError):
            json_rpc_signer(rpc=_RPC_URL)


class TestJsonRpcSignerSigning:
    @pytest.mark.asyncio
    async def test_personal_sign_round_trip(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_RPC_URL,
            json={"jsonrpc": "2.0", "id": 1, "result": "0x" + "11" * 65},
        )
        async with json_rpc_signer(rpc=_RPC_URL, address="0x" + "ab" * 20) as signer:
            sig = await signer.sign_user_op_hash(_USER_OP_HASH)
            assert sig == "0x" + "11" * 65

        request = httpx_mock.get_request()
        body = request.read()
        assert b'"method":"personal_sign"' in body
        assert _USER_OP_HASH.encode() in body

    @pytest.mark.asyncio
    async def test_invalid_signature_result_is_typed(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_RPC_URL,
            json={"jsonrpc": "2.0", "id": 1, "result": "0xabcd"},  # too short
        )
        async with json_rpc_signer(rpc=_RPC_URL, address="0x" + "ab" * 20) as signer:
            with pytest.raises(KashSignerError) as exc_info:
                await signer.sign_user_op_hash(_USER_OP_HASH)
            assert exc_info.value.code == ErrorCode.SIGNER_RPC_INVALID_RESULT

    @pytest.mark.asyncio
    async def test_rpc_error_envelope_surfaces_typed(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "user rejected"},
            },
        )
        async with json_rpc_signer(rpc=_RPC_URL, address="0x" + "ab" * 20) as signer:
            with pytest.raises(KashSignerError) as exc_info:
                await signer.sign_user_op_hash(_USER_OP_HASH)
            assert exc_info.value.code == ErrorCode.SIGNER_RPC_ERROR


class TestJsonRpcSignerHeadersImmutable:
    def test_headers_become_read_only(self) -> None:
        config = JsonRpcSignerConfig(
            rpc=_RPC_URL,
            address="0x" + "ab" * 20,
            headers={"Authorization": "Bearer x"},
        )
        with pytest.raises(TypeError):
            config.headers["Authorization"] = "tamper"  # type: ignore[index]


class TestJsonRpcSignerExposesSigner:
    """Regression guard against barrel-export drift."""

    def test_class_is_imported_from_barrel(self) -> None:
        assert JsonRpcSigner is not None
