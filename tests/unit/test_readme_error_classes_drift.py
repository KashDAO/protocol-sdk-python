"""README error-class catalog ↔ exported `Kash*Error` subclasses parity.

The Python SDK README documents every Kash*Error subclass in the
"Error hierarchy" section (~README lines 282–286):

    - **Error hierarchy**: `KashProtocolError` and 6 subclasses
      (`KashConfigError`, `KashChainError`, `KashBundlerError`,
      `KashSignerError`, `KashSimulationRevertedError`,
      `KashAbortedError`); cross-class identity via
      `KashProtocolError.is_(value)`

Customers branch on ``except Kash<X>Error`` to route recovery logic.
If a new error subclass ships without README update, customers don't
know to add the new branch; if the README documents a class that
doesn't exist, customer code ``from kashdao_protocol_sdk import
KashFooError`` fails to import.

Mirrors round BF (TS SDK README error-class table parity) for the
Python SDK. Both pin the same invariant: README's documented set ==
real exported set.
"""

from __future__ import annotations

import re
from pathlib import Path

import kashdao_protocol_sdk

README_PATH = Path(__file__).resolve().parents[2] / "README.md"
README = README_PATH.read_text(encoding="utf-8")


def _extract_readme_error_classes() -> set[str]:
    """Extract every ``Kash<X>Error`` token mentioned in a backtick
    span anywhere in the README. The narrowest signal we can
    reliably pull — markdown table cells and inline code both use
    backticks for class names.
    """
    return set(re.findall(r"`(Kash[A-Z]\w*Error)`", README))


def _extract_module_error_classes() -> set[str]:
    """Every public symbol on ``kashdao_protocol_sdk`` whose name
    matches ``Kash<X>Error``. Filtering on the namespace import
    rather than a hardcoded list means a new exported error
    auto-shows up in the parity check.
    """
    return {
        name
        for name in dir(kashdao_protocol_sdk)
        if re.fullmatch(r"Kash[A-Z]\w*Error", name)
    }


README_CLASSES = _extract_readme_error_classes()
MODULE_CLASSES = _extract_module_error_classes()


class TestReadmeErrorClassDrift:
    def test_sanity_floor_both_sides_non_empty(self) -> None:
        """Defence against a regex regression — if either side is
        empty, the per-class assertions degenerate to "no work
        done." Anchor on known load-bearing names.
        """
        assert len(MODULE_CLASSES) >= 5, (
            f"Module exports too few Kash*Error classes ({MODULE_CLASSES}). "
            "Likely a refactor of kashdao_protocol_sdk.__init__ — update extractor."
        )
        assert len(README_CLASSES) >= 5, (
            f"README mentions too few Kash*Error classes ({README_CLASSES}). "
            "Either the catalog section was removed (drop this test) or the "
            "format changed (update the extractor regex)."
        )
        assert "KashProtocolError" in MODULE_CLASSES, (
            "Module must export KashProtocolError (the base class)."
        )
        assert "KashChainError" in README_CLASSES, (
            "README must mention KashChainError — it's the most common subclass."
        )

    def test_every_exported_class_is_documented_in_readme(self) -> None:
        """Forward direction — the SDK adding a new class without
        a README update silently strands customers from branching
        on it. The failure message lists missing classes so the
        fix is obvious.
        """
        missing = sorted(MODULE_CLASSES - README_CLASSES)
        assert not missing, (
            f"Module exports {missing} but the README does not mention them. "
            f"Add each to the 'Error hierarchy' section in "
            f"packages/protocol-sdk-python/README.md. "
            f"Currently documented: {sorted(README_CLASSES)}."
        )

    def test_every_readme_class_corresponds_to_a_real_export(self) -> None:
        """Inverse direction — a typo or stale class name in the
        README would let customer code break at import time
        following the docs. Catch typos and stale references at
        PR time.
        """
        phantom = sorted(README_CLASSES - MODULE_CLASSES)
        assert not phantom, (
            f"README mentions {phantom} but they are not exported from "
            f"kashdao_protocol_sdk. Either restore the class export or "
            f"remove the README reference. "
            f"Currently exported: {sorted(MODULE_CLASSES)}."
        )

    def test_module_and_readme_sets_have_equal_sizes(self) -> None:
        """Summary invariant — passes only when both directions
        agree. The per-class assertions above produce the
        actionable failure messages; this one anchors the
        equality at a glance.
        """
        assert len(MODULE_CLASSES) == len(README_CLASSES), (
            f"Mismatch: {len(MODULE_CLASSES)} exported, "
            f"{len(README_CLASSES)} documented. "
            f"Exported: {sorted(MODULE_CLASSES)}. "
            f"Documented: {sorted(README_CLASSES)}."
        )
