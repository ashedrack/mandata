from mandata import Bounds, EvidenceLog, Mandate, enforce
from mandata.anchoring import Anchor, verify_anchor


def _log_with(n: int) -> EvidenceLog:
    log = EvidenceLog()
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    for _ in range(n):
        enforce(m, "x", 10.0, evidence_log=log)
    return log


def test_anchor_survives_append():
    log = _log_with(2)
    a = Anchor(head_hash=log.head_hash, service_token="tok", service_url="http://x")
    assert verify_anchor(log, a)
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    enforce(m, "x", 10.0, evidence_log=log)
    assert verify_anchor(log, a)


def test_anchor_detects_tail_truncation():
    log = _log_with(3)
    a = Anchor(head_hash=log.head_hash, service_token="tok", service_url="http://x")
    del log.records[-1]  # operator silently drops the latest decision
    assert log.verify_chain()  # a bare hash chain cannot see this...
    assert not verify_anchor(log, a)  # ...the anchor can


def test_anchor_detects_rewrite_of_last_record():
    log = _log_with(2)
    a = Anchor(head_hash=log.head_hash, service_token="tok", service_url="http://x")
    last = log.records[-1]
    log.records[-1] = last.__class__(**{**last.__dict__, "amount": 1.0})
    assert log.verify_chain()  # the final record has no successor to expose it
    assert not verify_anchor(log, a)
