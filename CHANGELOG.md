## [0.4.24] — implements MMF-v0.3.24

- 92 vectors (36 core), 235 tests.
- **Fix**: request strings bound for canonical evidence fields must be
  Unicode scalar sequences. An unpaired surrogate (e.g. JSON `"\uD800"`)
  or a non-string in `payee`/`currency`/`tx_id`, or a surrogate
  cancellation `reference`, is rejected before evaluation — no decision, no
  record. Previously such a value could be stored and then fail every
  subsequent chain hash (`UnicodeEncodeError` on `head_hash`/verify),
  effectively poisoning the log. New conformance vector V36; `lp_field`
  now fails with a clear error as a backstop.
- Spec: the registry timeout is a required finite *deployment* parameter;
  cross-implementation decision equivalence assumes identical registry
  observations and an identical timeout policy.

## [0.4.23] — implements MMF-v0.3.23

- No behavior change (91 vectors, 232 tests).
- **Withdrawn as overstated**: "the linearization point is the claim's
  commit". The claim's local commit is atomic, but the revocation/expiry
  check is linearized at the *observation* (flags read, registry response
  received). A revocation arriving between observation and commit does not
  prevent the claim — the gate serializes its own ledger, not the registry
  or the embedded `revoked` flag. Pinned by a test; global ordering needs
  versioned registry validation or a shared consistency mechanism
  (deployment-level, outside the spec).
- Spec: no reliance on idempotency-key reuse after the rail's retention
  period; raw-request log for malformed amounts needs its own access
  controls and retention.

## [0.4.22] — implements MMF-v0.3.22

- No behavior change (91 vectors, 231 tests).
- Spec §5.4: logical submission vs physical attempt; the normative baseline
  dispatcher policy is *no new logical submission after an uncertain
  outcome, reconcile read-only*. Same-key re-transmission is permitted only
  as a documented policy outside MMF's guarantees. Replaces a sentence that
  read as though resubmission were supported; the reference adapters have
  always followed the baseline (the claim slot is consumed, the transaction
  stays `CLAIMED`).
- Benchmark docstring states that the HTTP backend is loopback only.

## [0.4.21] — implements MMF-v0.3.21

- No behavior change (91 vectors, 231 tests).
- Spec: the registry wait bound is per consultation. A fault ends evaluation
  (cost ≤ one wait; measured 0.22 s at a 0.2 s timeout over a depth-5
  chain); a registry answering slowly inside the bound costs up to N × wait
  (measured 0.78 s for 5 × 0.15 s). An overall evaluation deadline is a
  SHOULD and is **not yet implemented** in the reference.
- Spec: a retry under a new `tx_id` has a different rail key; a replacement
  MUST NOT be authorized unless the original is established unable to
  execute (void with a validated cancellation reference first).

## [0.4.20] — implements MMF-v0.3.20

- No behavior change; the three behaviors below are now specified and
  pinned by tests (91 vectors, 230 tests).
- Spec: overflow rule scoped to the budget accumulator of members with
  `total_budget`; unbudgeted mandates are unaffected; velocity counters are
  exact counts. Test: 3 × (2^53 − 1) of spend on an unbudgeted mandate.
- Spec: bounded registry wait; a registry timeout is a registry fault
  (`REVOKED_REGISTRY` at enforce, `CLAIM:REGISTRY` at claim) and a rejected
  claim leaves the transaction `AUTHORIZED` and retryable. Tests: a flaky
  registry, and a real slow HTTP server bounded by a 0.2 s client timeout.
- Spec: tamper evidence is not transaction truth.
- **Correction**: the `ts/` directory was described as a "core port"; it is
  a 59-line pre-v0.3 scaffold with none of the current protocol. README
  files now say so.

## [0.4.19] — implements MMF-v0.3.19

- Spec/documentation only; no behavior change (91 vectors, 227 tests).
- Invalid-amount equivalence documented as a deliberate trade-off (stable
  denial replay over distinguishing malformed attempts).
