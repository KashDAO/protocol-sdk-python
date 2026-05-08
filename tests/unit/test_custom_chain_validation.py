"""``parse_eoa_client_config`` chain-id consistency validation.

Pins C-NEW-1 — a mismatched ``EoaClientConfig.chain_id`` and
``CustomChain.chain_id`` is a financial-path bug: signed transactions
target the top-level ``chain_id`` while the addresses come from the
``CustomChain`` deploy. Submission either reverts at the chain or
(worse) the tx replays on a chain where the addresses happen to overlap.
"""

from __future__ import annotations

import pytest

from kashdao_protocol_sdk import (
    CustomChain,
    CustomChainAddresses,
    EoaClientConfig,
    KashConfigError,
    LocalEoaSigner,
)
from kashdao_protocol_sdk.eoa.config import parse_eoa_client_config

_TEST_KEY = "0x" + "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _custom_chain(chain_id: int) -> CustomChain:
    return CustomChain(
        name="anvil-fork",
        chain_id=chain_id,
        addresses=CustomChainAddresses(
            factory="0x" + "11" * 20,
            usdc="0x" + "22" * 20,
        ),
    )


class TestChainIdConsistency:
    def test_matching_chain_ids_accepted(self) -> None:
        config = EoaClientConfig(
            chain_id=31337,
            rpc="http://localhost:8545",
            signer=LocalEoaSigner.from_private_key(_TEST_KEY),
            custom_chain=_custom_chain(31337),
        )
        # No raise.
        result = parse_eoa_client_config(config)
        assert result is config

    def test_mismatched_chain_ids_rejected(self) -> None:
        """A mismatch must surface a precise CHAIN_ID_MISMATCH error.

        Without this guard, signed txs would target ``chain_id=8453``
        (mainnet) while reading addresses from a 31337 (Anvil)
        deployment — a financial-path bug.
        """
        config = EoaClientConfig(
            chain_id=8453,  # mainnet
            rpc="https://my-mainnet-rpc.example.com",
            signer=LocalEoaSigner.from_private_key(_TEST_KEY),
            custom_chain=_custom_chain(31337),  # local fork
        )
        with pytest.raises(KashConfigError) as exc_info:
            parse_eoa_client_config(config)
        assert exc_info.value.code == "CHAIN_ID_MISMATCH"
        ctx = exc_info.value.context
        assert ctx is not None
        assert ctx["chain_id"] == 8453
        assert ctx["custom_chain_id"] == 31337
        assert ctx["custom_chain_name"] == "anvil-fork"
