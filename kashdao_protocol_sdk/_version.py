"""Single source of truth for the package version.

Hatchling reads this via `[tool.hatch.version]` in `pyproject.toml`.

Version policy: lockstep with `@kashdao/protocol-sdk` while we are in 0.x.
`0.1.0b2` adds Base mainnet (8453) support now that the Kash protocol is
deployed there. `0.1.0` (GA) remains gated on at least one external
integrator running on mainnet for >= 30 days.
"""

__version__ = "0.1.0b2"
