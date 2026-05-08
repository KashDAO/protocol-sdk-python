"""Error-boundary integration tests — construction-time validation.

Exercises the :class:`KashConfigError` boundary at ``create_*_client``
construction time. No live RPC needed — all failures are triggered by
invalid configuration that the config validators catch before any
network I/O occurs.

Lives under the ``integration`` marker because it exercises the full
Pydantic validation chain and config-schemas pipeline end-to-end, not
just isolated unit-level validators.

What it covers
--------------

1. EOA client with an unsupported chain id (1234567) raises
   ``KashConfigError(code=ErrorCode.UNSUPPORTED_CHAIN)``.
2. SA client with a malformed bundler URL raises
   ``KashConfigError(code=ErrorCode.INVALID_CONFIG)``.
3. EOA client constructed with a plain :class:`dict` as signer (missing
   ``sign_transaction``) raises ``KashConfigError(code=ErrorCode.INVALID_SIGNER)``.
4. SA client constructed with a plain :class:`dict` as signer (missing
   ``sign_user_op_hash``) raises ``KashConfigError(code=ErrorCode.INVALID_SIGNER)``.
"""

from __future__ import annotations

import pytest
from eth_account import Account

from kashdao_protocol_sdk import (
    BundlerOptions,
    KashConfigError,
    create_eoa_client,
    create_smart_account_client,
    viem_account_eoa_signer,
    viem_account_signer,
)
from kashdao_protocol_sdk.shared.error_codes import ErrorCode

# A throwaway private key — this test never signs anything.
_TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


@pytest.mark.integration
def test_unsupported_chain_id_raises_config_error() -> None:
    """EOA client with chain id 1234567 (not in registry) raises UNSUPPORTED_CHAIN."""
    account = Account.from_key(_TEST_KEY)

    with pytest.raises(KashConfigError) as exc_info:
        create_eoa_client(
            chain_id=1234567,
            rpc="https://rpc.example.com",
            signer=viem_account_eoa_signer(account),
        )

    err = exc_info.value
    # eoa/config.py emits "UNKNOWN_CHAIN" (literal) for unregistered chain ids;
    # shared/config_schemas.py emits ErrorCode.UNSUPPORTED_CHAIN for the SA path.
    # Both indicate the chain is not in the static registry.
    assert err.code in ("UNKNOWN_CHAIN", ErrorCode.UNSUPPORTED_CHAIN)
    assert not err.is_retryable


@pytest.mark.integration
def test_sa_malformed_bundler_url_raises_config_error() -> None:
    """SA client with a plain-HTTP non-localhost bundler URL raises INVALID_CONFIG."""
    account = Account.from_key(_TEST_KEY)

    with pytest.raises(KashConfigError) as exc_info:
        create_smart_account_client(
            chain_id=84532,
            rpc="https://sepolia.base.org",
            signer=viem_account_signer(account),
            bundler=BundlerOptions(
                provider="pimlico",
                url="http://not-localhost.example.com/bundler",  # plain HTTP to remote
            ),
        )

    err = exc_info.value
    assert err.code == ErrorCode.INVALID_CONFIG
    assert not err.is_retryable


@pytest.mark.integration
def test_eoa_invalid_signer_raises_config_error() -> None:
    """EOA client constructed with a dict-signer (missing sign_transaction) raises INVALID_SIGNER."""

    class _FakeSigner:
        owner_address = "0x" + "a" * 40
        # deliberately missing sign_transaction

    with pytest.raises(KashConfigError) as exc_info:
        create_eoa_client(
            chain_id=84532,
            rpc="https://sepolia.base.org",
            signer=_FakeSigner(),  # type: ignore[arg-type]
        )

    err = exc_info.value
    assert err.code == ErrorCode.INVALID_SIGNER
    assert not err.is_retryable


@pytest.mark.integration
def test_sa_invalid_signer_raises_config_error() -> None:
    """SA client constructed with a dict-signer (missing sign_user_op_hash) raises INVALID_SIGNER."""

    class _FakeSigner:
        owner_address = "0x" + "a" * 40
        # deliberately missing sign_user_op_hash

    with pytest.raises(KashConfigError) as exc_info:
        create_smart_account_client(
            chain_id=84532,
            rpc="https://sepolia.base.org",
            signer=_FakeSigner(),  # type: ignore[arg-type]
            bundler=BundlerOptions(provider="pimlico", url="https://bundler.example.com"),
        )

    err = exc_info.value
    assert err.code == ErrorCode.INVALID_SIGNER
    assert not err.is_retryable
