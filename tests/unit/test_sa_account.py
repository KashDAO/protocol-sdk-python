"""SA account derivation + init-code tests.

Pins:

* ``encode_create_account`` produces the canonical
  ``createAccount(address,uint256)`` calldata (selector + ABI-encoded
  args). Selector is derived from :data:`SIMPLE_ACCOUNT_FACTORY_ABI` at
  import time — a future ABI rename would surface as a regression
  here.
* ``compute_smart_account_address_via_web3`` parses the right-most 20
  bytes of the 32-byte ``eth_call`` return as a checksummed address.
* ``is_smart_account_deployed`` returns ``False`` for empty bytecode
  (account not yet deployed) and ``True`` for any non-empty bytecode.
* Bad owner addresses surface ``KashConfigError(INVALID_OWNER_ADDRESS)``.
"""

from __future__ import annotations

from typing import Any

import pytest
from eth_utils import keccak  # type: ignore[attr-defined]
from hexbytes import HexBytes

from kashdao_protocol_sdk import (
    ComputeSmartAccountAddressParams,
    ErrorCode,
    KashChainError,
    KashConfigError,
    compute_smart_account_address,
    encode_create_account,
)
from kashdao_protocol_sdk.smart_account.account.address import (
    compute_smart_account_address_via_web3,
    is_smart_account_deployed,
)

# ---------------------------------------------------------------------------
# encode_create_account
# ---------------------------------------------------------------------------


class TestEncodeCreateAccount:
    def test_selector_matches_canonical_signature(self) -> None:
        owner = "0x" + "ab" * 20
        calldata = encode_create_account(owner, salt=0)
        assert calldata.startswith("0x")
        # First 4 bytes = function selector
        selector = HexBytes(calldata)[:4]
        expected = keccak(text="createAccount(address,uint256)")[:4]
        assert bytes(selector) == bytes(expected)

    def test_args_are_abi_encoded(self) -> None:
        owner = "0x" + "ab" * 20
        salt = 7
        calldata = HexBytes(encode_create_account(owner, salt=salt))
        # Selector (4 bytes) + address (32 bytes left-padded) + uint256 (32 bytes).
        assert len(calldata) == 4 + 32 + 32
        # Owner occupies the final 20 bytes of the first 32-byte word
        # (left-padded with zeros).
        owner_word = bytes(calldata[4 : 4 + 32])
        assert owner_word[:12] == b"\x00" * 12
        assert owner_word[12:].hex() == owner[2:].lower()
        # Salt is the second 32-byte word, big-endian.
        salt_word = bytes(calldata[4 + 32 :])
        assert int.from_bytes(salt_word, "big") == salt

    def test_default_salt_is_zero(self) -> None:
        owner = "0x" + "ab" * 20
        with_default = encode_create_account(owner)
        with_explicit_zero = encode_create_account(owner, salt=0)
        assert with_default == with_explicit_zero


# ---------------------------------------------------------------------------
# compute_smart_account_address (input validation)
# ---------------------------------------------------------------------------


