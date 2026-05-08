"""Factory-function configuration errors must be typed Kash errors.

Plain ``ValueError`` would leak through ``except KashError`` blocks in
consumer code. These tests pin the typed error contract so a future
refactor can't silently regress.
"""

from __future__ import annotations

import pytest

from kashdao_protocol_sdk import (
    KashConfigError,
    create_eoa_client,
    json_rpc_eoa_signer,
)


class TestCreateEoaClientMissingArgs:
    @pytest.mark.asyncio
    async def test_missing_signer_raises_kash_config_error(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            await create_eoa_client(chain_id=84532, rpc="https://sepolia.base.org")
        assert exc_info.value.code == "MISSING_CLIENT_CONFIG"
        assert exc_info.value.context["missing"] == ["signer"]

    @pytest.mark.asyncio
    async def test_missing_chain_id_raises_kash_config_error(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            await create_eoa_client(rpc="https://sepolia.base.org", signer=object())  # type: ignore[arg-type]
        assert exc_info.value.code == "MISSING_CLIENT_CONFIG"
        assert "chain_id" in exc_info.value.context["missing"]

    @pytest.mark.asyncio
    async def test_missing_all_three_lists_them_all(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            await create_eoa_client()
        missing = exc_info.value.context["missing"]
        assert set(missing) == {"chain_id", "rpc", "signer"}


class TestJsonRpcEoaSignerMissingArgs:
    def test_missing_address_raises_kash_config_error(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            json_rpc_eoa_signer(rpc="https://signer.example.com")
        assert exc_info.value.code == "MISSING_SIGNER_CONFIG"
        assert exc_info.value.context["missing"] == ["address"]

    def test_missing_rpc_raises_kash_config_error(self) -> None:
        with pytest.raises(KashConfigError) as exc_info:
            json_rpc_eoa_signer(address="0x" + "ab" * 20)
        assert exc_info.value.code == "MISSING_SIGNER_CONFIG"
        assert exc_info.value.context["missing"] == ["rpc"]
