# API reference

The full public surface, grouped by the job each part does — in the order a
real integration touches it. Everything is importable from `mandata` unless a
module path is noted.

```python
import mandata
mandata.__version__   # "0.4.24"
```

## 1. Setup: slips and money values

### `Bounds` — the permitted envelope

Every limit lives here. Malformed bounds are rejected at construction: a
mandate that cannot be evaluated is never created.

```python
from mandata import Bounds, to_minor

bounds = Bounds(
    max_amount=to_minor("500.00", "GBP"),    # cap per transaction, minor units
    currency="GBP",
    allowed_payees=["store.example"],        # empty = any payee
    expires_in_days=30,
    velocity_limit=3,                        # tx/day; 0 = unlimited
    total_budget=to_minor("2000.00", "GBP"), # cumulative cap, minor units
    budget_window_days=30,
)
```

### `Mandate` — the grant

Binds a principal to an agent under bounds.

```python
from mandata import Mandate

mandate = Mandate(principal="user:alice", agent="agent:shop-bot", bounds=bounds)
mandate.mandate_id     # unique id (uuid hex)
mandate.expires_at     # timestamp it dies (issued_at + expires_in_days)
mandate.is_expired()   # bool
mandate.revoke()       # kill-switch; takes effect at the next enforce()
mandate.chain()        # this mandate plus resolved ancestors
```

### `to_minor` / `from_minor` / `register_currency` — integer money

Amounts are always integers in the currency's minor unit — floats lose
pennies and are rejected by the gate.

```python
from mandata import to_minor, from_minor, register_currency

to_minor("12.99", "GBP")     # 1299
from_minor(1299, "GBP")      # "12.99"
register_currency("KWD", 3)  # teach the gate a non-standard exponent
```

## 2. The gate: the transaction lifecycle

### `enforce(mandate, payee, amount, currency=None, *, at=None, evidence_log=None, store=None, registry=None, hooks=(), credential=None, principal_key=None, tx_id=None) -> Decision`

The checkpoint. Evaluates the proposed transaction against the mandate (and
its delegation chain) in spec order and returns a `Decision`. Call it before
money moves. With `tx_id`, retries are idempotent: a repeated request replays
the stored decision rather than re-evaluating.

```python
d = enforce(mandate, "store.example", 4999, "GBP",
            tx_id="order-1", evidence_log=log)

d.allowed    # bool
d.check      # "WITHIN" or the denial check id ("CAP_EXCEEDED", "PAYEE",
             # "EXPIRED", "REVOKED", "VELOCITY", "BUDGET", "NON_POSITIVE",
             # "REVOKED_REGISTRY", "REGISTRY", ...)
d.status     # "AUTHORIZED" or "DENIED"
d.replayed   # True if tx_id was previously decided — no new reservation
```

### `claim(mandate, tx_id, payee, amount, currency=None, *, evidence_log=None, store=None, at=None)`

Takes the single dispatch slot for an AUTHORIZED transaction — exactly one
worker may submit it to the rail — and binds the rail instruction to the
approval (payee/amount/currency must match or it rejects `CLAIM:BINDING`).

```python
claim(mandate, "order-1", "store.example", 4999, "GBP")
```

### `settle(mandate, tx_id, *, evidence_log=None, store=None, at=None)`

Marks a CLAIMED transaction SETTLED: funds moved, spend counted. Idempotent.

```python
settle(mandate, "order-1")
```

### `void(mandate, tx_id, *, cancellation=None, verify_cancellation=None, evidence_log=None, store=None, at=None)`

Releases an unresolved reservation — the budget comes back, the velocity
slot does not (authorizations issued still count, so authorize/void cycling
cannot evade the rate limit). Voiding a CLAIMED transaction requires a
cancellation reference.

```python
void(mandate, "order-1", cancellation="rail-cancel-ref-42")
```

Illegal transitions (settle a denied tx, claim a claimed tx, settle an
AUTHORIZED one, ...) raise `LifecycleError`.

## 3. Delegation

### `delegate(parent, sub_agent, bounds, *, at=None) -> Mandate`

Issues a child mandate whose bounds must be a non-widening subset of the
parent's in every dimension — wider cap, different currency, extra payees,
higher velocity, higher budget, longer life are all rejected at creation.
Enforcement on the child evaluates the whole chain; revoking any ancestor
stops every descendant.

