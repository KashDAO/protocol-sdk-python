"""EOA-mode trade lifecycle.

Order of typical use:

1. ``build_*_transaction`` — encode calldata, fetch nonce.
2. ``prepare_*_transaction`` — build + estimate gas + estimate fees +
   recompute hash (handles the build-time-staleness bug).
3. ``signer.sign_transaction(transaction)`` — produce signed serialized hex.
4. ``submit_transaction`` — staleness guard + ``eth_sendRawTransaction``.

Or use ``send_*_transaction`` for all-in-one prepare → simulate →
sign → submit → wait.
"""

from kashdao_protocol_sdk.eoa.trades.build import (
    build_approve_transaction,
    build_buy_transaction,
    build_close_position_transaction,
    build_sell_transaction,
)
from kashdao_protocol_sdk.eoa.trades.hash import compute_transaction_hash
from kashdao_protocol_sdk.eoa.trades.prepare import (
    prepare_approve_transaction,
    prepare_buy_transaction,
    prepare_close_position_transaction,
    prepare_sell_transaction,
)
from kashdao_protocol_sdk.eoa.trades.send import (
    send_approve_transaction,
    send_buy_transaction,
    send_close_position_transaction,
    send_sell_transaction,
)
from kashdao_protocol_sdk.eoa.trades.simulate import simulate_transaction
from kashdao_protocol_sdk.eoa.trades.submit import submit_transaction

__all__ = [
    "build_approve_transaction",
    "build_buy_transaction",
    "build_close_position_transaction",
    "build_sell_transaction",
    "compute_transaction_hash",
    "prepare_approve_transaction",
    "prepare_buy_transaction",
    "prepare_close_position_transaction",
    "prepare_sell_transaction",
    "send_approve_transaction",
    "send_buy_transaction",
    "send_close_position_transaction",
    "send_sell_transaction",
    "simulate_transaction",
    "submit_transaction",
]
