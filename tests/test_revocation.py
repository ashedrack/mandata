from mandata import Bounds, EvidenceLog, InMemoryRegistry, Mandate, enforce


def test_registry_revocation_rejects():
    reg = InMemoryRegistry()
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    reg.revoke(m.mandate_id)
    d = enforce(m, "store.example", 10.0, evidence_log=EvidenceLog(), registry=reg)
    assert not d.allowed and "registry" in d.reason


def test_revocation_affects_all_processes_sharing_registry():
    reg = InMemoryRegistry()
    m1 = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    m2 = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    m1.mandate_id = m2.mandate_id = "shared-id"
    reg.revoke("shared-id")
    log = EvidenceLog()
    assert not enforce(m1, "x", 1.0, evidence_log=log, registry=reg).allowed
    assert not enforce(m2, "x", 1.0, evidence_log=log, registry=reg).allowed


def test_restore_reenables():
    reg = InMemoryRegistry()
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    reg.revoke(m.mandate_id)
    reg.restore(m.mandate_id)
    assert enforce(m, "x", 10.0, evidence_log=EvidenceLog(), registry=reg).allowed
