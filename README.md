# Mandata

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
from mandata import Mandate, Bounds, enforce

# A principal grants a bounded mandate to an agent
mandate = Mandate(
    principal="user:alice",
    agent="agent:shopping-bot",
    bounds=Bounds(
        max_amount=500.00,          # per transaction
        currency="GBP",
        allowed_payees=["store.example"],
        expires_in_days=30,
        velocity_limit=3,           # max transactions per day
    ),
)

# At transaction time: deterministic enforcement BEFORE settlement
decision = enforce(mandate, payee="store.example", amount=49.99)
assert decision.allowed          # → True
print(decision.evidence)         # → signed evidence record of the check
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
- [ ] Revocation registry (mandate kill-switch at transaction speed)
- [ ] TypeScript implementation
- [ ] AP2 / x402 adapter layer
- [ ] Multi-hop delegation (agent → sub-agent mandate weakening)

## Research

This project is the open-source implementation arm of the survey:

> *Agentic AI in Payments and Financial Transactions: A Survey of Architectures, Safeguards and Open Problems* — S.C. Aji, 2026. [doi:10.13140/RG.2.2.13674.89288](https://doi.org/10.13140/RG.2.2.13674.89288)

## Contributing

We welcome contributors — see [CONTRIBUTING.md](CONTRIBUTING.md). Good entry points are labelled [`good first issue`](https://github.com/ashedrack/mandata/labels/good%20first%20issue).

## License

Apache-2.0 — see [LICENSE](LICENSE).
