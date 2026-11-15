# Mandata

[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/ashedrack/mandata/badge)](https://securityscorecards.dev/viewer/?uri=github.com/ashedrack/mandata)

**The mandate toolkit for agent-initiated payments.**

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
- [ ] TypeScript implementation (core gate scaffold only — not yet MMF-v0.3.4 conformant)

## Research

This project is the open-source implementation arm of the survey:

> *Agentic AI in Payments and Financial Transactions: A Survey of Architectures, Safeguards and Open Problems* — S.C. Aji, 2026. [doi:10.13140/RG.2.2.13674.89288](https://doi.org/10.13140/RG.2.2.13674.89288)

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

