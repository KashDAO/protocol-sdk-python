# Releasing `kashdao-protocol-sdk` (Python)

The Python protocol SDK ships from the private monorepo to the public
mirror at `KashDAO/protocol-sdk-python` and from there to PyPI. This
document is the runbook.

> **Audience**: maintainers inside the Kash monorepo. The mirror repo
> has its own `CONTRIBUTING.md` for external contributors.

## Pattern: public client, private server, manual publish

- **Canonical source**: this package, at `packages/protocol-sdk-python/`
  inside the private Kash monorepo. All development happens here.
- **Public mirror**: `github.com/KashDAO/protocol-sdk-python` is a
  one-way mirror, synced via `scripts/sync-to-public-mirror.ts`.
- **PyPI artifact**: published as `kashdao-protocol-sdk` either via
  the public mirror's GitHub Actions workflow (OIDC trusted publishing)
  or manually via `scripts/publish.sh`.

This is the same model `@kashdao/protocol-sdk` (TypeScript) uses, and
the two SDKs release **independently**. The Python SDK has no runtime
dependency on the TypeScript SDK; version numbers are not tied
together. Bump and publish each on its own cadence.

## Release workflow

### 1. Land changes inside the monorepo

Normal monorepo PR workflow against `packages/protocol-sdk-python/`.
CI runs:

- `python scripts/sync-abis.py --check` — confirms vendored ABIs match
  `packages/protocol-sdk/src/shared/contracts/generated/` (drift gate).
- `ruff check kashdao_protocol_sdk tests`
- `ruff format --check kashdao_protocol_sdk tests`
- `mypy kashdao_protocol_sdk`
- `pytest -m 'not integration and not e2e' -ra --strict-markers`

See `.github/workflows/protocol-sdk-python-ci.yml`.

### 2. Bump the version

Edit `kashdao_protocol_sdk/_version.py` (`__version__ = "X.Y.Z"`).
Update the CHANGELOG: rename `[Unreleased]` to the new version + date,
leave a fresh empty `[Unreleased]` heading on top.

Land on monorepo `main`.

> **Independence from `@kashdao/protocol-sdk` (TS)**: the TS and Python
> SDKs version independently. Don't bump the TS SDK just because Python
> is changing.

### 3. Sync to the mirror

```sh
pnpm tsx packages/protocol-sdk-python/scripts/sync-to-public-mirror.ts \
  --mirror-url=git@github.com:KashDAO/protocol-sdk-python.git
```

What the script does:

1. Clones the mirror into a temp dir.
2. Wipes its working tree (preserving `.git`).
3. Copies `kashdao_protocol_sdk/`, `tests/`, `examples/`, `scripts/`,
   `.github/`, README.md, CHANGELOG.md, CONTRIBUTING.md, SECURITY.md,
   RELEASING.md, LICENSE, HUMMINGBOT_INTEGRATION.md, pyproject.toml,
   .gitignore.
4. Drops Python build / cache artefacts (`__pycache__/`, `.pytest_cache/`,
   `.mypy_cache/`, `.ruff_cache/`, `.venv/`, `*.pyc`, `.egg-info/`).
5. Drops monorepo-only test files (`*.private.test.py`).
6. Strips monorepo-only scripts that have no meaning in the mirror tree:
   `scripts/sync-to-public-mirror.ts` (the mirror has no monorepo to
   read from) and `scripts/sync-abis.py` (vendors ABIs from a monorepo
   path, `packages/protocol-sdk/src/shared/contracts/generated/`, that
   only exists inside this monorepo).
7. Commits with message `release: v<version>` and tags `v<version>`.
8. Pushes `main` + the tag to the mirror.

> **ABI vendor boundary**: ABIs are committed in-tree at release time
> as JSON under `kashdao_protocol_sdk/contracts/abis/`. The mirror has
> no `sync-abis.py` and no need for one — the ABIs that ship were
> already vendored on the monorepo side via the drift-gated CI step
> (`python scripts/sync-abis.py --check`). External contributors to the
> mirror who need to refresh ABIs do so via a monorepo PR; the next
> mirror sync picks the change up.

Useful flags:

- `--dry-run` — commit + tag locally but don't push.
- `--skip-push` — same, more explicit.
- `--local-output=<path>` — skip clone entirely; write the prepared
  mirror tree to a local directory for inspection.

The `pyproject.toml` is copied verbatim — no workspace-dep rewriting
needed because the package has zero internal monorepo runtime deps.

#### Before the FIRST publish (squash initial commit)

The normal sync flow commits a fresh release on top of whatever
history the mirror already has. For the **first** publish you want
the mirror's history to start with a single, clean
`initial public release` commit — not a trail of internal cleanup.
One-shot recipe:

