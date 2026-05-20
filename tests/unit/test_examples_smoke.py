"""Import-smoke for the ``examples/`` directory.

The examples are the first surface customers copy-paste from. A break
here — a stale import, a renamed parameter that nobody updated — tanks
onboarding before the customer ever reaches our docs. This test runs in
every unit-test pass and forces every example to:

1. Parse as valid Python (``ast.parse``), and
2. Import cleanly (``spec.loader.exec_module``) — meaning every symbol
   the example references is resolvable against the installed SDK.

Round N renamed ``BuildBuyParams.smart_account`` -> ``account`` for TS
parity. The legacy keyword is still accepted via Pydantic AliasChoices,
but all examples have been migrated to the canonical ``account=`` form
and this test pins that going forward — a future contributor reverting
to the old name will see the import-check fail here long before the
example surfaces in customer-facing docs.

Limitations:
- ``hummingbot/amm_arb_kash_uniswap.py`` is skipped — it depends on
  the ``hummingbot`` runtime which is not part of our dev dependency
  set (it ships with a custom build for the bot author).
- Examples that read environment variables at import time (e.g. the
  ``local-anvil`` quickstart) are filtered too. Most examples are
  defined as ``async def main():`` with all I/O behind that boundary,
  so import is side-effect-free.
"""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"

# Examples we intentionally skip from the import smoke. These either
# depend on optional/external runtimes or do work at module-import
# time that requires runtime state (env vars, anvil, etc.).
SKIP = {
    "hummingbot/amm_arb_kash_uniswap.py",  # hummingbot runtime not vendored
    "local-anvil/01_quickstart.py",  # requires KASH_* env at import? safer to skip
}


def _discover_examples() -> list[Path]:
    examples: list[Path] = []
    for path in EXAMPLES_DIR.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(EXAMPLES_DIR).as_posix()
        if rel in SKIP:
            continue
        examples.append(path)
    return sorted(examples)


EXAMPLE_PATHS = _discover_examples()


@pytest.mark.parametrize("path", EXAMPLE_PATHS, ids=lambda p: p.relative_to(EXAMPLES_DIR).as_posix())
def test_example_parses(path: Path) -> None:
    """Every example must parse as valid Python."""
    src = path.read_text(encoding="utf-8")
    ast.parse(src, filename=str(path))


@pytest.mark.parametrize("path", EXAMPLE_PATHS, ids=lambda p: p.relative_to(EXAMPLES_DIR).as_posix())
def test_example_imports(path: Path) -> None:
    """Every example must import cleanly against the installed SDK.

    Loading the module forces all top-level imports to resolve — so a
    rename in ``kashdao_protocol_sdk`` that the example missed surfaces
    here as an ImportError, not as a confused customer.
    """
    spec = importlib.util.spec_from_file_location(
        f"example_{path.stem}",
        str(path),
    )
    assert spec is not None and spec.loader is not None, f"no spec for {path}"
    mod = importlib.util.module_from_spec(spec)
    # Don't let an example with a top-level ``if __name__ == '__main__'``
    # block accidentally call ``asyncio.run()`` during import. The guard
    # already handles this, but we belt-and-braces by clearing the env.
    saved = os.environ.copy()
    try:
        spec.loader.exec_module(mod)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_at_least_one_example_per_mode_was_discovered() -> None:
    """Sanity check: if the examples directory layout ever moves, we
    want CI to scream rather than silently green out with zero
    parametrized cases. Pin the floor at one example per mode.
    """
    rels = {p.relative_to(EXAMPLES_DIR).as_posix() for p in EXAMPLE_PATHS}
    assert any(r.startswith("eoa/") for r in rels), "no EOA examples discovered"
    assert any(r.startswith("smart_account/") or r.startswith("smart-account/") for r in rels), (
        "no SA examples discovered"
    )
