"""The RPC seam the Solana client talks through.

Mirrors the TS SDK's ``SolanaClientConnection`` (a ``Pick`` of web3.js
``Connection``): a narrow :class:`SolanaConnection` protocol carrying only
the five calls the client makes, in plain Python types, so a test (or a
custom transport) can supply a fake without an RPC.

:class:`SolanaRpcConnection` implements it as plain Solana JSON-RPC over
``httpx`` (already a dependency of this package). It deliberately does not
wrap solana-py: solana-py 0.40 requires Python 3.11 and moved its send
options between minor versions, while this package supports Python 3.10 —
and the five calls below are the whole surface the client needs. Errors
keep the RPC's own JSON shape (``{"InstructionError": [1, {"Custom":
6001}]}``), which is exactly what
:func:`~kashdao_protocol_sdk.solana.transaction.decode_program_error` reads.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol, runtime_checkable

import httpx
from solders.hash import Hash
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from kashdao_protocol_sdk.shared.errors import KashTransactionExpiredError

#: Commitment for reads, preflight and confirmation.
Commitment = Literal["processed", "confirmed", "finalized"]

_COMMITMENT_RANK: Final[dict[str, int]] = {"processed": 0, "confirmed": 1, "finalized": 2}

#: JSON-RPC error code the RPC uses when ``sendTransaction``'s preflight refuses.
_PREFLIGHT_FAILURE_CODE: Final = -32002

#: Ceiling on the confirm loop's backoff between transient RPC failures.
_MAX_BACKOFF_SECONDS: Final = 4.0


@dataclass(frozen=True, slots=True)
class AccountInfo:
    """The slice of an account the client reads: its owner program and raw data."""

    owner: Pubkey
    data: bytes


@dataclass(frozen=True, slots=True)
class LatestBlockhash:
    blockhash: Hash
    last_valid_block_height: int


@dataclass(frozen=True, slots=True)
class Confirmation:
    """A confirmed transaction: the slot it was observed at and its error, if it failed."""

    slot: int
    #: ``None`` on success; otherwise the RPC's JSON error (``{"InstructionError": [...]}``).
    err: object | None


@dataclass(frozen=True, slots=True)
class SimulationValue:
    err: object | None
    logs: list[str]
    units_consumed: int | None


class SolanaRpcError(Exception):
    """A DEFINITE refusal: the RPC answered with a JSON-RPC error object or an HTTP 4xx.

    For ``sendTransaction`` this means the transaction was not accepted, so
    rebuilding and resending is safe. ``http_status`` is set for a 4xx (``429``
    is a rate limit, which a polling loop backs off from).
    """

    def __init__(
        self,
        method: str,
        code: int | None,
        message: str,
        data: object = None,
        *,
        http_status: int | None = None,
    ) -> None:
        super().__init__(f"{method}: {message} (code {code})")
        self.method = method
        self.code = code
        self.rpc_message = message
        self.data = data
        self.http_status = http_status


class SolanaRpcTransportError(Exception):
    """No definite answer: a timeout, a reset connection, an HTTP 5xx or an unreadable body.

    For ``sendTransaction`` the RPC may already have forwarded the
    transaction, so it MAY land; the client never treats this as a refusal.
    """

    def __init__(self, method: str, message: str) -> None:
        super().__init__(f"{method}: {message}")
        self.method = method


class PreflightFailure(SolanaRpcError):
    """``sendTransaction`` answered JSON-RPC -32002: the preflight simulation failed.

    :attr:`err` is the transaction error (``data.err``) and :attr:`logs` the
    simulation logs, which may be EMPTY. The client reads :attr:`err` first:
    ``"AlreadyProcessed"`` means this very transaction is already on chain.
    """

    def __init__(self, message: str, *, err: object, logs: list[str], data: object = None) -> None:
        super().__init__("sendTransaction", _PREFLIGHT_FAILURE_CODE, message, data)
        self.err = err
        self.logs = logs


@runtime_checkable
class SolanaConnection(Protocol):
    """Everything the client calls on an RPC. :class:`SolanaRpcConnection` satisfies it.

    Error contract — the client's money-safety classification rests on it:

    - ``send_raw_transaction`` raises :class:`PreflightFailure` for a JSON-RPC
      -32002 answer (whatever its ``data.err``) and :class:`SolanaRpcError`
      for any other JSON-RPC error object, with its ``code``. Only -32003 and
      -32602 (and a non-``AlreadyProcessed`` -32002) count as "not
      forwarded"; ANY other exception or code is read as "the outcome is
      unknown, it may land".
    - ``confirm_transaction`` raises
      :class:`~kashdao_protocol_sdk.KashTransactionExpiredError` ONLY after
      the blockhash expired and a status read from a node at or after the
      slot where the expiry was observed found nothing. Any other exception is
      read as "the confirmation could not be read".
    """

    async def get_multiple_accounts(
        self, addresses: Sequence[Pubkey], commitment: Commitment
    ) -> list[AccountInfo | None]: ...

    async def get_latest_blockhash(self, commitment: Commitment) -> LatestBlockhash: ...

    async def send_raw_transaction(
        self, raw: bytes, *, skip_preflight: bool, preflight_commitment: Commitment
    ) -> str: ...

    async def confirm_transaction(
        self,
        signature: str,
        *,
        last_valid_block_height: int,
        commitment: Commitment,
    ) -> Confirmation: ...

    async def simulate_transaction(
        self, transaction: VersionedTransaction, *, commitment: Commitment
    ) -> SimulationValue: ...


class SolanaRpcConnection:
    """:class:`SolanaConnection` as Solana JSON-RPC over HTTP.

    Parameters
    ----------
    rpc_url:
        The RPC endpoint.
    timeout_seconds:
        Per-request timeout.
    poll_interval_seconds:
        How often :meth:`confirm_transaction` polls ``getSignatureStatuses``.
    confirm_timeout_seconds:
        Overall budget for :meth:`confirm_transaction`, across backoff-and-retry
        of transient RPC failures.
    http_client:
        An ``httpx.AsyncClient`` to use instead of creating one. A client
        passed in is the caller's to close; one created here is closed by
        :meth:`aclose`.
    """

    def __init__(
        self,
        rpc_url: str,
        *,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.5,
        confirm_timeout_seconds: float = 90.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.rpc_url = rpc_url
        self._poll_interval = poll_interval_seconds
        self._confirm_timeout = confirm_timeout_seconds
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._ids = itertools.count(1)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _call(self, method: str, params: list[Any]) -> Any:
        try:
            response = await self._http.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params},
            )
        except httpx.HTTPError as exc:
            raise SolanaRpcTransportError(method, f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 500:
            raise SolanaRpcTransportError(method, f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise SolanaRpcError(
                method,
                None,
                f"HTTP {response.status_code}",
                response.text,
                http_status=response.status_code,
            )
        try:
            envelope = response.json()
        except ValueError as exc:
            raise SolanaRpcTransportError(method, "response body is not JSON") from exc
        if not isinstance(envelope, dict):
            raise SolanaRpcTransportError(method, "response is not a JSON-RPC envelope")
        error = envelope.get("error")
        if error is not None:
            if not isinstance(error, dict):
                raise SolanaRpcError(method, None, str(error))
            data = error.get("data")
            if method == "sendTransaction" and error.get("code") == _PREFLIGHT_FAILURE_CODE:
                details = data if isinstance(data, dict) else {}
                logs = details.get("logs")
                raise PreflightFailure(
                    str(error.get("message", "")),
                    err=details.get("err"),
                    logs=[str(x) for x in logs] if isinstance(logs, list) else [],
                    data=data,
                )
            code = error.get("code")
            raise SolanaRpcError(
                method,
                code if isinstance(code, int) else None,
                str(error.get("message", "")),
                data,
            )
        if "result" not in envelope:
            raise SolanaRpcTransportError(method, "response carries neither result nor error")
        return envelope["result"]

    async def get_multiple_accounts(
        self, addresses: Sequence[Pubkey], commitment: Commitment
    ) -> list[AccountInfo | None]:
        result = await self._call(
            "getMultipleAccounts",
            [[str(a) for a in addresses], {"encoding": "base64", "commitment": commitment}],
        )
        out: list[AccountInfo | None] = []
        for value in result["value"]:
            if value is None:
                out.append(None)
                continue
            encoded, encoding = value["data"]
            if encoding != "base64":
                raise SolanaRpcError(
                    "getMultipleAccounts", None, f"unexpected data encoding {encoding}"
                )
            out.append(
                AccountInfo(
                    owner=Pubkey.from_string(value["owner"]), data=base64.b64decode(encoded)
                )
            )
        return out

    async def get_latest_blockhash(self, commitment: Commitment) -> LatestBlockhash:
        result = await self._call("getLatestBlockhash", [{"commitment": commitment}])
        value = result["value"]
        return LatestBlockhash(
            blockhash=Hash.from_string(value["blockhash"]),
            last_valid_block_height=int(value["lastValidBlockHeight"]),
        )

    async def send_raw_transaction(
        self, raw: bytes, *, skip_preflight: bool, preflight_commitment: Commitment
    ) -> str:
        result = await self._call(
            "sendTransaction",
            [
                base64.b64encode(raw).decode("ascii"),
                {
                    "encoding": "base64",
                    "skipPreflight": skip_preflight,
                    "preflightCommitment": preflight_commitment,
                },
            ],
        )
        return str(result)

    async def confirm_transaction(
        self,
        signature: str,
        *,
        last_valid_block_height: int,
        commitment: Commitment,
    ) -> Confirmation:
        """Poll until the signature reaches ``commitment``, or provably will not land.

        - Transient failures while polling (timeouts, 5xx, HTTP 429) back off
          and retry until ``confirm_timeout_seconds``; only then does the
          failure surface (as "could not be read", never as "did not land").
        - Expiry is pinned to one slot, because behind a load balancer a status
          read can land on a node BEHIND the one that saw the expiry, and a
          lagging node's "no such signature" proves nothing. ``getEpochInfo``
          reports the block height AND its slot together; once the height is
          past ``last_valid_block_height`` the status is re-read, and only a
          read whose context slot is at or after that slot can declare the
          transaction expired. A signature found there landed and is followed
          to ``commitment``; an absent one from a lagging node is polled again
          until the budget runs out (then "could not be read").
        """
        wanted = _COMMITMENT_RANK[commitment]
        deadline = time.monotonic() + self._confirm_timeout
        delay = self._poll_interval
        expired_at_slot: int | None = None
        while True:
            try:
                status, read_slot = await self._signature_status(signature)
                if status is not None:
                    reached = status.get("confirmationStatus")
                    if status.get("err") is not None or (
                        isinstance(reached, str) and _COMMITMENT_RANK.get(reached, -1) >= wanted
                    ):
                        return Confirmation(slot=int(status["slot"]), err=status.get("err"))
                    # Seen on chain: it landed, so expiry no longer applies.
                    expired_at_slot = None
                elif expired_at_slot is not None:
                    if read_slot >= expired_at_slot:
                        raise KashTransactionExpiredError(
                            f"Transaction {signature} did not land before its blockhash "
                            f"expired (a status read at slot {read_slot}, at or after the "
                            f"expiry slot {expired_at_slot}, found no trace of it); "
                            "rebuild and resend",
                            signature=signature,
                        )
                    # A node behind the expiry slot: its silence proves nothing.
                else:
                    epoch = await self._call("getEpochInfo", [{"commitment": commitment}])
                    if int(epoch["blockHeight"]) > last_valid_block_height:
                        expired_at_slot = int(epoch["absoluteSlot"])
                        # Re-read the status now, pinned to that slot.
                        continue
                delay = self._poll_interval
            except (SolanaRpcTransportError, SolanaRpcError) as exc:
                transient = isinstance(exc, SolanaRpcTransportError) or exc.http_status == 429
                if not transient or time.monotonic() >= deadline:
                    raise
                delay = min(max(delay * 2, self._poll_interval), _MAX_BACKOFF_SECONDS)
            if time.monotonic() >= deadline:
                raise SolanaRpcTransportError(
                    "confirmTransaction",
                    f"{signature} not confirmed within {self._confirm_timeout}s",
                )
            await asyncio.sleep(delay)

    async def _signature_status(self, signature: str) -> tuple[dict[str, Any] | None, int]:
        """The signature's status and the context slot of the node that answered."""
        statuses = await self._call(
            "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]
        )
        status: dict[str, Any] | None = statuses["value"][0]
        return status, int(statuses["context"]["slot"])

    async def simulate_transaction(
        self, transaction: VersionedTransaction, *, commitment: Commitment
    ) -> SimulationValue:
        result = await self._call(
            "simulateTransaction",
            [
                base64.b64encode(bytes(transaction)).decode("ascii"),
                {
                    "encoding": "base64",
                    "sigVerify": False,
                    "replaceRecentBlockhash": True,
                    "commitment": commitment,
                },
            ],
        )
        value = result["value"]
        units = value.get("unitsConsumed")
        return SimulationValue(
            err=value.get("err"),
            logs=[str(x) for x in value.get("logs") or []],
            units_consumed=int(units) if units is not None else None,
        )


__all__ = [
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
]