```sh
# Materialise a clean mirror tree locally (no clone, no commit)
pnpm tsx packages/protocol-sdk-python/scripts/sync-to-public-mirror.ts \
  --local-output=/tmp/protocol-sdk-python-init

cd /tmp/protocol-sdk-python-init
git init -b main
git add -A
git commit -m "chore: initial public release of kashdao-protocol-sdk"
git remote add origin git@github.com:KashDAO/protocol-sdk-python.git

# Mirror has no external consumers yet — force-push is safe here
# and only here. Every subsequent release goes through the normal
# sync-to-public-mirror.ts flow (clone + copy + commit + tag + push).
git push --force origin main
```

After this one-time push, every subsequent release uses the normal
`sync-to-public-mirror.ts` flow and the mirror history grows linearly
from the initial commit. The PyPI OIDC workflow on the mirror will
then fire on the first tag pushed by a subsequent release.

### 4. Publish to PyPI

**Preferred**: the public mirror's `publish-pypi.yml` workflow fires on
tag push (step 3 already pushed `v<version>`) and uses OIDC trusted
publishing — no API token in any repo. Watch the action; once green,
the package is on PyPI.

**Fallback (manual)**: from the monorepo, dry-run first:

```sh
bash packages/protocol-sdk-python/scripts/publish.sh --dry-run
```

The dry-run runs every gate (ruff, mypy, pytest, ABI drift, build,
SBOM via `cyclonedx-py environment`) plus the CHANGELOG-slice
extraction for the GitHub Release — but stops short of `twine
upload` and `gh release create`. Use it to verify the pipeline
end-to-end without touching PyPI. Non-interactive; tolerates
"version already published" + "twine not configured".

When the dry-run is clean, run the real publish:

```sh
bash packages/protocol-sdk-python/scripts/publish.sh
```

The script:

1. Verifies you're authenticated to PyPI (`twine` config / API token).
2. Confirms the version isn't already published.
3. Re-runs the pre-publish gate (ruff, mypy, pytest, ABI drift).
4. Builds wheel + sdist via `python -m build`.
5. Asks for an interactive `yes` confirmation.
6. Runs `twine upload dist/*`.

> ⚠️ Manual publish does NOT produce a PyPI sigstore attestation —
> that requires the OIDC workflow path. Prefer the workflow when
> possible.

### 5. Verify

```sh
pip index versions kashdao-protocol-sdk
```

Or fetch the metadata:

```sh
pip download --no-deps -d /tmp kashdao-protocol-sdk==<version>
```

### 6. Create the GitHub Release

If the OIDC workflow ran, it likely already drafted a release. If not:

1. Visit `https://github.com/KashDAO/protocol-sdk-python/releases/new`.
2. Choose the tag (`v0.1.0`).
3. Title: the version (`v0.1.0`).
4. Description: paste the relevant `CHANGELOG.md` section.
5. Publish release.

### 7. Smoke-test

```sh
python -m venv /tmp/kashsmoke && source /tmp/kashsmoke/bin/activate
pip install kashdao-protocol-sdk==<version>
python -c "import kashdao_protocol_sdk as k; print(k.__version__)"
# Expect: <version>
```

## Hotfix process

Same flow, patch version bump (`v0.1.1`). Since the TS SDK doesn't
depend on this package, no coordinated release is required — the
hotfix ships independently.

## Backporting external PRs

The mirror accepts PRs from external contributors. Once merged into
mirror `main`:

1. Cherry-pick the commit into `packages/protocol-sdk-python/` in the
   monorepo. Adjust paths if needed (the mirror has no
   `packages/protocol-sdk-python/` prefix).
2. Re-run the monorepo CI.
3. Land normally.

The next release sync will pick it up.

## Required access

| Resource                                      | Who needs access                                                         |
| --------------------------------------------- | ------------------------------------------------------------------------ |
| PyPI `kashdao-protocol-sdk` project (publish) | Whoever runs `publish.sh`, or the GitHub Actions OIDC role on the mirror |
| `KashDAO/protocol-sdk-python` repo (push)     | Whoever runs the sync script                                             |

## Rollback

If a published version is broken:

1. **Yank** it on PyPI: `pip` will refuse to install yanked versions
   for new resolves but existing pinned installs continue working.
   Use the PyPI web UI or `twine yank`.
2. Ship a patch release.

PyPI does not support deletion after upload (mirrors npm's no-unpublish
policy). Use yanking + patch.

## Future: when CI lands

Candidates for automation on the mirror:

- PyPI trusted publishing via OIDC (already preferred path; ensure
  workflow is present on every mirror).
- Multi-Python smoke test matrix (3.10 / 3.11 / 3.12).
- Sphinx → GitHub Pages for the API reference.

Deferred until the manual flow has been exercised a few times.
