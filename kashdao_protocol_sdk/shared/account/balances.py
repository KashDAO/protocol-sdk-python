"""``client.account.usdc_balance(address)`` — atomic-units USDC balance.

Mirrors ``src/shared/account/balances.ts``.
"""

from __future__ import annotations

from web3 import AsyncWeb3

from kashdao_protocol_sdk.shared.contracts.abis import ERC20_ABI
from kashdao_protocol_sdk.shared.contracts.addresses import ProtocolAddresses
from kashdao_protocol_sdk.shared.errors import KashChainError
from kashdao_protocol_sdk.shared.types import Hex


async def get_usdc_balance(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    account: Hex,
) -> int:
    """Read the USDC balance for ``account`` in atomic units (6dp).

    Multiply by ``10**12`` to compare with WAD-precision values.
    """
    contract = web3.eth.contract(
        address=AsyncWeb3.to_checksum_address(addresses.usdc),
        abi=ERC20_ABI,
    )
    try:
        return int(
            await contract.functions.balanceOf(AsyncWeb3.to_checksum_address(account)).call()
        )
    except Exception as cause:
        raise KashChainError(
            f"failed to read USDC balance for {account}",
            code="BALANCE_READ_FAILED",
            context={"account": account, "usdc": addresses.usdc},
            cause=cause,
        ) from cause


__all__ = ["get_usdc_balance"]
