"""Mock payment rail — the full enforce -> claim -> settle gate in front of a "pay" call.

Nothing leaves until the gate says so, and the rail only ever sees the
instruction the gate stored — never the agent's own wording of the request:

    enforce()  decides the transaction and reserves the amount (AUTHORIZED)
    claim()    binds the dispatch instruction to the stored request (CLAIMED)
    rail.pay() is invoked only after a successful claim, on tx_id as the
               rail's idempotency key
    settle()   records that funds moved (SETTLED); void() releases the
               reservation when the rail confirms it did not execute (VOIDED)

Run:  python examples/mock_rail.py
"""

from __future__ import annotations

from mandata import Bounds, EvidenceLog, LifecycleError, Mandate, claim, enforce, settle, to_minor, void


class MockRail:
    """A payment rail stub: idempotent charges, idempotent decline lookups."""

    def __init__(self) -> None:
        self.charges: dict[str, dict] = {}

    def pay(self, idem_key: str, payee: str, amount: int, currency: str) -> str:
        """Charge `payee`. Retries on the same key return the same receipt —
        the key is what makes dispatch safe to retry."""
        if idem_key in self.charges:
            return self.charges[idem_key]["receipt"]
        receipt = f"rcpt-{len(self.charges) + 1:03d}"
        self.charges[idem_key] = {"payee": payee, "amount": amount, "currency": currency, "receipt": receipt}
        print(f"    rail: charged {payee} {amount} {currency} -> {receipt}")
        return receipt

    def decline_reference(self, idem_key: str) -> str:
        """Authoritative confirmation the instruction was declined and cannot
        later execute — what void() needs to release a CLAIMED reservation."""
        return f"decl-{idem_key}"


log = EvidenceLog()
rail = MockRail()

mandate = Mandate(
    principal="user:alice",
    agent="agent:shopbot",
    bounds=Bounds(
        max_amount=to_minor("50.00", "GBP"),
        currency="GBP",
        allowed_payees=["store.example"],
        expires_in_days=30,
        velocity_limit=5,
    ),
)
print(f"Mandate {mandate.mandate_id}: £50 cap, payees [store.example], 5 tx/24h\n")


def purchase(tx_id: str, payee: str, amount: int, instruction_payee: str | None = None) -> None:
    """The gated dispatch path — rail is reachable only through claim()."""
    d = enforce(mandate, payee, amount, currency="GBP", evidence_log=log, tx_id=tx_id)
    tag = "REPLAY " if d.replayed else ("ALLOWED" if d.allowed else "DENIED ")
    print(f"  {tag} {d.check:12} {d.reason}")
    if not d.allowed or d.replayed:
        return
    # Dispatch the instruction the GATE stored — the agent cannot reword it.
    tx = claim(mandate, tx_id, instruction_payee or payee, amount, "GBP", evidence_log=log)
    receipt = rail.pay(tx.tx_id, tx.payee, tx.amount, tx.currency)
    settle(mandate, tx_id, evidence_log=log)
    print(f"    settled with {receipt}")


print("1) normal purchase")
purchase("order-1", "store.example", to_minor("29.99", "GBP"))

print("\n2) over the cap — denied before the rail is ever touched")
purchase("order-2", "store.example", to_minor("60.00", "GBP"))

print("\n3) retry of the same tx_id — replayed decision, no second reservation")
purchase("order-1", "store.example", to_minor("29.99", "GBP"))

print("\n4) substitution attempt — authorized for store.example,")
print("   dispatched instruction names a different payee")
enforce(mandate, "store.example", to_minor("10.00", "GBP"), currency="GBP",
        evidence_log=log, tx_id="order-4")
try:
    claim(mandate, "order-4", "evil.example", to_minor("10.00", "GBP"), "GBP", evidence_log=log)
except LifecycleError as e:
    print(f"  claim refused: {e}")

print("\n5) rail declines mid-flight — void releases the reservation")
enforce(mandate, "store.example", to_minor("15.00", "GBP"), currency="GBP",
        evidence_log=log, tx_id="order-5")
tx = claim(mandate, "order-5", "store.example", to_minor("15.00", "GBP"), "GBP", evidence_log=log)
try:
    void(mandate, "order-5", evidence_log=log)  # no reference: stays CLAIMED
except LifecycleError as e:
    print(f"  void refused: {e}")
ref = rail.decline_reference("order-5")
void(mandate, "order-5", evidence_log=log, cancellation=ref)
print(f"    voided on rail decline reference {ref}")

print("\n6) double dispatch — the claim slot is consumed")
try:
    claim(mandate, "order-1", "store.example", to_minor("29.99", "GBP"), "GBP", evidence_log=log)
except LifecycleError as e:
    print(f"  claim refused: {e}")

print("\n--- rail ledger (what actually moved) ---")
for key, c in rail.charges.items():
    print(f"  {key:8} {c['payee']:14} {c['amount']:>6} {c['currency']}  {c['receipt']}")

print("\n--- evidence chain ---")
print("valid:", log.verify_chain())
for r in log.records:
    print(f"  {r.hash[:12]}… {r.event:8} {r.check:24} {r.status}")
