"""``client.markets.watch()`` — real-time event stream for a market.

Mirrors ``src/shared/markets/watch.ts``.

Subscribes to Buy / Sell / MarketResolved / MarketFrozenObserved events
via ``eth_subscribe(logs)`` over the consumer's WebSocket RPC. Surfaces
them as a normalised :class:`WatchEvent` discriminated union.

**Delivery semantics: best-effort.** On WebSocket disconnect the
subscription attempts to reconnect with bounded backoff but does NOT
replay missed events. Consumers needing gap-free coverage should
backfill via their own indexer or a higher-level REST client.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol, cast

from eth_abi import decode as abi_decode  # type: ignore[attr-defined]
from eth_typing import BlockNumber, ChecksumAddress, HexStr
from eth_utils import keccak  # type: ignore[attr-defined]
from hexbytes import HexBytes
from web3 import AsyncWeb3
from web3.types import LogReceipt, LogsSubscriptionArg

from kashdao_protocol_sdk.shared.contracts.abis import MARKET_ABI
from kashdao_protocol_sdk.shared.errors import KashChainError
from kashdao_protocol_sdk.shared.types import Hex

# ---------------------------------------------------------------------------
# WatchEvent (discriminated union)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TradeWatchEvent:
    """Buy or Sell event surfaced via ``client.markets.watch``."""

    side: Literal["buy", "sell"]
    outcome: int
    receiver: Hex
    assets_usdc: int
    tokens_wad: int
    reserve_after_wad: int
    supply_after_wad: tuple[int, ...]
    block_number: int
    transaction_hash: Hex
    log_index: int
    type: Literal["TRADE"] = "TRADE"


@dataclass(frozen=True, slots=True)
class ResolvedWatchEvent:
    """``MarketResolved`` event surfaced via ``client.markets.watch``."""

    winning_outcome: int
    evidence_hash: Hex
    block_number: int
    transaction_hash: Hex
    log_index: int
    type: Literal["RESOLVED"] = "RESOLVED"


@dataclass(frozen=True, slots=True)
class FrozenWatchEvent:
    """``MarketFrozenObserved`` event surfaced via ``client.markets.watch``."""

    observed_at: int
    block_number: int
    transaction_hash: Hex
    log_index: int
    type: Literal["FROZEN"] = "FROZEN"


WatchEvent = TradeWatchEvent | ResolvedWatchEvent | FrozenWatchEvent
"""Discriminated union over the three event types we surface."""

WatchConnectionState = Literal["connected", "reconnecting", "unsubscribed"]


# ---------------------------------------------------------------------------
# WatchOptions / WatchSubscription
# ---------------------------------------------------------------------------


class _MaybeAsyncCallback(Protocol):
    def __call__(self, event: WatchEvent) -> Awaitable[None] | None: ...


@dataclass(frozen=True, slots=True)
class WatchOptions:
    """Inputs for :func:`watch_market`."""

    on_event: _MaybeAsyncCallback
    on_reconnect: Callable[[], None] | None = None
    on_error: Callable[[KashChainError], None] | None = None


@dataclass(slots=True)
class WatchSubscription:
    """Handle for a market subscription. ``unsubscribe()`` is idempotent."""

    _task: asyncio.Task[None]
    _cancel_event: asyncio.Event
    _state: dict[str, Any]

    def unsubscribe(self) -> None:
        """Cancel the subscription. Fire-and-forget.

        ``unsubscribe`` is non-blocking — the underlying task may
        still be running briefly after this returns. Use :meth:`aclose`
        if the consumer needs to wait for the task to finish before
        tearing down the ``web3`` client (otherwise the client may
        close mid-call and surface "loop is closed" warnings).
        """
        self._cancel_event.set()
        if not self._task.done():
            self._task.cancel()
        self._state["unsubscribed"] = True

    async def aclose(self, timeout_seconds: float = 5.0) -> None:
        """Cancel the subscription AND wait for the task to finish.

        Honors ``timeout_seconds`` so a stuck task doesn't block
        consumer shutdown forever. Idempotent.
        """
        self.unsubscribe()
        if self._task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            pass
        except (asyncio.CancelledError, Exception):
            # ``CancelledError`` is a ``BaseException`` (not ``Exception``)
            # in Python 3.8+, so ``except Exception`` alone would let it
            # propagate. We want both flavors swallowed here — the task
            # may have raised normally OR been cancelled mid-cleanup.
            pass

    def is_healthy(self) -> bool:
        if self._state["unsubscribed"]:
            return False
        return bool(self._state["last_event_arrived"]) and not self._state["last_was_error"]

    def last_block_seen(self) -> int | None:
        """Highest block number observed via dispatched events / polling.

        Tracks event arrivals, NOT chain head — between events, the
        underlying RPC may have advanced many blocks that this watcher
        simply hasn't surfaced (no Buy/Sell/Resolve/Frozen log to
        process). Suitable as a high-water mark for "what events have
        I delivered to the consumer", not for "where is the chain
        right now". For chain head, call ``web3.eth.block_number``.
        """
        value = self._state["last_block_seen"]
        if value is None:
            return None
        return int(value)

    def connection_state(self) -> WatchConnectionState:
        if self._state["unsubscribed"]:
            return "unsubscribed"
        if self._state["last_was_error"]:
            return "reconnecting"
        return "connected"


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


_EVENT_NAMES: Final[tuple[str, ...]] = (
    "Buy",
    "Sell",
    "MarketResolved",
    "MarketFrozenObserved",
)

_MAX_POLL_RANGE: Final[int] = 500
"""Max blocks per ``eth_getLogs`` call when in polling fallback mode.

