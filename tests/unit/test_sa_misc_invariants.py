"""Miscellaneous SA-mode invariants pinned by regression tests.

The four test classes pin:

* ``BuildOptions.nonce_key`` is a separate field, NOT inferred from
  ``salt``. Salt drives CREATE2 address derivation; nonce_key drives
  EntryPoint nonce stream selection.
* ``PrepareUserOpOptions.simulate`` is tri-state (``bool | None``).
  ``send.*`` resolves ``None`` -> ``True`` (TS parity); only explicit
  ``False`` is treated as opt-out.
* :class:`_LazyFailBundlerClient` raises typed
  :class:`KashConfigError(INVALID_CONFIG, missing="bundler")` on every
  method, NOT a retryable :class:`KashBundlerError(BUNDLER_NETWORK_ERROR)`
  from a placeholder URL.
* ``PrepareOptions`` and ``SendOptions`` (dead Pydantic models) are
  removed from the smart_account barrel. Only the live
  ``PrepareUserOpOptions`` ships.
"""

from __future__ import annotations

import pytest

from kashdao_protocol_sdk import (
    BuildOptions,
    ErrorCode,
    KashConfigError,
    LocalSigner,
    PrepareUserOpOptions,
    create_smart_account_client,
)
from kashdao_protocol_sdk.smart_account.trades.send import _with_simulate

_TEST_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _signer() -> LocalSigner:
    return LocalSigner.from_private_key(_TEST_KEY)


# ---------------------------------------------------------------------------
# nonce_key vs salt
# ---------------------------------------------------------------------------


class TestNonceKeyDistinctFromSalt:
    def test_build_options_has_nonce_key(self) -> None:
        opts = BuildOptions(nonce_key=42)
        assert opts.nonce_key == 42

    def test_build_options_default_nonce_key_zero(self) -> None:
        assert BuildOptions().nonce_key == 0

    def test_build_options_does_not_have_salt_field(self) -> None:
        """``salt`` belongs on ``PrepareUserOpOptions`` (CREATE2 address
        derivation), not on ``BuildOptions``. ``BuildOptions`` only
        knows about ``nonce_key`` and ``gas``.
        """
        with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
            BuildOptions(salt=5)  # type: ignore[call-arg]

    def test_prepare_options_carries_both_independently(self) -> None:
        opts = PrepareUserOpOptions(salt=7, nonce_key=99)
        assert opts.salt == 7
        assert opts.nonce_key == 99


# ---------------------------------------------------------------------------
# simulate tri-state on send path
# ---------------------------------------------------------------------------


class TestSimulateTristate:
    def test_default_is_none(self) -> None:
        """``PrepareUserOpOptions().simulate`` is None by default."""
        assert PrepareUserOpOptions().simulate is None

    def test_with_simulate_none_to_true(self) -> None:
        """Send-path resolves ``None`` -> ``True`` (TS parity)."""
        result = _with_simulate(None)
        assert result.simulate is True

    def test_with_simulate_unset_field_defaults_true(self) -> None:
        """If the caller passes other fields without setting simulate
        explicitly, the send path STILL enables simulation. Guards
        against the silent-disable trap where unset simulate would
        previously be coerced to ``False``.
        """
        opts = PrepareUserOpOptions(nonce_key=42)  # simulate unset → None
        result = _with_simulate(opts)
        assert result.simulate is True
        assert result.nonce_key == 42  # other fields preserved

    def test_with_simulate_explicit_false_is_respected(self) -> None:
        """An explicit opt-out (``simulate=False``) is honored."""
        opts = PrepareUserOpOptions(simulate=False)
        result = _with_simulate(opts)
        assert result.simulate is False

    def test_with_simulate_explicit_true_is_respected(self) -> None:
        opts = PrepareUserOpOptions(simulate=True)
        result = _with_simulate(opts)
        assert result.simulate is True


# ---------------------------------------------------------------------------
# lazy-fail bundler stub
# ---------------------------------------------------------------------------


