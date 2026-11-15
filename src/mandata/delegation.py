"""Delegation — agent -> sub-agent mandates that can only tighten, never loosen."""

from __future__ import annotations

import time

from .models import Bounds, Mandate


def delegate(parent: Mandate, sub_agent: str, bounds: Bounds, at: float | None = None) -> Mandate:
    """Create a child mandate for a sub-agent.

    Child bounds must be a subset of the parent's: no bound may be wider
    (equal is permitted). "Unlimited" sentinels count as the widest possible
    value, so a child cannot escape a parent limit by setting
    ``velocity_limit=0`` or ``allowed_payees=[]``. The child's effective
    expiry is the earlier of its own and every ancestor's, because the gate
    checks expiry across the whole chain.

    The child keeps a reference to its parent; the gate evaluates the whole
    chain, so parent revocation or expiry stops the child and the child's
    spending counts against every ancestor's velocity and budget.

    Raises ValueError on any attempt to widen scope.
    """
    now = time.time() if at is None else at
    if parent.revoked:
        raise ValueError("cannot delegate from a revoked mandate")
    if parent.is_expired(now):
        raise ValueError("cannot delegate from an expired mandate")

    pb = parent.bounds
    child = Mandate(principal=parent.principal, agent=sub_agent, bounds=bounds, issued_at=now)

    violations = []
    if bounds.max_amount > pb.max_amount:
        violations.append(f"amount cap {bounds.max_amount} exceeds parent {pb.max_amount}")
    if bounds.currency != pb.currency:
        violations.append("currency differs from parent's")
    if pb.allowed_payees and (
        not bounds.allowed_payees or not set(bounds.allowed_payees) <= set(pb.allowed_payees)
    ):
        violations.append("payees outside parent's allowlist (empty = any payee)")
    if bounds.expires_in_days > pb.expires_in_days:
        violations.append("expiry exceeds parent's window")
    if pb.velocity_limit and (
        bounds.velocity_limit == 0 or bounds.velocity_limit > pb.velocity_limit
    ):
        violations.append("velocity exceeds parent's limit (0 = unlimited)")
    if pb.total_budget is not None and (
        bounds.total_budget is None or bounds.total_budget > pb.total_budget
    ):
        violations.append("budget exceeds parent's (null = unlimited)")
    if pb.total_budget is not None and bounds.budget_window_days < pb.budget_window_days:
        # A shorter window cycles the same allowance faster (1000/1d is 30k/30d),
        # which is wider, not narrower. Longer-or-equal windows are required so
        # the child's own bound never exceeds the parent's rate. Chain-wide
        # charging makes the effective authority the intersection regardless.
        violations.append(
            f"budget window {bounds.budget_window_days}d is shorter than parent's "
            f"{pb.budget_window_days}d (cycles the allowance faster)"
        )
    if violations:
        raise ValueError("delegation widens scope: " + "; ".join(violations))

    child.parent_mandate_id = parent.mandate_id
    child._parent = parent
    return child