Public RPC providers reject ranges >10k blocks; 500 keeps us well under
that with headroom for slow callbacks. The poll loop catches up
chunk-by-chunk on the next tick if the chain runs ahead.
"""


def _topic_for_event(event_name: str) -> bytes:
    for entry in MARKET_ABI:
        if entry.get("type") == "event" and entry.get("name") == event_name:
            inputs = entry.get("inputs", [])
            sig = f"{event_name}({','.join(_canonical(i) for i in inputs)})"
            return keccak(text=sig)
    raise RuntimeError(f"event {event_name!r} not in vendored MARKET_ABI")


def _canonical(inp: dict[str, Any]) -> str:
    t = str(inp["type"])
    if t.startswith("tuple"):
        components = inp.get("components", [])
        return f"({','.join(_canonical(c) for c in components)}){t[len('tuple') :]}"
    return t


def _abi_entry(name: str) -> dict[str, Any]:
    for entry in MARKET_ABI:
        if entry.get("type") == "event" and entry.get("name") == name:
            return entry
    raise RuntimeError(f"event {name!r} not in vendored MARKET_ABI")


def watch_market(
    web3: AsyncWeb3,
    market_address: Hex,
    options: WatchOptions,
) -> WatchSubscription:
    """Subscribe to real-time events for ``market_address``.

    Requires ``web3`` to be configured with a WebSocket provider. With
    HTTP-only providers, the subscription falls back to a polling loop
    (1s interval; configurable via the ``web3`` client).

    Returns a :class:`WatchSubscription` immediately; the subscription
    runs in the background until :meth:`WatchSubscription.unsubscribe`
    is called.
    """
    state: dict[str, Any] = {
        "last_block_seen": None,
        "last_event_arrived": False,
        "last_was_error": False,
        "unsubscribed": False,
    }
    cancel_event = asyncio.Event()

    async def _runner() -> None:
        backoff = 1.0
        while not cancel_event.is_set():
            try:
                await _subscribe_loop(web3, market_address, options, state, cancel_event)
                # Clean exit (cancel_event set) — break.
                if cancel_event.is_set():
                    return
                # Otherwise the inner loop returned without an error;
                # treat as transient and reconnect.
                state["last_was_error"] = True
            except KashChainError as err:
                state["last_was_error"] = True
                if options.on_error is not None:
                    options.on_error(err)
            except Exception as cause:
                state["last_was_error"] = True
                if options.on_error is not None:
                    options.on_error(
                        KashChainError(
                            "markets.watch RPC error",
                            code="WATCH_RPC_ERROR",
                            context={"market_address": market_address},
                            cause=cause,
                            is_retryable=True,
                        )
                    )
            if cancel_event.is_set():
                return
            if options.on_reconnect is not None:
                options.on_reconnect()
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                backoff = min(backoff * 2, 30.0)

    # ``watch_market`` is sync because it returns a handle immediately.
    # ``asyncio.ensure_future`` schedules the coroutine on the running
    # loop; raises if no loop is running, which is the right diagnostic
    # — watch only makes sense from inside an async context.
    task = asyncio.ensure_future(_runner())
    return WatchSubscription(_task=task, _cancel_event=cancel_event, _state=state)


async def _subscribe_loop(
    web3: AsyncWeb3,
    market_address: Hex,
    options: WatchOptions,
    state: dict[str, Any],
    cancel_event: asyncio.Event,
) -> None:
    """One subscription attempt — runs until cancellation or error."""
    topics = {name: HexBytes(_topic_for_event(name)).to_0x_hex() for name in _EVENT_NAMES}
    abi_entries = {name: _abi_entry(name) for name in _EVENT_NAMES}
    address_cs = AsyncWeb3.to_checksum_address(market_address)

    # web3.py async eth_subscribe pattern. If the provider is HTTP, we
    # fall back to a polling loop using `eth_getLogs` from the latest block.
    is_ws = (
        "Websocket" in type(web3.provider).__name__ or "WebSocket" in type(web3.provider).__name__
    )

    if is_ws:
        await _ws_subscribe(web3, address_cs, topics, abi_entries, options, state, cancel_event)
    else:
        await _polling_loop(web3, address_cs, topics, abi_entries, options, state, cancel_event)


async def _ws_subscribe(
    web3: AsyncWeb3,
    address: str,
    topics: dict[str, str],
    abi_entries: dict[str, dict[str, Any]],
    options: WatchOptions,
    state: dict[str, Any],
    cancel_event: asyncio.Event,
) -> None:
    """eth_subscribe-based delivery via web3.py's WebsocketProvider."""
    # web3.py's exact subscription API differs by version; fall through
    # to polling if the underlying provider doesn't expose subscribe.
    sub_id_handlers: dict[str, str] = {}
    try:
        for name, topic in topics.items():
            sub_arg = cast(
                LogsSubscriptionArg,
                {"address": address, "topics": [topic]},
            )
            sub_id = await web3.eth.subscribe("logs", sub_arg)
            sub_id_handlers[str(sub_id)] = name
    except Exception:
        await _polling_loop(web3, address, topics, abi_entries, options, state, cancel_event)
        return

    try:
        async for payload in web3.socket.process_subscriptions():
            if cancel_event.is_set():
                break
            payload_id = payload.get("subscription") or payload.get("sub_id")
            log_data = payload.get("result")
            event_name = sub_id_handlers.get(str(payload_id))
            if event_name is None or log_data is None:
                continue
            ev = _decode_log(event_name, abi_entries[event_name], log_data, options, address)
            if ev is None:
                continue
            await _dispatch(ev, options, state)
    finally:
        for held_sub_id in sub_id_handlers:
            try:
                await web3.eth.unsubscribe(cast(HexStr, held_sub_id))
            except Exception:
                pass


