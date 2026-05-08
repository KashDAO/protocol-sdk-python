"""Smart Account mode types — ERC-4337 v0.7.

Mirrors the SA-specific portion of
``src/smart-account/types.ts``.

Smart Account mode wraps trade calldata in
``SimpleAccount.execute(market, 0, market_calldata)`` and submits a
``UserOperation`` through an ERC-4337 v0.7 bundler. EOA mode (in
:mod:`kashdao_protocol_sdk.eoa.types`) submits a vanilla EIP-1559
transaction directly.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from kashdao_protocol_sdk.shared.types import Hex

# ---------------------------------------------------------------------------
# UserOperation
# ---------------------------------------------------------------------------


class UnsignedUserOp(BaseModel):
    """ERC-4337 v0.7 unpacked UserOperation, as the library builds it.

    Mirrors ``UnsignedUserOp`` in the TS SDK. Optional fields
    (``factory``, ``factory_data``, ``paymaster*``) are populated only
    when meaningful: deploy-on-first-use accounts include
    ``factory``/``factory_data``; sponsored ops include ``paymaster*``.
    The non-custodial path leaves all paymaster fields ``None``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sender: Hex
    nonce: int
    factory: Hex | None = None
    factory_data: Hex | None = Field(default=None, alias="factoryData")
    call_data: Hex = Field(alias="callData")
    call_gas_limit: int = Field(alias="callGasLimit")
    verification_gas_limit: int = Field(alias="verificationGasLimit")
    pre_verification_gas: int = Field(alias="preVerificationGas")
    max_fee_per_gas: int = Field(alias="maxFeePerGas")
    max_priority_fee_per_gas: int = Field(alias="maxPriorityFeePerGas")
    paymaster: Hex | None = None
    paymaster_verification_gas_limit: int | None = Field(
        default=None, alias="paymasterVerificationGasLimit"
    )
    paymaster_post_op_gas_limit: int | None = Field(default=None, alias="paymasterPostOpGasLimit")
    paymaster_data: Hex | None = Field(default=None, alias="paymasterData")
    # `0x` until the consumer signs, then a 65-byte ECDSA signature.
    signature: Hex = "0x"


class UserOpTypedData(BaseModel):
    """EIP-712 typed-data view of an ERC-4337 v0.7 UserOperation.

    Optional path for signers that prefer EIP-712 over raw-hash signing
    (some Fireblocks workspaces, some KMS wrappers). Most signers will
    just sign ``BuiltUserOp.user_op_hash`` directly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: dict[str, Any]
    types: dict[str, list[dict[str, str]]]
    primary_type: Literal["PackedUserOperation"] = Field(alias="primaryType")
    message: UnsignedUserOp


class BuiltUserOp(BaseModel):
    """Result of a ``client.trades.build_*_user_op`` call.

    ``user_op_hash`` is the **build-time** hash; it goes stale once gas
    fields are populated. Use ``client.trades.hash_of(user_op)`` to
    recompute after population, or use ``client.trades.prepare_*`` which
    handles this automatically (recommended).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_op: UnsignedUserOp
    user_op_hash: Hex
    typed_data: UserOpTypedData


class SignedUserOp(BaseModel):
    """A UserOp with a populated signature, ready for the bundler."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_op: UnsignedUserOp
    signature: Hex


# ---------------------------------------------------------------------------
# Build / prepare / submit / send option types
# ---------------------------------------------------------------------------


class GasOverrides(BaseModel):
    """Per-call gas overrides for ``build_*_user_op``.

    Zero defaults are intentional — consumers MUST replace these via
    :meth:`BundlerClient.estimate_gas` (or hard-coded values) before
    signing. Submitting a UserOp with all-zero gas is rejected by every
    bundler.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_gas_limit: int | None = None
    verification_gas_limit: int | None = None
    pre_verification_gas: int | None = None
    max_fee_per_gas: int | None = None
    max_priority_fee_per_gas: int | None = None


class BuildOptions(BaseModel):
    """Options accepted by every ``build_*_user_op``.

    ``nonce_key`` selects the EntryPoint nonce stream — independent of
    the SA's CREATE2 salt (which is for address derivation, not nonce
    routing). Most consumers leave both at 0; advanced parallel-UserOp
    workloads use ``nonce_key`` to keep concurrent UserOps in
    independent streams.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    nonce_key: int = 0
    """EntryPoint v0.7 nonce key (uint192). Default 0."""

    gas: GasOverrides | None = None


class PrepareFeeOverrides(BaseModel):
    """Per-call fee overrides for ``prepare_*``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_fee_per_gas: int | None = None
    max_priority_fee_per_gas: int | None = None


