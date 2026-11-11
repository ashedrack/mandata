import threading

from mandata import Bounds, EvidenceLog, Mandate, enforce


def test_parallel_calls_never_exceed_velocity_limit():
    """50 threads racing on a velocity_limit=5 mandate: exactly 5 may pass."""
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100, velocity_limit=5))
    log = EvidenceLog()
    results = []

    def attempt():
        d = enforce(m, "store.example", 10.0, evidence_log=log)
        results.append(d.allowed)

    threads = [threading.Thread(target=attempt) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 5
    assert log.verify_chain()
    assert len(log.records) == 50


def test_records_stay_chained_under_parallel_appends():
    """Concurrent appends must not corrupt the hash chain."""
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100, velocity_limit=0))
    log = EvidenceLog()
    m2 = Mandate(principal="u2", agent="b", bounds=Bounds(max_amount=50))

    def attempt(mandate):
        enforce(mandate, "store.example", 5.0, evidence_log=log)

    threads = [threading.Thread(target=attempt, args=(m,)) for _ in range(30)] + [
        threading.Thread(target=attempt, args=(m2,)) for _ in range(30)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(log.records) == 60
    assert log.verify_chain()
