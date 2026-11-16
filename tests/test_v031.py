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
    void(m, "t1", evidence_log=log, at=1001, cancellation="rail-decline:confirmed")
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
    claim(m2, "t2", "x", 10, "GBP", at=1000)
    assert void(m2, "t2", at=1000, cancellation="rail-decline:confirmed").status == VOIDED


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
    claim(m, "t1", "x", 10, "GBP", store=store, evidence_log=log, at=1000)
    settle(m, "t1", store=store, evidence_log=log, at=1000)

    reloaded = EvidenceLog(store=store)
    assert [r.event for r in reloaded.records] == ["DECISION", "CLAIM", "SETTLE"]
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


def test_pipe_in_identifier_serializes_under_length_prefix():
    # v3 payloads are length-prefixed, so "|" inside a field is unambiguous —
    # malformed identifiers can always be recorded rather than dropped.
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
    assert "8:bad|pipe" in rec.canonical_payload()


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
    assert new[0].check == "SETTLE:UNKNOWN_TX" and not new[0].allowed
    assert new[1].check == "CLAIM:BINDING" and new[1].payee == "evil"
    assert log.verify_chain()  # rejections chain like everything else


def test_guard_settle_on_success_claims_settles_and_stays_claimed_on_error():
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
    # fn raised => uncertain outcome: stays CLAIMED for operator reconciliation
    assert [r.event for r in log2.records] == ["DECISION", "CLAIM"]


# ---------------------------------------------------------------- v0.3.2
def test_void_releases_budget_but_not_velocity():
    m = mk(velocity_limit=1, total_budget=100)
    log = EvidenceLog()
    assert enforce(m, "x", 10, currency="GBP", tx_id="t1", evidence_log=log, at=1000).allowed
    void(m, "t1", evidence_log=log, at=1001, cancellation="rail-decline:confirmed")
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


def test_claim_after_revocation_is_rejected():
    """MMF §5.4: revocation kills undispatched authorizations at claim time."""
    m = Mandate(
        mandate_id="m",
        agent="a",
        principal="p",
        bounds=Bounds(max_amount=100, currency="GBP"),
    )
    log = EvidenceLog()
    d = enforce(m, "x", 10, "GBP", tx_id="t1", at=1000, evidence_log=log)
    assert d.allowed
    m.revoked = True
    with pytest.raises(LifecycleError):
        claim(m, "t1", "x", 10, "GBP", evidence_log=log, at=1001)
    assert [r.event for r in log.records] == ["DECISION", "REJECTED"]
    # facts may still be recorded/released post-revocation
    assert void(m, "t1", evidence_log=log, at=1002).status == VOIDED


def test_claim_after_revocation_registry_fails_closed():
    class Reg:
        def is_revoked(self, mid):
            return mid == "m"

    m = Mandate(
        mandate_id="m",
        agent="a",
        principal="p",
        bounds=Bounds(max_amount=100, currency="GBP"),
    )
    log = EvidenceLog()
    enforce(m, "x", 10, "GBP", tx_id="t1", at=1000, evidence_log=log, registry=Reg())
    with pytest.raises(LifecycleError):
        claim(m, "t1", "x", 10, "GBP", evidence_log=log, at=1001, registry=Reg())


def test_repeated_void_and_settle_append_no_evidence():
    m, log = mk(), EvidenceLog()
    enforce(m, "x", 10, currency="GBP", tx_id="v1", evidence_log=log, at=1000)
    void(m, "v1", evidence_log=log, at=1000)
    n = len(log.records)
    assert void(m, "v1", evidence_log=log, at=1001).status == "VOIDED"
    assert len(log.records) == n
    enforce(m, "x", 10, currency="GBP", tx_id="s1", evidence_log=log, at=1000)
    claim(m, "s1", "x", 10, "GBP", evidence_log=log, at=1000)
    settle(m, "s1", evidence_log=log, at=1000)
    n = len(log.records)
    assert settle(m, "s1", evidence_log=log, at=1001).status == SETTLED
    assert len(log.records) == n


def test_decided_transaction_replays_after_chain_becomes_invalid():
    parent = mk(max_amount=1000)
    child = delegate(parent, "a-sub", Bounds(max_amount=500), at=0)
    log = EvidenceLog()
    d = enforce(child, "x", 10, currency="GBP", tx_id="t1", evidence_log=log, at=1000)
    assert d.allowed
    child.parent_mandate_id = "missing"
    child._parent = None
    d2 = enforce(child, "x", 10, currency="GBP", tx_id="t1", evidence_log=log, at=1001)
    assert d2.replayed and d2.allowed and d2.check == "WITHIN"
    d3 = enforce(child, "x", 10, currency="GBP", tx_id="t2", evidence_log=log, at=1002)
    assert not d3.allowed and d3.check == "INVALID_CHAIN"


