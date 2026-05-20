# Changelog

All notable changes to `kashdao-protocol-sdk` (Python) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the package is `0.x`, minor versions may include breaking changes —
breaking changes are explicitly called out in the entry.

## [Unreleased]

## [0.1.0b1] — 2026-05-20

Initial public beta release.

### Added

- **Two co-equal trading modes** — pythonic parity with the TS
  `@kashdao/protocol-sdk`:
  - `create_eoa_client(...)` — vanilla EIP-1559 from a plain EOA. The
    canonical Hummingbot path. No bundler, no smart account, no
    ERC-4337 overhead. Bring a private key and an RPC URL.
  - `create_smart_account_client(...)` — ERC-4337 v0.7 via SimpleAccount.
    For users on AA stacks (Privy, Coinbase Smart Wallet, Pimlico).
    Add a bundler URL to the EOA shape.
- **Signer adapters** — `viem_account_eoa_signer(...)` accepts an
  `eth_account.Account` directly; bring-your-own implementations of
  the `EoaSigner` protocol for HSM / Fireblocks / AWS-KMS integrations.
- **Markets**, **quotes**, **trades** — symmetric API across both
  client types:
  - `client.markets.get(addr)`, `client.markets.quote(addr, params)`
  - `client.trades.build_buy(...)`, `client.trades.build_sell(...)`,
    `client.trades.simulate(...)`, `client.trades.send(...)`
- **Chain support** — Base Sepolia (84532) for testnet integration
  today. Base mainnet (8453) is registered in `KNOWN_CHAIN_IDS` but
  not yet in `SUPPORTED_CHAIN_IDS`; `get_protocol_addresses(8453)`
  raises a clear `KashConfigError(code="CHAIN_NOT_DEPLOYED")` with
  the testnet workaround rather than silently routing reads at the
  zero address.
- **Typed errors** — `KashConfigError`, `KashRpcError`,
  `KashSignerError`, `KashContractRevertError`, `KashBundlerError`,
  `KashSimulationError`. Each carries a stable `code` (matches the
  TS SDK) and structured `context` for programmatic handling.
- **Cross-language parity** — public surface mirrors `@kashdao/protocol-sdk`
  (TypeScript) modulo `camelCase` ↔ `snake_case`. The
  `tests/parity/` suite asserts byte-equal typed-hash outputs and
  contract-call encodings across both SDKs to catch drift.
- **Examples** — `examples/eoa/` (quote-and-buy, watch-market) and
  `examples/smart_account/` (quote-and-buy, buy-with-simple-account).
- **Hummingbot integration guide** — `HUMMINGBOT_INTEGRATION.md`
  walks through a complete Hummingbot connector implementation.
- **Type hints throughout** — `py.typed` shipped; `mypy --strict`
  clean. Pydantic v2 models for all request/response types.
- **`web3.py`-based** — async support via `web3.AsyncHTTPProvider`.
  Works with Python 3.10–3.13.

### Beta status

The `b1` (beta-1) marker reflects two gating constraints:

1. **Mainnet contracts** are not yet deployed (only Base Sepolia is
   `SUPPORTED`). The non-final version signals to users that the
   contract surface MAY shift before the protocol locks mainnet
   addresses.
2. **External-integrator validation** — we want 30 days of usage by
   non-Kash teams before promoting to `0.1.0` stable.

The Python public API (function signatures, exported symbols, typed
error contracts) is locked at 0.1.0b1 — any breaking changes will
bump to 0.2.0 with a deprecation period.
