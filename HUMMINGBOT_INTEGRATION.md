# Hummingbot integration guide

`kashdao-protocol-sdk` (Python) is the canonical path for Hummingbot
strategies that want to trade on Kash. The SDK runs entirely inside
the Hummingbot strategy process — there is no separate connector
class to register, no Gateway service to provision, and no bundler
URL to supply (in EOA mode, which is the recommended Hummingbot
configuration).

## Why no Hummingbot connector class?

Hummingbot's `ExchangePyBase` connector framework is designed for
CLOB / centralized exchanges with resting orders, partial fills, and
cancel-replace semantics. None of these apply to an AMM, and there is
no precedent in mainline Hummingbot for an `ExchangePyBase` connector
that submits transactions on-chain itself.

The modern Hummingbot DEX path is the Gateway service — a TypeScript
REST mediator. Adding Kash to Gateway means writing a TS connector,
not a Python one; that work is out of scope here.

So: Hummingbot strategies `import kashdao_protocol_sdk` directly and
call it from their tick loop.

## Solana markets

The reference scripts below trade **Base** (EVM) markets. New Kash
markets launch on Solana mainnet-beta, which this SDK serves through
`kashdao_protocol_sdk.solana` (`pip install 'kashdao-protocol-sdk[solana]'`).
No Solana variant of the Hummingbot scripts ships yet: the Hummingbot
runtime is not part of this package's test environment, so a script
written for it could not be exercised here, and an untested trading
script is worse than none. The integration shape is the same one the
Base scripts use — create the client once, call it from the tick loop —
with `create_solana_client(rpc_url=...)`, `keypair_signer(...)` and
`await kash.trades.buy(market=..., outcome=..., amount_usdc=...,
max_slippage_bps=..., signer=...)`; see `examples/solana/` for the
same calls in standalone scripts.

## Start here: the runnable reference

`examples/hummingbot/kash_accumulator.py` is a **complete, runnable**
Hummingbot script — a single-venue DCA/accumulator that buys a fixed
USDC notional of one market outcome on an interval until a budget is
filled, on **Base mainnet (8453) by default**, with an optional price
ceiling. It exercises the full trade path (quote → approve → buy →
confirm → position) and is the template to fork. Its SDK read path is
validated against live mainnet markets.

Three files ship together:

