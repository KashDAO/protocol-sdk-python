"""EOA-mode smoke tests against a live Base Sepolia market.

These tests **build** and **simulate** a BUY but never submit. The
suite is gated by ``@pytest.mark.integration`` and skips cleanly
without the required environment variables — see
``tests/integration/conftest.py`` for the env-var contract.
"""

from __future__ import annotations

import pytest

from kashdao_protocol_sdk import (
    BuildBuyParams,
    EoaClient,
    LocalEoaSigner,
    SimulationFailure,
    SimulationSuccess,
    usdc,
)

#: Conservative slippage for the smoke test. 50 bps = 0.5%.
_SLIPPAGE_BPS = 50

#: Small (1 USDC) build amount keeps the simulated trade well below
#: any reasonable market reserve so any failure is informative.
_BUILD_AMOUNT_USDC = 1


@pytest.mark.integration
class TestEoaBuildAndSimulate:
    """Build a BUY, assert the EIP-1559 envelope, then simulate it.

    These checks pin two invariants that any first-time integrator
    needs working:

    1. The transaction structure produced by ``build_buy`` matches the
       chain (chain_id, market address) and is fully populated by
       ``prepare_buy`` (gas / fees / nonce non-zero).
    2. ``simulate`` returns a discriminated result we can branch on
       — :class:`SimulationSuccess` is the happy path; a
       :class:`SimulationFailure` here surfaces the canonical
       contract revert (e.g. market frozen, slippage too tight) so
       operators have an actionable error rather than a silent NaN.
    """

    async def test_prepare_buy_produces_valid_eip1559_envelope(
        self,
        eoa_client: EoaClient,
        eoa_signer: LocalEoaSigner,
        test_market_address: str,
    ) -> None:
        built = await eoa_client.trades.prepare_buy(
            test_market_address,
            BuildBuyParams(
                smart_account=eoa_signer.owner_address,
                outcome=0,
                amount_usdc=usdc(_BUILD_AMOUNT_USDC),
                max_slippage_bps=_SLIPPAGE_BPS,
            ),
        )

        tx = built.transaction
        assert tx.chain_id == 84532, "expected Base Sepolia chain id"
        assert tx.to.lower() == test_market_address.lower(), (
            f"tx.to {tx.to} should target the market {test_market_address}"
        )
        assert tx.nonce >= 0, "nonce must be populated by prepare_buy"
        assert tx.gas > 0, "gas must be populated by prepare_buy"
        assert tx.max_fee_per_gas > 0, "max_fee_per_gas must be populated"
        assert tx.max_priority_fee_per_gas > 0, "max_priority_fee_per_gas must be populated"
        assert tx.max_fee_per_gas >= tx.max_priority_fee_per_gas, (
            "EIP-1559 invariant: max_fee_per_gas >= max_priority_fee_per_gas"
        )
        assert tx.data.startswith("0x"), "calldata must be 0x-prefixed hex"
        assert len(tx.data) > 2, "calldata must be non-empty"

    async def test_simulate_buy_returns_discriminated_result(
        self,
        eoa_client: EoaClient,
        eoa_signer: LocalEoaSigner,
        test_market_address: str,
    ) -> None:
        built = await eoa_client.trades.prepare_buy(
            test_market_address,
            BuildBuyParams(
                smart_account=eoa_signer.owner_address,
                outcome=0,
                amount_usdc=usdc(_BUILD_AMOUNT_USDC),
                max_slippage_bps=_SLIPPAGE_BPS,
            ),
        )

        result = await eoa_client.trades.simulate(built.transaction)

        # Either outcome is informative for an integration smoke test:
        # success means the market is tradeable and the EOA has
        # sufficient USDC + allowance; a failure surfaces the canonical
        # contract revert (decoded when the ABI catalog recognises it)
        # so operators see exactly which guardrail tripped.
        assert isinstance(result, SimulationSuccess | SimulationFailure)
        if isinstance(result, SimulationFailure):
            assert result.revert_reason, "failure must carry a revert_reason"
