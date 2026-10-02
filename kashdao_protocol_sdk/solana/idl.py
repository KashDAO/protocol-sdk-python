"""The vendored ``kash_market`` Anchor IDL and a borsh codec driven by it.

Mirrors the TS SDK's vendored ``protocol-adapter-solana`` ``coder.ts`` and
``instruction-builder.ts``: the IDL is the single source of every
discriminator, argument layout, account order and signer/writable flag.
Nothing here is written per instruction.

The IDL (``_vendor/kash_market.json``) is ``@kashdao/program-idls``'s
``idl/kash_market.json`` byte for byte (drift-checked by
``scripts/sync-solana-vendor.py --check``). Its embedded ``address`` is the
canonical build id, which no cluster deploys — builders always take the
cluster's program id explicitly.

Encoding is plain borsh, which is what Anchor's ``BorshCoder`` emits:
little-endian fixed-width integers, 32-byte pubkeys, fixed arrays with no
length prefix, and an 8-byte discriminator in front. Every integer is
bounds-checked against its IDL width, so an out-of-range value is a
:class:`KashValidationError` naming the argument, never a silent wrap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, Final

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

from kashdao_protocol_sdk.shared.errors import KashValidationError

#: ``BPFLoaderUpgradeable`` — owner of every upgradeable program's ``program_data``.
BPF_LOADER_UPGRADEABLE_PROGRAM_ID: Final = Pubkey.from_string(
    "BPFLoaderUpgradeab1e11111111111111111111111"
)

EVENT_AUTHORITY_SEED: Final = b"__event_authority"

_INT_WIDTHS: Final[dict[str, tuple[int, bool]]] = {
    "u8": (8, False),
    "u16": (16, False),
    "u32": (32, False),
    "u64": (64, False),
    "u128": (128, False),
    "u256": (256, False),
    "i8": (8, True),
    "i16": (16, True),
    "i32": (32, True),
    "i64": (64, True),
    "i128": (128, True),
}


@cache
def kash_market_idl() -> dict[str, Any]:
    """The ``kash_market`` IDL, parsed once."""
    raw = (
        resources.files("kashdao_protocol_sdk.solana")
        .joinpath("_vendor/kash_market.json")
        .read_text(encoding="utf-8")
    )
    idl: dict[str, Any] = json.loads(raw)
    return idl


@cache
def program_error_names() -> dict[int, str]:
    """Custom error code (``6000+``) → IDL error name."""
    return {int(e["code"]): str(e["name"]) for e in kash_market_idl().get("errors", [])}


def _find(kind: str, name: str) -> dict[str, Any]:
    for entry in kash_market_idl()[kind]:
        if entry["name"] == name:
            found: dict[str, Any] = entry
            return found
    raise KashValidationError(
        f"IDL kash_market declares no {kind[:-1]} named {name}", field=kind, value=name
    )


def discriminator(kind: str, name: str) -> bytes:
    """The 8-byte discriminator the IDL declares for an instruction, account or event."""
    return bytes(_find(kind, name)["discriminator"])


def _type_def(name: str) -> dict[str, Any]:
    return _find("types", name)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _to_pubkey(value: object, field: str) -> Pubkey:
    if isinstance(value, Pubkey):
        return value
    if isinstance(value, str):
        try:
            return Pubkey.from_string(value)
        except ValueError as cause:
            raise KashValidationError(
                f"{field} is not a Solana pubkey", field=field, value=value, cause=cause
            ) from cause
    raise KashValidationError(f"{field} must be a pubkey", field=field, value=type(value).__name__)


def _bounded(value: object, field: str, bits: int, signed: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KashValidationError(
            f"{field} must be an integer", field=field, value=type(value).__name__
        )
    low = -(1 << (bits - 1)) if signed else 0
    high = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
    if value < low or value > high:
        raise KashValidationError(
            f"{field} is outside {'i' if signed else 'u'}{bits}", field=field, value=str(value)
        )
    return value


def encode_value(idl_type: Any, value: object, field: str) -> bytes:
    """Borsh-encode one value according to its IDL type."""
    if isinstance(idl_type, str):
        if idl_type in _INT_WIDTHS:
            bits, signed = _INT_WIDTHS[idl_type]
            n = _bounded(value, field, bits, signed)
            return n.to_bytes(bits // 8, "little", signed=signed)
        if idl_type == "bool":
            if not isinstance(value, bool):
                raise KashValidationError(f"{field} must be a boolean", field=field)
            return b"\x01" if value else b"\x00"
        if idl_type == "pubkey":
            return bytes(_to_pubkey(value, field))
        raise KashValidationError(f"unsupported IDL scalar {idl_type}", field=field, value=idl_type)
    if "array" in idl_type:
        inner, length = idl_type["array"]
        items = list(value) if isinstance(value, (bytes, bytearray, list, tuple)) else None
        if items is None or len(items) != length:
            raise KashValidationError(
                f"{field} must have exactly {length} elements",
                field=field,
                value=None if items is None else len(items),
            )
        return b"".join(encode_value(inner, item, f"{field}[{i}]") for i, item in enumerate(items))
    if "option" in idl_type:
        if value is None:
            return b"\x00"
        return b"\x01" + encode_value(idl_type["option"], value, field)
    if "defined" in idl_type:
        definition = _type_def(idl_type["defined"]["name"])
        if definition["type"]["kind"] != "struct" or not isinstance(value, dict):
            raise KashValidationError(f"{field} must be a struct", field=field)
        out = b""
        for f in definition["type"]["fields"]:
            if f["name"] not in value:
                raise KashValidationError(
                    f"{field}.{f['name']} is required", field=f"{field}.{f['name']}"
                )
            out += encode_value(f["type"], value[f["name"]], f"{field}.{f['name']}")
        return out
    raise KashValidationError(f"unsupported IDL type for {field}", field=field)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _decode_value(idl_type: Any, data: bytes, offset: int, field: str) -> tuple[Any, int]:
    if isinstance(idl_type, str):
        if idl_type in _INT_WIDTHS:
            bits, signed = _INT_WIDTHS[idl_type]
            end = offset + bits // 8
            _require_length(data, end, field)
            return int.from_bytes(data[offset:end], "little", signed=signed), end
        if idl_type == "bool":
            _require_length(data, offset + 1, field)
            return data[offset] != 0, offset + 1
        if idl_type == "pubkey":
            _require_length(data, offset + 32, field)
            return Pubkey.from_bytes(data[offset : offset + 32]), offset + 32
        raise KashValidationError(f"unsupported IDL scalar {idl_type}", field=field, value=idl_type)
    if "array" in idl_type:
        inner, length = idl_type["array"]
        if inner == "u8":
            end = offset + length
            _require_length(data, end, field)
            return bytes(data[offset:end]), end
        items: list[Any] = []
        for i in range(length):
            item, offset = _decode_value(inner, data, offset, f"{field}[{i}]")
            items.append(item)
        return items, offset
    if "defined" in idl_type:
        return _decode_struct(idl_type["defined"]["name"], data, offset)
    raise KashValidationError(f"unsupported IDL type for {field}", field=field)


def _require_length(data: bytes, end: int, field: str) -> None:
    if len(data) < end:
        raise KashValidationError(
            f"account data ends before {field}",
            field=field,
            value=len(data),
            constraint=f">= {end} bytes",
        )


def _decode_struct(name: str, data: bytes, offset: int) -> tuple[dict[str, Any], int]:
    definition = _type_def(name)
    if definition["type"]["kind"] != "struct":
        raise KashValidationError(f"{name} is not a struct", field=name)
    out: dict[str, Any] = {}
    for f in definition["type"]["fields"]:
        out[f["name"]], offset = _decode_value(f["type"], data, offset, f["name"])
    return out, offset


def decode_account(name: str, data: bytes) -> dict[str, Any]:
    """Decode an account's data by its IDL type, discriminator checked.

    Field names are the IDL's snake_case; ``[u8; N]`` arrays come back as
    ``bytes`` (zero-copy accounts render ``u128``/pubkeys that way — see
    :mod:`kashdao_protocol_sdk.solana.accounts`). Trailing bytes are ignored,
    as Anchor's coder ignores them.
    """
    expected = discriminator("accounts", name)
    if data[:8] != expected:
        raise KashValidationError(
            f"account discriminator is not {name}'s",
            field="discriminator",
            value=data[:8].hex(),
            constraint=expected.hex(),
        )
    decoded, _ = _decode_struct(name, data, 8)
    return decoded


def encode_account(name: str, fields: dict[str, Any]) -> bytes:
    """Encode an account (discriminator + struct). The inverse of :func:`decode_account`."""
    return discriminator("accounts", name) + encode_value({"defined": {"name": name}}, fields, name)


@dataclass(frozen=True, slots=True)
class DecodedInstruction:
    """An instruction's name and decoded arguments."""

    name: str
    args: dict[str, Any]


