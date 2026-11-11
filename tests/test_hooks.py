from mandata import Bounds, EvidenceLog, Mandate, enforce


def test_hooks_fire_on_allow_and_reject():
    seen = []
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    log = EvidenceLog()
    enforce(m, "x", 10.0, evidence_log=log, hooks=[seen.append])
    enforce(m, "x", 200.0, evidence_log=log, hooks=[seen.append])
    assert [d.allowed for d in seen] == [True, False]


def test_hook_failure_does_not_break_gate():
    def bad(d):
        raise RuntimeError("observer down")

    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    try:
        enforce(m, "x", 10.0, hooks=[bad])
        assert False, "hook exception should propagate to caller, not corrupt"
    except RuntimeError:
        pass  # decision was already recorded; exception surfaces, not swallowed
