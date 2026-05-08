"""Mode-agnostic surface — shared by both EOA and Smart Account modes.

Contains: error hierarchy, shared types (markets, account, simulation),
ABI loader, per-chain address registry, ERC-1155 helpers, USDC
allowance helpers, EIP-1559 fee estimation, lifecycle hooks, custom-chain
support, unit conversion, retry helpers, trading-client discriminated
union.

Mode-specific surfaces live in :mod:`kashdao_protocol_sdk.eoa` and
:mod:`kashdao_protocol_sdk.smart_account`.
"""
