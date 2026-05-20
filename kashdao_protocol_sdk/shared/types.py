"""Mode-agnostic types shared by both EOA and Smart Account modes.

Mirrors the shared portion of ``src/shared/types.ts``.

Mode-specific types (transaction shapes for EOA, UserOp shapes for SA)
live under their respective sub-packages:

- :mod:`kashdao_protocol_sdk.eoa.types` — ``UnsignedTransaction``,
  ``BuiltTransaction``, ``EoaSignerAdapter`` and lifecycle option types.
- :mod:`kashdao_protocol_sdk.smart_account.types` — ``UnsignedUserOp``,
  ``BuiltUserOp``, ``UserOpTypedData``, ``SmartAccountSignerAdapter``
  and lifecycle option types.

We use Pydantic v2 models for runtime-validated payloads and
``typing.Protocol`` for adapter contracts. Numeric ``uint256`` values
are held as Python ``int`` (arbitrary precision); conversion to / from
hex strings happens only at marshalling boundaries.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

# 0x-prefixed lowercase hex string. The alias stays loose (str) because
# web3.py / eth_account / eth_abi all accept plain ``str`` — wrapping in
# a NewType would force consumer-side casts everywhere with no payoff.
Hex = str

#: Market lifecycle status (mirrors TS ``MarketStatus`` literal union).
MarketStatus = Literal["unseeded", "active", "frozen", "resolved"]

#: Trade direction.
QuoteSide = Literal["BUY", "SELL"]


# ---------------------------------------------------------------------------
# Build params (shared by both modes)
# ---------------------------------------------------------------------------
#
# Note on the ``smart_account`` field name: in EOA mode this is the
# trading EOA's address (since EOAs trade for themselves); in SA mode
# it's the smart account address. The mode-neutral name comes from the
# TS surface and is preserved here for parity. Treat it as "the address
# that pays USDC and receives outcome tokens".


#: Field alias accepting both the TS-parity name ``account`` and the
#: legacy ``smart_account`` name. The canonical attribute is
#: ``account`` (mode-polymorphic: the EOA in EOA mode, the SA in SA
#: mode). The legacy ``smart_account=`` keyword on the constructor
#: remains accepted via Pydantic ``AliasChoices`` so existing
#: customer code keeps working through this deprecation window.
_ACCOUNT_ALIAS = AliasChoices("account", "smart_account")


class BuildBuyParams(BaseModel):
    """Parameters for a BUY trade build.

    .. note::
       The ``account`` field accepts the legacy ``smart_account=``
       keyword as well, matching the original Python-only name. New
       code should pass ``account=`` to match the TS SDK
       (``BuildBuyParams.account`` there). The legacy name is
       slated for removal in a future minor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    account: Hex = Field(validation_alias=_ACCOUNT_ALIAS)
    outcome: int
    amount_usdc: int
    max_slippage_bps: int
    deadline: int | None = None


class BuildSellParams(BaseModel):
    """Parameters for a SELL trade build.

    See :class:`BuildBuyParams` for the ``account`` field aliasing note.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    account: Hex = Field(validation_alias=_ACCOUNT_ALIAS)
    outcome: int
    amount_tokens: int
    max_slippage_bps: int
    deadline: int | None = None


class BuildClosePositionParams(BaseModel):
    """Parameters for a close-position trade build (SELLs full balance).

    See :class:`BuildBuyParams` for the ``account`` field aliasing note.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    account: Hex = Field(validation_alias=_ACCOUNT_ALIAS)
    outcome: int
    max_slippage_bps: int
    deadline: int | None = None


class BuildApproveParams(BaseModel):
    """Parameters for an ERC-20 approve calldata build.

    USDC approval is required once before the first BUY in EOA mode. In
    SA mode, approval can be batched into the trade UserOp via
    ``SimpleAccount.executeBatch`` (planned).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    account: Hex
    spender: Hex
    amount: int


# ---------------------------------------------------------------------------
# Market reads
# ---------------------------------------------------------------------------


class Quote(BaseModel):
    """Quote returned by ``client.markets.quote()``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    side: QuoteSide
    outcome_index: int
    amount_in: int
    amount_out: int
    reserve_after_wad: int
    prices_after_wad: tuple[int, ...]


class MarketOutcomeState(BaseModel):
    """Single-outcome state inside :class:`MarketState`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    outstanding_tokens_wad: int
    weight_wad: int
    probability: float


class MarketIdentity(BaseModel):
    """On-chain identity + lifecycle fields surfaced by :class:`MarketState`.

    Lets UIs show "Market #12345" and "resolves in 5h" without needing
    the off-chain @kashdao/sdk metadata layer. Mirrors the TS SDK's
    ``MarketState.identity`` sub-object byte-for-byte.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: uint256 market id assigned by the factory at creation.
    market_id: int
    #: uint256 category bucket id (set at creation; UI grouping).
    category_id: int
    #: Unix-seconds timestamp of market creation.
    created_at: int
    #: Unix-seconds timestamp at which trading freezes. ``0`` means no
    #: scheduled freeze (resolution-time only).
    freeze_time: int
    #: Unix-seconds timestamp at which the oracle resolves the market.
    resolve_time: int


class MarketState(BaseModel):
    """Aggregated on-chain market state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market_address: Hex
    outcomes: tuple[MarketOutcomeState, ...]
    reserve_wad: int
    status: MarketStatus
    read_at: int
    #: On-chain identity + lifecycle (market_id, category_id, timestamps).
    identity: MarketIdentity


class MinimalMarketRead(BaseModel):
    """Lightweight projection from :func:`get_market_minimal`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market_address: Hex
    num_outcomes: int
    status: MarketStatus


# ---------------------------------------------------------------------------
# Account / position
# ---------------------------------------------------------------------------


class OutcomePosition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome_index: int
    balance_wad: int


class Position(BaseModel):
    """Account's position across all outcomes of a market.

    ``holdings`` is the ordered list (length = num_outcomes); ``by_outcome``
    is a random-access map keyed by outcome index. Both views are
    populated; consumers pick whichever shape fits their access pattern.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    market_address: Hex
    num_outcomes: int
    holdings: tuple[OutcomePosition, ...]
    by_outcome: dict[int, OutcomePosition]


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


class SimulationSuccess(BaseModel):
    """Successful pre-flight simulation.

    Mirrors TS ``{ willSucceed: true }``. No gas estimate — gas estimation
    is a separate ``eth_estimateGas`` round-trip and is performed by the
    ``prepare_*`` lifecycle when ``gas`` is not supplied.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    will_succeed: Literal[True] = True


class SimulationDecodedError(BaseModel):
    """Decoded custom error from a Market / EntryPoint revert.

    Mirrors TS ``{ name: string; args: readonly unknown[] }``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    args: tuple[Any, ...]


class SimulationFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    will_succeed: Literal[False] = False
    revert_reason: str
    decoded_error: SimulationDecodedError | None = None


SimulationResult = SimulationSuccess | SimulationFailure


__all__ = [
    "BuildApproveParams",
    "BuildBuyParams",
    "BuildClosePositionParams",
    "BuildSellParams",
    "Hex",
    "MarketOutcomeState",
    "MarketState",
    "MarketStatus",
    "MinimalMarketRead",
    "SimulationDecodedError",
    "OutcomePosition",
    "Position",
    "Quote",
    "QuoteSide",
    "SimulationFailure",
    "SimulationResult",
    "SimulationSuccess",
]
