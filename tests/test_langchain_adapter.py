import pytest

from mandata import Bounds, EvidenceLog, Mandate
from mandata.adapters.langchain import guard


def test_guard_allows_within_bounds():
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100, allowed_payees=["tesco"]))
    calls = []

    def pay(payee: str, amount: float) -> str:
        calls.append((payee, amount))
        return f"settled {amount} to {payee}"

    safe = guard(m, pay, evidence_log=EvidenceLog())
    assert safe(payee="tesco", amount=50.0) == "settled 50.0 to tesco"
    assert calls == [("tesco", 50.0)]


def test_guard_denies_out_of_bounds_and_never_calls_tool():
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    calls = []

    def pay(payee: str, amount: float) -> str:
        calls.append(payee)
        return "settled"

    out = guard(m, pay, evidence_log=EvidenceLog())(payee="x", amount=500.0)
    assert out["allowed"] is False
    assert "exceeds cap" in out["denied"]
    assert calls == []  # the tool NEVER ran


def test_guard_denial_written_to_evidence():
    log = EvidenceLog()
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=10))
    guard(m, lambda payee, amount: "x", evidence_log=log)(payee="x", amount=99)
    assert len(log.records) == 1
    assert log.records[0].allowed is False


def test_guard_positional_args():
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    safe = guard(m, lambda payee, amount: "ok", evidence_log=EvidenceLog())
    assert safe("x", 10) == "ok"


def test_guard_raise_on_deny():
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=10))
    safe = guard(m, lambda payee, amount: "x", raise_on_deny=True)
    with pytest.raises(PermissionError, match="mandate denied"):
        safe(payee="x", amount=99)


def test_guard_missing_args_raises_typeerror():
    m = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    safe = guard(m, lambda recipient, total: "x")
    with pytest.raises(TypeError, match="could not extract"):
        safe(recipient="x", total=10)
