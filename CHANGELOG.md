# Changelog

All notable changes to `kashdao-protocol-sdk` (Python) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the package is `0.x`, minor versions may include breaking changes —
breaking changes are explicitly called out in the entry.

## [Unreleased]

## [0.2.0b1] — 2026-10-02

### Added

- **Solana — `kashdao_protocol_sdk.solana`.** The Kash market program on
  Solana, mirroring `@kashdao/protocol-sdk/solana` (TypeScript) with
  `snake_case` names. Installed through a new optional extra,
  `pip install 'kashdao-protocol-sdk[solana]'`, which adds `solders` (0.28) only;
  an EVM-only install gains no dependency and `import kashdao_protocol_sdk`
  never loads the Solana half.
  - `create_solana_client(rpc_url=... | connection=..., cluster=...)`:
    `mainnet-beta` by default (program
    `Jr8Bd8efPfNYHW65vZrVzeLbYkzy1oo3QB3i8cYdDcy`, USDC
    `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`), `devnet`, or a
    `CustomSolanaDeployment`. Instructions always name the cluster's
    program id, never the IDL's embedded canonical id.
  - Reads: `markets.get`, `account.position` / `positions` /
    `usdc_balance`, `protocol.config` / `template` / `templates`.
  - Exact quotes: `markets.quote_buy` / `quote_sell` / `quote_redeem`,
    computed by an integer port of the program's curve that matches the
    protocol's Python reference-model corpus vector for vector (372
    vectors) and the TS SDK's mainnet quote vectors. Sells use each
    market's own `sell_fee_bps`.
  - Writes, as build → simulate → send or one call: `open_position`,
    `buy`, `sell`, `redeem`, `redeem_cancelled`, `close_position`. Every
    buy, sell and redeem requires a slippage bound — an explicit floor or
    `max_slippage_bps` (the Base clients' option name). Buys and sells of
    a paused program, a non-active, frozen or (buys only) halted market
    are refused before signing with the program's error name; collateral
    is verified (its PDA, its existence, its USDC mint).
  - Send safety: the signature is computed from the signed transaction
    before it is sent and carried by every later error. Only a JSON-RPC
    answer that proves the transaction was never forwarded is a retryable
    `TX_SEND_FAILED`: -32003, -32602, or a -32002 preflight whose
    `data.err` is `BlockhashNotFound`. Any other -32002 is a preflight
    revert decoded from `data.err` (even with empty logs), and
    `data.err == "AlreadyProcessed"` is treated as LANDED and confirmed.
    Every other outcome — timeout, reset, any HTTP error status including
    429, unreadable body, any other JSON-RPC code (-32603, -32005, vendor)
    — and an unreadable confirmation raises
    `KashTransactionOutcomeUnknownError` (`WAIT_RECEIPT_FAILED`, not
    retryable, `.signature`). A blockhash is only declared expired
    (`KashTransactionExpiredError`, `TX_EXPIRED`, retryable, `.signature`)
    when `getEpochInfo` reports the block height past
    `last_valid_block_height` and a status read from a node at or after
    that slot finds nothing. The confirm loop backs off through transient
    429/5xx/timeouts within a `confirm_timeout_seconds` budget.
  - Signers: `keypair_signer(solders Keypair)`, or any `SolanaSigner`.
  - `SolanaRpcConnection`: plain Solana JSON-RPC over `httpx`; any
    `SolanaConnection` implementation can be supplied instead.
  - PDA derivations (`kash_market_pdas`) and IDL-driven instruction
    builders (`buy_instruction`, …) are public.
- `KashValidationError` (code `VALIDATION_FAILED`), with `field`,
  `constraint` and `program_error`, and the Solana error codes
  `ACCOUNT_NOT_FOUND`, `ACCOUNT_OWNER_MISMATCH`, `ACCOUNT_READ_FAILED`,
  `SIMULATION_REQUEST_FAILED`, `TX_REVERTED` and `TX_EXPIRED`.

### Changed

- The version docstring now states the policy RELEASING.md already
  documents: this package versions independently of the TypeScript SDK.

Base support (EOA and smart-account modes, chains 8453 and 84532) is
unchanged.

## [0.1.0b2] — 2026-06-18

### Added

- **Base mainnet (8453) support.** The Kash protocol is now deployed on
  Base mainnet; `8453` is registered with its live contract addresses
  (factory, oracle, vault, tokens1155, param-registry) and added to
  `SUPPORTED_CHAIN_IDS`. `get_protocol_addresses(8453)` and
  `create_eoa_client` / `create_smart_account_client` with
  `chain_id=8453` now work. Addresses mirror the TypeScript
  `@kashdao/protocol-sdk` registry one-to-one (parity-validated).

### Changed

- `CHAIN_NOT_DEPLOYED` guard message reworded — it now only fires for a
  custom chain registered with a zero-address factory, not for Base
  mainnet.
- Re-vendored the contract ABIs from the canonical `@kashdao/protocol-sdk`
  source so they match the deployed mainnet/testnet contracts (the
  vendored copies had drifted since the initial port). Additive only —
  new functions/events; no existing selector changed.

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
