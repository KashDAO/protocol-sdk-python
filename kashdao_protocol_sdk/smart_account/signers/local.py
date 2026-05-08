"""``LocalSigner`` — local-key SA signer backed by ``eth_account``.

Mirrors ``src/smart-account/signers/viem-account.ts``.

Implements both the required :py:meth:`sign_user_op_hash` (raw EIP-191
hash path) and the optional :py:meth:`sign_typed_data_v4` (EIP-712
path). Suitable for local-key dev work, Privy headless integrations
that surface a local key, and any consumer who wants the absolute
simplest signing path.

For HSM / Fireblocks / web3signer integrations that don't surface a
local key, see :class:`JsonRpcSigner` or implement
:class:`SmartAccountSignerAdapter` yourself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_account.signers.local import LocalAccount

from kashdao_protocol_sdk.shared.error_codes import ErrorCode
from kashdao_protocol_sdk.shared.errors import KashSignerError
from kashdao_protocol_sdk.shared.types import Hex
from kashdao_protocol_sdk.smart_account.types import UserOpTypedData


@dataclass(slots=True)
class LocalSigner:
    """In-memory SA signer.

    Construct via :meth:`from_private_key` or :meth:`from_account`.

    >>> signer = LocalSigner.from_private_key("0x" + "01" * 32)
    >>> isinstance(signer.owner_address, str)
    True
    """

    _account: LocalAccount

    @property
    def owner_address(self) -> Hex:
        return self._account.address

    async def sign_user_op_hash(self, user_op_hash: Hex) -> Hex:
        """Sign the raw UserOp hash via EIP-191 ``personal_sign``.

        ``SimpleAccount.validateUserOp`` expects the EIP-191 prefixed
        digest (``\\x19Ethereum Signed Message:\\n32`` || hash). We
        delegate to ``eth_account.Account.sign_message(encode_defunct
        (hash))`` which produces exactly that.

        The CPU-bound signing call runs in the default executor so
        long-running event loops (Hummingbot strategies, watchers)
        keep ticking while a signature is computed.
        """
        loop = asyncio.get_running_loop()
        try:
            signed = await loop.run_in_executor(
                None,
                self._sign_message_blocking,
                user_op_hash,
            )
        except Exception as cause:
            raise KashSignerError(
                "local signer failed to sign UserOp hash",
                code=ErrorCode.SIGNER_HASH_FAILED,
                context={"owner_address": self._account.address},
                cause=cause,
            ) from cause
        return self._format_signature(signed)

    def _sign_message_blocking(self, user_op_hash: Hex) -> Any:
        message = encode_defunct(hexstr=user_op_hash)
        return self._account.sign_message(message)

    async def sign_typed_data_v4(self, typed_data: UserOpTypedData) -> Hex:
        """EIP-712 typed-data signing.

        For SA verification this only matters when the consumer has
        opted out of the staleness guard via
        ``SubmitOptions(skip_staleness_check=True)`` AND their
        SimpleAccount implementation accepts EIP-712 signatures.
        """
        loop = asyncio.get_running_loop()
        try:
            signed = await loop.run_in_executor(
                None,
                self._sign_typed_data_blocking,
                typed_data,
            )
        except Exception as cause:
            raise KashSignerError(
                "local signer failed to sign typed data",
                code=ErrorCode.SIGNER_TYPED_DATA_FAILED,
                context={"owner_address": self._account.address},
                cause=cause,
            ) from cause
        return self._format_signature(signed)

    def _sign_typed_data_blocking(self, typed_data: UserOpTypedData) -> Any:
        message = encode_typed_data(
            domain_data=dict(typed_data.domain),
            message_types={k: list(v) for k, v in typed_data.types.items()},
            message_data=typed_data.message.model_dump(by_alias=True),
        )
        return self._account.sign_message(message)

    def _format_signature(self, signed: Any) -> Hex:
        """``eth_account`` returns ``SignedMessage.signature`` as bytes.

        Format as a 0x-prefixed 130-hex-char string (65 bytes — r/s/v).
        """
        sig = getattr(signed, "signature", None)
        if sig is None:
            raise KashSignerError(
                "local signer produced no signature bytes",
                code=ErrorCode.SIGNER_HASH_FAILED,
                context={"owner_address": self._account.address},
            )
        return _bytes_to_0x_hex(bytes(sig))

    @classmethod
    def from_private_key(cls, private_key: bytes | str) -> LocalSigner:
        """Build from a 32-byte private key (bytes) or hex string."""
        return cls(_account=Account.from_key(private_key))

    @classmethod
    def from_account(cls, account: LocalAccount) -> LocalSigner:
        """Wrap an existing :class:`eth_account.signers.local.LocalAccount`."""
        return cls(_account=account)


def viem_account_signer(account: LocalAccount) -> LocalSigner:
    """Mirror of TS ``viemAccountSigner(account)``.

    Provided so cross-language docs read the same. Returns a
    :class:`LocalSigner` wrapping the given account.
    """
    return LocalSigner.from_account(account)


def _bytes_to_0x_hex(value: bytes) -> Hex:
    return "0x" + value.hex()


__all__ = ["LocalSigner", "viem_account_signer"]
