"""Solana clusters the Kash market program is deployed on.

Mirrors ``src/solana/clusters.ts``. Program ids and USDC mints are the
backend's own registry (``@kashdao/constants`` ``SOLANA_PROGRAM_IDENTITIES``),
copied here and drift-checked by ``scripts/sync-solana-vendor.py --check``,
so the SDK cannot address a different program than the one Kash's
services trade against.

The ``kash_market`` IDL embeds the CANONICAL build's id
(``4WFoPLragac369ctiiJMoSkrd2y9WL4LGH4vuX9hWs1K``), which is no deployed
cluster's id — every instruction this SDK builds names the cluster's
program id explicitly, never the IDL's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, TypeGuard

from solders.pubkey import Pubkey

from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashConfigError

#: A cluster with a Kash deployment. ``mainnet-beta`` is the default everywhere.
SolanaCluster = Literal["mainnet-beta", "devnet"]

#: ``kash_market`` program id and USDC mint per cluster.
SOLANA_PROGRAM_IDENTITIES: Final[dict[str, dict[str, str]]] = {
    "devnet": {
        "market_program_id": "J3tSyyhaeokZc9VnogXR9anQMeA9FjKn8fnnjrLTkuN5",
        "usdc_mint": "6xNqPRTd9V71N8KBp1X3x87rEA2sLiLsDqU7gQ4MuwBe",
    },
    "mainnet-beta": {
        "market_program_id": "Jr8Bd8efPfNYHW65vZrVzeLbYkzy1oo3QB3i8cYdDcy",
        "usdc_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    },
}

#: Every cluster with a Kash deployment, mainnet first.
SOLANA_CLUSTERS: Final[tuple[SolanaCluster, ...]] = ("mainnet-beta", "devnet")

DEFAULT_SOLANA_CLUSTER: Final[SolanaCluster] = "mainnet-beta"


@dataclass(frozen=True, slots=True)
class SolanaDeployment:
    """The addresses one deployment is identified by."""

    cluster: SolanaCluster | Literal["custom"]
    #: ``kash_market`` program id.
    program_id: Pubkey
    #: The USDC mint the program was compiled against (6 decimals on every cluster).
    usdc_mint: Pubkey


@dataclass(frozen=True, slots=True)
class CustomSolanaDeployment:
    """Overrides for a deployment the registry does not list (a local validator, a fork)."""

    program_id: Pubkey | str
    usdc_mint: Pubkey | str


def is_solana_cluster(value: object) -> TypeGuard[SolanaCluster]:
    """Whether ``value`` names a cluster with a Kash deployment."""
    return isinstance(value, str) and value in SOLANA_PROGRAM_IDENTITIES


def _to_pubkey(value: Pubkey | str, field: str) -> Pubkey:
    if isinstance(value, Pubkey):
        return value
    try:
        return Pubkey.from_string(value)
    except (ValueError, TypeError) as cause:
        raise KashConfigError(
            f"{field} is not a valid Solana address",
            code=ErrorCode.INVALID_CONFIG,
            context={"field": field, "value": value},
            cause=cause,
        ) from cause


def get_solana_deployment(
    cluster: SolanaCluster | CustomSolanaDeployment = DEFAULT_SOLANA_CLUSTER,
) -> SolanaDeployment:
    """Resolve a cluster name (or explicit addresses) to its deployment.

    Raises
    ------
    KashConfigError
        ``UNSUPPORTED_CHAIN`` for a cluster with no Kash deployment,
        ``INVALID_CONFIG`` for a malformed custom address.
    """
    if isinstance(cluster, CustomSolanaDeployment):
        return SolanaDeployment(
            cluster="custom",
            program_id=_to_pubkey(cluster.program_id, "program_id"),
            usdc_mint=_to_pubkey(cluster.usdc_mint, "usdc_mint"),
        )
    if not is_solana_cluster(cluster):
        raise KashConfigError(
            f'Kash is not deployed on Solana cluster "{cluster}"',
            code=ErrorCode.UNSUPPORTED_CHAIN,
            context={"cluster": cluster, "supported": list(SOLANA_CLUSTERS)},
        )
    identity = SOLANA_PROGRAM_IDENTITIES[cluster]
    return SolanaDeployment(
        cluster=cluster,
        program_id=Pubkey.from_string(identity["market_program_id"]),
        usdc_mint=Pubkey.from_string(identity["usdc_mint"]),
    )


__all__ = [
    "DEFAULT_SOLANA_CLUSTER",
    "SOLANA_CLUSTERS",
    "SOLANA_PROGRAM_IDENTITIES",
    "CustomSolanaDeployment",
    "SolanaCluster",
    "SolanaDeployment",
    "get_solana_deployment",
    "is_solana_cluster",
]
