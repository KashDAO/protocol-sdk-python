"""USDC allowance helpers — mode-agnostic.

Mirrors ``src/shared/account/allowance.ts``.

Every first-time consumer needs to approve the Market contract (or a
batched executor — depends on the flow) to spend USDC before their
first BUY. Without it, the trade reverts at ``transferFrom``.
"""

from __future__ import annotations

from typing import Final

from eth_abi import encode as abi_encode  # type: ignore[attr-defined]
from eth_utils import keccak, to_bytes  # type: ignore[attr-defined]
from hexbytes import HexBytes
from web3 import AsyncWeb3

from kashdao_protocol_sdk.shared.contracts.abis import ERC20_ABI
from kashdao_protocol_sdk.shared.contracts.addresses import ProtocolAddresses
from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashChainError, KashConfigError
from kashdao_protocol_sdk.shared.types import Hex

#: Common "infinite" allowance — saves the consumer re-approving per trade.
MAX_UINT256: Final[int] = 2**256 - 1

# Pre-computed approve(address,uint256) selector — keccak-256 of the
# canonical signature, first 4 bytes. Cached for hot-path encoding.
_APPROVE_SELECTOR: Final[bytes] = keccak(text="approve(address,uint256)")[:4]


def encode_approve(*, spender: Hex, amount: int) -> Hex:
    """Encode ``ERC20.approve(spender, amount)`` calldata.

    Mode-agnostic — both EOA and SA ``buildApprove*`` builders use this
    internally. Exposed publicly so consumers building bespoke flows
    (e.g. batched approve+trade on a custom executor) can reuse the
    same encoding.

    >>> from kashdao_protocol_sdk import encode_approve, MAX_UINT256
    >>> calldata = encode_approve(spender='0x' + '00' * 20, amount=MAX_UINT256)
    >>> calldata.startswith('0x095ea7b3')
    True
    """
    if amount < 0:
        # Input-validation bug, not a chain failure → KashConfigError.
        # Reuses the ``INVALID_AMOUNT`` code shared with ``usdc()`` /
        # ``tokens()`` since both gate on the same "non-negative
        # integer" invariant.
        raise KashConfigError(
            f"approve amount must be non-negative, got {amount}",
            code=ErrorCode.INVALID_AMOUNT,
            context={"amount": amount},
        )
    encoded_args = abi_encode(["address", "uint256"], [_to_checksum_bytes(spender), amount])
    return HexBytes(_APPROVE_SELECTOR + encoded_args).to_0x_hex()


async def get_usdc_allowance(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    owner_address: Hex,
    spender: Hex,
) -> int:
    """Read the current USDC allowance from ``owner_address`` to ``spender``.

    Use this to skip ``approve`` round-trips when the allowance is
    already sufficient — the conventional first-trade pattern is::

        current = await client.account.usdc_allowance(account, market)
        if current < amount_to_trade:
            await client.trades.send.approve(...)
    """
    contract = web3.eth.contract(
        address=AsyncWeb3.to_checksum_address(addresses.usdc),
        abi=ERC20_ABI,
    )
    try:
        return int(
            await contract.functions.allowance(
                AsyncWeb3.to_checksum_address(owner_address),
                AsyncWeb3.to_checksum_address(spender),
            ).call()
        )
    except Exception as cause:
        raise KashChainError(
            f"failed to read USDC allowance from {owner_address} to {spender}",
            code="USDC_ALLOWANCE_READ_FAILED",
            context={
                "owner_address": owner_address,
                "spender": spender,
                "usdc": addresses.usdc,
            },
            cause=cause,
            is_retryable=True,
        ) from cause


def _to_checksum_bytes(addr: Hex) -> bytes:
    """Coerce a 0x-prefixed address string to 20-byte form for ABI encoding."""
    return to_bytes(hexstr=addr)


__all__ = [
    "MAX_UINT256",
    "encode_approve",
    "get_usdc_allowance",
]