- Rail idempotency key: default profile, `gate_id` stability/uniqueness
  requirements, and documented adapter-side transformations for rails with
  key length or alphabet limits.

## [0.4.18] — implements MMF-v0.3.18

- **Documentation correction (no behavior change to amount handling)**:
  specs v0.3.6–v0.3.17 and the paper described request equality and the
  ledger incorrectly. The reference never treated `49.99`, `"49.99"` and
  `4999` as three distinct requests, never treated `1` and `1.0` as equal,
  and never retained which malformed amount was submitted. Actual, fail-
  closed behavior: valid amount = same integer; every invalid amount is one
  equivalence class. Now specified (§3.1 lexical classification, §5.3
  equality classes) and pinned by vectors V31–V35 and L29; a mutation that
  accepts integral floats fails them.
- **New**: `rail_idempotency_key(gate_id, tx_id)` — gate-scoped,
  collision-resistant, deterministic rail key (SHA-256 of the
  length-prefixed form; pinned test value).
- **New**: optional `verify_cancellation` callback on `void()`; a verifier
  that returns false or raises rejects as `VOID:CANCELLATION` and the
  transaction stays `CLAIMED`.
- 91 vectors, 227 tests.

## [0.4.17] — implements MMF-v0.3.17

- No behavior change: the reference already satisfied every rule below;
  they are now specified and conformance-tested.
- New vectors **L27** (cross-mandate `TX_CONFLICT` evidence carries the
  attempting mandate, never the stored one), **L28** (rolling-window lower
  bound is inclusive — a `>=` → `>` mutation fails it) and **D18**
  (shorter-window parent, longer-window child, aged settled spend plus an
  unresolved authorization) — 85 vectors, 219 tests.
- Spec: rail idempotency key rule (`tx_id` verbatim or injective documented
  derivation); normative window arithmetic; evidence described as a
  canonical hash-bound representation, not a verbatim copy.
- Conformance runner: second mandate on a shared ledger, record-field
  assertions, claim/settle steps in delegation vectors.

## [0.4.16] — implements MMF-v0.3.16

- **Fix (evidence leak)**: on a gate-wide shared store, a rejected
  `settle`/`void`/`claim` for a transaction belonging to *another mandate*
  copied that transaction's payee, amount, currency and status into the
  rejecting mandate's `REJECTED` evidence record. Such a transaction is now
  `UNKNOWN_TX` and its stored fields are never copied. Regression test added.
- **Conformance**: rejection causes and precedence are now testable —
  lifecycle vectors L21–L26 assert the `<OP>:<CAUSE>` check and attempted
  instruction fields (the runner supports a per-vector registry). 82 vectors,
  216 tests.
- Spec: attenuation proof scoped to one hypothetical history; deployed
  ancestor counters can only further restrict.

## [0.4.15] — implements MMF-v0.3.15

- Spec: structural vs effective delegation attenuation defined over
  accepted histories; standalone budget implication argued and checked by a
  randomized test (200 histories; fails when the window is narrowed).
- Spec: `settle` wording aligned with the idempotent rule; forensic limit of
  malformed-amount evidence; explicit atomic-commit rule.
- No behavior change — 76 vectors, 209 tests.

## [0.4.14] — implements MMF-v0.3.14

- Replay-first order made structural: authoritative chain re-resolution now
  runs only for *new* `tx_id`s, so a decided transaction never depends on
  the current chain. New test: an allowed transaction replays after its
  chain becomes invalid; a new one denies `INVALID_CHAIN`.
- Spec: idempotent settle/void step precedes `STATUS` in the rejection
  precedence; new vector L20 — 76 vectors, 208 tests.
- Spec: claim vs authorization precedence distinction, lock-planning rule,
  raw `REFERENCE` retention.

## [0.4.13] — implements MMF-v0.3.13

- Spec-only semantic clarifications (no behavior change): adapter binding
  constrains what is submitted, not what the rail executes; repeated
  `settle`/`void` append no evidence (no `REPLAY`); ten failure checks vs
  the `WITHIN` outcome; hash determinism stated as serialization
  determinism.

