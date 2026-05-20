"""Quote and (optionally) buy outcome tokens via the SA-mode SDK.

Demonstrates the ERC-4337 v0.7 path: a SimpleAccount derived from a
local-key owner, signed by ``LocalSigner``, submitted via a bundler.
The SA may not be deployed yet — ``send.buy(..., auto_deploy=True)``
will include the factory init fields on the first UserOp so the SA is
deployed lazily as a side effect of its first trade.

Required environment variables
------------------------------

- ``KASH_RPC_URL``         — Base Sepolia RPC URL (HTTPS or WSS).
- ``KASH_BUNDLER_URL``     — ERC-4337 v0.7 bundler URL (Alchemy, Pimlico,
                             Stackup, or your own).
- ``KASH_PRIVATE_KEY``     — 0x-prefixed 32-byte hex private key for the
                             SA owner. Use a TESTNET key only.
- ``KASH_MARKET_ADDRESS``  — 0x-prefixed market contract address.

Optional flags
--------------

- ``--amount <usdc>``      — USDC amount (whole-USDC, default 10) to
                             quote and (with ``--confirm``) buy.
- ``--outcome <index>``    — Outcome index to buy (default 0).
- ``--confirm``            — Actually submit the BUY UserOp. Without
                             this flag the script is read-only.

Prerequisites
-------------

The SA must hold its own ETH for gas (no paymaster in this example) and
its own USDC. Check both balances by running this script without
``--confirm`` first.

Usage
-----

Read-only quote::

    KASH_RPC_URL=https://sepolia.base.org \\
    KASH_BUNDLER_URL=https://... \\
    KASH_PRIVATE_KEY=0x... \\
    KASH_MARKET_ADDRESS=0x... \\
    python examples/smart-account/quote_and_buy.py

Submit a real on-chain BUY (Base Sepolia)::

    KASH_RPC_URL=https://sepolia.base.org \\
    KASH_BUNDLER_URL=https://... \\
    KASH_PRIVATE_KEY=0x... \\
    KASH_MARKET_ADDRESS=0x... \\
    python examples/smart-account/quote_and_buy.py --confirm
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
    ComputeSmartAccountAddressParams,
    KashProtocolError,
    LocalSigner,
    PrepareUserOpOptions,
    QuoteParams,
    SendResultWaited,
    compute_smart_account_address,
    create_smart_account_client,
    format_tokens,
    format_usdc,
    usdc,
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
        description="Quote and optionally buy outcome tokens via the SA SDK."
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
        help="Actually submit the BUY UserOp. Without this flag the script is read-only.",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    rpc_url = _require_env("KASH_RPC_URL")
    bundler_url = _require_env("KASH_BUNDLER_URL")
    private_key = _require_env("KASH_PRIVATE_KEY")
    market_address = _require_env("KASH_MARKET_ADDRESS")

    if args.amount <= 0:
        sys.stderr.write("error: --amount must be positive\n")
        raise SystemExit(2)
    if args.outcome < 0:
        sys.stderr.write("error: --outcome must be non-negative\n")
        raise SystemExit(2)

    account = Account.from_key(private_key)
    signer = LocalSigner.from_account(account)
    amount_atomic = usdc(args.amount)

    # Compute the SA address up-front so we can show balances even when
    # the SA isn't deployed yet (the factory's getAddress is a pure
    # CREATE2 computation).
    sa_address = await compute_smart_account_address(
        ComputeSmartAccountAddressParams(
            chain_id=BASE_SEPOLIA_CHAIN_ID,
            rpc=rpc_url,
            owner_address=signer.owner_address,
        )
    )

    print(f"Owner EOA:       {signer.owner_address}")
    print(f"Smart Account:   {sa_address}")
    print(f"Market:          {market_address}")
    print(f"Outcome:         {args.outcome}")
    print(f"Amount (USDC):   {format_usdc(amount_atomic)}")

    async with create_smart_account_client(
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        rpc=rpc_url,
        signer=signer,
        bundler=bundler_url,
    ) as client:
        usdc_balance = await client.account.usdc_balance(sa_address)
        gas_balance = await client.account.gas_balance(sa_address)
        is_deployed = await client.account.is_deployed(sa_address)

        print()
        print("Smart Account state")
        print("-------------------")
        print(f"  deployed:        {is_deployed}")
        print(f"  USDC balance:    {format_usdc(usdc_balance)}")
        print(f"  ETH balance:     {gas_balance} wei")

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
        print("Submitting BUY UserOp ...")
        try:
            result = await client.trades.send.buy(
                market_address,
                BuildBuyParams(
                    account=sa_address,
                    outcome=args.outcome,
                    amount_usdc=amount_atomic,
                    max_slippage_bps=DEFAULT_SLIPPAGE_BPS,
                ),
                prepare_options=PrepareUserOpOptions(
                    auto_deploy=not is_deployed,
                    owner_address=signer.owner_address,
                ),
            )
        except KashProtocolError as err:
            sys.stderr.write(f"buy failed: {err}\n")
            raise SystemExit(1) from err

        print(f"  user_op_hash: {result.user_op_hash}")
        if isinstance(result, SendResultWaited):
            tx_hash = result.receipt.receipt.get("transactionHash", "<unknown>")
            print(f"  tx_hash:      {tx_hash}")
            print(f"  success:      {result.receipt.success}")


if __name__ == "__main__":
    asyncio.run(main())
