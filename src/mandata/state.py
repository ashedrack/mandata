"""Persistent mandate state — velocity and spend tracking that survives restarts.

The enforcement gate is only as strong as its counters: if velocity limits or
spend caps live in process memory, restarting the agent resets them. StateStore
abstracts where that state lives.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Protocol


class StateStore(Protocol):
    """Where per-mandate transaction records live."""

    def record(self, mandate_id: str, timestamp: float, amount: float) -> None: ...
    def spend(self, mandate_id: str, since: float) -> tuple[int, float]:
        """Return (transaction_count, total_amount) since `since`."""
        ...


class InMemoryStore:
    """Default store — same process-memory semantics as v0.1, kept for parity/tests."""

    def __init__(self) -> None:
        self._records: dict[str, list[tuple[float, float]]] = {}
        self._lock = threading.Lock()

    def record(self, mandate_id: str, timestamp: float, amount: float) -> None:
        with self._lock:
            self._records.setdefault(mandate_id, []).append((timestamp, amount))

    def spend(self, mandate_id: str, since: float) -> tuple[int, float]:
        with self._lock:
            rows = [r for r in self._records.get(mandate_id, []) if r[0] >= since]
        return len(rows), sum(a for _, a in rows)


class SQLiteStore:
    """File-backed store — counters survive restarts and share across processes.

    Uses only the stdlib. Safe for concurrent use within a process; for
    multi-process deployments point every worker at the same file path.
    """

    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS spend (mandate_id TEXT, ts REAL, amount REAL)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_spend ON spend(mandate_id, ts)")
        self._conn.commit()
        self._lock = threading.Lock()

    def record(self, mandate_id: str, timestamp: float, amount: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO spend VALUES (?, ?, ?)",
                (mandate_id, timestamp, amount),
            )
            self._conn.commit()

    def spend(self, mandate_id: str, since: float) -> tuple[int, float]:
        cur = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM spend WHERE mandate_id = ? AND ts >= ?",
            (mandate_id, since),
        )
        count, total = cur.fetchone()
        return count, float(total)
