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
) -> Decision:
    """Evaluate a proposed transaction against its mandate.

    Returns a Decision with a linked evidence record. A rejection is also
    recorded — refusals are evidence too.
    """
    at = at or time.time()
    log = evidence_log or _DEFAULT_LOG
    b = mandate.bounds

    if mandate.revoked:
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
    elif b.velocity_limit and mandate.velocity_used_today(at) >= b.velocity_limit:
        reason = f"daily velocity limit {b.velocity_limit} reached"
    else:
        reason = "within mandate"

    allowed = reason == "within mandate"
    if allowed:
        mandate.record(at)

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