def decode_instruction(data: bytes) -> DecodedInstruction:
    """Decode instruction data by its discriminator (used by tests and tooling)."""
    for ix in kash_market_idl()["instructions"]:
        if bytes(ix["discriminator"]) == data[:8]:
            args: dict[str, Any] = {}
            offset = 8
            for arg in ix["args"]:
                args[arg["name"]], offset = _decode_value(arg["type"], data, offset, arg["name"])
            return DecodedInstruction(name=str(ix["name"]), args=args)
    raise KashValidationError(
        "instruction data matches no kash_market discriminator",
        field="discriminator",
        value=data[:8].hex(),
    )


# ---------------------------------------------------------------------------
# Instruction building
# ---------------------------------------------------------------------------


def event_authority(program_id: Pubkey) -> Pubkey:
    """Anchor's ``__event_authority`` PDA for ``program_id``."""
    return Pubkey.find_program_address([EVENT_AUTHORITY_SEED], program_id)[0]


def _seed_bytes(
    seed: dict[str, Any], ix: dict[str, Any], args: dict[str, Any], account: str
) -> bytes:
    if seed["kind"] == "const":
        return bytes(seed["value"])
    if seed["kind"] == "arg":
        for arg in ix["args"]:
            if arg["name"] == seed["path"]:
                return encode_value(arg["type"], args.get(seed["path"]), seed["path"])
        raise KashValidationError(f"seed refers to unknown arg {seed['path']}", field=account)
    raise KashValidationError(
        f"{account} depends on account data ({seed['path']}); supply it explicitly",
        field=account,
    )