def test_structural_attenuation_implies_child_history_acceptable_to_parent():
    """Structural attenuation (cap <=, window >=) means any history the child
    policy accepts on its own is also accepted by the parent policy standing
    alone — checked over random settled/unresolved histories."""
    import random

    rng = random.Random(20260101)
    day = 86400
    for _ in range(200):
        wp = rng.choice([1, 2, 3])
        wc = wp + rng.choice([0, 1, 2])
        bp = rng.randint(60, 150)
        bc = rng.randint(20, bp)
        parent = Mandate(
            principal="p",
            agent="a",
            bounds=Bounds(max_amount=bp, total_budget=bp, budget_window_days=wp, expires_in_days=30),
        )
        child = Mandate(
            principal="p",
            agent="a",
            bounds=Bounds(max_amount=bc, total_budget=bc, budget_window_days=wc, expires_in_days=30),
        )
        t = 0.0
        for i in range(rng.randint(3, 12)):
            t += rng.uniform(0, 1.5 * day)
            amt = rng.randint(1, bc)
            dc = enforce(child, "x", amt, currency="GBP", tx_id=f"c{i}", evidence_log=EvidenceLog(), at=t)
            if not dc.allowed:
                continue
            if rng.random() < 0.7:
                claim(child, f"c{i}", "x", amt, "GBP", evidence_log=EvidenceLog(), at=t)
                settle(child, f"c{i}", evidence_log=EvidenceLog(), at=t)
            dp = enforce(parent, "x", amt, currency="GBP", tx_id=f"c{i}", evidence_log=EvidenceLog(), at=t)
            assert dp.allowed, (wp, wc, bp, bc, i)
            if parent._state.get_tx(f"c{i}").status == "AUTHORIZED" and child._state.get_tx(f"c{i}").status == "SETTLED":
                claim(parent, f"c{i}", "x", amt, "GBP", evidence_log=EvidenceLog(), at=t)
                settle(parent, f"c{i}", evidence_log=EvidenceLog(), at=t)


def test_cross_mandate_rejection_does_not_copy_foreign_transaction_into_evidence(tmp_path):
    store = SQLiteStore(tmp_path / "s.db")
    a, b = mk(), mk()
    log = EvidenceLog(store=store)
    enforce(a, "secret.example", 123, currency="GBP", tx_id="t1", store=store, evidence_log=log, at=1000)
    for fn in (
        lambda: settle(b, "t1", store=store, evidence_log=log, at=1001),
        lambda: claim(b, "t1", "x", 1, "GBP", store=store, evidence_log=log, at=1001),
    ):
        with pytest.raises(LifecycleError):
            fn()
    settle_rec, claim_rec = log.records[-2:]
    assert settle_rec.check == "SETTLE:UNKNOWN_TX" and claim_rec.check == "CLAIM:UNKNOWN_TX"
    assert settle_rec.payee == "" and settle_rec.amount is None and settle_rec.currency == ""
    assert settle_rec.status == "" and claim_rec.status == ""
    assert claim_rec.payee == "x" and claim_rec.amount == 1  # attempted instruction
    assert a._state is not b._state


def test_void_verifier_rejects_reference_not_bound_to_instruction_and_fails_closed():
    m, log = mk(), EvidenceLog()
    enforce(m, "x", 10, currency="GBP", tx_id="rk1", evidence_log=log, at=1000)
    claim(m, "rk1", "x", 10, "GBP", evidence_log=log, at=1000)

    def bound_to_this_tx(ref, tx):
        return ref == f"decline:{tx.tx_id}"

    with pytest.raises(LifecycleError):
        void(m, "rk1", evidence_log=log, at=1001, cancellation="decline:OTHER-TX",
             verify_cancellation=bound_to_this_tx)
    assert log.records[-1].check == "VOID:CANCELLATION" and log.records[-1].event == "REJECTED"
    assert m._state.get_tx("rk1").status == "CLAIMED"  # reservation still held

    def crashing(ref, tx):
        raise RuntimeError("verifier down")

    with pytest.raises(LifecycleError):
        void(m, "rk1", evidence_log=log, at=1002, cancellation="decline:rk1",
             verify_cancellation=crashing)
    assert m._state.get_tx("rk1").status == "CLAIMED"

    assert void(m, "rk1", evidence_log=log, at=1003, cancellation="decline:rk1",
                verify_cancellation=bound_to_this_tx).status == "VOIDED"
    assert log.records[-1].event == "VOID" and log.records[-1].reference == "decline:rk1"


