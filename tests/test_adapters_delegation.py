import pytest

from mandata import Bounds, EvidenceLog, Mandate, delegate, enforce
from mandata.adapters import ap2, x402


def test_x402_adapter_builds_mandate():
    req = {"maxAmountRequired": "50", "asset": "usdc", "payTo": "merchant.example"}
    m = x402.from_payment_requirements(req, "u", "a")
    assert m.bounds.max_amount == 50.0
    assert m.bounds.currency == "USDC"
    assert m.bounds.allowed_payees == ["merchant.example"]


def test_ap2_adapter_builds_mandate():
    intent = {
        "mandate_details": {
            "max_amount": 200,
            "currency": "gbp",
            "allowed_merchants": ["tesco"],
            "expires_in_days": 60,
        }
    }
    m = ap2.from_intent_mandate(intent, "u", "a")
    assert m.bounds.max_amount == 200.0
    assert m.bounds.allowed_payees == ["tesco"]


def test_delegation_stricter_child_allowed():
    parent = Mandate(
        principal="u", agent="a", bounds=Bounds(max_amount=100, allowed_payees=["tesco"])
    )
    child = delegate(parent, "a-sub", Bounds(max_amount=50, allowed_payees=["tesco"]))
    assert child.parent_mandate_id == parent.mandate_id
    assert enforce(child, "tesco", 40.0, evidence_log=EvidenceLog()).allowed


def test_delegation_rejects_looser_bounds():
    parent = Mandate(principal="u", agent="a", bounds=Bounds(max_amount=100))
    with pytest.raises(ValueError, match="widens scope"):
        delegate(parent, "a-sub", Bounds(max_amount=500))
