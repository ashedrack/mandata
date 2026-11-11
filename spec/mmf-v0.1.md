# The Mandata Mandate Format (MMF) — v0.1

**Status:** Draft specification
**Reference implementation:** `mandata` (Python), `mandata` (TypeScript)
**License:** Apache-2.0

This document defines the wire format, evaluation semantics, and evidence
requirements for *mandates* — bounded spending authority granted by a
**principal** to an **agent** and enforced by a deterministic **gate** before
any payment rail is invoked.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are to be
interpreted as described in RFC 2119.

---

## 1. Concepts

| Term | Definition |
|------|-----------|
| **Principal** | The human or organization granting spending authority |
| **Agent** | The autonomous entity permitted to propose transactions |
| **Mandate** | The principal's grant: identity binding + bounds + validity window |
| **Gate** | The evaluation function `enforce(mandate, tx) -> Decision` |
| **Rail** | Any settlement mechanism (AP2, x402, bank, card, mobile money). Out of scope |
| **Evidence log** | Append-only hash-chained record of every gate decision |

The gate is **not** a payment rail. Implementations MUST evaluate the gate
BEFORE invoking settlement, and MUST NOT treat a gate decision as settlement
confirmation.

## 2. Mandate object

```json
{
  "mandate_id": "string, unique",
  "principal": "string identifier, e.g. user:alice",
  "agent": "string identifier, e.g. agent:shopbot",
  "issued_at": 1730000000.0,
  "revoked": false,
  "parent_mandate_id": "string | null",
  "bounds": {
    "max_amount": 500.0,
    "currency": "GBP",
    "allowed_payees": ["tesco.com", "skyscanner.com"],
    "expires_in_days": 30,
    "velocity_limit": 4,
    "total_budget": 900.0,
    "budget_window_days": 30
  }
}
```

### 2.1 Field semantics

| Field | Type | Required | Semantics |
|-------|------|----------|-----------|
| `mandate_id` | string | MUST | Unique identifier; the revocation registry key |
| `principal` | string | MUST | Authority-granting party |
| `agent` | string | MUST | Party the mandate binds to |
| `issued_at` | number | MUST | Unix epoch seconds |
| `revoked` | boolean | MUST | Local revocation flag |
| `parent_mandate_id` | string\|null | MAY | Set when created by `delegate()` |
| `bounds.max_amount` | number | MUST | Per-transaction cap; MUST be > 0 |
| `bounds.currency` | string | MUST | ISO-4217-style code; comparisons are exact |
| `bounds.allowed_payees` | string[] | MUST | Empty = any payee permitted |
| `bounds.expires_in_days` | integer | MUST | Validity = `issued_at + days*86400` |
| `bounds.velocity_limit` | integer | MUST | Max transactions per trailing 86400s; 0 = unlimited |
| `bounds.total_budget` | number\|null | MAY | Cumulative spend cap over `budget_window_days` |
| `bounds.budget_window_days` | integer | MUST | Trailing window for `total_budget`; default 30 |

## 3. Transaction object

```json
{ "payee": "tesco.com", "amount": 49.99, "currency": "GBP", "at": 1730000100.0 }
```

`at` MAY be omitted; the implementation then uses current time. The currency
field MAY be omitted; when omitted, no currency check is applied.

## 4. Decision object

```json
{ "allowed": false, "check": "PAYEE", "reason": "payee 'evil' not in allowed set" }
```

| Field | Semantics |
|-------|-----------|
| `allowed` | boolean — the ONLY normative output; `true` means proceed to rail |
| `check` | Normative check identifier (§5); present in conformant implementations |
| `reason` | Human-readable string; **advisory**, implementations MAY differ in wording |

## 5. Evaluation semantics

`enforce()` MUST evaluate the following checks **in order** and MUST stop at
the first failure. A transaction is allowed **only if all checks pass**.

| Order | Check ID | Condition for denial |
|-------|----------|---------------------|
| 1 | `REVOKED_REGISTRY` | `registry.is_revoked(mandate_id)` — if a registry is attached |
| 2 | `REVOKED` | `mandate.revoked == true` |
| 3 | `EXPIRED` | `at > issued_at + expires_in_days*86400` |
| 4 | `NON_POSITIVE` | `amount <= 0` |
| 5 | `CAP_EXCEEDED` | `amount > max_amount` |
| 6 | `CURRENCY` | tx currency present and `!= bounds.currency` |
| 7 | `PAYEE` | `allowed_payees` non-empty and payee not a member |
| 8 | `VELOCITY` | `velocity_limit > 0` and count of recorded txs in trailing 86400s `>= velocity_limit` |
| 9 | `BUDGET` | `total_budget` set and `spent_in_window + amount > total_budget` |
| — | `WITHIN` | all checks pass |

### 5.1 State requirements

- Velocity and budget counters MUST only increment on **allowed** decisions.
- Denied transactions MUST NOT consume velocity or budget.
- Check-and-record MUST be atomic — concurrent `enforce()` calls MUST NOT
  produce more allowances than bounds permit (no TOCTOU races).
- Counters SHOULD be durable (survive restart) for production deployments;
  in-memory counters are acceptable for single-process use.

### 5.2 Evidence requirements

Every evaluation — allowed or denied — MUST append one record to the evidence
log containing at minimum: `mandate_id`, `agent`, `payee`, `amount`,
`allowed`, `reason`, `timestamp`, and `prev_hash` (the hash of the previous
record; `"genesis"` for the first).

Record hash: `SHA-256(mandate_id|agent|payee|amount|allowed|reason|timestamp|prev_hash)`

An implementation MUST expose `verify_chain()` returning false if any
record's `prev_hash` does not match its predecessor.

## 6. Delegation

`delegate(parent, sub_agent, child_bounds)` MUST produce a mandate whose
bounds are a **strict subset** of the parent's: amount cap, expiry, velocity,
and budget MUST NOT exceed the parent's; `allowed_payees` MUST be a subset;
currency MUST match. Any widening MUST raise an error. The child's
`parent_mandate_id` MUST equal the parent's `mandate_id`.

## 7. Conformance

An implementation conforms to MMF-v0.1 if it:

1. Evaluates §5 checks in order, first-failure-wins
2. Records denials as well as allowances in evidence
3. Passes every vector in `spec/vectors/` (`allowed` + `check` must match)
4. Implements `verify_chain()` correctly on the produced records

Reason strings and internal APIs are implementation-defined.
