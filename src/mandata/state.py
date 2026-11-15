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

# Settlement lifecycle of a transaction (MMF §5.4). CLAIMED is the dispatch
# state: exactly one worker may claim an authorization before submitting it
# to the rail, so concurrent dispatchers cannot settle the same approval twice.
AUTHORIZED, CLAIMED, SETTLED, VOIDED, DENIED = (
    "AUTHORIZED",
    "CLAIMED",
    "SETTLED",
    "VOIDED",
    "DENIED",
)


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

    def append_evidence(self, record) -> None:
        """Durable stores persist an evidence record inside the current
        ``atomic()`` transaction, so state, ledger and evidence commit — or
        roll back — together (MMF §5.2). Optional."""
        ...

    def evidence_records(self) -> list:
        """Load persisted evidence records in chain order. Optional."""
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
            self._records.setdefault(mandate_id, []).append((timestamp, amount, tx_id, False))

    def release(self, mandate_id: str, tx_id: str) -> None:
        with self._lock:
            rows = self._records.get(mandate_id, [])
            self._records[mandate_id] = [
                (ts, a, tid, True if tid == tx_id else rel) for ts, a, tid, rel in rows
            ]

    def spend(self, mandate_id: str, since: float) -> tuple[int, int]:
        """(issued authorizations in window, outstanding reserved amount).

        Velocity counts every authorization issued — released ones included:
        a voided payment still consumed an authorization slot, so rapid
        authorize-and-void cycling cannot evade the rate limit. Budget counts
        only outstanding (unreleased) reservations.
        """
        with self._lock:
            rows = [r for r in self._records.get(mandate_id, []) if r[0] >= since]
        return len(rows), sum(a for _, a, _, rel in rows if not rel)

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
            "(mandate_id TEXT, ts REAL, amount INTEGER, tx_id TEXT, "
            "released INTEGER NOT NULL DEFAULT 0)"
        )
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(spend)")}
        if "tx_id" not in cols:  # migrate a pre-0.4 store
            self._conn.execute("ALTER TABLE spend ADD COLUMN tx_id TEXT")
        if "released" not in cols:  # migrate a pre-0.4.2 store
            self._conn.execute("ALTER TABLE spend ADD COLUMN released INTEGER NOT NULL DEFAULT 0")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_spend ON spend(mandate_id, ts)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS txs (tx_id TEXT PRIMARY KEY, mandate_id TEXT, "
            "payee TEXT, amount INTEGER, currency TEXT, allowed INTEGER, check_id TEXT, "
            "status TEXT)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS evidence ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT, tx_id TEXT, "
            "mandate_id TEXT, agent TEXT, payee TEXT, amount INTEGER, "
            "currency TEXT, allowed INTEGER, check_id TEXT, status TEXT, "
            "ts REAL, prev_hash TEXT, hash TEXT, detail TEXT, signature TEXT)"
        )
        ecols = {r[1] for r in self._conn.execute("PRAGMA table_info(evidence)")}
        for col in ("currency", "status"):  # migrate a pre-0.4.2 store
            if col not in ecols:
                self._conn.execute(f"ALTER TABLE evidence ADD COLUMN {col} TEXT")
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
                "INSERT INTO spend (mandate_id, ts, amount, tx_id) VALUES (?, ?, ?, ?)",
                (mandate_id, timestamp, amount, tx_id),
            )

    def release(self, mandate_id: str, tx_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE spend SET released = 1 WHERE mandate_id = ? AND tx_id = ?",
                (mandate_id, tx_id),
            )

    def spend(self, mandate_id: str, since: float) -> tuple[int, int]:
        """(issued authorizations in window, outstanding reserved amount).

        Velocity counts every authorization issued — released ones included
        (MMF §5.1: a voided payment still consumed an authorization slot);
        budget counts only unreleased reservations.
        """
        with self._lock:
            count, total = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(CASE WHEN released = 0 "
                "THEN amount ELSE 0 END), 0) FROM spend "
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

    def append_evidence(self, record) -> None:
        """Insert one evidence row inside the caller's ``atomic()`` transaction.

        Called by ``EvidenceLog`` while the gate holds ``BEGIN IMMEDIATE``, so
        the ledger, the reservation and the evidence commit or roll back as
        one operation — a crash cannot leave a decision without its record.
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO evidence (event, tx_id, mandate_id, agent, payee, "
                "amount, currency, allowed, check_id, status, ts, prev_hash, "
                "hash, detail, signature) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.event,
                    record.tx_id,
                    record.mandate_id,
                    record.agent,
                    record.payee,
                    record.amount,
                    record.currency,
                    int(record.allowed),
                    record.check,
                    record.status,
                    record.timestamp,
                    record.prev_hash,
                    record.hash,
                    record.detail,
                    record.signature,
                ),
            )

    def evidence_records(self) -> list:
        """Reload persisted evidence records in insertion (chain) order."""
        from .models import EvidenceRecord

        with self._lock:
            rows = self._conn.execute(
                "SELECT event, tx_id, mandate_id, agent, payee, amount, currency, "
                "allowed, check_id, status, ts, prev_hash, detail, signature "
                "FROM evidence ORDER BY seq"
            ).fetchall()
        return [
            EvidenceRecord(
                mandate_id=r[2],
                agent=r[3],
                payee=r[4],
                amount=r[5],
                currency=r[6] or "",
                allowed=bool(r[7]),
                check=r[8],
                status=r[9] or "",
                timestamp=r[10],
                prev_hash=r[11],
                detail=r[12] or "",
                tx_id=r[1],
                event=r[0],
                signature=r[13] or "",
            )
            for r in rows
        ]


__all__ = [
    "AUTHORIZED",
    "CLAIMED",
    "DENIED",
    "SETTLED",
    "VOIDED",
    "InMemoryStore",
    "SQLiteStore",
    "StateStore",
    "TxRecord",
]
