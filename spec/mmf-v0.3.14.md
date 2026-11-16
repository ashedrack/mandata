# The Mandata Mandate Format (MMF) — v0.3.14

**Status:** Draft specification (v0.3.14; supersedes [v0.3.13](mmf-v0.3.13.md), [v0.3.12](mmf-v0.3.12.md), [v0.3.11](mmf-v0.3.11.md), [v0.3.10](mmf-v0.3.10.md), [v0.3.9](mmf-v0.3.9.md), [v0.3.8](mmf-v0.3.8.md), [v0.3.7](mmf-v0.3.7.md), [v0.3.6](mmf-v0.3.6.md), [v0.3.5](mmf-v0.3.5.md), [v0.3.4](mmf-v0.3.4.md), [v0.3.3](mmf-v0.3.3.md), [v0.3.2](mmf-v0.3.2.md), [v0.3.1](mmf-v0.3.1.md), [v0.3](mmf-v0.3.md) and [v0.2](mmf-v0.2.md))
**Reference implementation:** `mandata` ≥ 0.4.14 (Python)
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
| `mandate_id` | string | MUST | Unique identifier; the revocation registry key. **Immutable and bound for life to exactly one signed mandate object** — a `mandate_id` MUST NOT be reused for a different mandate or bounds set (§5.6 evidence depends on it) |
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
| `bounds.total_budget` | integer\|null | MAY | Minor units, 1 ≤ value ≤ 2⁵³−1; cumulative cap over `budget_window_days`; null = unlimited. Unresolved open reservations (AUTHORIZED or CLAIMED) count against available budget at ANY age — an open claim that outlives the window still consumes it until settled or voided |
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
  `bounds.currency` exactly; MMF-v0.3.14 does not convert currencies.
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
  conforming implementation can represent exactly — the IEEE-754
  safe-integer bound). Anything else — zero, negative, fractional,
  non-numeric, NaN, ±infinity, or out of range — is invalid.
- **Checked arithmetic.** The same bound applies to *accumulated* totals
  (windowed spend, outstanding reservations). Implementations MUST use
  checked arithmetic on every addition and subtraction: a language whose
  integers silently lose precision or wrap (e.g. 64-bit float, fixed-width
  overflow) MUST NOT be used unguarded — use arbitrary-precision integers
  or explicit checks. Where an accumulated total plus a new amount would
  exceed 2⁵³ − 1, evaluation MUST deny `BUDGET`: the gate cannot prove the
  budget is available, so it fails safe. An overflow must never make an
  exhausted budget appear available.
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

The gate MUST evaluate the following ten checks **in order** and MUST stop at the
first failure; each check has a distinct denial identifier. `WITHIN` is the
success outcome reached when all ten pass — it is not itself a check. A transaction is allowed **only if all checks pass**.

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
| 8 | `VELOCITY` | Any chain member has `velocity_limit > 0` and the number of authorizations **issued** in the trailing 86400 s `>= velocity_limit` |
| 9 | `BUDGET` | Any chain member has `total_budget` set and `outstanding + amount > total_budget` (outstanding = unreleased windowed spend + unresolved open reservations at any age, §5.2) |
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
  chain := resolve(m)                     # pure function; parent links are
                                          # immutable once set by delegate()
  serialize on (chain or [m]) + store     # leaf only when chain unresolvable
  within the serialization:
      if ledger has tx.tx_id:                          # §5.3 — ALWAYS first
          if same request: return REPLAY of the stored decision
          else:            decide(TX_CONFLICT)         # ledger entry UNCHANGED
      if chain is unresolvable: decide(INVALID_CHAIN)
      for node in chain (leaf → root):                  # §7 — unconditional
          if credential(node) missing, fails to verify,
             or is not signed by the root principal: decide(CREDENTIAL)
      for check in [REVOKED_REGISTRY, REVOKED, EXPIRED, NON_POSITIVE,
                    CAP_EXCEEDED, CURRENCY, PAYEE, VELOCITY, BUDGET]:
          for node in chain (leaf → root):
              if check fails for node:  decide(check)
      reserve tx.amount on every node; status := AUTHORIZED; decide(WITHIN)
