"""Reference SA signer adapters.

Mirrors ``src/smart-account/signers/``.

* :class:`LocalSigner` — local-key, ``eth_account``-backed (Python parallel
  to ``viemAccountSigner``).
* :class:`JsonRpcSigner` — remote-RPC via ``personal_sign`` /
  ``eth_signTypedData_v4``.
"""

from __future__ import annotations

from kashdao_protocol_sdk.smart_account.signers.json_rpc import (
    JsonRpcSigner,
    JsonRpcSignerConfig,
    json_rpc_signer,
)
from kashdao_protocol_sdk.smart_account.signers.local import (
    LocalSigner,
    viem_account_signer,
)

__all__ = [
    "JsonRpcSigner",
    "JsonRpcSignerConfig",
    "LocalSigner",
    "json_rpc_signer",
    "viem_account_signer",
]
