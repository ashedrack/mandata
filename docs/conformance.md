# Running the conformance corpus

The vector corpus is the contract. An implementation conforms to MMF-v0.3.24
when it produces the expected verdict, check id, lifecycle state, evidence
record, and signature bytes for every vector it can express. This document
explains how the corpus is laid out, what the fields mean, and how to report
results.

The corpus lives in `spec/vectors/` and is pinned to the release tag you are
testing against. If you vendor it, record the tag and the commit hash so your
results are reproducible. The canonical pin for v0.4.24 is commit `9aba772`.

## Layout

| File | Vectors | What it covers |
|------|---------|----------------|
| `core.json` | 36 | Spend decisions: bounds checks, check ordering, registry failure, velocity, budgets, malformed inputs |
| `delegation.json` | 18 | Multi-hop delegation: attenuation, non-widening, chain evaluation |
| `lifecycle.json` | 29 | The AUTHORIZED, CLAIMED, SETTLED, VOIDED state machine: idempotency, replay, claim slots, void rules |
| `evidence.json` | 3 | Canonical evidence serialization and hash chaining |
| `credential.json` | 6 | Ed25519 credential generation and verification, including negative cases |

92 vectors in total, all deterministic. No network, no database, no keys
beyond the pinned test seeds in `credential.json`.

## Vector shape

A spend vector gives a mandate and a transaction, then says what the gate
must return:

```json
{
  "id": "V01",
  "desc": "within bounds",
  "mandate": { "bounds": { "max_amount": 50000, "currency": "GBP",
                           "allowed_payees": ["tesco.com"],
                           "expires_in_days": 30, "velocity_limit": 0 } },
  "tx": { "amount": 4999, "at": 1000, "currency": "GBP",
          "payee": "tesco.com" },
  "expect": { "allowed": true, "check": "WITHIN" }
}
```

The fields you must match:

- `allowed` — the verdict on the request as given.
- `check` — the normative check id from section 5.1 of the spec. Check ids
  are broad buckets, not one per deny reason. A denied decision reports the
  first failing check in the ordered pipeline, and an allowed decision
  reports `WITHIN`.
- `replayed` — lifecycle vectors only. True when the verdict is served from
  a stored decision rather than a fresh evaluation.
- `status` — lifecycle vectors only. The transaction state after the step:
  AUTHORIZED, CLAIMED, SETTLED, VOIDED.
- `events` — where given, the evidence events the step must emit. The event
  vocabulary (DECISION, REPLAY, REJECTED, CLAIM, SETTLE, VOID) is part of
  conformance, not observational.
- `expect.rejected` — some vectors assert that malformed input is rejected
  before evaluation. This binds every implementation that admits the input
  into its evaluation path; having no separate validation stage is not an
  exemption.

Lifecycle vectors (`L*`) and delegation vectors (`D*`) contain a `steps`
array instead of a single `tx`. Each step has its own `expect`, and steps
may use `op` fields (`claim`, `settle`, `void`) rather than `tx` to drive
lifecycle transitions. Registry behaviour is injected through a `registry`
field on the vector, for example `"registry": {"unavailable": true}`.

Evidence vectors (`E*`) pin exact canonical bytes and chain head hashes.
Credential vectors (`C*`) pin Ed25519 seeds, public keys, and expected
verification outcomes, including negative cases that must each fail for
their stated reason.

## Classifying your results

Report results per vector class, not as a single number. The classification
below is the one used on the first independent run of this corpus and is
recommended so results stay comparable:

- **PASS** — your implementation produced the expected verdict, check, and
  where applicable the status, events, and canonical bytes.
- **MODEL** — your implementation enforces the same security property
  through a different model. Example: collapsing `AUTHORIZED, CLAIMED,
  SETTLED` into `reserved, settled|released` makes the claim operation and
  its check ids unreachable but still enforces the reservation. MODEL is
  an honest divergence, not a defect.
- **BUG** — a real defect the vector caught in your code. These belong in
  your own issue tracker.
- **NOT_MAPPABLE** — your implementation has no concept for what the vector
  exercises at all. A missing feature, not a wrong one.
- **GATE_EX** — the evaluation itself raised rather than returning a
  verdict. Often still a closed deny at the transport layer, but it means
  no denied entry was recorded, and the denied entry is itself part of the
  verdict.

Keep the four metrics (spend, delegation, lifecycle, crypto/evidence)
separate. Summing them hides where the divergence actually lives.

## When you disagree with a vector

Read [spec/FAQ.md](../spec/FAQ.md) first. It records rulings from prior
runs: check id granularity, denied-entry requirements, verdict memory,
velocity semantics, the claim handshake, and rejected-input binding.

If the FAQ does not settle it, open an issue on the repository with the
vector id and your reasoning. Questions about spec intent get normative
answers and, where the clarification is general, get folded back into the
FAQ so the next implementation does not have to ask.

A worked example of a full independent run, including the classification
table and the spec questions it raised, is at
[issue #32](https://github.com/ashedrack/mandata/issues/32).

## Reporting

When you publish or share results, include: the corpus tag and commit you
ran against, the classification table, which divergences you classify as
MODEL versus BUG on your side, and a link to your suite so results are
reproducible. The corpus is a shared reference point; the more verifiable
runs exist against it, the stronger the spec gets.
