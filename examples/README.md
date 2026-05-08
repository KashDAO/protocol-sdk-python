# Examples

Runnable Python scripts that demonstrate the SDK end-to-end. Each
example is self-contained: clone the repo, install the SDK, populate
the config (RPC URL, signer key, etc.), and run.

## Layout

| Subdir           | Audience                                                                          |
| ---------------- | --------------------------------------------------------------------------------- |
| `eoa/`           | Vanilla EOA mode (Hummingbot, ad-hoc bots, anyone with their own EIP-1559 signer) |
| `smart-account/` | ERC-4337 v0.7 mode (Privy, Coinbase Smart Wallet, AA stacks)                      |
| `hummingbot/`    | Reference Hummingbot strategies that import the SDK                               |
| `local-anvil/`   | End-to-end against a local Anvil + dev-stack deploy                               |

Each example carries a leading module docstring with its required
env vars + setup steps. See `HUMMINGBOT_INTEGRATION.md` for the
canonical Hummingbot strategy walk-through;
`examples/hummingbot/amm_arb_kash_uniswap.py` is the runnable reference.

| Example                              | What it shows                                                                                            |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| `eoa/01_quickstart.py`               | Construct EOA client, read market state + a non-binding quote.                                           |
| `eoa/02_one_line_trade.py`           | All-in-one buy via `client.trades.send.buy(...)`. Gated behind `--confirm`.                              |
| `eoa/03_error_handling.py`           | Catching the typed `KashProtocolError` hierarchy; deliberately triggers a simulation revert.             |
| `smart-account/01_quickstart.py`     | Construct SA client, derive the SimpleAccount address, health-check the bundler.                         |
| `smart-account/02_one_line_trade.py` | All-in-one SA-mode buy via `client.trades.send.buy(...)`. Gated behind `--confirm`.                      |
| `hummingbot/amm_arb_kash_uniswap.py` | Reference Hummingbot `ScriptStrategyBase`. SKELETON — Uniswap leg + sizing model stubbed for production. |
| `local-anvil/01_quickstart.py`       | `custom_chain` escape hatch: run against a local Anvil deploy of the protocol contracts.                 |

## Running

Each example sets out its required env vars in a leading docstring.
Common pattern:

```sh
pip install kashdao-protocol-sdk
KASH_RPC_URL=https://sepolia.base.org \
KASH_PRIVATE_KEY=0x... \
python examples/eoa/quote_and_buy.py
```

## Contributing examples

A good example is:

- **Self-contained.** No imports from sibling examples; copy-pasteable.
- **Documented.** A leading module docstring explains what it does and
  what env vars it needs.
- **Idempotent or annotated.** If it places an on-chain trade, say so
  loudly at the top and gate the action behind an explicit `--confirm`
  flag.
- **Free of secrets.** Only reads from env or CLI args.

Open a PR with the example + a one-line entry in this README.
