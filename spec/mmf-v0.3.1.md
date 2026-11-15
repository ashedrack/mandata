# The Mandata Mandate Format (MMF) — v0.3.1

**Status:** Draft specification (v0.3.1; supersedes [v0.3](mmf-v0.3.md) and [v0.2](mmf-v0.2.md))
**Reference implementation:** `mandata` ≥ 0.4.1 (Python)
**License:** Apache-2.0

This document defines the wire format, evaluation semantics, transaction
lifecycle, evidence requirements, delegation rules and conformance criteria
for *mandates* — bounded spending authority granted by a **principal** to an
**agent** and enforced by a deterministic **gate** before any payment rail is
invoked.

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
| **Transaction** | One payment attempt, identified by a `tx_id` (§3) |
| **Authorization** | An allowed decision; it reserves the amount until settled or voided (§5.4) |
| **Rail** | Any settlement mechanism (AP2, x402, bank, card, mobile money). Out of scope |
| **Evidence log** | Append-only hash-chained record of every gate decision and lifecycle event |

The gate is **not** a payment rail. Implementations MUST evaluate the gate
BEFORE invoking settlement, and MUST NOT treat a gate decision as settlement
confirmation. MMF bounds **authorizations**; §5.4 defines how authorizations
relate to funds actually moved.

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
    "max_amount": 50000,
    "currency": "GBP",
    "allowed_payees": ["tesco.com", "skyscanner.com"],
    "expires_in_days": 30,
    "velocity_limit": 4,
    "total_budget": 90000,
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
| `revoked` | boolean | MUST | Local revocation flag; mutable and not signed (§7) |
| `parent_mandate_id` | string\|null | MAY | Set when created by delegation (§6). An identity field, not a bound |
| `bounds.max_amount` | integer | MUST | Per-transaction cap in minor units (§3.1); 1 ≤ value ≤ 2⁵³−1 |
| `bounds.currency` | string | MUST | Non-empty code; comparisons are exact |
| `bounds.allowed_payees` | string[] | MUST | Empty = any payee permitted |
| `bounds.expires_in_days` | integer | MUST | ≥ 1; validity = `issued_at + days*86400` |
| `bounds.velocity_limit` | integer | MUST | ≥ 0; max authorizations per trailing 86400 s; 0 = unlimited |
| `bounds.total_budget` | integer\|null | MAY | Minor units, 1 ≤ value ≤ 2⁵³−1; cumulative cap over `budget_window_days`; null = unlimited |
| `bounds.budget_window_days` | integer | MUST | ≥ 1; trailing window for `total_budget`. Producers that omit it MUST be treated as 30 |

### 2.2 Validation

A mandate whose bounds violate §2.1 is **malformed**. Implementations MUST
reject a malformed mandate before it can be evaluated (for example at
construction or deserialization). A malformed mandate MUST NOT produce an
allow decision.

## 3. Transaction object

```json
{ "tx_id": "order-8812", "payee": "tesco.com", "amount": 4999, "currency": "GBP", "at": 1730000100 }
```

- `tx_id` — **MUST** be present on the wire. A stable identifier of this
  payment attempt, unique per gate; the rail's idempotency key or nonce is
  the natural choice. It makes evaluation idempotent (§5.3) and links the
  authorization to its settlement (§5.4).
- `payee` — the beneficiary identifier the rail will settle to (§5.5).
- `amount` — integer in minor units of the mandate currency (§3.1).
- `currency` — **MUST** be present. An integer amount is not financially
  meaningful without a currency (`100` could be £1.00, ¥100 or 100 atomic
  units), so a transaction that omits `currency` is denied with `CURRENCY`
  (§5.1). The transaction currency MUST equal every chain member's
  `bounds.currency` exactly; MMF-v0.3.1 does not convert currencies.
- `at` — evaluation time. **The gate's own clock is authoritative.** `at`
  exists for conformance vectors and offline replay only. A production gate
  MUST NOT accept `at` from the agent or from any party the mandate
  constrains, because a backdated `at` could evade expiry and the velocity
  and budget windows. An `at` of `0` is a valid time, not an omission.

### 3.1 Amounts

All amounts (`max_amount`, `total_budget`, transaction `amount`) are JSON
**integers** in the currency's **minor unit** (pence for GBP, cents for USD,
yen for JPY, fils for KWD, atomic units for on-chain assets). Binary floating
point MUST NOT be used in any authorization decision.