## [0.4.12] — implements MMF-v0.3.12

- Revocation guarantee at `claim` stated precisely: relative to state
  visible during serialized validation; registry propagation delay is
  outside the protocol guarantee.
- Budget aging rule made explicit (by authorization timestamp) and covered
  by new lifecycle vector L19 — 75 vectors, 205 tests.
- Evidence hash-binding statement aligned with the `invalid` amount marker.

## [0.4.11] — implements MMF-v0.3.11

- **`settle` requires `CLAIMED`**: settling an `AUTHORIZED` (never-dispatched)
  transaction is now rejected as `SETTLE:STATUS` — nothing was sent to the
  rail, so nothing can settle. L06 and lifecycle tests updated to claim
  first.
- **Split rejection precedence**: `SETTLE` and `VOID` now have separate
  precedence rules (VOID additionally checks `CANCELLATION`); claim ordering
  distinguishes `REVOKED` (embedded) → `EXPIRED` → `REVOKED_REGISTRY`
  (registry-reported) → `REGISTRY` (fault), matching evaluation order — a
  registry that cannot answer cannot establish revocation.
- **Evidence fidelity qualified**: out-of-range amounts serialize as
  `invalid`; the ledger's typed request retains the full distinction.
- Lifecycle state diagram now shows `CLAIMED → SETTLED` and
  `CLAIMED → VOIDED` as distinct labeled transitions.

## [0.4.10] — implements MMF-v0.3.10

- **Resolution failure is a result, never an exception**: preliminary chain
  resolution is non-authoritative — a malformed link, cycle or unavailable
  ancestor cannot terminate evaluation before the serialized ledger lookup;
  a decided `tx_id` stays retrievable under an invalid chain. Spec makes the
  identity-change vs resolution-failure distinction explicit.
- **Rejection precedence**: `REJECTED` evidence `<OP>:<CAUSE>` now has a
  fixed evaluation order (CLAIM: UNKNOWN_TX→STATUS→BINDING→REVOKED→EXPIRED→
  REGISTRY; SETTLE/VOID: UNKNOWN_TX→STATUS→CANCELLATION) — lifecycle
  rejections are as deterministic as authorization denials.
- **Exact-bytes evidence**: NFC normalization removed from canonical
  serialization — evidence preserves the exact submitted value, reconciled
  with §5.3's unnormalized request equality (payee identifiers stay
  byte-sensitive end to end).
- **Negative credential vectors** C02–C06: bad signature, tampered bounds
  hash, wrong issuer, wrong agent, wrong mandate — all MUST fail
  verification. 74 vectors, 204 tests.

## [0.4.9] — implements MMF-v0.3.9

- **Ledger-first ordering made structural**: the chain is re-resolved inside
  the serialization after the ledger lookup; a changed chain identity fails
  closed as `INVALID_CHAIN`, so a decided `tx_id` stays replayable no matter
  what happens to the delegation graph afterward.
- **Normative REJECTED causes**: rejected lifecycle transitions now record
  `check` as `<OP>:<CAUSE>` (`UNKNOWN_TX`, `STATUS`, `BINDING`, `REVOKED`,
  `EXPIRED`, `REGISTRY`, `CANCELLATION`), so conforming implementations
  produce identical records for identical rejections.
- **Cancellation binding**: the spec now requires a `void` cancellation
  reference to attest non-execution for the specific transaction, bound to
  the rail instruction/idempotency key, via a trusted channel, retained for
  audit.
- **Timestamp input**: integer epoch milliseconds is the normative input;
  float-seconds conversion is an API convenience.
- **New golden vector** `spec/vectors/credential.json` (C01): canonical
  bounds payload, signed payload and deterministic Ed25519 signature for a
  fixed seed — 69 vectors, 199 tests.

# Changelog

## [0.4.8] — implements MMF-v0.3.8

