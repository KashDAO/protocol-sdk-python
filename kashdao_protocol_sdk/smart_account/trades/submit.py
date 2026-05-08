"""``client.trades.submit(signed_user_op)`` — staleness guard + bundler forward.

Mirrors ``src/smart-account/trades/submit.ts``.

**Staleness guard.** Before forwarding, the SDK recomputes the
canonical UserOp hash from the (now fully-populated) UserOp and
recovers the EOA address from the signature against that hash. If the
recovered address doesn't match the expected owner, the signature was
made over a different (stale) hash — almost always because the
consumer signed the build-time hash before populating gas + fee
fields. We raise :class:`KashSignerError(STALE_USEROP_HASH)` pre-flight
so the consumer gets a clear diagnostic instead of a cryptic ``AA24
signature error`` from the bundler.

The recovery covers the EIP-191 raw-hash path (which is what
``SimpleAccount.validateUserOp`` expects). Consumers using the
optional ``sign_typed_data_v4`` path are responsible for ensuring
their signer-side infrastructure produces signatures the EntryPoint
will accept; the staleness guard skips them and returns control to
the bundler for verification.

**No retries.** If ``eth_sendUserOperation`` fails, the failure is
surfaced to the consumer. Retrying a transaction submission can
cause double-spends.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_account import Account
from eth_account.messages import encode_defunct

from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashSignerError
from kashdao_protocol_sdk.shared.types import Hex
from kashdao_protocol_sdk.smart_account.bundler.generic import BundlerClient
from kashdao_protocol_sdk.smart_account.trades.hash import compute_user_op_hash
from kashdao_protocol_sdk.smart_account.types import SignedUserOp, SubmitOptions, SubmitResult


async def submit_user_op(
    bundler: BundlerClient,
    chain_id: int,
    entry_point_address: Hex,
    expected_owner_address: Hex,
    signed_user_op: SignedUserOp,
    options: SubmitOptions | None = None,
) -> SubmitResult:
    """Forward a signed UserOp to the configured bundler.

    Recomputes the canonical UserOp hash and recovers the EOA from
    the signature; raises :class:`KashSignerError(STALE_USEROP_HASH)`
    when the recovered address doesn't match
    ``expected_owner_address``. Pass ``SubmitOptions(skip_staleness_check=True)``
    to bypass the recovery (e.g. for non-EIP-191 signing paths).
    """
    opts = options or SubmitOptions()
    if not opts.skip_staleness_check:
        _validate_signature_freshness(
            chain_id,
            entry_point_address,
            expected_owner_address,
            signed_user_op,
        )
    user_op_hash = await bundler.send(signed_user_op)
    return SubmitResult(user_op_hash=user_op_hash)


@dataclass(frozen=True, slots=True)
class _StalenessContext:
    """Context fields copied into the typed staleness errors."""

    sender: Hex
    expected_hash: Hex


def _validate_signature_freshness(
    chain_id: int,
    entry_point_address: Hex,
    expected_owner_address: Hex,
    signed_user_op: SignedUserOp,
) -> None:
    expected_hash = compute_user_op_hash(chain_id, entry_point_address, signed_user_op.user_op)

    try:
        # ``encode_defunct`` produces the EIP-191 prefixed message that
        # ``viem.recoverMessageAddress({ message: { raw: hash } })``
        # signs over. Recovering against this is byte-equivalent to the
        # TS path.
        message = encode_defunct(hexstr=expected_hash)
        recovered = Account.recover_message(message, signature=signed_user_op.signature)
    except Exception as cause:
        raise KashSignerError(
            "signature is malformed and cannot be recovered",
            code=ErrorCode.SIGNATURE_MALFORMED,
            context={
                "sender": signed_user_op.user_op.sender,
                "expected_hash": expected_hash,
            },
            cause=cause,
        ) from cause

    if recovered.lower() != expected_owner_address.lower():
        raise KashSignerError(
            "signature was made over a stale or wrong userOp hash - "
            "recovered EOA does not match the configured signer. After "
            "populating gas + fee fields, recompute the hash via "
            "client.trades.hash_of(user_op) and re-sign. If you used a "
            "non-EIP-191 signing path (e.g. sign_typed_data_v4), pass "
            "SubmitOptions(skip_staleness_check=True) to "
            "client.trades.submit().",
            code=ErrorCode.STALE_USEROP_HASH,
            context={
                "sender": signed_user_op.user_op.sender,
                "expected_hash": expected_hash,
                "expected_owner": expected_owner_address,
                "recovered_owner": recovered,
            },
        )


__all__ = ["submit_user_op"]
