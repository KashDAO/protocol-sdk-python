"""EOA mode — Example 02: One-line trade with confirmation.

Demonstrates the highest-level convenience API for EOA mode:
``client.trades.send.buy(...)`` builds, signs, submits, and (because
``wait=True`` by default) returns the on-chain receipt in a single
call.

This example is the "hello world" of placing a real trade. It DOES
broadcast a transaction — gate it behind ``--confirm`` to avoid
accidental fires.

Run::

    KASH_PRIVATE_KEY=0x... \\
    KASH_MARKET=0x... \\
    python examples/eoa/02_one_line_trade.py --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from eth_account import Account

from kashdao_protocol_sdk import (
    BuildBuyParams,
    create_eoa_client,
    usdc,
    viem_account_eoa_signer,
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(f"missing required env var: {name}\n")
        sys.exit(2)
    return value


async def main(amount_usdc: int, *, confirm: bool) -> None:
    pk = _require_env("KASH_PRIVATE_KEY")
    market = _require_env("KASH_MARKET")
    rpc = os.environ.get("KASH_BASE_RPC_URL", "https://sepolia.base.org")

    account = Account.from_key(pk)
    if not confirm:
        sys.stderr.write(
            "Refusing to broadcast without --confirm. This example places a "
            f"real {amount_usdc}-USDC trade on Base Sepolia.\n"
        )
        sys.exit(1)

    async with create_eoa_client(
        chain_id=84532,
        rpc=rpc,
        signer=viem_account_eoa_signer(account),
    ) as client:
        print(f"Buying {amount_usdc} USDC of outcome 0 on market {market}…")
        receipt = await client.trades.send.buy(
            market,
            BuildBuyParams(
                # In EOA mode `account` is the EOA itself; in SA mode
                # it's the SimpleAccount address. The mode-polymorphic
                # field name matches the TS SDK. Pass your EOA address
                # here. (The legacy ``smart_account=`` keyword still
                # works through Pydantic AliasChoices but is deprecated.)
                account=client.signer.owner_address,
                outcome=0,
                amount_usdc=usdc(amount_usdc),
                max_slippage_bps=50,  # 0.5%
            ),
        )
        print(
            "✓ trade landed:\n"
            f"  tx hash:       {receipt.transaction_hash}\n"
            f"  block number:  {receipt.block_number}\n"
            f"  success:       {receipt.success}\n"
            f"  gas used:      {receipt.gas_used}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--amount",
        type=int,
        default=1,
        help="USDC amount (default: 1).",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required — broadcasts a real transaction.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.amount, confirm=args.confirm))