- **Length-prefixed canonicalization everywhere (breaking).** `bounds_hash` and the credential signature payload now use the same `len:value` form as evidence — a `|` inside `principal`, `agent`, `mandate_id` or a payee can no longer shift field boundaries and collide two different signed objects. Credentials issued by ≤0.4.7 do not verify under this version.
- **`mandate_id` immutability + mandate preservation specified** — bound for life to one signed mandate object; the gate preserves the mandate for evidence interpretation.
- **Timestamp edge cases specified** — finite only (NaN/±inf invalid), ms value fits int64, negatives serialize uniformly.
- **Integral-equality rule defined** for typed request comparison.

## [0.4.7] — implements MMF-v0.3.7

- **`claim` re-verifies expiry as well as revocation**, for every chain member, inside the claim's serialization (linearization point = the claim's commit).
- **Unresolved open reservations consume budget at any age** — an AUTHORIZED/CLAIMED transaction that outlives the rolling window keeps counting until settled or voided. Spend records now carry a `resolved` flag, set by `settle` (SQLite schema migrates automatically).
- **Optional signed-evidence conformance profile** — hash chain mandatory, signatures a defined opt-in profile.
- **Typed-value request equality** — `1` and `1.0` compare integral-equal; different JSON types never equal.
- 198 tests, 68 vectors (new L17/L18 cover the window-boundary semantics).

## [0.4.6] — implements MMF-v0.3.6

- **`claim` re-verifies revocation** — an AUTHORIZED transaction of a revoked mandate (local flag or registry, fail-closed) can no longer be dispatched: the kill switch now reaches the dispatch point, not just new evaluations. `settle`/`void` proceed post-revocation (they record facts, not grants); replays return the historical decision.
- **Delegation model stated** — principal-authorized delegation with agent-selected attenuation: the agent proposes attenuated bounds; the root principal signs every chain credential.
- **Request equality specified** — exact, unnormalized field-wise comparison (`49.99` ≠ `"49.99"` ≠ `4999`); malformed requests enter the ledger as durable replayable denials.
- 196 tests, 66 vectors.

## [0.4.5] — implements MMF-v0.3.5

Specification-closure release (no behavior change):

- **Three-level dispatch guarantee** — the gate guarantees at most one successful claim per `tx_id`; at-most-once rail submission additionally requires a trusted dispatcher that never resubmits an ambiguous instruction; exactly-once execution remains the rail's idempotency-key responsibility.
- **Normative evidence serialization table** — per-field type, permitted encoding, null representation (`0:`), and normalization (NFC strings, `true`/`false`, `invalid` amounts, 64-hex `prev_hash`).
- **Evidence obligations scoped to completed, durably committed evaluations** — completed decision ⇒ one record; aborted evaluation ⇒ no record and no authorization released; requests that never reach the gate are outside the audit scope.

## [0.4.4] — implements MMF-v0.3.4

- **Canonical evidence v3 (breaking).** Fields serialize as `<UTF-8 byte length>:<value>` joined by `|` — self-delimiting, so any field value (including `|`) is encodable and malformed instructions can always be recorded. Chains produced by ≤0.4.3 do not verify under this version.
- **Timestamps serialize as integer Unix milliseconds** (`floor(t×1000+0.5)`) — byte-identical across languages regardless of numeric type.
- **New hashed `REFERENCE` field** carries normative provenance; `detail` (denial prose) stays advisory and unhashed.
- **`void` on CLAIMED requires a `cancellation` reference** (replaces `confirmed_rail_cancelled`) — a non-empty identifier of the authoritative rail confirmation, hashed into the VOID record.
- **Checked arithmetic** — an accumulated total that would exceed 2⁵³−1 denies `BUDGET` instead of overflowing.
- 66 conformance vectors; 194 tests.

## [0.4.3] — implements MMF-v0.3.3

