"""Mode-agnostic account reads — USDC balance + allowance, native gas balance, position holdings.

Used identically by :mod:`kashdao_protocol_sdk.eoa` and
:mod:`kashdao_protocol_sdk.smart_account` clients.
"""

from kashdao_protocol_sdk.shared.account.allowance import (
    MAX_UINT256,
    encode_approve,
    get_usdc_allowance,
)
from kashdao_protocol_sdk.shared.account.balances import get_usdc_balance
from kashdao_protocol_sdk.shared.account.gas import get_gas_balance
from kashdao_protocol_sdk.shared.account.positions import (
    get_position,
    token_id_for,
)

__all__ = [
    "MAX_UINT256",
    "encode_approve",
    "get_gas_balance",
    "get_position",
    "get_usdc_allowance",
    "get_usdc_balance",
    "token_id_for",
]
