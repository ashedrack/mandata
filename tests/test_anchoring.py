from mandata import Bounds, EvidenceLog, Mandate, enforce
from mandata.anchoring import Anchor, verify_anchor


def test_anchor_verification():
    log = EvidenceLog()
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    enforce(m, "x", 10.0, evidence_log=log)
    a = Anchor(head_hash=log.head_hash, service_token="tok", service_url="http://x")
    assert verify_anchor(log, a, "http://x")
    enforce(m, "x", 10.0, evidence_log=log)
    assert not verify_anchor(log, a, "http://x")
