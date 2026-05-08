"""SA UserOp hash determinism + sensitivity tests.

Pins:

* :func:`compute_user_op_hash` is fully deterministic (no clock, no
  randomness, no async I/O). Same inputs → same output.
* The hash is sensitive to every field that participates in the
  EntryPoint v0.7 packed encoding — flipping any of sender, nonce,
  callData, gas limits, fees, paymaster fields, or factory init code
  must produce a different hash.
* The hash is bound to the chain id and entry-point address — same
  UserOp on a different chain produces a different hash.

This is the safety net for AA24 — the EntryPoint computes the same
hash and the bundler verifies the signature against it. Drift here
would corrupt every signed UserOp silently.
"""

from __future__ import annotations

from kashdao_protocol_sdk import (
    UnsignedUserOp,
    compute_user_op_hash,
    user_op_to_typed_data,
)
from kashdao_protocol_sdk.shared.contracts.addresses import ENTRY_POINT_07_ADDRESS

_CHAIN_ID = 84532


def _user_op(**overrides: object) -> UnsignedUserOp:
    base: dict[str, object] = {
        "sender": "0x" + "ab" * 20,
        "nonce": 0,
        "callData": "0xdeadbeef",
        "callGasLimit": 100_000,
        "verificationGasLimit": 200_000,
        "preVerificationGas": 21_000,
        "maxFeePerGas": 1_000_000_000,
        "maxPriorityFeePerGas": 1_000_000_000,
    }
    base.update(overrides)
    return UnsignedUserOp(**base)  # type: ignore[arg-type]


class TestDeterminism:
    def test_same_inputs_same_hash(self) -> None:
        h1 = compute_user_op_hash(_CHAIN_ID, ENTRY_POINT_07_ADDRESS, _user_op())
        h2 = compute_user_op_hash(_CHAIN_ID, ENTRY_POINT_07_ADDRESS, _user_op())
        assert h1 == h2

    def test_returns_32_byte_hash(self) -> None:
        h = compute_user_op_hash(_CHAIN_ID, ENTRY_POINT_07_ADDRESS, _user_op())
        assert h.startswith("0x")
        # 32 bytes = 64 hex chars + "0x" prefix
        assert len(h) == 2 + 64


class TestSensitivity:
    """Each field must be a load-bearing input to the hash."""

    _BASE = compute_user_op_hash(_CHAIN_ID, ENTRY_POINT_07_ADDRESS, _user_op())

    def _assert_changes(self, **overrides: object) -> None:
        h = compute_user_op_hash(_CHAIN_ID, ENTRY_POINT_07_ADDRESS, _user_op(**overrides))
        assert h != self._BASE, f"hash unchanged for overrides={overrides!r}"

    def test_changes_on_sender(self) -> None:
        self._assert_changes(sender="0x" + "cd" * 20)

    def test_changes_on_nonce(self) -> None:
        self._assert_changes(nonce=1)

    def test_changes_on_call_data(self) -> None:
        self._assert_changes(callData="0xfeedface")

    def test_changes_on_call_gas_limit(self) -> None:
        self._assert_changes(callGasLimit=200_000)

    def test_changes_on_verification_gas_limit(self) -> None:
        self._assert_changes(verificationGasLimit=300_000)

    def test_changes_on_pre_verification_gas(self) -> None:
        self._assert_changes(preVerificationGas=42_000)

    def test_changes_on_max_fee_per_gas(self) -> None:
        self._assert_changes(maxFeePerGas=2_000_000_000)

    def test_changes_on_max_priority_fee_per_gas(self) -> None:
        self._assert_changes(maxPriorityFeePerGas=2_000_000_000)

    def test_changes_on_factory_init_code(self) -> None:
        # Add factory + factoryData → new initCode → different hash
        self._assert_changes(
            factory="0x" + "ff" * 20,
            factoryData="0x" + "11" * 32,
        )

    def test_changes_on_paymaster(self) -> None:
        self._assert_changes(
            paymaster="0x" + "ee" * 20,
            paymasterVerificationGasLimit=10_000,
            paymasterPostOpGasLimit=10_000,
            paymasterData="0x",
        )

    def test_changes_on_chain_id(self) -> None:
        h = compute_user_op_hash(8453, ENTRY_POINT_07_ADDRESS, _user_op())
        assert h != self._BASE

    def test_changes_on_entry_point_address(self) -> None:
        # A different EntryPoint produces a different hash (binding
        # check). Use any unrelated 20-byte hex address.
        other_ep = "0x" + "12" * 20
        h = compute_user_op_hash(_CHAIN_ID, other_ep, _user_op())
        assert h != self._BASE


class TestTypedData:
    def test_emits_eip4337_domain(self) -> None:
        td = user_op_to_typed_data(_CHAIN_ID, ENTRY_POINT_07_ADDRESS, _user_op())
        assert td.domain["name"] == "ERC4337"
        assert td.domain["version"] == "0.7.0"
        assert td.domain["chainId"] == _CHAIN_ID
        assert td.domain["verifyingContract"] == ENTRY_POINT_07_ADDRESS

    def test_primary_type_is_packed_user_operation(self) -> None:
        td = user_op_to_typed_data(_CHAIN_ID, ENTRY_POINT_07_ADDRESS, _user_op())
        assert td.primary_type == "PackedUserOperation"
