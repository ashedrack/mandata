"""Hash-chained evidence log — every enforcement decision is appended and linked."""

from __future__ import annotations

import threading
import time

from .models import EvidenceRecord


class EvidenceLog:
    """Append-only chain of enforcement records; each record hashes its predecessor."""

    GENESIS = "0" * 64

    def __init__(self) -> None:
        self.records: list[EvidenceRecord] = []
        self._lock = threading.Lock()

    @property
    def head_hash(self) -> str:
        return self.records[-1].hash if self.records else self.GENESIS

    def append(
        self,
        mandate_id: str,
        agent: str,
        payee: str,
        amount: float,
        allowed: bool,
        check: str,
        timestamp: float | None = None,
        detail: str = "",
    ) -> EvidenceRecord:
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
            )
            self.records.append(record)
        return record

    def verify_chain(self, anchored_head: str | None = None) -> bool:
        """Recompute every record hash and check each link.

        Detects modification, insertion, deletion, or reordering of any record
        that has a successor. On its own a hash chain cannot detect removal of
        the most recent records (tail truncation) or an edit to the final
        record; pass ``anchored_head`` — a head hash previously published to
        an external anchor — to also require that the chain still contains
        that point, which detects truncation or rewriting up to the anchor.
        """
        prev = self.GENESIS
        seen_anchor = anchored_head is None or anchored_head == self.GENESIS
        for rec in self.records:
            if rec.prev_hash != prev:
                return False
            prev = rec.hash
            if prev == anchored_head:
                seen_anchor = True
        return seen_anchor
