"""``EoaClient`` lifecycle tests — pin the SA-parity contract.

EOA mode is the canonical Hummingbot path; consumers MUST be able to
close the client cleanly to avoid socket accumulation across
strategy-restart epochs. This file mirrors the SA-mode lifecycle
tests:

* ``async with create_eoa_client(...) as client`` releases the web3
  transport on exit.
* :meth:`EoaClient.aclose` runs the signer close inside ``try`` and
  the web3 close in ``finally``, so a flaky signer teardown can't
  orphan the chain RPC connection.
* :class:`LocalEoaSigner` has no ``aclose`` — :meth:`EoaClient.aclose`
  duck-types the call and skips when missing.
* :class:`JsonRpcEoaSigner` (when used) gets its ``httpx.AsyncClient``
  closed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kashdao_protocol_sdk import (
    EoaClient,
    LocalEoaSigner,
    create_eoa_client,
)

_TEST_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _signer() -> LocalEoaSigner:
    return LocalEoaSigner.from_private_key(_TEST_KEY)


class TestEoaClientHasAclose:
    """Pin that EoaClient exposes ``aclose`` for httpx lifecycle parity with the SA client."""

    def test_eoa_client_exposes_aclose(self) -> None:
        assert hasattr(EoaClient, "aclose")
        assert callable(EoaClient.aclose)

    def test_eoa_client_supports_async_with(self) -> None:
        assert hasattr(EoaClient, "__aenter__")
        assert hasattr(EoaClient, "__aexit__")


class TestEoaAcloseLifecycle:
    @pytest.mark.asyncio
    async def test_async_with_calls_aclose(self) -> None:
        """The ``__aexit__`` hook must call ``aclose`` so consumers
        using ``async with`` get the same cleanup as explicit close.
        """
        called: list[bool] = []

        async def fake_close(_web3: object) -> None:
            called.append(True)

        with patch(
            "kashdao_protocol_sdk.eoa.client.close_web3_provider",
            new=AsyncMock(side_effect=fake_close),
        ):
            async with create_eoa_client(
                chain_id=84532,
                rpc="https://chain.example.com",
                signer=_signer(),
            ):
                pass

        assert called == [True]

    @pytest.mark.asyncio
    async def test_local_signer_no_aclose_is_handled(self) -> None:
        """:class:`LocalEoaSigner` has no ``aclose`` — the EOA client
        must duck-type the call without raising.
        """
        client = create_eoa_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_signer(),
        )
        # Must not raise even though the signer has no aclose.
        await client.aclose()

    @pytest.mark.asyncio
    async def test_web3_closes_even_if_signer_close_raises(self) -> None:
        """If the configured signer exposes ``aclose`` and that raises,
        the web3 transport MUST still get closed (try/finally invariant).
        """

        class _FakeRaisingSigner:
            owner_address = "0x" + "ab" * 20

            async def sign_transaction(self, _tx: object) -> str:
                return "0x"

            async def aclose(self) -> None:
                raise RuntimeError("simulated signer crash")

        client = create_eoa_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_FakeRaisingSigner(),  # type: ignore[arg-type]
        )

        web3_close_called: list[bool] = []

        async def fake_web3_close(_web3: object) -> None:
            web3_close_called.append(True)

        with patch(
            "kashdao_protocol_sdk.eoa.client.close_web3_provider",
            new=AsyncMock(side_effect=fake_web3_close),
        ):
            with pytest.raises(RuntimeError, match="simulated signer crash"):
                await client.aclose()

        assert web3_close_called == [True], (
            "web3 close MUST run even if signer aclose raised — try/finally invariant"
        )

    @pytest.mark.asyncio
    async def test_aclose_is_idempotent_local_signer(self) -> None:
        """Multiple ``aclose`` calls with :class:`LocalEoaSigner` (no
        signer-side connection pool) must succeed without error.
        """
        client = create_eoa_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=_signer(),
        )
        await client.aclose()
        await client.aclose()  # second close MUST not raise

    @pytest.mark.asyncio
    async def test_aclose_is_idempotent_json_rpc_signer(self) -> None:
        """When the configured signer is :class:`JsonRpcEoaSigner`
        (which owns an httpx pool), double-close MUST also succeed.
        Closing httpx twice is safe — but pin the contract.
        """
        from kashdao_protocol_sdk import json_rpc_eoa_signer

        signer = json_rpc_eoa_signer(rpc="https://signer.example.com", address="0x" + "ab" * 20)
        client = create_eoa_client(
            chain_id=84532,
            rpc="https://chain.example.com",
            signer=signer,
        )
        await client.aclose()
        await client.aclose()  # second close MUST not raise
