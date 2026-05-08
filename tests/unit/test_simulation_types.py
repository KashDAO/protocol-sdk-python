"""Pin the public ``SimulationResult`` shape.

* ``SimulationSuccess`` has no ``gas_estimate`` field — gas estimation is a separate ``eth_estimateGas`` round-trip handled by ``prepare_*`` (matches TS reference).
* ``SimulationFailure.decoded_error`` is the typed :class:`SimulationDecodedError`, not an untyped ``dict[str, Any]``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kashdao_protocol_sdk import (
    SimulationDecodedError,
    SimulationFailure,
    SimulationSuccess,
)


class TestSimulationSuccess:
    def test_no_gas_estimate_field(self) -> None:
        success = SimulationSuccess()
        # Field must NOT exist (was a divergence from TS).
        assert not hasattr(success, "gas_estimate")
        # Pydantic forbids extra fields, so this should fail.
        with pytest.raises(ValidationError):
            SimulationSuccess(gas_estimate=21_000)  # type: ignore[call-arg]

    def test_will_succeed_is_literal_true(self) -> None:
        assert SimulationSuccess().will_succeed is True


class TestSimulationFailure:
    def test_decoded_error_is_typed(self) -> None:
        decoded = SimulationDecodedError(name="Slippage", args=(123, 456))
        failure = SimulationFailure(
            revert_reason="Slippage",
            decoded_error=decoded,
        )
        assert isinstance(failure.decoded_error, SimulationDecodedError)
        assert failure.decoded_error.name == "Slippage"
        assert failure.decoded_error.args == (123, 456)

    def test_decoded_error_can_be_none(self) -> None:
        failure = SimulationFailure(revert_reason="raw")
        assert failure.decoded_error is None

    def test_dict_shape_coerced_into_typed_model(self) -> None:
        """Pydantic v2 will coerce a matching dict into the typed model.

        That's fine — the field annotation is the binding contract; the
        value the consumer holds is always a :class:`SimulationDecodedError`,
        never a raw dict. (Pydantic's coercion is the win — consumer
        code that has already migrated still type-checks against the
        new model.)
        """
        failure = SimulationFailure(
            revert_reason="x",
            decoded_error={"name": "X", "args": [1, 2]},  # type: ignore[arg-type]
        )
        assert isinstance(failure.decoded_error, SimulationDecodedError)
        assert failure.decoded_error.name == "X"
        assert failure.decoded_error.args == (1, 2)

    def test_will_succeed_is_literal_false(self) -> None:
        assert SimulationFailure(revert_reason="x").will_succeed is False
