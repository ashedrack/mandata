import time

from nacl.signing import SigningKey

from mandata import Bounds, Mandate
from mandata.credentials import issue_credential, verify_credential


def make():
    key = SigningKey.generate()
    m = Mandate(
        principal="user:alice",
        agent="agent:bot",
        bounds=Bounds(max_amount=500.0, currency="GBP", allowed_payees=["store.example"]),
    )
    return key, m, issue_credential(m, key)


def test_valid_credential_verifies():
    key, m, cred = make()
    ok, reason = verify_credential(cred, m, key.verify_key.encode().hex())
    assert ok and reason == "credential valid"


def test_rejects_forged_signature():
    key, m, _cred = make()
    other = SigningKey.generate()
    forged = issue_credential(m, other)  # signed by a different key
    ok, reason = verify_credential(forged, m, key.verify_key.encode().hex())
    assert not ok and "unexpected key" in reason


def test_rejects_wrong_agent():
    key, m, cred = make()
    m2 = Mandate(
        principal="user:alice",
        agent="agent:evil",
        bounds=m.bounds,
        mandate_id=m.mandate_id,
        issued_at=m.issued_at,
    )
    ok, reason = verify_credential(cred, m2, key.verify_key.encode().hex())
    assert not ok and "different agent" in reason


def test_rejects_altered_bounds():
    key, m, cred = make()
    tampered = Mandate(
        principal="user:alice",
        agent="agent:bot",
        bounds=Bounds(max_amount=999999.0, currency="GBP", allowed_payees=["store.example"]),
        mandate_id=m.mandate_id,
        issued_at=m.issued_at,
    )
    ok, reason = verify_credential(cred, tampered, key.verify_key.encode().hex())
    assert not ok and "bounds altered" in reason


def test_rejects_expired():
    key, m, cred = make()
    future = time.time() + 31 * 86400
    ok, reason = verify_credential(cred, m, key.verify_key.encode().hex(), at=future)
    assert not ok and "expired" in reason