- A valid amount is an integer in **[1, 2⁵³ − 1]** (the largest integer every
  JSON implementation represents exactly). Anything else — zero, negative,
  fractional, non-numeric, NaN, ±infinity, or out of range — is invalid.
- Implementations MUST compute sums exactly (arbitrary-precision or 64-bit
  integer arithmetic; `BigInt` in JavaScript).
- Converting a decimal major-unit value to minor units uses the ISO 4217
  minor-unit exponent (2 unless listed otherwise; e.g. JPY 0, KWD 3).
  Conversion MUST reject — never round — a value with more decimal places
  than the exponent allows. Non-ISO assets MUST declare their exponent
  (for example USDC = 6). Conversion happens in adapters; the gate only sees
  integers.

## 4. Decision object

```json
{ "tx_id": "order-8812", "allowed": false, "check": "PAYEE", "status": "DENIED",
  "replayed": false, "reason": "payee 'evil' not in allowed set" }
```

| Field | Semantics |
|-------|-----------|
| `tx_id` | The transaction decided |
| `allowed` | boolean — `true` means the amount is authorized |
| `check` | Normative check identifier (§5). MUST be present |
| `status` | Lifecycle state of `tx_id` at the time of the call (§5.4) |
| `replayed` | `true` if returned from the idempotency ledger rather than evaluated (§5.3) |
| `reason` | Human-readable string; **advisory**, wording is implementation-defined |

A decision also carries the exact `payee`, `amount` and `currency` it
evaluated, for payee binding (§5.5).

## 5. Evaluation semantics

### 5.1 Ordered checks

The gate MUST evaluate the following checks **in order** and MUST stop at the
first failure. A transaction is allowed **only if all checks pass**.

| Order | Check ID | Condition for denial |
|-------|----------|---------------------|
| 0 | `CREDENTIAL` | Gate is credential-bound (§7) and the credential for any chain member does not verify or is missing, or only one of credential / principal key is supplied |
| 1 | `REVOKED_REGISTRY` | A registry is attached and reports any mandate in the chain revoked, **or the registry cannot be consulted** |
| 2 | `REVOKED` | Any mandate in the chain has `revoked == true` |
| — | `INVALID_CHAIN` | A `parent_mandate_id` cannot be resolved, or the chain is cyclic or malformed. `INVALID_CHAIN` is not an ordered check: it fires during chain resolution, before any ordered check, because no chain means no checks |
| 3 | `EXPIRED` | Any mandate in the chain has `at > issued_at + expires_in_days*86400` |
| 4 | `NON_POSITIVE` | `amount` is not a valid amount (§3.1) |
| 5 | `CAP_EXCEEDED` | `amount > max_amount` for any mandate in the chain |
| 6 | `CURRENCY` | tx `currency` absent, or differs from any chain member's `currency` |
| 7 | `PAYEE` | Any chain member has non-empty `allowed_payees` not containing payee |
| 8 | `VELOCITY` | Any chain member has `velocity_limit > 0` and its open-or-settled authorizations in the trailing 86400 s `>= velocity_limit` |
| 9 | `BUDGET` | Any chain member has `total_budget` set and `reserved_in_window + amount > total_budget` |
| — | `WITHIN` | all checks pass |

**Chain.** The chain of a mandate is the mandate followed by its resolved
ancestors (leaf → root). Each check is applied to every member of the chain
before the next check is evaluated, so the ordering of check IDs is preserved
across delegation: a child payee violation and an exhausted ancestor budget
deny as `PAYEE`.

**Fail-closed registry.** Any error, timeout or malformed response from the
revocation registry MUST be treated as `REVOKED_REGISTRY` and recorded. The
gate cannot prove non-revocation, so it MUST NOT allow.

**Ordering rationale.** Because every denial is evidence (§5.6), the check
recorded MUST be the most authoritative applicable failure: credential before
revocation, revocation before expiry, expiry before bounds, and stateless
bounds before stateful counters.

**Decision function.**

```
enforce(m, tx, t = gate_clock()):
  chain := resolve(m)                     # walk parent links, detect cycles
  if chain is unresolvable: decide(INVALID_CHAIN)   # before locks and ledger
  serialize on every mandate in chain               # §5.2
  if ledger has tx.tx_id:                            # §5.3
      if same request: return REPLAY of the stored decision
      else:            decide(TX_CONFLICT)
  if credential-bound:                               # §7 — unconditional
      for node in chain: if credential(node) fails: decide(CREDENTIAL)
  for check in [REVOKED_REGISTRY, REVOKED, EXPIRED, NON_POSITIVE,
                CAP_EXCEEDED, CURRENCY, PAYEE, VELOCITY, BUDGET]:
      for node in chain (leaf → root):
          if check fails for node:  decide(check)
  reserve tx.amount on every node; status := AUTHORIZED; decide(WITHIN)
decide(c): store (tx_id, request, c, status) in ledger; append evidence; return
```

