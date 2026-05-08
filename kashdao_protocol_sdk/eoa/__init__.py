"""EOA mode — vanilla EIP-1559 trading from a plain externally-owned account.

The canonical Hummingbot integration path: bring your own private key
or signer adapter, an RPC URL, and a USDC-funded EOA. The library
encodes trade calldata, builds an EIP-1559 transaction, and (after the
consumer signs) submits via ``eth_sendRawTransaction``. No bundler, no
SimpleAccount, no ERC-4337 overhead.
"""

from kashdao_protocol_sdk.eoa.client import (
    EoaAccount,
    EoaClient,
    EoaMarkets,
    EoaTrades,
    EoaTradesSend,
    create_eoa_client,
)
from kashdao_protocol_sdk.eoa.config import (
    EoaClientConfig,
    EoaClientConfigInput,
    parse_eoa_client_config,
)
from kashdao_protocol_sdk.eoa.signers import (
    JsonRpcEoaSigner,
    JsonRpcEoaSignerConfig,
    LocalEoaSigner,
    json_rpc_eoa_signer,
    viem_account_eoa_signer,
)
from kashdao_protocol_sdk.eoa.trades import (
    build_approve_transaction,
    build_buy_transaction,
    build_close_position_transaction,
    build_sell_transaction,
    compute_transaction_hash,
    prepare_approve_transaction,
    prepare_buy_transaction,
    prepare_close_position_transaction,
    prepare_sell_transaction,
    send_approve_transaction,
    send_buy_transaction,
    send_close_position_transaction,
    send_sell_transaction,
    simulate_transaction,
    submit_transaction,
)
from kashdao_protocol_sdk.eoa.types import (
    BuiltTransaction,
    EoaSignerAdapter,
    EoaSubmitOptions,
    EoaSubmitResult,
    EoaTxOverrides,
    PrepareEoaFeeOverrides,
    PrepareEoaOptions,
    SendEoaOptions,
    SendEoaResult,
    SendEoaResultFireAndForget,
    SendEoaResultWaited,
    UnsignedTransaction,
)

__all__ = [
    # Types
    "BuiltTransaction",
    "EoaAccount",
    "EoaClient",
    "EoaClientConfig",
    "EoaClientConfigInput",
    "EoaMarkets",
    "EoaSignerAdapter",
    "EoaSubmitOptions",
    "EoaSubmitResult",
    "EoaTrades",
    "EoaTradesSend",
    "EoaTxOverrides",
    "JsonRpcEoaSigner",
    "JsonRpcEoaSignerConfig",
    "LocalEoaSigner",
    "PrepareEoaFeeOverrides",
    "PrepareEoaOptions",
    "SendEoaOptions",
    "SendEoaResult",
    "SendEoaResultFireAndForget",
    "SendEoaResultWaited",
    "UnsignedTransaction",
    # Factory + signer helpers
    "create_eoa_client",
    "json_rpc_eoa_signer",
    "parse_eoa_client_config",
    "viem_account_eoa_signer",
    # Lifecycle functions
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