decide(c): store (tx_id, request, c, status) in ledger; append evidence; return
```

The ledger lookup is the **first step inside serialization**, before the
`INVALID_CHAIN` decision and all ordered checks: a decided `tx_id` is final,
so a replay returns the *original* decision even if the chain has since
become resolvable, and a conflicting request cannot overwrite or shadow the
stored one. Because the lookup happens inside the serialization, two
concurrent requests for the same `tx_id` cannot both be decided: the loser
either replays or conflicts. `TX_CONFLICT` is an evidence-only denial — it
MUST append a `DECISION` record carrying the attempted request, and MUST NOT
modify the stored ledger entry (request, decision or reservation).

`INVALID_CHAIN` is decided — and recorded — like an ordered-check denial,
but it is *not* a check in the table: it fires during resolution, so a
malformed chain has no defined bounds to check. Because there is no chain to
lock, the serialization for this path is the leaf mandate (plus the store's
transaction): the ledger lookup, the denial record and the evidence append
still happen inside one serialized transaction, so an `INVALID_CHAIN`
decision is replayed exactly like any other denial and never races a
concurrent request for the same `tx_id`.

### 5.2 State requirements

- Velocity and budget measure different things and are released
  differently. **Velocity** counts every authorization *issued* in the
  window — including ones later voided — because a voided payment still
  consumed an authorization slot; otherwise rapid authorize-and-void
  cycling would evade the rate limit. **Budget** counts *outstanding*
  reservations; a voided authorization MUST be released from every chain
  member's budget (§5.4). Denied transactions consume neither.
  *Outstanding* means unreleased spend within the window **plus every
  unresolved open reservation regardless of age** — an AUTHORIZED or
  CLAIMED transaction is money possibly committed and keeps consuming
  budget until it settles or voids. Worked example: `total_budget` = 100
  over a 1-day window; day 0, `t1` for 60 is claimed but never resolved;
  day 2, a request for 50 denies `BUDGET` because the still-open 60 plus
  50 exceeds 100 even though t1 aged out of the window. Had t1 settled on
  day 0, the request is allowed — resolved spend ages out normally.
  **Aging is by authorization timestamp, not settlement time.** A
  reservation authorized on day 1, claimed on day 2 and settled on day 40
  (window 30 days) counts as unresolved spend until it settles and stops
  counting at the moment it settles, because its authorization timestamp
  is by then outside the window. Vector L19 fixes this.
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

**Ordering.** Within the store transaction (§5.2) the gate MUST proceed in
this order: (1) begin the serializable transaction; (2) look up `tx_id` in
the ledger; (3) for a decided `tx_id`, return the replay or `TX_CONFLICT`
result **without consulting the current delegation chain** — a decided
transaction never depends on present chain validity; (4) only for a *new*
`tx_id`, resolve and validate the chain under serialization; (5) evaluate
the checks, reserve and append evidence atomically.

*Lock planning.* Resolving the chain before the transaction, to decide which
mandate locks to take, is permitted because it is pure and delegation
links are immutable once set (§6). The authoritative resolution in step 4
MUST yield the same chain identity. If it does not, link immutability has
been violated and the gate MUST fail closed (`INVALID_CHAIN`); it MUST NOT
acquire additional ancestor locks while already holding others, which could
deadlock against the root-first lock order. Because a decided `tx_id` is
answered before any resolution matters, revocation, expiry or resolution
failure after the decision never shadows a stored result and never issues
new authority.

**Resolution failure is a result, never an exception.** Preliminary chain
resolution is non-authoritative: a malformed parent link, a cycle or an
unavailable ancestor MUST be represented as an unresolved-chain *result*
and MUST NOT terminate evaluation before the serialized ledger lookup. A
previously decided `tx_id` MUST remain retrievable even when the current
delegation chain is invalid. For a new `tx_id`, the gate MUST record an
`INVALID_CHAIN` denial atomically with its evidence. An *identity change*
between the two resolutions is a distinct case from a resolution failure:
it indicates a violation of link immutability and MUST also fail closed.

**Namespace.** The ledger key is `tx_id` **within one gate's ledger** (one
shared store): the identifier is not mandate-scoped. Two unrelated
transactions from different mandates that collide on the same `tx_id` fail
closed — the second request mismatches the stored request and denies as
`TX_CONFLICT`. Producers SHOULD therefore generate keys that are unique per
gate (for example a UUID, or a prefix of cart or mandate id). Lifecycle
operations (`claim`, `settle`, `void`) take a `tx_id` and MUST verify the
stored request's `mandate_id` equals the mandate they are invoked on, so a
key belonging to another mandate is rejected rather than transitioned.

- A call whose `tx_id` is in the ledger with an **identical request** MUST
  NOT be re-evaluated and MUST NOT reserve again. The gate MUST return the
  stored `allowed` and `check`, with `replayed = true` and the current
  `status`, and MUST append an evidence record with event `REPLAY`.

**Request equality is exact, typed and unnormalized.** Requests compare
field-wise on their *parsed typed values* — JSON type first, then value —
not on raw lexical tokens and not after coercion: `49.99` (number), the
string `"49.99"` and the integer `4999` are three *distinct* requests;
JSON numbers with no fractional part compare integral-equal regardless of
lexical form — a number is *integral* iff it equals its truncation after
parsing to IEEE-754 double (`1` and `1.0` parse to the same integral value
and are equal; `1.5` is never integral). This rule is defined on the implementation's typed request model,
so a conforming implementation MUST specify how its parser maps wire
values to typed values. A malformed request is not refused admission to
the ledger — it is evaluated, denied under the ordered checks
(`NON_POSITIVE` for a non-integer amount), and recorded as a durable,
replayable denial: the malformed attempt is itself auditable. Identical
malformed retries replay; different malformed encodings conflict as
`TX_CONFLICT`.
- A call whose `tx_id` is in the ledger with a **different request** MUST be
  denied with check `TX_CONFLICT` and recorded (event `DECISION`). The
  ledger entry is unchanged.
- A denial is final for its `tx_id`: a replay returns the same denial even
  if the cause has since gone away. A new attempt needs a new `tx_id`.

### 5.4 Transaction lifecycle

```
            enforce: allowed          claim            settle
  (none) ─────────────────────▶ AUTHORIZED ───────▶ CLAIMED ────▶ SETTLED
     │                              │              │
     │ enforce: denied              │ void         │ void (authoritative
     ▼                              ▼              ▼  cancellation ref.)
   DENIED                         VOIDED ◀────────┘