- **`void` on a CLAIMED transaction requires `confirmed_rail_cancelled=True`** — an uncertain in-flight outcome stays CLAIMED; releasing budget on an unconfirmed void could let spend escape the budget. AUTHORIZED → VOIDED is unchanged.
- **Normative ledger ordering** — the serialized `tx_id` lookup is specified as the first step inside serialization; `TX_CONFLICT` is evidence-only and MUST NOT modify the stored ledger entry (matches prior behavior).
- **Mediation-scoped claim guarantee** — "at most one successful claim per `tx_id` within a correctly mediated deployment"; rail-bypass is a deployment concern.
- **REJECTED records carry the attempted instruction** (submitted payee/amount/currency), specified.
- **Byte-level timestamp rule** — Unix epoch seconds, shortest round-trip decimal, specified; evidence chain scope = per evidence log/gate, genesis = 64 ASCII `0`s.
- **LangChain `guard(settle_on_success=True)`** — a tool exception now leaves the transaction CLAIMED (uncertain outcome) rather than auto-voiding; reconcile via `settle()` or `void(confirmed_rail_cancelled=True)`.
- 66 conformance vectors (L16: unconfirmed void of a claimed transaction rejected); 194 tests.

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.2] — implements MMF-v0.3.2

Correctness release addressing the second-round review on 0.4.1.
Conformance vectors: 65 (30 core + 17 delegation + 15 lifecycle + 3 evidence-hash).

### Security
- **The claim slot is permanently consumed.** `unclaim()` is removed: a `tx_id`
  can be claimed at most once, unconditionally. Recovery for a claimed transaction
  confirmed never submitted is `void()` (releases the reservation) plus a **new**
  `tx_id` — an unknown rail outcome stays CLAIMED. This upgrades "at most one
  claim while claimed" to "at most one dispatch per tx_id", with no reliance on
  correct operator judgement.
- **Rejected lifecycle transitions are evidence.** A failed `claim`/`settle`/`void`
  (binding mismatch, wrong state, unknown `tx_id`, wrong mandate) appends a
  `REJECTED` record — a binding-mismatched claim is exactly the substitution
  signal the audit exists to catch.
- **Velocity counts issued authorizations.** Voiding releases the budget
  reservation but not the velocity slot, so authorize-and-void cycling can no
  longer evade a rate limit. `spend()` now returns (issued, outstanding);
  released spend rows are marked, not deleted. SQLite stores gain a `released`
  column (auto-migrated).
- **Canonical evidence v2.** The record hash now covers `currency` and the
  lifecycle `status` the record establishes, so no security-relevant field is
  outside the hash. String fields are UTF-8 NFC; absent fields serialize as the
  empty string. Golden vectors include CLAIM and REJECTED records.
- **Budget-window attenuation.** A child mandate's `budget_window_days` must be
  ≥ the parent's when the parent has a `total_budget` — a shorter window cycles
  the allowance faster and is wider, not narrower.

### Specified (no code change)
- `tx_id` namespace: ledger keys are unique per gate/store, not per mandate;
  cross-mandate collision denies `TX_CONFLICT` (already the behavior); lifecycle
  ops verify `mandate_id` (already enforced).
- Issuer authority: every chain member's credential verifies against the root
  principal's key — a delegate agent cannot self-issue (already the behavior).
- `INVALID_CHAIN` denials are serialized on the leaf mandate, so their ledger
  lookup and evidence append are atomic like every other denial (already the
  behavior; now normative).

### Breaking
- `unclaim()` removed (use `void()` + a new `tx_id`).
- Evidence hashes change (canonical v2): chains written by 0.4.x will not verify
  under 0.4.2. Regenerate or migrate by re-hashing.
- Void no longer frees velocity slots: tests relying on authorize→void→authorize
  within a velocity window will now see VELOCITY denials.

## [0.4.1] — implements MMF-v0.3.1

Correctness release addressing review findings on the 0.4.0 security semantics.
Conformance vectors: 64 (30 core + 17 delegation + 15 lifecycle + 2 evidence-hash).

