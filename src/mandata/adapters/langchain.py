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
    safe(payee="tesco.com", amount=49.99)

On denial the wrapped callable is NEVER invoked — the guard returns a dict
describing the rejection, which the agent observes as tool output.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..enforce import enforce
from ..models import Mandate


def guard(
    mandate: Mandate,
    fn: Callable,
    *,
    payee_arg: str = "payee",
    amount_arg: str = "amount",
    currency_arg: str = "currency",
    evidence_log=None,
    store=None,
    registry=None,
    hooks=(),
    raise_on_deny: bool = False,
) -> Callable:
    """Wrap ``fn`` so every call is gated by ``enforce(mandate, ...)``.

    Args:
        mandate: the mandate to enforce
        fn: the tool/function to protect — invoked only on allow
        payee_arg / amount_arg / currency_arg: kwarg names to extract from calls
        evidence_log, store, registry, hooks: passed through to enforce()
        raise_on_deny: if True, raise PermissionError on denial instead of
            returning a dict (useful outside agent loops)

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
            amount=float(amount),
            currency=currency,
            evidence_log=evidence_log,
            store=store,
            registry=registry,
            hooks=hooks,
        )
        if not decision.allowed:
            if raise_on_deny:
                raise PermissionError(f"mandate denied: {decision.reason}")
            return {"denied": decision.reason, "allowed": False, "payee": str(payee)}
        return fn(*args, **kwargs)

    wrapper.__name__ = getattr(fn, "__name__", "guarded_tool")
    wrapper.__doc__ = f"[mandate-gated] {getattr(fn, '__doc__', '') or ''}"
    wrapper.__wrapped__ = fn
    return wrapper


def _bind(fn: Callable, args: tuple, kwargs: dict) -> dict:
    """Best-effort arg binding: inspect signature, fall back to kwargs."""
    import inspect

    try:
        sig = inspect.signature(fn.func if hasattr(fn, "func") else fn)
        return dict(sig.bind_partial(*args, **kwargs).arguments)
    except (TypeError, ValueError):
        return dict(kwargs)
