"""AP2 adapter — map Google AP2 mandate/intent structures onto Mandata."""

from __future__ import annotations

from ..models import Bounds, Mandate


def from_intent_mandate(intent: dict, principal: str, agent: str) -> Mandate:
    """Build a Mandate from an AP2 IntentMandate-style dict.

    Handles common shapes: ``mandate_details.bounds`` or flattened fields;
    ``payees``/``allowed_merchants`` map to the allowlist.
    """
    details = intent.get("mandate_details") or intent
    amount = details.get("max_amount") or details.get("amount_cap") or details.get("maxAmount") or 0
    currency = (details.get("currency") or "USD").upper()
    payees = (
        details.get("payees") or details.get("allowed_merchants") or details.get("merchants") or []
    )
    days = details.get("expires_in_days") or details.get("validity_days") or 30
    bounds = Bounds(
        max_amount=float(amount),
        currency=currency,
        allowed_payees=list(payees),
        expires_in_days=int(days),
        velocity_limit=int(details.get("velocity_limit") or 0),
        total_budget=details.get("total_budget"),
    )
    return Mandate(principal=principal, agent=agent, bounds=bounds)
