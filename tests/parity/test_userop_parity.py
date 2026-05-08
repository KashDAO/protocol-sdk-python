"""Cross-language parity test — Smart-account UserOp v0.7 (Python side).

Reads ``tests/parity/fixtures/smart-account.json`` (synced from the TS
SDK via ``scripts/sync-parity.py``) and asserts the local Python
``compute_user_op_hash`` implementation produces the same hash for
each input.

The fixture is the canonical source — both languages MUST agree
byte-for-byte. A divergence here means a UserOp signed via either
SDK cannot be validated by the other (or by the EntryPoint).

Run::

    pytest tests/parity/test_userop_parity.py

Markers
-------

Decorated with the ``parity`` marker (see ``pyproject.toml``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kashdao_protocol_sdk.smart_account.trades.hash import compute_user_op_hash
from kashdao_protocol_sdk.smart_account.types import UnsignedUserOp

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "smart-account.json"


def _load_fixtures() -> list[dict[str, Any]]:
    if not FIXTURE_PATH.is_file():
        pytest.skip(
            f"parity fixture not found at {FIXTURE_PATH}; run "
            "`python scripts/sync-parity.py` to vendor it from the TS SDK."
        )
    with FIXTURE_PATH.open("rb") as fh:
        doc: dict[str, Any] = json.load(fh)
    assert doc["fixtureVersion"] == 1, (
        f"unexpected fixture version: {doc['fixtureVersion']!r}; "
        "regenerate with the matching TS generator"
    )
    assert doc["entryPointVersion"] == "0.7", (
        f"unexpected entry-point version: {doc['entryPointVersion']!r}; "
        "this test is hard-coded to v0.7"
    )
    return list(doc["fixtures"])


def _hydrate(user_op_json: dict[str, Any]) -> UnsignedUserOp:
    """Build an :class:`UnsignedUserOp` from the JSON fixture shape.

    The fixture uses camelCase keys (the JS shape). Pydantic field
    aliases handle the camelCase → snake_case conversion automatically;
    we just need to drop ``None`` values (since the model uses
    ``extra="forbid"`` but optional fields default to ``None``).
    """
    payload: dict[str, Any] = {
        "sender": user_op_json["sender"],
        "nonce": int(user_op_json["nonce"]),
        "callData": user_op_json["callData"],
        "callGasLimit": int(user_op_json["callGasLimit"]),
        "verificationGasLimit": int(user_op_json["verificationGasLimit"]),
        "preVerificationGas": int(user_op_json["preVerificationGas"]),
        "maxFeePerGas": int(user_op_json["maxFeePerGas"]),
        "maxPriorityFeePerGas": int(user_op_json["maxPriorityFeePerGas"]),
        "signature": user_op_json["signature"],
    }
    if user_op_json.get("factory") is not None:
        payload["factory"] = user_op_json["factory"]
    if user_op_json.get("factoryData") is not None:
        payload["factoryData"] = user_op_json["factoryData"]
    if user_op_json.get("paymaster") is not None:
        payload["paymaster"] = user_op_json["paymaster"]
    if user_op_json.get("paymasterVerificationGasLimit") is not None:
        payload["paymasterVerificationGasLimit"] = int(
            user_op_json["paymasterVerificationGasLimit"]
        )
    if user_op_json.get("paymasterPostOpGasLimit") is not None:
        payload["paymasterPostOpGasLimit"] = int(user_op_json["paymasterPostOpGasLimit"])
    if user_op_json.get("paymasterData") is not None:
        payload["paymasterData"] = user_op_json["paymasterData"]
    return UnsignedUserOp.model_validate(payload)


@pytest.mark.parity
@pytest.mark.parametrize(
    "fixture",
    _load_fixtures(),
    ids=lambda f: f["name"],  # type: ignore[no-any-return]
)
def test_userop_hash_matches(fixture: dict[str, Any]) -> None:
    """Python's ``compute_user_op_hash`` MUST equal the TS canonical hash."""
    user_op = _hydrate(fixture["userOp"])
    actual = compute_user_op_hash(
        chain_id=fixture["chainId"],
        entry_point_address=fixture["entryPointAddress"],
        user_op=user_op,
    )
    expected = fixture["expected"]["hash"]
    assert actual == expected, f"[{fixture['name']}] UserOp hash diverges from canonical TS output"
