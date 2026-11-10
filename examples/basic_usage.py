"""Minimal end-to-end example: issue a mandate, enforce a transaction, inspect evidence."""

from mandata import Bounds, EvidenceLog, Mandate, enforce

log = EvidenceLog()

mandate = Mandate(
    principal="user:alice",
    agent="agent:shopping-bot",
    bounds=Bounds(
        max_amount=500.00,
        currency="GBP",
        allowed_payees=["store.example"],
        expires_in_days=30,
        velocity_limit=3,
    ),
)

for payee, amount in [("store.example", 49.99), ("store.example", 600.0), ("evil.example", 10.0)]:
    d = enforce(mandate, payee, amount, evidence_log=log)
    status = "ALLOWED" if d.allowed else "REJECTED"
    print(f"{status:8} {payee:20} £{amount:8.2f}  ({d.reason})")

print("\nEvidence chain valid:", log.verify_chain())
for r in log.records:
    print(f"  {r.hash[:12]}… <- {r.prev_hash[:12]}…  {r.reason}")