`INVALID_CHAIN` is decided — and recorded — exactly like an ordered-check
denial, but it is *not* a check in the table: it fires during resolution,
before the ledger is consulted, so a malformed chain cannot poison a `tx_id`
as `TX_CONFLICT` fodder for a later retry.

### 5.2 State requirements

- Velocity and budget counters MUST only count **authorized** transactions
  (status AUTHORIZED or SETTLED). Denied transactions MUST NOT consume
  velocity or budget; voided authorizations MUST be released (§5.4).
- An authorized transaction MUST be reserved against **every** mandate in the
  chain, so delegated spending counts toward each ancestor's limits.
- Check-and-record MUST be **serializable** across the whole chain —
  concurrent evaluations, including by sibling delegates and by gates in
  other processes sharing the same state, MUST NOT produce more
  authorizations than any chain member's bounds permit. Implementations MUST
  acquire per-mandate locks in a consistent order (root first) and, for a
  shared store, hold a store-level serializable transaction spanning
  check-and-record (for example SQLite `BEGIN IMMEDIATE` or a row lock).
- Counters and the ledger SHOULD be durable for production deployments.

### 5.3 Idempotency

The gate MUST keep a ledger of every decided `tx_id`, holding the request
(`mandate_id`, `payee`, `amount`, `currency`), the decision (`allowed`,
`check`) and the lifecycle status.

- A call whose `tx_id` is in the ledger with an **identical request** MUST
  NOT be re-evaluated and MUST NOT reserve again. The gate MUST return the
  stored `allowed` and `check`, with `replayed = true` and the current
  `status`, and MUST append an evidence record with event `REPLAY`.
- A call whose `tx_id` is in the ledger with a **different request** MUST be
  denied with check `TX_CONFLICT` and recorded (event `DECISION`). The
  ledger entry is unchanged.
- A denial is final for its `tx_id`: a replay returns the same denial even
  if the cause has since gone away. A new attempt needs a new `tx_id`.

### 5.4 Transaction lifecycle

```
            enforce: allowed          claim        dispatch→settle
  (none) ─────────────────────▶ AUTHORIZED ───────▶ CLAIMED ───────▶ SETTLED
     │                              │   ▲            │
     │ enforce: denied              │   │ unclaim    │ void / settle
     ▼                              ▼───┘            ▼
   DENIED                         VOIDED ◀──────────┘ (void also AUTHORIZED→VOIDED)
```

Two distinct idempotency properties:

- **Authorization idempotency** (§5.3): the gate does not reserve the same
  request twice.
- **Settlement uniqueness** (this section): an authorization is not
  *dispatched* twice. `enforce` does not prevent two concurrent dispatcher
  workers from both observing AUTHORIZED and submitting to the rail — so
  settlement goes through an explicit, atomic **claim** step.

- **AUTHORIZED** — reserved against every chain member's velocity and budget.
- **CLAIMED** — a dispatcher has claimed the authorization for exactly one
  settlement attempt. `claim(mandate, tx_id, payee, amount, currency)` MUST
  atomically: verify the instruction binds to the stored request, transition
  AUTHORIZED → CLAIMED under the same serialization as `enforce`, and append
  a `CLAIM` evidence record. A second `claim` MUST fail; a `claim` whose
  instruction differs from the stored request MUST fail. A CLAIMED
  transaction MUST NOT bind to settlement instructions for a *new* dispatch.
- **SETTLED** — the rail confirmed funds moved; the reservation stays counted.
- **VOIDED** — the rail confirmed funds will not move (failure, cancellation,
  expiry of the authorization); the reservation MUST be released from every
  chain member. `void` MUST hold the same serialization as `enforce`, and
  applies to AUTHORIZED and CLAIMED transactions (a claimed attempt the rail
  definitively declined is voided).
- `unclaim(mandate, tx_id)` — operator recovery: CLAIMED → AUTHORIZED,
  appending an `UNCLAIM` record. It MUST be used only when the rail was never
  invoked; if the rail outcome is unknown the transaction MUST stay CLAIMED
  (fail-safe: an unreleased reservation costs budget, a double-dispatch
  costs money).
