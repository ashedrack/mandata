"""Credential signing — bind a mandate cryptographically to an agent identity.

A credential is the portable, verifiable form of a mandate: the principal's
key signs (mandate_id, agent, bounds_hash, expiry). Anyone can verify that a
transaction was proposed under a mandate the principal actually issued.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from .models import Mandate, canonical_number, lp_fields


def bounds_hash(mandate: Mandate) -> str:
    """Canonical hash over every authority-bearing field of the mandate.

    Covers all bounds plus principal, issuance time and delegation link, so no
    field that widens or redirects authority can change without breaking the
    signature (MMF §6).
    """
    b = mandate.bounds
    # Length-prefixed fields (same canonical form as evidence, MMF §5.6):
    # unambiguous for any field value, byte-identical across languages.
    payload = lp_fields(
        mandate.principal,
        canonical_number(mandate.issued_at),
        mandate.parent_mandate_id or "",
        str(b.max_amount),
        b.currency,
        ",".join(sorted(b.allowed_payees)),
        str(b.expires_in_days),
        str(b.velocity_limit),
        "" if b.total_budget is None else str(b.total_budget),
        str(b.budget_window_days),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Credential:
    """A signed binding: principal's key vouches for (mandate, agent, bounds, expiry)."""

    mandate_id: str
    agent: str
    bounds_hash: str
    expires_at: float
    signature: str  # hex
    public_key: str  # hex — principal's verify key

    def payload(self) -> bytes:
        return _payload(self.mandate_id, self.agent, self.bounds_hash, self.expires_at)


def _payload(mandate_id: str, agent: str, bh: str, expires_at: float) -> bytes:
    return lp_fields(mandate_id, agent, bh, canonical_number(expires_at)).encode()


def issue_credential(mandate: Mandate, signing_key: SigningKey) -> Credential:
    """Principal signs a credential for a mandate + agent."""
    bh = bounds_hash(mandate)
    expires_at = mandate.expires_at
    signature = signing_key.sign(_payload(mandate.mandate_id, mandate.agent, bh, expires_at))
    return Credential(
        mandate_id=mandate.mandate_id,
        agent=mandate.agent,
        bounds_hash=bh,
        expires_at=expires_at,
        signature=signature.signature.hex(),
        public_key=signing_key.verify_key.encode().hex(),
    )


def verify_credential(
    credential: Credential,
    mandate: Mandate,
    expected_public_key: str,
    at: float | None = None,
) -> tuple[bool, str]:
    """Verify a credential against a mandate + the principal's known public key.

    Checks: signature validity, key match, mandate/agent binding, bounds integrity,
    expiry. Returns (ok, reason).
    """
    at = time.time() if at is None else at

    if credential.public_key != expected_public_key:
        return False, "credential signed by unexpected key"
    if credential.mandate_id != mandate.mandate_id:
        return False, "credential is for a different mandate"
    if credential.agent != mandate.agent:
        return False, "credential is for a different agent"
    if credential.bounds_hash != bounds_hash(mandate):
        return False, "bounds altered since credential issued"
    if at > credential.expires_at:
        return False, "credential expired"
    try:
        VerifyKey(bytes.fromhex(credential.public_key)).verify(
            credential.payload(), bytes.fromhex(credential.signature)
        )
    except (BadSignatureError, ValueError, TypeError):
        return False, "invalid signature"
    return True, "credential valid"
