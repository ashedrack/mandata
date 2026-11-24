# Mandata (TypeScript)

The mandate toolkit for agent-initiated payments — TypeScript port of the
Python reference gate (mandata 0.4.x, MMF-v0.3). `Mandate` + `Bounds` define
the authority, `enforce()` is the gate, `EvidenceLog` is the tamper-evident
trail, `claim()`/`settle()`/`void()` cover the settlement lifecycle.

## Status

**Conformance-verified port.** `npm test` runs every vector in
`spec/vectors/` (core, delegation, lifecycle, evidence, credential) against
this implementation — all 92 pass, including byte-identical canonical
payloads and evidence hashes and deterministic Ed25519 signatures that match
the Python gate.

Ported: the `enforce()` decision gate (all checks, idempotent `txId`
replay/TX_CONFLICT, delegation chains, credentials, revocation registry),
the settlement lifecycle (`claim`/`settle`/`void` with REJECTED evidence),
canonical serialization (`lp_fields`, `canonical_number`), the hash-chained
evidence log with optional Ed25519 signing, `InMemoryStore` and
`SQLiteStore` (stdlib `node:sqlite`, WAL + `BEGIN IMMEDIATE` sections —
persists spend rows, the tx ledger and evidence rows atomically),
`InMemoryRegistry`, `HTTPRegistry` (prefetching — the gate's `isRevoked` is
synchronous, so `refresh(mandateIds)` pulls statuses before evaluation and
an unrefreshed id fails closed), external anchoring (`anchor` /
`verifyAnchor`), and `railIdempotencyKey`.

Not yet ported: revocation reporting. Contributions welcome —
write against `spec/` and the vectors, not by copying internals.

## Layout

    src/canonical.ts    lp_field / canonical_number primitives (MMF §5.6)
    src/models.ts       Bounds, Mandate, Decision, EvidenceRecord
    src/enforce.ts      enforce(), claim(), settle(), void() — the gate
    src/state.ts        TxRecord, InMemoryStore, lifecycle statuses
    src/sqlite.ts       SQLiteStore — durable counters, ledger, evidence (node:sqlite)
    src/evidence.ts     EvidenceLog — hash-chained records, Ed25519 signing
    src/anchoring.ts    anchor / verifyAnchor — external timestamp proofs
    src/credentials.ts  bounds_hash, issue/verify credentials
    src/delegation.ts   delegate() — tightening-only sub-mandates
    src/money.ts        minor-unit amounts, ISO 4217 exponents
    src/revocation.ts   RevocationRegistry, InMemoryRegistry, HTTPRegistry
    src/railkey.ts      gate-scoped rail idempotency keys (MMF §5.4)
    test/vectors.test.ts  conformance runner for spec/vectors/*.json

## Develop

    npm install
    npm test          # tsc + all spec vectors

Note on wire semantics: `amount` must be a JSON integer. JS numbers cannot
distinguish `1` from `1.0` — the vector runner preserves the Python gate's
int/float distinction via the raw `amount_json` text; adapters should do the
same at the wire edge.
