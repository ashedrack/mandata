# MMF — Conformance FAQ and rulings

Clarifications of spec intent raised by independent conformance runs. These
entries interpret the existing text; they change no normative requirement.
Filed from [ashedrack/mandata#32](https://github.com/ashedrack/mandata/issues/32).

## Check-ids are broad buckets, not 1:1 deny reasons

The check identifiers in §5.1 (`REVOKED_REGISTRY`, `NON_POSITIVE`, `VELOCITY`,
...) are conformance buckets: a denied decision reports the *first* failing
check in the ordered pipeline, not a per-reason diagnostic. One check-id can
cover several distinct deny conditions — for example `NON_POSITIVE` applies
both to `amount <= 0` and to `amount > 2^53-1`. Implementations may log finer
internal reasons; the wire-level `check` value is always the bucket.

## A denied entry is part of the verdict

Failing closed at the transport layer is not a substitute for the deny
verdict. Where the spec says a deny "MUST be treated as ... and recorded"
(e.g. §5.1 on an unconsultable registry → `REVOKED_REGISTRY`), the denied
decision is itself the conformance object: it must carry the normative
`check` and produce its evidence record like any completed decision (§5.6).
An exception or a 500 that leaves no denied entry fails the vector even
though no spend was allowed.

## Registry unavailability is REVOKED_REGISTRY by intent

§5.1 check 1 covers "any mandate in the chain revoked, **or the registry
cannot be consulted**" — conservative deny is the required verdict, not a
generic closed failure. Registry scope also wins the check-id when several
revocation reasons apply: the pipeline evaluates in declared order and
`REVOKED_REGISTRY` sits first.

## `expect.rejected` binds every implementation that admits the input

A `rejected` vector is not conditional on the implementation having a
separate validation stage. If an implementation's parser produces a value
from malformed input and its gate then *allows* a spend on that value, the
outcome is a non-conformance — the absent validation stage is the defect the
vector exists to catch.

## Verdict memory is a MUST

§5.3 requires that a repeated request return `REPLAY` of the stored
decision, with a `REPLAY` evidence record. An enforcer that re-reserves on
every repeat `tx_id` has no replay protection — dedup-by-`tx_id` is
normative, not a modelling choice.

## Velocity counts issued authorizations

§5.1 check 8 counts authorizations **issued** in the trailing window.
Counting only live `reserved|settled` entries is evadable via
authorize → void → authorize and does not conform.

## The evidence event vocabulary is contract

`DECISION`, `REPLAY`, `REJECTED`, `CLAIM`, `SETTLE`, `VOID` (§5.6) are part
of conformance for any implementation with an evidence layer — the spec
says MUST append an evidence record per transition, and two conforming
implementations must produce identical records.

## Clamp-to-cap is a different transaction

Rejecting an over-cap request vs clamping it to the cap are different
verdicts: the clamped spend is a new transaction with a different amount
and evidence trail. A clamp cannot PASS a vector that expects `invalid` —
it is an alternative enforcement semantics tracked as a v0.5 candidate in
[ashedrack/mandata#33](https://github.com/ashedrack/mandata/issues/33).
