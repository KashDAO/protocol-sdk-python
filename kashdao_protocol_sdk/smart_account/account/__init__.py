"""Smart Account address derivation + init-code helpers.

Mirrors ``src/smart-account/account/``.

Re-exports the public surface for SA address computation
(:func:`compute_smart_account_address`,
:func:`is_smart_account_deployed`) and init-code construction
(:func:`encode_create_account`).
"""

from __future__ import annotations

from kashdao_protocol_sdk.smart_account.account.address import (
    ComputeSmartAccountAddressParams,
    compute_smart_account_address,
    compute_smart_account_address_via_web3,
    is_smart_account_deployed,
)
from kashdao_protocol_sdk.smart_account.account.init_code import encode_create_account

__all__ = [
    "ComputeSmartAccountAddressParams",
    "compute_smart_account_address",
    "compute_smart_account_address_via_web3",
    "encode_create_account",
    "is_smart_account_deployed",
]
