# Examples

Runnable Python scripts that demonstrate the SDK end-to-end. Each
example is self-contained: clone the repo, install the SDK, populate
the environment variables documented in the script's leading docstring,
and run.

## Layout

| Subdir           | Audience                                                                          |
| ---------------- | --------------------------------------------------------------------------------- |
| `solana/`        | Solana mainnet-beta (where new markets launch); needs the `[solana]` extra        |
| `eoa/`           | Vanilla EOA mode (Hummingbot, ad-hoc bots, anyone with their own EIP-1559 signer) |
| `smart_account/` | ERC-4337 v0.7 mode (Privy, Coinbase Smart Wallet, AA stacks)                      |

Each example carries a leading module docstring with its required
env vars + setup steps. See `HUMMINGBOT_INTEGRATION.md` for the
canonical Hummingbot strategy walk-through;
`examples/hummingbot/amm_arb_kash_uniswap.py` is the runnable reference.

| Example                              | What it shows                                                                                            |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| `solana/01_read_and_quote.py`        | Solana: read the program config and a market, print an exact buy quote. No signer, no funds.             |
| `solana/02_buy_and_sell.py`          | Solana: buy 1 USDC then sell it back with `max_slippage_bps`. Gated behind `--confirm`.                  |
| `solana/03_redeem_and_close.py`      | Solana: build -> simulate -> send a redeem, then close the empty position. Gated behind `--confirm`.     |
| `eoa/01_quickstart.py`               | Construct EOA client, read market state + a non-binding quote.                                           |
| `eoa/02_one_line_trade.py`           | All-in-one buy via `client.trades.send.buy(...)`. Gated behind `--confirm`.                              |
| `eoa/03_error_handling.py`           | Catching the typed `KashProtocolError` hierarchy; deliberately triggers a simulation revert.             |
| `smart-account/01_quickstart.py`     | Construct SA client, derive the SimpleAccount address, health-check the bundler.                         |
| `smart-account/02_one_line_trade.py` | All-in-one SA-mode buy via `client.trades.send.buy(...)`. Gated behind `--confirm`.                      |
| `hummingbot/amm_arb_kash_uniswap.py` | Reference Hummingbot `ScriptStrategyBase`. SKELETON — Uniswap leg + sizing model stubbed for production. |
| `local-anvil/01_quickstart.py`       | `custom_chain` escape hatch: run against a local Anvil deploy of the protocol contracts.                 |

## Running

Each script reads its config from environment variables and is
read-only by default. The `--confirm` flag is the universal opt-in: any
example that submits a transaction on-chain is gated behind `--confirm`,
so you can dry-run without surprises.

```sh
pip install kashdao-protocol-sdk
KASH_RPC_URL=https://sepolia.base.org \
KASH_PRIVATE_KEY=0x... \
KASH_MARKET_ADDRESS=0x... \
python examples/eoa/quote_and_buy.py            # read-only quote
python examples/eoa/quote_and_buy.py --confirm  # submits a BUY
```

The required env vars are listed in each script's module docstring and
in the subdirectory's `README.md`.

## Contributing examples

A good example is:

- **Self-contained.** No imports from sibling examples; copy-pasteable.
- **Documented.** A leading module docstring explains what it does and
  what env vars it needs.
- **Idempotent or annotated.** If it places an on-chain trade, gate
  the action behind an explicit `--confirm` flag and say so loudly at
  the top.
- **Free of secrets.** Only reads from env or CLI args.

Open a PR with the example + a one-line entry in this README.
