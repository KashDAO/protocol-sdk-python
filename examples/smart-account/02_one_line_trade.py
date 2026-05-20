"""Smart-account mode — Example 02: One-line trade.

Demonstrates ``client.trades.send.buy(...)`` — the highest-level
convenience API. It builds, signs, submits, and (because ``wait=True``
by default) waits for the bundler to surface the receipt in a single
call.

This example DOES broadcast a UserOp. Gate it behind ``--confirm`` to
avoid accidental fires.

The SmartAccount must be funded with USDC and have approved the
market for spending. For a "first trade" path that handles approval
in the same UserOp via ``executeBatch``, see ``09_first_trade_approval.py``.

Run::

    KASH_PRIVATE_KEY=0x... \\
    KASH_MARKET=0x... \\
    KASH_BUNDLER_URL=https://api.pimlico.io/v2/84532/rpc?apikey=... \\
    python examples/smart-account/02_one_line_trade.py --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from eth_account import Account

from kashdao_protocol_sdk import (
    BuildBuyParams,
    BundlerOptions,
    create_smart_account_client,
    usdc,
    viem_account_signer,
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
    bundler_url = _require_env("KASH_BUNDLER_URL")
    rpc = os.environ.get("KASH_BASE_RPC_URL", "https://sepolia.base.org")

    if not confirm:
        sys.stderr.write(
            "Refusing to broadcast without --confirm. This example places a "
            f"real {amount_usdc}-USDC trade on Base Sepolia.\n"
        )
        sys.exit(1)

    account = Account.from_key(pk)
    async with create_smart_account_client(
        chain_id=84532,
        rpc=rpc,
        signer=viem_account_signer(account),
        bundler=BundlerOptions(provider="pimlico", url=bundler_url),
    ) as client:
        sa_address = await client.account.compute_address(client.signer.owner_address)
        print(f"Buying {amount_usdc} USDC of outcome 0 on market {market}…")
        print(f"  SmartAccount: {sa_address}")
        receipt = await client.trades.send.buy(
            market,
            BuildBuyParams(
                account=sa_address,
                outcome=0,
                amount_usdc=usdc(amount_usdc),
                max_slippage_bps=50,  # 0.5%
            ),
        )
        print(
            "✓ trade landed:\n"
            f"  user-op hash:  {receipt.user_op_hash}\n"
            f"  tx hash:       {receipt.transaction_hash}\n"
            f"  block number:  {receipt.block_number}\n"
            f"  success:       {receipt.success}\n"
            f"  gas used:      {receipt.gas_used}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amount", type=int, default=1, help="USDC amount (default: 1).")
    parser.add_argument(
        "--confirm", action="store_true", help="Required — broadcasts a real UserOp."
    )
    args = parser.parse_args()
    asyncio.run(main(args.amount, confirm=args.confirm))
