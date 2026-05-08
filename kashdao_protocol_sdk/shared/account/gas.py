"""``client.account.gas_balance(address)`` — native ETH balance.

Mirrors ``src/shared/account/gas.ts``. Returns the
account's native (ETH) balance in atomic units (18dp). Use to gate
trade attempts on having enough ETH to cover the next transaction or
UserOp.
"""

from __future__ import annotations

from web3 import AsyncWeb3

from kashdao_protocol_sdk.shared.errors import KashChainError
from kashdao_protocol_sdk.shared.types import Hex


async def get_gas_balance(web3: AsyncWeb3, account: Hex) -> int:
    """Read the native (ETH) balance for ``account`` in atomic units (18dp)."""
    try:
        return int(await web3.eth.get_balance(AsyncWeb3.to_checksum_address(account)))
    except Exception as cause:
        raise KashChainError(
            f"failed to read native balance for {account}",
            code="GAS_BALANCE_READ_FAILED",
            context={"account": account},
            cause=cause,
        ) from cause


__all__ = ["get_gas_balance"]
