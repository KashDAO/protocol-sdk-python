"""Subscribe to a market's real-time event stream via the EOA-mode SDK.

Demonstrates ``client.markets.watch(...)``: opens a WebSocket connection
to the configured RPC, subscribes to the canonical Kash market events
(buys, sells, market resolved, market frozen), prints each event as it
arrives, and unsubscribes cleanly after a fixed wall-clock duration.

This script is **read-only**. It never signs, never broadcasts, never
costs anything beyond the RPC quota of your provider.

Required environment variables
------------------------------

- ``KASH_RPC_URL``         — Base Sepolia RPC URL. **Must be ``wss://``
                             (or ``ws://`` for local dev).** The watch
                             subscription opens a long-lived WebSocket;
                             plain HTTPS RPCs cannot push events and the
                             SDK will fall back to polling, which is
                             slower and chattier than what this example
                             demonstrates.
- ``KASH_MARKET_ADDRESS``  — 0x-prefixed market contract address.

Optional flags
--------------

- ``--seconds <int>``      — How long to listen before unsubscribing.
                             Default 60.

Event types you'll see
----------------------

- ``BuyEvent``             — A trader bought outcome tokens.
- ``SellEvent``            — A trader sold outcome tokens.
- ``MarketResolvedEvent``  — Oracle resolved the market; payouts unlocked.
- ``MarketFrozenEvent``    — Market frozen (admin or auto on resolve);
                             new trades blocked, redemptions still work.

Each event is printed as a one-line summary so the terminal output
stays readable when an active market fires several events per second.

Usage
-----

::

    KASH_RPC_URL=wss://base-sepolia.g.alchemy.com/v2/<KEY> \\
    KASH_MARKET_ADDRESS=0x... \\
    python examples/eoa/watch_market.py

    # Listen for 5 minutes instead of 60 seconds:
    KASH_RPC_URL=wss://... \\
    KASH_MARKET_ADDRESS=0x... \\
    python examples/eoa/watch_market.py --seconds 300
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Final

from kashdao_protocol_sdk import (
    BASE_SEPOLIA,
    KashChainError,
    LocalEoaSigner,
    WatchEvent,
    WatchOptions,
    create_eoa_client,
)

#: Base Sepolia chain id. The example targets testnet only.
BASE_SEPOLIA_CHAIN_ID: Final = BASE_SEPOLIA.chain_id

#: Default subscription duration (seconds). Long enough to catch
#: activity on a moderately busy market without tying up the terminal.
DEFAULT_SECONDS: Final = 60

#: Disposable signer used only because ``create_eoa_client`` requires
#: one — this example never signs anything. The address derived from
#: this key never appears on-chain.
_DISPOSABLE_KEY: Final = "0x" + "11" * 32


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(f"error: {name} environment variable is required\n")
        raise SystemExit(2)
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Subscribe to a market's events and print them as they arrive."
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=DEFAULT_SECONDS,
        help=f"How long to listen before unsubscribing. Default {DEFAULT_SECONDS}.",
    )
    return parser.parse_args()


def _on_event(event: WatchEvent) -> None:
    """Print a one-line summary of each surfaced event.

    ``WatchEvent`` is a discriminated union; we use ``type(event).__name__``
    to keep the example robust against future event-class additions.
    """
    name = type(event).__name__
    payload = {k: v for k, v in vars(event).items() if not k.startswith("_")}
    print(f"[event] {name}: {payload}")


def _on_error(err: KashChainError) -> None:
    """Surface RPC-level errors without tearing the subscription down.

    The SDK reconnects on transient transport failures automatically;
    this hook exists so an operator can spot pathological providers
    (e.g. constant 429s) in real time.
    """
    print(f"[error] {err.code}: {err}", file=sys.stderr)


async def main() -> None:
    args = _parse_args()
    rpc_url = _require_env("KASH_RPC_URL")
    market_address = _require_env("KASH_MARKET_ADDRESS")

    if args.seconds <= 0:
        sys.stderr.write("error: --seconds must be positive\n")
        raise SystemExit(2)

    if not (rpc_url.startswith(("wss://", "ws://"))):
        sys.stderr.write(
            "warning: KASH_RPC_URL is not a WebSocket URL; the SDK will fall back\n"
            "         to polling. For best results pass a wss:// endpoint.\n"
        )

    print(f"Market:    {market_address}")
    print(f"Duration:  {args.seconds}s")
    print("Listening for events ...")
    print()

    signer = LocalEoaSigner.from_private_key(_DISPOSABLE_KEY)
    async with create_eoa_client(
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        rpc=rpc_url,
        signer=signer,
    ) as client:
        subscription = client.markets.watch(
            market_address,
            WatchOptions(on_event=_on_event, on_error=_on_error),
        )
        try:
            await asyncio.sleep(args.seconds)
        finally:
            await subscription.aclose()

    print()
    print("Subscription closed cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
