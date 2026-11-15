import time

from mandata import Bounds, EvidenceLog, Mandate, enforce


def make_mandate(**over):
    b = Bounds(
        max_amount=50000,
        currency="GBP",
        allowed_payees=["store.example"],
        expires_in_days=30,
        velocity_limit=2,
    )
    m = Mandate(principal="user:alice", agent="agent:bot", bounds=b)
    for k, v in over.items():
        setattr(m, k, v)
    return m


def test_allows_within_bounds():
    log = EvidenceLog()
    m = make_mandate()
    d = enforce(m, "store.example", 4999, evidence_log=log, currency="GBP")
    assert d.allowed and d.reason == "within mandate"
    assert log.verify_chain()


def test_rejects_amount_over_cap():
    d = enforce(make_mandate(), "store.example", 50001, evidence_log=EvidenceLog(), currency="GBP")
    assert not d.allowed and "exceeds cap" in d.reason


def test_rejects_unlisted_payee():
    d = enforce(make_mandate(), "evil.example", 10, evidence_log=EvidenceLog(), currency="GBP")
    assert not d.allowed and "not in allowed set" in d.reason


def test_rejects_wrong_currency():
    d = enforce(make_mandate(), "store.example", 10, currency="USD", evidence_log=EvidenceLog())
    assert not d.allowed and "outside mandate" in d.reason


def test_rejects_expired():
    m = make_mandate()
    future = time.time() + 31 * 86400
    d = enforce(m, "store.example", 10, at=future, evidence_log=EvidenceLog(), currency="GBP")
    assert not d.allowed and d.reason == "mandate expired"


def test_rejects_revoked():
    m = make_mandate()
    m.revoke()
    d = enforce(m, "store.example", 10, evidence_log=EvidenceLog(), currency="GBP")
    assert not d.allowed and d.reason == "mandate revoked"


def test_velocity_limit():
    m, log = make_mandate(), EvidenceLog()
    assert enforce(m, "store.example", 10, evidence_log=log, currency="GBP").allowed
    assert enforce(m, "store.example", 10, evidence_log=log, currency="GBP").allowed
    assert not enforce(m, "store.example", 10, evidence_log=log, currency="GBP").allowed


def test_evidence_chain_detects_tampering():
    log = EvidenceLog()
    m = make_mandate()
    enforce(m, "store.example", 10, evidence_log=log, currency="GBP")
    enforce(m, "store.example", 10, evidence_log=log, currency="GBP")
    assert log.verify_chain()
    tampered = log.records[0].__class__(
        mandate_id=log.records[0].mandate_id,
        agent=log.records[0].agent,
        payee=log.records[0].payee,
        amount=999,
        allowed=True,
        check="WITHIN",
        timestamp=log.records[0].timestamp,
        prev_hash=log.records[0].prev_hash,
    )
    log.records[0] = tampered
    assert not log.verify_chain()
