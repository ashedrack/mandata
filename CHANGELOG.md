# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
