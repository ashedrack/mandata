"""MMF-v0.1 conformance — run spec/vectors/core.json against the Python gate.

Any MMF implementation must produce the same `allowed` + `check` results.
"""

import json
from pathlib import Path

import pytest

from mandata import Bounds, EvidenceLog, Mandate, enforce

VECTORS = json.loads((Path(__file__).parent.parent / "spec" / "vectors" / "core.json").read_text())[
    "vectors"
]

CHECK_MAP = [
    ("revoked (registry)", "REVOKED_REGISTRY"),
    ("revoked", "REVOKED"),
    ("expired", "EXPIRED"),
    ("non-positive", "NON_POSITIVE"),
    ("exceeds cap", "CAP_EXCEEDED"),
    ("outside mandate", "CURRENCY"),
    ("not in allowed set", "PAYEE"),
    ("velocity", "VELOCITY"),
    ("budget", "BUDGET"),
]


def check_id(d) -> str:
    if d.allowed:
        return "WITHIN"
    for frag, cid in CHECK_MAP:
        if frag in d.reason:
            return cid
    raise AssertionError(f"unmapped reason: {d.reason}")


def build_mandate(spec: dict) -> Mandate:
    b = spec.get("bounds", {})
    bounds = Bounds(
        max_amount=b.get("max_amount", 1e9),
        currency=b.get("currency", "GBP"),
        allowed_payees=b.get("allowed_payees", []),
        expires_in_days=b.get("expires_in_days", 30),
        velocity_limit=b.get("velocity_limit", 0),
        total_budget=b.get("total_budget"),
        budget_window_days=b.get("budget_window_days", 30),
    )
    m = Mandate(
        principal=spec.get("principal", "u"),
        agent=spec.get("agent", "a"),
        bounds=bounds,
        issued_at=spec.get("issued_at", 0),
    )
    if spec.get("revoked"):
        m.revoke()
    return m


@pytest.mark.parametrize("v", VECTORS, ids=[v["id"] for v in VECTORS])
def test_vector(v):
    m = build_mandate(v["mandate"])
    log = EvidenceLog()
    for p in v.get("prior_txns", []):  # seed prior allowed spend
        m._state.record(m.mandate_id, p["at"], p["amount"])

    tx = v["tx"]
    d = enforce(m, tx["payee"], tx["amount"], tx.get("currency"), at=tx.get("at"), evidence_log=log)
    assert d.allowed == v["expect"]["allowed"], f"{v['id']}: {d.reason}"
    assert check_id(d) == v["expect"]["check"], f"{v['id']}: {d.reason}"

    if "then_tx" in v:  # second evaluation on same mandate (state carried)
        t2 = v["then_tx"]
        d2 = enforce(
            m, t2["payee"], t2["amount"], t2.get("currency"), at=t2.get("at"), evidence_log=log
        )
        assert d2.allowed == v["then_expect"]["allowed"], f"{v['id']} then: {d2.reason}"
        assert check_id(d2) == v["then_expect"]["check"]
