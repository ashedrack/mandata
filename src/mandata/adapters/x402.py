"""x402 adapter — map x402 payment requirements onto Mandata, with payee binding.

x402 amounts (``maxAmountRequired``, ``authorization.value``) are already
integers in the asset's atomic unit, so they pass to the gate unchanged.

Payee binding (MMF §5.5): the gate evaluates the payee and amount taken from
the server's ``PaymentRequirements`` (``payTo``, ``maxAmountRequired``) —
never from agent-supplied text — and ``bind_payment`` refuses to release a
signed payment payload whose ``to``/``value`` differ from what was approved.
"""

from __future__ import annotations

from ..enforce import enforce
from ..models import Bounds, Decision, Mandate


class PayeeBindingError(PermissionError):
    """The settlement instruction does not match the gate's approved decision."""


def _asset(req: dict) -> str:
    return str(req.get("asset") or req.get("currency") or "USD").upper()


def _atomic(value) -> int | object:
    """Parse an x402 atomic-unit amount; invalid input is returned as-is so the
    gate denies it as NON_POSITIVE rather than the adapter guessing."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def from_payment_requirements(
    req: dict, principal: str, agent: str, expires_in_days: int = 1
) -> Mandate:
    """Build a Mandate from an x402 PaymentRequirements-style dict.

    Expected keys: ``maxAmountRequired`` (atomic units, integer string),
    ``asset``/``currency``, ``payTo``.
    """
    amount = _atomic(req.get("maxAmountRequired") or req.get("amount"))
    payee = req.get("payTo") or req.get("payee") or ""
    bounds = Bounds(
        max_amount=amount,
        currency=_asset(req),
        allowed_payees=[payee] if payee else [],
        expires_in_days=expires_in_days,
    )
    return Mandate(principal=principal, agent=agent, bounds=bounds)


def enforce_requirements(mandate: Mandate, req: dict, tx_id: str, **kwargs) -> Decision:
    """Gate an x402 payment using the payee/amount/asset the *server* requires."""
    return enforce(
        mandate,
        payee=str(req.get("payTo") or ""),
        amount=_atomic(req.get("maxAmountRequired")),
        currency=_asset(req),
        tx_id=tx_id,
        **kwargs,
    )


def bind_payment(decision: Decision, payment_payload: dict) -> None:
    """Raise PayeeBindingError unless the signed payment pays exactly what the
    gate approved. Accepts an x402 payment payload (``payload.authorization``)
    or a bare authorization dict with ``to``/``value``."""
    auth = (payment_payload.get("payload") or {}).get("authorization") or payment_payload
    to, value = auth.get("to"), _atomic(auth.get("value"))
    if not decision.binds(str(to), value):
        raise PayeeBindingError(
            f"payment to {to!r} for {value!r} does not bind to approved decision "
            f"{decision.tx_id} ({decision.payee!r}, {decision.amount!r}, {decision.status})"
        )
