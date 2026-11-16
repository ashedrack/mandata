"""MMF-v0.3 regressions: minor-unit amounts, idempotency + lifecycle, payee
binding, signed evidence, and cross-process atomicity. Each test maps to a
reviewer concern on the MMF paper (P0 items)."""

import multiprocessing as mp
from decimal import Decimal

import pytest
from nacl.signing import SigningKey

from mandata import (
    Bounds,
    EvidenceLog,
    LifecycleError,
    Mandate,
    SQLiteStore,
    claim,
    delegate,
    enforce,
    from_minor,
    register_currency,
    settle,
    to_minor,
    void,
)
from mandata.adapters import ap2, x402
from mandata.adapters.langchain import guard
from mandata.adapters.x402 import PayeeBindingError


def mk(**kw) -> Mandate:
    return Mandate(principal="u", agent="a", bounds=Bounds(max_amount=10_000, **kw))


# --- amounts ------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,currency,minor",
    [
        ("49.99", "GBP", 4999),
        ("49.9", "GBP", 4990),
        ("100", "USD", 10000),
        (Decimal("0.01"), "EUR", 1),
        ("1500", "JPY", 1500),
        ("1.234", "KWD", 1234),
        (7, "GBP", 700),
    ],
)
def test_to_minor_uses_currency_exponent(value, currency, minor):
    assert to_minor(value, currency) == minor


@pytest.mark.parametrize(
    "value,currency",
    [
        ("49.999", "GBP"),
        ("0.5", "JPY"),
        ("1.2345", "KWD"),
        ("abc", "GBP"),
        ("NaN", "GBP"),
        ("Infinity", "GBP"),
    ],
)
def test_to_minor_refuses_rather_than_rounds(value, currency):
    with pytest.raises(ValueError):
        to_minor(value, currency)


def test_to_minor_refuses_floats():
    with pytest.raises(TypeError):
        to_minor(49.99, "GBP")


def test_from_minor_and_custom_currency():
    assert from_minor(4999, "GBP") == "49.99"
    assert from_minor(1500, "JPY") == "1500"
    register_currency("USDC", 6)
    assert to_minor("1.5", "USDC") == 1_500_000


# --- idempotency and lifecycle --------------------------------------------------


def test_settled_or_voided_decision_no_longer_binds():
    m, log = mk(), EvidenceLog()
    d = enforce(m, "tesco.com", 500, tx_id="t1", evidence_log=log, currency="GBP")
    assert d.binds("tesco.com", 500)
    claim(m, "t1", "tesco.com", 500, "GBP", evidence_log=log)
    settle(m, "t1", evidence_log=log)
    retry = enforce(m, "tesco.com", 500, tx_id="t1", evidence_log=log, currency="GBP")
    assert retry.replayed and retry.status == "SETTLED"
    assert not retry.binds("tesco.com", 500)  # a retry can never be settled twice


def test_lifecycle_for_wrong_mandate_rejected():
    a, b, log = mk(), mk(), EvidenceLog()
    enforce(a, "x", 10, tx_id="t1", evidence_log=log, currency="GBP")
    with pytest.raises(LifecycleError):
        settle(b, "t1", evidence_log=log)


def test_void_releases_reservation_across_delegation_chain():
    parent = mk(total_budget=100)
    child = delegate(parent, "sub", Bounds(max_amount=100, total_budget=100))
    log = EvidenceLog()
    assert enforce(child, "x", 100, tx_id="t1", evidence_log=log, currency="GBP").allowed
    assert enforce(parent, "x", 1, tx_id="p1", evidence_log=log, currency="GBP").check == "BUDGET"
    void(child, "t1", evidence_log=log)
    assert enforce(parent, "x", 1, tx_id="p2", evidence_log=log, currency="GBP").allowed


def test_idempotency_survives_restart(tmp_path):
    path = tmp_path / "gate.db"
    m = mk(total_budget=100)
    assert enforce(
        m, "x", 80, tx_id="t1", store=SQLiteStore(path), evidence_log=EvidenceLog(), currency="GBP"
    )
    d = enforce(
        m, "x", 80, tx_id="t1", store=SQLiteStore(path), evidence_log=EvidenceLog(), currency="GBP"
    )
    assert d.replayed and d.allowed  # same answer after reopening the store
    d2 = enforce(
        m, "x", 80, tx_id="t2", store=SQLiteStore(path), evidence_log=EvidenceLog(), currency="GBP"
    )
    assert d2.check == "BUDGET"  # the first reservation still counts


def _spend_worker(path: str, start: int, n: int, out) -> None:
    m = Mandate(
        principal="u",
        agent="a",
        bounds=Bounds(max_amount=10, total_budget=100),
        mandate_id="shared",
        issued_at=0,
    )
    store = SQLiteStore(path)
    allowed = 0
    for i in range(n):
        d = enforce(
            m,
            "x",
            10,
            tx_id=f"w{start}-{i}",
            store=store,
            evidence_log=EvidenceLog(),
            at=1000,
            currency="GBP",
        )
        allowed += d.allowed
    out.put(allowed)


