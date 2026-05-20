"""EOA mode — Example 03: Error handling.

The SDK emits a typed error hierarchy rooted at
:class:`KashProtocolError`. Each error has a stable ``code`` (mirrors
:class:`ErrorCode` in the TS SDK), a structured ``context`` dict, and
``is_retryable`` / ``is_operational`` flags.

This example demonstrates the canonical exception-handling pattern:

- :class:`KashConfigError` for misconfiguration (invalid chain id,
  bad RPC URL, missing signer fields).
- :class:`KashChainError` for chain/RPC failures (RPC unavailable,
  reorg-suspected receipt loss).
- :class:`KashSimulationRevertedError` for trades that would revert
  (slippage exceeded, insufficient allowance, etc.). Includes a
  decoded revert hint when available.
- :class:`KashSignerError` for signer-side failures (always
  ``is_retryable=False`` — re-prompting MFA on a retry is a footgun).
- :class:`KashAbortedError` when a caller's ``signal`` fires.

Run::

    KASH_PRIVATE_KEY=0x... \\
    KASH_MARKET=0x... \\
    python examples/eoa/03_error_handling.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from eth_account import Account

from kashdao_protocol_sdk import (
    BuildBuyParams,
    KashAbortedError,
    KashChainError,
    KashConfigError,
    KashSignerError,
    KashSimulationRevertedError,
    create_eoa_client,
    usdc,
    viem_account_eoa_signer,
)


async def main() -> None:
    pk = os.environ.get("KASH_PRIVATE_KEY")
    market = os.environ.get("KASH_MARKET")
    if not pk or not market:
        sys.stderr.write("KASH_PRIVATE_KEY and KASH_MARKET are required.\n")
        sys.exit(2)

    account = Account.from_key(pk)
    rpc = os.environ.get("KASH_BASE_RPC_URL", "https://sepolia.base.org")

    try:
        async with create_eoa_client(
            chain_id=84532,
            rpc=rpc,
            signer=viem_account_eoa_signer(account),
        ) as client:
            # Force a simulation failure by demanding zero slippage on a
            # decently-sized trade. The revert decoder will surface the
            # exact protocol error.
            await client.trades.send.buy(
                market,
                BuildBuyParams(
                    # `account` is the mode-polymorphic field name (TS
                    # parity). In EOA mode pass the EOA address; in SA
                    # mode pass the SimpleAccount address.
                    account=client.signer.owner_address,
                    outcome=0,
                    amount_usdc=usdc(1000),
                    max_slippage_bps=0,  # impossibly tight
                ),
            )
    except KashConfigError as e:
        # Misconfiguration. Never retryable. Fix code / config.
        print(f"config error: {e.code} — {e}")
        print(f"  context: {e.context}")
    except KashSimulationRevertedError as e:
        # The trade would revert if submitted. Includes a decoded hint
        # like 'SlippageExceeded' or 'InsufficientAllowance'.
        print(f"simulation reverted: {e.revert_hint or e}")
        print(f"  retryable: {e.is_retryable}")
    except KashSignerError as e:
        # Signer-side failure. NOT retryable — re-prompting a signer
        # (especially MFA) on retry is a security footgun.
        print(f"signer error: {e}")
    except KashChainError as e:
        # Chain/RPC-side failure. May be retryable.
        print(f"chain error: {e.code} — {e}")
        print(f"  retryable: {e.is_retryable}")
    except KashAbortedError as e:
        # Caller's signal fired. Always non-retryable from the SDK's
        # perspective — the abort was deliberate.
        print(f"aborted: {e}")


if __name__ == "__main__":
    asyncio.run(main())
