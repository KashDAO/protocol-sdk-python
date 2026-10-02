# `kashdao-protocol-sdk` (Python)

Official Python SDK for the [Kash](https://kash.bot) prediction-market protocol — the canonical Hummingbot integration path and a first-class option for any Python trading bot, AI agent, or partner integration.

[![PyPI version](https://img.shields.io/pypi/v/kashdao-protocol-sdk?include_prereleases)](https://pypi.org/project/kashdao-protocol-sdk/)
[![Python versions](https://img.shields.io/pypi/pyversions/kashdao-protocol-sdk)](https://pypi.org/project/kashdao-protocol-sdk/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![CI](https://github.com/KashDAO/protocol-sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/KashDAO/protocol-sdk-python/actions/workflows/ci.yml)
[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy.readthedocs.io/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> 🚀 **Solana mainnet and Base.** New markets launch on **Solana
> mainnet-beta** — use [`kashdao_protocol_sdk.solana`](#solana--kashdao_protocol_sdksolana)
> (install the `[solana]` extra). Base mainnet (8453) and Base Sepolia
> (84532) stay fully supported for markets that live there until they
> resolve. See [Supported chains](#supported-chains).

> **Status: 0.2.0b1 (beta).** Three clients ship complete:
>
> - **Solana** — `create_solana_client`. The Kash market program on
>   mainnet-beta (default) or devnet; bring your own RPC + keypair.
> - **Base EOA mode** (vanilla EIP-1559) — `create_eoa_client`. The
>   canonical Hummingbot integration path; bring your own RPC + signer.
> - **Base Smart Account mode** (ERC-4337 v0.7) —
>   `create_smart_account_client`. SimpleAccount + bundler;
>   bring your own RPC + signer + bundler URL.
>
> Cross-language parity with `@kashdao/protocol-sdk` (TypeScript) is
> validated by `tests/parity/` — the same JSON fixtures drive both
> SDKs, so EIP-1559 serialization and UserOp v0.7 hashes are byte-equal
> by construction. A position opened via either SDK can be closed via
> the other.

## Supported chains

| Chain                 | Identifier     | Status                                                                                   |
| --------------------- | -------------- | ---------------------------------------------------------------------------------------- |
| Solana mainnet-beta   | `mainnet-beta` | ✅ Live — the default for `create_solana_client`                                         |
| Solana devnet         | `devnet`       | ✅ Live                                                                                  |
| Custom Solana cluster | any            | ✅ Via `cluster=CustomSolanaDeployment(program_id=..., usdc_mint=...)` (local validator) |
| Base mainnet          | 8453           | ✅ Live                                                                                  |
| Base Sepolia          | 84532          | ✅ Live                                                                                  |
| Custom EVM chain      | any            | ✅ Via `custom_chain=CustomChain(...)` (Anvil / forks / dev)                             |

## When to use this package

| You are…                                                                                        | Mode | Notes                                                             |
| ----------------------------------------------------------------------------------------------- | ---- | ----------------------------------------------------------------- |
| A **trader, market maker or agent on Solana** (where new markets launch)                        | Solana | `create_solana_client` — see [Solana](#solana--kashdao_protocol_sdksolana) |
| A **Hummingbot strategy author** with `eth_account` already in use                              | EOA  | Canonical path — see `HUMMINGBOT_INTEGRATION.md`                  |
| A **Python market maker** with existing tx-signing infra (HSM, Fireblocks, web3signer, AWS-KMS) | EOA  | Plug your signer in via the `EoaSignerAdapter` Protocol           |
| An **AI agent runner** in Python with a bundler relationship                                    | SA   | Adds gasless onboarding via paymaster + batched approve+trade     |
| A **research / data-science user** evaluating the protocol from a Jupyter notebook              | EOA  | One `create_eoa_client(...)` call away from quotes + reads        |
| A **TypeScript / web developer** building a retail integration                                  | —    | Use `@kashdao/sdk` (REST) or `@kashdao/protocol-sdk` (TS) instead |

## Install

```bash
pip install 'kashdao-protocol-sdk[solana]'   # Solana + Base
pip install kashdao-protocol-sdk             # Base only — gains no Solana dependency
```

Requires Python ≥ 3.10. End-user installs touch only the public PyPI
registry — no private repos, no auth tokens. The `[solana]` extra adds
[`solders`](https://pypi.org/project/solders/) only; the Solana RPC
transport is plain JSON-RPC over `httpx`, which the base install
already carries. Importing `kashdao_protocol_sdk` never loads the
Solana half.

## Solana — `kashdao_protocol_sdk.solana`

The Kash market program on Solana, non-custodially: bring an RPC URL (or
any `SolanaConnection`) and a signer. Mainnet-beta is the default
cluster — program `Jr8Bd8efPfNYHW65vZrVzeLbYkzy1oo3QB3i8cYdDcy`, USDC
`EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` — and devnet is
supported. Mirrors `@kashdao/protocol-sdk/solana` (TypeScript) with
`snake_case` names.

```python
import asyncio
import os

from solders.keypair import Keypair

from kashdao_protocol_sdk.solana import (
    KashSimulationRevertedError,
    KashValidationError,
    create_solana_client,
    keypair_signer,
)


async def main() -> None:
    signer = keypair_signer(Keypair.from_base58_string(os.environ["SOLANA_SECRET_KEY"]))
    async with create_solana_client(rpc_url=os.environ["SOLANA_RPC_URL"]) as kash:
        # Read a market (by numeric id or by its account address).
        market = await kash.markets.get(12)
        print(market.status, market.probabilities_wad)

        # Exact quote — the program's own integer curve.
        quote = await kash.markets.quote_buy(market=12, outcome=0, amount_usdc=5_000_000)
        print(quote.tokens_out_wad)

        # Build, sign, send and confirm in one call. A slippage bound is mandatory.
        try:
            result = await kash.trades.buy(
                market=12,
                outcome=0,
                amount_usdc=5_000_000,  # 5 USDC, atomic (6dp)
                max_slippage_bps=100,
                signer=signer,
            )
            print(result.signature, result.plan.min_tokens_out_wad)
        except KashValidationError as e:
            print(f"refused before signing: {e} ({e.program_error})")
        except KashSimulationRevertedError as e:
            print(f"preflight refused: {e.context}")


asyncio.run(main())
```

| Method                                                                                                                       | What it does                                                                                          |
| ---------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `markets.get(ref)`                                                                                                           | Decoded market + collateral, status (`active`/`frozen`/`resolved`/`cancelled`), prices, probabilities |
| `markets.quote_buy` / `quote_sell` / `quote_redeem`                                                                          | Exact quotes; refusals carry the program's error name on `KashValidationError.program_error`          |
| `account.position` / `positions` / `usdc_balance`                                                                            | Positions are `u128` WAD balances in a program account — **not** SPL tokens                           |
| `protocol.config` / `template` / `templates`                                                                                 | Program config (protocol fee, pause) and creation templates                                           |
| `trades.build_buy` / `build_sell` / `build_redeem` / `build_redeem_cancelled` / `build_open_position` / `build_close_position` | A plan: solders `Instruction`s plus the bounds it encodes                                             |
| `trades.simulate(plan, payer=...)` / `trades.send(plan, signer=...)`                                                         | Simulate unsigned, or sign → send → confirm                                                           |
| `trades.buy` / `sell` / `redeem` / `redeem_cancelled` / `open_position` / `close_position`                                   | Build + send in one call, the signer as trader                                                        |

- **Slippage is mandatory.** Every buy, sell and redeem takes exactly one
  of an explicit floor (`min_tokens_out_wad` / `min_amount_out_usdc`;
  `0` accepts anything) or `max_slippage_bps` below a fresh quote — the
  same option name the Base clients use. Neither, or both, is a
  `KashConfigError` (`INVALID_SLIPPAGE`).
- **Signers.** `keypair_signer(keypair)` for bots and scripts. Anything
  with a `pubkey` and an async `sign_message(message) -> Signature`
  satisfies `SolanaSigner` (a KMS, an HSM, a remote signer). Pass
  `fee_payer=` to `send` to sponsor fees from a second signer.
- **Amounts.** USDC is an atomic `int` (6 decimals). Outcome balances
  are a WAD `int` (18 decimals) — the program stores each position as a
  `u128`, so 18 decimals is the native scale on Solana too.
- **Sell fee.** Each market charges its own `sell_fee_bps` (on
  `SolanaMarket`), and quotes and slippage floors use it.
- **First buy.** The program requires a position account before `buy`;
  `build_buy` opens it in the same transaction when the receiver has none.
- **Refused before signing.** `build_buy`/`build_sell` raise
  `KashValidationError` (with the program's error name on
  `program_error`) when the program is paused (`Paused`), the market is
  not active (`InvalidState`) or frozen, or — for buys only — the market
  is halted (`HaltedEntries`; a holder can still sell). A market whose
  collateral account is missing or not the cluster's USDC is an error,
  never a zero balance. The instruction builders (`buy_instruction`, …)
  apply none of these checks.
- **Closing a position** refunds its rent to whoever funded it:
  `plan.rent_recipient`, which can differ from the owner who signs.
- **Errors** are the same hierarchy as the Base clients, and every error
  raised after signing carries `context["signature"]`, computed before the
  transaction is sent. Sending distinguishes:
  - a preflight revert (`KashSimulationRevertedError`) and a send the RPC
    refused BEFORE forwarding it (`TX_SEND_FAILED`, retryable: JSON-RPC
    -32003, -32602, or a -32002 preflight with `BlockhashNotFound`) —
    neither landed. A preflight reporting `AlreadyProcessed` means the
    transaction is already on chain; the SDK goes on to confirm it;
  - `KashTransactionExpiredError` (`TX_EXPIRED`, retryable): the blockhash
    expired **and** a status read from a node at or after the slot where
    the expiry was observed found no trace of the signature, so it did not
    land — rebuild with a fresh quote and resend;
  - `TX_REVERTED`: it landed and failed on chain;
  - `KashTransactionOutcomeUnknownError` (`WAIT_RECEIPT_FAILED`, **not**
    retryable, carries `.signature`): any other outcome — a timeout, a
    reset, any HTTP error status (429 included), an unreadable body, any
    other JSON-RPC code (-32603, -32005, vendor codes), or an unreadable
    confirmation. The transaction **may have landed**: look the signature
    up before doing anything else; rebuilding takes a fresh blockhash,
    which makes a new transaction, and sending both can double the trade.
- **Other deployments.** `create_solana_client(cluster="devnet", rpc_url=...)`,
  or `cluster=CustomSolanaDeployment(program_id=..., usdc_mint=...)` for a
  local validator. Instructions always name the cluster's program id,
  never the IDL's embedded canonical id.
- `kash_groups` (competitions) is not exposed: it is paused on mainnet.

Runnable examples: [`examples/solana/`](./examples/solana).

## Base (EVM)

Base markets keep trading until they resolve. The two Base modes below
are unchanged by the Solana release.

### Quickstart (EOA mode — canonical Hummingbot path)

```python
import asyncio
import os

from eth_account import Account
from kashdao_protocol_sdk import (
    BuildApproveParams,
    BuildBuyParams,
    MAX_UINT256,
    create_eoa_client,
    usdc,
    viem_account_eoa_signer,
)

MARKET = "0x..."  # the Market contract address you're trading


async def main() -> None:
    account = Account.from_key(os.environ["KASH_PRIVATE_KEY"])
    signer = viem_account_eoa_signer(account)

    client = create_eoa_client(
        chain_id=84532,                            # Base Sepolia
        rpc=os.environ["BASE_SEPOLIA_RPC"],
        signer=signer,
    )

    # First-time setup: approve the Market contract to spend USDC.
    await client.trades.send.approve(
        BuildApproveParams(
            account=account.address,
            spender=MARKET,
            amount=MAX_UINT256,
        ),
    )

    # Per trade tick:
    result = await client.trades.send.buy(
        MARKET,
        BuildBuyParams(
            smart_account=account.address,         # in EOA mode, this is the EOA itself
            outcome=0,
            amount_usdc=usdc(10),                  # 10 USDC
            max_slippage_bps=50,                   # 0.5%
        ),
    )
    print(result.transaction_hash, result.success, result.gas_used)


asyncio.run(main())
```

That's it. No bundler URL, no SimpleAccount provisioning, no UserOp.
Just an EOA, an RPC URL, and EIP-1559 transactions.

For Hummingbot users: see
[`HUMMINGBOT_INTEGRATION.md`](./HUMMINGBOT_INTEGRATION.md) for the full
strategy-author guide.

### Quickstart — smart-account mode

For consumers on AA stacks (Privy, Coinbase Smart Wallet, Pimlico,
Alchemy AA), or who want gasless onboarding via paymaster:

```python
import asyncio
import os

from eth_account import Account
from kashdao_protocol_sdk import (
    BuildBuyParams,
    BundlerOptions,
    create_smart_account_client,
    usdc,
    viem_account_signer,
)


async def main() -> None:
    account = Account.from_key(os.environ["KASH_PRIVATE_KEY"])

    async with create_smart_account_client(
        chain_id=84532,
        rpc=os.environ["BASE_SEPOLIA_RPC"],
        signer=viem_account_signer(account),
        bundler=BundlerOptions(
            provider="pimlico",
            url=os.environ["KASH_BUNDLER_URL"],
        ),
    ) as client:
        sa = await client.account.address()  # deterministic, no deploy needed
        result = await client.trades.send.buy(
            os.environ["KASH_MARKET"],
            BuildBuyParams(
                smart_account=sa,
                outcome=0,
                amount_usdc=usdc(10),
                max_slippage_bps=50,
            ),
        )
        print(result.user_op_hash, result.transaction_hash, result.success)


asyncio.run(main())
```

The full set of runnable examples (EOA, SA, Hummingbot, local Anvil)
lives in [`examples/`](./examples/).

## Power users — explicit trade lifecycle

`client.trades.send.{buy,sell,close_position,approve}` is the all-in-one
ergonomic path: it builds, prepares (gas + fees + nonce), simulates,
signs, submits, and (by default) waits for inclusion. For full control
the lifecycle is exposed in three layers — pick the highest one that
fits:

| Layer                                                | When you'd use it                                                                  |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `client.trades.send.<action>(...)`                   | Default. Hummingbot, AI agents, dashboards.                                        |
| `client.trades.prepare_<action>(...)` then submit    | You want explicit control of the signing step — e.g. log + audit before signing.   |
| `client.trades.build_<action>(...)` + `hash_of(...)` | You want to construct the unsigned tx, hash it, route to a remote signer yourself. |

`client.trades.hash_of(transaction)` and the SA equivalent
`client.trades.hash_of(user_op)` are the canonical hash that the
chain's signature-recovery validates against — call this AFTER
populating gas + fees and BEFORE signing to avoid stale-hash footguns.
Submit-time `STALE_SIGNED_TX` / `STALE_USEROP_HASH` checks are the
backstop.

## Real-time market events

```python
async with client.markets.watch(market_address) as sub:
    async for event in sub:
        print(event.event_type, event.market, event.data)
```

WebSocket if your RPC URL starts with `wss://`, otherwise polling. Both
yield the same event shape. Events are bounded queues; a slow consumer
won't OOM the SDK — the queue's high-water mark is documented in the
`watch` config.

## Error handling

Every error inherits from `KashProtocolError` and carries a stable
`code`, structured `context`, plus `is_retryable` / `is_operational`
flags. Mirrors the TypeScript `@kashdao/protocol-sdk` hierarchy
exactly — same class names, same string-valued `ErrorCode` constants.

```python
from kashdao_protocol_sdk import (
    KashChainError,
    KashConfigError,
    KashSignerError,
    KashSimulationRevertedError,
    KashAbortedError,
)

try:
    await client.trades.send.buy(market, params)
except KashSimulationRevertedError as e:
    # Pre-flight `eth_call` caught a revert before any signing happened.
    # `revert_hint` decodes the on-chain custom error name when known.
    print(f"would revert: {e.revert_hint or e}")
except KashSignerError as e:
    # Signer-side failure. ALWAYS non-retryable — re-prompting MFA on
    # retry is a footgun.
    print(f"signer failed: {e}")
except KashChainError as e:
    # Chain/RPC failure. May be retryable.
    if e.is_retryable:
        ...  # back off and retry
except KashConfigError as e:
    # Misconfig. Never retryable. Fix the code.
    raise
except KashAbortedError:
    # The caller's `signal` fired. Deliberate; don't auto-retry.
    raise
```

A full worked example lives in
[`examples/eoa/03_error_handling.py`](./examples/eoa/03_error_handling.py).

## What this SDK deliberately does NOT do

- **Kash-orchestrated trade routing.** No Kash backend on the trade
  path. If you want a REST surface that wraps the Kash public API, use
  [`@kashdao/sdk`](https://www.npmjs.com/package/@kashdao/sdk) — that
  path is also non-custodial (Kash never holds funds, never moves
  funds, never holds keys, never signs anything; the user's
  Privy-managed smart account is the signer).
- **Sponsor or bundle UserOps.** Bring your own bundler URL.
- **Hold or generate keys.** Bring your own signer (eth_account, KMS,
  Fireblocks, hardware wallet).
- **Background market scanners.** `markets.watch` only fires while a
  consumer is iterating; idle subscriptions are torn down via
  `aclose`.
- **Auto-retries on submit.** Submit is single-attempt by design.
  Reads (`prepare_*`, `simulate`) are retryable via `RetryOptions`.
- **Telemetry.** Zero phone-home. The SDK never reaches Kash
  infrastructure.

## Public surface

The SDK mirrors `@kashdao/protocol-sdk` (TypeScript) with `snake_case`
names. ~210 public exports across:

- **Factories**: `create_eoa_client`, `create_smart_account_client`;
  `create_solana_client` in `kashdao_protocol_sdk.solana` (the
  `[solana]` extra), with its own `markets` / `account` / `protocol` /
  `trades` surface, PDA derivations (`kash_market_pdas`) and IDL-driven
  instruction builders
- **Signers**: `LocalEoaSigner`, `JsonRpcEoaSigner` (EOA);
  `LocalSigner`, `JsonRpcSigner` (SA)
- **Bundlers** (SA): `create_generic_bundler_client` plus presets for
  `create_alchemy_bundler_client`, `create_pimlico_bundler_client`,
  `create_flashbots_bundler_client`
- **Trade lifecycle**:
  `client.trades.{build_*, prepare_*, simulate, hash_of, submit, send.*}`
- **Market reads**:
  `client.markets.{get, state, quote, watch}`
- **Account reads**:
  `client.account.{usdc_balance, position, gas_balance, usdc_allowance}`
- **Allowance helpers**: `encode_approve`, `MAX_UINT256`,
  `get_usdc_allowance`
- **Unit conversion**: `usdc`, `tokens`, `format_usdc`, `format_tokens`
- **Fee estimation**: `estimate_chain_fees` with
  `EstimateFeesOptions` (eth_feeHistory windowing, percentile, base
  multiplier, priority floor)
- **Custom-chain support**: `CustomChain`, `resolve_custom_chain` for
  Anvil / Hardhat / Tenderly forks
- **Error hierarchy**: `KashProtocolError` and 9 subclasses
  (`KashConfigError`, `KashChainError`, `KashBundlerError`,
  `KashSignerError`, `KashSimulationRevertedError`,
  `KashAbortedError`, `KashValidationError`, and the two Solana send
  outcomes, `KashTransactionOutcomeUnknownError` and
  `KashTransactionExpiredError`, both `KashChainError`s); cross-class
  identity via `KashProtocolError.is_(value)`
- **Lifecycle hooks**: `KashProtocolHooks` Protocol with 5
  fire-and-forget callbacks for telemetry / structured logging
- **Cancellation**: every async method takes a `signal` parameter;
  `throw_if_aborted` and `to_kash_aborted` helpers wrap
  `asyncio.CancelledError` into `KashAbortedError`

## Architectural boundary

This is a **non-custodial** library. Consumers bring:

- their own RPC URL,
- their own private key OR signer abstraction (`eth_account.Account`,
  web3signer over RPC, hardware wallet, Fireblocks, AWS-KMS — anything
  exposing the `EoaSignerAdapter` Protocol),
- (SA mode only) ERC-4337 bundler URL + smart-account address.

Kash never sees a private key, never relays a transaction or UserOp,
never sponsors gas, and is never on the trade path. The library's job
is to construct calldata + the unsigned tx, then forward signed
artifacts to the consumer's RPC.

## Why dual modes?

- **EOA mode** — vanilla EIP-1559 from a plain EOA. Best for market
  makers, Hummingbot strategies, AI agents, any consumer with their
  own EIP-1559 signing infrastructure (web3signer, Fireblocks,
  AWS-KMS, eth-account local key, hardware wallets).
- **Smart Account mode** — ERC-4337 v0.7 via SimpleAccount + bundler.
  Best for users on AA stacks (Privy embedded wallets, Coinbase Smart
  Wallet, Pimlico, Alchemy AA), or flows that benefit from gasless
  onboarding via paymaster, batched approve+trade, or session keys.

Both modes share the same markets / account-read surface and the same
discriminated `TradingClient` union.

## Source repository

This repository is the canonical public source. PyPI releases are
published from tags here via OIDC trusted publishing
(`.github/workflows/publish-pypi.yml`).

ABIs under `kashdao_protocol_sdk/shared/contracts/generated/` are
vendored at release time and shipped in the wheel; runtime loading
goes through `importlib.resources`, so consumers don't need any
post-install fetch step.

See [`RELEASING.md`](./RELEASING.md) for the full release runbook,
[`CONTRIBUTING.md`](./CONTRIBUTING.md) for the contributor workflow,
and [`SECURITY.md`](./SECURITY.md) for the vulnerability-disclosure
policy.

## License

MIT. The artifact published to PyPI is MIT-licensed; the development
repo is private but the published library carries no usage
restrictions.
