# The Mandate Model

Mandata's core schema: how a principal's intent becomes an enforceable,
provable constraint on an agent.

## The chain

```
principal ──grants──► mandate ──binds via credential──► agent
                            │
                            ▼
            bounds enforced at gate ──► transaction allowed/rejected
                            │
                            ▼
                    evidence record (hash-chained)
```

## Schema

### Mandate

| Field | Type | Meaning |
|-------|------|---------|
| `mandate_id` | hex string | Unique identifier — referenced by every evidence record |
| `principal` | string | The entity granting authority (`user:alice`, `org:acme-corp`) |
| `agent` | string | The entity holding authority (`agent:shopping-bot`) |
| `bounds` | `Bounds` | The permitted envelope (below) |
| `issued_at` | timestamp | Grant time; expiry derives from it |
| `revoked` | bool | Kill-switch — checked on every `enforce()` call |

### Bounds

| Field | Type | Meaning |
|-------|------|---------|
| `max_amount` | decimal | Ceiling per transaction |
| `currency` | ISO 4217 | Single currency per mandate — no ambiguity |
| `allowed_payees` | string[] | Payee allowlist; empty = any payee within other bounds |
| `expires_in_days` | int | Mandate lifetime from `issued_at` |
| `velocity_limit` | int | Max transactions per day; 0 = unlimited |

### Credential

| Field | Type | Meaning |
|-------|------|---------|
| `mandate_id` | hex | The mandate this credential binds to |
| `agent` | string | The agent identity the credential binds |
| `bounds_hash` | SHA-256 | Canonical hash of bounds — detects post-issuance tampering |
| `expires_at` | timestamp | Credential validity window |
| `signature` | Ed25519 hex | Principal's signature over the payload |
| `public_key` | hex | Principal's verify key |

**Signed payload:** `mandate_id | agent | bounds_hash | expires_at`

A credential proves: *this principal signed over this agent, these exact bounds,
this expiry.* Altering bounds after issuance breaks `bounds_hash`; swapping
agent or mandate breaks the binding; forging the signature fails verification.

## Decision model

`enforce()` returns a `Decision`:

- `allowed: bool` — did every check pass?
- `reason: str` — the specific condition that failed (or `"within mandate"`)
- `evidence: EvidenceRecord` — the hash-chained receipt

Check order is deliberate — integrity state before bounds conditions:

```
revoked? → expired? → amount>0? → amount≤cap? → currency? → payee? → velocity?
```

## Evidence chain

Append-only; each record stores `prev_hash`, and `record.hash` covers the full
record + predecessor. Tamper with any historical record and `verify_chain()`
fails. Rejections are records too — an audit trail that only stores approvals
is not an audit trail.

## Threat model (v0.1)

| Attack | Defense |
|--------|---------|
| Agent exceeds amount cap | deterministic bound check at gate |
| Agent pays unlisted payee | allowlist check |
| Mandate tampered post-issuance | `bounds_hash` in signed credential |
| Forged credential | Ed25519 verify against principal's known key |
| Replay of old decisions | hash-chained, timestamped evidence records |
| Audit-trail manipulation | chain verification fails on any edit |

Open problems (roadmap): revocation propagation latency, delegation depth
(agent→sub-agent weakening), evidence anchoring to an external timestamping
service.
