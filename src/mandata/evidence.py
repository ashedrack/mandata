"""Hash-chained evidence log — every enforcement decision is appended and linked."""

from __future__ import annotations

import time

from .models import EvidenceRecord


class EvidenceLog:
    """Append-only chain of enforcement records; each record hashes its predecessor."""

    GENESIS = "0" * 64

    def __init__(self) -> None:
        self.records: list[EvidenceRecord] = []

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
        reason: str,
        timestamp: float | None = None,
    ) -> EvidenceRecord:
        record = EvidenceRecord(
            mandate_id=mandate_id,
            agent=agent,
            payee=payee,
            amount=amount,
            allowed=allowed,
            reason=reason,
            timestamp=timestamp or time.time(),
            prev_hash=self.head_hash,
        )
        self.records.append(record)
        return record

    def verify_chain(self) -> bool:
        prev = self.GENESIS
        for rec in self.records:
            if rec.prev_hash != prev:
                return False
            prev = rec.hash
        return True
