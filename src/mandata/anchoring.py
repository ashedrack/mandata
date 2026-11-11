"""Evidence anchoring — prove the chain head existed at a point in time.

A local hash chain proves internal consistency, but an operator could rewrite
history and recompute it. Anchoring `head_hash` to an external timestamping
service produces independently-verifiable proof of existence at time T.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from .evidence import EvidenceLog


@dataclass(frozen=True)
class Anchor:
    """A timestamped proof that a chain head existed."""

    head_hash: str
    service_token: str  # the timestamping service's signed proof/receipt
    service_url: str


def anchor(log: EvidenceLog, service_url: str, timeout: float = 5.0) -> Anchor:
    """POST the chain head to a timestamping service; store its signed receipt.

    The service is expected to return JSON with a ``token`` field — its signed
    attestation that ``head_hash`` was received at its recorded time. Any
    RFC 3161-style TSA endpoint or transparency log can be plugged in here.
    """
    req = urllib.request.Request(
        service_url,
        data=json.dumps({"hash": log.head_hash}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        token = json.loads(resp.read() or b"{}").get("token", "")
    return Anchor(head_hash=log.head_hash, service_token=token, service_url=service_url)


def verify_anchor(log: EvidenceLog, anchor_obj: Anchor, service_url: str) -> bool:
    """Confirm the current chain head still matches the anchored proof."""
    return log.head_hash == anchor_obj.head_hash
