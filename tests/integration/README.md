# Integration tests

Opt-in tests that exercise the SDK against real Base Sepolia
infrastructure. Skipped on every fresh checkout — they only run when
the contributor has populated the environment variables below.

## Why opt-in

These tests need:

- A live Base Sepolia RPC URL (rate-limited public endpoints work).
- A funded testnet EOA private key.
- A specific Base Sepolia market address.

We don't ship any of those. Hardcoding test fixtures against a public
testnet creates a maintenance liability the moment the chain or the
market is reorganised. Each contributor brings their own pinned
artefact, runs the suite, and reports back.

## Environment variables

| Variable                | Required by                | Notes                                                  |
| ----------------------- | -------------------------- | ------------------------------------------------------ |
| `KASH_BASE_SEPOLIA_RPC` | every test                 | Base Sepolia RPC URL (HTTPS or WSS).                   |
| `KASH_TEST_OWNER_KEY`   | every test                 | 0x-prefixed 32-byte hex private key. **Testnet only.** |
| `KASH_TEST_MARKET`      | tests that target a market | 0x-prefixed market contract address on Base Sepolia.   |

Any test whose required env var is missing skips with a clear message.
The suite never fails for "missing env" — only for "real bug".

## Running

```sh
KASH_BASE_SEPOLIA_RPC=https://sepolia.base.org \
KASH_TEST_OWNER_KEY=0x... \
KASH_TEST_MARKET=0x... \
pytest -m integration -v
```

Collect-only (CI safety check that the suite still parses):

```sh
pytest -m integration --collect-only -q
```

## What's in the suite

- `test_eoa_smoke.py` — builds and simulates a BUY against the
  configured market, asserting the EIP-1559 envelope is well-formed
  and the simulation returns a discriminated `SimulationSuccess` /
  `SimulationFailure`. Never submits a transaction.

Add more files following the same pattern: gate every test with
`@pytest.mark.integration`, reuse the fixtures in `conftest.py`, and
skip cleanly when env vars are missing.
