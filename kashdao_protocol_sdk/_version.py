"""Single source of truth for the package version.

Hatchling reads this via `[tool.hatch.version]` in `pyproject.toml`.

Version policy: lockstep with `@kashdao/protocol-sdk` while we are in 0.x.
A `0.1.0b1` (beta) release goes out alongside the protocol-sdk first
mainnet-stable tag. `0.1.0` (GA) is gated on Kash mainnet factory
deployment plus at least one external integrator running for >= 30 days.
"""

__version__ = "0.1.0b1"
