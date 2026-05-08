"""ABI loader.

Reads the vendored JSON ABIs in ``generated/`` and exposes them as
Python lists ready for ``web3.py`` / ``eth_abi`` consumption. The
``importlib.resources`` lookup means the ABIs ship inside the wheel
and survive ``pip install``.

The vendored JSON is pinned at release time; a CI drift gate prevents
divergence from the upstream contract source.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, cast

# The package data root for vendored ABIs (matches TS layout
# ``src/shared/contracts/generated/``).
_ABI_PACKAGE = "kashdao_protocol_sdk.shared.contracts.generated"


def _load(name: str) -> list[dict[str, Any]]:
    """Load and parse a single vendored ABI JSON file."""
    with resources.files(_ABI_PACKAGE).joinpath(name).open("rb") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise RuntimeError(f"vendored ABI {name!r} is not a JSON array; the wheel may be corrupt")
    return cast(list[dict[str, Any]], data)


# Loaded eagerly at import time. The wheel ships the JSON files; if any
# is missing the failure is loud and immediate at import, never at the
# trade-build hot path.
MARKET_ABI: list[dict[str, Any]] = _load("market.json")
MARKET_FACTORY_ABI: list[dict[str, Any]] = _load("market_factory.json")
ORACLE_ABI: list[dict[str, Any]] = _load("oracle.json")
VAULT_ABI: list[dict[str, Any]] = _load("vault.json")
ERC20_ABI: list[dict[str, Any]] = _load("erc20.json")
ERC1155_OUTCOME_TOKENS_ABI: list[dict[str, Any]] = _load("erc1155_outcome_tokens.json")
PARAM_REGISTRY_ABI: list[dict[str, Any]] = _load("param_registry.json")
MULTICALL3_ABI: list[dict[str, Any]] = _load("multicall3.json")
ENTRY_POINT_07_ABI: list[dict[str, Any]] = _load("entry_point_07.json")
SIMPLE_ACCOUNT_ABI: list[dict[str, Any]] = _load("simple_account.json")
SIMPLE_ACCOUNT_FACTORY_ABI: list[dict[str, Any]] = _load("simple_account_factory.json")


__all__ = [
    "ENTRY_POINT_07_ABI",
    "ERC20_ABI",
    "ERC1155_OUTCOME_TOKENS_ABI",
    "MARKET_ABI",
    "MARKET_FACTORY_ABI",
    "MULTICALL3_ABI",
    "ORACLE_ABI",
    "PARAM_REGISTRY_ABI",
    "SIMPLE_ACCOUNT_ABI",
    "SIMPLE_ACCOUNT_FACTORY_ABI",
    "VAULT_ABI",
]
