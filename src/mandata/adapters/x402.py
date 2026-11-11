"""x402 adapter — map x402 payment requirements onto Mandata bounds."""

from __future__ import annotations

from ..models import Bounds, Mandate


def from_payment_requirements(
    req: dict, principal: str, agent: str, expires_in_days: int = 1
) -> Mandate:
    """Build a Mandate from an x402 PaymentRequirements-style dict.

    Expected keys: ``maxAmountRequired`` (string/int, atomic or decimal units),
    ``asset``/``currency``, ``payTo``. Unmappable fields are ignored.
    """
    amount = req.get("maxAmountRequired") or req.get("amount") or 0
    payee = req.get("payTo") or req.get("payee") or ""
    currency = (req.get("asset") or req.get("currency") or "USD").upper()
    bounds = Bounds(
        max_amount=float(amount),
        currency=currency,
        allowed_payees=[payee] if payee else [],
        expires_in_days=expires_in_days,
    )
    return Mandate(principal=principal, agent=agent, bounds=bounds)
