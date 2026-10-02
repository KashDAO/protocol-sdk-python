"""The Solana half is an optional extra: the EVM barrel must never import it.

Run in a fresh interpreter so this test process's own imports (which already
loaded solders through the other Solana tests) cannot hide a leak. The control
proves the probe can see a solders import when one happens.
"""

from __future__ import annotations

import subprocess
import sys

PROBE = "import sys, {module}; print('solders' in sys.modules)"


def _loads_solders(module: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(module=module)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "True"


def test_the_evm_barrel_does_not_import_solders() -> None:
    assert _loads_solders("kashdao_protocol_sdk") is False


def test_control_the_solana_subpackage_does() -> None:
    assert _loads_solders("kashdao_protocol_sdk.solana") is True
