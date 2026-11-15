"""The mandate gate — deterministic enforcement evaluated BEFORE settlement."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from contextlib import ExitStack

from .evidence import EvidenceLog
from .models import Decision, Mandate, _is_finite_number

_DEFAULT_LOG = EvidenceLog()


def enforce(
    mandate: Mandate,
    payee: str,
    amount: float,
    currency: str | None = None,
    at: float | None = None,
    evidence_log: EvidenceLog | None = None,
    store=None,
    registry=None,
    hooks=(),
    credential=None,
    principal_key: str | None = None,
    clock: Callable[[], float] = time.time,
) -> Decision:
    """Evaluate a proposed transaction against its mandate (MMF §5).

    Returns a Decision carrying the normative ``check`` ID and a linked
    evidence record. Denials are recorded too — refusals are evidence.

    Time: the gate's own ``clock`` is authoritative. ``at`` exists for
    conformance vectors and replay; production callers MUST NOT pass a
    timestamp supplied by the agent, or it could backdate around expiry and
    velocity/budget windows.

    Credential binding: pass ``credential`` and ``principal_key`` to make the
    gate verify the principal's signature first (check ``CREDENTIAL``).

    Delegation: a mandate produced by ``delegate()`` is evaluated together
    with all of its ancestors; allowed spend is charged to every mandate in
    the chain.

    Optional ``hooks`` are callables invoked with the final Decision.
    """
    at = clock() if at is None else at
    if not _is_finite_number(at):
        raise ValueError(f"evaluation time must be a finite number, got {at!r}")
    log = evidence_log or _DEFAULT_LOG
    chain = mandate.chain()

    with ExitStack() as locks:
        for node in reversed(chain):  # root-first: consistent order, no deadlock
            locks.enter_context(node._lock)
        check, detail = _evaluate(
            mandate, chain, payee, amount, currency, at, store, registry, credential, principal_key
        )
        allowed = check == "WITHIN"
        if allowed:
            for node in chain:
                (store or node._state).record(node.mandate_id, at, amount)
        record = log.append(
            mandate_id=mandate.mandate_id,
            agent=mandate.agent,
            payee=payee,
            amount=amount,
            allowed=allowed,
            check=check,
            timestamp=at,
            detail=detail,
        )

    decision = Decision(allowed=allowed, reason=detail, evidence=record, check=check)
    for hook in hooks:
        hook(decision)
    return decision


def _label(node: Mandate, mandate: Mandate) -> str:
    return "mandate" if node is mandate else f"ancestor mandate {node.mandate_id}"


def _spend(node: Mandate, store, at: float, window_days: int) -> tuple[int, float]:
    return (store or node._state).spend(node.mandate_id, at - window_days * 86400)


def _evaluate(mandate, chain, payee, amount, currency, at, store, registry, credential, key):
    """Return (check_id, advisory_detail). First failure wins (MMF §5)."""
    if credential is not None or key is not None:
        if credential is None or key is None:
            return "CREDENTIAL", "credential and principal_key must both be supplied"
        from .credentials import verify_credential

        ok, why = verify_credential(credential, mandate, key, at=at)
        if not ok:
            return "CREDENTIAL", why

    if registry is not None:
        for node in chain:
            try:
                revoked = registry.is_revoked(node.mandate_id)
            except Exception as exc:  # noqa: BLE001 — fail closed: any registry fault denies
                return (
                    "REVOKED_REGISTRY",
                    f"revocation registry unavailable ({exc.__class__.__name__})",
                )
            if revoked:
                return "REVOKED_REGISTRY", f"{_label(node, mandate)} revoked (registry)"

    for node in chain:
        if node.revoked:
            return "REVOKED", f"{_label(node, mandate)} revoked"
        if node.parent_mandate_id and node._parent is None:
            return "REVOKED", f"parent mandate {node.parent_mandate_id} unresolvable"

    for node in chain:
        if node.is_expired(at):
            return "EXPIRED", f"{_label(node, mandate)} expired"

    if not _is_finite_number(amount) or amount <= 0 or math.isnan(amount):
        return "NON_POSITIVE", f"amount {amount!r} is not a finite positive number"

    for node in chain:
        if amount > node.bounds.max_amount:
            return "CAP_EXCEEDED", f"amount {amount} exceeds cap {node.bounds.max_amount}"

    if currency:
        for node in chain:
            if currency != node.bounds.currency:
                return "CURRENCY", f"currency {currency} outside mandate ({node.bounds.currency})"

    for node in chain:
        if node.bounds.allowed_payees and payee not in node.bounds.allowed_payees:
            return "PAYEE", f"payee '{payee}' not in allowed set"

    for node in chain:
        limit = node.bounds.velocity_limit
        if limit and _spend(node, store, at, 1)[0] >= limit:
            return "VELOCITY", f"{_label(node, mandate)} velocity limit {limit} reached"

    for node in chain:
        budget = node.bounds.total_budget
        if budget is not None:
            _, spent = _spend(node, store, at, node.bounds.budget_window_days)
            if spent + amount > budget:
                return "BUDGET", f"{_label(node, mandate)} budget {budget} would be exceeded"

    return "WITHIN", "within mandate"