def test_rail_idempotency_key_is_gate_scoped_deterministic_and_pinned():
    from mandata import rail_idempotency_key as key

    a = key("gate-A", "order-8812")
    assert a == key("gate-A", "order-8812")  # stable across retries
    assert a != key("gate-B", "order-8812")  # two gates, one rail namespace: no collision
    assert a == "775592f0ba01f12db46f3b0001c581ad8a8c8b2075d065512b377139ac505cbf"
    assert key("gate-A", "order-8812", hashed=False) == "6:gate-A|10:order-8812"
    assert key("a|b", "c") != key("a", "b|c")  # delimiter-safe (injective form)
    assert len(a) == 64
    with pytest.raises(ValueError):
        key("", "x")


def test_claim_registry_failure_leaves_authorized_and_claim_can_be_retried():
    class Flaky:
        def __init__(self):
            self.calls = 0

        def is_revoked(self, mandate_id):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("registry timed out")
            return False

    m, log, reg = mk(), EvidenceLog(), Flaky()
    enforce(m, "x", 10, currency="GBP", tx_id="rg1", evidence_log=log, at=1000)
    with pytest.raises(LifecycleError):
        claim(m, "rg1", "x", 10, "GBP", evidence_log=log, at=1001, registry=reg)
    assert log.records[-1].event == "REJECTED" and log.records[-1].check == "CLAIM:REGISTRY"
    assert m._state.get_tx("rg1").status == "AUTHORIZED"  # not consumed by the failure
    assert claim(m, "rg1", "x", 10, "GBP", evidence_log=log, at=1002, registry=reg).status == "CLAIMED"


def test_slow_http_registry_is_bounded_by_its_timeout_at_claim_and_enforce():
    import http.server
    import threading
    import time

    from mandata.revocation import HTTPRegistry

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(1.5)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"revoked": false}')

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        reg = HTTPRegistry(f"http://127.0.0.1:{srv.server_address[1]}", timeout=0.2)
        m, log = mk(), EvidenceLog()
        t0 = time.perf_counter()
        d = enforce(m, "x", 10, currency="GBP", tx_id="sl0", evidence_log=log, at=1000, registry=reg)
        assert not d.allowed and d.check == "REVOKED_REGISTRY"
        assert time.perf_counter() - t0 < 1.0  # bounded by the 0.2 s timeout, not the 1.5 s stall
        enforce(m, "x", 10, currency="GBP", tx_id="sl1", evidence_log=log, at=1000)
        t0 = time.perf_counter()
        with pytest.raises(LifecycleError):
            claim(m, "sl1", "x", 10, "GBP", evidence_log=log, at=1001, registry=reg)
        assert time.perf_counter() - t0 < 1.0
        assert log.records[-1].check == "CLAIM:REGISTRY"
        assert m._state.get_tx("sl1").status == "AUTHORIZED"
    finally:
        srv.shutdown()


def test_without_a_configured_budget_accumulated_spend_never_affects_decisions():
    from mandata.money import MAX_AMOUNT

    m = Mandate(principal="p", agent="a", bounds=Bounds(max_amount=MAX_AMOUNT))
    log = EvidenceLog()
    for i in range(3):  # cumulative authorized spend reaches 3 * (2^53 - 1)
        d = enforce(m, "x", MAX_AMOUNT, currency="GBP", tx_id=f"big{i}", evidence_log=log, at=1000 + i)
        assert d.allowed and d.check == "WITHIN"
    capped = Mandate(principal="p", agent="a", bounds=Bounds(max_amount=MAX_AMOUNT, total_budget=MAX_AMOUNT))
    assert enforce(capped, "x", MAX_AMOUNT, currency="GBP", tx_id="c0", evidence_log=log, at=1000).allowed
    d = enforce(capped, "x", 1, currency="GBP", tx_id="c1", evidence_log=log, at=1001)
    assert not d.allowed and d.check == "BUDGET"  # the bound is exact at the 2^53 - 1 edge
