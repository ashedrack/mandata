"""Full loop: agent proposes -> gate decides -> rail executes only allowed txs -> audit.

Run: python examples/end_to_end.py
"""

from typing import ClassVar

from mandata import Bounds, EvidenceLog, InMemoryRegistry, Mandate, enforce


class MockRail:
    """Stand-in for AP2/x402/bank settlement — executes only what the gate allows."""

    executed: ClassVar[list] = []

    @classmethod
    def settle(cls, payee, amount):
        cls.executed.append((payee, amount))
        return f"settled {amount:.2f} -> {payee}"


class ShoppingAgent:
    """A simple agent that proposes transactions — benign and compromised."""

    def __init__(self, mandate, log, registry):
        self.mandate, self.log, self.registry = mandate, log, registry

    def attempt(self, payee, amount):
        d = enforce(self.mandate, payee, amount, evidence_log=self.log, registry=self.registry)
        if d.allowed:
            return MockRail.settle(payee, amount)
        return f"BLOCKED ({d.reason})"


if __name__ == "__main__":
    log, registry = EvidenceLog(), InMemoryRegistry()
    mandate = Mandate(
        principal="user:alice",
        agent="agent:shopbot",
        bounds=Bounds(
            max_amount=500.0,
            currency="GBP",
            allowed_payees=["tesco.com", "skyscanner.com"],
            expires_in_days=30,
            velocity_limit=3,
            total_budget=900.0,
            budget_window_days=30,
        ),
    )
    bot = ShoppingAgent(mandate, log, registry)

    print("== Normal operation ==")
    for payee, amount in [("tesco.com", 49.99), ("skyscanner.com", 420.0)]:
        print(f"  {bot.attempt(payee, amount)}")

    print("\n== Compromised agent ==")
    for payee, amount in [("evil-payee.io", 100.0), ("tesco.com", 600.0)]:
        print(f"  {bot.attempt(payee, amount)}")

    print("\n== Principal revokes remotely ==")
    registry.revoke(mandate.mandate_id)
    print(f"  {bot.attempt('tesco.com', 10.0)}")

    print("\n== Audit ==")
    print(f"  Evidence chain valid: {log.verify_chain()}")
    for r in log.records:
        tag = "ALLOW" if r.allowed else "DENY "
        print(f"  {tag} {r.payee:20} {r.amount:8.2f}  {r.reason}")

    print(f"\nRail executed {len(MockRail.executed)} settlements:")
    for p, a in MockRail.executed:
        print(f"  {a:8.2f} -> {p}")