- `settle` and `void` MUST be idempotent on their target state (repeating
  them returns the transaction unchanged and appends no evidence).
- Every other transition (settling or voiding a DENIED, VOIDED/SETTLED, or
  unknown `tx_id`, one belonging to another mandate, claiming anything but
  AUTHORIZED, unclaiming anything but CLAIMED) MUST be rejected and MUST NOT
  append evidence. A SETTLED transaction cannot be voided: refunds, partial
  captures and chargebacks are rail concerns outside MMF.
- An AUTHORIZED or CLAIMED transaction whose outcome is unknown (for example
  a settlement timeout) MUST remain reserved until the rail's outcome is
  known (fail-safe). The agent retries with the **same** `tx_id` (§5.3); the
  rail MUST be invoked idempotently on the same key.

### 5.5 Payee binding

The gate checks the `payee` and `amount` it is given; binding guarantees the
rail moves exactly those.

- Adapters MUST derive `payee`, `amount` and `currency` from the rail's
  settlement instruction (for example x402 `payTo`/`maxAmountRequired`, AP2
  cart merchant and total), never from free text the agent supplies.
- Before submitting to the rail, an adapter MUST `claim` the authorization
  (§5.4) with the exact instruction it is about to submit. `claim` verifies
  the binding and atomically takes the single dispatch slot; an instruction
  that does not bind MUST NOT be submitted. Because only one `claim` can win,
  and a replayed decision for a claimed, settled or voided transaction is not
  AUTHORIZED, a retry can never be dispatched twice by the gate. MMF still
  requires the rail to honor its own idempotency key for crash-in-flight
  retries the gate cannot see (§5.4 fail-safe rule).
- Payee identifiers are compared exactly. Which identifier a rail settles to
  (domain, merchant ID, account, address) is rail-specific; an adapter MUST
  use the identifier the rail itself settles to.

### 5.6 Evidence requirements

Every `enforce` call MUST append exactly one record (event `DECISION` or
`REPLAY`); every accepted `claim` / `unclaim` / `settle` / `void` MUST append
exactly one record (event `CLAIM` / `UNCLAIM` / `SETTLE` / `VOID`). A record
contains at minimum: `event`, `tx_id`, `mandate_id`, `agent`, `payee`,
`amount`, `allowed`, `check`, `timestamp`, `prev_hash`. Lifecycle records
carry the authorization's `check` and `allowed = true`. Records MAY carry an
advisory `detail` string and a `signature`, neither covered by the hash.

**Atomicity.** The ledger write, the reservation update and the evidence
append are **one atomic durable operation**: a crash between them MUST NOT
leave a decided transaction without its evidence record, nor evidence for a
decision that was not recorded. An implementation whose evidence lives
outside the state store MUST use a transactional outbox with specified
crash-recovery semantics.

`prev_hash` of the first record is the genesis value: 64 ASCII `0` characters.

**Canonical serialization.** The record hash is

```
SHA-256( UTF-8( event | tx_id | mandate_id | agent | payee | AMOUNT | ALLOWED | check | TIMESTAMP | prev_hash ) )
```

where `|` is a literal vertical bar and:

- String fields are encoded as UTF-8 **with no escaping**. Because `|` is
  the field delimiter, any string field containing a `|` character MUST be
  rejected before serialization — an implementation MUST NOT serialize a
  record it cannot encode unambiguously. Control characters and NUL are
  likewise invalid in identifiers. Hash inputs are the UTF-8 bytes of the
  payload exactly as written; there is no canonicalization step beyond the
  field renderings below.
- `AMOUNT` — the amount as a base-10 integer with no sign, leading zeros or
  separators; an invalid amount (§3.1) is written `invalid`.
- `TIMESTAMP` — shortest round-trip decimal rendering of the number, no
  exponent, no trailing zeros, no trailing decimal point, `-0` written `0`
  (e.g. `1000`, `1730000000.25`).
- `ALLOWED` — `true` or `false`.
- the hex digest is lower-case.

`spec/vectors/evidence.json` gives payloads and hashes that every
implementation MUST reproduce byte for byte.

**Integrity, authenticity, completeness.** These are distinct properties:

- *Integrity.* `verify_chain()` MUST recompute every record's hash and return
  false if any record's `prev_hash` differs from its predecessor's hash. This
  detects modification, insertion, deletion or reordering of any record that
  has a successor. It cannot detect removal of the most recent records or an
  edit to the final record.
