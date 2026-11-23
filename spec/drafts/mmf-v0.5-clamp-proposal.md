# MMF v0.5 DRAFT: optional clamp to cap

**Status:** DRAFT proposal for discussion in
[#33](https://github.com/ashedrack/mandata/issues/33). Not normative. Nothing
here changes MMF v0.3.24 until the shape below is agreed and merged. The
reference implementation does not implement it yet.

**Base text:** `spec/mmf-v0.3.24.md`. Section numbers below refer to that file.

This draft follows the direction agreed in #33 so far:

1. A declared `clamp` field decides the verdict. `clamp` unset means today's
   behaviour, so no existing vector changes meaning.
2. No new evidence event. The record carries extra fields instead.
3. Clamping is an expectation annotation on existing vectors, not a new
   vector class.

Where the thread left a choice open, this draft takes a position and marks it
**OPEN** so it can be argued with. The open choices are collected in section 11.

---

## 1. Terms

| Term | Meaning |
|------|---------|
| requested amount | The `amount` the caller submitted |
| effective cap | The per transaction cap that applies at decision time (section 3) |
| effective amount | The amount actually authorized. Equals the requested amount unless clamped |
| clamped | `true` when a valid requested amount exceeded the effective cap and was reduced to it |

## 2. The `clamp` bound (amends §2)

Add one optional field to the bounds of a mandate:

| Field | Type | Req | Semantics |
|-------|------|-----|-----------|
| `bounds.clamp` | boolean | MAY | Default `false`. When `true`, an over cap request is authorized at the effective cap instead of denied as `CAP_EXCEEDED` |

- `clamp` MUST be a boolean. Any other type is a validation error (§2.2).
- `clamp` lives inside `bounds` so it is covered by `bounds_hash` and signed by
  the root principal (§7). An agent cannot turn clamping on for itself.
- When `clamp` is `false` or absent it MUST be omitted from the canonical
  bounds serialization, so every existing `bounds_hash` and golden credential
  vector stays byte identical.
- **Leaf governs.** Only the `clamp` value of the mandate passed to `enforce`
  (the leaf) is read. Ancestors' `clamp` values are ignored. A parent cannot
  switch a child into or out of clamping.
- Clamping applies to one bound only: the per transaction cap
  (`max_amount`, check `CAP_EXCEEDED`). Currency, payee, velocity, budget,
  expiry and revocation are never clamped. They deny as before.

## 3. Effective cap (amends §5.1)

```
effective_cap(chain) := min(node.bounds.max_amount for node in chain)
```

evaluated at decision time over the resolved chain.

**Note on current MMF.** Under §6, every child `max_amount` is at most its
parent's, and bounds are immutable once issued. So today `effective_cap` always
equals the leaf's `max_amount`. The `min` over the chain is stated so the text
stays correct if a later version lets an owner tighten bounds after issue.
**OPEN:** "owner tightening propagates to already registered delegates", as
described in #33, has no counterpart in MMF v0.3.24. Either it is done by
re-issuing mandates (which MMF already models) or it needs a separate
`tighten` operation. That is a different discussion from clamp. This draft only
requires that the cap used be **recorded** (section 6), so both models replay
correctly.

## 4. Decision function (amends §5.1)

Check 5 changes from "deny if over cap" to the following. All other checks are
unchanged and keep their order.

```
check 5 (CAP_EXCEEDED):
    cap := effective_cap(chain)
    if amount is valid (§3.1) and amount <= cap:
        pass, amount unchanged
    else if amount is valid and amount > cap:
        if leaf.bounds.clamp is true:
            requested := amount
            amount    := cap          # effective amount
            clamped   := true         # continue to check 6
        else:
            decide(CAP_EXCEEDED)      # unchanged behaviour
    # NON_POSITIVE (check 4) has already denied any invalid amount

checks 6..9 (CURRENCY, PAYEE, VELOCITY, BUDGET) evaluate with the
effective amount, not the requested amount.
```

Consequences, each fixed by a vector:

- A clamp never hides a later failure. An over cap request with a wrong payee
  denies as `PAYEE`, not as an allowed clamp (K06, K07).
- The clamped amount is what BUDGET sees. A request that cannot fit the budget
  even after clamping denies as `BUDGET` (K13).
- A clamped authorization is **one** issued authorization for `VELOCITY`
  (K11). A caller that retries the unreserved tail under a new `tx_id` consumes
  another authorization. This is by design and is why the requester must be
  told about the clamp (section 5).

## 5. Decision object (amends §4)

Add four fields. They are present on every decision. When `clamped` is `false`
the other three are omitted from the serialized decision.

| Field | Semantics |
|-------|-----------|
| `clamped` | `true` if the effective amount is less than the requested amount |
| `requested_amount` | The amount the caller submitted. Only when `clamped` |
| `effective_amount` | The amount authorized and reserved. Only when `clamped` |
| `effective_cap` | The resolved cap at decision time. Only when `clamped` |

On a clamped decision `requested_amount`, `effective_amount` and
`effective_cap` are REQUIRED fields, not optional annotations.
`effective_cap` in particular is what makes a clamped record replayable
(section 7), and it is the recorded value any future owner tightening
proposal must anchor on (section 3).

The existing `amount` field on a decision (§4, "the exact amount it
evaluated") is defined as the **effective amount**. Payee binding (§5.5)
therefore binds the effective amount.

The requester MUST be able to see the clamp. An implementation that returns a
decision without these fields to a caller of a clamped authorization is
non conformant, because the caller would believe its full request is reserved.

### 5.1 The `check` value of a clamped decision

**RESOLVED, Shape 2.** The thread converged on keeping clamp out of the check
id namespace as a new pseudo denial, and the second implementation confirmed
Shape 2 over Shape 1.

**Shape 2 (agreed).** A clamped decision has `allowed = true`,
`check = CAP_EXCEEDED`, `clamped = true`. The check names the bound that would
have fired, so the evidence answers "which cap bound this" without a new id.

This requires one wording change in §5.1: "`WITHIN` is the success outcome
reached when all ten pass" becomes "`WITHIN` is the success outcome reached
when all ten pass unadjusted. A decision with `allowed = true` and
`clamped = true` carries the check id of the bound that adjusted the amount.
A check id on an allowed decision names the adjusting bound; it is not a
denial and MUST NOT be read as a failed check."

**Shape 1 (alternative).** A clamped decision has `allowed = true`,
`check = WITHIN`, `clamped = true`. Clamp is purely a decision modifier and
§5.1 needs no wording change. The cost is that `check` alone no longer says
which bound applied.

The vectors in `spec/drafts/vectors/` use Shape 2. Moving to Shape 1 is a
mechanical edit: replace `"check": "CAP_EXCEEDED"` with `"check": "WITHIN"` on
vectors where `"clamped": true`.

## 6. Evidence (amends §5.6)

No new event. The existing `DECISION` record is used.

- `amount` on a clamped `DECISION` record is the **effective amount**.
- A clamped `DECISION` record also carries `requested_amount` and
  `effective_cap`.
- A denial carries the **requested** amount, as today, and `clamped = false`.
  Nothing was authorized, so nothing was clamped.
- `REPLAY` records for a clamped decision carry the same fields as the stored
  decision.

**Canonical payload (OPEN).** To keep every existing record hash unchanged,
the extra fields are included in the canonical serialization **only when
`clamped` is true**, inserted as three length prefixed fields (`true`,
`requested_amount`, `effective_cap`) immediately before `prev_hash`. Records
with `clamped` false serialize exactly as in v0.3.24. This needs a new
evidence hash vector before it can be agreed. It is not included in this
draft because the hash must be produced by an implementation, not written by
hand.

## 7. Idempotency and replay (amends §5.3)

- **Ledger.** The ledger stores the **requested** request
  (`mandate_id`, `payee`, `amount`, `currency`) for equality, **and** the
  decision including `effective_amount` and `effective_cap`.
- **Request equality** is evaluated on the requested amount. Two requests for
  one `tx_id` are identical iff their requested amounts are in the same amount
  class, as today. Two different requested amounts that clamp to the same
  effective amount are **different** requests, and the second is `TX_CONFLICT`
  (K15).
- **Replay.** A replay returns the stored decision, including the stored
  `effective_cap`, even if the resolved cap would differ if evaluated now.
  Verifiers replaying at different times therefore agree, because the cap is a
  recorded value and not a function of when evaluation happens (K14).
- **Deferred case.** "Same requested amount, new `tx_id`, after a cap change"
  has no vector in this draft. It is inexpressible until an owner tightening
  operation exists (section 3), so the gap is deliberate rather than missed.
  It lands with the tighten proposal.

## 8. State and lifecycle (amends §5.2, §5.4, §5.5)

- Reservations, velocity and budget are charged the **effective amount** on
  every node of the chain (K12).
- `claim` binds to the effective amount. A `claim` carrying the requested
  amount fails as `CLAIM:BINDING` (K16). A `claim` carrying the effective
  amount succeeds.
- `settle` and `void` are unchanged.

## 9. Delegation (amends §6)

**RESOLVED, register time (strict).** This draft keeps registration
**strict**: a delegate whose `max_amount` is wider than its parent's MUST be
rejected, exactly as today, regardless of any `clamp` value (KD04). The second
implementation confirmed this position and is switching its register path to
reject a widening delegate cap, so D03 to D12 pass unchanged on both sides.
Reasons:

1. Registration is a configuration action, not a spend. Silently narrowing a
   declared delegation, with no event, is the kind of silent change the
   evidence log exists to catch.
2. Structural attenuation (§6) stays an invariant, and vectors D03 to D12 stay
   `PASS` unchanged.

**Consequence to be aware of.** An implementation that clamps a delegate's cap
at registration time, as described in #33, stays `MODEL` for D03 to D12 under
this draft. Only the **spend time** clamp becomes conformant. If register time
clamping should also be conformant, the alternative is a defined, recorded
registration event that carries the requested and resolved cap. That needs a
new event, which the thread preferred to avoid.

## 10. Conformance (amends §8)

- Clamping is an **optional feature**. An implementation that does not
  implement `clamp` reports the clamp vectors as `NOT_MAPPABLE`. They are not
  failures.
- An implementation that implements `clamp` MUST pass every clamp vector and
  every existing vector unchanged.
- Existing vectors are unchanged. Vectors with `clamp` absent or `false`
  behave exactly as in v0.3.24 (K02, K03).

## 11. Open choices for the thread

Resolved by the thread and the second implementation:

1. **Check value of a clamped decision:** Shape 2 (`CAP_EXCEEDED`).
   Section 5.1.
2. **Register time:** strict, no recorded event. Section 9.
5. **Owner tightening:** a separate proposal. Section 3. `effective_cap`
   stays REQUIRED on clamped decisions so that proposal has its anchor.
7. **Naming:** `CAP_EXCEEDED` confirmed for the per transaction cap.

Still open:

3. **Where `clamp` lives:** inside `bounds` and signed (this draft) or on the
   mandate outside `bounds`. Inside `bounds` changes what the principal signs
   but keeps clamping out of agent hands.
4. **Whose `clamp` governs:** the leaf only (this draft) or any chain member.
6. **Canonical payload change:** the inclusion rule in section 6.

## 12. Implementation notes (non normative)

Found while checking the vectors against the v0.3.24 gate through a wrapper
that clamps the amount and then calls the existing `enforce`. Two easy
mistakes, both covered by vectors:

1. **Do not store the effective amount as the request.** If the ledger keeps
   the clamped amount, a retry that submits the effective amount looks like a
   replay of a request the caller never made, instead of `TX_CONFLICT` (K15).
2. **Record the requested amount on a denial.** A clamp followed by a later
   denial (for example `BUDGET`) must record the amount the caller submitted,
   not the intermediate clamped value (K06, K13).

## 13. Not yet covered

- Evidence hash vectors for clamped records (section 6).
- The `claim` operation when the stored authorization was clamped and the
  claim arrives after the caller has retried the tail.
- JSON schema changes (`spec/mmf.schema.json`).
- Reference implementation and changelog.

These follow once the shape in section 11 converges.
