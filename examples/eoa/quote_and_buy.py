"""Quote and (optionally) buy outcome tokens via the EOA-mode SDK.

Demonstrates the canonical Hummingbot path: a local-key EOA reads a
quote via ``client.markets.quote`` and, if the user passes
``--confirm``, submits a small BUY via ``client.trades.send.buy`` with
conservative slippage.

Required environment variables
------------------------------

- ``KASH_RPC_URL``         — Base Sepolia RPC URL (HTTPS or WSS).
- ``KASH_PRIVATE_KEY``     — 0x-prefixed 32-byte hex private key for the
                             trading EOA. Use a TESTNET key only.
- ``KASH_MARKET_ADDRESS``  — 0x-prefixed market contract address.

Optional flags
--------------

- ``--amount <usdc>``      — USDC amount (whole-USDC, default 10) to
                             quote and (with ``--confirm``) buy.
- ``--outcome <index>``    — Outcome index to buy (default 0).
- ``--confirm``            — Actually submit the BUY transaction. Without
                             this flag the script is read-only.

Usage
-----

Read-only quote::

    KASH_RPC_URL=https://sepolia.base.org \\
    KASH_PRIVATE_KEY=0x... \\
    KASH_MARKET_ADDRESS=0x... \\
    python examples/eoa/quote_and_buy.py

Submit a real on-chain BUY (Base Sepolia)::

    KASH_RPC_URL=https://sepolia.base.org \\
    KASH_PRIVATE_KEY=0x... \\
    KASH_MARKET_ADDRESS=0x... \\
    python examples/eoa/quote_and_buy.py --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Final

from eth_account import Account

from kashdao_protocol_sdk import (
    BuildBuyParams,
    KashProtocolError,
    QuoteParams,
    SendEoaResultWaited,
    create_eoa_client,
    format_tokens,
    format_usdc,
    usdc,
    viem_account_eoa_signer,
)

#: Base Sepolia chain id. The example targets testnet only.
BASE_SEPOLIA_CHAIN_ID: Final = 84532

#: Conservative slippage for the example. 50 bps = 0.5%.
DEFAULT_SLIPPAGE_BPS: Final = 50


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(f"error: {name} environment variable is required\n")
        raise SystemExit(2)
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quote and optionally buy outcome tokens via the EOA SDK."
    )
    parser.add_argument(
        "--amount",
        type=int,
        default=10,
        help="USDC amount to quote/buy (whole USDC). Default 10.",
    )
    parser.add_argument(
        "--outcome",
        type=int,
        default=0,
        help="Outcome index to buy. Default 0.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually submit the BUY. Without this flag the script is read-only.",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    rpc_url = _require_env("KASH_RPC_URL")
    private_key = _require_env("KASH_PRIVATE_KEY")
    market_address = _require_env("KASH_MARKET_ADDRESS")

    if args.amount <= 0:
        sys.stderr.write("error: --amount must be positive\n")
        raise SystemExit(2)
    if args.outcome < 0:
        sys.stderr.write("error: --outcome must be non-negative\n")
        raise SystemExit(2)

    account = Account.from_key(private_key)
    signer = viem_account_eoa_signer(account)
    amount_atomic = usdc(args.amount)

    print(f"Trading EOA:    {signer.owner_address}")
    print(f"Market:         {market_address}")
    print(f"Outcome:        {args.outcome}")
    print(f"Amount (USDC):  {format_usdc(amount_atomic)}")

    async with create_eoa_client(
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        rpc=rpc_url,
        signer=signer,
    ) as client:
        try:
            quote = await client.markets.quote(
                market_address,
                QuoteParams(side="BUY", outcome=args.outcome, amount=amount_atomic),
            )
        except KashProtocolError as err:
            sys.stderr.write(f"quote failed: {err}\n")
            raise SystemExit(1) from err

        print()
        print("Quote")
        print("-----")
        print(f"  amount_in (USDC):  {format_usdc(quote.amount_in)}")
        print(f"  amount_out (tok):  {format_tokens(quote.amount_out)}")
        print(f"  reserve_after:     {format_tokens(quote.reserve_after_wad)}")
        print(f"  prices_after_wad:  {quote.prices_after_wad}")

        if not args.confirm:
            print()
            print("Read-only run complete. Re-run with --confirm to submit a BUY.")
            return

        print()
        print("Submitting BUY ...")
        try:
            result = await client.trades.send.buy(
                market_address,
                BuildBuyParams(
                    account=signer.owner_address,
                    outcome=args.outcome,
                    amount_usdc=amount_atomic,
                    max_slippage_bps=DEFAULT_SLIPPAGE_BPS,
                ),
            )
        except KashProtocolError as err:
            sys.stderr.write(f"buy failed: {err}\n")
            raise SystemExit(1) from err

        print(f"  tx_hash: {result.transaction_hash}")
        if isinstance(result, SendEoaResultWaited):
            status = "success" if result.success else "reverted"
            print(f"  block:   {result.block_number}")
            print(f"  status:  {status}")
            print(f"  gas:     {result.gas_used}")


if __name__ == "__main__":
    asyncio.run(main())
