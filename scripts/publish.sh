#!/usr/bin/env bash
# Manual publish flow for kashdao-protocol-sdk (Python).
#
# Same shape as the sibling publish flows for `@kashdao/protocol-sdk`
# (TypeScript) and `@kashdao/sdk` (TypeScript) — the three release
# scripts are kept parallel so each package's release flow is local
# and self-contained.
#
# This package and the TypeScript SDKs are INDEPENDENT — none depend on
# the others at runtime. Publish ordering does not matter.
#
# Preferred path: the public mirror's `publish-pypi.yml` workflow fires
# on tag push (after `sync-to-public-mirror.ts` runs) and publishes via
# OIDC trusted publishing — no API token in any repo. Use this script
# only when the OIDC path is unavailable (mirror workflow failure,
# emergency hotfix without a tag, etc.).
#
# Usage (from the package root, after a successful sync):
#
#   bash scripts/publish.sh
#
# Requires: python ≥ 3.10, build, twine, ruff, mypy, pytest in the env.

set -euo pipefail

# ---- Args -----------------------------------------------------------------
#
# `--dry-run` runs the full pre-publish gate (lint, type, test, abi-drift,
# build, SBOM) AND the CHANGELOG-slice extraction for the GitHub Release,
# but stops short of:
#
#   * `python -m twine upload` (no actual upload)
#   * `gh release create` (no GitHub Release drafted)
#
# Use it before the first publish to verify every step works end-to-
# end without committing to a real PyPI version. Also useful as a
# pre-flight before a dependency-graph sensitive release.
#
# Dry-run also relaxes the "version not yet published" gate — instead
# of failing, it warns and continues so you can verify the rest of the
# pipeline locally without bumping the version constant.

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: bash $0 [--dry-run]" >&2
      exit 2
      ;;
  esac
done

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PKG_DIR"

# Resolve a Python interpreter. macOS + most Linux distros ship `python3`
# but not `python`; the venv-activated path provides both. Prefer the
# pinned venv if present (`packages/protocol-sdk-python/.venv/bin/python`),
# then `python3`, then `python`.
#
# When the venv exists we also activate it so bare tool invocations
# (`pip`, `ruff`, `mypy`, `pytest`, `twine`, `cyclonedx-py`) resolve to
# the venv's bin/. Without activation, a system shell with no `python`
# on PATH also has no `pip`, which breaks the gate.
if [ -x "$PKG_DIR/.venv/bin/python" ]; then
  PY="$PKG_DIR/.venv/bin/python"
  # shellcheck disable=SC1091
  source "$PKG_DIR/.venv/bin/activate"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
elif command -v python >/dev/null 2>&1; then
  PY="python"
else
  echo "  ✗ No Python interpreter found. Install Python 3.10+ first." >&2
  exit 1
fi

