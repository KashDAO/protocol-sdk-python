"""README Solana section ↔ the real ``kashdao_protocol_sdk.solana`` surface.

The EVM import-drift detector only parses ``from kashdao_protocol_sdk import
(...)`` blocks, so the Solana quickstart's ``from kashdao_protocol_sdk.solana
import (...)`` would drift unseen. This pins it, plus the mainnet identities
the README names and the client methods its table documents.
"""

from __future__ import annotations

import re
from pathlib import Path

import kashdao_protocol_sdk.solana as solana
from kashdao_protocol_sdk.solana import SOLANA_PROGRAM_IDENTITIES, SolanaTrades

README = (Path(__file__).resolve().parents[3] / "README.md").read_text(encoding="utf-8")


def _solana_imports() -> set[str]:
    names: set[str] = set()
    for block in re.findall(
        r"from\s+kashdao_protocol_sdk\.solana\s+import\s+\(([^)]*)\)", README, re.MULTILINE
    ):
        names.update(re.findall(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*,?\s*$", block, re.MULTILINE))
    return names


def test_the_parser_finds_the_quickstart_imports() -> None:
    names = _solana_imports()
    assert "create_solana_client" in names
    assert "keypair_signer" in names


def test_every_readme_import_is_exported() -> None:
    missing = _solana_imports() - set(solana.__all__)
    assert not missing, (
        f"README imports names kashdao_protocol_sdk.solana does not export: {missing}"
    )


def test_the_readme_names_the_registry_mainnet_identities() -> None:
    mainnet = SOLANA_PROGRAM_IDENTITIES["mainnet-beta"]
    assert mainnet["market_program_id"] in README
    assert mainnet["usdc_mint"] in README


def test_every_documented_trade_method_exists() -> None:
    documented = set(re.findall(r"`trades\.(\w+)", README))
    documented |= {
        m
        for row in re.findall(r"^\| `trades\.[^|]*\|", README, re.MULTILINE)
        for m in re.findall(r"`(?:trades\.)?(\w+)", row)
    }
    assert {"build_buy", "buy", "simulate", "send"} <= documented
    for name in documented:
        assert hasattr(SolanaTrades, name), f"README documents trades.{name}, which does not exist"