```

- `settle` is valid **only** from `CLAIMED`: it records that the rail
  executed the dispatched instruction. An `AUTHORIZED` transaction was
  never dispatched — it can only be claimed or voided, never settled
  directly.
- `void` is valid from `AUTHORIZED` (never dispatched — no reference
  needed) and from `CLAIMED` (requires the authoritative cancellation
  reference below).

Two distinct idempotency properties:

- **Authorization idempotency** (§5.3): the gate does not reserve the same
  request twice.
- **Settlement uniqueness** (this section): an authorization is not
  *dispatched* twice. `enforce` does not prevent two concurrent dispatcher
  workers from both observing AUTHORIZED and submitting to the rail — so
  settlement goes through an explicit, atomic **claim** step.

- **AUTHORIZED** — reserved against every chain member's velocity and budget.
- **Revocation and expiry policy by lifecycle state.** A *replay* returns
  the stored decision as a historical fact — it issues no new authority, so
  a replayed `AUTHORIZED` answer describes what was decided, not what may
  still be done. **`claim` MUST re-verify revocation AND expiry for every
  member of the delegation chain** (embedded flags and, for registry-bound
  mandates, the registry — failing closed on registry faults): an
  AUTHORIZED but unclaimed transaction under a revoked or expired chain
  member MUST be rejected. The check runs inside the same serialization as
  the transition (§5.2), so the linearization point is the claim's commit
  **relative to the state the gate consulted during that serialized
  validation**: a claim MUST be rejected for any revocation or expiry
  visible in the embedded flags and in the registry response obtained
  during validation. A revocation committed at a registry but not yet
  visible to the gate (propagation delay) is *not* detected — MMF does not
  specify registry consistency, and a stronger guarantee (a revocation
  committed before a claim commit always prevents it) requires a
  coordinated registry/claim consistency mechanism outside this
  specification. Deployments needing that guarantee SHOULD consult a
  strongly consistent registry within the claim's serialization.
  facts or release reservations, granting no authority. Credential
  *signatures* are established at authorization and are not re-verified;
  DENIED/SETTLED/VOIDED transactions have no legal transitions regardless.
- **CLAIMED** — a dispatcher has claimed the authorization for exactly one
  settlement attempt. `claim(mandate, tx_id, payee, amount, currency)` MUST
  atomically: verify the instruction binds to the stored request, transition
  AUTHORIZED → CLAIMED under the same serialization as `enforce`, and append
  a `CLAIM` evidence record. A second `claim` MUST fail; a `claim` whose
  instruction differs from the stored request MUST fail.
- **The claim slot is permanently consumed.** A `tx_id` can be claimed at
  most once — there is no `unclaim` and no re-claim, so an authorization is
  never dispatchable twice under any recovery path.
- **The dispatch guarantee has exactly three levels, and they must not be
  conflated.** (1) *The gate* guarantees **at most one successful claim per
  `tx_id`** — atomic under §5.2 serialization, unconditional within the
  gate's own state. (2) *At-most-once rail submission* additionally requires
  a **trusted dispatcher** that durably records its submission attempt and
  never resubmits an instruction whose outcome became ambiguous (a network
  timeout after the rail may have received it) — the gate's atomicity ends
  at the process boundary and cannot by itself prevent a second external
  submission. (3) *Exactly-once execution* remains the rail's responsibility
  and requires the rail to honor a durable idempotency key. The gate also
  cannot stop an operator who bypasses it entirely and invokes the rail
  directly — that is a deployment-security property (§5.7), not a protocol
  one.
- Three distinct operations must not be conflated. **Authorization
  replay** — an identical `enforce` for the same `tx_id` returns the stored
  decision without reserving again. **Rail reconciliation** — an operator
  or adapter may *query* the rail for the existing instruction by its
  idempotency key to learn its outcome; this is a read, not a dispatch.
  **A second rail submission is never permitted** through the consumed
  claim slot: a confirmed-never-invoked transaction is voided (with a
  cancellation reference, releasing the reservation) and the retry uses a
  **new** `tx_id`; an unconfirmed outcome MUST stay CLAIMED (fail-safe: an
  unreleased reservation costs budget, a double-dispatch costs money).
  Exactly-once execution additionally requires the rail to honor its
  idempotency key.
- **SETTLED** — the rail confirmed funds moved; the reservation stays counted.
- **VOIDED** — the rail confirmed funds will not move (failure, cancellation,
  expiry of the authorization, or confirmed-never-invoked recovery); the
  budget reservation MUST be released from every chain member — the velocity
  slot is NOT released (§5.1). `void` MUST hold the same serialization as
  `enforce`.
- **Voiding a CLAIMED transaction is a reconciliation action.** Because a
  claimed instruction may be in flight, `void` on a CLAIMED `tx_id` MUST be
  rejected unless the caller supplies a non-empty **cancellation
  reference** — an identifier of the authoritative confirmation that the
  rail did not execute and cannot subsequently execute it: a rail decline
  response ID, a signed rail/adapter verdict, or a record from a trusted
  reconciliation system. The reference MUST (a) attest that execution did
  not occur **and cannot subsequently occur** for *that specific
  transaction* — a generic rail health or batch status is not sufficient;
  (b) be bound to the transaction's own rail instruction or idempotency key
  (the reference SHOULD name or hash the instruction identifier, so it
  cannot be replayed to release a different reservation); (c) be verified
  through a trusted channel, not the transacting agent; and (d) be retained
  for audit — it is recorded in the VOID record's hashed `REFERENCE` field
  so *what* justified releasing the reservation is itself auditable. A bare
  caller assertion is not confirmation, and an authoritative `VOID` is
  permanent: it releases the budget reservation. An
  uncertain in-flight outcome MUST stay CLAIMED until reconciliation:
  voiding early would release the budget reservation while money may still
  move. Voiding an AUTHORIZED transaction needs no reference — it was
  never dispatched.
- `settle` MUST require source status `CLAIMED`; `void` MUST require
  `AUTHORIZED` or `CLAIMED`. A `settle` on any other status — including
  `AUTHORIZED`, which was never dispatched — is rejected as
  `SETTLE:STATUS`.
- `settle` and `void` MUST be idempotent on their target state (repeating
  them returns the transaction unchanged and appends no evidence).
- Every other transition (settling or voiding a DENIED, VOIDED/SETTLED, or
  unknown `tx_id`, one belonging to another mandate, claiming anything but
  AUTHORIZED) MUST be rejected and MUST append a `REJECTED` evidence record
  (§5.6): a binding-mismatched claim is exactly the substitution signal the
  audit exists to catch, and rejected transitions carry no state change, so
  recording them is safe. A SETTLED transaction cannot be voided: refunds,
  partial captures and chargebacks are rail concerns outside MMF.
- An AUTHORIZED or CLAIMED transaction whose outcome is unknown (for example
  a settlement timeout) MUST remain reserved until the rail's outcome is
  known (fail-safe). The agent retries with the **same** `tx_id` (§5.3); the
  rail MUST be invoked idempotently on the same key.

### 5.5 Payee binding

The gate checks the proposed payee and amount; adapter binding ensures that
any instruction submitted through a correctly mediated settlement path
matches the approved payee, amount and currency. Actual execution remains
the responsibility of the rail — binding constrains what is *submitted*,
not what the rail ultimately does.

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

Evidence obligations attach to a **completed, durably committed
evaluation** — not to every call. Three cases are distinct: a *completed
decision* (the ledger write, reservation and evidence commit atomically)
MUST produce exactly one record; an *aborted evaluation* (a storage or
serialization failure rolls the whole operation back) produces no record
and MUST release no authorization — the gate fails closed, never silently;
and a request that *never reaches the gate* cannot be audited by it.

Every `enforce` call whose evaluation completes MUST append exactly one
record (event `DECISION` or
`REPLAY`); every accepted `claim` / `settle` / `void` that **changes state** MUST
append exactly one record (event `CLAIM` / `SETTLE` / `VOID`); a repeated
`settle` or `void` on its target state is an idempotent no-op that MUST
return the transaction unchanged and MUST append **no** record — not a
lifecycle record and not a `REPLAY` (that event is reserved for `enforce`); every *rejected* lifecycle
transition MUST append exactly one record (event `REJECTED`, `allowed =
false`, `check` naming the rejected operation). A record contains at minimum:
`event`, `tx_id`, `mandate_id`, `agent`, `payee`, `amount`, `currency`,
`allowed`, `check`, `status`, `timestamp`, `prev_hash`, where `status` is the
lifecycle status the record establishes (AUTHORIZED, CLAIMED, SETTLED,
VOIDED, DENIED) or the empty string for a REJECTED record — every
security-relevant field is covered by the hash, so a record cannot be
relabelled to a different event, instruction or status without breaking the
chain. Records MAY carry an advisory `detail` string and a `signature`,
neither covered by the hash.

**Scope of "every".** These guarantees cover every request the gate
processes under its failure model: because the ledger write, reservation and
evidence append are one atomic operation, a storage failure rolls the whole
decision back and no authorization is released — the gate fails closed
rather than silently. MMF cannot produce evidence for a request that never
reached the gate.

**Atomicity.** The ledger write, the reservation update and the evidence
append are **one atomic durable operation**: a crash between them MUST NOT
leave a decided transaction without its evidence record, nor evidence for a
decision that was not recorded. An implementation whose evidence lives
outside the state store MUST use a transactional outbox with specified
crash-recovery semantics.

`prev_hash` of the first record is the genesis value: exactly 64 ASCII `0`
characters.

**Mandate preservation.** A record's `mandate_id` is meaningful only
alongside the mandate it names: `mandate_id` MUST be immutable and bound
for life to one signed mandate object, and the gate SHOULD durably
preserve that object (or its signed credential form, which embeds the
bounds hash) for as long as its evidence may need interpreting — a bare
identifier without the mandate is not independently interpretable.

**Completeness qualification.** A valid hash chain proves consistency of
the records it contains — not that every externally observed request
reached the gate, and not that records past the last external anchor
survive truncation by whoever holds write access. Completeness against
operator-controlled truncation requires the anchored checkpoints below.

**Chain scope.** The chain is **per evidence log** — in practice, per gate
deployment: every mandate evaluated through a gate appends to that gate's
single chain, so evidence for a mandate, its delegates and its lifecycle
events interleave in one tamper-evident sequence keyed to the store the log
is backed by. A REJECTED record carries the *attempted* instruction (the
payee, amount and currency the caller submitted), not the stored
authorization's — it is evidence of what was tried, not of what stood.

**Canonical serialization (v3).** The record hash is

```
SHA-256( UTF-8( len:event | len:tx_id | len:mandate_id | len:agent |
                len:payee | len:AMOUNT | len:currency | len:ALLOWED |
                len:check | len:status | len:REFERENCE | len:TIMESTAMP_MS |
                len:prev_hash ) )
