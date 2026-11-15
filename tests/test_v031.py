"""MMF-v0.3.1 corrections — INVALID_CHAIN, CLAIMED dispatch, mandatory currency,
chain-wide credentials, atomic durable evidence."""

import threading

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
    settle,
    void,
)
from mandata.credentials import issue_credential
from mandata.models import EvidenceRecord
from mandata.state import AUTHORIZED, CLAIMED, SETTLED, VOIDED


def mk(**kw):
    bounds = Bounds(max_amount=kw.pop("max_amount", 100), **kw)
    return Mandate(principal="u", agent="a", bounds=bounds, issued_at=0)


def allowed(m, **kw):
    return enforce(
        m, "x", 10, currency="GBP", tx_id="t1", evidence_log=EvidenceLog(), at=1000, **kw
    )


# ------------------------------------------------------------- INVALID_CHAIN
def test_cyclic_chain_denies_invalid_chain():
    m = mk()
    m._parent = m
    m.parent_mandate_id = m.mandate_id
    d = allowed(m)
    assert not d.allowed and d.check == "INVALID_CHAIN"


def test_unresolvable_parent_denies_invalid_chain_not_revoked():
    m = mk()
    m.parent_mandate_id = "missing"
    d = allowed(m)
    assert not d.allowed and d.check == "INVALID_CHAIN"
    # recorded in the ledger: a replay returns the same INVALID_CHAIN denial
    d2 = allowed(m)
    assert d2.replayed and d2.check == "INVALID_CHAIN"


# ------------------------------------------------------------------ currency
def test_missing_currency_denies():
    assert enforce(mk(), "x", 10, evidence_log=EvidenceLog(), at=1000).check == "CURRENCY"


def test_mismatched_currency_denies():
    assert (
        enforce(mk(), "x", 10, currency="USD", evidence_log=EvidenceLog(), at=1000).check
        == "CURRENCY"
    )


# -------------------------------------------------------------------- claim
def test_claim_dispatches_exactly_once():
    m = mk()
    log = EvidenceLog()
    d = enforce(m, "x", 10, currency="GBP", tx_id="t1", evidence_log=log, at=1000)
    assert d.allowed and d.status == AUTHORIZED

    tx = claim(m, "t1", "x", 10, "GBP", evidence_log=log, at=1000)
    assert tx.status == CLAIMED

    with pytest.raises(LifecycleError):
        claim(m, "t1", "x", 10, "GBP", evidence_log=log, at=1000)  # second dispatcher loses

    settle(m, "t1", evidence_log=log, at=1000)
    assert [r.event for r in log.records] == ["DECISION", "CLAIM", "REJECTED", "SETTLE"]


def test_claim_rejects_unbound_instruction():
    m = mk()
    enforce(m, "x", 10, currency="GBP", tx_id="t1", evidence_log=EvidenceLog(), at=1000)
    with pytest.raises(LifecycleError):
        claim(m, "t1", "evil", 10, "GBP", at=1000)  # different beneficiary
    with pytest.raises(LifecycleError):
        claim(m, "t1", "x", 11, "GBP", at=1000)  # different amount


def test_replayed_decision_cannot_dispatch():
    m = mk()
    log = EvidenceLog()
    enforce(m, "x", 10, currency="GBP", tx_id="t1", evidence_log=log, at=1000)
    claim(m, "t1", "x", 10, "GBP", evidence_log=log, at=1000)
    d = enforce(m, "x", 10, currency="GBP", tx_id="t1", evidence_log=log, at=1000)
    assert d.replayed and d.status == CLAIMED and not d.binds("x", 10, "GBP")


def test_claim_slot_is_permanently_consumed():
    """MMF-v0.3.2: at most one dispatch per tx_id, unconditionally — the
    confirmed-never-invoked recovery path is void + a NEW tx_id, never
    a re-claim of the same one."""
    m = mk()
    log = EvidenceLog()
    enforce(m, "x", 10, currency="GBP", tx_id="t1", evidence_log=log, at=1000)
    claim(m, "t1", "x", 10, "GBP", evidence_log=log, at=1000)
    # operator confirms the rail was never invoked -> void releases budget
    void(m, "t1", evidence_log=log, at=1001)
    # but the claim slot stays consumed: same tx_id can never dispatch again
    with pytest.raises(LifecycleError):
        claim(m, "t1", "x", 10, "GBP", evidence_log=log, at=1002)
    # retry proceeds under a new tx_id (budget was released by the void)
    d = enforce(m, "x", 10, currency="GBP", tx_id="t2", evidence_log=log, at=1003)
    assert d.allowed
    assert [r.event for r in log.records] == [
        "DECISION",
        "CLAIM",
        "VOID",
        "REJECTED",
        "DECISION",
    ]


