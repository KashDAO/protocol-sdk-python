"""EOA mode types — vanilla EIP-1559.

Mirrors the EOA-specific portion of
``src/eoa/types.ts``.

EOA mode submits a regular EIP-1559 transaction directly to the
consumer's RPC via ``eth_sendRawTransaction``. The trader IS the
signing EOA — there's no SimpleAccount indirection, no bundler, no
ERC-4337 verification overhead. Best-fit for market makers running
existing tx-signing infrastructure (web3signer, Fireblocks-direct,
AWS-KMS, ethers / viem account, hardware wallets).

Smart Account types live in :mod:`kashdao_protocol_sdk.smart_account.types`.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from kashdao_protocol_sdk.shared.types import Hex

# ---------------------------------------------------------------------------
# Transaction shapes
# ---------------------------------------------------------------------------


class UnsignedTransaction(BaseModel):
    """EIP-1559 unsigned transaction, as the library builds it.

    Mirrors viem's ``TransactionSerializableEIP1559``. Gas / fee fields
    default to ``0`` at build time and are populated by ``prepare_*``
    via ``eth_estimateGas`` and ``estimate_chain_fees`` (or by the
    consumer manually before signing).

    The library does not populate ``access_list`` for trades.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Discriminator that matches the TS side's `type: 'eip1559'`
    # literal. EIP-1559 is the only tx kind protocol-sdk-python builds
    # today; the field exists for cross-language parity and so
    # consumers can branch on it in handlers that may later see
    # `'eip4844'` or new kinds without changing the SDK contract.
    type: Literal["eip1559"] = "eip1559"
    chain_id: int
    to: Hex
    data: Hex
    value: int = 0
    nonce: int
    gas: int = 0
    max_fee_per_gas: int = 0
    max_priority_fee_per_gas: int = 0


class BuiltTransaction(BaseModel):
    """Result of a ``client.trades.build_*_transaction`` call.

    ``transaction_hash`` is the **build-time** hash. It goes stale once
    gas / fee fields are populated. Use
    ``client.trades.hash_of(transaction)`` to recompute, or use
    ``client.trades.prepare_*`` which handles this automatically
    (recommended).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction: UnsignedTransaction
    transaction_hash: Hex


# ---------------------------------------------------------------------------
# Build / prepare / submit / send option types
# ---------------------------------------------------------------------------


class EoaTxOverrides(BaseModel):
    """Per-call EOA tx overrides for ``build_*_transaction``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gas: int | None = None
    max_fee_per_gas: int | None = None
    max_priority_fee_per_gas: int | None = None
    nonce: int | None = None


class PrepareEoaFeeOverrides(BaseModel):
    """Fee overrides for ``prepare_*``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_fee_per_gas: int | None = None
    max_priority_fee_per_gas: int | None = None


class PrepareEoaOptions(BaseModel):
    """Options for the ``prepare_*`` lifecycle.

    Setting ``gas`` skips ``eth_estimateGas``. ``simulate=True`` runs
    an ``eth_call`` pre-flight before returning, raising
    :class:`KashSimulationRevertedError` on revert.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    gas: int | None = None
    fee_overrides: PrepareEoaFeeOverrides | None = None
    nonce: int | None = None
    simulate: bool = False


class EoaSubmitOptions(BaseModel):
    """Options for ``client.trades.submit``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skip_staleness_check: bool = False


class EoaSubmitResult(BaseModel):
    """Result of ``client.trades.submit``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_hash: Hex


class SendEoaOptions(BaseModel):
    """Options for ``client.trades.send.{buy,sell,close_position,approve}``.

    ``simulate`` defaults to ``True`` on the send path (matches the TS
    reference) — set to ``False`` to skip the pre-flight ``eth_call``
    when the caller already simulated (or wants to save the round-trip
    on a hot loop). ``submit`` lets the caller opt out of the staleness
    guard via ``EoaSubmitOptions(skip_staleness_check=True)``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    gas: int | None = None
    fee_overrides: PrepareEoaFeeOverrides | None = None
    nonce: int | None = None
    simulate: bool = True
    submit: EoaSubmitOptions | None = None
    wait: bool = True
    wait_timeout_ms: int = 90_000
    wait_confirmations: int = 1


class SendEoaResultFireAndForget(BaseModel):
    """``send.*`` result with ``wait=False``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_hash: Hex


class SendEoaResultWaited(BaseModel):
    """``send.*`` result with ``wait=True`` (the default)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_hash: Hex
    block_number: int
    success: bool
    gas_used: int


SendEoaResult = SendEoaResultFireAndForget | SendEoaResultWaited


# ---------------------------------------------------------------------------
# Signer adapter
# ---------------------------------------------------------------------------


@runtime_checkable
class EoaSignerAdapter(Protocol):
    """EOA-mode signer adapter contract.

    Mirrors TS ``EoaSignerAdapter``. The signer is identified by its
    EOA address; the library never holds the underlying key. The
    ``sign_transaction`` method accepts an :class:`UnsignedTransaction`
    and returns a fully-signed serialized hex string suitable for
    ``eth_sendRawTransaction``.

    Reference adapters in :mod:`kashdao_protocol_sdk.eoa.signers`:

    * ``LocalEoaSigner`` — local key via ``eth_account.Account``;
      mirrors ``viemAccountEoaSigner``.
    * ``JsonRpcEoaSigner`` — forwards to a remote ``eth_signTransaction``;
      mirrors ``jsonRpcEoaSigner``.
    """

    @property
    def owner_address(self) -> Hex:
        """The signing EOA's address."""

    def sign_transaction(self, transaction: UnsignedTransaction) -> Awaitable[Hex]:
        """Sign and serialize a populated EIP-1559 transaction.

        Returns the 0x-prefixed hex of the signed serialized tx (RLP
        with EIP-1559 envelope, type 0x02), ready for
        ``eth_sendRawTransaction``.
        """


__all__ = [
    "BuiltTransaction",
    "EoaSignerAdapter",
    "EoaSubmitOptions",
    "EoaSubmitResult",
    "EoaTxOverrides",
    "PrepareEoaFeeOverrides",
    "PrepareEoaOptions",
    "SendEoaOptions",
    "SendEoaResult",
    "SendEoaResultFireAndForget",
    "SendEoaResultWaited",
    "UnsignedTransaction",
]
