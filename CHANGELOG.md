# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