def _resolve_account(
    account: dict[str, Any],
    ix: dict[str, Any],
    program_id: Pubkey,
    args: dict[str, Any],
    supplied: dict[str, Pubkey],
) -> Pubkey:
    name = account["name"]
    given = supplied.get(name)
    if given is not None:
        return given
    if name == "event_authority":
        return event_authority(program_id)
    if name == "program":
        return program_id
    if name == "program_data":
        return Pubkey.find_program_address([bytes(program_id)], BPF_LOADER_UPGRADEABLE_PROGRAM_ID)[
            0
        ]
    if "address" in account:
        return Pubkey.from_string(account["address"])
    pda = account.get("pda")
    if pda and all(s["kind"] != "account" for s in pda["seeds"]):
        seeds = [_seed_bytes(s, ix, args, name) for s in pda["seeds"]]
        program = pda.get("program")
        owner = (
            Pubkey.from_bytes(bytes(program["value"]))
            if program and program["kind"] == "const"
            else program_id
        )
        return Pubkey.find_program_address(seeds, owner)[0]
    raise KashValidationError(
        f"{ix['name']} needs account {name} and the IDL cannot derive it",
        field=name,
        metadata={"instruction": ix["name"]},
    )


def build_program_instruction(
    *,
    program_id: Pubkey,
    name: str,
    args: dict[str, Any],
    accounts: dict[str, Pubkey],
) -> Instruction:
    """Build one ``kash_market`` instruction from its IDL definition.

    Accounts come out in IDL order with the IDL's signer/writable flags;
    data is the discriminator plus borsh-encoded arguments. Every
    const/arg-seeded PDA, fixed address and Anchor convention account
    (``event_authority``, ``program``, ``program_data``) is derived; a
    caller-supplied account always wins over derivation.
    """
    ix = _find("instructions", name)
    data = bytes(ix["discriminator"])
    for arg in ix["args"]:
        if arg["name"] not in args:
            raise KashValidationError(f"{name} needs argument {arg['name']}", field=arg["name"])
        data += encode_value(arg["type"], args[arg["name"]], arg["name"])
    metas = [
        AccountMeta(
            pubkey=_resolve_account(account, ix, program_id, args, accounts),
            is_signer=account.get("signer") is True,
            is_writable=account.get("writable") is True,
        )
        for account in ix["accounts"]
    ]
    return Instruction(program_id, data, metas)


def idl_instruction_accounts(name: str) -> list[dict[str, Any]]:
    """The IDL's account list for an instruction (order, signer and writable flags)."""
    accounts: list[dict[str, Any]] = _find("instructions", name)["accounts"]
    return accounts


__all__ = [
    "BPF_LOADER_UPGRADEABLE_PROGRAM_ID",
    "DecodedInstruction",
    "build_program_instruction",
    "decode_account",
    "decode_instruction",
    "discriminator",
    "encode_account",
    "encode_value",
    "event_authority",
    "idl_instruction_accounts",
    "kash_market_idl",
    "program_error_names",
]
