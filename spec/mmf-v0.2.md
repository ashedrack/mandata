# The Mandata Mandate Format (MMF) — v0.2

**Status:** Draft specification (supersedes [v0.1](mmf-v0.1.md))
**Reference implementation:** `mandata` ≥ 0.3.0 (Python)
**License:** Apache-2.0

This document defines the wire format, evaluation semantics, evidence
requirements, delegation rules and conformance criteria for *mandates* —
bounded spending authority granted by a **principal** to an **agent** and
enforced by a deterministic **gate** before any payment rail is invoked.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are to be
interpreted as described in RFC 2119.

---

## 1. Concepts

| Term | Definition |
|------|-----------|
| **Principal** | The human or organization granting spending authority |
| **Agent** | The autonomous entity permitted to propose transactions |
| **Mandate** | The principal's grant: identity binding + bounds + validity window |
| **Credential** | The principal's Ed25519 signature binding a mandate to an agent (§7) |
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
  "issued_at": 1730000000,
  "revoked": false,
  "parent_mandate_id": "string | null",
  "bounds": {
    "max_amount": 500,
    "currency": "GBP",
    "allowed_payees": ["tesco.com", "skyscanner.com"],
    "expires_in_days": 30,
    "velocity_limit": 4,
    "total_budget": 900,
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
| `parent_mandate_id` | string\|null | MAY | Set when created by delegation (§6) |
| `bounds.max_amount` | number | MUST | Per-transaction cap; finite, > 0 |
| `bounds.currency` | string | MUST | Non-empty code; comparisons are exact |
| `bounds.allowed_payees` | string[] | MUST | Empty = any payee permitted |
| `bounds.expires_in_days` | integer | MUST | ≥ 1; validity = `issued_at + days*86400` |
| `bounds.velocity_limit` | integer | MUST | ≥ 0; max allowed txs per trailing 86400 s; 0 = unlimited |
| `bounds.total_budget` | number\|null | MAY | Finite, > 0; cumulative cap over `budget_window_days`; null = unlimited |
| `bounds.budget_window_days` | integer | MUST | ≥ 1; trailing window for `total_budget`; default 30 |

`parent_mandate_id` is an identity field, not a bound.

### 2.2 Validation

A mandate whose bounds violate §2.1 is **malformed**. Implementations MUST
reject a malformed mandate before it can be evaluated (for example at
construction or deserialization). A malformed mandate MUST NOT produce an
allow decision.

## 3. Transaction object

```json
{ "payee": "tesco.com", "amount": 49.99, "currency": "GBP", "at": 1730000100 }
```

- `amount` — the proposed payment amount in the mandate's currency.
- `currency` — MAY be omitted. When omitted, no currency check is applied and
  the amount is interpreted in the mandate's currency. Adapters MUST forward
  the rail's currency whenever the rail supplies one.
- `at` — evaluation time. **The gate's own clock is authoritative.** `at`
  exists for conformance vectors and offline replay only. A production gate
  MUST NOT accept `at` from the agent or from any party the mandate
  constrains, because a backdated `at` could evade expiry and the velocity
  and budget windows. An `at` of `0` is a valid time, not an omission.

## 4. Decision object

```json
{ "allowed": false, "check": "PAYEE", "reason": "payee 'evil' not in allowed set" }
```

| Field | Semantics |
|-------|-----------|
| `allowed` | boolean — `true` means proceed to rail |
| `check` | Normative check identifier (§5). MUST be present |
| `reason` | Human-readable string; **advisory**, wording is implementation-defined |

## 5. Evaluation semantics

The gate MUST evaluate the following checks **in order** and MUST stop at the
first failure. A transaction is allowed **only if all checks pass**.

| Order | Check ID | Condition for denial |
|-------|----------|---------------------|
| 0 | `CREDENTIAL` | Gate is credential-bound (§7) and the credential does not verify, or only one of credential / principal key is supplied |
| 1 | `REVOKED_REGISTRY` | A registry is attached and reports any mandate in the chain revoked, **or the registry cannot be consulted** |
| 2 | `REVOKED` | Any mandate in the chain has `revoked == true`, or a `parent_mandate_id` cannot be resolved |
| 3 | `EXPIRED` | Any mandate in the chain has `at > issued_at + expires_in_days*86400` |
| 4 | `NON_POSITIVE` | `amount` is not a finite number > 0 (includes NaN, ±infinity, non-numeric) |
| 5 | `CAP_EXCEEDED` | `amount > max_amount` for any mandate in the chain |
| 6 | `CURRENCY` | tx currency present and differs from any chain member's `currency` |
| 7 | `PAYEE` | Any chain member has non-empty `allowed_payees` not containing payee |
| 8 | `VELOCITY` | Any chain member has `velocity_limit > 0` and its allowed-tx count in the trailing 86400 s `>= velocity_limit` |
| 9 | `BUDGET` | Any chain member has `total_budget` set and `spent_in_window + amount > total_budget` |
| — | `WITHIN` | all checks pass |

**Chain.** The chain of a mandate is the mandate followed by its resolved
ancestors (leaf → root). Each check is applied to every member of the chain
before the next check is evaluated, so the ordering of check IDs is preserved
across delegation.

**Fail-closed registry.** Any error, timeout or malformed response from the
revocation registry MUST be treated as `REVOKED_REGISTRY` and recorded. The
gate cannot prove non-revocation, so it MUST NOT allow.

**Ordering rationale.** Because every denial is evidence (§5.2), the check
recorded MUST be the most authoritative applicable failure: credential before
revocation, revocation before expiry, expiry before bounds, and stateless
bounds before stateful counters.

### 5.1 State requirements

- Velocity and budget counters MUST only increment on **allowed** decisions.
  Denied transactions MUST NOT consume velocity or budget.
- An allowed transaction MUST be recorded against **every** mandate in the
  chain, so delegated spending counts toward each ancestor's limits.
- Check-and-record MUST be atomic across the whole chain — concurrent
  evaluations, including by sibling delegates, MUST NOT produce more
  allowances than any chain member's bounds permit.
- Counters SHOULD be durable for production deployments. Where several
  processes enforce the same mandate, the shared store MUST provide the
  atomicity above (for example a transaction or row lock spanning
  check-and-record).

### 5.2 Evidence requirements

Every evaluation — allowed or denied — MUST append exactly one record to the
evidence log containing at minimum: `mandate_id`, `agent`, `payee`, `amount`,
`allowed`, `check`, `timestamp`, `prev_hash`. Records MAY carry an advisory
`detail` string, which is not covered by the hash.

`prev_hash` of the first record is the genesis value: 64 ASCII `0` characters.

**Canonical serialization.** The record hash is

```
SHA-256( UTF-8( mandate_id | agent | payee | AMOUNT | ALLOWED | check | TIMESTAMP | prev_hash ) )
```

where `|` is a literal vertical bar and:

- `AMOUNT`, `TIMESTAMP` — shortest round-trip decimal rendering of the number,
  no exponent, no trailing zeros, no trailing decimal point, `-0` written `0`
  (e.g. `100`, `49.99`, `0.0000001`). A non-finite amount is written `invalid`.
- `ALLOWED` — `true` or `false`.
- the hex digest is lower-case.

Conformance vector: `m1|a|tesco.com|49.99|true|WITHIN|1000|` followed by 64
`0` characters MUST hash to the SHA-256 of exactly that string.

**Verification.** An implementation MUST expose `verify_chain()` that
recomputes every record's hash and returns false if any record's `prev_hash`
differs from its predecessor's hash. This detects modification, insertion,
deletion or reordering of any record that has a successor. A bare hash chain
cannot detect removal of the most recent records or an edit to the final
record; implementations SHOULD periodically publish the head hash to an
external anchor (timestamping service or transparency log), and
`verify_chain(anchored_head)` MUST additionally return false if the chain no
longer contains the anchored head.

### 5.3 Deployment requirements

- **Mediation boundary.** Complete mediation holds only if the agent cannot
  reach the rail except through the gate. The gate SHOULD run outside the
  agent's trust boundary and hold (or broker) the rail credentials. A gate
  embedded in the agent's own process (for example a tool wrapper) protects
  against model error and prompt injection but not against a compromised
  agent runtime.
- **Clock.** See §3: the gate uses its own clock; adapters MUST NOT forward
  agent-supplied timestamps.

## 6. Delegation

`delegate(parent, sub_agent, child_bounds)` produces a child mandate whose
bounds are a **subset** of the parent's — no bound may be wider; equal is
permitted:

- `max_amount` ≤ parent's; `currency` identical;
- `allowed_payees` ⊆ parent's. If the parent's list is non-empty, an empty
  child list (meaning "any payee") is wider and MUST be rejected;
- `expires_in_days` ≤ parent's;
- if the parent's `velocity_limit > 0`, the child's MUST be `> 0` and ≤ parent's
  (0 means unlimited and is wider);
- if the parent's `total_budget` is set, the child's MUST be set and ≤ parent's
  (null means unlimited and is wider).

Any widening MUST raise an error. Delegation from a revoked or expired parent
MUST fail. The child's `parent_mandate_id` MUST equal the parent's
`mandate_id`, and the parent MUST remain resolvable by the gate.

Because the gate evaluates the whole chain (§5), the child's effective
expiry is the earliest expiry in its chain, revoking any ancestor stops every
descendant, and sibling delegates share their ancestors' velocity and budget.

## 7. Credentials

A credential is the principal's Ed25519 signature over
`mandate_id | agent | bounds_hash | EXPIRES_AT`, where `EXPIRES_AT` is the
mandate's expiry in the canonical number form of §5.2 and `bounds_hash` is the
lower-case hex SHA-256 of

```
principal | ISSUED_AT | parent_mandate_id | MAX_AMOUNT | currency | sorted(allowed_payees) joined by "," | expires_in_days | velocity_limit | TOTAL_BUDGET | budget_window_days
```

with numbers in canonical form, a null `parent_mandate_id` or `total_budget`
written as the empty string. Every authority-bearing field is therefore
covered: none can be altered without invalidating the signature.

A gate is **credential-bound** when it is given both a credential and the
principal's expected public key. A credential-bound gate MUST verify, before
any other check, that the key matches, the credential names this mandate and
agent, the bounds hash matches, the credential is unexpired, and the
signature is valid; any failure denies with `CREDENTIAL`.

## 8. Conformance

An implementation conforms to MMF-v0.2 if it:

1. Validates mandates per §2.2
2. Evaluates §5 checks in order, chain-wide, first-failure-wins, fail-closed on registry errors
3. Records every decision, allowed and denied, in evidence with the normative `check`
4. Produces evidence hashes per the canonical serialization of §5.2
5. Passes every vector in `spec/vectors/core.json` and `spec/vectors/delegation.json`
   (`allowed` and `check` must both match; for delegation, validity must match)
6. Implements `verify_chain()` and `verify_chain(anchored_head)` per §5.2

Reason strings, detail strings and internal APIs are implementation-defined.

## 9. Changes from v0.1

- Added check 0 `CREDENTIAL` (credential-bound gate) and the Credential concept.
- Checks now apply to the full delegation chain; allowed spend is charged to every ancestor.
- Registry failure is fail-closed (`REVOKED_REGISTRY`); unresolvable parents deny (`REVOKED`).
- `NON_POSITIVE` now covers NaN, infinity and non-numeric amounts.
- Malformed mandates MUST be rejected (§2.2).
- Delegation: "unlimited" sentinels (`velocity_limit = 0`, empty `allowed_payees`,
  null `total_budget`) can no longer widen a limited parent; "strict subset" corrected to
  "subset"; delegation from revoked/expired parents fails.
- Evidence: hash covers the normative `check` instead of free-text reason; canonical
  serialization defined; genesis value fixed as 64 zeros (v0.1 text said `"genesis"`);
  `verify_chain` limits and anchored verification specified.
- Credentials: signed bounds hash now covers every bound, principal, issuance time and
  delegation link (v0.1 omitted `total_budget` and `budget_window_days`).
- `at` is test/replay-only; the gate's clock is authoritative.
- Vectors: 26 core (registry, fail-closed, ordering, denial-consumes-nothing) + 17 delegation.
