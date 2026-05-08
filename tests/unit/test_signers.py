"""Signer adapter contract tests.

Pins:

* C2 — ``JsonRpcEoaSigner`` lifecycle: aclose / async-with releases
  the underlying ``httpx.AsyncClient``; passing in an external client
  leaves it untouched.
* C4 — ``LocalEoaSigner.sign_transaction`` does not block the event loop.
* S10 — ``JsonRpcEoaSignerConfig.headers`` is read-only after
  construction (MappingProxyType).
"""

from __future__ import annotations

import asyncio
from types import MappingProxyType

import httpx
import pytest

from kashdao_protocol_sdk import (
    JsonRpcEoaSigner,
    JsonRpcEoaSignerConfig,
    LocalEoaSigner,
    json_rpc_eoa_signer,
)

_TEST_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


class TestLocalEoaSigner:
    def test_from_private_key_hex_string_works(self) -> None:
        """Pin that the README docstring example actually runs."""
        signer = LocalEoaSigner.from_private_key(_TEST_KEY)
        assert isinstance(signer.owner_address, str)
        assert signer.owner_address.startswith("0x")
        assert len(signer.owner_address) == 42

    def test_from_private_key_bytes_works(self) -> None:
        signer = LocalEoaSigner.from_private_key(b"\x01" * 32)
        assert signer.owner_address.startswith("0x")

    @pytest.mark.asyncio
    async def test_sign_does_not_block_event_loop(self) -> None:
        """While signing, other coroutines must keep ticking.

        Differentiator: replace the underlying ``_account`` with a
        fake whose ``sign_transaction`` does ``time.sleep(0.3)`` —
        a real CPU sleep that DOES block the event loop if called
        inline. Spawn a wall-clock ticker concurrently and assert
        the ticker observes meaningful elapsed time *during* the
        sign — proving signing ran in the executor and yielded the
        loop. Without ``run_in_executor`` (C4 regression), the ticker
        would be starved for the full 300ms and tick zero times.
        """
        import time
        from dataclasses import dataclass as _dc
        from typing import Any

        from kashdao_protocol_sdk.eoa.types import UnsignedTransaction

        sleep_seconds = 0.3

        class _FakeSignedTx:
            raw_transaction = b"\x02"

            def __init__(self) -> None:
                # mimic HexBytes.to_0x_hex
                self.raw_transaction = type("RT", (), {"to_0x_hex": lambda self: "0xfeed"})()  # type: ignore[assignment]

        @_dc
        class _FakeAccount:
            address: str = "0x" + "ab" * 20

            def sign_transaction(self, _payload: Any) -> Any:
                time.sleep(sleep_seconds)
                return _FakeSignedTx()

        signer = LocalEoaSigner(_account=_FakeAccount())  # type: ignore[arg-type]

        tx = UnsignedTransaction(
            chain_id=84532,
            to="0x" + "00" * 20,
            data="0x",
            value=0,
            nonce=0,
            gas=21_000,
            max_fee_per_gas=1_000_000_000,
            max_priority_fee_per_gas=1_000_000_000,
        )

        ticks_during_sign = 0

        async def _ticker(stop_at: float) -> None:
            nonlocal ticks_during_sign
            tick_loop = asyncio.get_running_loop()
            while tick_loop.time() < stop_at:
                await asyncio.sleep(0.01)
                ticks_during_sign += 1

        loop = asyncio.get_running_loop()
        deadline = loop.time() + sleep_seconds
        ticker_task = asyncio.create_task(_ticker(deadline))
        # If sign blocks the loop, the ticker is starved for the full
        # 300ms and ticks_during_sign stays at 0. With run_in_executor,
        # we expect ~30 ticks (10ms each) during 300ms sign.
        result = await signer.sign_transaction(tx)
        await ticker_task

        assert result.startswith("0x")
        assert ticks_during_sign >= 5, (
            f"Event loop was blocked during sign; only {ticks_during_sign} ticks "
            f"observed during {sleep_seconds * 1000:.0f}ms sign — would expect ≥5 "
            "if run_in_executor was used."
        )


class TestJsonRpcEoaSignerLifecycle:
    @pytest.mark.asyncio
    async def test_aclose_closes_owned_client(self) -> None:
        signer = json_rpc_eoa_signer(rpc="https://x", address="0x" + "ab" * 20)
        # `is_closed` is httpx's public-ish flag.
        assert signer._http.is_closed is False
        await signer.aclose()
        assert signer._http.is_closed is True

    @pytest.mark.asyncio
    async def test_async_context_manager_closes(self) -> None:
        async with json_rpc_eoa_signer(rpc="https://x", address="0x" + "ab" * 20) as signer:
            assert signer._http.is_closed is False
        assert signer._http.is_closed is True

    @pytest.mark.asyncio
    async def test_external_client_not_closed(self) -> None:
        """Caller supplied the client → caller owns the lifecycle."""
        external = httpx.AsyncClient()
        signer = json_rpc_eoa_signer(
            rpc="https://x",
            address="0x" + "ab" * 20,
            http_client=external,
        )
        await signer.aclose()
        # External client untouched.
        assert external.is_closed is False
        await external.aclose()


class TestJsonRpcEoaSignerConfig:
    def test_headers_become_read_only(self) -> None:
        original = {"Authorization": "Bearer x"}
        config = JsonRpcEoaSignerConfig(
            rpc="https://x",
            address="0x" + "ab" * 20,
            headers=original,
        )
        assert isinstance(config.headers, MappingProxyType)

    def test_external_dict_mutation_does_not_leak(self) -> None:
        original = {"Authorization": "Bearer x"}
        config = JsonRpcEoaSignerConfig(
            rpc="https://x",
            address="0x" + "ab" * 20,
            headers=original,
        )
        original["Authorization"] = "Bearer hijacked"
        # MappingProxyType wraps a *copy* — original mutation does not
        # leak into the signer's view.
        assert config.headers["Authorization"] == "Bearer x"

    def test_consumer_cannot_mutate_after_construction(self) -> None:
        config = JsonRpcEoaSignerConfig(
            rpc="https://x",
            address="0x" + "ab" * 20,
            headers={"Authorization": "Bearer x"},
        )
        with pytest.raises(TypeError):
            config.headers["Authorization"] = "tamper"  # type: ignore[index]


class TestJsonRpcEoaSigner:
    """Sanity check for the type."""

    def test_is_imported_from_barrel(self) -> None:
        # Smoke check that the public name resolves (regression guard
        # against barrel-export drift).
        assert JsonRpcEoaSigner is not None
