# How Mandata maps to AP2 and x402

Three pieces of agentic-payment infrastructure, three different jobs. This
page maps Mandata's concepts onto Google's **Agent Payments Protocol (AP2)**
and Coinbase's **x402** — and says plainly where the models differ, because
the differences are the reason Mandata exists.

Primary sources: [AP2 specification](https://github.com/google-agentic-commerce/AP2) ·
[x402 specification](https://github.com/coinbase/x402) · [x402.org](https://x402.org)

## One-line framing

| | What it answers |
|---|---|
| **AP2** | Did the user approve *this purchase*, and can the merchant prove it? |
| **x402** | How does a payment get delivered and settled over HTTP? |
| **Mandata** | Is *this transaction* within what the agent may do, and can anyone verify it was checked before money moved? |

## Concept map

| Mandata | AP2 | x402 |
|---|---|---|
| `Mandate` — a principal's signed grant of bounded spending authority | Mandate — a cryptographically signed digital contract carrying user approval | No standing grant; each request carries a payment signature |
| `Bounds` — cap, payee allowlist, currency, expiry, velocity limit, cumulative budget window | Intent Mandate's conditions (natural-language intent + constraints, human-not-present flow) | `upto` scheme's `maxAmount` per request |
| `Credential` — Ed25519 signature binding (mandate, agent, bounds, expiry) | Verifiable credentials signed by the user's wallet / merchant keys | EIP-712 / EIP-3009 signatures authorizing one transfer |
| `enforce()` — the gate that checks every transaction before settlement | No equivalent runtime component; validation is left to implementations | The facilitator's `verify` step checks the payment signature is valid and sufficient |
| `tx_id` ledger — replay returns the stored decision, conflicts deny | — | Scheme-level nonces (e.g. EIP-3009 `authorizationNonce`) prevent on-chain replays |
| `claim()` / `settle()` / `void()` — dispatch binding and reservation lifecycle | — | Facilitator `settle`; no reservation or void concept |
| `EvidenceLog` — hash-chained, optionally signed, replayable decision records | The signed mandate chain is itself an audit trail | On-chain settlement is the record; facilitation logs are implementation-private |

## Where the models genuinely differ

**AP2's mandates are purchase contracts, Mandata's are standing authority.**
An AP2 Intent Mandate or Cart Mandate scopes *a purchase*: this cart, these
conditions, this user approval. A Mandata mandate is an envelope an agent
lives inside across *every* transaction it initiates — velocity limits and
budget windows that no single signed cart expresses. The check is cumulative
and stateful: request five must account for requests one through four, which
is why the gate keeps a reservation ledger rather than validating signatures
in isolation.

**x402 settles payments; it does not define an authorization boundary.**
The payment signature in x402 authorizes *the transfer itself* — that is its
job, and it does it well. Whether the agent that produced the signature was
permitted to make it is outside the protocol's scope. Mandata is designed to
sit in front of x402 the same way it sits in front of a card rail: the gate
runs at the proposer's boundary, before the signed payload is ever formed.
An x402 `exact` or `upto` payment under a Mandata mandate gains cap, payee,
velocity and budget checks plus a replayable evidence trail; an x402
facilitator never needs to know the mandate exists.

**The checkpoint belongs to a different party.** In x402 the facilitator
verifies at settlement; in AP2 the merchant's provider validates at purchase.
In Mandata the gate is enforced at a point *the proposing agent does not
control*, and the decision — allowed or refused — is hashed into evidence
either way. Denials are as auditable as approvals, which is what makes "the
agent was within bounds" a verifiable claim rather than a deployment promise.

**Delegation direction.** AP2's model re-invokes the user for approvals.
Mandata's `delegate()` lets an agent re-issue *narrower* authority to
sub-agents without touching the principal — bounds may only tighten down a
chain, and a child's spend counts against every ancestor's limits.

## Using them together

- **AP2 + Mandata:** an AP2 signed cart supplies the purchase intent; the
  Mandata gate decides whether executing it stays inside the agent's
  standing bounds and produces the decision evidence AP2 leaves to
  implementations.
- **x402 + Mandata:** the agent proposes an x402 payment; `enforce()` checks
  bounds and reserves the amount, `claim()` binds the instruction to the
  mandate's decision, the signed payload goes to the facilitator only after
  the claim succeeds, and `settle()`/`void()` reconcile against the
  facilitator's outcome. The rail stays a rail; the authorization layer
  stays answerable to the principal.