VERSION="$("$PY" -c "import importlib.util, pathlib; spec = importlib.util.spec_from_file_location('v', 'kashdao_protocol_sdk/_version.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(m.__version__)")"
TAG="v$VERSION"

if [ "$DRY_RUN" = "1" ]; then
  echo "▶ kashdao-protocol-sdk publish — version $VERSION  [DRY-RUN]"
else
  echo "▶ kashdao-protocol-sdk publish — version $VERSION"
fi
echo

# ---- Pre-flight checks ----------------------------------------------------

echo "▶ twine + build availability check (interpreter: $PY)"
"$PY" -m build --version >/dev/null 2>&1 || {
  echo "  ✗ $PY -m build not available. Run: $PY -m pip install build twine"
  exit 1
}
"$PY" -m twine --version >/dev/null 2>&1 || {
  echo "  ✗ $PY -m twine not available. Run: $PY -m pip install build twine"
  exit 1
}
echo "  ✓ build + twine available"

echo "▶ Confirming version is not already published"
if pip index versions kashdao-protocol-sdk 2>/dev/null \
  | grep -E "^kashdao-protocol-sdk \([^)]+\)" \
  | grep -E -o "[0-9][^,]*" \
  | tr ',' '\n' \
  | grep -E "^[[:space:]]*${VERSION//./\\.}[[:space:]]*$" >/dev/null; then
  if [ "$DRY_RUN" = "1" ]; then
    echo "  ⚠ kashdao-protocol-sdk==$VERSION is already published — continuing in dry-run."
  else
    echo "  ✗ kashdao-protocol-sdk==$VERSION is already published."
    echo "    Bump the version in kashdao_protocol_sdk/_version.py (and CHANGELOG)."
    exit 1
  fi
else
  echo "  ✓ kashdao-protocol-sdk==$VERSION is unclaimed"
fi

# ---- Gate: lint / type / test / abi-drift / build -------------------------

echo
echo "▶ Running pre-publish gate"

# Reinstall to ensure dev deps are pinned + present.
pip install --quiet -e '.[dev]'

echo "  · ruff check"
ruff check kashdao_protocol_sdk tests

echo "  · ruff format check"
ruff format --check kashdao_protocol_sdk tests

echo "  · mypy"
mypy kashdao_protocol_sdk

echo "  · pytest (no integration / e2e / parity)"
pytest -m 'not integration and not e2e and not parity' -ra --strict-markers

echo "  · ABI drift check"
"$PY" scripts/sync-abis.py --check

echo "  · build wheel + sdist"
rm -rf dist build
"$PY" -m build

# ---- SBOM (CycloneDX) -----------------------------------------------------
#
# Generates `sbom.cyclonedx.json` from the project's installed dependency
# tree. Mirrors the TS SDKs' `pnpm sbom` step. Attached to the GitHub
# Release as a release asset for enterprise consumers running supply-
# chain audits.
#
# `cyclonedx-py environment` reads from the active Python environment
# (the dev install we already have to run pytest/ruff/mypy above), so
# no extra install step. The output uses CycloneDX 1.6 schema by
# default — the same schema the TS SDKs emit via `npm sbom`.

echo
echo "▶ SBOM (CycloneDX)"
if command -v cyclonedx-py >/dev/null 2>&1; then
  cyclonedx-py environment --output-file sbom.cyclonedx.json --output-format json
  echo "  ✓ SBOM written: $(realpath sbom.cyclonedx.json 2>/dev/null || pwd)/sbom.cyclonedx.json"
else
  echo "  ⚠ cyclonedx-py not on PATH - install dev extras (pip install -e .[dev]) to generate the SBOM."
fi

# ---- Final confirmation ---------------------------------------------------

echo
if [ "$DRY_RUN" = "1" ]; then
  echo "▶ About to publish (DRY-RUN — would prompt for confirmation):"
else
  echo "▶ About to publish:"
fi
echo "  Package:  kashdao-protocol-sdk"
echo "  Version:  $VERSION"
echo "  Registry: https://upload.pypi.org/legacy/"
echo "  Artifacts:"
ls -la dist/ | grep -E '\.(whl|tar\.gz)$' | awk '{print "    "$NF}'
echo
if [ "$DRY_RUN" != "1" ]; then
  read -r -p "Proceed? (yes/N) " confirm
  if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 1
  fi
fi

# ---- Publish --------------------------------------------------------------

echo
if [ "$DRY_RUN" = "1" ]; then
  echo "▶ Publishing  [DRY-RUN — would run: $PY -m twine upload dist/*]"
  echo "  Skipping the actual upload. Pre-flight gate, build, SBOM, and CHANGELOG"
  echo "  extraction all ran above; the artifacts in \`dist/\` are what would ship."
else
  echo "▶ Publishing"
  "$PY" -m twine upload dist/*

  echo
  echo "✓ Published kashdao-protocol-sdk==$VERSION"
fi

# ---- GitHub Release -------------------------------------------------------
#
# Best-effort: if `gh` is on PATH and authenticated, draft a Release on
# the public mirror with the CHANGELOG slice for this version inlined,
# attaching the wheel/sdist artifacts and the CycloneDX SBOM. Mirrors
# the TS SDKs' publish gates. Non-fatal — twine upload above is the
# source of truth.

if command -v gh >/dev/null 2>&1; then
  echo
  if [ "$DRY_RUN" = "1" ]; then
    echo "▶ Would draft GitHub Release $TAG on KashDAO/protocol-sdk-python  [DRY-RUN]"
  else
    echo "▶ Drafting GitHub Release $TAG on KashDAO/protocol-sdk-python"
  fi
  RELEASE_NOTES=$(awk -v ver="$VERSION" '
    BEGIN { capture = 0 }
    /^## \[/ {
      if (capture) exit
      if ($0 ~ "\\[" ver "\\]") { capture = 1; next }
    }
    capture { print }
  ' "$(pwd)/CHANGELOG.md")

  if [ -z "$RELEASE_NOTES" ]; then
    echo "  ⚠ Could not extract CHANGELOG slice for $VERSION."
    echo "    Create the release manually:"
    echo "    https://github.com/KashDAO/protocol-sdk-python/releases/new?tag=$TAG"
  else
    gh_args=("$TAG" --repo KashDAO/protocol-sdk-python --title "$TAG" --notes-file - --draft)
    # Attach wheel + sdist + SBOM if present.
    for asset in dist/*.whl dist/*.tar.gz sbom.cyclonedx.json; do
      if [ -f "$asset" ]; then
        gh_args+=("$asset")
      fi
    done
    if [ "$DRY_RUN" = "1" ]; then
      notes_lines=$(printf '%s\n' "$RELEASE_NOTES" | wc -l | tr -d ' ')
      echo "  Notes preview - first 20 lines of ${notes_lines} total:"
      printf '%s\n' "$RELEASE_NOTES" | sed 's/^/    /' | head -20
      echo "  ..."
      echo "  Would invoke: gh release create ${gh_args[*]}"
    elif ! gh release view "$TAG" --repo KashDAO/protocol-sdk-python >/dev/null 2>&1; then
      printf '%s\n' "$RELEASE_NOTES" | gh release create "${gh_args[@]}" \
        || echo "  ⚠ gh release create failed; create manually at https://github.com/KashDAO/protocol-sdk-python/releases/new"
      echo "  ✓ Drafted release; review at https://github.com/KashDAO/protocol-sdk-python/releases/tag/$TAG"
    else
      echo "  ✓ Release $TAG already exists; skipping"
    fi
  fi
else
  echo
  echo "  ℹ \`gh\` CLI not on PATH — skipping GitHub Release draft."
  echo "    Install gh and authenticate, or create manually:"
  echo "    https://github.com/KashDAO/protocol-sdk-python/releases/new?tag=$TAG"
fi

echo
echo "Smoke-test from a scratch venv:"
echo "  python -m venv /tmp/kashsmoke && source /tmp/kashsmoke/bin/activate"
echo "  pip install kashdao-protocol-sdk==$VERSION"
echo "  python -c \"import kashdao_protocol_sdk as k; print(k.__version__)\""
