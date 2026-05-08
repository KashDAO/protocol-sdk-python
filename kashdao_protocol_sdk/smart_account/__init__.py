"""Smart Account mode — ERC-4337 v0.7 via SimpleAccount + EntryPoint.

Full surface — :func:`create_smart_account_client` exposes the same
``markets.*`` / ``account.*`` / ``trades.*`` shape as the EOA client,
plus a ``client.bundler`` namespace for direct bundler-RPC access:

* ``client.markets.*`` — chain reads (get / state / quote / watch).
* ``client.account.*`` — USDC / position / SA-address derivation.
* ``client.trades.{build_*, prepare_*, simulate, submit, send.*}``.
* ``client.bundler.*`` — JSON-RPC over the configured bundler.

For users on AA-style wallet stacks (Privy embedded wallets, Coinbase
Smart Wallet, Pimlico, Alchemy AA): supply a
:class:`SmartAccountSignerAdapter` (or use the reference
:class:`LocalSigner` / :class:`JsonRpcSigner`) plus a bundler URL or
:class:`BundlerOptions` preset. Same lifecycle pattern as the EOA
client (``async with`` or explicit ``aclose``).
"""

from kashdao_protocol_sdk.smart_account.account import (
    ComputeSmartAccountAddressParams,
    compute_smart_account_address,
    compute_smart_account_address_via_web3,
    encode_create_account,
    is_smart_account_deployed,
)
from kashdao_protocol_sdk.smart_account.bundler import (
    AlchemyBundlerConfig,
    BundlerCallOptions,
    BundlerClient,
    BundlerClientConfig,
    FlashbotsBundlerConfig,
    PimlicoBundlerConfig,
    create_alchemy_bundler_client,
    create_flashbots_bundler_client,
    create_generic_bundler_client,
    create_pimlico_bundler_client,
)
from kashdao_protocol_sdk.smart_account.client import (
    SmartAccountAccount,
    SmartAccountClient,
    SmartAccountMarkets,
    SmartAccountTrades,
    SmartAccountTradesSend,
    create_smart_account_client,
)
from kashdao_protocol_sdk.smart_account.config import (
    BundlerOptions,
    SmartAccountClientConfig,
    SmartAccountClientConfigInput,
    parse_smart_account_client_config,
)
from kashdao_protocol_sdk.smart_account.signers import (
    JsonRpcSigner,
    JsonRpcSignerConfig,
    LocalSigner,
    json_rpc_signer,
    viem_account_signer,
)
from kashdao_protocol_sdk.smart_account.trades import (
    PaymasterConfig,
    PrepareUserOpOptions,
    build_approve_user_op,
    build_buy_user_op,
    build_close_position_user_op,
    build_sell_user_op,
    compute_user_op_hash,
    prepare_approve_user_op,
    prepare_buy_user_op,
    prepare_close_position_user_op,
    prepare_sell_user_op,
    send_approve_user_op,
    send_buy_user_op,
    send_close_position_user_op,
    send_sell_user_op,
    simulate_user_op,
    submit_user_op,
    user_op_to_typed_data,
)
from kashdao_protocol_sdk.smart_account.types import (
    BuildOptions,
    BuiltUserOp,
    BundlerHealth,
    BundlerHealthError,
    BundlerHealthOk,
    GasEstimate,
    GasOverrides,
    PrepareFeeOverrides,
    SendResult,
    SendResultFireAndForget,
    SendResultWaited,
    SignedUserOp,
    SignerAdapter,
    SmartAccountSignerAdapter,
    SubmitOptions,
    SubmitResult,
    UnsignedUserOp,
    UserOpReceipt,
    UserOpTypedData,
)

__all__ = [
    "AlchemyBundlerConfig",
    "BuildOptions",
    "BundlerOptions",
    "ComputeSmartAccountAddressParams",
    "JsonRpcSigner",
    "JsonRpcSignerConfig",
    "LocalSigner",
    "PaymasterConfig",
    "PrepareUserOpOptions",
    "SmartAccountAccount",
    "SmartAccountClientConfig",
    "SmartAccountClientConfigInput",
    "SmartAccountMarkets",
    "SmartAccountTrades",
    "SmartAccountTradesSend",
    "build_approve_user_op",
    "build_buy_user_op",
    "build_close_position_user_op",
    "build_sell_user_op",
    "compute_user_op_hash",
    "json_rpc_signer",
    "parse_smart_account_client_config",
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
    "viem_account_signer",
    "BuiltUserOp",
    "BundlerCallOptions",
    "BundlerClient",
    "BundlerClientConfig",
    "BundlerHealth",
    "BundlerHealthError",
    "BundlerHealthOk",
    "FlashbotsBundlerConfig",
    "GasEstimate",
    "GasOverrides",
    "PimlicoBundlerConfig",
    "PrepareFeeOverrides",
    "SendResult",
    "SendResultFireAndForget",
    "SendResultWaited",
    "SignedUserOp",
    "SignerAdapter",
    "SmartAccountClient",
    "SmartAccountSignerAdapter",
    "SubmitOptions",
    "SubmitResult",
    "UnsignedUserOp",
    "UserOpReceipt",
    "UserOpTypedData",
    "compute_smart_account_address",
    "compute_smart_account_address_via_web3",
    "create_alchemy_bundler_client",
    "create_flashbots_bundler_client",
    "create_generic_bundler_client",
    "create_pimlico_bundler_client",
    "create_smart_account_client",
    "encode_create_account",
    "is_smart_account_deployed",
]
