#!/usr/bin/env python3
"""Vendor cross-language parity fixtures into the Python SDK tree.

The TypeScript SDK is the canonical implementation. It emits parity
fixtures via a generator in the `@kashdao/protocol-sdk` source
(https://github.com/KashDAO/protocol-sdk-typescript) which records
the expected serialized bytes (EOA) and UserOp hashes (SA) for a
hand-crafted set of inputs. The Python SDK consumes the SAME JSON
files and asserts its local re-encoding produces identical output —
that guarantees a position opened via either SDK can be closed via
the other.

Pipeline
--------

    @kashdao/protocol-sdk (TS) parity-fixture generator
            |
            | (this script)
            v
    tests/parity/fixtures/*.json

Modes
-----

``python scripts/sync-parity.py``
    Copy/refresh the fixtures.

``python scripts/sync-parity.py --check``
    Verify the on-disk fixtures match the TS-side source. Exit 0 on
    match, 1 on drift. Used as a pre-release gate.

Notes
-----
- The fixture format is documented at the top of the TS generator
  script and mirrors a ``fixtureVersion`` so we can evolve the schema
  with a coordinated bump.
- The Python parity test (``tests/parity/test_eoa_parity.py``,
  ``tests/parity/test_userop_parity.py``) reads from this destination
  path. Don't move the destination without updating the test imports.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TS_FIXTURES_DIR = REPO_ROOT / "packages" / "protocol-sdk" / "tests" / "parity" / "fixtures"
PY_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "parity" / "fixtures"

FIXTURE_FILES = ("eoa.json", "smart-account.json")


def copy_fixtures(*, check: bool) -> int:
    """Copy fixtures from the TS source-of-truth to the Python tree.

    Returns ``0`` on success / no drift, ``1`` on drift in ``--check``
    mode.
    """
    if not TS_FIXTURES_DIR.is_dir():
        sys.stderr.write(
            f"sync-parity: TS fixture dir not found at {TS_FIXTURES_DIR}.\n"
            "Run `pnpm --filter @kashdao/protocol-sdk parity:fixtures` "
            "in the monorepo first.\n"
        )
        return 1

    PY_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    drift = []
    for name in FIXTURE_FILES:
        src = TS_FIXTURES_DIR / name
        dst = PY_FIXTURES_DIR / name
        if not src.is_file():
            sys.stderr.write(f"sync-parity: missing {src}\n")
            return 1

        src_bytes = src.read_bytes()
        if check:
            if not dst.is_file() or dst.read_bytes() != src_bytes:
                drift.append(name)
        else:
            dst.write_bytes(src_bytes)
            print(f"  ✓ vendored {name} ({len(src_bytes)} bytes)")

    if check:
        if drift:
            sys.stderr.write(
                "sync-parity: drift detected in: " + ", ".join(drift) + "\n"
                "Run `python scripts/sync-parity.py` (without --check) to "
                "refresh, then commit.\n"
            )
            return 1
        print("  ✓ no drift; fixtures in sync with the TS SDK")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero on drift instead of copying. CI gate mode.",
    )
    args = parser.parse_args()
    return copy_fixtures(check=args.check)


if __name__ == "__main__":
    sys.exit(main())