```python
from mandata import delegate

child = delegate(mandate, "agent:errand-bot", Bounds(
    max_amount=to_minor("100.00", "GBP"), currency="GBP",
    allowed_payees=["store.example"], expires_in_days=7,
))
```

## 4. Revocation registries

### `InMemoryRegistry()` / `HTTPRegistry(url)` / `RevocationRegistry` (protocol)

A shared blacklist consulted at transaction speed. `HTTPRegistry` fails
closed: an unreachable registry denies rather than guesses.

```python
from mandata import InMemoryRegistry, HTTPRegistry

reg = HTTPRegistry("https://revocations.internal")
reg.revoke(mandate.mandate_id)
enforce(mandate, "store.example", 4999, "GBP", registry=reg)
```

## 5. Evidence

### `EvidenceLog(signing_key=None, store=None)` — the hash-chained receipt book

Passed to `enforce`/`claim`/`settle`/`void`, every decision appends a
`EvidenceRecord` sealed to its predecessor — including denials.

```python
from mandata import EvidenceLog

log = EvidenceLog(signing_key=key)   # optional Ed25519 per-record signature
log.head_hash                        # fingerprint of the chain tip
log.verify_chain()                   # False → tampered history
```

### `anchor(log, service_url)` / `verify_anchor(log, anchor_obj)` — external notarization

Stamps the chain head with an external timestamping service so history
cannot be backdated even by the operator. Import from `mandata.anchoring`.

```python
from mandata.anchoring import anchor, verify_anchor
a = anchor(log, "https://notary.example.com")
assert verify_anchor(log, a)
```

## 6. Credentials

### `issue_credential(mandate, signing_key)` / `verify_credential(credential, mandate, expected_public_key)` / `bounds_hash(mandate)`

A portable Ed25519 proof that this agent holds these bounds under this
mandate — verifiable offline against the principal's public key.
Import from `mandata.credentials`.

```python
from mandata.credentials import issue_credential, verify_credential

cred = issue_credential(mandate, principal_signing_key)
ok, reason = verify_credential(cred, mandate, expected_public_key=pub)
# fails: wrong key, wrong mandate, wrong agent, altered bounds, expired
```

## 7. Plumbing

| Symbol | Purpose |
|--------|---------|
| `InMemoryStore()` | Transaction state for single-process use (the default) |
| `SQLiteStore(path)` | Durable transaction state for production |
| `StateStore` | Protocol for custom stores |
| `rail_idempotency_key(gate_id, tx_id)` | Idempotency key to hand the rail so a retried submit cannot double-charge |
| `Decision` | `enforce` result: `.allowed`, `.check`, `.status`, `.replayed`, `.binds(payee, amount, currency)` |
| `EvidenceRecord` | A receipt: `.canonical_payload()`, `.hash()`, `.reason` |
| `TxRecord` | A transaction entry: tx_id plus lifecycle status |
| `CHECKS` / `EVENTS` | The normative check ids and event types |
| `canonical_number(x)` | Spec-exact number formatting used in hashing |
| `LifecycleError` | Raised for illegal lifecycle transitions |

## 8. End to end

```python
from mandata import (Bounds, EvidenceLog, Mandate, claim, enforce,
                     rail_idempotency_key, settle, to_minor)

log = EvidenceLog()
mandate = Mandate(
    principal="alice", agent="shop-bot",
    bounds=Bounds(max_amount=to_minor("500.00", "GBP"), currency="GBP",
                  allowed_payees=["store.example"]))

d = enforce(mandate, "store.example", 4999, "GBP", tx_id="o1", evidence_log=log)
if d.allowed:
    claim(mandate, "o1", "store.example", 4999, "GBP", evidence_log=log)
    rail.pay(4999, idempotency_key=rail_idempotency_key("gate-1", "o1"))
    settle(mandate, "o1", evidence_log=log)

assert log.verify_chain()   # every step above is provable
```

Deeper material: [mandate-model.md](mandate-model.md) for the schema,
[architecture.md](architecture.md) for module roles,
[conformance.md](conformance.md) for the vector corpus, and
[examples/](../examples/) for runnable flows.
