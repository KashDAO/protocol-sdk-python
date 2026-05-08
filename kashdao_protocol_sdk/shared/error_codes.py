"""Stable, machine-readable error codes emitted by ``kashdao-protocol-sdk``.

Mirrors ``src/shared/error-codes.ts``.

Every :class:`KashProtocolError` subclass carries a ``code: str`` field.
This module gives consumers an autocompletable handle on every static
code so the canonical pattern::

    if err.code == "STALE_USEROP_HASH": ...

becomes::

    if err.code == ErrorCode.STALE_USEROP_HASH: ...

— a typo on the LHS surfaces as ``AttributeError`` rather than a
silent always-false branch.

**Stability**: code string values are part of the SDK's compatibility
surface. Additions are non-breaking; renames are. Codes are emitted
verbatim into observability output (logs, metrics, alert routing
keys), so changing one is a contract break for consumers and ops.

**Dynamic codes**: a small number of codes are templated at runtime
with the upstream HTTP status:

* ``BUNDLER_HTTP_${status}`` (e.g. ``BUNDLER_HTTP_502``)
* ``SIGNER_RPC_HTTP_${status}`` (e.g. ``SIGNER_RPC_HTTP_429``)

These are not enumerated here; match on a prefix when needed.
"""

from __future__ import annotations

from typing import Final


