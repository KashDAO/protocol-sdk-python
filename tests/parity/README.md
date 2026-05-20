# Parity tests

Byte-equality tests that pin the Python SDK's `build_*_user_op` output
to the TypeScript SDK's. Two language implementations of the same
protocol must produce **exactly** the same `callData` and `userOpHash`
for identical inputs — anything else is a portability bug that will
surface as a chain-side revert at the worst possible moment.

## How it works

A generator script in the TypeScript package (target path:
`packages/protocol-sdk/scripts/generate-parity-fixtures.ts`) writes a
shared fixture file at:

```
tests/parity/fixtures/userop_parity.json
```

The Python parity harness (`test_userop_parity.py`) loads that file and
asserts byte-equality of `callData` and `userOpHash` against the
SDK's output for each vector. Until the generator script lands, the
fixture file does not exist and the parity tests skip cleanly.

## Fixture format

```json
{
  "version": "1",
  "vectors": [
    {
      "name": "buy_5_usdc_outcome_0",
      "kind": "buy",
      "inputs": {
        "chain_id": 84532,
        "sender": "0x...",
        "market": "0x...",
        "outcome": 0,
        "amount_usdc": "5000000",
        "max_slippage_bps": 50,
        "deadline_unix_seconds": 1700000000,
        "nonce_key": 0,
        "nonce": "0x...",
        "quote_result_amount_out_min": "1234567890000000000"
      },
      "expected": {
        "call_data": "0x...",
        "user_op_hash": "0x..."
      }
    }
  ]
}
```

### Field-by-field

| Path                                           | Type    | Notes                                                                                                                                                       |
| ---------------------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `version`                                      | string  | Fixture format version. Currently `"1"`.                                                                                                                    |
| `vectors[]`                                    | array   | At least one vector.                                                                                                                                        |
| `vectors[].name`                               | string  | Human-readable id, used in test failure messages.                                                                                                           |
| `vectors[].kind`                               | string  | `"buy"`, `"sell"`, or `"close_position"`.                                                                                                                   |
| `vectors[].inputs.chain_id`                    | int     | Chain id (84532 for Base Sepolia, 8453 for Base mainnet).                                                                                                   |
| `vectors[].inputs.sender`                      | hex     | Smart account address (`BuildBuyParams.smart_account`).                                                                                                     |
| `vectors[].inputs.market`                      | hex     | Market contract address.                                                                                                                                    |
| `vectors[].inputs.outcome`                     | int     | Outcome index.                                                                                                                                              |
| `vectors[].inputs.amount_usdc`                 | string  | Required for `buy`. Atomic 6-decimal USDC.                                                                                                                  |
| `vectors[].inputs.amount_tokens`               | string  | Required for `sell`. WAD-scale outcome tokens.                                                                                                              |
| `vectors[].inputs.max_slippage_bps`            | int     | Slippage tolerance in basis points.                                                                                                                         |
| `vectors[].inputs.deadline_unix_seconds`       | int     | Deadline (seconds since unix epoch).                                                                                                                        |
| `vectors[].inputs.nonce_key`                   | int     | EntryPoint v0.7 uint192 nonce key.                                                                                                                          |
| `vectors[].inputs.nonce`                       | int/hex | The nonce returned by `EntryPoint.getNonce` at fixture generation time.                                                                                     |
| `vectors[].inputs.quote_result_amount_out_min` | string  | The `amount_out` returned by the on-chain quote at generation time. The Python harness mocks `get_quote` to return this so the test never hits the network. |
| `vectors[].expected.call_data`                 | hex     | Reference `callData` from the TypeScript SDK.                                                                                                               |
| `vectors[].expected.user_op_hash`              | hex     | Reference `userOpHash` from the TypeScript SDK.                                                                                                             |

All hex values are `0x`-prefixed and case-insensitive (the harness
lowercases both sides before comparison). All integer-valued strings
must be base-10 unless explicitly hex-prefixed (`0x...`).

## Running

```sh
pytest -m parity -v
```

Collect-only (CI safety check that the harness still parses):

```sh
pytest -m parity --collect-only -q
```

## Adding a new vector

1. Add a generation case to the TS generator script.
2. Re-run the generator. It writes a new vector to `userop_parity.json`.
3. Run the Python parity tests — every new vector should pass on the
   first run (if it doesn't, that's the byte-equality bug we're here
   to catch).

Never hand-edit `userop_parity.json`. The whole point of the suite is
that the file is a deterministic projection of the TS SDK's output.
