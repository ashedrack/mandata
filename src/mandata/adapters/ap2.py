"""AP2 adapter — map Google AP2 mandate/cart structures onto Mandata, with payee binding.

AP2 amounts are W3C Payment Request ``PaymentCurrencyAmount`` values:
decimal major-unit strings. They are converted to integer minor units with
``mandata.money.to_minor``, which refuses (never rounds) excess precision.

Payee binding (MMF §5.5): ``enforce_cart`` evaluates the merchant and total
taken from the merchant-signed CartMandate, and ``bind_payment_mandate``
refuses a PaymentMandate whose merchant or total differs from the approval.
The adapter trusts the cart's merchant identifier; verifying the merchant
authorization signature is the AP2 layer's responsibility.
"""

from __future__ import annotations

from ..enforce import enforce
from ..models import Bounds, Decision, Mandate
from ..money import to_minor
from .x402 import PayeeBindingError


def _minor(value, currency: str):
    """Decimal major units -> minor units; invalid input passes through so the
    gate denies it as NON_POSITIVE."""
    if value is None:
        return None
    try:
        return to_minor(value if isinstance(value, str) else str(value), currency)
    except (TypeError, ValueError):
        return value


def from_intent_mandate(intent: dict, principal: str, agent: str) -> Mandate:
    """Build a Mandate from an AP2 IntentMandate-style dict.

    Handles common shapes: ``mandate_details.bounds`` or flattened fields;
    ``payees``/``allowed_merchants`` map to the allowlist. Amounts are decimal
    major units (``"500.00"``).
    """
    details = intent.get("mandate_details") or intent
    currency = (details.get("currency") or "USD").upper()
    amount = details.get("max_amount") or details.get("amount_cap") or details.get("maxAmount")
    payees = (
        details.get("payees") or details.get("allowed_merchants") or details.get("merchants") or []
    )
    days = details.get("expires_in_days") or details.get("validity_days") or 30
    bounds = Bounds(
        max_amount=_minor(amount, currency),
        currency=currency,
        allowed_payees=list(payees),
        expires_in_days=int(days),
        velocity_limit=int(details.get("velocity_limit") or 0),
        total_budget=_minor(details.get("total_budget"), currency),
    )
    return Mandate(principal=principal, agent=agent, bounds=bounds)


def _cart_terms(cart: dict) -> tuple[str, object, str]:
    contents = cart.get("contents") or cart
    total = ((contents.get("payment_request") or {}).get("details") or {}).get("total") or {}
    amount = total.get("amount") or {}
    currency = str(amount.get("currency") or "USD").upper()
    payee = str(contents.get("merchant_name") or contents.get("merchant") or "")
    return payee, _minor(amount.get("value"), currency), currency


def enforce_cart(mandate: Mandate, cart: dict, tx_id: str | None = None, **kwargs) -> Decision:
    """Gate an AP2 CartMandate using the merchant and total it states.

    ``tx_id`` defaults to the cart id (``contents.id``), the natural
    idempotency key for an AP2 checkout.
    """
    payee, amount, currency = _cart_terms(cart)
    contents = cart.get("contents") or cart
    return enforce(
        mandate,
        payee=payee,
        amount=amount,
        currency=currency,
        tx_id=tx_id or contents.get("id"),
        **kwargs,
    )


def bind_payment_mandate(decision: Decision, payment_mandate: dict) -> None:
    """Raise PayeeBindingError unless the PaymentMandate pays exactly what the
    gate approved (``payment_details_total`` and ``merchant_agent``)."""
    contents = payment_mandate.get("payment_mandate_contents") or payment_mandate
    total = (contents.get("payment_details_total") or {}).get("amount") or {}
    currency = str(total.get("currency") or "USD").upper()
    payee = str(contents.get("merchant_agent") or contents.get("merchant_name") or "")
    amount = _minor(total.get("value"), currency)
    if not decision.binds(payee, amount, currency):
        raise PayeeBindingError(
            f"payment mandate to {payee!r} for {amount!r} {currency} does not bind to "
            f"approved decision {decision.tx_id} ({decision.payee!r}, {decision.amount!r})"
        )
