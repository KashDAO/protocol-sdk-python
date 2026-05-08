"""Chain-RPC liveness probe — symmetric with ``BundlerClient.health()``.

Mirrors ``src/shared/rpc-health.ts``.

Calls ``eth_chainId`` via the consumer's chain RPC and returns
:class:`ChainRpcHealthOk` on success or :class:`ChainRpcHealthError`
on any failure. Never raises — designed for status pages, readiness
probes, and pre-flight checks alongside ``client.bundler.health()``.

Mirrors the bundler-side shape so consumers can wire one status-page
card per dependency.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from web3 import AsyncWeb3


@dataclass(frozen=True, slots=True)
class ChainRpcHealthOk:
    """Successful health probe."""

    chain_id: int
    latency_ms: int
    ok: bool = True


@dataclass(frozen=True, slots=True)
class ChainRpcHealthError:
    """Failed health probe."""

    error: BaseException
    latency_ms: int
    ok: bool = False


ChainRpcHealth = ChainRpcHealthOk | ChainRpcHealthError


async def check_chain_rpc_health(web3: AsyncWeb3) -> ChainRpcHealth:
    """Probe the chain RPC; never raises.

    Reports wall-clock latency in ms. Round-trips ``eth_chainId``,
    which has no chain-state dependency — failure isolates the RPC
    layer, not contract availability.
    """
    started_at = time.perf_counter()
    try:
        chain_id = await web3.eth.chain_id
    except Exception as err:
        return ChainRpcHealthError(
            error=err,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
        )
    return ChainRpcHealthOk(
        chain_id=int(chain_id),
        latency_ms=int((time.perf_counter() - started_at) * 1000),
    )


__all__ = [
    "ChainRpcHealth",
    "ChainRpcHealthError",
    "ChainRpcHealthOk",
    "check_chain_rpc_health",
]
