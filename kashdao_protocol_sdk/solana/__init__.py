"""``kashdao_protocol_sdk.solana`` — the Kash market program on Solana.

Mirrors the TypeScript ``@kashdao/protocol-sdk/solana`` subpath. A separate
subpackage installed through an optional extra, so EVM-only consumers never
import solders::

    pip install 'kashdao-protocol-sdk[solana]'

.. code-block:: python

    from solders.keypair import Keypair
    from kashdao_protocol_sdk.solana import create_solana_client, keypair_signer

    async with create_solana_client(rpc_url=RPC_URL) as kash:
        signer = keypair_signer(Keypair.from_bytes(SECRET))
        result = await kash.trades.buy(
            market=12, outcome=0, amount_usdc=5_000_000, max_slippage_bps=100, signer=signer
        )

Amounts are ``int``: USDC in atomic units (6dp), outcome tokens in WAD (18dp).
"""

from __future__ import annotations

try:
    import solders as _solders  # noqa: F401 — presence check for the optional extra
except ImportError as exc:  # pragma: no cover — exercised only without the extra
    raise ImportError(
        "kashdao_protocol_sdk.solana needs the optional Solana dependencies: "
        "pip install 'kashdao-protocol-sdk[solana]'"
    ) from exc

from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import (
    KashChainError,
    KashConfigError,
    KashProtocolError,
    KashSignerError,
    KashSimulationRevertedError,
    KashTransactionExpiredError,
    KashTransactionOutcomeUnknownError,
    KashValidationError,
)
from kashdao_protocol_sdk.solana.client import (
    DEFAULT_TRADE_DEADLINE_SECONDS,
    SolanaAccount,
    SolanaClient,
    SolanaMarkets,
    SolanaProtocol,
    SolanaTradeResult,
    SolanaTrades,
    create_solana_client,
)
from kashdao_protocol_sdk.solana.clusters import (
    DEFAULT_SOLANA_CLUSTER,
    SOLANA_CLUSTERS,
    SOLANA_PROGRAM_IDENTITIES,
    CustomSolanaDeployment,
    SolanaCluster,
    SolanaDeployment,
    get_solana_deployment,
    is_solana_cluster,
)
from kashdao_protocol_sdk.solana.connection import (
    AccountInfo,
    Commitment,
    Confirmation,
    LatestBlockhash,
    PreflightFailure,
    SimulationValue,
    SolanaConnection,
    SolanaRpcConnection,
    SolanaRpcError,
    SolanaRpcTransportError,
)
from kashdao_protocol_sdk.solana.instructions import (
    InstructionContext,
    MarketTarget,
    buy_instruction,
    close_position_instruction,
    ensure_usdc_ata_instruction,
    open_position_instruction,
    redeem_instruction,
    sell_instruction,
    usdc_ata,
)
from kashdao_protocol_sdk.solana.pda import KashMarketPdas, SolanaPda, kash_market_pdas
from kashdao_protocol_sdk.solana.signer import KeypairSigner, SolanaSigner, keypair_signer
from kashdao_protocol_sdk.solana.transaction import (
    SolanaComputeBudget,
    SolanaProgramError,
    SolanaSendResult,
    SolanaSimulationResult,
    decode_program_error,
)
from kashdao_protocol_sdk.solana.types import (
    SolanaBuyPlan,
    SolanaBuyQuote,
    SolanaClosePositionPlan,
    SolanaMarket,
    SolanaMarketRef,
    SolanaMarketStatus,
    SolanaOpenPositionPlan,
    SolanaPlan,
    SolanaPosition,
    SolanaProgramConfig,
    SolanaRedeemPlan,
    SolanaRedeemQuote,
    SolanaSellPlan,
    SolanaSellQuote,
    SolanaTemplate,
)

__all__ = [
    # client
    "DEFAULT_TRADE_DEADLINE_SECONDS",
    "SolanaAccount",
    "SolanaClient",
    "SolanaMarkets",
    "SolanaProtocol",
    "SolanaTradeResult",
    "SolanaTrades",
    "create_solana_client",
    # clusters
    "DEFAULT_SOLANA_CLUSTER",
    "SOLANA_CLUSTERS",
    "SOLANA_PROGRAM_IDENTITIES",
    "CustomSolanaDeployment",
    "SolanaCluster",
    "SolanaDeployment",
    "get_solana_deployment",
    "is_solana_cluster",
    # connection
    "AccountInfo",
    "Commitment",
    "Confirmation",
    "LatestBlockhash",
    "PreflightFailure",
    "SimulationValue",
    "SolanaConnection",
    "SolanaRpcConnection",
    "SolanaRpcError",
    "SolanaRpcTransportError",
    # pdas
    "KashMarketPdas",
    "SolanaPda",
    "kash_market_pdas",
    # instructions
    "InstructionContext",
    "MarketTarget",
    "buy_instruction",
    "close_position_instruction",
    "ensure_usdc_ata_instruction",
    "open_position_instruction",
    "redeem_instruction",
    "sell_instruction",
    "usdc_ata",
    # signers
    "KeypairSigner",
    "SolanaSigner",
    "keypair_signer",
    # transactions
    "SolanaComputeBudget",
    "SolanaProgramError",
    "SolanaSendResult",
    "SolanaSimulationResult",
    "decode_program_error",
    # types
    "SolanaBuyPlan",
    "SolanaBuyQuote",
    "SolanaMarket",
    "SolanaMarketRef",
    "SolanaMarketStatus",
    "SolanaPlan",
    "SolanaPosition",
    "SolanaClosePositionPlan",
    "SolanaOpenPositionPlan",
    "SolanaProgramConfig",
    "SolanaRedeemPlan",
    "SolanaRedeemQuote",
    "SolanaSellPlan",
    "SolanaSellQuote",
    "SolanaTemplate",
    # errors, re-exported so a Solana-only consumer can branch without the EVM barrel
    "ErrorCode",
    "KashChainError",
    "KashConfigError",
    "KashProtocolError",
    "KashSignerError",
    "KashSimulationRevertedError",
    "KashTransactionExpiredError",
    "KashTransactionOutcomeUnknownError",
    "KashValidationError",
]