### Security
- **Transaction currency is mandatory.** An integer amount without a currency is not
  financially meaningful; `enforce()` now denies an omitted `currency` with `CURRENCY`
  instead of silently interpreting it in the mandate's currency.
- **Single-dispatch settlement.** `enforce()` alone could not stop two concurrent
  dispatcher workers from both observing AUTHORIZED and submitting to the rail. New
  `claim(mandate, tx_id, payee, amount, currency)` atomically verifies the instruction
  binds to the stored request and takes the one dispatch slot (AUTHORIZED → CLAIMED);
  `unclaim()` is the fail-safe operator-recovery reverse. `settle`/`void` accept
  AUTHORIZED and CLAIMED transactions. Lifecycle vectors L11–L15.
- **Atomic durable evidence.** `EvidenceLog(store=SQLiteStore)` writes the evidence
  record inside the same `BEGIN IMMEDIATE` transaction as the ledger and reservation
  update: a failed append rolls the decision back, so a decided `tx_id` can never be
  left without its mandatory evidence record.
- **Chain resolution is explicit.** An unresolvable, cyclic or malformed parent chain
  now denies with `INVALID_CHAIN` (distinct from `REVOKED`), evaluated during chain
  resolution before the ledger — including on replay, so a malformed chain cannot
  poison a `tx_id`.
- **Credentials verified chain-wide, unconditionally.** A credential-bound gate now
  requires a valid credential for *every* chain member; a delegate without its own
  credential denies with `CREDENTIAL`.
- **Canonical serialization is byte-level.** Evidence payloads reject `|` (and must
  be UTF-8) so no record can be serialized ambiguously.

### Changed
- AP2 adapter requires a real `tx_id` on the cart (no more random fallback, which
  silently broke idempotency).
- LangChain `guard()` fills the mandate's currency when the tool takes no currency
  argument, and accepts `settle_on_success=True` to complete the lifecycle after the
  tool runs (settle on success, void on exception).
- `money.exponent()` rejects non-string currency cleanly.

### Breaking
- `enforce()` requires `currency`; a `claim()` step sits between AUTHORIZED and
  settlement dispatch; evidence chains from 0.4.0 remain valid (the canonical form
  is unchanged).

## [0.4.0] — implements MMF-v0.3

### Security
- **Cross-process overspend fixed.** `SQLiteStore` was documented as safe to share across
  processes, but check-and-record was only serialized in-process: under contention, 8 processes
  sharing one budget of 10 authorizations allowed up to 13. `atomic()` now wraps
  check-and-record in `BEGIN IMMEDIATE`; the same race allows exactly 10.
- **No floating point in decisions.** Amounts are integers in minor units; floats, strings,
  booleans and values outside [1, 2^53-1] deny with `NON_POSITIVE`.
- **Retries cannot double-spend.** `tx_id` makes `enforce()` idempotent: a retry returns the
  stored decision (`replayed=True`) and reserves nothing; a reused `tx_id` with a different
  request denies with `TX_CONFLICT`.
- **Payee binding.** `Decision.binds()` plus `x402.bind_payment()` and
  `ap2.bind_payment_mandate()` refuse to release a settlement instruction whose beneficiary,
  amount or currency differs from the approval, or whose authorization is already settled or
  voided. x402/AP2 gating now takes payee and amount from the rail's instruction
  (`x402.enforce_requirements`, `ap2.enforce_cart`).

### Added
- Transaction lifecycle: `settle()` and `void()` (AUTHORIZED → SETTLED / VOIDED); `void`
  releases budget and velocity across the delegation chain; `LifecycleError`.
- `mandata.money`: `to_minor()` / `from_minor()` with ISO 4217 exponents and
  `register_currency()`; conversion rejects excess precision instead of rounding.
