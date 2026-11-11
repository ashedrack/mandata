"""Delegation — agent -> sub-agent mandates that can only tighten, never loosen."""

from __future__ import annotations

from .models import Bounds, Mandate


def delegate(parent: Mandate, sub_agent: str, bounds: Bounds) -> Mandate:
    """Create a child mandate for a sub-agent. Child bounds must be a strict
    subset of the parent's — delegation may only weaken authority.

    Raises ValueError on any attempt to widen scope.
    """
    pb = parent.bounds
    violations = []
    if bounds.max_amount > pb.max_amount:
        violations.append(f"amount cap {bounds.max_amount} exceeds parent {pb.max_amount}")
    if pb.allowed_payees and not set(bounds.allowed_payees) <= set(pb.allowed_payees):
        violations.append("payees outside parent's allowlist")
    if bounds.expires_in_days > pb.expires_in_days:
        violations.append("expiry exceeds parent's window")
    if pb.velocity_limit and bounds.velocity_limit > pb.velocity_limit:
        violations.append("velocity exceeds parent's limit")
    if pb.total_budget is not None and (
        bounds.total_budget is None or bounds.total_budget > pb.total_budget
    ):
        violations.append("budget exceeds parent's")
    if bounds.currency != pb.currency:
        violations.append("currency differs from parent's")
    if violations:
        raise ValueError("delegation widens scope: " + "; ".join(violations))

    child = Mandate(principal=parent.principal, agent=sub_agent, bounds=bounds)
    child.parent_mandate_id = parent.mandate_id
    return child
