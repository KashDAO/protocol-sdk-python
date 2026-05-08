<!--
  Thanks for contributing! Please fill in the sections below.
  See CONTRIBUTING.md for the full standards we hold PRs to.
-->

## What

<!-- One-sentence description of the change. -->

## Why

<!-- The user-visible problem this solves. NOT "what the code does" — that's "What" above. -->

## Risk / blast radius

<!--
  Be honest. Examples:
  - "Internal refactor; no behaviour change"
  - "Adds a new keyword arg with a safe default; backwards compatible"
  - "Changes the shape of a public dataclass — breaking"
  - "Cross-language parity: also requires changes in @kashdao/protocol-sdk (TS)"
-->

## Cross-language parity

<!-- Tick whichever applies -->

- [ ] Pure Python change (docs, examples, internal helpers, Hummingbot adapter) — no TS counterpart needed
- [ ] Mirrors a change already on the TS side (link the TS PR / commit)
- [ ] Introduces new behaviour — TS counterpart will land in a separate PR (link the issue)
- [ ] N/A (release plumbing only)

## Checklist

- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] `mypy kashdao_protocol_sdk` passes (strict mode)
- [ ] Tests pass and cover both happy path AND at least one failure mode (`pytest tests/unit`)
- [ ] CHANGELOG.md updated under `[Unreleased]`
- [ ] If touching public surface: README + `__all__` in `kashdao_protocol_sdk/__init__.py` updated; docstring naming the TS counterpart added
- [ ] If touching `kashdao_protocol_sdk/shared/contracts/generated/`: ran `python scripts/sync-abis.py --check` (these files are vendored from the TS SDK)
- [ ] No private keys, RPC API keys, or bundler API keys committed anywhere
