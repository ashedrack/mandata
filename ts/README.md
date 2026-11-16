# Mandata (TypeScript)

The mandate toolkit for agent-initiated payments — TypeScript port of the core
gate + evidence chain. Mirrors the Python API: `Mandate` + `Bounds` define the
authority, `enforce()` is the gate, `EvidenceLog` is the tamper-evident trail.

## Status

**Scaffold only — not an MMF implementation.** This is a 59-line sketch that
predates MMF-v0.3: no `tx_id`/idempotency or lifecycle, no integer-amount
validation, no budget or window accounting, no credentials, delegation or
registry, and a non-canonical evidence hash (SHA-256 over a `"genesis"`
string and `JSON.stringify` output). It has never been run against
`spec/vectors/`. An independent implementation would be written from
`spec/` and the vectors, not extended from this file.