class TestLazyFailBundler:
    """Constructed-without-bundler clients raise typed errors on
    trades.* / bundler.* — never a network error against a placeholder.
    """

    def _client(self) -> object:
        return create_smart_account_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_signer(),
            # bundler omitted → lazy-fail stub installed
        )

    @pytest.mark.asyncio
    async def test_send_raises_typed_config_error(self) -> None:
        client = self._client()
        with pytest.raises(KashConfigError) as exc_info:
            await client.bundler.send({})  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INVALID_CONFIG
        assert exc_info.value.context["missing"] == "bundler"  # type: ignore[index]

    @pytest.mark.asyncio
    async def test_estimate_gas_raises_typed_config_error(self) -> None:
        client = self._client()
        with pytest.raises(KashConfigError) as exc_info:
            await client.bundler.estimate_gas({})  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.INVALID_CONFIG

    @pytest.mark.asyncio
    async def test_chain_id_raises_typed_config_error(self) -> None:
        client = self._client()
        with pytest.raises(KashConfigError):
            await client.bundler.chain_id()

    @pytest.mark.asyncio
    async def test_health_does_not_raise_returns_error_shape(self) -> None:
        """``BundlerClient.health()`` is documented "Never raises" and
        returns the :class:`BundlerHealth` discriminated union. The
        lazy-fail stub MUST honor this contract — its ``chain_id()``
        refusal gets wrapped into ``BundlerHealthError`` by the parent's
        ``health()`` try/except, NOT propagated.
        """
        from kashdao_protocol_sdk import BundlerHealthError

        client = self._client()
        health = await client.bundler.health()
        assert isinstance(health, BundlerHealthError)
        assert health.ok is False
        assert isinstance(health.error, KashConfigError)
        assert health.error.code == ErrorCode.INVALID_CONFIG

    @pytest.mark.asyncio
    async def test_aclose_is_noop(self) -> None:
        """The lazy-fail stub never opened a pool; ``aclose`` is a no-op."""
        client = self._client()
        await client.aclose()  # must not raise


# ---------------------------------------------------------------------------
# dead types removed from barrel
# ---------------------------------------------------------------------------


class TestDeadTypesRemoved:
    def test_prepare_options_not_in_smart_account_barrel(self) -> None:
        from kashdao_protocol_sdk import smart_account as sa

        assert not hasattr(sa, "PrepareOptions"), (
            "PrepareOptions was a dead type — must be removed from the SA barrel"
        )

    def test_send_options_not_in_smart_account_barrel(self) -> None:
        from kashdao_protocol_sdk import smart_account as sa

        assert not hasattr(sa, "SendOptions"), (
            "SendOptions was a dead type — must be removed from the SA barrel"
        )

    def test_prepare_options_not_in_top_level_barrel(self) -> None:
        import kashdao_protocol_sdk as k

        assert "PrepareOptions" not in k.__all__
        assert "SendOptions" not in k.__all__

    def test_prepare_user_op_options_is_canonical(self) -> None:
        """The live ``PrepareUserOpOptions`` is the only options shape."""
        from kashdao_protocol_sdk import PrepareUserOpOptions as Top

        assert Top is PrepareUserOpOptions


# ---------------------------------------------------------------------------
# client.trades.build_* accepts BuildOptions + PaymasterConfig
# ---------------------------------------------------------------------------


class TestBuildAcceptsOptions:
    """Direct ``client.trades.build_*`` callers can pass ``nonce_key``,
    ``gas`` overrides, and a paymaster — closing the DX gap on the
    build-only path (no bundler, no gas estimation).
    """

    def test_build_buy_signature_accepts_options(self) -> None:
        import inspect

        from kashdao_protocol_sdk.smart_account.client import SmartAccountTrades

        sig = inspect.signature(SmartAccountTrades.build_buy)
        params = sig.parameters
        assert "options" in params
        assert "paymaster" in params

    def test_build_sell_signature_accepts_options(self) -> None:
        import inspect

        from kashdao_protocol_sdk.smart_account.client import SmartAccountTrades

        sig = inspect.signature(SmartAccountTrades.build_sell)
        assert "options" in sig.parameters
        assert "paymaster" in sig.parameters

    def test_build_close_position_signature_accepts_options(self) -> None:
        import inspect

        from kashdao_protocol_sdk.smart_account.client import SmartAccountTrades

        sig = inspect.signature(SmartAccountTrades.build_close_position)
        assert "options" in sig.parameters
        assert "paymaster" in sig.parameters

    def test_build_approve_signature_accepts_options(self) -> None:
        import inspect

        from kashdao_protocol_sdk.smart_account.client import SmartAccountTrades

        sig = inspect.signature(SmartAccountTrades.build_approve)
        assert "options" in sig.parameters
        assert "paymaster" in sig.parameters


# ---------------------------------------------------------------------------
# send.* through unconfigured bundler raises typed error
# ---------------------------------------------------------------------------


