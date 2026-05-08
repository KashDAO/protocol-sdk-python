# Security policy

## Reporting a vulnerability

**Please do not file a public GitHub issue for security vulnerabilities.**

Email: `security@kash.bot`

Include:

- A clear description of the issue
- Reproduction steps or proof-of-concept
- The version of `kashdao-protocol-sdk` (and any relevant runtime info:
  Python version, OS, web3.py version, bundler vendor, signer type)
- Whether the issue affects the SDK, the consumer-side smart-account
  flow, or both
- Your contact info if you'd like attribution in the disclosure

We aim to:

1. Acknowledge receipt within **2 business days**.
2. Confirm or reject the report within **7 business days**.
3. Ship a fix within **30 days** for accepted reports, faster for issues
   actively being exploited on mainnet.

Once the fix has shipped and a reasonable upgrade window has elapsed, we
publish a coordinated disclosure crediting the reporter (with consent).

## Scope

In scope:

- Vulnerabilities in `kashdao-protocol-sdk` itself (this package).
- Issues that allow bypassing the SDK's documented guarantees:
  - **UserOp / EIP-1559 hash divergence** — calldata or hash that
    disagrees with what EntryPoint v0.7 (SA mode) or
    `keccak(rlp(envelope))` (EOA mode) produce, causing signed
    transactions to be rejected or accepted incorrectly
  - **ABI drift** — vendored ABIs that disagree with the canonical TS
    `@kashdao/protocol-sdk` source in a way the CI drift gate fails to
    catch
  - **Address drift** — per-chain address registry that disagrees with
    the TS canonical
  - **Signer adapter leakage** — any path where the SDK persists, logs,
    or transmits signed bytes, raw hashes, or signer-side credentials
    beyond what's required to forward to the consumer-configured
    bundler / RPC
  - **REST client SSRF / open-redirect** via RPC URL validation gaps
  - **HTTPS bypass** — `http://` URL accepted in production paths
    (currently rejected unless localhost)
  - **HTTP smuggling / header injection** via config inputs
  - **Slippage-protection bypass** — calldata that ignores
    `max_slippage_bps` or routes around the on-chain quote check
  - **Decoder injection** — revert-data decoding that allows an attacker
    to surface forged error names through `simulate()`
  - **Staleness-guard bypass** — submission paths that accept signed
    transactions targeting a different chain id or signer than the
    configured client without a typed error

Out of scope (please report to `security@kash.bot` separately if relevant):

- Vulnerabilities in the Kash protocol contracts themselves (Market,
  Vault, Oracle, MarketFactory, OutcomeTokens1155, ParamRegistry) —
  these are in-scope for the protocol bug bounty, not the SDK
- Vulnerabilities in transitive dependencies (`web3.py`, `eth-account`,
  `httpx`, `pydantic`, etc.) — report upstream first; we'll respond to
  coordinated disclosures
- Vulnerabilities in the consumer's own bundler / RPC / signer
  infrastructure
- Theoretical timing attacks against `time.time()` / `asyncio.sleep`
  granularity
- Issues requiring physical access to the user's machine, signer, or HSM

## Trust model recap

The non-custodial path is zero-custody by construction:

- **No private keys.** The SDK never receives or persists the consumer's
  private key. Signing happens on the consumer's infrastructure (HSM,
  Fireblocks, web3signer, eth-account local key, etc.).
- **No relay.** Signed UserOps / EIP-1559 transactions go to the
  consumer-configured bundler / RPC, never to a Kash-hosted service.
- **No paymaster.** The smart account pays its own gas in ETH. The SDK
  does not sponsor gas or bundle paymaster data.
- **No telemetry.** The SDK does not phone home. There is no usage
  analytics, no error reporting to Kash, no opt-out required.

If you find a path that violates any of these guarantees, that's an
in-scope vulnerability.

## Supported versions

| Version | Supported            |
| ------- | -------------------- |
| 0.x     | ✅ Latest minor only |

While the package is `0.x`, only the latest published `0.x.y` receives
security fixes. After 1.0, we'll publish a long-term-support policy here.

## Disclosure

Acknowledged researchers are listed in `CHANGELOG.md` against the patched
release.
