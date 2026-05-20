# EOA examples

Vanilla EIP-1559 trading from a plain EOA — the canonical Hummingbot
path. No bundler, no smart account, no ERC-4337. Bring a private key
and an RPC URL.

## Environment variables

| Variable              | Required | Description                                                                   |
| --------------------- | -------- | ----------------------------------------------------------------------------- |
| `KASH_RPC_URL`        | yes      | Base Sepolia RPC URL (HTTPS or WSS). Use your own provider, never a Kash one. |
| `KASH_PRIVATE_KEY`    | yes      | 0x-prefixed 32-byte hex private key for the trading EOA. **Testnet only.**    |
| `KASH_MARKET_ADDRESS` | yes      | 0x-prefixed market contract address on Base Sepolia.                          |

## Scripts

| Script             | What it does                                                                      |
| ------------------ | --------------------------------------------------------------------------------- |
| `quote_and_buy.py` | Reads a quote and (with `--confirm`) submits a small EIP-1559 BUY.                |
| `watch_market.py`  | Subscribes to a market's real-time event stream over WebSocket and prints events. |

## Running

Read-only quote (the default — no on-chain side effects):

```sh
KASH_RPC_URL=https://sepolia.base.org \
KASH_PRIVATE_KEY=0x... \
KASH_MARKET_ADDRESS=0x... \
python examples/eoa/quote_and_buy.py
```

Submit a real on-chain BUY:

```sh
KASH_RPC_URL=https://sepolia.base.org \
KASH_PRIVATE_KEY=0x... \
KASH_MARKET_ADDRESS=0x... \
python examples/eoa/quote_and_buy.py --confirm --amount 5 --outcome 0
```

Watch a market's events for a minute:

```sh
KASH_RPC_URL=wss://base-sepolia.g.alchemy.com/v2/<KEY> \
KASH_MARKET_ADDRESS=0x... \
python examples/eoa/watch_market.py --seconds 60
```

The `--confirm` flag is the universal opt-in across every Kash example.
Anything that touches the chain is gated behind it.

## What to expect on success

- Without `--confirm`: prints the quote (input/output amounts in atomic
  units, post-trade reserves, post-trade probabilities) and exits 0.
- With `--confirm`: submits an EIP-1559 transaction, waits for
  confirmation, and prints `tx_hash`, `block`, `status`, and `gas`.

## Errors

The SDK's error catalog is documented in the [README](../../README.md#errors).
The classes you may see from this script:

- `KashConfigError` — bad RPC URL, bad private key, unsupported chain.
  Non-retryable; fix the inputs.
- `KashChainError` — RPC is down or rate-limiting. Retryable; back off
  and try again, or switch RPC providers.
- `KashSimulationRevertedError` — pre-flight `eth_call` revealed the
  trade would revert on-chain (slippage too tight, market frozen, etc).
  Inspect `decoded_error` for the canonical contract revert.
- `KashSignerError` — the local signer failed (corrupt key bytes,
  signing library mismatch). Non-retryable.
- `KashProtocolError` — base class; catches anything not enumerated above.
