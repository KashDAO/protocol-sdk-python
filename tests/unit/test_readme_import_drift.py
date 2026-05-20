"""README import-block ↔ ``__init__.__all__`` drift detector.

``packages/protocol-sdk-python/README.md`` contains 3 verbatim
``from kashdao_protocol_sdk import (...)`` blocks — the EOA quickstart,
the smart-account quickstart, and the error-handling example. Every
symbol listed in those blocks is a name a Python developer will paste
into their own code on day one.

If ``kashdao_protocol_sdk.__init__.__all__`` ever drops or renames one
of those symbols (refactor, rename, accidental deletion), the README
example crashes with::

    ImportError: cannot import name 'BuildBuyParams' from kashdao_protocol_sdk

at the very first attempt — silently breaks every customer who copied
the README's quickstart.

Same drift class as the TS-side rounds AH (CLI bin-name), AK (SDK
README ↔ KashClient surface), AL (admin-cli bin-name). The customer-
facing README is a contract; the source-of-truth is ``__all__`` plus
the actual module attributes.

This detector pairs with ``test_readme_chain_id_drift.py`` (round AF):
that test pins the chain-id literals in the README; this one pins the
import surface those literals are framed by.
"""

from __future__ import annotations

import re
from pathlib import Path

import kashdao_protocol_sdk

README_PATH = Path(__file__).resolve().parents[2] / "README.md"
README = README_PATH.read_text(encoding="utf-8")


def _extract_imported_names(text: str) -> set[str]:
    """Return every symbol imported via ``from kashdao_protocol_sdk import (...)``.

    Multi-line parenthesised imports are the only form the README uses
    (each import block already spans 5–10 lines). Each line inside the
    parens that starts with a bare identifier is a symbol.
    """
    names: set[str] = set()
    block_re = re.compile(
        r"from\s+kashdao_protocol_sdk\s+import\s+\(([^)]*)\)",
        re.MULTILINE,
    )
    identifier_re = re.compile(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*,?\s*$", re.MULTILINE)
    for block in block_re.findall(text):
        for match in identifier_re.finditer(block):
            names.add(match.group(1))
    return names


class TestReadmeImportDrift:
    def test_parser_finds_a_sane_floor_of_symbols(self) -> None:
        """Sanity floor: if the regex breaks (import-block syntax in the
        README changes), the test must not pass empty.

        Anchor to a minimum count plus a few specific symbols every
        version of the README is guaranteed to mention.
        """
        names = _extract_imported_names(README)
        assert len(names) >= 8, (
            f"Only {len(names)} symbols extracted from README — the import-block "
            f"parser may be broken. Names found: {sorted(names)}"
        )
        assert "BuildBuyParams" in names, "README must import BuildBuyParams"
        assert "create_eoa_client" in names, "README must import create_eoa_client"

    def test_every_readme_imported_symbol_is_in_dunder_all(self) -> None:
        """Each README import symbol MUST appear in ``__all__``.

        ``__all__`` is the public-API allowlist. A regression that dropped
        a symbol from ``__all__`` (or moved it to a private submodule
        without updating ``__init__``) would break the README's example
        at import time. We pin the allowlist itself, not just the
        attribute presence — moving a symbol to a non-exported state is
        equally a breaking change for customers.
        """
        dunder_all = set(kashdao_protocol_sdk.__all__)
        readme_names = _extract_imported_names(README)
        missing = sorted(readme_names - dunder_all)
        assert not missing, (
            f"README imports symbols not in kashdao_protocol_sdk.__all__: {missing}. "
            "Either re-export them from kashdao_protocol_sdk/__init__.py's __all__, "
            "or update the README to remove the import."
        )

    def test_every_readme_imported_symbol_actually_resolves(self) -> None:
        """Defence-in-depth: ``__all__`` is just a list of strings. The
        attribute must also exist on the module.

        A regression that left a symbol in ``__all__`` but removed the
        actual binding (e.g. unused-import lint removed the
        re-export, but ``__all__`` was hand-written) would still ship
        the bug. ``getattr`` walks the same path Python's import
        machinery uses for ``from ... import X``, so this is the most
        faithful drift check available.
        """
        readme_names = _extract_imported_names(README)
        for name in readme_names:
            assert hasattr(kashdao_protocol_sdk, name), (
                f"README imports `{name}` but the module has no such attribute. "
                "Either restore the export or update the README."
            )

    def test_critical_quickstart_symbols_remain_in_all(self) -> None:
        """Anchor the load-bearing symbols — the ones that appear in BOTH
        quickstart examples (EOA + smart-account) and are absolutely
        required for a customer to make their first trade.

        This protects against the case where the regex parser silently
        starts returning fewer names (e.g. the README changes its import
        style) AND the dropped names happen to be these. Belt-and-braces:
        the earlier tests check what the README currently says; this one
        checks the SDK still says the right thing regardless of README
        state.
        """
        critical = [
            "BuildBuyParams",  # The buy-trade params dataclass — every example uses it
            "create_eoa_client",  # The EOA quickstart entrypoint
            "create_smart_account_client",  # The smart-account quickstart entrypoint
            "viem_account_signer",  # The smart-account signer adapter
            "viem_account_eoa_signer",  # The EOA signer adapter
            "BundlerOptions",  # Required for smart-account quickstart
            "KashChainError",  # The error-handling example imports it
            "KashSimulationRevertedError",  # The error-handling example imports it
        ]
        dunder_all = set(kashdao_protocol_sdk.__all__)
        for name in critical:
            assert name in dunder_all, (
                f"Load-bearing public symbol `{name}` is missing from "
                f"kashdao_protocol_sdk.__all__. The README's quickstart depends "
                "on it; removing it is a breaking change."
            )
            assert hasattr(kashdao_protocol_sdk, name), (
                f"Load-bearing public symbol `{name}` doesn't resolve on the "
                "module — the __all__ entry is dangling."
            )
