"""Core mandate model: principal -> agent -> credential -> transaction -> evidence."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Bounds:
    """The permitted envelope an agent must transact within."""

    max_amount: float
    currency: str = "GBP"
    allowed_payees: tuple[str, ...] | list[str] = ()
    expires_in_days: int = 30
    velocity_limit: int = 0  # max transactions per day; 0 = unlimited
    total_budget: float | None = None  # cumulative spend cap over window
    budget_window_days: int = 30  # window for total_budget


@dataclass
class Mandate:
    """A principal's grant of bounded spending authority to an agent."""

    principal: str
    agent: str
    bounds: Bounds
    mandate_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    issued_at: float = field(default_factory=time.time)
    revoked: bool = False
    parent_mandate_id: str | None = None
    _state: object = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self._state is None:
            from .state import InMemoryStore

            self._state = InMemoryStore()

    @property
    def expires_at(self) -> float:
        return self.issued_at + self.bounds.expires_in_days * 86400

    def is_expired(self, at: float | None = None) -> bool:
        return (at or time.time()) > self.expires_at

    def revoke(self) -> None:
        self.revoked = True

    def record(self, at: float, amount: float = 0.0) -> None:
        self._state.record(self.mandate_id, at, amount)

    def velocity_used_today(self, at: float) -> int:
        day_start = int(at // 86400) * 86400
        count, _ = self._state.spend(self.mandate_id, day_start)
        return count

    def spend_in_window(self, at: float) -> tuple[int, float]:
        since = at - self.bounds.budget_window_days * 86400
        return self._state.spend(self.mandate_id, since)


@dataclass(frozen=True)
class Decision:
    """The outcome of a mandate gate check."""

    allowed: bool
    reason: str
    evidence: EvidenceRecord


@dataclass(frozen=True)
class EvidenceRecord:
    """An auditable record of a single enforcement decision."""

    mandate_id: str
    agent: str
    payee: str
    amount: float
    allowed: bool
    reason: str
    timestamp: float
    prev_hash: str

    @property
    def hash(self) -> str:
        payload = (
            f"{self.mandate_id}|{self.agent}|{self.payee}|{self.amount}|"
            f"{self.allowed}|{self.reason}|{self.timestamp}|{self.prev_hash}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()
