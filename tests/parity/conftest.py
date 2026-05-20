"""Parity-test fixtures.

Loads byte-equality fixtures generated from the TypeScript SDK and
hands them to the parity tests. The fixture file is produced by a
generator script in the TS package
(target path: ``packages/protocol-sdk/scripts/generate-parity-fixtures.ts``)
that is mirror-published alongside this Python SDK.

Until the generator lands the fixture file does not exist and the
parity tests skip cleanly. See ``tests/parity/README.md`` for the
expected fixture format.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

#: Canonical fixture path relative to this directory.
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "userop_parity.json"


@pytest.fixture(scope="session")
def parity_fixtures() -> dict[str, Any]:
    """Load the parity fixture JSON or skip the test cleanly.

    Returns the parsed JSON object (a dict with ``version`` and
    ``vectors`` keys). Skips the test when the fixture file is absent
    so a fresh checkout doesn't fail on a missing artefact.
    """
    if not _FIXTURE_PATH.exists():
        pytest.skip(
            "parity fixtures not yet generated; see tests/parity/README.md for the fixture format"
        )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data