| File                                            | Purpose                                                                                  |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `examples/hummingbot/kash_accumulator.py`       | The Hummingbot script (config-driven, mainnet-default, runnable as shipped).             |
| `examples/hummingbot/conf_kash_accumulator.yml` | Sample config template (edit + drop into Hummingbot's `conf/scripts/`).                  |
| `examples/hummingbot/find_kash_markets.py`      | Lists live market addresses to put in `market_address` (needs a read-only Kash API key). |

```bash
# 1. discover a market address + outcome index
KASH_API_KEY=kash_live_... python examples/hummingbot/find_kash_markets.py
# 2. set the trading EOA key (NEVER stored in the YAML)
export KASH_TRADER_PK=0x...
# 3. inside Hummingbot, with conf_kash_accumulator.yml edited:
start --script kash_accumulator.py --conf conf_kash_accumulator.yml
```

> **Market discovery still routes through the Kash API.** Trading is
> on-chain and non-custodial, but finding _which_ market to trade needs
> a (free, read-only) `markets:read` API key — or grab a market address
> from the Kash app UI and skip the helper. The trading EOA key is
> separate and never touches the API.

## Strategy compatibility

| Hummingbot strategy                              | Status           | Notes                                                                                                                     |
| ------------------------------------------------ | ---------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Single-venue DCA / accumulator                   | ✅ Runnable      | `examples/hummingbot/kash_accumulator.py` — complete, mainnet-default, ships as a working strategy                        |
| `amm_arb`-style cross-venue arbitrage            | ⚠️ Skeleton      | `examples/hummingbot/amm_arb_kash_uniswap.py` — SDK shape is real, but the counter-venue quote + profitability are stubs  |
| `pure_market_making` (PMM)                       | ❌ Not supported | AMMs do not honor resting limit orders; PMM's order-book assumptions cannot apply                                         |
| Strategies relying on `cancel_order` for hedging | ⚠️ Limited       | Once a transaction is on-chain, the trade is atomic. Pre-confirmation cancellation is best-effort (RPC nonce-replacement) |

## Install

```bash
pip install kashdao-protocol-sdk
```

Requires Python ≥ 3.10, which Hummingbot already requires.

## Quick start (EOA mode)

A typical Hummingbot script for trading on Kash:

```python
"""Kash AMM arbitrage strategy (skeleton).

Hummingbot strategy that compares prices on Kash vs another DEX (e.g.,
Uniswap on the same chain) and executes corrective trades on Kash via
the Python protocol SDK.
"""

import asyncio
import os

from eth_account import Account
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from kashdao_protocol_sdk import (
    BuildApproveParams,
    BuildBuyParams,
    BuildSellParams,
    MAX_UINT256,
    QuoteParams,
    create_eoa_client,
    usdc,
    viem_account_eoa_signer,
)


KASH_MARKET = os.environ["KASH_MARKET_ADDRESS"]
BASE_RPC = os.environ["BASE_RPC"]
KASH_PK = os.environ["KASH_TRADER_PK"]


class KashAmmArbStrategy(StrategyV2Base):
    """Hummingbot script that uses kashdao-protocol-sdk in its tick."""

    markets = {}  # No Hummingbot connectors — we drive Kash via the SDK directly.

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        account = Account.from_key(KASH_PK)
        self._signer = viem_account_eoa_signer(account)
        self._owner = account.address
        self._kash = create_eoa_client(
            chain_id=8453,
            rpc=BASE_RPC,
            signer=self._signer,
        )
        self._approved = False

    async def _ensure_approval(self) -> None:
        if self._approved:
            return
        current = await self._kash.account.usdc_allowance(self._owner, KASH_MARKET)
        if current < usdc(1_000_000):  # arbitrary "enough" threshold
            await self._kash.trades.send.approve(
                BuildApproveParams(
                    account=self._owner,
                    spender=KASH_MARKET,
                    amount=MAX_UINT256,
                ),
            )
        self._approved = True

    def on_tick(self) -> None:
        """Hummingbot calls this on every tick interval. Kick off async work."""
        from hummingbot.core.utils.async_utils import safe_ensure_future

        safe_ensure_future(self._tick_async())

    async def _tick_async(self) -> None:
        await self._ensure_approval()

        # Read Kash quote
        kash_quote = await self._kash.markets.quote(
            KASH_MARKET,
            QuoteParams(side="BUY", outcome=0, amount=usdc(10)),
        )
        # …compare against other-venue price; if profitable, trade:
        if self._is_profitable(kash_quote):
            await self._kash.trades.send.buy(
                KASH_MARKET,
                BuildBuyParams(
                    account=self._owner,
                    outcome=0,
                    amount_usdc=usdc(10),
                    max_slippage_bps=50,
                ),
            )

    def _is_profitable(self, kash_quote) -> bool:
        # Strategy-specific logic.
        return False  # placeholder
```

## What you need to bring

| Resource               | Description                                                                                                                                                                                                                                                                       |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **EOA private key**    | Stored wherever your existing trading-key infra lives. Typically in env vars, AWS Secrets Manager, Vault, or a hardware wallet. The SDK's `LocalEoaSigner` and `JsonRpcEoaSigner` adapters cover the common cases; implement the `EoaSignerAdapter` Protocol for anything custom. |
| **Base RPC URL**       | Any RPC provider with EIP-1559 support — Alchemy, Infura, QuickNode, your own node. WebSocket URLs (`wss://`) enable real-time `client.markets.watch`.                                                                                                                            |
| **USDC + ETH balance** | The trading EOA holds USDC for trades and ETH for gas. No paymaster sponsorship in EOA mode.                                                                                                                                                                                      |
| **Market addresses**   | Hardcoded in your strategy config or fetched via a side channel (raw `httpx` call to `api.kash.bot/v1/markets`, or — once shipped — `kashdao-sdk` Python).                                                                                                                        |

## Performance expectations

| Operation                                 | Latency profile                                                       |
| ----------------------------------------- | --------------------------------------------------------------------- |
| `client.trades.build_*`                   | ~50–150ms (one RPC call: `eth_getTransactionCount` + on-chain quote)  |
| `client.trades.prepare_*`                 | ~150–400ms (build + `eth_estimateGas` + `eth_feeHistory` in parallel) |
| `signer.sign_transaction`                 | <10ms locally; varies for remote signers                              |
| `client.trades.submit`                    | ~50–150ms (`eth_sendRawTransaction`)                                  |
| Inclusion on Base                         | ~2s (block time) for confirmation 1                                   |
| `client.trades.send.buy(..., wait=False)` | ~250–600ms total to `transaction_hash` (no wait)                      |
| `client.trades.send.buy(..., wait=True)`  | ~3–4s total to receipt (default)                                      |

## Observability

Pass `hooks=KashProtocolHooks(...)` on `create_eoa_client` to get
fire-and-forget telemetry callbacks for every signer call and (in SA
mode, when shipped) every bundler RPC. Hooks are async-but-never-
awaited; exceptions are silently dropped — they MUST NOT add latency
to the request path.

## Cancellation

Every async method on `EoaClient` takes a `signal` parameter (an
`asyncio.Event` or any object with an `aborted` attribute). Cancellation
raises `KashAbortedError` (a `KashProtocolError` subclass; safe to
distinguish from operational errors).

## Troubleshooting

- **`KashSignerError(STALE_SIGNED_TX)`** on `submit`: you signed a
  build-time tx and populated gas/fees afterwards. Either (a) use
  `client.trades.send.buy(...)` which handles this for you, or (b)
  call `client.trades.hash_of(transaction)` after populating gas and
  re-sign before submit.
- **`KashConfigError(UNKNOWN_CHAIN)`**: the chain ID isn't in the
  static registry. For local dev (Anvil / Hardhat / Tenderly), pass
  `custom_chain=CustomChain(...)` to bypass the registry.
- **`KashSimulationRevertedError`**: the trade would revert on-chain.
  The SDK's pre-flight `eth_call` caught it before you paid for
  signing-infra round-trips. Inspect `decoded_error` to see which
  Market custom error fired.

## Validation status

- **SDK read path: validated on Base mainnet.** `markets.state` and
  `markets.quote` have been exercised against live mainnet markets
  (chain 8453) — the deployed Market ABI matches the SDK.
- **Write path (approve / buy): operator-validated with funds.** The
  read path proves connectivity + ABI; the first live trade requires a
  trading EOA funded with USDC + ETH on Base. Validate with a tiny
  `total_budget_usdc` (e.g. 1–2 USDC) and a single small slice, confirm
  the on-chain `tx`, then scale up. A ≥ 1 hour soak under real risk
  limits is the bar before unattended operation.

## Open issues / future work

- **`examples/hummingbot/kash_accumulator.py`** — shipped and runnable;
  the recommended starting point (see "Start here" above).
- **`examples/hummingbot/amm_arb_kash_uniswap.py`** — SKELETON: the SDK
  integration shape is production-ready, but the Uniswap quote and the
  spread-detection logic are stubbed and need to be wired to your real
  venue + sizing model before going live.
- **Smart Account mode for Hummingbot** — `create_smart_account_client`
  is fully supported in the SDK; a Hummingbot-specific worked
  example for AA stacks (Privy embedded wallets, Coinbase Smart
  Wallet) is on the same follow-on track as the EOA reference
  strategy. EOA mode is the recommended Hummingbot configuration today.
- **Native Hummingbot Gateway connector** — TypeScript Gateway
  connector that exposes Kash via stock `gateway connect kash` UX.
  Deferred until external demand materializes; tracked separately
  from this Python SDK.

Track progress at https://github.com/KashDAO/protocol-sdk-python/issues.
