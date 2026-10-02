"""Program-derived addresses for the Kash market program.

Mirrors the TS SDK's ``program.ts`` + vendored ``protocol-adapter-solana``
``pda.ts``. Seeds are the program's own (``crates/kash-types/src/seeds.rs``):
``config``, ``counter``, ``template`` + u16 LE, ``market`` + u64 LE,
``collateral`` + u64 LE, ``position`` + market + owner + u8 outcome, and
Anchor's ``__event_authority``. Only the market-program seeds are surfaced;
``kash_groups`` is out of scope (paused on mainnet).

An id outside its seed's integer width is refused, never wrapped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from solders.pubkey import Pubkey

from kashdao_protocol_sdk.shared.errors import KashValidationError
from kashdao_protocol_sdk.solana.idl import EVENT_AUTHORITY_SEED

_CONFIG: Final = b"config"
_COUNTER: Final = b"counter"
_TEMPLATE: Final = b"template"
_MARKET: Final = b"market"
_COLLATERAL: Final = b"collateral"
_POSITION: Final = b"position"


@dataclass(frozen=True, slots=True)
class SolanaPda:
    """A derived program address and its bump seed."""

    address: Pubkey
    bump: int


def _uint_le(value: int, width: int, field: str) -> bytes:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < (1 << (8 * width)):
        raise KashValidationError(f"u{8 * width} out of range", field=field, value=str(value))
    return value.to_bytes(width, "little")


class KashMarketPdas:
    """PDA derivations bound to one deployment's ``kash_market`` program id."""

    __slots__ = ("program_id",)

    def __init__(self, program_id: Pubkey) -> None:
        self.program_id = program_id

    def _derive(self, seeds: list[bytes]) -> SolanaPda:
        address, bump = Pubkey.find_program_address(seeds, self.program_id)
        return SolanaPda(address=address, bump=bump)

    def config(self) -> SolanaPda:
        """The singleton ``Config`` account."""
        return self._derive([_CONFIG])

    def counter(self) -> SolanaPda:
        """The ``MarketCounter`` (``next_market_id``)."""
        return self._derive([_COUNTER])

    def template(self, template_id: int) -> SolanaPda:
        """A market ``Template`` by its u16 id."""
        return self._derive([_TEMPLATE, _uint_le(template_id, 2, "template_id")])

    def market(self, market_id: int) -> SolanaPda:
        """A ``Market`` by its u64 id."""
        return self._derive([_MARKET, _uint_le(market_id, 8, "market_id")])

    def collateral(self, market_id: int) -> SolanaPda:
        """The market's USDC collateral token account."""
        return self._derive([_COLLATERAL, _uint_le(market_id, 8, "market_id")])

    def position(self, market: Pubkey, owner: Pubkey, outcome: int) -> SolanaPda:
        """One owner's ``Position`` in one outcome of one market."""
        if isinstance(outcome, bool) or not isinstance(outcome, int) or not 0 <= outcome <= 0xFF:
            raise KashValidationError("outcome out of range", field="outcome", value=outcome)
        return self._derive([_POSITION, bytes(market), bytes(owner), bytes([outcome])])

    def event_authority(self) -> SolanaPda:
        """Anchor's ``__event_authority`` for emitted CPI events."""
        return self._derive([EVENT_AUTHORITY_SEED])


def kash_market_pdas(program_id: Pubkey) -> KashMarketPdas:
    """Bind the market-program derivations to a program id."""
    return KashMarketPdas(program_id)


__all__ = ["KashMarketPdas", "SolanaPda", "kash_market_pdas"]
