# Mandata

[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/ashedrack/mandata/badge)](https://securityscorecards.dev/viewer/?uri=github.com/ashedrack/mandata)

**The mandate toolkit for agent-initiated payments.**

*Built and maintained by [Shedrack Chiedozie Aji](https://orcid.org/0009-0007-0823-2112) at [Doztra Ltd](https://doztra.ai). Mandata specifies and implements the Mandate Format (MMF), an open format for bounded spending authority in agentic payment systems.*

When AI agents hold spending authority, someone must answer: *what exactly are they allowed to do — and can you prove it afterward?* Mandata is a reference implementation of the bounded-autonomy mandate chain:

**principal → agent → credential → transaction → evidence**

## The problem

Agentic payment protocols (AP2, x402, card-network agent programs) let AI agents initiate real financial transactions. But production systems lack a standard way to:

- **Express mandates** — bounded permissions: amounts, payees, time windows, velocity limits
- **Bind credentials** — cryptographic proof a transaction was made within mandate
- **Enforce at the gate** — deterministic checks *before* settlement, not logs after
- **Produce evidence** — an auditable chain showing every agent action against its mandate

Mandata formalizes all four as a small, dependency-light library.

## Quickstart

```bash
pip install mandata
```

```python
from mandata import Mandate, Bounds, enforce, claim, settle, to_minor

# A principal grants a bounded mandate to an agent (amounts in minor units)
mandate = Mandate(
    principal="user:alice",
    agent="agent:shopping-bot",
    bounds=Bounds(
        max_amount=to_minor("500.00", "GBP"),  # 50000 pence per transaction
        currency="GBP",
        allowed_payees=["store.example"],
        expires_in_days=30,
        velocity_limit=3,  # max authorizations per day
    ),
)

# At transaction time: deterministic enforcement BEFORE settlement.
# tx_id makes retries idempotent and links the authorization to settlement.
decision = enforce(mandate, payee="store.example", amount=4999, currency="GBP", tx_id="order-1")
assert decision.allowed  # → True (status AUTHORIZED, amount reserved)
print(decision.check)  # → "WITHIN" (normative MMF check ID)

# Claim takes the single dispatch slot and verifies the rail instruction binds
# to the approval — only then submit, and only once.
claim(mandate, "order-1", "store.example", 4999, "GBP")
...  # rail.pay(..., idempotency_key="order-1")
settle(mandate, "order-1")  # or void(mandate, "order-1") if it failed
```

## Architecture

```
principal issues mandate ──► credential binds mandate to agent
        │                           │
        ▼                           ▼
  agent proposes transaction ──► mandate gate (enforce())
        │                           │
        ▼                           ▼
   allowed? ──► yes: execute    no: reject
        │                           │
        └───────► evidence record appended (hash-chained audit log)
```

See [`docs/architecture.md`](docs/architecture.md) for the full model, [`docs/mandate-model.md`](docs/mandate-model.md) for the schema, and [`docs/diagrams.md`](docs/diagrams.md) for the system design diagrams.

## Roadmap

- [x] **v0.1** — Mandate model, bounds enforcement, evidence chain (Python)
- [x] Credential signing (Ed25519) + verification API
- [x] Revocation registry (mandate kill-switch at transaction speed)
- [x] AP2 / x402 / LangChain adapter layer
- [x] Multi-hop delegation (agent → sub-agent mandate weakening)
- [x] **v0.3** — [MMF-v0.2](spec/mmf-v0.2.md): credential-bound gate, chain-wide delegation enforcement, fail-closed registry, canonical evidence hashing, 43 conformance vectors
- [x] **v0.4** — [MMF-v0.3](spec/mmf-v0.3.md): integer minor-unit amounts, `tx_id` idempotency, authorize/settle/void lifecycle, payee binding in adapters, gate-signed evidence, cross-process serializable SQLite store, 58 conformance vectors
- [x] **v0.4.1** — [MMF-v0.3.1](spec/mmf-v0.3.1.md): mandatory transaction currency, `INVALID_CHAIN` for unresolvable/cyclic chains, chain-wide credential verification, `claim()`/`unclaim()` single-dispatch lifecycle (AUTHORIZED→CLAIMED→SETTLED), atomic durable evidence in the state transaction, byte-level canonical serialization, 64 conformance vectors
- [x] **v0.4.2** — [MMF-v0.3.2](spec/mmf-v0.3.2.md): permanently-consumed claim slot (no re-claim; recovery = void + new tx_id), `REJECTED` evidence for failed lifecycle transitions, velocity counts issued authorizations (void refunds budget only), budget-window attenuation, canonical evidence v2 (currency + status hashed, NFC), 65 conformance vectors
- [x] **v0.4.3** — [MMF-v0.3.3](spec/mmf-v0.3.3.md): `void` of a CLAIMED transaction requires `confirmed_rail_cancelled` (uncertain in-flight stays CLAIMED), serialized ledger-first ordering + `TX_CONFLICT` evidence-only rule, mediation-scoped claim guarantee, REJECTED records carry the attempted instruction, byte-level timestamp rule, per-gate evidence chain scope — 66 vectors
- [x] **v0.4.4** — [MMF-v0.3.4](spec/mmf-v0.3.4.md): canonical evidence v3 — length-prefixed fields (any value encodable), integer-millisecond timestamps, hashed `REFERENCE` provenance; `void` of a CLAIMED transaction requires a `cancellation` reference; checked arithmetic on accumulated totals
- [x] **v0.4.5** — [MMF-v0.3.5](spec/mmf-v0.3.5.md): three-level dispatch guarantee formalized (claim / trusted dispatcher / rail), normative evidence serialization table (types, nulls, NFC, encodings), evidence obligations scoped to completed durably-committed evaluations
- [x] **v0.4.6** — [MMF-v0.3.6](spec/mmf-v0.3.6.md): revocation re-verified at `claim` (a revoked mandate's unclaimed authorization cannot be dispatched); per-state revocation policy; principal-authorized delegation model stated; exact unnormalized request equality
- [x] **v0.4.7** — [MMF-v0.3.7](spec/mmf-v0.3.7.md): `claim` re-verifies revocation AND expiry chain-wide (linearization = claim commit); unresolved open reservations consume budget at any age (L17/L18); optional signed-evidence conformance profile; typed-value request equality — 68 vectors
- [x] **v0.4.24** — [MMF-v0.3.24](spec/mmf-v0.3.24.md): non-scalar strings rejected before evaluation (chain-poisoning fix, vector V36); registry timeout is a deployment parameter — 92 vectors
- [x] **v0.4.23** — [MMF-v0.3.23](spec/mmf-v0.3.23.md): claim authority linearized at the observation, not the commit (overstatement withdrawn; gap pinned by a test); key-retention rule; raw-log guidance — no behavior change
- [x] **v0.4.22** — [MMF-v0.3.22](spec/mmf-v0.3.22.md): dispatcher policy made unambiguous (logical vs physical submission; no resubmission after an uncertain outcome) — no behavior change
- [x] **v0.4.21** — [MMF-v0.3.21](spec/mmf-v0.3.21.md): registry wait is per consultation (overall deadline SHOULD, not yet implemented); replacement-transaction rule for uncertain claims — no behavior change
- [x] **v0.4.20** — [MMF-v0.3.20](spec/mmf-v0.3.20.md): overflow-rule scope; bounded registry wait (timeout = registry fault, claim stays AUTHORIZED); tamper evidence ≠ transaction truth — no behavior change
- [x] **v0.4.19** — [MMF-v0.3.19](spec/mmf-v0.3.19.md): invalid-amount trade-off stated; rail-key default profile and `gate_id` requirements — documentation only
- [x] **v0.4.18** — [MMF-v0.3.18](spec/mmf-v0.3.18.md): corrected request-equality/ledger text to the actual behavior (V31–V35, L29); gate-scoped `rail_idempotency_key`; optional `verify_cancellation` — 91 vectors
- [x] **v0.4.17** — [MMF-v0.3.17](spec/mmf-v0.3.17.md): cross-mandate TX_CONFLICT evidence; rail idempotency-key rule; normative window arithmetic (inclusive lower bound) — vectors L27, L28, D18 — 85 vectors
- [x] **v0.4.16** — [MMF-v0.3.16](spec/mmf-v0.3.16.md): cross-mandate evidence-leak fix; rejection causes/precedence now conformance-tested (L21–L26) — 82 vectors; attenuation proof scoped to one hypothetical history
- [x] **v0.4.15** — [MMF-v0.3.15](spec/mmf-v0.3.15.md): structural vs effective attenuation formalized (+ randomized test); settle wording; atomic-commit rule; evidence forensic limit — no behavior change
- [x] **v0.4.14** — [MMF-v0.3.14](spec/mmf-v0.3.14.md): replay-first ordering structural (resolution only for new `tx_id`); idempotent lifecycle step precedes STATUS; vector L20 — 76 vectors
- [x] **v0.4.13** — [MMF-v0.3.13](spec/mmf-v0.3.13.md): final semantic clarifications — binding scope, repeated-lifecycle-call evidence rule, check terminology; no behavior change
- [x] **v0.4.12** — [MMF-v0.3.12](spec/mmf-v0.3.12.md): claim-time revocation guarantee scoped to visible state; settle-after-window aging rule + vector L19 — 75 vectors
- [x] **v0.4.11** — [MMF-v0.3.11](spec/mmf-v0.3.11.md): `settle` requires CLAIMED (AUTHORIZED can only claim or void); per-operation REJECTED precedence with `REVOKED_REGISTRY`; evidence-fidelity qualification
- [x] **v0.4.10** — [MMF-v0.3.10](spec/mmf-v0.3.10.md): resolution-failure-as-result ordering; fixed REJECTED cause precedence; exact-bytes evidence (NFC removed); negative credential vectors C02–C06 — 74 vectors
- [x] **v0.4.9** — [MMF-v0.3.9](spec/mmf-v0.3.9.md): ledger-lookup-first ordering made structural (chain re-resolved under serialization); normative `<OP>:<CAUSE>` on REJECTED evidence; cancellation-reference binding rules; credential golden vector C01 — 69 vectors
- [x] **v0.4.8** — [MMF-v0.3.8](spec/mmf-v0.3.8.md): unified length-prefixed canonicalization for credentials + bounds_hash (delimiter-smuggling closed); `mandate_id` immutable-for-life + durable mandate preservation; timestamp edge cases; typed-equality integral rule
- [ ] TypeScript implementation — `ts/` is a 59-line pre-v0.3 scaffold, **not** an MMF implementation (never run against the vectors)

## Research

This project is the open-source implementation arm of two papers by the author:

> *Mandata and the Mandate Format (MMF): Bounded Spending Authority for Agent-Initiated Payments* — S.C. Aji, 2026. [doi:10.13140/RG.2.2.29395.34083](https://doi.org/10.13140/RG.2.2.29395.34083)

> *Agentic AI in Payments and Financial Transactions: A Survey of Architectures, Safeguards and Open Problems* — S.C. Aji & V. Palade, 2026. [doi:10.20944/preprints202611.2539.v1](https://doi.org/10.20944/preprints202611.2539.v1)

## Contributing

We welcome contributors — see [CONTRIBUTING.md](CONTRIBUTING.md). Good entry points are labelled [`good first issue`](https://github.com/ashedrack/mandata/labels/good%20first%20issue).

## License

Apache-2.0 — see [LICENSE](LICENSE).

## Demo app

`examples/demo_app.py` runs a full simulated deployment over real HTTP — a
revocation-registry service, a mock payment rail, and an agent whose every
proposed payment goes through the gate: signed credentials, sub-agent
delegation, x402 intake, compromise attempts, remote revocation, then a full
evidence-chain audit. `python examples/demo_app.py` — stdlib only.

For a shorter single-process walkthrough see `examples/end_to_end.py`.

