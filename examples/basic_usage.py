"""Minimal end-to-end example: issue a mandate, enforce a transaction, inspect evidence.

Amounts on the gate are integers in minor units (pence for GBP); ``to_minor``
converts decimal strings exactly and refuses anything it would have to round.
"""

from mandata import Bounds, EvidenceLog, Mandate, enforce, to_minor

log = EvidenceLog()

mandate = Mandate(
    principal="user:alice",
    agent="agent:shopping-bot",
    bounds=Bounds(
        max_amount=to_minor("500.00", "GBP"),
        currency="GBP",
        allowed_payees=["store.example"],
        expires_in_days=30,
        velocity_limit=3,
    ),
)

for i, (payee, amount) in enumerate(
    [("store.example", "49.99"), ("store.example", "600.00"), ("evil.example", "10.00")]
):
    d = enforce(mandate, payee, to_minor(amount, "GBP"), evidence_log=log, tx_id=f"order-{i}")
    status = "ALLOWED" if d.allowed else "REJECTED"
    print(f"{status:8} {payee:20} £{amount:>8}  [{d.check}] ({d.reason})")

print("\nEvidence chain valid:", log.verify_chain())
for r in log.records:
    print(f"  {r.hash[:12]}… <- {r.prev_hash[:12]}…  {r.check}")
