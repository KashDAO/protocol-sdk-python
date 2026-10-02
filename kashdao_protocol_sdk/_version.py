"""Single source of truth for the package version.

Hatchling reads this via `[tool.hatch.version]` in `pyproject.toml`.

Version policy: versioned independently of `@kashdao/protocol-sdk`
(TypeScript), as RELEASING.md states. `0.2.0b1` adds the Solana client
(`kashdao_protocol_sdk.solana`, installed with the `[solana]` extra),
mainnet-beta by default, alongside the unchanged Base support. `0.x` minor
versions may carry breaking changes; `1.0.0` (GA) remains gated on at least
one external integrator running on mainnet for >= 30 days.
"""

__version__ = "0.2.0b1"