def test_settle_and_void_accept_claimed():
    m, m2 = mk(), mk()
    enforce(m, "x", 10, currency="GBP", tx_id="t1", evidence_log=EvidenceLog(), at=1000)
    claim(m, "t1", "x", 10, "GBP", at=1000)
    assert settle(m, "t1", at=1000).status == SETTLED

    enforce(m2, "x", 10, currency="GBP", tx_id="t2", evidence_log=EvidenceLog(), at=1000)
    claim(m2, "t2", "x", 10, "GBP")
    assert void(m2, "t2", at=1000).status == VOIDED


def test_concurrent_claimers_single_winner(tmp_path):
    store = SQLiteStore(tmp_path / "s.db")
    m = mk()
    m._state = store
    enforce(
        m, "x", 10, currency="GBP", tx_id="t1", store=store, evidence_log=EvidenceLog(), at=1000
    )
    wins, losses = [], []

    def racer():
        try:
            claim(m, "t1", "x", 10, "GBP", store=store, at=1000)
            wins.append(1)
        except LifecycleError:
            losses.append(1)

    ts = [threading.Thread(target=racer) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert wins == [1] and len(losses) == 7


# --------------------------------------------------------------- credentials
def test_credential_bound_gate_verifies_every_chain_member():
    key = SigningKey.generate()
    parent = mk(max_amount=1000)
    child = delegate(parent, "a-sub", Bounds(max_amount=500), at=0)
    cred_parent = issue_credential(parent, key)
    cred_child = issue_credential(child, key)
    pk = key.verify_key.encode().hex()

    # leaf credential only: parent unverified -> CREDENTIAL
    d = enforce(
        child,
        "x",
        10,
        currency="GBP",
        evidence_log=EvidenceLog(),
        at=1000,
        credential=cred_child,
        principal_key=pk,
    )
    assert not d.allowed and d.check == "CREDENTIAL"

    # both credentials: verifies
    d = enforce(
        child,
        "x",
        10,
        currency="GBP",
        evidence_log=EvidenceLog(),
        at=1000,
        credential={child.mandate_id: cred_child, parent.mandate_id: cred_parent},
        principal_key=pk,
    )
    assert d.allowed


# ------------------------------------------- atomic durable evidence (SQLite)
def test_evidence_persists_and_reloads(tmp_path):
    store = SQLiteStore(tmp_path / "s.db")
    m = mk()
    log = EvidenceLog(store=store)
    d = enforce(m, "x", 10, currency="GBP", tx_id="t1", store=store, evidence_log=log, at=1000)
    assert d.allowed
    settle(m, "t1", store=store, evidence_log=log, at=1000)

    reloaded = EvidenceLog(store=store)
    assert [r.event for r in reloaded.records] == ["DECISION", "SETTLE"]
    assert reloaded.verify_chain()
    assert reloaded.records[0].hash == log.records[0].hash


def test_failed_evidence_append_rolls_back_state(tmp_path):
    """A crash between state commit and evidence append must not leave a
    decided transaction without its record: both roll back together."""

    class FailingStore(SQLiteStore):
        def append_evidence(self, record):
            raise RuntimeError("disk full")

    store = FailingStore(tmp_path / "s.db")
    m = mk()
    with pytest.raises(RuntimeError):
        enforce(
            m,
            "x",
            10,
            currency="GBP",
            tx_id="t1",
            store=store,
            evidence_log=EvidenceLog(store=store),
            at=1000,
        )
    # nothing committed: no ledger entry, no spend, no evidence
    assert store.get_tx("t1") is None
    assert store.spend(m.mandate_id, 0) == (0, 0)
    assert store.evidence_records() == []


def test_pipe_in_identifier_fails_closed():
    rec = EvidenceRecord(
        mandate_id="m1",
        agent="a",
        payee="bad|pipe",
        amount=10,
        allowed=True,
        check="WITHIN",
        timestamp=1000,
        prev_hash="0" * 64,
    )
    with pytest.raises(ValueError):
        rec.canonical_payload()


def test_rejected_lifecycle_transitions_are_security_evidence():
    """MMF-v0.3.2: rejected lifecycle operations append a REJECTED record —
    a binding-mismatched claim is exactly the T6 substitution signal."""
    m = mk()
    log = EvidenceLog()
    enforce(m, "x", 10, currency="GBP", tx_id="t1", evidence_log=log, at=1000)
    n = len(log.records)
    with pytest.raises(LifecycleError):
        settle(m, "nope", evidence_log=log, at=1000)
    with pytest.raises(LifecycleError):
        claim(m, "t1", "evil", 10, "GBP", evidence_log=log, at=1000)
    new = log.records[n:]
    assert [r.event for r in new] == ["REJECTED", "REJECTED"]
    assert new[0].check == "SETTLE" and not new[0].allowed
    assert new[1].check == "CLAIM" and new[1].payee == "evil"
    assert log.verify_chain()  # rejections chain like everything else


def test_guard_settle_on_success_claims_settles_and_voids():
    import time

    from mandata.adapters.langchain import guard

    # guard enforces on the wall clock, so the mandate must be issued "now"
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100), issued_at=time.time())
    log = EvidenceLog()

    pay = guard(
        m,
        lambda payee, amount, tid: "ok",
        payee_arg="payee",
        amount_arg="amount",
        tx_id_arg="tid",
        amount_unit="minor",
        evidence_log=log,
        settle_on_success=True,
    )
    assert pay(payee="x", amount=10, tid="g1") == "ok"
    assert [r.event for r in log.records] == ["DECISION", "CLAIM", "SETTLE"]

    m2 = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100), issued_at=time.time())
    log2 = EvidenceLog()

    def _boom(payee, amount, tid):
        raise RuntimeError("rail down")

    pay_fail = guard(
        m2,
        _boom,
        payee_arg="payee",
        amount_arg="amount",
        tx_id_arg="tid",
        amount_unit="minor",
        evidence_log=log2,
        settle_on_success=True,
    )

    with pytest.raises(RuntimeError):
        pay_fail(payee="x", amount=10, tid="g2")
    assert [r.event for r in log2.records] == ["DECISION", "CLAIM", "VOID"]


