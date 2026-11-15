import time

from mandata import Bounds, EvidenceLog, Mandate, enforce
from mandata.state import InMemoryStore, SQLiteStore


def test_cumulative_budget_allows_then_rejects():
    m = Mandate(
        principal="u",
        agent="a",
        bounds=Bounds(max_amount=1000, total_budget=100, budget_window_days=30),
    )
    log = EvidenceLog()
    assert enforce(m, "store.example", 60, evidence_log=log, currency="GBP").allowed
    assert not enforce(m, "store.example", 50, evidence_log=log, currency="GBP").allowed
    assert enforce(m, "store.example", 40, evidence_log=log, currency="GBP").allowed


def test_budget_respects_window():
    m = Mandate(
        principal="u",
        agent="a",
        bounds=Bounds(max_amount=1000, total_budget=100, budget_window_days=30),
    )
    past = time.time() - 31 * 86400
    m.record(past, 90)
    assert enforce(m, "store.example", 80, evidence_log=EvidenceLog(), currency="GBP").allowed


def test_sqlite_store_persists_across_instances(tmp_path):
    path = tmp_path / "state.db"
    s1 = SQLiteStore(path)
    s1.record("m1", time.time(), 25, "t1")
    s2 = SQLiteStore(path)  # reopen — same data must be visible
    count, total = s2.spend("m1", 0)
    assert count == 1 and total == 25


def test_enforce_uses_shared_store():
    """Two mandate objects sharing a store see each other's spend."""
    store = InMemoryStore()
    m1 = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100, velocity_limit=2))
    m1.mandate_id = "shared"
    m2 = Mandate(principal="u", agent="a2", bounds=Bounds(max_amount=100, velocity_limit=2))
    m2.mandate_id = "shared"
    log = EvidenceLog()
    assert enforce(m1, "store.example", 10, evidence_log=log, store=store, currency="GBP").allowed
    assert enforce(m2, "store.example", 10, evidence_log=log, store=store, currency="GBP").allowed
    assert not enforce(
        m1, "store.example", 10, evidence_log=log, store=store, currency="GBP"
    ).allowed