async def _polling_loop(
    web3: AsyncWeb3,
    address: str,
    topics: dict[str, str],
    abi_entries: dict[str, dict[str, Any]],
    options: WatchOptions,
    state: dict[str, Any],
    cancel_event: asyncio.Event,
) -> None:
    """Fallback delivery via ``eth_getLogs`` polling for HTTP providers.

    Per-iteration block range is capped at :data:`_MAX_POLL_RANGE` to
    survive falling-behind scenarios (network blip, slow callback): a
    single ``eth_getLogs`` call over more than ~10k blocks fails on most
    public RPC providers and burns substantial quota on paid ones. When
    we fall behind, we catch up in chunks of ``_MAX_POLL_RANGE`` blocks
    per tick.

    Honors ``state['last_block_seen']`` so reconnects (after a WS
    failure or polling-loop error) resume from the last processed
    block instead of jumping forward — closes the event-loss gap on
    reconnect.
    """
    last_seen = state.get("last_block_seen")
    if last_seen is not None:
        last_block: int = int(last_seen)
    else:
        last_block = int(await web3.eth.block_number)
    poll_interval = 1.5

    while not cancel_event.is_set():
        try:
            current = int(await web3.eth.block_number)
            if current > last_block:
                upper = min(current, last_block + _MAX_POLL_RANGE)
                logs = await web3.eth.get_logs(
                    {
                        "address": cast(ChecksumAddress, address),
                        "fromBlock": cast(BlockNumber, last_block + 1),
                        "toBlock": cast(BlockNumber, upper),
                        "topics": [list(topics.values())],
                    }
                )
                for raw in logs:
                    name = _topic_to_name(topics, raw["topics"][0])
                    if name is None:
                        continue
                    ev = _decode_log(name, abi_entries[name], raw, options, address)
                    if ev is None:
                        continue
                    await _dispatch(ev, options, state)
                last_block = int(upper)
                # Persist progress so a subsequent reconnect resumes
                # from here, not from `web3.eth.block_number`.
                state["last_block_seen"] = last_block
        except Exception as cause:
            if options.on_error is not None:
                options.on_error(
                    KashChainError(
                        "markets.watch polling error",
                        code="WATCH_RPC_ERROR",
                        context={"market_address": address},
                        cause=cause,
                        is_retryable=True,
                    )
                )
            state["last_was_error"] = True
            return  # Outer runner will reconnect with backoff.
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            continue