- *Completeness up to an anchor.* Implementations SHOULD periodically publish
  the head hash to an external anchor (timestamping service or transparency
  log). `verify_chain(anchored_head)` MUST additionally return false if the
  chain no longer contains the anchored head. Records after the last anchor
  are not protected against truncation.
- *Authenticity.* A gate SHOULD sign each record's hash (hex string, UTF-8)
  with its Ed25519 key. `verify_chain(public_key)` MUST additionally return
  false unless every record carries a valid signature by that key. Signing
  prevents an operator who lacks the gate key from rewriting and re-chaining
  history, and attributes each record to the gate.
- A record's content is only as truthful as the gate's inputs; MMF does not
  prove that the rail settled what the gate recorded beyond the binding of
  §5.5.

### 5.7 Deployment requirements

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
mandate's expiry in the canonical number form of §5.6 and `bounds_hash` is the
lower-case hex SHA-256 of

```
principal | ISSUED_AT | parent_mandate_id | max_amount | currency | sorted(allowed_payees) joined by "," | expires_in_days | velocity_limit | total_budget | budget_window_days
```

with `ISSUED_AT` in canonical number form, integers in base 10, and a null
`parent_mandate_id` or `total_budget` written as the empty string. Every
authority-bearing field is therefore covered: none can be altered without
invalidating the signature.

`revoked` is deliberately **not** signed: revocation state is mutable, and
revoking a mandate never invalidates its credential (check `REVOKED` or
`REVOKED_REGISTRY` denies instead).

A gate is **credential-bound** when it is given credentials and the
principal's expected public key. A credential-bound gate MUST verify, before
any other check, **every member of the resolved chain**: for each member the
key must match, the credential must name that mandate and agent, the bounds
hash must match, the credential must be unexpired, and the signature must be
valid; any failure — including a missing credential for any chain member —
denies with `CREDENTIAL`. Supplying only one of credentials / principal key
also denies with `CREDENTIAL`: there is no mode in which verification is
skipped.

**Trust model.** The gate trusts the principal public key it is configured
with, out of band. MMF-v0.3.1 does not define key discovery, rotation, who may
act as a principal, or proof that the agent presenting a transaction holds
the key of the named agent; these belong to the identity layer (for example
SPIFFE workload identity or DID-based verification) and are out of scope.

## 8. Conformance

An implementation conforms to MMF-v0.3.1 if it:

1. Validates mandates per §2.2 and amounts per §3.1
2. Evaluates §5.1 checks in order, chain-wide, first-failure-wins, fail-closed on registry errors
3. Implements serializable check-and-record (§5.2), the idempotency ledger (§5.3) and the
   lifecycle of §5.4 including the single-dispatch `claim` step
4. Records every decision and lifecycle event atomically with the state update (§5.6),
   with the normative `check` and `event`
5. Produces evidence hashes per the byte-level canonical serialization of §5.6
6. Passes every vector in `spec/vectors/core.json`, `delegation.json`, `lifecycle.json` and
   `evidence.json` (`allowed`, `check` and — where given — `replayed`, `status`, `events` and
   errors must all match; for delegation, validity must match)
7. Implements `verify_chain()`, `verify_chain(anchored_head)` and `verify_chain(public_key)` per §5.6

Adapters additionally conform if they implement payee binding per §5.5.

Reason strings, detail strings and internal APIs are implementation-defined.

## 9. Changes from v0.2

- **Amounts are integers in minor units** (§3.1), bounded to [1, 2⁵³−1]; floats are invalid
  (`NON_POSITIVE`). Conversion rejects excess precision instead of rounding.
- **`tx_id`, idempotency and `TX_CONFLICT`** (§5.3): retries return the stored decision
  (`REPLAY`) and reserve nothing.
- **Transaction lifecycle** (§5.4): AUTHORIZED / SETTLED / VOIDED / DENIED; `void` releases
  reservations across the chain; MMF now bounds authorizations with explicit settlement
  outcomes instead of treating every approval as spent.
- **Payee binding** (§5.5) for adapters.
- **Serializable state across processes** (§5.2): store-level transaction required for shared
  stores (v0.2 required only in-process atomicity; a shared SQLite store could overspend under
  multi-process contention).
- **Evidence**: hash covers `event` and `tx_id`; amounts serialized as integers; optional gate
  signatures and `verify_chain(public_key)`; integrity, completeness and authenticity specified
  separately.
- **Credentials**: amounts in the bounds hash are integers; trust model stated; `revoked`
  explicitly unsigned.
- Vectors: 30 core + 17 delegation + 15 lifecycle + 2 evidence-hash (64 total).
