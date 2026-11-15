"""Hash-chained evidence log — every gate decision and lifecycle event is appended and linked.

Integrity comes from the hash chain; authenticity from an optional gate
signing key (each record's hash is signed with Ed25519); protection against
truncation and wholesale rewriting from external anchoring (``anchoring.py``).
"""

from __future__ import annotations

import dataclasses
import threading
import time

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from .models import EVENTS, EvidenceRecord


class EvidenceLog:
    """Append-only chain of enforcement records; each record hashes its predecessor.

    Pass ``signing_key`` (the gate's Ed25519 key) to sign every record;
    ``verify_chain(public_key=...)`` then also proves each record was written
    by that gate.
    """

    GENESIS = "0" * 64

    def __init__(self, signing_key: SigningKey | None = None) -> None:
        self.records: list[EvidenceRecord] = []
        self._lock = threading.Lock()
        self._signing_key = signing_key

    @property
    def public_key(self) -> str | None:
        """Hex verify key of the gate signing key, if records are signed."""
        return self._signing_key.verify_key.encode().hex() if self._signing_key else None

    @property
    def head_hash(self) -> str:
        return self.records[-1].hash if self.records else self.GENESIS

    def append(
        self,
        mandate_id: str,
        agent: str,
        payee: str,
        amount: int | None,
        allowed: bool,
        check: str,
        timestamp: float | None = None,
        detail: str = "",
        tx_id: str = "",
        event: str = "DECISION",
    ) -> EvidenceRecord:
        if event not in EVENTS:
            raise ValueError(f"unknown evidence event {event!r}")
        with self._lock:
            record = EvidenceRecord(
                mandate_id=mandate_id,
                agent=agent,
                payee=payee,
                amount=amount,
                allowed=allowed,
                check=check,
                timestamp=time.time() if timestamp is None else timestamp,
                prev_hash=self.head_hash,
                detail=detail,
                tx_id=tx_id,
                event=event,
            )
            if self._signing_key is not None:
                sig = self._signing_key.sign(record.hash.encode()).signature.hex()
                record = dataclasses.replace(record, signature=sig)
            self.records.append(record)
        return record

    def verify_chain(self, anchored_head: str | None = None, public_key: str | None = None) -> bool:
        """Recompute every record hash and check each link.

        Detects modification, insertion, deletion, or reordering of any record
        that has a successor. On its own a hash chain cannot detect removal of
        the most recent records (tail truncation) or an edit to the final
        record; pass ``anchored_head`` — a head hash previously published to
        an external anchor — to also require that the chain still contains
        that point, which detects truncation or rewriting up to the anchor.

        Pass ``public_key`` (the gate's hex verify key) to also require that
        every record carries a valid signature by that key (authenticity).
        """
        verifier = VerifyKey(bytes.fromhex(public_key)) if public_key else None
        prev = self.GENESIS
        seen_anchor = anchored_head is None or anchored_head == self.GENESIS
        for rec in self.records:
            if rec.prev_hash != prev:
                return False
            prev = rec.hash
            if verifier is not None:
                try:
                    verifier.verify(prev.encode(), bytes.fromhex(rec.signature))
                except (BadSignatureError, ValueError, TypeError):
                    return False
            if prev == anchored_head:
                seen_anchor = True
        return seen_anchor
