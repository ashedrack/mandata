"""MMF conformance — run spec/vectors/*.json against the Python gate.

Any MMF implementation must produce the same `allowed` + `check` results.
"""

import json
from pathlib import Path

import pytest

from mandata import (
    Bounds,
    EvidenceLog,
    InMemoryRegistry,
    LifecycleError,
    Mandate,
    claim,
    delegate,
    enforce,
    settle,
    void,
)
from mandata.models import EvidenceRecord

SPEC = Path(__file__).parent.parent / "spec" / "vectors"
CORE = json.loads((SPEC / "core.json").read_text())["vectors"]
DELEGATION = json.loads((SPEC / "delegation.json").read_text())["vectors"]
LIFECYCLE = json.loads((SPEC / "lifecycle.json").read_text())["vectors"]
EVIDENCE = json.loads((SPEC / "evidence.json").read_text())["vectors"]
CREDENTIAL = json.loads((SPEC / "credential.json").read_text())


class UnavailableRegistry:
    def is_revoked(self, mandate_id: str) -> bool:
        raise ConnectionError("registry unreachable")


def build_bounds(b: dict) -> Bounds:
    return Bounds(
        max_amount=b.get("max_amount", 10**9),
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
    # "amount_json" carries the amount as raw JSON text, so lexical forms such
    # as 1.0, 1e0 and -0 reach the gate exactly as a wire parser would yield them.
    amount = json.loads(tx["amount_json"]) if "amount_json" in tx else tx["amount"]
    return enforce(
        m,
        tx["payee"],
        amount,
        tx.get("currency"),
        at=tx["at"],
        evidence_log=log,
        registry=registry,
        tx_id=tx.get("tx_id"),
    )


@pytest.mark.parametrize("v", CORE, ids=[v["id"] for v in CORE])
def test_core_vector(v):
    m = build_mandate(v["mandate"])
    registry = build_registry(v.get("registry"))
    log = EvidenceLog()
    for p in v.get("prior_txns", []):  # seed prior completed (resolved) spend
        m.record(p["at"], p["amount"], tx_id=f"prior-{p['at']}")
        m._state.resolve(m.mandate_id, f"prior-{p['at']}")

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
        elif "claim" in step:
            c = step["claim"]
            claim(mandates[c["as"]], c["tx_id"], c["payee"], c["amount"], c["currency"],
                  evidence_log=log, at=step["at"])
        elif "settle" in step:
            c = step["settle"]
            settle(mandates[c["as"]], c["tx_id"], evidence_log=log, at=step["at"])
        else:
            tx = step["tx"]
            d = run(mandates[tx["as"]], tx, log)
            expect = step["expect"]
            assert (d.allowed, d.check) == (expect["allowed"], expect["check"]), d.reason
    assert log.verify_chain()


@pytest.mark.parametrize("v", LIFECYCLE, ids=[v["id"] for v in LIFECYCLE])
def test_lifecycle_vector(v):
    m = build_mandate(v["mandate"])
    mandates = {"m": m}
    if "mandate2" in v:  # a second mandate sharing the gate-wide ledger (MMF §5.3)
        mandates["m2"] = build_mandate(v["mandate2"])
        mandates["m2"]._state = m._state
    registry = build_registry(v.get("registry"))
    log = EvidenceLog()
    for step in v["steps"]:
        expect = step["expect"]
        if "tx" in step:
            d = run(mandates[step["tx"].get("as", "m")], step["tx"], log)
            got = {
                "allowed": d.allowed,
                "check": d.check,
                "replayed": d.replayed,
                "status": d.status,
            }
            checks = {k: val for k, val in expect.items() if k != "record"}
            assert {k: got[k] for k in checks} == checks, d.reason
            for k, val in expect.get("record", {}).items():
                assert getattr(log.records[-1], k) == val, f"{v['id']}: record.{k}"
            continue
        before = len(log.records)
        try:
            if "claim" in step:
                c = step["claim"]
                tx = claim(
                    m,
                    c["tx_id"],
                    c["payee"],
                    c["amount"],
                    c["currency"],
                    evidence_log=log,
                    at=step["at"],
                    registry=registry,
                )
            elif "settle" in step:
                tx = settle(m, step["settle"], evidence_log=log, at=step["at"])
            else:
                tx = void(
                    m,
                    step["void"],
                    evidence_log=log,
                    at=step["at"],
                    cancellation=step.get("cancellation"),
                )
        except LifecycleError:
            assert expect.get("error"), f"{v['id']}: unexpected rejection"
            # rejected transitions append exactly one REJECTED evidence record
            assert len(log.records) == before + 1
            rec = log.records[-1]
            assert rec.event == "REJECTED"
            if "check" in expect:  # normative <OP>:<CAUSE> and precedence (MMF §5.6)
                assert rec.check == expect["check"]
            for k, val in expect.get("record", {}).items():  # attempted-instruction fields
                assert getattr(rec, k) == val, f"{v['id']}: record.{k}"
        else:
            assert not expect.get("error"), f"{v['id']}: expected rejection"
            assert tx.status == expect["status"]
    assert [r.event for r in log.records] == v["events"]
    assert log.verify_chain()


@pytest.mark.parametrize("v", EVIDENCE, ids=[v["id"] for v in EVIDENCE])
def test_evidence_hash_vector(v):
    """Canonical serialization + SHA-256 must match byte-for-byte (MMF §5.2)."""
    prev = "0" * 64
    for r, expected in zip(v["records"], v["expect"], strict=True):
        rec = EvidenceRecord(prev_hash=prev, **r)
        assert rec.canonical_payload() == expected["payload"]
        assert rec.hash == expected["hash"]
        prev = rec.hash
    assert prev == v["head"]


@pytest.mark.parametrize("v", CREDENTIAL, ids=[v["id"] for v in CREDENTIAL])
def test_credential_signature_vector(v):
    """Golden credential vectors: canonical bytes, bounds_hash and the
    deterministic Ed25519 signature must reproduce exactly for C01, and every
    negative credential (C02+) MUST fail verification (MMF §7)."""
    from nacl.signing import SigningKey

    from mandata.credentials import (
        Credential,
        _payload,
        bounds_hash,
        verify_credential,
    )
    from mandata.models import Bounds, Mandate, canonical_number, lp_fields

    i = v.get("inputs") or v["mandate_inputs"]
    b = i["bounds"]
    bounds = Bounds(
        max_amount=b["max_amount"], currency=b["currency"],
        allowed_payees=b["allowed_payees"],
        expires_in_days=b["expires_in_days"],
        velocity_limit=b["velocity_limit"],
        total_budget=b["total_budget"],
        budget_window_days=b["budget_window_days"],
    )
    m = Mandate(
        principal=i["principal"], agent=i["agent"], bounds=bounds,
        mandate_id=i["mandate_id"], issued_at=i["issued_at"],
        parent_mandate_id=i["parent_mandate_id"] or None,
    )
    bounds_payload = lp_fields(
        m.principal, canonical_number(m.issued_at), m.parent_mandate_id or "",
        str(bounds.max_amount), bounds.currency,
        ",".join(sorted(bounds.allowed_payees)),
        str(bounds.expires_in_days), str(bounds.velocity_limit),
        "" if bounds.total_budget is None else str(bounds.total_budget),
        str(bounds.budget_window_days),
    ).encode()
    exp = v["expected"]
    if "credential" in v:  # negative vectors: verification MUST fail as specified
        cred = Credential(**v["credential"])
        ok, _reason = verify_credential(
            cred, m, v["principal_public_key_hex"], at=m.issued_at + 100
        )
        assert ok is exp["valid"], f"{v['id']}: expected valid={exp['valid']}"
        return
    assert bounds_payload.hex() == exp["bounds_payload_hex"]
    assert bounds_hash(m) == exp["bounds_hash"]
    payload = _payload(m.mandate_id, m.agent, exp["bounds_hash"], m.expires_at)
    assert payload.hex() == exp["payload_hex"]
    sk = SigningKey(bytes.fromhex(v["seed_hex"]))
    assert sk.verify_key.encode().hex() == v["public_key_hex"]
    assert sk.sign(payload).signature.hex() == exp["signature"]
