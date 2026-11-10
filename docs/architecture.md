# Mandata Architecture

Mandata implements the bounded-autonomy mandate chain identified in the parent
survey as missing from production agentic-payment systems:

```
principal → agent → credential → transaction → evidence
```

## Components

| Module | Role |
|--------|------|
| `models.Mandate` | The grant: who (principal) empowers whom (agent) under what `Bounds` |
| `models.Bounds` | The envelope: amount cap, currency, payee allowlist, expiry, daily velocity |
| `enforce` | The gate: deterministic allow/reject evaluated *before* settlement |
| `evidence.EvidenceLog` | Append-only, hash-chained record of every decision — including rejections |

## Design rules

1. **Deterministic gate.** The enforcement check contains no probabilistic
   decision-making; agent uncertainty is handled *above* the gate (intent
   formation), never inside it. Mirroring the survey's core result: the closer
   to settlement, the less agency a transaction can safely carry.
2. **Rejections are evidence.** A refused transaction is recorded in the same
   chain as an allowed one — an audit log that only stores successes is not an
   audit log.
3. **No secrets in the log.** Evidence records carry decisions and hashes, never
   credentials or payment details.
4. **Revocation first.** `mandate.revoke()` takes effect at the next `enforce()`
   call — revocation must be faster than the transaction.

## Roadmap alignment

- Ed25519 credential signing (bind mandate → agent cryptographically)
- Revocation registry with transaction-speed lookup
- AP2 / x402 adapters mapping protocol mandates onto `Bounds`