def _topic_to_name(topics: dict[str, str], topic_hex: Any) -> str | None:
    canon = HexBytes(topic_hex).to_0x_hex().lower()
    for name, ref in topics.items():
        if HexBytes(ref).to_0x_hex().lower() == canon:
            return name
    return None


def _decode_log(
    event_name: str,
    abi_entry: dict[str, Any],
    raw: Any,
    options: WatchOptions,
    market_address: str,
) -> WatchEvent | None:
    """Parse a raw log dict / LogReceipt into a typed WatchEvent."""
    block_number = raw.get("blockNumber") or raw.get("block_number")
    tx_hash = raw.get("transactionHash") or raw.get("transaction_hash")
    log_index = raw.get("logIndex")
    if log_index is None:
        log_index = raw.get("log_index")
    if block_number is None or tx_hash is None or log_index is None:
        if options.on_error is not None:
            options.on_error(
                KashChainError(
                    f"markets.watch dropped incomplete {event_name} log "
                    "(missing blockNumber/transactionHash/logIndex)",
                    code="LOG_INCOMPLETE",
                    context={
                        "market_address": market_address,
                        "event_type": event_name,
                    },
                    is_retryable=True,
                )
            )
        return None
    block_number = int(block_number, 16) if isinstance(block_number, str) else int(block_number)
    log_index = int(log_index, 16) if isinstance(log_index, str) else int(log_index)
    tx_hash = HexBytes(tx_hash).to_0x_hex()

    receipt: LogReceipt = {
        "address": cast(ChecksumAddress, raw.get("address", market_address)),
        "topics": [HexBytes(t) for t in raw.get("topics", [])],
        "data": HexBytes(raw.get("data", "0x")),
        "blockNumber": cast(BlockNumber, block_number),
        "transactionHash": HexBytes(tx_hash),
        "logIndex": log_index,
        "transactionIndex": int(raw.get("transactionIndex", 0))
        if raw.get("transactionIndex")
        else 0,
        "blockHash": HexBytes(raw.get("blockHash", "0x" + "00" * 32)),
        "removed": bool(raw.get("removed", False)),
    }
    try:
        decoded = _decode_event_log(abi_entry, receipt)
    except Exception as cause:
        if options.on_error is not None:
            options.on_error(
                KashChainError(
                    f"markets.watch failed to decode {event_name} log",
                    code="LOG_DECODE_FAILED",
                    context={
                        "market_address": market_address,
                        "event_type": event_name,
                        "block_number": block_number,
                        "transaction_hash": tx_hash,
                    },
                    cause=cause,
                    is_retryable=True,
                )
            )
        return None
    args = decoded["args"]

    if event_name in ("Buy", "Sell"):
        side: Literal["buy", "sell"] = "buy" if event_name == "Buy" else "sell"
        return TradeWatchEvent(
            side=side,
            outcome=int(args.get("outcome", 0)),
            receiver=str(args.get("receiver", "0x")),
            assets_usdc=int(args.get("assetsInUsdc" if side == "buy" else "assetsOutUsdc", 0)),
            tokens_wad=int(args.get("tokensOutWad" if side == "buy" else "tokensInWad", 0)),
            reserve_after_wad=int(args.get("reserveAfterWad", 0)),
            supply_after_wad=tuple(int(s) for s in args.get("supplyAfterWad", []) or []),
            block_number=block_number,
            transaction_hash=tx_hash,
            log_index=log_index,
        )
    if event_name == "MarketResolved":
        return ResolvedWatchEvent(
            winning_outcome=int(args.get("winningOutcome", 0)),
            evidence_hash=HexBytes(args.get("evidenceHash", "0x")).to_0x_hex(),
            block_number=block_number,
            transaction_hash=tx_hash,
            log_index=log_index,
        )
    if event_name == "MarketFrozenObserved":
        return FrozenWatchEvent(
            observed_at=int(args.get("observedAt", 0)),
            block_number=block_number,
            transaction_hash=tx_hash,
            log_index=log_index,
        )
    return None


