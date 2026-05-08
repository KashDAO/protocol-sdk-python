"""Regression tests for the ``send.*`` option-propagation contract.

Pins:
* ``simulate=False`` propagates verbatim instead of being collapsed to ``True``.
* ``SendEoaOptions.simulate`` defaults to ``True`` (matches TS reference).
* ``SendEoaOptions.submit`` lets the caller opt out of the staleness guard via ``EoaSubmitOptions(skip_staleness_check=True)``.
"""

from __future__ import annotations

from kashdao_protocol_sdk import (
    EoaSubmitOptions,
    PrepareEoaOptions,
    SendEoaOptions,
)
from kashdao_protocol_sdk.eoa.trades.send import _to_prepare_options


class TestSimulateDefault:
    def test_default_is_true_for_send(self) -> None:
        """Send-path mirror of TS — simulate ON by default."""
        assert SendEoaOptions().simulate is True

    def test_default_is_false_for_prepare(self) -> None:
        """Prepare path is opt-in (TS parity)."""
        assert PrepareEoaOptions().simulate is False


class TestPropagation:
    def test_simulate_false_propagates(self) -> None:
        """Caller's ``simulate=False`` MUST reach the prepare layer.

        The original send-path bug collapsed both branches to ``True``.
        Without this test, every consumer paid an extra ``eth_call``
        per tick.
        """
        opts = SendEoaOptions(simulate=False)
        prep = _to_prepare_options(opts)
        assert prep.simulate is False

    def test_simulate_true_propagates(self) -> None:
        opts = SendEoaOptions(simulate=True)
        prep = _to_prepare_options(opts)
        assert prep.simulate is True

    def test_default_simulate_propagates_true(self) -> None:
        prep = _to_prepare_options(SendEoaOptions())
        assert prep.simulate is True

    def test_gas_overrides_propagate(self) -> None:
        opts = SendEoaOptions(gas=123_456, nonce=7)
        prep = _to_prepare_options(opts)
        assert prep.gas == 123_456
        assert prep.nonce == 7


class TestSubmitOpts:
    def test_default_submit_is_none(self) -> None:
        assert SendEoaOptions().submit is None

    def test_caller_submit_opts_pass_through_field(self) -> None:
        """The send path forwards the caller-supplied submit opts."""
        submit = EoaSubmitOptions(skip_staleness_check=True)
        opts = SendEoaOptions(submit=submit)
        assert opts.submit is submit
        assert opts.submit.skip_staleness_check is True
