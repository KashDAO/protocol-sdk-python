"""Find Kash market addresses to plug into `kash_accumulator.py`.

The on-chain protocol SDK trades against a market *contract address*, but
it has no market-discovery surface — discovery lives behind the Kash
public API. This helper lists live markets so you can copy a
`contractAddress` (and pick an `outcome_index`) into your strategy config.

It needs a Kash API key with `markets:read` (create a free one under
Settings → API Keys at https://app.kash.bot). A `kash_live_*` key lists
Base-mainnet markets; a `kash_test_*` key lists Base Sepolia markets.
The key is read from the `KASH_API_KEY` environment variable and is only
used for this read — your trading EOA key is separate and never touches
the API.

Usage::

    KASH_API_KEY=kash_live_... python find_kash_markets.py
    KASH_API_KEY=kash_live_... python find_kash_markets.py --status ACTIVE --limit 50
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

_PROD = "https://api.kash.bot/v1"
_STAGING = "https://api-staging.kash.bot/v1"


def _base_url(api_key: str) -> str:
    # Mirror the SDK/CLI auto-route: test keys → staging, everything else → prod.
    return _STAGING if api_key.startswith("kash_test_") else _PROD


def main() -> int:
    parser = argparse.ArgumentParser(description="List Kash markets (address + outcomes).")
    parser.add_argument("--status", default="ACTIVE", help="UNSEEDED | ACTIVE | RESOLVED")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    api_key = os.environ.get("KASH_API_KEY", "")
    if not api_key:
        print(
            "KASH_API_KEY is unset. Create a read-only key under Settings → API Keys "
            "at https://app.kash.bot, then: KASH_API_KEY=kash_live_... python find_kash_markets.py",
            file=sys.stderr,
        )
        return 2

    url = f"{_base_url(api_key)}/markets"
    try:
        resp = httpx.get(
            url,
            params={"status": args.status, "limit": args.limit},
            headers={"X-API-Key": api_key, "Accept": "application/json"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    if resp.status_code == 401:
        print("401 Unauthorized — check KASH_API_KEY (Settings → API Keys).", file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        return 1

    markets = resp.json().get("data", [])
    if not markets:
        print(f"No {args.status} markets found.")
        return 0

    print(f"{len(markets)} {args.status} market(s) on {_base_url(api_key)}:\n")
    for m in markets:
        title = m.get("title") or "(untitled)"
        print(f"• {title}")
        print(f"    contractAddress: {m['contractAddress']}   chainId: {m.get('chainId')}")
        outcomes = m.get("outcomes", [])
        for o in outcomes:
            prob = o.get("probability")
            prob_str = f"{prob:.2%}" if isinstance(prob, (int, float)) else "?"
            print(f"    outcome_index={o['index']}  {o.get('label', '?')}  (≈{prob_str})")
        print()
    print("Copy a contractAddress into `market_address` and an outcome_index into your config.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