class TestComputeSmartAccountAddressValidation:
    @pytest.mark.asyncio
    async def test_invalid_owner_address_rejected(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            await compute_smart_account_address(
                ComputeSmartAccountAddressParams(
                    chain_id=84532,
                    rpc="https://sepolia.base.org",
                    owner_address="not-an-address",  # type: ignore[arg-type]
                )
            )
        assert exc_info.value.code == ErrorCode.INVALID_OWNER_ADDRESS

    @pytest.mark.asyncio
    async def test_too_short_address_rejected(self) -> None:
        with pytest.raises(KashConfigError):
            await compute_smart_account_address(
                ComputeSmartAccountAddressParams(
                    chain_id=84532,
                    rpc="https://sepolia.base.org",
                    owner_address="0x123",
                )
            )


# ---------------------------------------------------------------------------
# compute_smart_account_address_via_web3 (return parsing)
# ---------------------------------------------------------------------------


class _FakeAsyncEth:
    """Stub ``AsyncWeb3.eth`` for unit-testing the address derivation."""

    def __init__(self, call_returns: bytes | BaseException) -> None:
        self._return = call_returns
        self._code: bytes | BaseException = b""

    async def call(self, _params: Any) -> bytes:
        if isinstance(self._return, BaseException):
            raise self._return
        return self._return

    async def get_code(self, _address: Any) -> bytes:
        if isinstance(self._code, BaseException):
            raise self._code
        return self._code


class _FakeWeb3:
    def __init__(self, eth: _FakeAsyncEth) -> None:
        self.eth = eth

    @staticmethod
    def to_checksum_address(value: Any) -> str:
        if isinstance(value, bytes):
            return "0x" + value.hex()
        return str(value).lower()


class TestComputeViaWeb3:
    @pytest.mark.asyncio
    async def test_parses_rightmost_20_bytes(self) -> None:
        # 32-byte word: 12 zero bytes + 20-byte address (0xab * 20).
        return_bytes = b"\x00" * 12 + b"\xab" * 20
        web3 = _FakeWeb3(_FakeAsyncEth(call_returns=return_bytes))
        address = await compute_smart_account_address_via_web3(
            web3,  # type: ignore[arg-type]
            factory_address="0x" + "ff" * 20,
            owner_address="0x" + "11" * 20,
            salt=0,
        )
        # to_checksum_address normalizes; just confirm hex is right.
        assert address.lower().endswith("ab" * 20)

    @pytest.mark.asyncio
    async def test_short_return_surfaces_typed(self) -> None:
        web3 = _FakeWeb3(_FakeAsyncEth(call_returns=b"\xab" * 16))  # 16 bytes
        with pytest.raises(KashChainError) as exc_info:
            await compute_smart_account_address_via_web3(
                web3,  # type: ignore[arg-type]
                factory_address="0x" + "ff" * 20,
                owner_address="0x" + "11" * 20,
            )
        assert exc_info.value.code == ErrorCode.SA_DERIVATION_FAILED

    @pytest.mark.asyncio
    async def test_rpc_failure_surfaces_typed(self) -> None:
        web3 = _FakeWeb3(_FakeAsyncEth(call_returns=RuntimeError("rpc unreachable")))
        with pytest.raises(KashChainError) as exc_info:
            await compute_smart_account_address_via_web3(
                web3,  # type: ignore[arg-type]
                factory_address="0x" + "ff" * 20,
                owner_address="0x" + "11" * 20,
            )
        assert exc_info.value.code == ErrorCode.SA_DERIVATION_FAILED
        assert exc_info.value.is_retryable is True


# ---------------------------------------------------------------------------
# is_smart_account_deployed
# ---------------------------------------------------------------------------


class TestIsSmartAccountDeployed:
    @pytest.mark.asyncio
    async def test_empty_bytecode_returns_false(self) -> None:
        eth = _FakeAsyncEth(call_returns=b"")
        eth._code = b""
        web3 = _FakeWeb3(eth)
        result = await is_smart_account_deployed(web3, "0x" + "ab" * 20)  # type: ignore[arg-type]
        assert result is False

    @pytest.mark.asyncio
    async def test_nonempty_bytecode_returns_true(self) -> None:
        eth = _FakeAsyncEth(call_returns=b"")
        eth._code = b"\x60\x80\x60\x40"  # arbitrary EVM bytecode prefix
        web3 = _FakeWeb3(eth)
        result = await is_smart_account_deployed(web3, "0x" + "ab" * 20)  # type: ignore[arg-type]
        assert result is True

    @pytest.mark.asyncio
    async def test_rpc_failure_surfaces_typed(self) -> None:
        eth = _FakeAsyncEth(call_returns=b"")
        eth._code = RuntimeError("rpc dropped")
        web3 = _FakeWeb3(eth)
        with pytest.raises(KashChainError) as exc_info:
            await is_smart_account_deployed(web3, "0x" + "ab" * 20)  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.SA_DEPLOYMENT_CHECK_FAILED
        assert exc_info.value.is_retryable is True
