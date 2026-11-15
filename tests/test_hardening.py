"""Regression tests for the v0.3 hardening — each maps to a claim in the MMF paper."""

import hashlib
import threading

import pytest
from nacl.signing import SigningKey

from mandata import Bounds, EvidenceLog, InMemoryRegistry, Mandate, delegate, enforce
from mandata.adapters.langchain import guard
from mandata.credentials import issue_credential, verify_credential
from mandata.models import EvidenceRecord, canonical_number


def mk(**kw) -> Mandate:
    return Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100, **kw))


# --- fail-safe defaults -------------------------------------------------------


@pytest.mark.parametrize(
    "amount", [float("nan"), float("inf"), -float("inf"), "10", None, 10.0, 49.99, True, 0, -5]
)
def test_non_finite_or_non_numeric_amount_denied(amount):
    """Only positive integers in minor units pass: floats (even 10.0) never do."""
    log = EvidenceLog()
    d = enforce(mk(total_budget=50), "x", amount, evidence_log=log, currency="GBP")
    assert not d.allowed and d.check == "NON_POSITIVE"
    assert log.verify_chain()


@pytest.mark.parametrize(
    "kw",
    [
        {"max_amount": 0},
        {"max_amount": float("nan")},
        {"max_amount": 10.0},
        {"max_amount": 10, "total_budget": 50.5},
        {"max_amount": 10, "expires_in_days": 0},
        {"max_amount": 10, "velocity_limit": -1},
        {"max_amount": 10, "total_budget": 0},
        {"max_amount": 10, "allowed_payees": "tesco.com"},
        {"max_amount": 10, "currency": ""},
    ],
)
def test_malformed_bounds_rejected_at_construction(kw):
    with pytest.raises(ValueError, match="invalid bounds"):
        Bounds(**kw)


def test_unreachable_registry_denies_and_records_evidence():
    class Down:
        def is_revoked(self, mandate_id):
            raise ConnectionError("timeout")

    log = EvidenceLog()
    d = enforce(mk(), "x", 10, evidence_log=log, registry=Down(), currency="GBP")
    assert not d.allowed and d.check == "REVOKED_REGISTRY"
    assert "unavailable" in d.reason
    assert len(log.records) == 1 and log.records[0].check == "REVOKED_REGISTRY"


def test_at_zero_is_honoured_not_replaced_by_wall_clock():
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100), issued_at=0)
    d = enforce(m, "x", 1, at=0, evidence_log=EvidenceLog(), currency="GBP")
    assert d.allowed and d.evidence.timestamp == 0


def test_gate_clock_is_authoritative_when_at_omitted():
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100, expires_in_days=1))
    future = m.expires_at + 1
    d = enforce(m, "x", 1, evidence_log=EvidenceLog(), clock=lambda: future, currency="GBP")
    assert d.check == "EXPIRED"


# --- normative check IDs + canonical evidence --------------------------------


def test_decision_and_evidence_carry_check_id():
    d = enforce(mk(), "x", 500, evidence_log=EvidenceLog(), currency="GBP")
    assert d.check == "CAP_EXCEEDED" and d.evidence.check == "CAP_EXCEEDED"
    assert "exceeds cap" in d.reason  # advisory text still available


@pytest.mark.parametrize(
    "x,expected",
    [
        (100.0, "100"),
        (100, "100"),
        (49.99, "49.99"),
        (0.5, "0.5"),
        (1e-7, "0.0000001"),
        (1730000000.0, "1730000000"),
        (-0.0, "0"),
    ],
)
def test_canonical_number(x, expected):
    assert canonical_number(x) == expected


def test_evidence_hash_is_fixed_cross_language_vector():
    rec = EvidenceRecord(
        mandate_id="m1",
        agent="a",
        payee="tesco.com",
        amount=4999,
        allowed=True,
        check="WITHIN",
        timestamp=1000,
        prev_hash="0" * 64,
        detail="ignored by hash",
        tx_id="t1",
    )
    payload = "DECISION|t1|m1|a|tesco.com|4999|true|WITHIN|1000|" + "0" * 64
    assert rec.canonical_payload() == payload
    assert rec.hash == hashlib.sha256(payload.encode()).hexdigest()


