"""``BundlerClient`` JSON-RPC behaviour tests.

Pins the wire shape, error envelope handling, hook firing, and
lifecycle contract for the generic ERC-4337 bundler client. The four
provider presets (Alchemy, Pimlico, Flashbots) are thin wrappers and
get smoke coverage at the bottom.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from kashdao_protocol_sdk import (
    AlchemyBundlerConfig,
    BundlerCallOptions,
    BundlerClientConfig,
    BundlerHealthError,
    BundlerHealthOk,
    BundlerRequestEvent,
    BundlerResponseEvent,
    ErrorCode,
    FlashbotsBundlerConfig,
    KashBundlerError,
    PimlicoBundlerConfig,
    SignedUserOp,
    UnsignedUserOp,
    create_alchemy_bundler_client,
    create_flashbots_bundler_client,
    create_generic_bundler_client,
    create_pimlico_bundler_client,
)

_BUNDLER_URL = "https://bundler.example.com/rpc"


def _config(**overrides: Any) -> BundlerClientConfig:
    base = {"url": _BUNDLER_URL}
    base.update(overrides)
    return BundlerClientConfig(**base)


def _user_op() -> UnsignedUserOp:
    return UnsignedUserOp(
        sender="0x" + "ab" * 20,
        nonce=0,
        callData="0x",
        callGasLimit=100_000,
        verificationGasLimit=100_000,
        preVerificationGas=21_000,
        maxFeePerGas=1_000_000_000,
        maxPriorityFeePerGas=1_000_000_000,
    )


def _signed() -> SignedUserOp:
    return SignedUserOp(user_op=_user_op(), signature="0x" + "00" * 65)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_aclose_closes_owned_client(self) -> None:
        client = create_generic_bundler_client(_config())
        assert client._http.is_closed is False
        await client.aclose()
        assert client._http.is_closed is True

    @pytest.mark.asyncio
    async def test_async_context_manager_closes(self) -> None:
        async with create_generic_bundler_client(_config()) as client:
            assert client._http.is_closed is False
        assert client._http.is_closed is True

    @pytest.mark.asyncio
    async def test_external_client_not_closed(self) -> None:
        external = httpx.AsyncClient()
        client = create_generic_bundler_client(_config(http_client=external))
        await client.aclose()
        assert external.is_closed is False
        await external.aclose()


# ---------------------------------------------------------------------------
# RPC: send / estimate / receipt / chain_id
# ---------------------------------------------------------------------------


class TestSend:
    @pytest.mark.asyncio
    async def test_returns_hash_from_envelope(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_BUNDLER_URL,
            json={"jsonrpc": "2.0", "id": 1, "result": "0x" + "11" * 32},
        )
        client = create_generic_bundler_client(_config())
        try:
            result = await client.send(_signed())
            assert result == "0x" + "11" * 32
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_serializes_user_op_with_camel_case(self, httpx_mock: Any) -> None:
        """Wire shape must use ``camelCase`` keys (callData, callGasLimit, …)
        because that's what the EntryPoint v0.7 standard mandates and what
        every bundler implementation expects.
        """
        httpx_mock.add_response(
            method="POST",
            url=_BUNDLER_URL,
            json={"jsonrpc": "2.0", "id": 1, "result": "0xff" * 16 + "00" * 16},
        )
        client = create_generic_bundler_client(_config())
        try:
            await client.send(_signed())
        finally:
            await client.aclose()

        request = httpx_mock.get_request()
        body = request.read()
        # All required EntryPoint v0.7 fields present in camelCase.
        for key in (
            b'"sender"',
            b'"nonce"',
            b'"callData"',
            b'"callGasLimit"',
            b'"verificationGasLimit"',
            b'"preVerificationGas"',
            b'"maxFeePerGas"',
            b'"maxPriorityFeePerGas"',
            b'"signature"',
        ):
            assert key in body, f"missing required field {key!r} in serialized UserOp"
        # snake_case must NOT leak through.
        for forbidden in (b'"call_data"', b'"call_gas_limit"', b'"max_fee_per_gas"'):
            assert forbidden not in body


class TestEstimateGas:
    @pytest.mark.asyncio
    async def test_decodes_hex_fields(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_BUNDLER_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "preVerificationGas": "0x5208",  # 21000
                    "verificationGasLimit": "0x186a0",  # 100000
                    "callGasLimit": "0xf4240",  # 1000000
                },
            },
        )
        client = create_generic_bundler_client(_config())
        try:
            estimate = await client.estimate_gas(_user_op())
            assert estimate.pre_verification_gas == 21_000
            assert estimate.verification_gas_limit == 100_000
            assert estimate.call_gas_limit == 1_000_000
            assert estimate.paymaster_verification_gas_limit is None
            assert estimate.paymaster_post_op_gas_limit is None
        finally:
            await client.aclose()


class TestGetReceipt:
    @pytest.mark.asyncio
    async def test_null_returns_none(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_BUNDLER_URL,
            json={"jsonrpc": "2.0", "id": 1, "result": None},
        )
        client = create_generic_bundler_client(_config())
        try:
            assert await client.get_receipt("0x" + "ab" * 32) is None
        finally:
            await client.aclose()


class TestChainId:
    @pytest.mark.asyncio
    async def test_decodes_hex_chain_id(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_BUNDLER_URL,
            json={"jsonrpc": "2.0", "id": 1, "result": "0x14a34"},  # 84532
        )
        client = create_generic_bundler_client(_config())
        try:
            assert await client.chain_id() == 84532
        finally:
            await client.aclose()


class TestHealth:
    @pytest.mark.asyncio
    async def test_ok_on_success(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_BUNDLER_URL,
            json={"jsonrpc": "2.0", "id": 1, "result": "0x14a34"},
        )
        client = create_generic_bundler_client(_config())
        try:
            health = await client.health()
            assert isinstance(health, BundlerHealthOk)
            assert health.ok is True
            assert health.chain_id == 84532
            assert health.latency_ms >= 0
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_error_on_failure_does_not_raise(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_BUNDLER_URL,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "rate limited"}},
        )
        client = create_generic_bundler_client(_config())
        try:
            health = await client.health()
            assert isinstance(health, BundlerHealthError)
            assert health.ok is False
            assert isinstance(health.error, KashBundlerError)
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Error envelopes
# ---------------------------------------------------------------------------


class TestErrorEnvelopes:
    @pytest.mark.asyncio
    async def test_rpc_error_surfaces_typed(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_BUNDLER_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "AA10 sender already constructed"},
            },
        )
        client = create_generic_bundler_client(_config())
        try:
            with pytest.raises(KashBundlerError) as exc_info:
                await client.send(_signed())
            assert exc_info.value.code == ErrorCode.BUNDLER_RPC_ERROR
            assert exc_info.value.context is not None
            assert exc_info.value.context["rpc_code"] == -32000
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_http_5xx_is_retryable(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST", url=_BUNDLER_URL, status_code=502, text="bad gateway"
        )
        client = create_generic_bundler_client(_config())
        try:
            with pytest.raises(KashBundlerError) as exc_info:
                await client.send(_signed())
            assert exc_info.value.code == "BUNDLER_HTTP_502"
            assert exc_info.value.is_retryable is True
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_http_4xx_is_not_retryable(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST", url=_BUNDLER_URL, status_code=401, text="unauthorized"
        )
        client = create_generic_bundler_client(_config())
        try:
            with pytest.raises(KashBundlerError) as exc_info:
                await client.send(_signed())
            assert exc_info.value.code == "BUNDLER_HTTP_401"
            assert exc_info.value.is_retryable is False
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_invalid_envelope_surfaces_typed(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(method="POST", url=_BUNDLER_URL, json={"weird": "shape"})
        client = create_generic_bundler_client(_config())
        try:
            with pytest.raises(KashBundlerError) as exc_info:
                await client.send(_signed())
            # Either ENVELOPE or EMPTY_RESULT depending on lenient parser
            # behavior. Both are typed; that's the contract.
            assert exc_info.value.code in {
                ErrorCode.BUNDLER_INVALID_ENVELOPE,
                ErrorCode.BUNDLER_EMPTY_RESULT,
            }
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


class _Hooks:
    def __init__(self) -> None:
        self.requests: list[BundlerRequestEvent] = []
        self.responses: list[BundlerResponseEvent] = []
        self.errors: list[Any] = []
        self.on_bundler_request = self.requests.append
        self.on_bundler_response = self.responses.append
        self.on_bundler_error = self.errors.append
        self.on_signer_request = None
        self.on_signer_error = None
        self.on_wait_orphaned = None


class TestHooks:
    @pytest.mark.asyncio
    async def test_request_response_fired_on_success(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_BUNDLER_URL,
            json={"jsonrpc": "2.0", "id": 1, "result": "0x14a34"},
        )
        hooks = _Hooks()
        client = create_generic_bundler_client(_config(hooks=hooks))
        try:
            await client.chain_id()
        finally:
            await client.aclose()
        assert len(hooks.requests) == 1
        assert hooks.requests[0].method == "eth_chainId"
        assert hooks.requests[0].url == _BUNDLER_URL
        assert len(hooks.responses) == 1
        assert hooks.responses[0].status == 200
        assert len(hooks.errors) == 0

    @pytest.mark.asyncio
    async def test_error_fired_on_failure(self, httpx_mock: Any) -> None:
        httpx_mock.add_response(method="POST", url=_BUNDLER_URL, status_code=500)
        hooks = _Hooks()
        client = create_generic_bundler_client(_config(hooks=hooks))
        try:
            with pytest.raises(KashBundlerError):
                await client.chain_id()
        finally:
            await client.aclose()
        assert len(hooks.errors) == 1


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancellation:
    @pytest.mark.asyncio
    async def test_pre_call_abort_raises(self, httpx_mock: Any) -> None:
        signal = asyncio.Event()
        signal.set()
        client = create_generic_bundler_client(_config())
        try:
            with pytest.raises(Exception) as exc_info:
                await client.chain_id(BundlerCallOptions(signal=signal))
            assert exc_info.value.code == ErrorCode.OPERATION_ABORTED  # type: ignore[attr-defined]
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------


class TestPresets:
    """Each preset is a thin wrapper over the generic client.

    Smoke-checking that they construct and forward configs verbatim is
    sufficient — the real behaviour is exercised by ``TestSend`` etc.
    above against the generic client.
    """

    def test_alchemy_preset(self) -> None:
        client = create_alchemy_bundler_client(AlchemyBundlerConfig(url=_BUNDLER_URL))
        assert client.entry_point_address.startswith("0x")

    def test_pimlico_preset(self) -> None:
        client = create_pimlico_bundler_client(PimlicoBundlerConfig(url=_BUNDLER_URL))
        assert client.entry_point_address.startswith("0x")

    def test_flashbots_preset(self) -> None:
        client = create_flashbots_bundler_client(FlashbotsBundlerConfig(url=_BUNDLER_URL))
        assert client.entry_point_address.startswith("0x")
