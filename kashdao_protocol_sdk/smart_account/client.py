"""``create_smart_account_client`` — non-custodial SA-mode trading client.

Mirrors ``src/smart-account/client.ts``.

Wires the bundler client + SA signer + EntryPoint v0.7 + chain RPC
into a single :class:`SmartAccountClient` exposing the same
``markets.*`` / ``account.*`` / ``trades.*`` surface as the EOA
client. Bring your own RPC, bundler, and signer; Kash is never on the
trade path.

Use ``async with create_smart_account_client(...) as client:`` (or
call :meth:`SmartAccountClient.aclose` explicitly) to release the
bundler's HTTP connection pool on shutdown.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.providers.async_base import AsyncBaseProvider
from web3.providers.persistent import WebSocketProvider

from kashdao_protocol_sdk.shared.account.allowance import get_usdc_allowance
from kashdao_protocol_sdk.shared.account.balances import get_usdc_balance
from kashdao_protocol_sdk.shared.account.gas import get_gas_balance
from kashdao_protocol_sdk.shared.account.positions import get_position
from kashdao_protocol_sdk.shared.contracts.addresses import (
    ProtocolAddresses,
    get_protocol_addresses,
)
from kashdao_protocol_sdk.shared.custom_chain import CustomChain, resolve_custom_chain
from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashConfigError
from kashdao_protocol_sdk.shared.fees import EstimateFeesOptions, FeeEstimate, estimate_chain_fees
from kashdao_protocol_sdk.shared.hooks import KashProtocolHooks
from kashdao_protocol_sdk.shared.markets.get import get_market_minimal
from kashdao_protocol_sdk.shared.markets.quote import QuoteParams, get_quote
from kashdao_protocol_sdk.shared.markets.state import get_market_state
from kashdao_protocol_sdk.shared.markets.watch import (
    WatchOptions,
    WatchSubscription,
    watch_market,
)
from kashdao_protocol_sdk.shared.types import (
    BuildApproveParams,
    BuildBuyParams,
    BuildClosePositionParams,
    BuildSellParams,
    Hex,
    MarketState,
    MinimalMarketRead,
    Position,
    Quote,
    SimulationResult,
)
from kashdao_protocol_sdk.shared.web3_close import close_web3_provider
from kashdao_protocol_sdk.smart_account.account.address import (
    compute_smart_account_address_via_web3,
    is_smart_account_deployed,
)
from kashdao_protocol_sdk.smart_account.bundler.alchemy import create_alchemy_bundler_client
from kashdao_protocol_sdk.smart_account.bundler.flashbots import create_flashbots_bundler_client
from kashdao_protocol_sdk.smart_account.bundler.generic import (
    BundlerClient,
    BundlerClientConfig,
    create_generic_bundler_client,
)
from kashdao_protocol_sdk.smart_account.bundler.pimlico import create_pimlico_bundler_client
from kashdao_protocol_sdk.smart_account.config import (
    BundlerOptions,
    SmartAccountClientConfig,
    SmartAccountClientConfigInput,
    parse_smart_account_client_config,
)
from kashdao_protocol_sdk.smart_account.trades.build import (
    PaymasterConfig,
    build_approve_user_op,
    build_buy_user_op,
    build_close_position_user_op,
    build_sell_user_op,
)
from kashdao_protocol_sdk.smart_account.trades.hash import (
    compute_user_op_hash,
    user_op_to_typed_data,
)
from kashdao_protocol_sdk.smart_account.trades.prepare import (
    PrepareUserOpOptions,
    prepare_approve_user_op,
    prepare_buy_user_op,
    prepare_close_position_user_op,
    prepare_sell_user_op,
)
from kashdao_protocol_sdk.smart_account.trades.send import (
    send_approve_user_op,
    send_buy_user_op,
    send_close_position_user_op,
    send_sell_user_op,
)
from kashdao_protocol_sdk.smart_account.trades.simulate import simulate_user_op
from kashdao_protocol_sdk.smart_account.trades.submit import submit_user_op
from kashdao_protocol_sdk.smart_account.types import (
    BuildOptions,
    BuiltUserOp,
    SendResult,
    SignedUserOp,
    SmartAccountSignerAdapter,
    SubmitOptions,
    SubmitResult,
    UnsignedUserOp,
    UserOpTypedData,
)

_FeeEstimator = Callable[[], Awaitable[FeeEstimate]]


@dataclass(slots=True)
class SmartAccountMarkets:
    """``client.markets.*`` — read-only market reads."""

    _web3: AsyncWeb3

    async def get(self, market_address: Hex) -> MinimalMarketRead:
        return await get_market_minimal(self._web3, market_address)

    async def state(self, market_address: Hex) -> MarketState:
        return await get_market_state(self._web3, market_address)

    async def quote(self, market_address: Hex, params: QuoteParams) -> Quote:
        return await get_quote(self._web3, market_address, params)

    def watch(
        self,
        market_address: Hex,
        options: WatchOptions,
    ) -> WatchSubscription:
        return watch_market(self._web3, market_address, options)


@dataclass(slots=True)
class SmartAccountAccount:
    """``client.account.*`` — on-chain reads for an SA address."""

    _web3: AsyncWeb3
    _addresses: ProtocolAddresses

    async def usdc_balance(self, address: Hex) -> int:
        return await get_usdc_balance(self._web3, self._addresses, address)

    async def usdc_allowance(self, address: Hex, spender: Hex) -> int:
        return await get_usdc_allowance(self._web3, self._addresses, address, spender)

    async def gas_balance(self, address: Hex) -> int:
        return await get_gas_balance(self._web3, address)

    async def position(self, address: Hex, market_address: Hex) -> Position:
        return await get_position(self._web3, self._addresses, address, market_address)

    async def compute_address(self, owner_address: Hex, salt: int = 0) -> Hex:
        return await compute_smart_account_address_via_web3(
            self._web3,
            self._addresses.smart_account.factory_address,
            owner_address,
            salt,
        )

    async def is_deployed(self, address: Hex) -> bool:
        return await is_smart_account_deployed(self._web3, address)


@dataclass(slots=True)
class SmartAccountTradesSend:
    """``client.trades.send.*`` orchestrator namespace."""

    _web3: AsyncWeb3
    _bundler: BundlerClient
    _estimate_fees: _FeeEstimator
    _signer: SmartAccountSignerAdapter
    _addresses: ProtocolAddresses
    _hooks: KashProtocolHooks | None

    async def buy(
        self,
        market_address: Hex,
        params: BuildBuyParams,
        *,
        prepare_options: PrepareUserOpOptions | None = None,
        submit_options: SubmitOptions | None = None,
        wait: bool = True,
        wait_timeout_seconds: float = 90.0,
        wait_interval_seconds: float = 2.0,
        signal: object | None = None,
    ) -> SendResult:
        return await send_buy_user_op(
            self._web3,
            self._bundler,
            self._estimate_fees,
            self._signer,
            self._addresses,
            market_address,
            params,
            prepare_options=prepare_options,
            submit_options=submit_options,
            wait=wait,
            wait_timeout_seconds=wait_timeout_seconds,
            wait_interval_seconds=wait_interval_seconds,
            signal=signal,
            hooks=self._hooks,
        )

    async def sell(
        self,
        market_address: Hex,
        params: BuildSellParams,
        *,
        prepare_options: PrepareUserOpOptions | None = None,
        submit_options: SubmitOptions | None = None,
        wait: bool = True,
        wait_timeout_seconds: float = 90.0,
        wait_interval_seconds: float = 2.0,
        signal: object | None = None,
    ) -> SendResult:
        return await send_sell_user_op(
            self._web3,
            self._bundler,
            self._estimate_fees,
            self._signer,
            self._addresses,
            market_address,
            params,
            prepare_options=prepare_options,
            submit_options=submit_options,
            wait=wait,
            wait_timeout_seconds=wait_timeout_seconds,
            wait_interval_seconds=wait_interval_seconds,
            signal=signal,
            hooks=self._hooks,
        )

    async def close_position(
        self,
        market_address: Hex,
        params: BuildClosePositionParams,
        *,
        prepare_options: PrepareUserOpOptions | None = None,
        submit_options: SubmitOptions | None = None,
        wait: bool = True,
        wait_timeout_seconds: float = 90.0,
        wait_interval_seconds: float = 2.0,
        signal: object | None = None,
    ) -> SendResult:
        return await send_close_position_user_op(
            self._web3,
            self._bundler,
            self._estimate_fees,
            self._signer,
            self._addresses,
            market_address,
            params,
            prepare_options=prepare_options,
            submit_options=submit_options,
            wait=wait,
            wait_timeout_seconds=wait_timeout_seconds,
            wait_interval_seconds=wait_interval_seconds,
            signal=signal,
            hooks=self._hooks,
        )

    async def approve(
        self,
        params: BuildApproveParams,
        *,
        prepare_options: PrepareUserOpOptions | None = None,
        submit_options: SubmitOptions | None = None,
        wait: bool = True,
        wait_timeout_seconds: float = 90.0,
        wait_interval_seconds: float = 2.0,
        signal: object | None = None,
    ) -> SendResult:
        return await send_approve_user_op(
            self._web3,
            self._bundler,
            self._estimate_fees,
            self._signer,
            self._addresses,
            params,
            prepare_options=prepare_options,
            submit_options=submit_options,
            wait=wait,
            wait_timeout_seconds=wait_timeout_seconds,
            wait_interval_seconds=wait_interval_seconds,
            signal=signal,
            hooks=self._hooks,
        )


@dataclass(slots=True)
class SmartAccountTrades:
    """``client.trades.*`` namespace.

    Direct flow: ``build_*`` → consumer signs → ``submit``. Or the
    recommended ``send.*`` orchestrator.
    """

    _web3: AsyncWeb3
    _bundler: BundlerClient
    _estimate_fees: _FeeEstimator
    _signer: SmartAccountSignerAdapter
    _addresses: ProtocolAddresses
    send: SmartAccountTradesSend

    # ---- build_* (no gas estimation, no bundler call) -------------------

    async def build_buy(
        self,
        market_address: Hex,
        params: BuildBuyParams,
        options: BuildOptions | None = None,
        paymaster: PaymasterConfig | None = None,
    ) -> BuiltUserOp:
        return await build_buy_user_op(
            self._web3, self._addresses, market_address, params, options, paymaster
        )

    async def build_sell(
        self,
        market_address: Hex,
        params: BuildSellParams,
        options: BuildOptions | None = None,
        paymaster: PaymasterConfig | None = None,
    ) -> BuiltUserOp:
        return await build_sell_user_op(
            self._web3, self._addresses, market_address, params, options, paymaster
        )

    async def build_close_position(
        self,
        market_address: Hex,
        params: BuildClosePositionParams,
        options: BuildOptions | None = None,
        paymaster: PaymasterConfig | None = None,
    ) -> BuiltUserOp:
        return await build_close_position_user_op(
            self._web3, self._addresses, market_address, params, options, paymaster
        )

    async def build_approve(
        self,
        params: BuildApproveParams,
        options: BuildOptions | None = None,
        paymaster: PaymasterConfig | None = None,
    ) -> BuiltUserOp:
        return await build_approve_user_op(self._web3, self._addresses, params, options, paymaster)

    # ---- prepare_* (build + estimate + recompute hash) -----------------

    async def prepare_buy(
        self,
        market_address: Hex,
        params: BuildBuyParams,
        options: PrepareUserOpOptions | None = None,
    ) -> BuiltUserOp:
        return await prepare_buy_user_op(
            self._web3,
            self._bundler,
            self._estimate_fees,
            self._addresses,
            market_address,
            params,
            options,
        )

    async def prepare_sell(
        self,
        market_address: Hex,
        params: BuildSellParams,
        options: PrepareUserOpOptions | None = None,
    ) -> BuiltUserOp:
        return await prepare_sell_user_op(
            self._web3,
            self._bundler,
            self._estimate_fees,
            self._addresses,
            market_address,
            params,
            options,
        )

    async def prepare_close_position(
        self,
        market_address: Hex,
        params: BuildClosePositionParams,
        options: PrepareUserOpOptions | None = None,
    ) -> BuiltUserOp:
        return await prepare_close_position_user_op(
            self._web3,
            self._bundler,
            self._estimate_fees,
            self._addresses,
            market_address,
            params,
            options,
        )

    async def prepare_approve(
        self,
        params: BuildApproveParams,
        options: PrepareUserOpOptions | None = None,
    ) -> BuiltUserOp:
        return await prepare_approve_user_op(
            self._web3,
            self._bundler,
            self._estimate_fees,
            self._addresses,
            params,
            options,
        )

    # ---- single-step helpers -------------------------------------------

    async def simulate(self, user_op: UnsignedUserOp) -> SimulationResult:
        return await simulate_user_op(self._web3, user_op)

    async def submit(
        self,
        signed_user_op: SignedUserOp,
        options: SubmitOptions | None = None,
    ) -> SubmitResult:
        return await submit_user_op(
            self._bundler,
            self._addresses.chain_id,
            self._addresses.smart_account.entry_point_address,
            self._signer.owner_address,
            signed_user_op,
            options,
        )

    def hash_of(self, user_op: UnsignedUserOp) -> Hex:
        return compute_user_op_hash(
            self._addresses.chain_id,
            self._addresses.smart_account.entry_point_address,
            user_op,
        )

    def typed_data_for(self, user_op: UnsignedUserOp) -> UserOpTypedData:
        return user_op_to_typed_data(
            self._addresses.chain_id,
            self._addresses.smart_account.entry_point_address,
            user_op,
        )


@dataclass(slots=True)
class SmartAccountClient:
    """Non-custodial SA-mode trading client.

    Construct via :func:`create_smart_account_client`. Composes:

    * :class:`SmartAccountMarkets` (``client.markets.*``)
    * :class:`SmartAccountAccount` (``client.account.*``)
    * :class:`SmartAccountTrades` (``client.trades.*``) including
      ``client.trades.send.*`` orchestrator
    * :class:`BundlerClient` (``client.bundler``)

    Use as an ``async with`` context manager — releases the bundler's
    HTTP connection pool on exit.
    """

    config: SmartAccountClientConfig
    addresses: ProtocolAddresses
    web3: AsyncWeb3
    bundler: BundlerClient
    signer: SmartAccountSignerAdapter
    markets: SmartAccountMarkets
    account: SmartAccountAccount
    trades: SmartAccountTrades
    mode: str = "smart-account"

    @property
    def chain_id(self) -> int:
        return self.config.chain_id

    async def estimate_fees(self, options: EstimateFeesOptions | None = None) -> FeeEstimate:
        return await estimate_chain_fees(self.web3, options)

    async def aclose(self) -> None:
        """Release the bundler's HTTP connection pool AND the web3 provider.

        Idempotent. Closes:

        * :class:`BundlerClient` (releases its ``httpx.AsyncClient`` if owned)
        * The underlying ``AsyncHTTPProvider`` / ``WebSocketProvider`` constructed
          by :func:`create_smart_account_client` (releases the aiohttp /
          websocket session)

        Long-running consumers (Hummingbot strategies) that recreate
        clients between epochs MUST call ``aclose`` to avoid socket
        accumulation.

        The bundler close runs first; the web3 close ALWAYS runs even
        if the bundler raised — otherwise a flaky bundler teardown
        could leak the web3 transport (which is exactly the failure
        Hummingbot would never recover from).
        """
        try:
            await self.bundler.aclose()
        finally:
            await close_web3_provider(self.web3)

    async def __aenter__(self) -> SmartAccountClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


def create_smart_account_client(
    config: SmartAccountClientConfigInput | None = None,
    *,
    chain_id: int | None = None,
    rpc: str | None = None,
    signer: SmartAccountSignerAdapter | None = None,
    bundler: BundlerOptions | str | None = None,
    custom_chain: CustomChain | None = None,
    timeout_ms: int = 30_000,
    hooks: KashProtocolHooks | None = None,
) -> SmartAccountClient:
    """Construct a non-custodial SA-mode trading client.

    Pass either a :class:`SmartAccountClientConfig` object or the
    keyword args individually. Validates the config via
    :func:`parse_smart_account_client_config` (raising
    :class:`KashConfigError` on failure) and constructs an
    :class:`AsyncWeb3` bound to the consumer's RPC.

    The bundler is required for any ``trades.*`` call; read-only
    consumers (``markets.*`` / ``account.*``) can omit it.
    """
    if config is None:
        if chain_id is None or rpc is None or signer is None:
            missing = [
                name
                for name, value in (
                    ("chain_id", chain_id),
                    ("rpc", rpc),
                    ("signer", signer),
                )
                if value is None
            ]
            raise KashConfigError(
                "create_smart_account_client: chain_id, rpc, and signer are required "
                "(or pass a fully-populated config)",
                code=ErrorCode.MISSING_CLIENT_CONFIG,
                context={"missing": missing},
            )
        config = SmartAccountClientConfig(
            chain_id=chain_id,
            rpc=rpc,
            signer=signer,
            bundler=bundler,
            custom_chain=custom_chain,
            timeout_ms=timeout_ms,
            hooks=hooks,
        )

    parsed = parse_smart_account_client_config(config)
    if parsed.custom_chain is not None:
        addresses = resolve_custom_chain(parsed.chain_id, parsed.custom_chain)
    else:
        addresses = get_protocol_addresses(parsed.chain_id)

    provider: AsyncBaseProvider
    if parsed.rpc.startswith(("ws://", "wss://")):
        provider = WebSocketProvider(parsed.rpc)
    else:
        provider = AsyncHTTPProvider(
            parsed.rpc, request_kwargs={"timeout": parsed.timeout_ms / 1000.0}
        )
    web3 = AsyncWeb3(provider)

    bundler_client = _build_bundler_client(parsed, addresses)

    async def _estimate_fees() -> FeeEstimate:
        return await estimate_chain_fees(web3)

    markets = SmartAccountMarkets(_web3=web3)
    account = SmartAccountAccount(_web3=web3, _addresses=addresses)
    send = SmartAccountTradesSend(
        _web3=web3,
        _bundler=bundler_client,
        _estimate_fees=_estimate_fees,
        _signer=parsed.signer,
        _addresses=addresses,
        _hooks=parsed.hooks,
    )
    trades = SmartAccountTrades(
        _web3=web3,
        _bundler=bundler_client,
        _estimate_fees=_estimate_fees,
        _signer=parsed.signer,
        _addresses=addresses,
        send=send,
    )

    return SmartAccountClient(
        config=parsed,
        addresses=addresses,
        web3=web3,
        bundler=bundler_client,
        signer=parsed.signer,
        markets=markets,
        account=account,
        trades=trades,
    )


def _build_bundler_client(
    parsed: SmartAccountClientConfig,
    addresses: ProtocolAddresses,
) -> BundlerClient:
    """Pick the right bundler preset from ``parsed.bundler``.

    Read-only consumers can skip the bundler at construction time;
    we instantiate a placeholder that raises :class:`KashConfigError`
    on first ``trades.*`` use.
    """
    if parsed.bundler is None:
        # Lazy-fail: install a stub whose every method raises
        # KashConfigError(MISSING_BUNDLER_CONFIG). Consumers reaching
        # for trades.* / client.bundler.* see a typed diagnostic, not
        # a confusing retryable BUNDLER_NETWORK_ERROR against a
        # placeholder URL.
        return _LazyFailBundlerClient(
            _config=BundlerClientConfig(
                url="https://bundler-not-configured.invalid",
                entry_point_address=addresses.smart_account.entry_point_address,
            ),
            _http=None,  # type: ignore[arg-type]  # never accessed; methods refuse
            _owns_http=False,
        )

    if isinstance(parsed.bundler, BundlerOptions):
        url = parsed.bundler.url
        api_key = parsed.bundler.api_key
        provider_name = parsed.bundler.provider
    else:
        url = parsed.bundler
        api_key = None
        provider_name = "generic"

    config = BundlerClientConfig(
        url=url,
        api_key=api_key,
        timeout_seconds=parsed.timeout_ms / 1000.0,
        entry_point_address=addresses.smart_account.entry_point_address,
        hooks=parsed.hooks,
    )

    if provider_name == "alchemy":
        return create_alchemy_bundler_client(config)
    if provider_name == "pimlico":
        return create_pimlico_bundler_client(config)
    if provider_name == "flashbots":
        return create_flashbots_bundler_client(config)
    return create_generic_bundler_client(config)


class _LazyFailBundlerClient(BundlerClient):
    """Stub :class:`BundlerClient` returned when no bundler is configured.

    Every method raises :class:`KashConfigError` immediately so
    consumers reaching for ``trades.*`` (or ``client.bundler.*``) get
    a clear diagnostic instead of a confusing
    ``BUNDLER_NETWORK_ERROR(retryable=True)`` from a placeholder URL.

    Read-only consumers (``markets.*`` / ``account.*``) that never
    touch trades or the bundler surface never trigger any of these.

    **Maintenance invariant.** ``BundlerClient.__init__`` requires an
    ``httpx.AsyncClient`` for the ``_http`` slot, but this stub MUST
    NEVER touch the network — every method that would dereference
    ``self._http`` is overridden here. We stash ``_http=None`` (with a
    ``# type: ignore[arg-type]`` at the construction site) under the
    invariant that no method below ever reads it. If a new RPC method
    is added to :class:`BundlerClient`, override it here too. The
    test ``TestLazyFailBundler`` pins this contract.

    ``health()`` is intentionally NOT overridden — see the comment
    after ``chain_id`` for the "Never raises" contract preservation.
    """

    def _refuse(self, what: str) -> KashConfigError:
        return KashConfigError(
            f"bundler not configured - cannot {what}. Pass `bundler=...` "
            "to create_smart_account_client (a URL string or BundlerOptions).",
            code=ErrorCode.INVALID_CONFIG,
            context={"missing": "bundler"},
        )

    async def aclose(self) -> None:
        # No-op — we never opened a connection pool.
        return None

    async def send(self, *_args: object, **_kwargs: object) -> Any:
        raise self._refuse("submit a UserOp")

    async def estimate_gas(self, *_args: object, **_kwargs: object) -> Any:
        raise self._refuse("estimate gas")

    async def get_receipt(self, *_args: object, **_kwargs: object) -> Any:
        raise self._refuse("read a UserOp receipt")

    async def wait_for_receipt(self, *_args: object, **_kwargs: object) -> Any:
        raise self._refuse("wait for a UserOp receipt")

    async def chain_id(self, *_args: object, **_kwargs: object) -> Any:
        raise self._refuse("query the bundler chain id")

    # NOTE: ``health()`` is intentionally NOT overridden. The parent's
    # implementation calls ``chain_id()`` (which we override above to
    # refuse) and wraps any exception into a ``BundlerHealthError``.
    # So ``health()`` honors its documented "Never raises" contract:
    # it returns ``BundlerHealthError(error=KashConfigError(...))``,
    # which status-page consumers can render as a "down" card without
    # try/except.


__all__ = [
    "SmartAccountAccount",
    "SmartAccountClient",
    "SmartAccountMarkets",
    "SmartAccountTrades",
    "SmartAccountTradesSend",
    "create_smart_account_client",
]
