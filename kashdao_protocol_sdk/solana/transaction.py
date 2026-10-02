"""Turn instructions into a signed, sent and confirmed v0 transaction.

Mirrors ``src/solana/transaction.ts``. Every failure along the way becomes
a typed error that says which stage failed, because the stages need
opposite reactions:

=====================================  ===========================================  ======
stage                                  error                                        retry?
=====================================  ===========================================  ======
preflight simulation refused           ``KashSimulationRevertedError``              no
refused before forwarding (*)          ``KashChainError`` ``TX_SEND_FAILED``        yes
no definite answer (see below)         ``KashTransactionOutcomeUnknownError``       NO
expired, pinned status read empty      ``KashTransactionExpiredError``              yes
landed and FAILED on chain             ``KashChainError`` ``TX_REVERTED``           no
confirmation could not be read         ``KashTransactionOutcomeUnknownError``       NO
=====================================  ===========================================  ======

Every error raised after signing carries ``context['signature']``, computed
from the signed transaction BEFORE it is sent. ``WAIT_RECEIPT_FAILED`` means
the transaction MAY have landed: look the signature up before doing anything
else. It is deliberately not retryable — rebuilding takes a fresh blockhash,
which makes a NEW transaction, and sending both can double a trade.

(*) Only JSON-RPC answers that prove the transaction was never forwarded are
refusals: -32003 (signature verification), -32602 (invalid params) and a
-32002 preflight failure whose ``data.err`` is ``BlockhashNotFound``. Any other
-32002 is a preflight revert (``KashSimulationRevertedError``, decoded from
``data.err`` even with empty logs), and ``data.err == "AlreadyProcessed"``
means the transaction is ALREADY ON CHAIN and goes on to confirmation. Every
other outcome — a timeout, a reset, any HTTP error status (429 included), an
unreadable body, any other JSON-RPC code (-32603, -32005, vendor codes) — is
"may have landed". A program error is decoded against the vendored
``kash_market`` IDL
(``context['program_error']``, e.g. ``SlippageExceeded``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.instruction import Instruction
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import (
    KashChainError,
    KashProtocolError,
    KashSignerError,
    KashSimulationRevertedError,
    KashTransactionExpiredError,
    KashTransactionOutcomeUnknownError,
    KashValidationError,
)
from kashdao_protocol_sdk.solana.connection import (
    Commitment,
    LatestBlockhash,
    PreflightFailure,
    SolanaConnection,
    SolanaRpcError,
)
from kashdao_protocol_sdk.solana.idl import program_error_names
from kashdao_protocol_sdk.solana.signer import SolanaSigner

_FAILED_LOG: Final = re.compile(r"^Program (\w+) failed: custom program error: 0x([0-9a-fA-F]+)$")


@dataclass(frozen=True, slots=True)
class SolanaComputeBudget:
    """Priority-fee and compute-budget knobs, prepended as ComputeBudget instructions."""

    #: Compute-unit ceiling for the transaction.
    compute_unit_limit: int | None = None
    #: Priority fee, micro-lamports per compute unit.
    compute_unit_price_micro_lamports: int | None = None


@dataclass(frozen=True, slots=True)
class SolanaSendResult:
    signature: str
    #: Slot the confirmation was observed at.
    slot: int


@dataclass(frozen=True, slots=True)
class SolanaProgramError:
    """A program failure, decoded where possible against the ``kash_market`` IDL."""

    #: Custom error number (``6000+`` for ``kash_market``), when the failure carried one.
    code: int | None
    #: The IDL's name for ``code`` — only when the failing program IS ``kash_market``.
    name: str | None
    instruction_index: int | None
    #: The raw RPC error, for anything the decode does not cover.
    raw: object | None


@dataclass(frozen=True, slots=True)
class SolanaSimulationResult:
    #: ``None`` when the simulation succeeded.
    error: SolanaProgramError | None
    logs: tuple[str, ...]
    units_consumed: int | None


def decode_program_error(
    program_id: Pubkey,
    *,
    err: object | None = None,
    logs: Sequence[str] | None = None,
    instruction_programs: Sequence[Pubkey] | None = None,
) -> SolanaProgramError:
    """Decode a transaction error (the RPC's JSON ``err``) and/or its logs.

    The name is attached only when the failing program is ``program_id``:
    custom error numbers are per program, so ``6001`` from the token program
    is not ``Paused``.
    """
    code: int | None = None
    instruction_index: int | None = None
    failing_program: str | None = None

    if isinstance(err, dict):
        detail_pair = err.get("InstructionError")
        if isinstance(detail_pair, (list, tuple)) and len(detail_pair) == 2:
            index, detail = detail_pair
            if isinstance(index, int):
                instruction_index = index
                if instruction_programs is not None and 0 <= index < len(instruction_programs):
                    failing_program = str(instruction_programs[index])
            if isinstance(detail, dict) and isinstance(detail.get("Custom"), int):
                code = detail["Custom"]
    for line in logs or ():
        match = _FAILED_LOG.match(line.strip())
        if match:
            failing_program = match.group(1)
            code = int(match.group(2), 16)
    name = (
        program_error_names().get(code)
        if code is not None and failing_program == str(program_id)
        else None
    )
    return SolanaProgramError(code=code, name=name, instruction_index=instruction_index, raw=err)


def _budget_instructions(budget: SolanaComputeBudget) -> list[Instruction]:
    out: list[Instruction] = []
    if budget.compute_unit_limit is not None:
        out.append(set_compute_unit_limit(budget.compute_unit_limit))
    if budget.compute_unit_price_micro_lamports is not None:
        out.append(set_compute_unit_price(budget.compute_unit_price_micro_lamports))
    return out


def _require_signers(instructions: Sequence[Instruction], available: Sequence[Pubkey]) -> None:
    """Refuse before signing if an instruction needs a signature nobody here can give."""
    for ix in instructions:
        for meta in ix.accounts:
            if meta.is_signer and meta.pubkey not in available:
                raise KashValidationError(
                    f"instruction requires {meta.pubkey} to sign, and no signer provided is that key",
                    field="signer",
                    value=str(meta.pubkey),
                    metadata={"available": [str(a) for a in available]},
                )


@dataclass(frozen=True, slots=True)
class _Built:
    message: MessageV0
    latest: LatestBlockhash
    instruction_programs: tuple[Pubkey, ...]


async def _compile(
    connection: SolanaConnection,
    instructions: Sequence[Instruction],
    payer: Pubkey,
    budget: SolanaComputeBudget,
    commitment: Commitment,
) -> _Built:
    if not instructions:
        raise KashValidationError("no instructions to send", field="instructions")
    try:
        latest = await connection.get_latest_blockhash(commitment)
    except Exception as cause:
        raise KashChainError(
            "Failed to fetch a recent blockhash",
            code=ErrorCode.TX_SEND_FAILED,
            cause=cause,
            is_retryable=True,
        ) from cause
    every = [*_budget_instructions(budget), *instructions]
    message = MessageV0.try_compile(payer, every, [], latest.blockhash)
    return _Built(
        message=message,
        latest=latest,
        instruction_programs=tuple(ix.program_id for ix in every),
    )


async def _sign(message: MessageV0, signers: Sequence[SolanaSigner]) -> VersionedTransaction:
    by_key = {s.pubkey: s for s in signers}
    payload = to_bytes_versioned(message)
    required = message.account_keys[: message.header.num_required_signatures]
    signatures: list[Signature] = []
    for key in required:
        signer = by_key[key]
        try:
            signatures.append(await signer.sign_message(payload))
        except KashProtocolError:
            raise
        except Exception as cause:
            raise KashSignerError(
                f"signer {key} failed to sign",
                code=ErrorCode.SIGNER_SIGN_FAILED,
                context={"signer": str(key)},
                cause=cause,
            ) from cause
    return VersionedTransaction.populate(message, signatures)


def _outcome_unknown(
    signature: str, why: str, cause: BaseException
) -> KashTransactionOutcomeUnknownError:
    """The transaction may have landed: never retryable, always carries the signature."""
    return KashTransactionOutcomeUnknownError(
        f"Transaction {signature} may have landed ({why}); look the signature up "
        "before resending — a rebuilt transaction is a new trade",
        signature=signature,
        cause=cause,
    )


#: JSON-RPC codes on ``sendTransaction`` that prove the transaction was NOT
#: forwarded (besides a -32002 preflight failure, handled on its own because
#: its ``data.err`` may say ``AlreadyProcessed``). Every other code — internal
#: error, node unhealthy, any vendor code — proves nothing.
_PRE_FORWARD_REFUSALS: Final[dict[int, str]] = {
    -32003: "the RPC rejected the transaction signature",
    -32602: "the RPC rejected the request parameters",
}


def _raise_for_send_failure(
    cause: Exception,
    signature: str,
    program_id: Pubkey,
    instruction_programs: Sequence[Pubkey],
) -> None:
    """Classify a failed send. Returns only when the transaction is already on chain."""
    if isinstance(cause, PreflightFailure):
        # data.err FIRST: AlreadyProcessed means this exact transaction landed.
        if cause.err == "AlreadyProcessed":
            return
        if cause.err == "BlockhashNotFound":
            raise KashChainError(
                "The RPC does not know the blockhash (preflight BlockhashNotFound); "
                "the transaction was not forwarded",
                code=ErrorCode.TX_SEND_FAILED,
                context={"signature": signature, "rpc_code": cause.code, "err": cause.err},
                cause=cause,
                is_retryable=True,
            ) from cause
        program_error = decode_program_error(
            program_id, err=cause.err, logs=cause.logs, instruction_programs=instruction_programs
        )
        suffix = f": {program_error.name}" if program_error.name else ""
        raise KashSimulationRevertedError(
            f"Preflight simulation refused the transaction{suffix}",
            code=ErrorCode.SIMULATION_REVERTED,
            context={
                "signature": signature,
                "program_error": program_error.name,
                "program_error_code": program_error.code,
                "err": cause.err,
                "logs": cause.logs,
            },
            cause=cause,
        ) from cause
    if (
        isinstance(cause, SolanaRpcError)
        and cause.http_status is None
        and cause.code in _PRE_FORWARD_REFUSALS
    ):
        raise KashChainError(
            f"{_PRE_FORWARD_REFUSALS[cause.code]}; the transaction was not forwarded",
            code=ErrorCode.TX_SEND_FAILED,
            context={"signature": signature, "rpc_code": cause.code},
            cause=cause,
            is_retryable=True,
        ) from cause
    # A timeout, a reset, ANY HTTP error status (429 included), an unreadable
    # answer, or a JSON-RPC code that does not prove the transaction was never
    # forwarded: the RPC may already have forwarded it.
    raise _outcome_unknown(signature, "the send did not return a definite answer", cause) from cause


async def send_instructions(
    connection: SolanaConnection,
    program_id: Pubkey,
    instructions: Sequence[Instruction],
    *,
    signer: SolanaSigner,
    fee_payer: SolanaSigner | None,
    budget: SolanaComputeBudget,
    skip_preflight: bool,
    commitment: Commitment,
) -> SolanaSendResult:
    """Compile, sign, send and confirm."""
    candidates = [fee_payer, signer] if fee_payer is not None else [signer]
    unique: list[SolanaSigner] = []
    for s in candidates:
        if all(s.pubkey != o.pubkey for o in unique):
            unique.append(s)
    _require_signers(instructions, [s.pubkey for s in unique])
    payer = (fee_payer or signer).pubkey
    built = await _compile(connection, instructions, payer, budget, commitment)
    signed = await _sign(built.message, unique)

    # The signature is the fee payer's signature over the message, known before
    # the RPC sees anything. Every error past this point carries it, because a
    # send whose outcome is unknown may still land: the caller must look the
    # signature up, not rebuild (a new blockhash makes a NEW transaction, and
    # resending it can double the trade).
    signature = str(signed.signatures[0])

    try:
        await connection.send_raw_transaction(
            bytes(signed), skip_preflight=skip_preflight, preflight_commitment=commitment
        )
    except Exception as cause:
        _raise_for_send_failure(cause, signature, program_id, built.instruction_programs)
        # Reached only for AlreadyProcessed: it is on chain — confirm it.

    try:
        confirmation = await connection.confirm_transaction(
            signature,
            last_valid_block_height=built.latest.last_valid_block_height,
            commitment=commitment,
        )
    except KashTransactionExpiredError:
        raise
    except Exception as cause:
        raise _outcome_unknown(signature, "its confirmation could not be read", cause) from cause
    if confirmation.err is not None:
        program_error = decode_program_error(
            program_id, err=confirmation.err, instruction_programs=built.instruction_programs
        )
        suffix = f": {program_error.name}" if program_error.name else ""
        raise KashChainError(
            f"Transaction {signature} failed on chain{suffix}",
            code=ErrorCode.TX_REVERTED,
            context={
                "signature": signature,
                "program_error": program_error.name,
                "program_error_code": program_error.code,
                "err": confirmation.err,
            },
        )
    return SolanaSendResult(signature=signature, slot=confirmation.slot)


async def simulate_instructions(
    connection: SolanaConnection,
    program_id: Pubkey,
    instructions: Sequence[Instruction],
    *,
    payer: Pubkey,
    budget: SolanaComputeBudget,
    commitment: Commitment,
) -> SolanaSimulationResult:
    """Simulate without signing (``sigVerify: false``, fresh blockhash).

    A program refusal is a RESULT here, not a raised error; only an RPC
    failure raises.
    """
    built = await _compile(connection, instructions, payer, budget, commitment)
    unsigned = VersionedTransaction.populate(
        built.message,
        [Signature.default()] * built.message.header.num_required_signatures,
    )
    try:
        value = await connection.simulate_transaction(unsigned, commitment=commitment)
    except Exception as cause:
        raise KashChainError(
            "Simulation request failed",
            code=ErrorCode.SIMULATION_REQUEST_FAILED,
            cause=cause,
            is_retryable=True,
        ) from cause
    return SolanaSimulationResult(
        error=(
            decode_program_error(
                program_id,
                err=value.err,
                logs=value.logs,
                instruction_programs=built.instruction_programs,
            )
            if value.err is not None
            else None
        ),
        logs=tuple(value.logs),
        units_consumed=value.units_consumed,
    )


__all__ = [
    "SolanaComputeBudget",
    "SolanaProgramError",
    "SolanaSendResult",
    "SolanaSimulationResult",
    "decode_program_error",
    "send_instructions",
    "simulate_instructions",
]
