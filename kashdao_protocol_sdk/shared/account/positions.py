"""``client.account.position(account, market_address)`` — outcome-token holdings.

Mirrors ``src/shared/account/positions.ts``.

Reads ERC-1155 outcome-token balances for an account in a single
market via ``balanceOfBatch``. The token id encoding is
``(market_id << 8) | outcome_index``, matching the on-chain
``OutcomeTokens1155`` contract.
"""

from __future__ import annotations

from web3 import AsyncWeb3

from kashdao_protocol_sdk.shared.contracts.abis import (
    ERC1155_OUTCOME_TOKENS_ABI,
    MARKET_ABI,
)
from kashdao_protocol_sdk.shared.contracts.addresses import ProtocolAddresses
from kashdao_protocol_sdk.shared.errors import KashChainError, KashProtocolError
from kashdao_protocol_sdk.shared.types import Hex, OutcomePosition, Position


def token_id_for(market_id: int, outcome_index: int) -> int:
    """Compute the ERC-1155 token id for ``(market_id, outcome_index)``.

    Mirrors the on-chain encoding in ``OutcomeTokens1155``:
    ``(market_id << 8) | (outcome_index & 0xff)``.

    >>> token_id_for(7, 0)
    1792
    >>> token_id_for(7, 1)
    1793
    """
    return (market_id << 8) | (outcome_index & 0xFF)


async def get_position(
    web3: AsyncWeb3,
    addresses: ProtocolAddresses,
    account: Hex,
    market_address: Hex,
) -> Position:
    """Read all outcome-token balances for ``account`` in the given market."""
    if not addresses.tokens1155:
        raise KashChainError(
            f"chain {addresses.chain_id} does not have an ERC-1155 outcome-tokens deployment",
            code="TOKENS_1155_NOT_DEPLOYED",
            context={"chain_id": addresses.chain_id},
        )
    market = web3.eth.contract(
        address=AsyncWeb3.to_checksum_address(market_address),
        abi=MARKET_ABI,
    )
    try:
        market_id = int(await market.functions.marketId().call())
        cfg = await market.functions.cfg().call()
    except Exception as cause:
        raise KashChainError(
            f"failed to read market metadata for {market_address}",
            code="POSITION_READ_FAILED",
            context={"account": account, "market_address": market_address},
            cause=cause,
        ) from cause

    # cfg is a Solidity struct returned positionally; first slot is uint8 numOutcomes.
    num_outcomes = int(cfg[0])
    indexes = list(range(num_outcomes))
    ids = [token_id_for(market_id, i) for i in indexes]
    accounts = [AsyncWeb3.to_checksum_address(account)] * num_outcomes

    tokens = web3.eth.contract(
        address=AsyncWeb3.to_checksum_address(addresses.tokens1155),
        abi=ERC1155_OUTCOME_TOKENS_ABI,
    )
    try:
        balances_raw = await tokens.functions.balanceOfBatch(accounts, ids).call()
    except Exception as cause:
        if KashProtocolError.is_(cause):
            raise
        raise KashChainError(
            f"failed to read position for {account} on {market_address}",
            code="POSITION_READ_FAILED",
            context={"account": account, "market_address": market_address},
            cause=cause,
        ) from cause
    balances = [int(b) for b in balances_raw]

    holdings = tuple(
        OutcomePosition(outcome_index=i, balance_wad=balances[i] if i < len(balances) else 0)
        for i in indexes
    )
    by_outcome = {h.outcome_index: h for h in holdings}
    return Position(
        market_address=market_address,
        num_outcomes=num_outcomes,
        holdings=holdings,
        by_outcome=by_outcome,
    )


__all__ = [
    "get_position",
    "token_id_for",
]
