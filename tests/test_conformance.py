"""MMF conformance — run spec/vectors/*.json against the Python gate.

Any MMF implementation must produce the same `allowed` + `check` results.
"""

import json
from pathlib import Path

import pytest

from mandata import Bounds, EvidenceLog, InMemoryRegistry, Mandate, delegate, enforce

SPEC = Path(__file__).parent.parent / "spec" / "vectors"
CORE = json.loads((SPEC / "core.json").read_text())["vectors"]
DELEGATION = json.loads((SPEC / "delegation.json").read_text())["vectors"]


class UnavailableRegistry:
    def is_revoked(self, mandate_id: str) -> bool:
        raise ConnectionError("registry unreachable")


def build_bounds(b: dict) -> Bounds:
    return Bounds(
        max_amount=b.get("max_amount", 1e9),
        currency=b.get("currency", "GBP"),
        allowed_payees=b.get("allowed_payees", []),
        expires_in_days=b.get("expires_in_days", 30),
        velocity_limit=b.get("velocity_limit", 0),
        total_budget=b.get("total_budget"),
        budget_window_days=b.get("budget_window_days", 30),
    )


def build_mandate(spec: dict) -> Mandate:
    m = Mandate(
        principal=spec.get("principal", "u"),
        agent=spec.get("agent", "a"),
        bounds=build_bounds(spec.get("bounds", {})),
        issued_at=spec.get("issued_at", 0),
    )
    if "mandate_id" in spec:
        m.mandate_id = spec["mandate_id"]
    if spec.get("revoked"):
        m.revoke()
    return m


def build_registry(spec: dict | None):
    if spec is None:
        return None
    if spec.get("unavailable"):
        return UnavailableRegistry()
    reg = InMemoryRegistry()
    for mid in spec.get("revoked", []):
        reg.revoke(mid)
    return reg


def run(m, tx, log, registry=None):
    return enforce(
        m,
        tx["payee"],
        tx["amount"],
        tx.get("currency"),
        at=tx["at"],
        evidence_log=log,
        registry=registry,
    )


@pytest.mark.parametrize("v", CORE, ids=[v["id"] for v in CORE])
def test_core_vector(v):
    m = build_mandate(v["mandate"])
    registry = build_registry(v.get("registry"))
    log = EvidenceLog()
    for p in v.get("prior_txns", []):  # seed prior allowed spend
        m._state.record(m.mandate_id, p["at"], p["amount"])

    d = run(m, v["tx"], log, registry)
    assert (d.allowed, d.check) == (v["expect"]["allowed"], v["expect"]["check"]), d.reason
    assert d.evidence.check == d.check

    if "then_tx" in v:  # second evaluation on same mandate (state carried)
        d2 = run(m, v["then_tx"], log, registry)
        expect = v["then_expect"]
        assert (d2.allowed, d2.check) == (expect["allowed"], expect["check"]), d2.reason

    assert len(log.records) == (2 if "then_tx" in v else 1)  # denials recorded too
    assert log.verify_chain()


@pytest.mark.parametrize("v", DELEGATION, ids=[v["id"] for v in DELEGATION])
def test_delegation_vector(v):
    mandates = {"parent": build_mandate({"bounds": v["parent"], "issued_at": 0})}
    log = EvidenceLog()
    for step in v["steps"]:
        if "revoke" in step:
            mandates[step["revoke"]].revoke()
        elif "delegate" in step:
            source = mandates[step.get("from", "parent")]
            bounds = build_bounds(step["bounds"])
            if step["expect"] == "invalid":
                with pytest.raises(ValueError):
                    delegate(source, step["delegate"], bounds, at=1000)
            else:
                child = delegate(source, step["delegate"], bounds, at=1000)
                assert child.parent_mandate_id == source.mandate_id
                mandates[step["delegate"]] = child
        else:
            tx = step["tx"]
            d = run(mandates[tx["as"]], tx, log)
            expect = step["expect"]
            assert (d.allowed, d.check) == (expect["allowed"], expect["check"]), d.reason
    assert log.verify_chain()