# ---------------------------------------------------------------- v0.3.2
def test_void_releases_budget_but_not_velocity():
    m = mk(velocity_limit=1, total_budget=100)
    log = EvidenceLog()
    assert enforce(m, "x", 10, currency="GBP", tx_id="t1", evidence_log=log, at=1000).allowed
    void(m, "t1", evidence_log=log, at=1001)
    # budget refunded but the authorization slot is spent: still VELOCITY
    d = enforce(m, "x", 10, currency="GBP", tx_id="t2", evidence_log=log, at=1002)
    assert not d.allowed and d.check == "VELOCITY"


def test_tx_id_namespace_is_per_gate_cross_mandate_conflict():
    """The ledger key is tx_id within a gate's store; two mandates colliding
    on the same key fail closed with TX_CONFLICT."""
    from mandata.state import InMemoryStore

    store = InMemoryStore()
    m1, m2 = mk(), mk()
    m1._state = m2._state = store
    log = EvidenceLog()
    assert enforce(
        m1, "x", 10, currency="GBP", tx_id="order-1", store=store, evidence_log=log, at=1000
    ).allowed
    d = enforce(
        m2, "x", 10, currency="GBP", tx_id="order-1", store=store, evidence_log=log, at=1001
    )
    assert not d.allowed and d.check == "TX_CONFLICT"
    # and a lifecycle op scoped to the wrong mandate is rejected
    with pytest.raises(LifecycleError):
        settle(m2, "order-1", store=store, evidence_log=log, at=1002)


def test_child_budget_window_must_not_cycle_faster_than_parent():
    parent = mk(total_budget=1000, budget_window_days=30)
    # shorter window cycles the allowance faster -> rejected as widening
    with pytest.raises(ValueError, match="window"):
        delegate(parent, "s", Bounds(max_amount=100, total_budget=100, budget_window_days=1), at=0)
    # equal or longer is fine
    ok = delegate(
        parent, "s", Bounds(max_amount=100, total_budget=100, budget_window_days=60), at=0
    )
    assert ok.parent_mandate_id == parent.mandate_id
