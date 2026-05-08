"""``_wait_for_receipt`` resilience contract.

Pins:

* Uses ``asyncio.get_running_loop()`` (not the deprecated
  ``get_event_loop()``).
* Only swallows ``TransactionNotFound``; every other RPC exception
  propagates so misconfigured endpoints surface immediately.
* Once a receipt is in hand, transient RPC failures during the
  confirmations block-number check are NOT fatal — they get retried
  on the next tick.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from web3.exceptions import TransactionNotFound

from kashdao_protocol_sdk.eoa.trades.send import _wait_for_receipt


class _FakeAsyncEth:
    """Stub ``AsyncWeb3.eth`` for unit testing the wait loop."""

    def __init__(
        self,
        receipt_responses: list[Any],
        block_number_responses: list[Any] | None = None,
    ) -> None:
        self._receipt_responses = list(receipt_responses)
        self._block_number_responses = list(block_number_responses or [])

    async def get_transaction_receipt(self, _tx_hash: bytes) -> Any:
        if not self._receipt_responses:
            raise TransactionNotFound("no more")
        item = self._receipt_responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    async def block_number(self) -> int:
        if not self._block_number_responses:
            return 0
        item = self._block_number_responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return int(item)


class _FakeWeb3:
    def __init__(self, eth: _FakeAsyncEth) -> None:
        self.eth = eth


class TestWaitForReceipt:
    @pytest.mark.asyncio
    async def test_returns_receipt_on_first_hit_with_one_confirmation(self) -> None:
        receipt = {"blockNumber": 100, "status": 1, "gasUsed": 21_000}
        web3 = _FakeWeb3(_FakeAsyncEth(receipt_responses=[receipt]))
        result = await _wait_for_receipt(web3, "0x" + "ab" * 32, 5_000, 1, None)
        assert result == receipt

    @pytest.mark.asyncio
    async def test_swallows_transaction_not_found_and_retries(self) -> None:
        receipt = {"blockNumber": 100, "status": 1, "gasUsed": 21_000}
        web3 = _FakeWeb3(
            _FakeAsyncEth(
                receipt_responses=[
                    TransactionNotFound("not yet"),
                    TransactionNotFound("still not yet"),
                    receipt,
                ]
            )
        )
        # Patch the poll interval down so the test is fast.
        result = await asyncio.wait_for(
            _wait_for_receipt(web3, "0x" + "ab" * 32, 30_000, 1, None),
            timeout=5.0,
        )
        assert result == receipt

    @pytest.mark.asyncio
    async def test_other_rpc_errors_propagate(self) -> None:
        """Auth failure / 5xx / etc must not be silently swallowed."""

        class FakeAuthError(Exception):
            pass

        web3 = _FakeWeb3(_FakeAsyncEth(receipt_responses=[FakeAuthError("401")]))
        with pytest.raises(FakeAuthError):
            await _wait_for_receipt(web3, "0x" + "ab" * 32, 5_000, 1, None)

    @pytest.mark.asyncio
    async def test_block_number_rpc_blip_does_not_lose_receipt(self) -> None:
        """Pin that an RPC failure during the confirmations block-number check is non-fatal."""
        receipt = {"blockNumber": 100, "status": 1, "gasUsed": 21_000}
        web3 = _FakeWeb3(
            _FakeAsyncEth(
                receipt_responses=[receipt, receipt],
                block_number_responses=[
                    RuntimeError("RPC blip"),  # first read fails
                    105,  # second read succeeds → 105 - 100 + 1 = 6 ≥ 3
                ],
            )
        )
        result = await asyncio.wait_for(
            _wait_for_receipt(web3, "0x" + "ab" * 32, 30_000, 3, None),
            timeout=5.0,
        )
        assert result == receipt

    @pytest.mark.asyncio
    async def test_uses_running_loop_not_deprecated(self) -> None:
        """Smoke check that the function works under
        ``filterwarnings=["error"]`` — i.e. no DeprecationWarning from
        ``asyncio.get_event_loop()``.
        """
        receipt = {"blockNumber": 100, "status": 1, "gasUsed": 21_000}
        web3 = _FakeWeb3(_FakeAsyncEth(receipt_responses=[receipt]))
        # If the deprecated API were still in use, pyproject's
        # filterwarnings=['error'] would convert the DeprecationWarning
        # to a failure here.
        await _wait_for_receipt(web3, "0x" + "ab" * 32, 5_000, 1, None)
