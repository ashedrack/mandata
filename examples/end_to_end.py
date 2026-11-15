"""Full loop: agent proposes -> gate decides -> rail executes only allowed txs -> audit.

Run: python examples/end_to_end.py
"""

from typing import ClassVar

from mandata import (
    Bounds,
    EvidenceLog,
    InMemoryRegistry,
    Mandate,
    enforce,
    from_minor,
    settle,
    to_minor,
)


class MockRail:
    """Stand-in for AP2/x402/bank settlement — executes only what the gate allows."""

    executed: ClassVar[list] = []

    @classmethod
    def settle(cls, payee, amount):
        cls.executed.append((payee, amount))
        return f"settled £{from_minor(amount, 'GBP')} -> {payee}"


class ShoppingAgent:
    """A simple agent that proposes transactions — benign and compromised."""

    def __init__(self, mandate, log, registry):
        self.mandate, self.log, self.registry = mandate, log, registry
        self.n = 0

    def attempt(self, payee, major):
        self.n += 1
        amount, tx_id = to_minor(major, "GBP"), f"order-{self.n}"
        d = enforce(
            self.mandate,
            payee,
            amount,
            evidence_log=self.log,
            registry=self.registry,
            tx_id=tx_id,
        )
        if d.binds(payee, amount):
            receipt = MockRail.settle(payee, amount)
            settle(self.mandate, tx_id, evidence_log=self.log)
            return receipt
        return f"BLOCKED [{d.check}] ({d.reason})"


if __name__ == "__main__":
    log, registry = EvidenceLog(), InMemoryRegistry()
    mandate = Mandate(
        principal="user:alice",
        agent="agent:shopbot",
        bounds=Bounds(
            max_amount=to_minor("500", "GBP"),
            currency="GBP",
            allowed_payees=["tesco.com", "skyscanner.com"],
            expires_in_days=30,
            velocity_limit=3,
            total_budget=to_minor("900", "GBP"),
            budget_window_days=30,
        ),
    )
    bot = ShoppingAgent(mandate, log, registry)

    print("== Normal operation ==")
    for payee, amount in [("tesco.com", "49.99"), ("skyscanner.com", "420.00")]:
        print(f"  {bot.attempt(payee, amount)}")

    print("\n== Compromised agent ==")
    for payee, amount in [("evil-payee.io", "100.00"), ("tesco.com", "600.00")]:
        print(f"  {bot.attempt(payee, amount)}")

    print("\n== Principal revokes remotely ==")
    registry.revoke(mandate.mandate_id)
    print(f"  {bot.attempt('tesco.com', '10.00')}")

    print("\n== Audit ==")
    print(f"  Evidence chain valid: {log.verify_chain()}")
    for r in log.records:
        tag = "ALLOW" if r.allowed else "DENY "
        print(f"  {r.event:<8} {tag} {r.payee:20} {from_minor(r.amount, 'GBP'):>8}  {r.check}")

    print(f"\nRail executed {len(MockRail.executed)} settlements:")
    for p, a in MockRail.executed:
        print(f"  £{from_minor(a, 'GBP'):>8} -> {p}")
