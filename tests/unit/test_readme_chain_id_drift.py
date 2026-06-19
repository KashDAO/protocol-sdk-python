"""README ↔ source-of-truth drift detector for Python SDK chain ids.

Mirrors the TS-side detector added in round AE
(`packages/protocol-sdk/tests/unit/readme-chain-id-drift.test.ts`).

The Python README at ``packages/protocol-sdk-python/README.md``
advertises the supported chain ids verbatim in its "Chain support"
section:

    | Base mainnet | 8453     | ✅ Live |
    | Base Sepolia | 84532    | ✅ Live |

If ``BASE_SEPOLIA.chain_id`` or ``BASE_MAINNET.chain_id`` in
``shared/contracts/addresses.py`` ever change (a new testnet, a
rebrand), the README MUST update. A regression here is customer-
facing: a Python dev reading "Base Sepolia 84532" but the SDK now
deploys to Base Sepolia v2 with a different chain id will plan their
integration against a chain the SDK rejects.

Same drift class as round AB (api-tiers.md ↔ tier defaults), AC
(error docs ↔ ERROR_CODE_HTTP_STATUS), AD (SDK README ↔ base URLs),
AE (protocol-sdk README ↔ chain-id arrays).
"""

from __future__ import annotations

import re
from pathlib import Path

from kashdao_protocol_sdk.shared.contracts.addresses import (
    BASE_MAINNET,
    BASE_SEPOLIA,
    KNOWN_CHAIN_IDS,
    SUPPORTED_CHAIN_IDS,
)

README_PATH = Path(__file__).resolve().parents[2] / "README.md"
README = README_PATH.read_text(encoding="utf-8")


class TestReadmeChainIdDrift:
    def test_readme_mentions_base_sepolia_chain_id(self) -> None:
        """The README must contain the Sepolia chain id verbatim.

        A drift here breaks every code example in the README — every
        ``CustomChain(chain_id=84532, ...)`` snippet, every chain-support
        table row, every faucet pointer.
        """
        assert str(BASE_SEPOLIA.chain_id) in README, (
            f"README is missing Base Sepolia chain id {BASE_SEPOLIA.chain_id}. "
            "Update README.md or the constant; the constant is the source of truth."
        )

    def test_readme_mentions_base_mainnet_chain_id(self) -> None:
        """Same invariant for mainnet — the README documents Base
        mainnet (8453) as Live and uses ``chain_id=8453`` in examples.
        """
        assert str(BASE_MAINNET.chain_id) in README, (
            f"README is missing Base mainnet chain id {BASE_MAINNET.chain_id}."
        )

    def test_readme_chain_support_table_has_both_rows(self) -> None:
        """Coarse safety net for the table itself — if the table is
        rewritten or moved, this catches the move before customers see
        stale rows.
        """
        sepolia_row_re = re.compile(r"Base\s*Sepolia.+?" + str(BASE_SEPOLIA.chain_id), re.DOTALL)
        mainnet_row_re = re.compile(
            r"Base\s*(?:mainnet|Mainnet).+?" + str(BASE_MAINNET.chain_id) + r"(?!\d)",
            re.DOTALL,
        )
        assert sepolia_row_re.search(README), (
            f"README missing chain-support row for Base Sepolia ({BASE_SEPOLIA.chain_id})."
        )
        assert mainnet_row_re.search(README), (
            f"README missing chain-support row for Base mainnet ({BASE_MAINNET.chain_id})."
        )

    def test_supported_and_known_chain_id_arrays_are_consistent(self) -> None:
        """Source-side sanity invariant the README depends on: every
        SUPPORTED chain id must also appear in KNOWN. A drift here
        would let the SDK accept a chain that isn't even acknowledged
        in the registry — KNOWN must remain a superset of SUPPORTED.
        """
        for chain_id in SUPPORTED_CHAIN_IDS:
            assert chain_id in KNOWN_CHAIN_IDS, (
                f"SUPPORTED_CHAIN_IDS contains {chain_id} but KNOWN_CHAIN_IDS does not. "
                "KNOWN must be a superset of SUPPORTED — see addresses.py docstrings."
            )

    def test_chain_id_registry_keys_match_address_dataclass(self) -> None:
        """The chain-id table in the README is derived from BASE_SEPOLIA
        and BASE_MAINNET objects. The objects in turn carry their
        chain_id as an attribute. If anyone ever reshuffles the
        ProtocolAddresses dataclass so chain_id isn't where the README
        thinks it is, this fails before customers see stale rows.
        """
        # Sanity: the names BASE_SEPOLIA / BASE_MAINNET are themselves
        # part of the customer-facing surface (imported in examples).
        # A rename means the README import snippets break, even if the
        # chain-id values stay the same.
        assert hasattr(BASE_SEPOLIA, "chain_id"), (
            "BASE_SEPOLIA dataclass lost the `chain_id` attribute — README "
            "examples that read `BASE_SEPOLIA.chain_id` will fail."
        )
        assert hasattr(BASE_MAINNET, "chain_id"), (
            "BASE_MAINNET dataclass lost the `chain_id` attribute."
        )
        assert isinstance(BASE_SEPOLIA.chain_id, int)
        assert isinstance(BASE_MAINNET.chain_id, int)