def _decode_event_log(abi_entry: dict[str, Any], log: LogReceipt) -> dict[str, Any]:
    """Vendored event-log decoder.

    Replaces ``web3._utils.events.get_event_data`` so the SDK doesn't
    depend on web3.py's underscored ``_utils`` package. Decodes:

    * Indexed inputs from ``log["topics"][1:]`` — value-bearing types
      (uint, int, bool, address) are ABI-decoded; reference types
      (string, bytes, arrays, tuples) are kept as the 32-byte topic
      digest under the input name (Solidity stores their keccak in
      the topic, not the value, so the original value isn't recoverable
      — same caveat as web3.py's reference decoder).
    * Non-indexed inputs from ``log["data"]`` via ``eth_abi.decode``
      against the canonical Solidity types.

    Returns ``{"event": <name>, "args": {<name>: <value>, ...}, ...}``
    matching the surface our caller (`_decode_log`) expects.
    """
    inputs = abi_entry.get("inputs", [])
    indexed_inputs = [i for i in inputs if i.get("indexed")]
    data_inputs = [i for i in inputs if not i.get("indexed")]

    args: dict[str, Any] = {}

    topics = log["topics"]
    # topics[0] is the event signature; indexed args start at topics[1].
    for i, inp in enumerate(indexed_inputs):
        if i + 1 >= len(topics):
            break
        topic = HexBytes(topics[i + 1])
        sol_type = _canonical_solidity_type(inp)
        if _is_value_type(sol_type):
            (decoded,) = abi_decode([sol_type], bytes(topic))
            args[inp["name"]] = decoded
        else:
            # Reference types: only the topic digest is on-chain.
            args[inp["name"]] = bytes(topic)

    if data_inputs:
        data_types = [_canonical_solidity_type(i) for i in data_inputs]
        data_bytes = bytes(HexBytes(log["data"]))
        decoded_data = abi_decode(data_types, data_bytes)
        for inp, value in zip(data_inputs, decoded_data, strict=True):
            args[inp["name"]] = value

    return {
        "event": abi_entry["name"],
        "args": args,
        "logIndex": log.get("logIndex"),
        "transactionIndex": log.get("transactionIndex"),
        "transactionHash": log.get("transactionHash"),
        "address": log.get("address"),
        "blockHash": log.get("blockHash"),
        "blockNumber": log.get("blockNumber"),
    }


def _canonical_solidity_type(inp: dict[str, Any]) -> str:
    """Return the canonical Solidity type for ABI codec calls.

    Tuples need their components flattened to ``(t1,t2)`` (with array
    suffixes preserved) — same logic as the decoders module's
    ``_canonical_type``; vendored here to avoid a circular import.
    """
    t = cast(str, inp["type"])
    if t.startswith("tuple"):
        components = inp.get("components", [])
        flat = ",".join(_canonical_solidity_type(c) for c in components)
        suffix = t[len("tuple") :]
        return f"({flat}){suffix}"
    return t


def _is_value_type(sol_type: str) -> bool:
    """Whether the type's value (not its keccak) lives in an indexed topic.

    Solidity stores indexed reference-type args as ``keccak(value)``
    in the topic; only value types ship their actual value. This
    match keeps the codepath simple — anything not in the
    fixed-size value-type set is treated as reference.
    """
    if sol_type in ("address", "bool"):
        return True
    if sol_type.startswith(("uint", "int")) and "[" not in sol_type:
        return True
    if sol_type.startswith("bytes") and sol_type != "bytes" and "[" not in sol_type:
        # bytes1..bytes32 are value types; ``bytes`` (dynamic) is not.
        return True
    return False


async def _dispatch(event: WatchEvent, options: WatchOptions, state: dict[str, Any]) -> None:
    state["last_event_arrived"] = True
    if event.block_number is not None:
        last = state["last_block_seen"]
        if last is None or event.block_number > last:
            state["last_block_seen"] = event.block_number
    try:
        result = options.on_event(event)
        if asyncio.iscoroutine(result):
            await result
    except Exception as cause:
        # Handler raised — keep the unhealthy signal so consumers
        # observing ``is_healthy()`` / ``connection_state()`` see the
        # state. Without this flip, a long-suffering handler exception
        # would silently report "connected" forever.
        state["last_was_error"] = True
        if options.on_error is not None:
            options.on_error(
                KashChainError(
                    "markets.watch on_event handler raised",
                    code="WATCH_HANDLER_FAILED",
                    cause=cause,
                )
            )
        return
    # Successful dispatch — clear any prior error flag.
    state["last_was_error"] = False


# Re-export for static type annotations
__all__ = [
    "FrozenWatchEvent",
    "ResolvedWatchEvent",
    "TradeWatchEvent",
    "WatchConnectionState",
    "WatchEvent",
    "WatchOptions",
    "WatchSubscription",
    "watch_market",
]
