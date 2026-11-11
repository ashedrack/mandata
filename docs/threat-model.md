# Threat Model

Attacks the Mandata enforcement layer must survive — mapped to defenses.

| Attack | Description | Defense (current) | Status |
|--------|-------------|-------------------|--------|
| Cap overrun | Agent spends above per-transaction limit | `max_amount` bound check at gate | Mitigated |
| Payee substitution | Agent pays an unlisted party | `allowed_payees` allowlist | Mitigated |
| Velocity bypass | Many small transactions drain funds | `velocity_limit` daily counter | Mitigated |
| Budget exhaustion | Death-by-a-thousand-cuts spending | `total_budget` + windowed sum (v0.2) | Mitigated |
| Expired mandate reuse | Transaction post-expiry | `expires_at` check on every call | Mitigated |
| Counter reset | Restart wipes velocity/spend counters | `SQLiteStore` shared persistent state | Mitigated |
| Local revocation evasion | Other processes keep enforcing | `RevocationRegistry` checked at gate | Mitigated |
| TOCTOU race | Parallel calls both pass the check | Per-mandate lock around check+record | Mitigated |
| Audit tampering | Operator edits historical evidence | Hash-chained records; `verify_chain()` fails | Mitigated |
| Chain recompute | Operator rewrites AND recomputes chain | Needs external timestamp anchor | **Open (#13)** |
| Credential forgery | Fake mandate presentation | Ed25519 verify vs principal key | Mitigated |
| Bounds tampering | Bounds altered post-issuance | `bounds_hash` in signed credential | Mitigated |
| Delegation abuse | Sub-agent gets looser scope than parent | Subset-only `delegate()` | **Open (#11)** |
| Prompt-injection → payment | Injected content steers payment tool | Gate rejects out-of-bounds transactions | Partially — gate can't fix intent, only bound it |
| Clock manipulation | Host time skew bypasses expiry | NTP-verified `at` param; anchor timestamps | **Open (#13)** |

## Residual risks honestly stated

- Gate cannot judge *semantic* correctness — a payment inside bounds to a
  legit-listed payee but for a hallucinated purpose still passes. That's a
  policy problem above the gate; Mandata's job is to bound it.
- `InMemoryRegistry`/`InMemoryStore` are per-process; multi-process deployments
  must use shared backends.
