"""The mandate gate — deterministic enforcement evaluated BEFORE settlement."""

from __future__ import annotations

import time

from .evidence import EvidenceLog
from .models import Decision, Mandate

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
) -> Decision:
    """Evaluate a proposed transaction against its mandate.

    Returns a Decision with a linked evidence record. A rejection is also
    recorded — refusals are evidence too.
    """
    at = at or time.time()
    log = evidence_log or _DEFAULT_LOG
    b = mandate.bounds

    with mandate._lock:
        return _evaluate_and_record(mandate, payee, amount, currency, at, log, b, store, registry)


def _evaluate_and_record(mandate, payee, amount, currency, at, log, b, store, registry) -> Decision:
    if registry is not None and registry.is_revoked(mandate.mandate_id):
        reason = "mandate revoked (registry)"
    elif mandate.revoked:
        reason = "mandate revoked"
    elif mandate.is_expired(at):
        reason = "mandate expired"
    elif amount <= 0:
        reason = "non-positive amount"
    elif amount > b.max_amount:
        reason = f"amount {amount} exceeds cap {b.max_amount}"
    elif currency and currency != b.currency:
        reason = f"currency {currency} outside mandate ({b.currency})"
    elif b.allowed_payees and payee not in b.allowed_payees:
        reason = f"payee '{payee}' not in allowed set"
    elif b.velocity_limit and _spend(mandate, store, at, 1)[0] >= b.velocity_limit:
        reason = f"daily velocity limit {b.velocity_limit} reached"
    elif b.total_budget is not None:
        _, spent = _spend(mandate, store, at, b.budget_window_days)
        if spent + amount > b.total_budget:
            reason = f"cumulative budget {b.total_budget} would be exceeded"
        else:
            reason = "within mandate"
    else:
        reason = "within mandate"

    allowed = reason == "within mandate"
    if allowed:
        _record(mandate, store, at, amount)

    record = log.append(
        mandate_id=mandate.mandate_id,
        agent=mandate.agent,
        payee=payee,
        amount=amount,
        allowed=allowed,
        reason=reason,
        timestamp=at,
    )
    return Decision(allowed=allowed, reason=reason, evidence=record)


def _spend(mandate, store, at, window_days):
    """(count, total) in `window_days` — shared store if given, else mandate memory."""
    since = at - window_days * 86400
    if store is not None:
        return store.spend(mandate.mandate_id, since)
    return (
        mandate.spend_in_window(at)
        if window_days == mandate.bounds.budget_window_days
        else mandate._state.spend(mandate.mandate_id, since)
    )


def _record(mandate, store, at, amount):
    if store is not None:
        store.record(mandate.mandate_id, at, amount)
    else:
        mandate._state.record(mandate.mandate_id, at, amount)