class SubmitOptions(BaseModel):
    """Options for ``client.trades.submit``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skip_staleness_check: bool = False


class SubmitResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_op_hash: Hex


class SendResultFireAndForget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_op_hash: Hex


class SendResultWaited(BaseModel):
    """Includes the bundler-returned receipt when ``wait=True``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_op_hash: Hex
    receipt: UserOpReceipt


SendResult = SendResultFireAndForget | SendResultWaited


# ---------------------------------------------------------------------------
# Bundler types
# ---------------------------------------------------------------------------


class GasEstimate(BaseModel):
    """Result of ``BundlerClient.estimate_gas``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pre_verification_gas: int
    verification_gas_limit: int
    call_gas_limit: int
    paymaster_verification_gas_limit: int | None = None
    paymaster_post_op_gas_limit: int | None = None


class BundlerHealthOk(BaseModel):
    """Successful :py:meth:`BundlerClient.health` probe."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chain_id: int
    latency_ms: int
    ok: Literal[True] = True


class BundlerHealthError(BaseModel):
    """Failed :py:meth:`BundlerClient.health` probe.

    Holds the wrapped error so consumers can inspect upstream failure
    codes. ``health()`` itself never raises — it returns this shape on
    any failure so status pages can render a "down" card without
    try/except.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    error: BaseException
    latency_ms: int
    ok: Literal[False] = False


BundlerHealth = BundlerHealthOk | BundlerHealthError
"""Discriminated union over :py:meth:`BundlerClient.health` results.

Mirrors the TS reference's ``{ ok: true, chainId, latencyMs }`` / ``{
ok: false, error, latencyMs }`` shape. Match on ``health.ok`` to access
the mode-specific fields.
"""


class UserOpReceipt(BaseModel):
    """Shape returned by ``eth_getUserOperationReceipt`` (ERC-4337 v0.7)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    user_op_hash: Hex = Field(alias="userOpHash")
    entry_point: Hex = Field(alias="entryPoint")
    sender: Hex
    nonce: Hex
    success: bool
    actual_gas_cost: Hex = Field(alias="actualGasCost")
    actual_gas_used: Hex = Field(alias="actualGasUsed")
    receipt: dict[str, Any]
    logs: list[dict[str, Any]]


# Resolve forward reference so SendResultWaited can hold UserOpReceipt.
SendResultWaited.model_rebuild()


# ---------------------------------------------------------------------------
# Signer adapter
# ---------------------------------------------------------------------------


@runtime_checkable
class SmartAccountSignerAdapter(Protocol):
    """SA-mode signer adapter contract.

    Required ``sign_user_op_hash`` keeps the contract minimal so HSM,
    web3signer, and Fireblocks-via-RPC integrations don't pay any
    library tax. Optional ``sign_typed_data_v4`` is provided for
    signers that cannot expose raw-hash signing but do support EIP-712
    (notable: some Fireblocks workspace policies, AWS-KMS via certain
    wrappers).

    Reference adapters in :mod:`kashdao_protocol_sdk.smart_account.signers`:

    * ``LocalSigner`` — local key via ``eth_account``; mirrors
      ``viemAccountSigner``.
    * ``JsonRpcSigner`` — forwards to a remote ``personal_sign`` /
      ``eth_signTypedData_v4``; mirrors ``jsonRpcSigner``.

    Implementations MUST return a 65-byte 0x-prefixed hex signature
    compatible with the smart account's verification step (typically
    ECDSA, EIP-191 hash for ``personal_sign``, raw for ``eth_sign``).
    """

    @property
    def owner_address(self) -> Hex:
        """EOA address that owns the smart account."""

    def sign_user_op_hash(self, user_op_hash: Hex) -> Awaitable[Hex]:
        """Sign the ERC-4337 UserOp hash directly. Required."""


# Convenience alias for SA-mode-only consumers who don't also import
# the EOA signer adapter and prefer the shorter name. New code should
# prefer :class:`SmartAccountSignerAdapter` (unambiguous across modes);
# this alias is purely additive.
SignerAdapter = SmartAccountSignerAdapter


__all__ = [
    "BuildOptions",
    "BuiltUserOp",
    "BundlerHealth",
    "BundlerHealthError",
    "BundlerHealthOk",
    "GasEstimate",
    "GasOverrides",
    "PrepareFeeOverrides",
    "SendResult",
    "SendResultFireAndForget",
    "SendResultWaited",
    "SignedUserOp",
    "SignerAdapter",
    "SmartAccountSignerAdapter",
    "SubmitOptions",
    "SubmitResult",
    "UnsignedUserOp",
    "UserOpReceipt",
    "UserOpTypedData",
]