```

Every field serializes as `<decimal UTF-8 byte length>:<UTF-8 bytes>` and
fields are joined by single `|` bytes. The length prefixes make the format
self-delimiting: `|` inside a *value* is never parsed, so **any** field
value is encodable and malformed or adversarial inputs can always be
recorded rather than rejected for delimiter-sensitivity — every evaluated
request is auditable without exception. Parsing is: read decimal digits to
`:`, consume exactly that many bytes, then one `|` (absent after the last
field).

**Normative serialization table.** Every field MUST serialize exactly as
specified — two implementations hashing the same record MUST produce
identical bytes:

| Field | Type | Encoding | Absent/null |
| ----- | ---- | -------- | ----------- |
| `event` | enum | One of `DECISION`, `REPLAY`, `CLAIM`, `SETTLE`, `VOID`, `REJECTED` — ASCII uppercase | never absent |
| `tx_id` | string | UTF-8, exact value — no normalization | `0:` when absent |
| `mandate_id` | string | UTF-8, exact value — no normalization | `0:` |
| `agent` | string | UTF-8, exact value — no normalization | `0:` |
| `payee` | string | UTF-8, exact value — no normalization; any bytes, `|` included | `0:` |
| `AMOUNT` | integer | Base-10, no sign, no leading zeros, no separators; any value outside §3.1's range (including negative or fractional inputs on rejected requests) → the literal `invalid` | `invalid` |
| `currency` | string | UTF-8, exact value — no normalization; uppercase ISO 4217 or declared code | `0:` |
| `ALLOWED` | boolean | The ASCII literals `true` or `false` — lowercase, nothing else | never absent |
| `check` | string | Normative check identifier (§5.1) or lifecycle-operation name, ASCII uppercase. On a `REJECTED` record the value MUST be `<OP>:<CAUSE>` where `OP` is the attempted operation (`CLAIM`/`SETTLE`/`VOID`) and `CAUSE` is one of `UNKNOWN_TX`, `STATUS`, `BINDING`, `REVOKED`, `EXPIRED`, `REVOKED_REGISTRY`, `REGISTRY`, `CANCELLATION` — so two conforming implementations rejecting the same attempt produce identical records | `0:` |

**Rejection precedence.** Lifecycle rejections are as deterministic as
authorization denials: causes evaluate in a fixed order and the first match
wins —

| Operation | Precedence order (first match) |
| --------- | ------------------------------ |
| `CLAIM` | `UNKNOWN_TX` → `STATUS` → `BINDING` → `REVOKED` → `EXPIRED` → `REVOKED_REGISTRY` → `REGISTRY` |
| `SETTLE` | `UNKNOWN_TX` → *(idempotent: already `SETTLED` ⇒ unchanged, no record)* → `STATUS` (any status other than `CLAIMED`) |
| `VOID` | `UNKNOWN_TX` → *(idempotent: already `VOIDED` ⇒ unchanged, no record)* → `STATUS` (any status other than `AUTHORIZED`/`CLAIMED`) → `CANCELLATION` (`CLAIMED` without an authoritative reference) |

The idempotency step is evaluated **before** `STATUS`: a repeated `settle` on
`SETTLED` or `void` on `VOIDED` is never a `STATUS` rejection. Vectors L06 and
L20 fix the expected evidence-record count (zero for the repeat).

`UNKNOWN_TX` covers an unknown `tx_id` and a `mandate_id` mismatch;
`STATUS` covers any unexpected current status (for `SETTLE` that means
anything but `CLAIMED`; for `VOID` anything but `AUTHORIZED`/`CLAIMED`);
`BINDING` covers a payee/amount/currency mismatch against the stored
authorization; `REVOKED` covers embedded-flag revocation, chain-wide;
`EXPIRED` covers chain-wide expiry; `REVOKED_REGISTRY` covers
registry-reported revocation; `REGISTRY` covers a registry fault (fail
closed). The ordering mirrors evaluation order: local state first
(embedded flags), then expiry, then the external registry — a registry
that cannot answer cannot establish revocation, so its unavailability is
reported only after the local causes are exhausted; `CANCELLATION` covers
a missing authoritative cancellation reference.
| `status` | enum | `AUTHORIZED`/`CLAIMED`/`SETTLED`/`VOIDED`/`DENIED` | `0:` on REJECTED |
| `REFERENCE` | string | UTF-8, exact value — no normalization; normative provenance asserted by a trusted caller (the cancellation reference on a `VOID` of CLAIMED, §5.4). The **raw reference string** is retained in the record and hashed as-is — not a digest of it. MMF does not define how a verifier retrieves the underlying confirmation: an audit validating more than the hash chain MUST resolve the reference against the named trusted source out of band | `0:` |
| `TIMESTAMP_MS` | integer | Integer Unix epoch **milliseconds**. The *normative input* is the integer millisecond value itself; implementations whose clocks yield floating-point seconds convert once via `floor(t × 1000 + 0.5)` (round-half-up) — that conversion is an API convenience, not part of the protocol. Base-10, no leading zeros. The value MUST be finite (NaN/±infinity invalid — such a record MUST NOT be produced), MUST fit a signed 64-bit integer, and MAY be negative (pre-epoch) — serialization is uniform | never absent |
| `prev_hash` | hex | Exactly 64 **lowercase** hexadecimal characters; genesis = 64 `0`s | never absent |

There is **no distinct null marker**: absent, null and empty all serialize
as `0:` (they are canonically identical in MMF). String fields serialize
the **exact value the gate evaluated** — no NFC or other Unicode
normalization — reconciling evidence with §5.3's exact, unnormalized
request equality: two strings differing in code points are different
requests *and* produce different evidence bytes, so a record is a
byte-faithful representation of what was submitted. **One qualification:**
`AMOUNT` outside the protocol range serializes as the literal `invalid`,
so two different malformed amount inputs (for example `49.99` and `-7`)
are *distinguished in the ledger's typed request* but serialize to the
same `invalid` marker — the record is faithful for every protocol-range
field, not for out-of-range amount values; the ledger's stored request
(§5.3) retains the full distinction for replay semantics. An implementation
MUST NOT normalize — original scalar sequences are preserved exactly. The
advisory `detail` string (denial prose) is deliberately NOT hashed: reason
text is implementation-specific, while the payload must be byte-identical
across implementations. `len` is the UTF-8 **byte** length as base-10 with
no leading zeros (`é` is two bytes in NFC → `2:é`; the same character as
NFD is three bytes → `3:é` — the length prefix always counts the exact
bytes submitted). The hex digest is lower-case.

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
- *Authenticity (optional conformance profile).* Gate signatures are an
  OPTIONAL profile layered on the mandatory hash chain — a conforming
  implementation need not sign; an implementation claiming the
  **signed-evidence profile** MUST sign every committed record. A gate SHOULD
  sign each record's hash (hex string, UTF-8)
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

**Signing model.** MMF uses **principal-authorized delegation with
agent-selected attenuation**: an agent proposes and constructs the
delegated mandate (choosing attenuated bounds for its sub-agent), but the
root principal MUST sign every chain member's credential (§7). The agent
holds no credential-issuing authority of its own — this is deliberately
not autonomous cryptographic delegation, where a delegate could mint child
credentials without involving the principal. The delegation-time cost of a
principal signature buys a hard invariant: no sub-agent can issue authority
the principal did not cryptographically approve.

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
  (null means unlimited and is wider);
- if the parent's `total_budget` is set, the child's `budget_window_days` MUST
  be ≥ parent's — a shorter window cycles the same allowance faster and is
  wider, not narrower. Windows need not be equal; because every authorization
  charges all ancestors, the effective authority is always the intersection
  of every chain member's bounds and windows.

Any widening MUST raise an error. Delegation from a revoked or expired parent
MUST fail. The child's `parent_mandate_id` MUST equal the parent's
`mandate_id`, and the parent MUST remain resolvable by the gate.

Because the gate evaluates the whole chain (§5), the child's effective
expiry is the earliest expiry in its chain, revoking any ancestor stops every
descendant, and sibling delegates share their ancestors' velocity and budget.

## 7. Credentials

A credential is the principal's Ed25519 signature over
the length-prefixed canonical form of §5.6:
`len:mandate_id | len:agent | len:bounds_hash | len:EXPIRES_AT`, where
`EXPIRES_AT` is the mandate's expiry in the canonical number form of §5.6
and `bounds_hash` is the
lower-case hex SHA-256 of the same length-prefixed form over

```
len:principal | len:ISSUED_AT | len:parent_mandate_id | len:max_amount | len:currency | len:allowed_payees(sorted, joined by ",") | len:expires_in_days | len:velocity_limit | len:total_budget | len:budget_window_days
```

with `ISSUED_AT` in canonical number form, integers in base 10, and a null
`parent_mandate_id` or `total_budget` written as the empty field (`0:`).
Every authority-bearing field is therefore covered: none can be altered —
or smuggled through a delimiter — without invalidating the signature.
Vector `C01` in `spec/vectors/credential.json` fixes both byte sequences
(bounds payload and signed payload) and the deterministic Ed25519 signature
for a fixed seed, so an independent implementation can verify it reproduces
the exact signed bytes.

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

**Issuer authority.** Every chain member's credential MUST verify against
the **root principal's** key — the principal at the head of the delegation
chain is the only recognized issuer. A delegate agent cannot self-issue a
credential for its own child: delegation attenuates bounds, but issuance
authority stays with the root principal, so a sub-agent can never mint
authority the principal did not sign.

**Trust model.** The gate trusts the principal public key it is configured
with, out of band. MMF-v0.3.14 does not define key discovery, rotation, who may
act as a principal, or proof that the agent presenting a transaction holds
the key of the named agent; these belong to the identity layer (for example
SPIFFE workload identity or DID-based verification) and are out of scope.

## 8. Conformance

An implementation conforms to MMF-v0.3.14 if it:

1. Validates mandates per §2.2 and amounts per §3.1
2. Evaluates §5.1 checks in order, chain-wide, first-failure-wins, fail-closed on registry errors
3. Implements serializable check-and-record (§5.2), the idempotency ledger (§5.3) and the
   lifecycle of §5.4 including the single-dispatch `claim` step
4. Records every decision and lifecycle event atomically with the state update (§5.6),
   with the normative `check` and `event`
5. Produces evidence hashes per the byte-level canonical serialization of §5.6
6. Passes every vector in `spec/vectors/core.json`, `delegation.json`, `lifecycle.json`,
   `evidence.json` and `credential.json` (`allowed`, `check` and — where given — `replayed`,
   `status`, `events`, canonical bytes and signatures must all match; for delegation
   and credential vectors, validity must match — C02–C06 MUST each fail verification
   for the stated reason)
7. Implements `verify_chain()`, `verify_chain(anchored_head)` and `verify_chain(public_key)` per §5.6

Adapters additionally conform if they implement payee binding per §5.5.

Reason strings, detail strings and internal APIs are implementation-defined.

## 9. Changes from v0.3.13

- **§5.3 replay-first order** made explicit as five steps; authoritative
  chain resolution applies to new `tx_id`s only; lock-planning rule and
  fail-closed handling of an identity change specified.
- **§5.6 idempotent step** precedes `STATUS` in the SETTLE/VOID rows;
  vector **L20** covers repeated void (L06 covers repeated settle).
- **§5.6**: claim ordering vs §5.1 authorization ordering stated as
  intentionally different; `REFERENCE` retained raw with out-of-band
  verification.

## 9. Changes from v0.3.12

- **§5.5 binding**: no longer claims the rail moves the approved amount;
  binding constrains what a correctly mediated path *submits*.
- **§5.6 repeated lifecycle calls**: a repeated `settle`/`void` is a no-op
  with no evidence record (no `REPLAY`), so evidence chains agree across
  implementations.
- **§5.1**: ten ordered *failure checks*; `WITHIN` is the success outcome,
  not a check.
- Editorial: hash determinism stated as serialization determinism.

## 9. Changes from v0.3.11

- **§5.4 revocation visibility**: the claim guarantee is stated relative to
  the state visible during serialized validation; registry propagation
  delay is explicitly outside the protocol guarantee.
- **§5.2 aging rule**: settled spend ages out by authorization timestamp;
  vector L19 covers a reservation that settles after its window.
- **§5.6**: evidence hash-binding statement aligned with the `invalid`
  amount marker.

## 9. Changes from v0.3.10

- **§5.4 settle semantics**: `settle` is valid only from `CLAIMED` —
  settling an `AUTHORIZED` (never-dispatched) transaction is rejected as
  `SETTLE:STATUS`. The state diagram shows `CLAIMED → SETTLED` and
  `CLAIMED → VOIDED` as distinct transitions.
- **§5.6 precedence**: SETTLE and VOID have separate precedence rules;
  claim ordering now distinguishes `REVOKED` (embedded) → `EXPIRED` →
  `REVOKED_REGISTRY` (registry-reported) → `REGISTRY` (fault), matching
  evaluation order.
- **§5.6 evidence fidelity qualified**: out-of-range amounts serialize as
  `invalid`; the ledger's typed request retains the full distinction.

## 9. Changes from v0.3.9

- **§5.3 resolution failure**: preliminary chain resolution is normatively
  non-authoritative — failure is a result, never an exception past the
  ledger lookup; identity change vs resolution failure distinguished.
- **§5.6 rejection precedence**: a fixed cause-evaluation order for CLAIM
  and SETTLE/VOID rejections — lifecycle rejections are now as
  deterministic as authorization denials.
- **§5.6 exact-bytes strings**: NFC normalization removed; evidence and
  credential serialization preserve the exact evaluated value, reconciled
  with §5.3's unnormalized request equality.
- **§7 credential vectors**: C02–C06 negative vectors — bad signature,
  tampered bounds hash, wrong issuer key, wrong agent and wrong mandate
  binding MUST all fail verification.
- **Wording**: "exactly-once execution" replaced with rail-defined
  idempotent execution semantics throughout.


- **§5.3 ordering**: the authoritative ledger lookup MUST be the first
  operation inside the store transaction; the chain MUST be re-resolved
  inside the serialization and evaluation fails closed if it changed.
- **§5.4 cancellation binding**: a cancellation reference MUST attest
  non-execution *for the specific transaction*, be bound to the rail
  instruction/idempotency key, come via a trusted channel and be retained.
- **§5.6 REJECTED cause**: the `check` field on REJECTED records is now
  normatively `<OP>:<CAUSE>` from a fixed cause set, so conforming
  implementations produce identical records for identical rejections.
- **§5.6 timestamp input**: integer epoch milliseconds is the normative
  input; float-seconds conversion is an API convenience.
- **§7 credential vector**: `credential.json` C01 fixes the bounds payload,
  signed payload and deterministic Ed25519 signature.


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
- Vectors: 30 core + 17 delegation + 20 lifecycle + 3 evidence-hash + 6 credential (76 total).