- Gate-signed evidence: `EvidenceLog(signing_key=...)` and `verify_chain(public_key=...)`.
- Evidence records carry `event` (`DECISION`, `REPLAY`, `SETTLE`, `VOID`) and `tx_id`.
- `StateStore.atomic()`, `release()`, transaction ledger (`get_tx` / `put_tx`, `TxRecord`).
- LangChain `guard()`: `amount_unit` ("major" default, exact conversion) and `tx_id_arg`.
- Conformance: `spec/vectors/lifecycle.json` (10), `spec/vectors/evidence.json` (canonical
  hash vectors), 3 new core vectors; `benchmarks/enforce_latency.py`.
- Spec [MMF-v0.3](spec/mmf-v0.3.md); v0.2 marked superseded. JSON Schema v0.3 with
  transaction object.

### Changed (breaking)
- `Bounds.max_amount` / `total_budget` and `enforce(amount=)` are integers in minor units.
  Use `to_minor("49.99", "GBP")`.
- Evidence hash payload now starts `event | tx_id | ...` and serializes amounts as integers;
  0.3.x chains will not verify under 0.4.0.
- Credential bounds hash serializes amounts as integers — re-issue credentials.
- `StateStore.record()` takes a `tx_id`; `spend()` returns integer totals.
- `x402.from_payment_requirements` keeps atomic units (no float conversion);
  `ap2.from_intent_mandate` converts decimal major units to minor units.

## [0.3.0] — implements MMF-v0.2

### Security
- **Delegation widening fixed.** A child could escape a limited parent by setting
  `velocity_limit=0` (unlimited) or `allowed_payees=[]` (any payee). Both are now rejected.
- **Delegation chain enforced at the gate.** Revoking or expiring a parent now stops every
  descendant; child spending is charged to every ancestor, so sibling delegates can no longer
  each spend the parent's full budget. Delegation from a revoked/expired parent fails.
- **Fail-closed registry.** An unreachable or malformed revocation registry now denies with
  `REVOKED_REGISTRY` and records evidence (previously raised without a record, or — for a
  response missing `revoked` — allowed).
- **Invalid amounts denied.** NaN, ±infinity and non-numeric amounts previously passed every
  check; they now deny with `NON_POSITIVE`.
- **Credentials cover all bounds.** The signed bounds hash previously omitted `total_budget`
  and `budget_window_days`, so the budget could be raised without breaking the signature.
- **Gate clock.** `at=0` is no longer silently replaced by wall-clock time; new `clock` param;
  `at` documented as test/replay-only (agent-supplied timestamps could backdate expiry and
  windows).

### Added
- `Decision.check` and `EvidenceRecord.check` — normative MMF check IDs; free text moves to
  advisory `detail` (not hashed). `EvidenceRecord.reason` kept as a compatibility alias.
- Credential-bound gate: `enforce(..., credential=, principal_key=)` verifies first (`CREDENTIAL`).
- `Bounds` validation — malformed bounds raise at construction.
- `verify_chain(anchored_head=)` and anchor-aware `verify_anchor()` — detect tail truncation and
  last-record edits up to the anchored point; appends after the anchor no longer fail.
- Canonical evidence serialization (`canonical_number`) for cross-language identical hashes.
- Conformance: 7 new core vectors (registry, fail-closed, ordering, denial-consumes-nothing)
  and a 17-vector delegation suite (`spec/vectors/delegation.json`).
- Spec [MMF-v0.2](spec/mmf-v0.2.md); v0.1 marked superseded.

### Changed (breaking)
- Evidence hash format (canonical, covers `check`) — chains written by 0.2.x will not verify
  under 0.3.0.
- Credential payload/bounds hash format — re-issue credentials.
- `EvidenceLog.append(..., reason=)` renamed to `check=` plus optional `detail=`.

## [0.2.x]

### Added
- Initial scaffold: mandate model, bounds enforcement, evidence chain

### Added (v0.2.0)
- Concurrency safety — `enforce()` holds the mandate lock across check+record (#12)
- `StateStore` protocol + `InMemoryStore` + `SQLiteStore` — persistent velocity/spend state (#7)
- `Bounds.total_budget` + `budget_window_days` — cumulative spend caps (#8)