def test_sqlite_store_is_atomic_across_processes(tmp_path):
    """Four processes race on one budget of 100 (10 per tx): exactly 10 allowed."""
    path = str(tmp_path / "shared.db")
    SQLiteStore(path)  # create schema before the race
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    procs = [ctx.Process(target=_spend_worker, args=(path, k, 10, out)) for k in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert sum(out.get() for _ in procs) == 10


# --- payee binding ---------------------------------------------------------------


REQ = {"payTo": "0xMERCHANT", "maxAmountRequired": "1000000", "asset": "usdc"}


def test_x402_gate_uses_server_requirements_and_binds_payment():
    m = Mandate(
        principal="u",
        agent="a",
        bounds=Bounds(max_amount=2_000_000, currency="USDC", allowed_payees=["0xMERCHANT"]),
    )
    log = EvidenceLog()
    d = x402.enforce_requirements(m, REQ, tx_id="nonce-1", evidence_log=log)
    assert d.allowed and d.amount == 1_000_000
    x402.bind_payment(d, {"payload": {"authorization": {"to": "0xMERCHANT", "value": "1000000"}}})
    with pytest.raises(PayeeBindingError):
        x402.bind_payment(
            d, {"payload": {"authorization": {"to": "0xATTACKER", "value": "1000000"}}}
        )
    with pytest.raises(PayeeBindingError):
        x402.bind_payment(d, {"to": "0xMERCHANT", "value": "1000001"})


CART = {
    "contents": {
        "id": "cart-42",
        "merchant_name": "tesco.com",
        "payment_request": {
            "details": {"total": {"amount": {"currency": "GBP", "value": "49.99"}}}
        },
    }
}


def test_ap2_cart_gate_and_payment_mandate_binding():
    m = mk(allowed_payees=["tesco.com"])
    d = ap2.enforce_cart(m, CART, evidence_log=EvidenceLog())
    assert d.allowed and d.amount == 4999 and d.tx_id == "cart-42"
    good = {
        "payment_mandate_contents": {
            "merchant_agent": "tesco.com",
            "payment_details_total": {"amount": {"currency": "GBP", "value": "49.99"}},
        }
    }
    ap2.bind_payment_mandate(d, good)
    bad = {
        "payment_mandate_contents": {
            "merchant_agent": "tesco.com",
            "payment_details_total": {"amount": {"currency": "GBP", "value": "499.90"}},
        }
    }
    with pytest.raises(PayeeBindingError):
        ap2.bind_payment_mandate(d, bad)


def test_denied_decision_never_binds():
    d = enforce(
        mk(allowed_payees=["tesco.com"]), "evil.io", 10, evidence_log=EvidenceLog(), currency="GBP"
    )
    assert not d.binds("evil.io", 10)


# --- LangChain guard ----------------------------------------------------------------


def test_guard_converts_major_units_exactly_and_refuses_float_noise():
    m, seen = mk(), []
    safe = guard(m, lambda payee, amount: seen.append(amount) or "ok", evidence_log=EvidenceLog())
    assert safe(payee="x", amount="49.99") == "ok"
    out = safe(payee="x", amount=0.1 + 0.2)  # 0.30000000000000004 — not a valid GBP amount
    assert out["check"] == "NON_POSITIVE" and seen == ["49.99"]


def test_guard_passes_idempotency_key():
    m, log = mk(), EvidenceLog()
    safe = guard(m, lambda payee, amount, tx_id: "ok", evidence_log=log)
    safe(payee="x", amount="1", tx_id="k1")
    safe(payee="x", amount="1", tx_id="k1")
    assert [r.event for r in log.records] == ["DECISION", "REPLAY"]


# --- signed evidence ------------------------------------------------------------------


def test_signed_evidence_verifies_and_detects_forgery():
    key = SigningKey.generate()
    log = EvidenceLog(signing_key=key)
    m = mk()
    enforce(m, "x", 10, tx_id="t1", evidence_log=log, currency="GBP")
    claim(m, "t1", "x", 10, "GBP", evidence_log=log)
    settle(m, "t1", evidence_log=log)
    assert log.verify_chain(public_key=log.public_key)

    other = SigningKey.generate().verify_key.encode().hex()
    assert not log.verify_chain(public_key=other)  # wrong gate key

    # An operator who rewrites and re-chains history cannot re-sign it.
    forged = EvidenceLog()
    for r in log.records:
        forged.append(
            r.mandate_id,
            r.agent,
            r.payee,
            r.amount,
            r.allowed,
            r.check,
            r.timestamp,
            tx_id=r.tx_id,
            event=r.event,
        )
    assert forged.verify_chain() and not forged.verify_chain(public_key=log.public_key)


def test_unsigned_log_fails_signature_verification():
    log = EvidenceLog()
    enforce(mk(), "x", 10, evidence_log=log, currency="GBP")
    assert log.verify_chain() and not log.verify_chain(
        public_key=SigningKey.generate().verify_key.encode().hex()
    )
