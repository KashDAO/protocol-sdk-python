# Smart Account examples

ERC-4337 v0.7 trading via SimpleAccount + bundler. The right path for
consumers on AA stacks (Privy, Coinbase Smart Wallet, Pimlico). Adds a
bundler URL to the EOA shape; the rest of the surface is identical.

## Environment variables

| Variable              | Required | Description                                                                   |
| --------------------- | -------- | ----------------------------------------------------------------------------- |
| `KASH_RPC_URL`        | yes      | Base Sepolia RPC URL (HTTPS or WSS). Use your own provider, never a Kash one. |
| `KASH_BUNDLER_URL`    | yes      | ERC-4337 v0.7 bundler URL (Alchemy, Pimlico, Stackup, or your own).           |
| `KASH_PRIVATE_KEY`    | yes      | 0x-prefixed 32-byte hex private key for the SA owner. **Testnet only.**       |
| `KASH_MARKET_ADDRESS` | yes      | 0x-prefixed market contract address on Base Sepolia.                          |

## Scripts

| Script                       | What it does                                                                                                           |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `quote_and_buy.py`           | Reads a quote and (with `--confirm`) submits a small SA BUY UserOp.                                                    |
| `buy_with_simple_account.py` | Derives the SA via CREATE2, prints deploy/balance state, and (with `--confirm`) submits a 1-USDC BUY with auto-deploy. |

## Prerequisites

The SimpleAccount in this example pays its own gas — there is **no
paymaster**. That means the SA address must hold both:

- USDC for the trade (in atomic 6-decimal units).
- ETH for gas (the bundler deducts gas from the SA's ETH balance).

The script computes the deterministic SA address up-front and prints
both balances before submitting anything. Fund the SA address (not the
owner EOA) before running with `--confirm`.

The first BUY a fresh SA submits will deploy the SA on-chain via the
factory init fields (`auto_deploy=True` is wired automatically when the
script detects the SA is not yet deployed). Subsequent UserOps skip
`auto_deploy`.

## Running

Read-only quote + balance check (the default — no on-chain side effects):

```sh
KASH_RPC_URL=https://sepolia.base.org \
KASH_BUNDLER_URL=https://api.pimlico.io/v2/84532/rpc?apikey=... \
KASH_PRIVATE_KEY=0x... \
KASH_MARKET_ADDRESS=0x... \
python examples/smart_account/quote_and_buy.py
```

Submit a real on-chain BUY UserOp:

```sh
KASH_RPC_URL=https://sepolia.base.org \
KASH_BUNDLER_URL=https://api.pimlico.io/v2/84532/rpc?apikey=... \
KASH_PRIVATE_KEY=0x... \
KASH_MARKET_ADDRESS=0x... \
python examples/smart_account/quote_and_buy.py --confirm --amount 5 --outcome 0
```

The `--confirm` flag is the universal opt-in across every Kash example.
Anything that touches the chain is gated behind it.

## What to expect on success

- Without `--confirm`: prints the SA address, deploy/balance status,
  the quote, and exits 0.
- With `--confirm`: submits a UserOp, waits for the bundler receipt,
  and prints `user_op_hash`, the inner `tx_hash`, and `success`.

## Errors

The SDK's error catalog is documented in the [README](../../README.md#errors).
The classes you may see from this script:

- `KashConfigError` — bad RPC URL, bad bundler URL, bad private key,
  unsupported chain. Non-retryable; fix the inputs.
- `KashChainError` — chain RPC is down or rate-limiting. Retryable.
- `KashBundlerError` — the bundler rejected the UserOp (insufficient
  prefund / SA gas, malformed UserOp, paymaster issues). Inspect
  `error.context` for the bundler's diagnostic.
- `KashSimulationRevertedError` — pre-flight reveal of an on-chain
  revert. Inspect `decoded_error` for the contract revert.
- `KashSignerError` — the local signer failed.
- `KashProtocolError` — base class; catches anything not enumerated above.
