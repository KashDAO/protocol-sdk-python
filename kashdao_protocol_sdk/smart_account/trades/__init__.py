"""SA-mode trades pipeline — build / hash / prepare / simulate / submit / send.

Mirrors ``src/smart-account/trades/``.

Entry points:

* ``build_*_user_op`` — construct an unsigned UserOp at the lowest layer.
* ``prepare_*_user_op`` — build + estimate + recompute hash (recommended).
* ``simulate_user_op`` — pre-flight ``eth_call`` against the inner Market call.
* ``submit_user_op`` — staleness-guard + bundler forward.
* ``send_*_user_op`` — all-in-one (prepare → sign → submit → optionally wait).
* ``compute_user_op_hash`` / ``user_op_to_typed_data`` — canonical hash helpers.
"""

from __future__ import annotations

from kashdao_protocol_sdk.smart_account.trades.build import (
    PaymasterConfig,
    build_approve_user_op,
    build_buy_user_op,
    build_close_position_user_op,
    build_sell_user_op,
)
from kashdao_protocol_sdk.smart_account.trades.hash import (
    compute_user_op_hash,
    user_op_to_typed_data,
)
from kashdao_protocol_sdk.smart_account.trades.prepare import (
    PrepareUserOpOptions,
    prepare_approve_user_op,
    prepare_buy_user_op,
    prepare_close_position_user_op,
    prepare_sell_user_op,
)
from kashdao_protocol_sdk.smart_account.trades.send import (
    send_approve_user_op,
    send_buy_user_op,
    send_close_position_user_op,
    send_sell_user_op,
)
from kashdao_protocol_sdk.smart_account.trades.simulate import simulate_user_op
from kashdao_protocol_sdk.smart_account.trades.submit import submit_user_op

__all__ = [
    "PaymasterConfig",
    "PrepareUserOpOptions",
    "build_approve_user_op",
    "build_buy_user_op",
    "build_close_position_user_op",
    "build_sell_user_op",
    "compute_user_op_hash",
    "prepare_approve_user_op",
    "prepare_buy_user_op",
    "prepare_close_position_user_op",
    "prepare_sell_user_op",
    "send_approve_user_op",
    "send_buy_user_op",
    "send_close_position_user_op",
    "send_sell_user_op",
    "simulate_user_op",
    "submit_user_op",
    "user_op_to_typed_data",
]
