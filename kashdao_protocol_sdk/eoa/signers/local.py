"""``LocalEoaSigner`` — local-key EOA signer backed by ``eth_account``.

Mirrors ``src/eoa/signers/viem-account.ts``.

Wraps an ``eth_account.Account`` (or anything with the same
``sign_transaction`` shape) into the :class:`EoaSignerAdapter` Protocol.
Suitable for local-key dev, hot-wallet trading bots, and any
deployment where the consumer is comfortable holding the key in
memory.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from eth_account import Account
from eth_account.signers.local import LocalAccount

from kashdao_protocol_sdk.eoa.types import UnsignedTransaction
from kashdao_protocol_sdk.shared.types import Hex


@dataclass(slots=True)
class LocalEoaSigner:
    """In-memory EOA signer.

    Construct via :meth:`from_private_key` or :meth:`from_account`.

    >>> signer = LocalEoaSigner.from_private_key("0x" + "01" * 32)
    >>> isinstance(signer.owner_address, str)
    True
    """

    _account: LocalAccount

    @property
    def owner_address(self) -> Hex:
        return self._account.address

    async def sign_transaction(self, transaction: UnsignedTransaction) -> Hex:
        """Sign and return a serialized EIP-1559 (type-0x02) transaction.

        ``eth_account.Account.sign_transaction`` is synchronous, CPU-
        bound (keccak + secp256k1). Run it on the default executor so
        long-running event loops (Hummingbot strategies, watch
        subscriptions) keep ticking while a signature is computed.
        """
        loop = asyncio.get_running_loop()
        signed = await loop.run_in_executor(
            None,
            self._sign_blocking,
            transaction,
        )
        # eth_account returns SignedTransaction.raw_transaction (HexBytes).
        # Older versions used `.rawTransaction`; support both attribute names.
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
        if raw is None:
            raise AttributeError(
                "eth_account.SignedTransaction did not expose raw_transaction / rawTransaction; "
                "update eth_account to a supported version."
            )
        return str(raw.to_0x_hex()) if hasattr(raw, "to_0x_hex") else str(raw.hex())

    def _sign_blocking(self, transaction: UnsignedTransaction) -> object:
        """Perform the synchronous signing call. Used via ``run_in_executor``."""
        # eth_account's sign_transaction accepts a wider dict shape than
        # its TypedDict declares; the runtime accepts our shape fine.
        return self._account.sign_transaction(_to_eth_account_dict(transaction))  # type: ignore[arg-type]

    @classmethod
    def from_private_key(cls, private_key: bytes | str) -> LocalEoaSigner:
        """Build from a 32-byte private key (bytes) or hex string."""
        return cls(_account=Account.from_key(private_key))

    @classmethod
    def from_account(cls, account: LocalAccount) -> LocalEoaSigner:
        """Wrap an existing :class:`eth_account.signers.local.LocalAccount`."""
        return cls(_account=account)


def viem_account_eoa_signer(account: LocalAccount) -> LocalEoaSigner:
    """Mirror of TS ``viemAccountEoaSigner(account)``.

    Provided so cross-language docs read the same. Returns a
    :class:`LocalEoaSigner` wrapping the given account.
    """
    return LocalEoaSigner.from_account(account)


def _to_eth_account_dict(transaction: UnsignedTransaction) -> dict[str, object]:
    """Convert :class:`UnsignedTransaction` to the dict shape ``Account.sign_transaction`` expects.

    eth_account's typed-tx validator rejects lowercase hex addresses
    (`Transaction had invalid fields: {'to': '0x...'}`) — it requires
    a checksum-cased string or raw bytes. We checksum here so callers
    can keep passing lowercase hex through the SDK boundary.
    """
    # `eth_utils.address` is the explicit-export module — mypy strict
    # rejects `from eth_utils import to_checksum_address` because the
    # top-level `__init__` doesn't list it in `__all__`.
    from eth_utils.address import (
        to_checksum_address,  # local import: hot path is signing, not module load
    )

    return {
        "type": 2,
        "chainId": transaction.chain_id,
        "nonce": transaction.nonce,
        "to": to_checksum_address(transaction.to),
        "value": transaction.value,
        "data": transaction.data,
        "gas": transaction.gas,
        "maxFeePerGas": transaction.max_fee_per_gas,
        "maxPriorityFeePerGas": transaction.max_priority_fee_per_gas,
    }


__all__ = ["LocalEoaSigner", "viem_account_eoa_signer"]
