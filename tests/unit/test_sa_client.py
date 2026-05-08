"""SA client factory + namespace shape tests.

Pins:

* Missing ``chain_id`` / ``rpc`` / ``signer`` → typed
  :class:`KashConfigError(MISSING_CLIENT_CONFIG)`.
* Bundler URL validation flows through the shared validator.
* Bundler provider whitelist is enforced.
* The constructed client exposes the expected sub-namespaces and
  ``async with`` closes the bundler connection pool.
"""

from __future__ import annotations

import pytest

from kashdao_protocol_sdk import (
    BundlerOptions,
    ErrorCode,
    KashConfigError,
    LocalSigner,
    SmartAccountClient,
    create_smart_account_client,
)

_TEST_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _signer() -> LocalSigner:
    return LocalSigner.from_private_key(_TEST_KEY)


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


class TestMissingArgs:
    def test_missing_signer(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            create_smart_account_client(chain_id=84532, rpc="https://x")
        assert exc_info.value.code == ErrorCode.MISSING_CLIENT_CONFIG
        assert "signer" in exc_info.value.context["missing"]  # type: ignore[index]

    def test_missing_chain_id(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            create_smart_account_client(rpc="https://x", signer=_signer())
        assert exc_info.value.code == ErrorCode.MISSING_CLIENT_CONFIG
        assert "chain_id" in exc_info.value.context["missing"]  # type: ignore[index]


class TestBundlerValidation:
    def test_bundler_url_must_be_https(self) -> None:
        """Plain-http bundler URLs are rejected."""
        with pytest.raises(KashConfigError) as exc_info:
            create_smart_account_client(
                chain_id=84532,
                rpc="https://chain.example.com",
                signer=_signer(),
                bundler="http://insecure.example.com",
            )
        assert exc_info.value.code == ErrorCode.INVALID_CONFIG

    def test_unknown_bundler_provider_rejected(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            create_smart_account_client(
                chain_id=84532,
                rpc="https://chain.example.com",
                signer=_signer(),
                bundler=BundlerOptions(provider="unknown", url="https://b.example.com"),
            )
        assert exc_info.value.code == ErrorCode.INVALID_CONFIG


class TestChainIdValidation:
    def test_unsupported_chain_id_rejected(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            create_smart_account_client(
                chain_id=999_999,
                rpc="https://chain.example.com",
                signer=_signer(),
            )
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_CHAIN


# ---------------------------------------------------------------------------
# Constructed-client surface
# ---------------------------------------------------------------------------


class TestConstructedClient:
    def test_factory_returns_smart_account_client(self) -> None:
        client = create_smart_account_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_signer(),
            bundler=BundlerOptions(provider="alchemy", url="https://bundler.example.com"),
        )
        assert isinstance(client, SmartAccountClient)
        assert client.mode == "smart-account"
        assert client.chain_id == 84532

    def test_exposes_namespaces(self) -> None:
        client = create_smart_account_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_signer(),
            bundler="https://bundler.example.com",
        )
        # markets / account / trades / trades.send
        assert hasattr(client.markets, "get")
        assert hasattr(client.markets, "watch")
        assert hasattr(client.account, "compute_address")
        assert hasattr(client.account, "is_deployed")
        assert hasattr(client.trades, "build_buy")
        assert hasattr(client.trades, "prepare_buy")
        assert hasattr(client.trades, "simulate")
        assert hasattr(client.trades, "submit")
        assert hasattr(client.trades, "hash_of")
        assert hasattr(client.trades.send, "buy")
        assert hasattr(client.trades.send, "sell")
        assert hasattr(client.trades.send, "close_position")
        assert hasattr(client.trades.send, "approve")

    @pytest.mark.asyncio
    async def test_aclose_closes_bundler(self) -> None:
        client = create_smart_account_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_signer(),
            bundler="https://bundler.example.com",
        )
        assert client.bundler._http.is_closed is False
        await client.aclose()
        assert client.bundler._http.is_closed is True

    @pytest.mark.asyncio
    async def test_async_context_manager_closes(self) -> None:
        async with create_smart_account_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_signer(),
            bundler="https://bundler.example.com",
        ) as client:
            assert client.bundler._http.is_closed is False
        assert client.bundler._http.is_closed is True

    def test_read_only_construction_no_bundler(self) -> None:
        """Construction without a bundler is allowed for read-only consumers."""
        client = create_smart_account_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_signer(),
        )
        # Any markets.* / account.* call would work; trades.* would
        # surface the missing-bundler error on first use, but no error
        # at construction.
        assert client.bundler is not None  # placeholder client
