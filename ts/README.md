# Mandata (TypeScript)

The mandate toolkit for agent-initiated payments — TypeScript port of the core
gate + evidence chain. Mirrors the Python API: `Mandate` + `Bounds` define the
authority, `enforce()` is the gate, `EvidenceLog` is the tamper-evident trail.

## Status

Core port — gate, bounds, evidence chain. Credentials/delegation/adapters to
follow (see python implementation for reference semantics).
