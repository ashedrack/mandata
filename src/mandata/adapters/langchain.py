"""LangChain adapter — a tool guard that enforces mandates before tool execution.

Wraps any callable — a LangChain ``@tool`` function, a StructuredTool's ``func``,
or a plain function — so the mandate gate decides *before* the tool runs.

Usage with a LangChain tool::

    from mandata.adapters.langchain import guard

    @tool
    def pay_merchant(payee: str, amount: float) -> str:
        '''Pay a merchant.'''
        return rail.settle(payee, amount)

    safe_pay = guard(mandate, pay_merchant)          # wraps .func / the callable
    agent = create_react_agent(llm, tools=[safe_pay_tool])

Or with any callable::

    safe = guard(mandate, my_payment_fn)
    safe(payee="tesco.com", amount="49.99")

Tool amounts are major units by default (``"49.99"`` GBP -> 4999 pence);
pass ``amount_unit="minor"`` if the tool already takes minor units. A value
that cannot be converted exactly is passed to the gate as-is and denied
(``NON_POSITIVE``) — the guard never rounds.

On denial the wrapped callable is NEVER invoked — the guard returns a dict
describing the rejection, which the agent observes as tool output.

Deployment note (MMF §5.3): the guard runs inside the agent's process, so it
defends against model error and prompt injection, not against a compromised
agent runtime. For complete mediation, run the gate where the rail
credentials live.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..enforce import enforce
from ..models import Mandate
from ..money import to_minor


def guard(
    mandate: Mandate,
    fn: Callable,
    *,
    payee_arg: str = "payee",
    amount_arg: str = "amount",
    currency_arg: str = "currency",
    tx_id_arg: str = "tx_id",
    amount_unit: str = "major",
    evidence_log=None,
    store=None,
    registry=None,
    hooks=(),
    raise_on_deny: bool = False,
    settle_on_success: bool = False,
) -> Callable:
    """Wrap ``fn`` so every call is gated by ``enforce(mandate, ...)``.

    Args:
        mandate: the mandate to enforce
        fn: the tool/function to protect — invoked only on allow
        payee_arg / amount_arg / currency_arg: kwarg names to extract from calls
        tx_id_arg: kwarg carrying an idempotency key, if the tool has one
        amount_unit: ``"major"`` (decimal, default) or ``"minor"`` (integer)
        evidence_log, store, registry, hooks: passed through to enforce()
        raise_on_deny: if True, raise PermissionError on denial instead of
            returning a dict (useful outside agent loops)
        settle_on_success: if True, complete the lifecycle around the tool
            call: ``claim()`` the authorization for dispatch, ``void()`` it if
            ``fn`` raises, ``settle()`` it if ``fn`` returns. Otherwise
            authorizations stay reserved and the caller must complete the
            lifecycle itself.

    Returns:
        A callable with the same signature; returns ``fn(...)`` on allow or a
        ``{"denied": ..., "check": ...}`` dict on denial.
    """

    def wrapper(*args, **kwargs) -> Any:
        bound = _bind(fn, args, kwargs)
        payee = bound.get(payee_arg)
        amount = bound.get(amount_arg)
        currency = bound.get(currency_arg)
        if payee is None or amount is None:
            raise TypeError(
                f"mandata guard could not extract '{payee_arg}'/'{amount_arg}' "
                f"from call arguments {sorted(bound)} — pass *_arg names matching "
                "your function's parameters"
            )
        decision = enforce(
            mandate,
            payee=str(payee),
            amount=_to_gate_amount(amount, currency or mandate.bounds.currency, amount_unit),
            # A tool with no currency arg transacts in the mandate's own
            # currency — the only value the gate would accept anyway.
            currency=str(currency) if currency is not None else mandate.bounds.currency,
            evidence_log=evidence_log,
            store=store,
            registry=registry,
            hooks=hooks,
            tx_id=bound.get(tx_id_arg),
        )
        if not decision.allowed:
            if raise_on_deny:
                raise PermissionError(f"mandate denied [{decision.check}]: {decision.reason}")
            return {
                "denied": decision.reason,
                "check": decision.check,
                "allowed": False,
                "payee": str(payee),
            }
        if not settle_on_success:
            return fn(*args, **kwargs)
        from ..enforce import LifecycleError, claim, settle, void

        gate_amount = _to_gate_amount(amount, currency or mandate.bounds.currency, amount_unit)
        gate_currency = str(currency) if currency is not None else mandate.bounds.currency
        try:
            claim(
                mandate,
                decision.tx_id,
                str(payee),
                gate_amount,
                gate_currency,
                evidence_log=evidence_log,
                store=store,
            )
        except LifecycleError as exc:
            raise PermissionError(f"mandate dispatch rejected: {exc}") from exc
        try:
            result = fn(*args, **kwargs)
        except Exception:
            void(mandate, decision.tx_id, evidence_log=evidence_log, store=store)
            raise
        settle(mandate, decision.tx_id, evidence_log=evidence_log, store=store)
        return result

    wrapper.__name__ = getattr(fn, "__name__", "guarded_tool")
    wrapper.__doc__ = f"[mandate-gated] {getattr(fn, '__doc__', '') or ''}"
    wrapper.__wrapped__ = fn
    return wrapper


def _to_gate_amount(amount, currency: str, unit: str):
    if unit == "minor":
        return amount
    try:
        # str() of a float is its shortest round-trip form, so 49.99 -> "49.99".
        return to_minor(amount if isinstance(amount, str) else str(amount), currency)
    except (TypeError, ValueError):
        return amount  # the gate denies it as NON_POSITIVE; never round


def _bind(fn: Callable, args: tuple, kwargs: dict) -> dict:
    """Best-effort arg binding: inspect signature, fall back to kwargs."""
    import inspect

    try:
        sig = inspect.signature(fn.func if hasattr(fn, "func") else fn)
        return dict(sig.bind_partial(*args, **kwargs).arguments)
    except (TypeError, ValueError):
        return dict(kwargs)