def test_advisory_detail_does_not_affect_hash():
    base = {
        "mandate_id": "m",
        "agent": "a",
        "payee": "p",
        "amount": 1,
        "allowed": False,
        "check": "PAYEE",
        "timestamp": 1,
        "prev_hash": "0" * 64,
    }
    assert EvidenceRecord(**base, detail="x").hash == EvidenceRecord(**base, detail="y").hash


# --- credential-bound gate ----------------------------------------------------


def test_gate_verifies_credential_first():
    key = SigningKey.generate()
    pub = key.verify_key.encode().hex()
    m = mk()
    cred = issue_credential(m, key)
    assert enforce(
        m, "x", 10, evidence_log=EvidenceLog(), credential=cred, principal_key=pub, currency="GBP"
    ).allowed

    other = SigningKey.generate().verify_key.encode().hex()
    d = enforce(
        m, "x", 10, evidence_log=EvidenceLog(), credential=cred, principal_key=other, currency="GBP"
    )
    assert d.check == "CREDENTIAL"


def test_credential_check_precedes_revocation():
    key = SigningKey.generate()
    m = mk()
    cred = issue_credential(m, key)
    m.revoke()
    d = enforce(
        m,
        "x",
        10,
        evidence_log=EvidenceLog(),
        credential=cred,
        principal_key=SigningKey.generate().verify_key.encode().hex(),
        currency="GBP",
    )
    assert d.check == "CREDENTIAL"


def test_half_supplied_credential_binding_denied():
    d = enforce(mk(), "x", 10, evidence_log=EvidenceLog(), principal_key="ab" * 32, currency="GBP")
    assert d.check == "CREDENTIAL"


def test_credential_covers_budget_fields():
    key = SigningKey.generate()
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100, total_budget=200))
    cred = issue_credential(m, key)
    m.bounds = Bounds(max_amount=100, total_budget=10_000)  # silent widening attempt
    ok, why = verify_credential(cred, m, key.verify_key.encode().hex())
    assert not ok and "bounds altered" in why


def test_malformed_signature_rejected_not_raised():
    key = SigningKey.generate()
    m = mk()
    cred = issue_credential(m, key)
    bad = cred.__class__(**{**cred.__dict__, "signature": "not-hex"})
    assert verify_credential(bad, m, key.verify_key.encode().hex()) == (False, "invalid signature")


# --- delegation chain ---------------------------------------------------------


def test_registry_revocation_of_parent_stops_child():
    reg = InMemoryRegistry()
    parent = mk()
    child = delegate(parent, "sub", Bounds(max_amount=50))
    reg.revoke(parent.mandate_id)
    d = enforce(child, "x", 10, evidence_log=EvidenceLog(), registry=reg, currency="GBP")
    assert d.check == "REVOKED_REGISTRY" and "ancestor" in d.reason


def test_unresolvable_parent_denied():
    orphan = mk()
    orphan.parent_mandate_id = "missing"
    assert (
        enforce(orphan, "x", 1, evidence_log=EvidenceLog(), currency="GBP").check == "INVALID_CHAIN"
    )


def test_concurrent_siblings_cannot_overspend_parent_budget():
    parent = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=10, total_budget=100))
    kids = [delegate(parent, f"s{i}", Bounds(max_amount=10, total_budget=100)) for i in range(4)]
    log, results = EvidenceLog(), []

    def spend(child):
        for _ in range(10):
            results.append(enforce(child, "x", 10, evidence_log=log, currency="GBP").allowed)

    threads = [threading.Thread(target=spend, args=(k,)) for k in kids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 10  # parent budget 100 / 10 per tx, across all siblings
    assert log.verify_chain()


# --- adapters -----------------------------------------------------------------


def test_langchain_guard_reports_check_id():
    calls = []
    safe = guard(
        mk(allowed_payees=["tesco"]), lambda payee, amount: calls.append(payee), amount_unit="minor"
    )
    out = safe(payee="evil.io", amount=5)
    assert out["check"] == "PAYEE" and calls == []


@pytest.mark.parametrize("body", [{}, {"revoked": "no"}, {"revoked": None}, []])
def test_http_registry_malformed_response_fails_closed(body):
    from mandata import HTTPRegistry

    reg = HTTPRegistry("http://registry.invalid")
    reg._request = lambda method, path, payload=None: body
    d = enforce(mk(), "x", 10, evidence_log=EvidenceLog(), registry=reg, currency="GBP")
    assert not d.allowed and d.check == "REVOKED_REGISTRY"
