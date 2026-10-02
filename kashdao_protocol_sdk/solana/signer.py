"""Solana signers. The SDK never holds key material.

Mirrors ``src/solana/signer.ts``. A signer is anything that can name its
public key and sign a v0 transaction message — a local solders
:class:`~solders.keypair.Keypair` (bots, market makers, scripts) via
:func:`keypair_signer`, or any custom implementation of
:class:`SolanaSigner` (a KMS/HSM, a remote signing service). The client
compiles the message, asks every required signer for its signature, and
assembles the transaction itself, so signers never see or mutate one
another's signatures.

The TypeScript SDK's ``walletAdapterSigner`` (browser wallets) has no
Python counterpart: there is no wallet-adapter ecosystem to adapt.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature


@runtime_checkable
class SolanaSigner(Protocol):
    """What the client needs from whoever authorises a transaction."""

    @property
    def pubkey(self) -> Pubkey: ...

    async def sign_message(self, message: bytes) -> Signature:
        """Sign the serialized (versioned) transaction message."""
        ...


class KeypairSigner:
    """A local solders ``Keypair`` as a :class:`SolanaSigner`."""

    __slots__ = ("_keypair",)

    def __init__(self, keypair: Keypair) -> None:
        self._keypair = keypair

    @property
    def pubkey(self) -> Pubkey:
        return self._keypair.pubkey()

    async def sign_message(self, message: bytes) -> Signature:
        return self._keypair.sign_message(message)

    def __repr__(self) -> str:
        return f"KeypairSigner(pubkey={self.pubkey})"


def keypair_signer(keypair: Keypair) -> KeypairSigner:
    """A local ``Keypair`` as a signer."""
    return KeypairSigner(keypair)


__all__ = ["KeypairSigner", "SolanaSigner", "keypair_signer"]
