"""Cross-language parity test — EOA encoding (Python side).

Reads ``tests/parity/fixtures/eoa.json`` (synced from the canonical
TypeScript SDK via ``scripts/sync-parity.py``) and asserts the local
Python encoder produces the same serialized bytes and hash for each
input. The fixture is the source of truth — both languages MUST agree.

If this test fails, the Python encoder has drifted from the canonical
TS implementation. A divergence here means a position opened via either
SDK cannot be safely closed via the other.

Run::

    pytest tests/parity/test_eoa_parity.py

Markers
-------

Decorated with the ``parity`` marker (see ``pyproject.toml``) so it can
be selected (or excluded) explicitly::

    pytest -m parity                  # parity tests only
    pytest -m "not parity"            # everything else
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kashdao_protocol_sdk import UnsignedTransaction
from kashdao_protocol_sdk.eoa.trades.hash import (
    compute_transaction_hash,
    serialize_unsigned,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "eoa.json"


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
    return list(doc["fixtures"])


def _hydrate(tx_json: dict[str, Any]) -> UnsignedTransaction:
    """Convert the camelCase JSON shape to the Python pydantic model.

    The fixture format is JS-side (camelCase, bigints as strings). Python's
    EOA ``UnsignedTransaction`` uses snake_case and ``int``, so we map
    explicitly. Bigint strings are decimal — ``int(...)`` handles them.
    """
    return UnsignedTransaction(
        chain_id=int(tx_json["chainId"]),
        to=tx_json["to"],
        data=tx_json["data"],
        value=int(tx_json["value"]),
        nonce=int(tx_json["nonce"]),
        gas=int(tx_json["gas"]),
        max_fee_per_gas=int(tx_json["maxFeePerGas"]),
        max_priority_fee_per_gas=int(tx_json["maxPriorityFeePerGas"]),
    )


@pytest.mark.parity
@pytest.mark.parametrize(
    "fixture",
    _load_fixtures(),
    ids=lambda f: f["name"],  # type: ignore[no-any-return]
)
def test_eoa_serialized_bytes_match(fixture: dict[str, Any]) -> None:
    """Python's RLP-encoded EIP-1559 envelope MUST equal the TS canonical bytes."""
    tx = _hydrate(fixture["tx"])
    serialized = serialize_unsigned(tx)
    expected_hex = fixture["expected"]["serializedHex"]
    # Strip 0x prefix and compare hex on both sides — equivalent to
    # comparing bytes but produces a readable diff on failure.
    actual_hex = "0x" + serialized.hex()
    assert actual_hex == expected_hex, (
        f"[{fixture['name']}] serialized bytes diverge from canonical TS output"
    )


@pytest.mark.parity
@pytest.mark.parametrize(
    "fixture",
    _load_fixtures(),
    ids=lambda f: f["name"],  # type: ignore[no-any-return]
)
def test_eoa_hash_matches(fixture: dict[str, Any]) -> None:
    """Python's keccak256(serialized) MUST equal the TS canonical hash."""
    tx = _hydrate(fixture["tx"])
    actual = compute_transaction_hash(tx)
    expected = fixture["expected"]["hash"]
    assert actual == expected, f"[{fixture['name']}] tx hash diverges from canonical TS output"
