"""``create_eoa_client`` — non-custodial EOA-mode trading client.

Mirrors ``src/eoa/client.ts``.

EOA mode is the canonical path for market makers, Hummingbot strategies,
and any consumer with their own EIP-1559 signing infrastructure. No
bundler, no SimpleAccount, no ERC-4337 — just an EOA, an RPC URL, and
a signer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.providers.async_base import AsyncBaseProvider
from web3.providers.persistent import WebSocketProvider

from kashdao_protocol_sdk.eoa.config import (
    EoaClientConfig,
    EoaClientConfigInput,
    parse_eoa_client_config,
)
from kashdao_protocol_sdk.eoa.trades.build import (
    build_approve_transaction,
    build_buy_transaction,
    build_close_position_transaction,
    build_sell_transaction,
)
from kashdao_protocol_sdk.eoa.trades.hash import compute_transaction_hash
from kashdao_protocol_sdk.eoa.trades.prepare import (
    prepare_approve_transaction,
    prepare_buy_transaction,
    prepare_close_position_transaction,
    prepare_sell_transaction,
)
from kashdao_protocol_sdk.eoa.trades.send import (
    send_approve_transaction,
    send_buy_transaction,
    send_close_position_transaction,
    send_sell_transaction,
)
from kashdao_protocol_sdk.eoa.trades.simulate import simulate_transaction
from kashdao_protocol_sdk.eoa.trades.submit import submit_transaction
from kashdao_protocol_sdk.eoa.types import (
    BuiltTransaction,
    EoaSignerAdapter,
    EoaSubmitOptions,
    EoaSubmitResult,
    EoaTxOverrides,
    PrepareEoaOptions,
    SendEoaOptions,
    SendEoaResult,
    UnsignedTransaction,
)
from kashdao_protocol_sdk.shared.account.allowance import get_usdc_allowance
from kashdao_protocol_sdk.shared.account.balances import get_usdc_balance
from kashdao_protocol_sdk.shared.account.gas import get_gas_balance
from kashdao_protocol_sdk.shared.account.positions import get_position
from kashdao_protocol_sdk.shared.contracts.addresses import (
    ProtocolAddresses,
    get_protocol_addresses,
)
from kashdao_protocol_sdk.shared.custom_chain import CustomChain, resolve_custom_chain
from kashdao_protocol_sdk.shared.errors import KashConfigError
from kashdao_protocol_sdk.shared.fees import (
    EstimateFeesOptions,
    FeeEstimate,
    estimate_chain_fees,
)
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

# ---------------------------------------------------------------------------
# Sub-namespace classes — small wrappers grouping methods by category
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EoaMarkets:
    """``client.markets.*`` namespace."""

    _web3: AsyncWeb3

    async def get(self, market_address: Hex) -> MinimalMarketRead:
        return await get_market_minimal(self._web3, market_address)

    async def state(self, market_address: Hex) -> MarketState:
        return await get_market_state(self._web3, market_address)

    async def quote(self, market_address: Hex, params: QuoteParams) -> Quote:
        return await get_quote(self._web3, market_address, params)

    def watch(self, market_address: Hex, options: WatchOptions) -> WatchSubscription:
        return watch_market(self._web3, market_address, options)


@dataclass(slots=True)
class EoaAccount:
    """``client.account.*`` namespace."""

    _web3: AsyncWeb3
    _addresses: ProtocolAddresses

    async def usdc_balance(self, address: Hex) -> int:
        return await get_usdc_balance(self._web3, self._addresses, address)

    async def position(self, address: Hex, market_address: Hex) -> Position:
        return await get_position(self._web3, self._addresses, address, market_address)

    async def gas_balance(self, address: Hex) -> int:
        return await get_gas_balance(self._web3, address)

    async def usdc_allowance(self, address: Hex, spender: Hex) -> int:
        return await get_usdc_allowance(self._web3, self._addresses, address, spender)


@dataclass(slots=True)
class EoaTradesSend:
    """``client.trades.send.*`` all-in-one namespace."""

    _web3: AsyncWeb3
    _signer: EoaSignerAdapter
    _addresses: ProtocolAddresses
    _estimate_fees: Callable[[], Awaitable[FeeEstimate]]

    async def buy(
        self,
        market_address: Hex,
        params: BuildBuyParams,
        options: SendEoaOptions | None = None,
        *,
        signal: asyncio.Event | None = None,
    ) -> SendEoaResult:
        return await send_buy_transaction(
            self._web3,
            self._estimate_fees,
            self._signer,
            self._addresses,
            market_address,
            params,
            options,
            signal=signal,
        )

    async def sell(
        self,
        market_address: Hex,
        params: BuildSellParams,
        options: SendEoaOptions | None = None,
        *,
        signal: asyncio.Event | None = None,
    ) -> SendEoaResult:
        return await send_sell_transaction(
            self._web3,
            self._estimate_fees,
            self._signer,
            self._addresses,
            market_address,
            params,
            options,
            signal=signal,
        )

    async def close_position(
        self,
        market_address: Hex,
        params: BuildClosePositionParams,
        options: SendEoaOptions | None = None,
        *,
        signal: asyncio.Event | None = None,
    ) -> SendEoaResult:
        return await send_close_position_transaction(
            self._web3,
            self._estimate_fees,
            self._signer,
            self._addresses,
            market_address,
            params,
            options,
            signal=signal,
        )

    async def approve(
        self,
        params: BuildApproveParams,
        options: SendEoaOptions | None = None,
        *,
        signal: asyncio.Event | None = None,
    ) -> SendEoaResult:
        return await send_approve_transaction(
            self._web3,
            self._estimate_fees,
            self._signer,
            self._addresses,
            params,
            options,
            signal=signal,
        )


@dataclass(slots=True)
class EoaTrades:
    """``client.trades.*`` namespace.

    Direct flow: ``build_*`` → consumer signs → ``submit``. Or the
    recommended ``send.*`` orchestrator.
    """

    _web3: AsyncWeb3
    _signer: EoaSignerAdapter
    _addresses: ProtocolAddresses
    _estimate_fees: Callable[[], Awaitable[FeeEstimate]]
    send: EoaTradesSend

    async def build_buy(
        self, market_address: Hex, params: BuildBuyParams, overrides: EoaTxOverrides | None = None
    ) -> BuiltTransaction:
        return await build_buy_transaction(
            self._web3,
            self._addresses,
            self._signer.owner_address,
            market_address,
            params,
            overrides,
        )

    async def build_sell(
        self,
        market_address: Hex,
        params: BuildSellParams,
        overrides: EoaTxOverrides | None = None,
    ) -> BuiltTransaction:
        return await build_sell_transaction(
            self._web3,
            self._addresses,
            self._signer.owner_address,
            market_address,
            params,
            overrides,
        )

    async def build_close_position(
        self,
        market_address: Hex,
        params: BuildClosePositionParams,
        overrides: EoaTxOverrides | None = None,
    ) -> BuiltTransaction:
        return await build_close_position_transaction(
            self._web3,
            self._addresses,
            self._signer.owner_address,
            market_address,
            params,
            overrides,
        )

    async def build_approve(
        self, params: BuildApproveParams, overrides: EoaTxOverrides | None = None
    ) -> BuiltTransaction:
        return await build_approve_transaction(
            self._web3,
            self._addresses,
            self._signer.owner_address,
            params,
            overrides,
        )

    async def prepare_buy(
        self,
        market_address: Hex,
        params: BuildBuyParams,
        options: PrepareEoaOptions | None = None,
    ) -> BuiltTransaction:
        return await prepare_buy_transaction(
            self._web3,
            self._estimate_fees,
            self._addresses,
            self._signer.owner_address,
            market_address,
            params,
            options,
        )

    async def prepare_sell(
        self,
        market_address: Hex,
        params: BuildSellParams,
        options: PrepareEoaOptions | None = None,
    ) -> BuiltTransaction:
        return await prepare_sell_transaction(
            self._web3,
            self._estimate_fees,
            self._addresses,
            self._signer.owner_address,
            market_address,
            params,
            options,
        )

    async def prepare_close_position(
        self,
        market_address: Hex,
        params: BuildClosePositionParams,
        options: PrepareEoaOptions | None = None,
    ) -> BuiltTransaction:
        return await prepare_close_position_transaction(
            self._web3,
            self._estimate_fees,
            self._addresses,
            self._signer.owner_address,
            market_address,
            params,
            options,
        )

    async def prepare_approve(
        self, params: BuildApproveParams, options: PrepareEoaOptions | None = None
    ) -> BuiltTransaction:
        return await prepare_approve_transaction(
            self._web3,
            self._estimate_fees,
            self._addresses,
            self._signer.owner_address,
            params,
            options,
        )

    async def simulate(self, transaction: UnsignedTransaction) -> SimulationResult:
        return await simulate_transaction(self._web3, transaction)

    def hash_of(self, transaction: UnsignedTransaction) -> Hex:
        return compute_transaction_hash(transaction)

    async def submit(
        self, signed_transaction: Hex, options: EoaSubmitOptions | None = None
    ) -> EoaSubmitResult:
        return await submit_transaction(
            self._web3,
            self._addresses.chain_id,
            self._signer.owner_address,
            signed_transaction,
            options,
        )


@dataclass(slots=True)
class EoaClient:
    """Returned by :func:`create_eoa_client`. Sub-namespaces hold the actual methods."""

    mode: str
    chain_id: int
    addresses: ProtocolAddresses
    web3: AsyncWeb3
    signer: EoaSignerAdapter
    markets: EoaMarkets
    account: EoaAccount
    trades: EoaTrades

    async def estimate_fees(self, options: EstimateFeesOptions | None = None) -> FeeEstimate:
        """Expose :func:`estimate_chain_fees` for custom fee strategies."""
        return await estimate_chain_fees(self.web3, options)

    async def aclose(self) -> None:
        """Release the signer's HTTP pool (if any) AND the web3 provider.

        Idempotent. Closes:

        * The signer's owned :class:`httpx.AsyncClient`, if the
          configured signer exposes :py:meth:`aclose` (e.g.
          :class:`JsonRpcEoaSigner`). :class:`LocalEoaSigner` owns no
          remote connection and is a no-op.
        * The underlying ``AsyncHTTPProvider`` / ``WebSocketProvider``
          constructed by :func:`create_eoa_client` (releases the
          aiohttp / websocket session).

        Long-running consumers (Hummingbot strategies) that recreate
        clients between epochs MUST call ``aclose`` to avoid socket
        accumulation.

        The signer close runs first; the web3 close ALWAYS runs even
        if the signer raised — otherwise a flaky signer teardown
        could leak the web3 transport.
        """
        try:
            signer_aclose = getattr(self.signer, "aclose", None)
            if callable(signer_aclose):
                await signer_aclose()
        finally:
            await close_web3_provider(self.web3)

    async def __aenter__(self) -> EoaClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


def create_eoa_client(
    config: EoaClientConfigInput | None = None,
    *,
    chain_id: int | None = None,
    rpc: str | None = None,
    signer: EoaSignerAdapter | None = None,
    custom_chain: CustomChain | None = None,
    timeout_ms: int = 30_000,
    hooks: KashProtocolHooks | None = None,
) -> EoaClient:
    """Construct a non-custodial EOA-mode trading client.

    Pass either an :class:`EoaClientConfig` object or the keyword args
    individually. Validates the config (raising :class:`KashConfigError`
    on failure) and constructs an :class:`AsyncWeb3` bound to the
    consumer's RPC.

    Example::

        from kashdao_protocol_sdk import create_eoa_client, viem_account_eoa_signer
        from eth_account import Account

        signer = viem_account_eoa_signer(Account.from_key(b"..."))
        client = create_eoa_client(
            chain_id=84532,
            rpc="https://my-base-sepolia-rpc.example.com",
            signer=signer,
        )
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
                "create_eoa_client: chain_id, rpc, and signer are required "
                "(or pass a fully-populated config)",
                code="MISSING_CLIENT_CONFIG",
                context={"missing": missing},
            )
        config = EoaClientConfig(
            chain_id=chain_id,
            rpc=rpc,
            signer=signer,
            custom_chain=custom_chain,
            timeout_ms=timeout_ms,
            hooks=hooks,
        )

    parsed = parse_eoa_client_config(config)
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

    async def _estimate_fees() -> FeeEstimate:
        return await estimate_chain_fees(web3)

    markets = EoaMarkets(_web3=web3)
    account = EoaAccount(_web3=web3, _addresses=addresses)
    send = EoaTradesSend(
        _web3=web3,
        _signer=parsed.signer,
        _addresses=addresses,
        _estimate_fees=_estimate_fees,
    )
    trades = EoaTrades(
        _web3=web3,
        _signer=parsed.signer,
        _addresses=addresses,
        _estimate_fees=_estimate_fees,
        send=send,
    )

    return EoaClient(
        mode="eoa",
        chain_id=parsed.chain_id,
        addresses=addresses,
        web3=web3,
        signer=parsed.signer,
        markets=markets,
        account=account,
        trades=trades,
    )


__all__ = [
    "EoaAccount",
    "EoaClient",
    "EoaMarkets",
    "EoaTrades",
    "EoaTradesSend",
    "create_eoa_client",
]
