"""Buy outcome tokens via a SimpleAccount (ERC-4337 v0.7).

Demonstrates the canonical SA flow end-to-end:

1. Derive the SimpleAccount address deterministically from the owner
   EOA via :func:`compute_smart_account_address` — this is a pure
   CREATE2 computation, so it works whether or not the SA is deployed.
2. Check whether the SA is already on-chain via
   ``client.account.is_deployed(...)``. If not, the BUY UserOp will
   include the factory init fields so the SA is deployed lazily as
   a side effect of its first trade (``auto_deploy=True``).
3. Fetch a quote via ``client.markets.quote(...)`` to show projected
   output before committing.
4. Optionally submit a real 1-USDC BUY through the bundler.

The script is read-only by default. Pass ``--confirm`` to actually
submit. **This places a real on-chain trade on Base Sepolia and burns
testnet USDC.**

Required environment variables
------------------------------

- ``KASH_RPC_URL``         — Base Sepolia RPC URL (HTTPS or WSS).
- ``KASH_BUNDLER_URL``     — ERC-4337 v0.7 bundler URL. The SA must
                             have its own ETH for gas; the example
                             does not use a paymaster.
- ``KASH_MARKET_ADDRESS``  — 0x-prefixed market contract address.
- ``KASH_PRIVATE_KEY``     — Owner EOA private key. **Required only
                             with ``--confirm``.** The address derived
                             from this key signs the UserOp.

Optional flags
--------------

- ``--confirm``            — Submit the BUY UserOp. Without this flag
                             the script only quotes and prints the
                             SA's deployment / balance state.

Usage
-----

Read-only quote::

    KASH_RPC_URL=https://sepolia.base.org \\
    KASH_BUNDLER_URL=https://... \\
    KASH_MARKET_ADDRESS=0x... \\
    python examples/smart_account/buy_with_simple_account.py

Submit a real on-chain BUY (Base Sepolia)::

    KASH_RPC_URL=https://sepolia.base.org \\
    KASH_BUNDLER_URL=https://... \\
    KASH_MARKET_ADDRESS=0x... \\
    KASH_PRIVATE_KEY=0x... \\
    python examples/smart_account/buy_with_simple_account.py --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Final

from kashdao_protocol_sdk import (
    BASE_SEPOLIA,
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
BASE_SEPOLIA_CHAIN_ID: Final = BASE_SEPOLIA.chain_id

#: 1-USDC trade — small enough to be safe on testnet, large enough
#: to produce a meaningful quote.
TRADE_AMOUNT_USDC: Final = 1

#: Conservative slippage. 50 bps = 0.5%.
DEFAULT_SLIPPAGE_BPS: Final = 50


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(f"error: {name} environment variable is required\n")
        raise SystemExit(2)
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quote and (with --confirm) buy 1 USDC via a SimpleAccount."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually submit the BUY UserOp. Without this flag the script is read-only.",
    )
    return parser.parse_args()


def _confirm_or_exit() -> None:
    """Prompt the operator for explicit confirmation before submitting.

    The ``--confirm`` flag is the first gate; this prompt is the second.
    Skipping the prompt requires piping ``y\\n`` into stdin (CI-friendly)
    while still preventing an accidental Enter-key submission from a
    distracted developer.
    """
    print()
    print("=" * 72)
    print("WARNING: this places a REAL on-chain trade on Base Sepolia.")
    print("It will consume testnet USDC and ETH (for bundler gas) from the SA.")
    print("=" * 72)
    answer = input("Proceed? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        print("Aborted.")
        raise SystemExit(0)


async def main() -> None:
    args = _parse_args()
    rpc_url = _require_env("KASH_RPC_URL")
    bundler_url = _require_env("KASH_BUNDLER_URL")
    market_address = _require_env("KASH_MARKET_ADDRESS")

    if args.confirm:
        private_key = _require_env("KASH_PRIVATE_KEY")
        signer = LocalSigner.from_private_key(private_key)
        owner_address = signer.owner_address
    else:
        # Read-only path: we only need the SA address for display.
        # ``KASH_PRIVATE_KEY`` is allowed but not required.
        private_key = os.environ.get("KASH_PRIVATE_KEY")
        if private_key:
            signer = LocalSigner.from_private_key(private_key)
            owner_address = signer.owner_address
        else:
            signer = None
            owner_address = _require_env("KASH_OWNER_ADDRESS")

    amount_atomic = usdc(TRADE_AMOUNT_USDC)

    # Pure CREATE2 computation — works whether or not the SA is deployed.
    sa_address = await compute_smart_account_address(
        ComputeSmartAccountAddressParams(
            chain_id=BASE_SEPOLIA_CHAIN_ID,
            rpc=rpc_url,
            owner_address=owner_address,
        )
    )

    print(f"Owner EOA:       {owner_address}")
    print(f"Smart Account:   {sa_address}")
    print(f"Market:          {market_address}")
    print(f"Amount (USDC):   {format_usdc(amount_atomic)}")

    if signer is None:
        async with create_smart_account_client(
            chain_id=BASE_SEPOLIA_CHAIN_ID,
            rpc=rpc_url,
            # The bundler is unused on the read-only path but the
            # client constructor requires it — pass the configured URL.
            bundler=bundler_url,
            signer=LocalSigner.from_private_key("0x" + "11" * 32),
        ) as readonly_client:
            await _print_state_and_quote(readonly_client, sa_address, market_address, amount_atomic)
        return

    async with create_smart_account_client(
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        rpc=rpc_url,
        signer=signer,
        bundler=bundler_url,
    ) as client:
        is_deployed = await _print_state_and_quote(
            client, sa_address, market_address, amount_atomic
        )

        if not args.confirm:
            print()
            print("Read-only run complete. Re-run with --confirm to submit a BUY.")
            return

        _confirm_or_exit()

        print()
        print("Submitting BUY UserOp ...")
        try:
            result = await client.trades.send.buy(
                market_address,
                BuildBuyParams(
                    account=sa_address,
                    outcome=0,
                    amount_usdc=amount_atomic,
                    max_slippage_bps=DEFAULT_SLIPPAGE_BPS,
                ),
                prepare_options=PrepareUserOpOptions(
                    auto_deploy=not is_deployed,
                    owner_address=owner_address,
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


async def _print_state_and_quote(
    client: object,  # SmartAccountClient — kept untyped to dodge a forward-ref import.
    sa_address: str,
    market_address: str,
    amount_atomic: int,
) -> bool:
    """Print SA deployment + balances + a fresh quote. Returns ``is_deployed``."""
    # ``client`` is the SA client; the cast is local-only so we can keep
    # the helper signature simple for the example reader.
    sa_client = client  # type: ignore[assignment]
    is_deployed: bool = await sa_client.account.is_deployed(sa_address)  # type: ignore[attr-defined]
    usdc_balance: int = await sa_client.account.usdc_balance(sa_address)  # type: ignore[attr-defined]
    gas_balance: int = await sa_client.account.gas_balance(sa_address)  # type: ignore[attr-defined]

    print()
    print("Smart Account state")
    print("-------------------")
    print(f"  deployed:        {is_deployed}")
    print(f"  USDC balance:    {format_usdc(usdc_balance)}")
    print(f"  ETH balance:     {gas_balance} wei")

    try:
        quote = await sa_client.markets.quote(  # type: ignore[attr-defined]
            market_address,
            QuoteParams(side="BUY", outcome=0, amount=amount_atomic),
        )
    except KashProtocolError as err:
        sys.stderr.write(f"quote failed: {err}\n")
        raise SystemExit(1) from err

    print()
    print("Quote (1 USDC BUY on outcome 0)")
    print("-------------------------------")
    print(f"  amount_in (USDC):  {format_usdc(quote.amount_in)}")
    print(f"  amount_out (tok):  {format_tokens(quote.amount_out)}")
    print(f"  reserve_after:     {format_tokens(quote.reserve_after_wad)}")
    print(f"  prices_after_wad:  {quote.prices_after_wad}")

    return is_deployed


if __name__ == "__main__":
    asyncio.run(main())