class TestCloseWeb3MustNotRaise:
    """``_close_web3`` honors the "MUST NOT raise" invariant for the
    realistic upstream-disconnect error set, AND swallows the listed
    types so the bundler error surfaces cleanly in
    :meth:`SmartAccountClient.aclose`'s ``finally``. Pins the invariant
    against future provider drift.
    """

    @pytest.mark.asyncio
    async def test_close_web3_swallows_runtime_error(self) -> None:
        from kashdao_protocol_sdk.shared.web3_close import close_web3_provider as _close_web3

        class _BadProvider:
            async def disconnect(self) -> None:
                raise RuntimeError("Event loop is closed")

        class _FakeWeb3:
            provider = _BadProvider()

        # MUST NOT raise — close-on-shutdown invariant.
        await _close_web3(_FakeWeb3())  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_close_web3_swallows_os_error(self) -> None:
        from kashdao_protocol_sdk.shared.web3_close import close_web3_provider as _close_web3

        class _BadProvider:
            async def disconnect(self) -> None:
                raise OSError("broken pipe")

        class _FakeWeb3:
            provider = _BadProvider()

        await _close_web3(_FakeWeb3())  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_close_web3_swallows_connection_error(self) -> None:
        from kashdao_protocol_sdk.shared.web3_close import close_web3_provider as _close_web3

        class _BadProvider:
            async def disconnect(self) -> None:
                raise ConnectionError("already disconnected")

        class _FakeWeb3:
            provider = _BadProvider()

        await _close_web3(_FakeWeb3())  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_close_web3_propagates_cancellation(self) -> None:
        """``asyncio.CancelledError`` is NOT in the swallow set —
        cancellation MUST propagate so the consumer's signal flow
        stays correct.
        """
        import asyncio

        from kashdao_protocol_sdk.shared.web3_close import close_web3_provider as _close_web3

        class _BadProvider:
            async def disconnect(self) -> None:
                raise asyncio.CancelledError()

        class _FakeWeb3:
            provider = _BadProvider()

        with pytest.raises(asyncio.CancelledError):
            await _close_web3(_FakeWeb3())  # type: ignore[arg-type]


class TestAcloseLifecycleOrdering:
    """``SmartAccountClient.aclose()`` always closes the web3 transport,
    even if the bundler close raises.

    Without the try/finally, a flaky bundler teardown (httpx pool
    error, transient network) would orphan the underlying aiohttp /
    websocket session. Hummingbot strategies that recreate clients
    between epochs MUST NOT accumulate transports.
    """

    @pytest.mark.asyncio
    async def test_web3_closes_even_if_bundler_close_raises(self) -> None:
        from unittest.mock import AsyncMock, patch

        from kashdao_protocol_sdk.smart_account.bundler.generic import BundlerClient

        client = create_smart_account_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_signer(),
            bundler="https://bundler.example.com",
        )

        # Replace bundler with a slotted subclass that overrides aclose
        # to raise. ``BundlerClient`` is ``@dataclass(slots=True)`` so
        # ``patch.object`` can't override the method directly.
        class _RaisingBundler(BundlerClient):
            async def aclose(self) -> None:
                raise RuntimeError("simulated pool crash")

        # Build a raising subclass instance with the same config + http,
        # then swap it onto the client. ``BundlerClient`` and
        # ``SmartAccountClient`` are ``@dataclass(slots=True)`` (NOT
        # frozen), so plain attribute rebinding is permitted at both
        # runtime and mypy strict.
        raising = _RaisingBundler(
            _config=client.bundler._config,
            _http=client.bundler._http,
            _owns_http=client.bundler._owns_http,
        )
        client.bundler = raising

        web3_close_called: list[bool] = []

        async def fake_web3_close(_web3: object) -> None:
            web3_close_called.append(True)

        with patch(
            "kashdao_protocol_sdk.smart_account.client.close_web3_provider",
            new=AsyncMock(side_effect=fake_web3_close),
        ):
            with pytest.raises(RuntimeError, match="simulated pool crash"):
                await client.aclose()

        assert web3_close_called == [True], (
            "web3 close MUST run even if bundler close raised — try/finally invariant"
        )


class TestEstimateGasThroughLazyFailBundler:
    """Pin the integrated path: any consumer code that reaches into
    ``client.bundler.*`` (via ``client.trades.send.*`` or directly)
    surfaces :class:`KashConfigError` when the client was constructed
    without a bundler — never an HTTP error against a placeholder URL.

    The send orchestrator's first bundler RPC is ``estimate_gas`` (via
    ``prepare_*``), so testing that path against the stub gives us the
    same coverage as a full ``send.buy`` test without needing a mock
    chain RPC for the upstream ``get_quote`` call.
    """

    @pytest.mark.asyncio
    async def test_estimate_gas_fails_with_typed_config_error(self) -> None:
        from kashdao_protocol_sdk import UnsignedUserOp

        client = create_smart_account_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_signer(),
            # bundler omitted → lazy-fail
        )
        try:
            user_op = UnsignedUserOp(
                sender="0x" + "ab" * 20,
                nonce=0,
                callData="0x",
                callGasLimit=0,
                verificationGasLimit=0,
                preVerificationGas=0,
                maxFeePerGas=0,
                maxPriorityFeePerGas=0,
            )
            with pytest.raises(KashConfigError) as exc_info:
                await client.bundler.estimate_gas(user_op)
            assert exc_info.value.code == ErrorCode.INVALID_CONFIG
            assert exc_info.value.context["missing"] == "bundler"  # type: ignore[index]
        finally:
            await client.aclose()
