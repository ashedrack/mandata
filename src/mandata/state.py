"""Persistent mandate state — spend tracking and the transaction ledger.

The enforcement gate is only as strong as its counters: if velocity limits or
spend caps live in process memory, restarting the agent resets them. StateStore
abstracts where that state lives. It holds two things:

- spend rows ``(mandate_id, ts, amount, tx_id)`` — one per mandate in the
  chain for every authorized transaction; voiding releases them;
- the transaction ledger ``tx_id -> TxRecord`` — what the gate decided for
  each transaction identifier, and its settlement status (MMF §5.3–5.4).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Settlement lifecycle of a transaction (MMF §5.4).
AUTHORIZED, SETTLED, VOIDED, DENIED = "AUTHORIZED", "SETTLED", "VOIDED", "DENIED"


@dataclass(frozen=True)
class TxRecord:
    tx_id: str
    mandate_id: str
    payee: str
    amount: int | None
    currency: str | None
    allowed: bool
    check: str
    status: str

    def same_request(self, mandate_id: str, payee: str, amount, currency) -> bool:
        return (self.mandate_id, self.payee, self.amount, self.currency) == (
            mandate_id,
            payee,
            amount,
            currency,
        )


class StateStore(Protocol):
    """Where spend rows and the transaction ledger live."""

    def record(self, mandate_id: str, timestamp: float, amount: int, tx_id: str) -> None: ...
    def release(self, mandate_id: str, tx_id: str) -> None: ...
    def spend(self, mandate_id: str, since: float) -> tuple[int, int]:
        """Return (transaction_count, total_amount) since `since`."""
        ...

    def get_tx(self, tx_id: str) -> TxRecord | None: ...
    def put_tx(self, tx: TxRecord) -> None: ...
    def atomic(self):
        """Context manager making check-and-record serializable for this store."""
        ...


class InMemoryStore:
    """Process-memory store. Atomicity comes from the gate's chain locks."""

    def __init__(self) -> None:
        self._records: dict[str, list[tuple[float, int, str]]] = {}
        self._txs: dict[str, TxRecord] = {}
        self._lock = threading.RLock()

    @contextmanager
    def atomic(self) -> Iterator[None]:
        with self._lock:
            yield

    def record(self, mandate_id: str, timestamp: float, amount: int, tx_id: str) -> None:
        with self._lock:
            self._records.setdefault(mandate_id, []).append((timestamp, amount, tx_id))

    def release(self, mandate_id: str, tx_id: str) -> None:
        with self._lock:
            rows = self._records.get(mandate_id, [])
            self._records[mandate_id] = [r for r in rows if r[2] != tx_id]

    def spend(self, mandate_id: str, since: float) -> tuple[int, int]:
        with self._lock:
            rows = [r for r in self._records.get(mandate_id, []) if r[0] >= since]
        return len(rows), sum(a for _, a, _ in rows)

    def get_tx(self, tx_id: str) -> TxRecord | None:
        with self._lock:
            return self._txs.get(tx_id)

    def put_tx(self, tx: TxRecord) -> None:
        with self._lock:
            self._txs[tx.tx_id] = tx


class SQLiteStore:
    """File-backed store — counters and ledger survive restarts and are shared
    across processes.

    ``atomic()`` opens a ``BEGIN IMMEDIATE`` transaction, which SQLite
    serializes across every process using the same file, so concurrent gates
    in different processes cannot jointly exceed a bound.
    """

    def __init__(self, path: str | Path, timeout: float = 30.0) -> None:
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None, timeout=timeout
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS spend "
            "(mandate_id TEXT, ts REAL, amount INTEGER, tx_id TEXT)"
        )
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(spend)")}
        if "tx_id" not in cols:  # migrate a pre-0.4 store
            self._conn.execute("ALTER TABLE spend ADD COLUMN tx_id TEXT")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_spend ON spend(mandate_id, ts)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS txs (tx_id TEXT PRIMARY KEY, mandate_id TEXT, "
            "payee TEXT, amount INTEGER, currency TEXT, allowed INTEGER, check_id TEXT, "
            "status TEXT)"
        )
        self._lock = threading.RLock()
        self._depth = 0

    @contextmanager
    def atomic(self) -> Iterator[None]:
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                self._conn.execute("COMMIT")

    def record(self, mandate_id: str, timestamp: float, amount: int, tx_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO spend VALUES (?, ?, ?, ?)", (mandate_id, timestamp, amount, tx_id)
            )

    def release(self, mandate_id: str, tx_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM spend WHERE mandate_id = ? AND tx_id = ?", (mandate_id, tx_id)
            )

    def spend(self, mandate_id: str, since: float) -> tuple[int, int]:
        with self._lock:
            count, total = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM spend "
                "WHERE mandate_id = ? AND ts >= ?",
                (mandate_id, since),
            ).fetchone()
        return count, int(total)

    def get_tx(self, tx_id: str) -> TxRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tx_id, mandate_id, payee, amount, currency, allowed, check_id, status "
                "FROM txs WHERE tx_id = ?",
                (tx_id,),
            ).fetchone()
        if row is None:
            return None
        return TxRecord(*row[:5], bool(row[5]), row[6], row[7])

    def put_tx(self, tx: TxRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO txs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tx.tx_id,
                    tx.mandate_id,
                    tx.payee,
                    tx.amount,
                    tx.currency,
                    int(tx.allowed),
                    tx.check,
                    tx.status,
                ),
            )


__all__ = [
    "AUTHORIZED",
    "DENIED",
    "SETTLED",
    "VOIDED",
    "InMemoryStore",
    "SQLiteStore",
    "StateStore",
    "TxRecord",
]
