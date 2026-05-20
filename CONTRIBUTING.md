# Contributing to `kashdao-protocol-sdk` (Python)

Thanks for considering a contribution. The protocol SDK is the **non-custodial**
surface of Kash — market makers, AI-agent runners, Hummingbot strategies,
and sophisticated users sign their own transactions through it. Bugs here
can cost real money on mainnet, so we hold the bar high.

## How development works

This repo is the **public mirror** of the Python protocol SDK. The canonical
source lives inside Kash's monorepo, alongside the protocol contracts and
the TypeScript counterpart `@kashdao/protocol-sdk`. Pull requests land here
in the public mirror, get reviewed, and once accepted are re-imported into
the monorepo by the release pipeline.

That's the same model Stripe (`stripe/stripe-python`), OpenAI
(`openai/openai-python`), and AWS use for their language SDKs — public
client, private server.

What that means in practice:

- ✅ Open issues and PRs in this repo.
- ✅ Comment on PRs, request changes, propose alternatives.
- ❌ The full Kash backend isn't visible from this repo. The SDK reads
  from the consumer's RPC and (optionally) `https://api.kash.bot/v1/markets`
  like any other consumer.

## Quick start

```sh
git clone https://github.com/KashDAO/protocol-sdk-python.git
cd protocol-sdk-python
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -m 'not integration and not e2e'
```

Requires Python 3.10+.

## What's in scope

✅ Welcome:

- Bug fixes — especially in transaction / UserOp construction, calldata
  encoding, and signer-adapter contracts.
- New `EoaSignerAdapter` / `SmartAccountSignerAdapter` reference
  implementations (additional HSM / wallet / remote-signer integrations).
- New bundler presets for any compliant ERC-4337 v0.7 bundler.
- Better docstrings and README clarifications.
- Test coverage for edge cases (slippage rounding, deadline boundaries,
  zero-balance closes, malformed bundler responses).

🟡 Discuss first (open an issue):

- New top-level methods on resource clients (`markets.*`, `account.*`,
  `trades.*`).
- New configuration options on `create_smart_account_client` /
  `create_eoa_client`.
- Changes to public types — even additive ones (every public name is a
  SemVer commitment).
- Bumping the minimum Python version, web3.py version, or EntryPoint version.
- New supported chains (requires updating both `addresses.py` and the
  drift test).

❌ Out of scope:

- **Internal `@kashdao/*` runtime imports.** The Python SDK ships with
  zero internal dependencies. ABIs are vendored as JSON; the drift gate
  catches divergence from the TypeScript canonical source.
- **Kash-orchestrated / API-key trade flows.** Those live in the `kashdao-sdk`
  package. This package is non-custodial only.
- **Telemetry.** No phone-home, no usage analytics, no error reporting
  back to Kash. Ever.
- **Retries on `submit()`.** Surfacing failure to the consumer is
  preferable to retrying a transaction.
- **Paymaster sponsorship.** The smart account pays its own gas. We don't
  ship paymaster-data builders.

## Standards

- **`web3.py`, `eth-account`, `eth-abi`, `eth-utils`, `httpx`,
  `pydantic`, `websockets` are the only runtime dependencies.** No
  logging libraries, no observability stack. If you need a few lines of
  helpers, write them inline.
- **ABIs are vendored, not imported.** All ABI definitions live under
  `kashdao_protocol_sdk/shared/contracts/generated/*.json` and are
  regenerated from `@kashdao/protocol-sdk`'s vendored TS literals via
  `python scripts/sync-abis.py`. CI fails on any mismatch.
- **Public surface is intentional.** The `__init__.py` barrel is the
  contract; symbols not in `__all__` are internal.
- **Docstrings on every public function and class.** The IDE-hover
  experience is part of the SDK.
- **Tests cover both happy path AND error paths.** Mock the boundary
  (`pytest-httpx`, an in-process JSON-RPC fake, etc.); never mock the
  SDK's own internals. The suite is split into three layers, each
  gated by a pytest marker: **unit** (default, runs offline,
  network-mocked), **integration** (gated by `@pytest.mark.integration`,
  requires `KASH_BASE_SEPOLIA_RPC` + `KASH_TEST_OWNER_KEY`, hits real
  Base Sepolia, skips cleanly without env vars), and **parity**
  (gated by `@pytest.mark.parity`, byte-equality against TS-generated
  fixtures, skips cleanly until the fixture file is vendored).
- **HTTPS-only.** RPC URLs must start with `https://`, `wss://`, or
  `ws://`. `http://localhost` (and `http://127.0.0.1`) is the only
  exception (for dev work).
- **Errors carry `code`, `context`, `is_retryable`, `is_operational`.**
  Every raised error extends `KashProtocolError`. Use codes from the
  `ErrorCode` namespace.

## Parity with the TypeScript SDK

The Python SDK is a **direct port** of `@kashdao/protocol-sdk`. Every
Python module's docstring names the TS file it mirrors. Trade-path
correctness (UserOp / EIP-1559 calldata, hashes, EIP-712 typed data) is
gated by parity tests against fixtures the TS side generates.

If you change behavior on one side, change it on the other. The
maintainers will land both halves of a parity-affecting change together.

## Workflow

1. **Fork** this repo and create a feature branch:
   `git checkout -b feat/add-xyz`.
2. **Make the change.** Run `ruff check`, `ruff format`, `mypy
kashdao_protocol_sdk`, `pytest -m 'not integration and not e2e'`
   after each substantial edit.
3. **Re-vendor ABIs** if you touched anything that depends on the
   protocol contracts: `python scripts/sync-abis.py` and commit the diff.
4. **Add tests.** Aim to cover happy path + at least one failure mode.
   Add a parity test if you touched calldata / hash / UserOp construction.
5. **Document.** Update the README, the docstrings, and `CHANGELOG.md`
   under the `[Unreleased]` heading.
6. **Open a PR** with a short summary explaining the _why_, not just
   the _what_. Link any related issue.

## Code of conduct

Be kind. Disagree on ideas, not people. The maintainers reserve the
right to close issues / PRs that violate basic respect.

## Security

Found a security issue? Don't open a public issue. Email
`security@kash.bot`. See [`SECURITY.md`](./SECURITY.md) for details.