class ErrorCode:
    """Namespace of all static :class:`KashProtocolError` codes.

    Use as ``ErrorCode.SIMULATION_REVERTED`` (string-equal to
    ``"SIMULATION_REVERTED"``). Defined as class attributes rather
    than an :class:`enum.Enum` so equality with raw strings remains
    O(1) and no ``.value`` access is needed.
    """

    # -- Approve / allowance ------------------------------------------------
    APPROVE_ACCOUNT_MISMATCH: Final = "APPROVE_ACCOUNT_MISMATCH"
    """EOA-mode build/prepare/send approve called with ``params.account`` that doesn't match the configured signer."""
    BUY_ACCOUNT_MISMATCH: Final = "BUY_ACCOUNT_MISMATCH"
    """EOA-mode build/prepare/send buy called with ``params.account`` that doesn't match the configured signer."""
    SELL_ACCOUNT_MISMATCH: Final = "SELL_ACCOUNT_MISMATCH"
    """EOA-mode build/prepare/send sell called with ``params.account`` that doesn't match the configured signer."""
    CLOSE_ACCOUNT_MISMATCH: Final = "CLOSE_ACCOUNT_MISMATCH"
    """EOA-mode build/prepare/send closePosition called with ``params.account`` that doesn't match the configured signer."""
    USDC_ALLOWANCE_READ_FAILED: Final = "USDC_ALLOWANCE_READ_FAILED"
    """USDC allowance read failed (RPC unreachable, malformed response). Retryable."""

    # -- Auto-deploy --------------------------------------------------------
    AUTO_DEPLOY_OWNER_REQUIRED: Final = "AUTO_DEPLOY_OWNER_REQUIRED"
    """SA ``prepare*`` was given ``auto_deploy=True`` but no ``owner_address``."""

    # -- Account reads ------------------------------------------------------
    BALANCE_READ_FAILED: Final = "BALANCE_READ_FAILED"
    """USDC ``balanceOf`` chain read failed. Retryable."""
    GAS_BALANCE_READ_FAILED: Final = "GAS_BALANCE_READ_FAILED"
    """Native ETH ``getBalance`` chain read failed. Retryable."""
    POSITION_READ_FAILED: Final = "POSITION_READ_FAILED"
    """ERC-1155 ``balanceOfBatch`` chain read failed. Retryable."""

    # -- Bundler ------------------------------------------------------------
    BUNDLER_EMPTY_RESULT: Final = "BUNDLER_EMPTY_RESULT"
    """Bundler returned ``result: undefined`` and no ``error``."""
    BUNDLER_INVALID_ENVELOPE: Final = "BUNDLER_INVALID_ENVELOPE"
    """Bundler response failed JSON-RPC envelope schema validation."""
    BUNDLER_INVALID_JSON: Final = "BUNDLER_INVALID_JSON"
    """Bundler response was not parseable JSON."""
    BUNDLER_NETWORK_ERROR: Final = "BUNDLER_NETWORK_ERROR"
    """Network-level bundler RPC failure (connection refused, DNS, etc.). Retryable."""
    BUNDLER_RECEIPT_TIMEOUT: Final = "BUNDLER_RECEIPT_TIMEOUT"
    """``wait_for_receipt`` exhausted its timeout without inclusion. Retryable."""
    BUNDLER_RPC_ERROR: Final = "BUNDLER_RPC_ERROR"
    """Bundler returned a JSON-RPC ``error`` field. ``context['rpc_code']`` carries the upstream code."""

    # -- Chain config -------------------------------------------------------
    CHAIN_NOT_DEPLOYED: Final = "CHAIN_NOT_DEPLOYED"
    """Configured chain id is in ``KNOWN_CHAIN_IDS`` but the protocol contracts aren't deployed there yet."""
    UNSUPPORTED_CHAIN: Final = "UNSUPPORTED_CHAIN"
    """Configured chain id is not in the static registry and no ``custom_chain`` was supplied."""
    TOKENS_1155_NOT_DEPLOYED: Final = "TOKENS_1155_NOT_DEPLOYED"
    """Tried to read positions on a chain whose ERC-1155 outcome-tokens contract address is not in the registry."""
    CHAIN_ID_MISMATCH: Final = "CHAIN_ID_MISMATCH"
    """``EoaClientConfig.chain_id`` and ``custom_chain.chain_id`` disagree."""

    # -- Close position -----------------------------------------------------
    CLOSE_POSITION_ZERO_BALANCE: Final = "CLOSE_POSITION_ZERO_BALANCE"
    """``close_position`` called for an outcome the account holds zero of."""

    # -- Config / input validation ------------------------------------------
    INVALID_CONFIG: Final = "INVALID_CONFIG"
    """Top-level config parse failure on ``create_*_client``."""
    INVALID_OWNER_ADDRESS: Final = "INVALID_OWNER_ADDRESS"
    """Owner address didn't match the 0x-prefixed 20-byte regex."""
    INVALID_AMOUNT: Final = "INVALID_AMOUNT"
    """``usdc()`` / ``tokens()`` got NaN, infinity, negative, or a non-decimal string."""
    INVALID_FORMAT_DECIMALS: Final = "INVALID_FORMAT_DECIMALS"
    """``format_usdc`` / ``format_tokens`` ``decimals`` was not a non-negative int or ``'all'``."""
    INVALID_FORMAT_ARG: Final = "INVALID_FORMAT_ARG"
    """``format_usdc`` / ``format_tokens`` ``atomic`` argument was not int."""
    INVALID_SLIPPAGE: Final = "INVALID_SLIPPAGE"
    """Slippage out of ``[0, 10_000]`` bps."""
    INVALID_FEE_OPTIONS: Final = "INVALID_FEE_OPTIONS"
    """``estimate_chain_fees`` got an out-of-range ``block_count`` / ``reward_percentile`` / ``base_multiplier``."""
    INVALID_SIGNER: Final = "INVALID_SIGNER"
    """Signer adapter didn't expose the required interface."""
    MISSING_CLIENT_CONFIG: Final = "MISSING_CLIENT_CONFIG"
    """``create_*_client`` called with insufficient args and no config object."""
    MISSING_SIGNER_CONFIG: Final = "MISSING_SIGNER_CONFIG"
    """``json_rpc_*_signer`` called with insufficient args and no config object."""

    # -- Fees ---------------------------------------------------------------
    FEE_HISTORY_EMPTY: Final = "FEE_HISTORY_EMPTY"
    """``eth_feeHistory`` returned an empty ``baseFeePerGas`` array."""
    FEE_HISTORY_FAILED: Final = "FEE_HISTORY_FAILED"
    """``eth_feeHistory`` chain read failed. Retryable."""

    # -- Gas estimation -----------------------------------------------------
    GAS_ESTIMATE_FAILED: Final = "GAS_ESTIMATE_FAILED"
    """``eth_estimateGas`` failed. Retryable."""
    GAS_ESTIMATE_NO_TO: Final = "GAS_ESTIMATE_NO_TO"
    """``eth_estimateGas`` called on a transaction missing the ``to`` field."""

    # -- Markets ------------------------------------------------------------
    MARKET_NO_OUTCOMES: Final = "MARKET_NO_OUTCOMES"
    """Market reported zero outcomes — usually means the address isn't a Market."""
    MARKET_READ_FAILED: Final = "MARKET_READ_FAILED"
    """Market ``cfg`` / ``state`` read failed. Retryable."""
    MARKET_STATE_READ_FAILED: Final = "MARKET_STATE_READ_FAILED"
    """Market state-projection read (multicall) failed. Retryable."""
    MARKET_UNKNOWN_STATE: Final = "MARKET_UNKNOWN_STATE"
    """Market ``state()`` returned a status enum the SDK doesn't know. Indicates ABI drift."""
    QUOTE_FAILED: Final = "QUOTE_FAILED"
    """Quote chain read failed. Retryable."""

    # -- Nonce --------------------------------------------------------------
    NONCE_READ_FAILED: Final = "NONCE_READ_FAILED"
    """EntryPoint ``getNonce`` (SA) or ``getTransactionCount`` (EOA) read failed."""

    # -- Cancellation -------------------------------------------------------
    OPERATION_ABORTED: Final = "OPERATION_ABORTED"
    """Operation aborted by consumer's signal."""

    # -- Smart-account address derivation -----------------------------------
    SA_DERIVATION_FAILED: Final = "SA_DERIVATION_FAILED"
    """Factory ``getAddress`` view call failed. Retryable."""
    SA_DEPLOYMENT_CHECK_FAILED: Final = "SA_DEPLOYMENT_CHECK_FAILED"
    """``get_code`` chain read failed. Retryable."""

    # -- Signer -------------------------------------------------------------
    SIGNER_SIGN_FAILED: Final = "SIGNER_SIGN_FAILED"
    """Signer call threw and the SDK has no narrower context — wraps the underlying cause."""
    SIGNER_HASH_FAILED: Final = "SIGNER_HASH_FAILED"
    """SA ``signMessage`` (raw hash) failed."""
    SIGNER_TX_FAILED: Final = "SIGNER_TX_FAILED"
    """EOA ``signTransaction`` failed."""
    SIGNER_TYPED_DATA_FAILED: Final = "SIGNER_TYPED_DATA_FAILED"
    """SA ``signTypedData`` failed."""
    SIGNER_UNSUPPORTED: Final = "SIGNER_UNSUPPORTED"
    """Underlying signer doesn't implement ``sign_message`` / ``sign_transaction``."""
    SIGNER_TYPED_DATA_UNSUPPORTED: Final = "SIGNER_TYPED_DATA_UNSUPPORTED"
    """Underlying signer doesn't implement ``sign_typed_data``."""
    SIGNER_RPC_NETWORK_ERROR: Final = "SIGNER_RPC_NETWORK_ERROR"
    """JSON-RPC signer network call failed. Retryable."""
    SIGNER_RPC_FAILED: Final = "SIGNER_RPC_FAILED"
    """JSON-RPC signer call failed."""
    SIGNER_RPC_ERROR: Final = "SIGNER_RPC_ERROR"
    """JSON-RPC signer returned a result with a JSON-RPC ``error`` field."""
    SIGNER_RPC_INVALID_ENVELOPE: Final = "SIGNER_RPC_INVALID_ENVELOPE"
    """JSON-RPC signer response failed envelope schema validation."""
    SIGNER_RPC_INVALID_RESULT: Final = "SIGNER_RPC_INVALID_RESULT"
    """JSON-RPC signer returned a result that wasn't 0x-prefixed hex of the expected length."""
    SIGNER_BAD_RESULT: Final = "SIGNER_BAD_RESULT"
    """JSON-RPC signer returned a malformed (non-hex) result."""
    SIGNATURE_MALFORMED: Final = "SIGNATURE_MALFORMED"
    """Submit-side staleness guard recovered an EOA signature it couldn't decode."""
    SIGNED_TX_MALFORMED: Final = "SIGNED_TX_MALFORMED"
    """Submit-side staleness guard couldn't parse the signed serialized EOA tx."""
    STALE_USEROP_HASH: Final = "STALE_USEROP_HASH"
    """SA submit-side: signature recovers to wrong EOA OR was made over a stale UserOp hash."""
    STALE_SIGNED_TX: Final = "STALE_SIGNED_TX"
    """EOA submit-side: signed tx parses but its chain id / signer don't match the configured client."""

    # -- Simulation ---------------------------------------------------------
    SIMULATION_REVERTED: Final = "SIMULATION_REVERTED"
    """Pre-flight ``eth_call`` reported the trade will revert. ``context['revert_reason']`` carries the decoded reason."""

    # -- Submission ---------------------------------------------------------
    TX_SEND_FAILED: Final = "TX_SEND_FAILED"
    """EOA ``eth_sendRawTransaction`` failed."""
    WAIT_RECEIPT_FAILED: Final = "WAIT_RECEIPT_FAILED"
    """Wait for receipt timed out / failed (EOA path). Retryable."""

    # -- Watch --------------------------------------------------------------
    WATCH_HANDLER_FAILED: Final = "WATCH_HANDLER_FAILED"
    """Consumer's ``markets.watch`` ``on_event`` handler raised."""
    WATCH_RPC_ERROR: Final = "WATCH_RPC_ERROR"
    """Watcher emitted an error (transport disconnect, decode failure). Retryable."""
    LOG_INCOMPLETE: Final = "LOG_INCOMPLETE"
    """Watcher dropped a log because identifying / args fields were missing."""
    LOG_DECODE_FAILED: Final = "LOG_DECODE_FAILED"
    """Watcher dropped a log because ABI decoding raised."""


__all__ = ["ErrorCode"]
