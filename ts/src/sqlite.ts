/**
 * File-backed store — counters and ledger survive restarts and are shared
 * across processes. Uses the stdlib `node:sqlite` module — no dependencies.
 *
 * `beginImmediate()` opens a `BEGIN IMMEDIATE` transaction, which SQLite
 * serializes across every process using the same file, so concurrent gates
 * in different processes cannot jointly exceed a bound. The synchronous
 * API means the gate's check-and-record section stays atomic without locks.
 */

import { DatabaseSync } from "node:sqlite";

import { EvidenceRecord } from "./models";
import { TxRecord, type StateStore } from "./state";

export class SQLiteStore implements StateStore {
  private conn: DatabaseSync;
  private depth = 0;

  constructor(path: string) {
    this.conn = new DatabaseSync(path);
    this.conn.exec("PRAGMA journal_mode=WAL");
    this.conn.exec(
      "CREATE TABLE IF NOT EXISTS spend " +
        "(mandate_id TEXT, ts REAL, amount INTEGER, tx_id TEXT, " +
        "released INTEGER NOT NULL DEFAULT 0, " +
        "resolved INTEGER NOT NULL DEFAULT 0)",
    );
    this.conn.exec("CREATE INDEX IF NOT EXISTS idx_spend ON spend(mandate_id, ts)");
    this.conn.exec(
      "CREATE TABLE IF NOT EXISTS txs (tx_id TEXT PRIMARY KEY, mandate_id TEXT, " +
        "payee TEXT, amount INTEGER, currency TEXT, allowed INTEGER, check_id TEXT, status TEXT)",
    );
    this.conn.exec(
      "CREATE TABLE IF NOT EXISTS evidence (" +
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT, tx_id TEXT, " +
        "mandate_id TEXT, agent TEXT, payee TEXT, amount INTEGER, " +
        "currency TEXT, allowed INTEGER, check_id TEXT, status TEXT, " +
        "ts REAL, prev_hash TEXT, hash TEXT, detail TEXT, reference TEXT, signature TEXT)",
    );
  }

  /**
   * Open a serializable section. Reentrant — nested calls join the outer
   * transaction, matching the Python gate's use inside `atomic()`.
   */
  beginImmediate<T>(fn: () => T): T {
    const outer = this.depth === 0;
    if (outer) this.conn.exec("BEGIN IMMEDIATE");
    this.depth++;
    try {
      const result = fn();
      this.depth--;
      if (outer) this.conn.exec("COMMIT");
      return result;
    } catch (e) {
      this.depth--;
      if (outer) this.conn.exec("ROLLBACK");
      throw e;
    }
  }

  record(mandateId: string, timestamp: number, amount: number, txId: string): void {
    this.conn
      .prepare("INSERT INTO spend (mandate_id, ts, amount, tx_id, resolved) VALUES (?, ?, ?, ?, 0)")
      .run(mandateId, timestamp, amount, txId);
  }

  release(mandateId: string, txId: string): void {
    this.conn
      .prepare("UPDATE spend SET released = 1 WHERE mandate_id = ? AND tx_id = ?")
      .run(mandateId, txId);
  }

  /** Mark a transaction's reservations resolved (settled). */
  resolve(mandateId: string, txId: string): void {
    this.conn
      .prepare("UPDATE spend SET resolved = 1 WHERE mandate_id = ? AND tx_id = ?")
      .run(mandateId, txId);
  }

  /**
   * `(issued authorizations in window, outstanding reserved amount)`.
   *
   * Velocity counts every authorization issued — released ones included
   * (MMF §5.1: a voided payment still consumed an authorization slot);
   * budget counts only unreleased reservations.
   */
  spend(mandateId: string, since: number): [number, number] {
    const row = this.conn
      .prepare(
        "SELECT COUNT(*) AS cnt, COALESCE(SUM(CASE WHEN released = 0 " +
          "THEN amount ELSE 0 END), 0) AS total FROM spend " +
          "WHERE mandate_id = ? AND ts >= ?",
      )
      .get(mandateId, since) as { cnt: number; total: number };
    return [Number(row.cnt), Number(row.total)];
  }

  getTx(txId: string): TxRecord | undefined {
    const r = this.conn
      .prepare(
        "SELECT tx_id, mandate_id, payee, amount, currency, allowed, check_id, status " +
          "FROM txs WHERE tx_id = ?",
      )
      .get(txId) as Record<string, unknown> | undefined;
    if (r === undefined) return undefined;
    return new TxRecord(
      r.tx_id as string,
      r.mandate_id as string,
      r.payee as string,
      (r.amount as number | null) ?? null,
      (r.currency as string | null) ?? null,
      Boolean(r.allowed),
      r.check_id as string,
      r.status as string,
    );
  }

  putTx(tx: TxRecord): void {
    this.conn
      .prepare("INSERT OR REPLACE INTO txs VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
      .run(
        tx.txId, tx.mandateId, tx.payee, tx.amount, tx.currency,
        tx.allowed ? 1 : 0, tx.check, tx.status,
      );
  }

  /**
   * Insert one evidence row inside the caller's `beginImmediate` section.
   *
   * Called by `EvidenceLog` while the gate holds `BEGIN IMMEDIATE`, so the
   * ledger, the reservation and the evidence commit or roll back as one
   * operation — a crash cannot leave a decision without its record.
   */
  appendEvidence(record: EvidenceRecord): void {
    this.conn
      .prepare(
        "INSERT INTO evidence (event, tx_id, mandate_id, agent, payee, " +
          "amount, currency, allowed, check_id, status, ts, prev_hash, " +
          "hash, detail, reference, signature) " +
          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .run(
        record.event, record.txId, record.mandateId, record.agent, record.payee,
        record.amount, record.currency, record.allowed ? 1 : 0, record.check,
        record.status, record.timestamp, record.prevHash, record.hash,
        record.detail, record.reference, record.signature,
      );
  }

  /** Reload persisted evidence records in insertion (chain) order. */
  evidenceRecords(): EvidenceRecord[] {
    const rows = this.conn
      .prepare(
        "SELECT event, tx_id, mandate_id, agent, payee, amount, currency, " +
          "allowed, check_id, status, ts, prev_hash, detail, reference, signature " +
          "FROM evidence ORDER BY seq",
      )
      .all() as unknown as Array<Record<string, unknown>>;
    return rows.map(
      (r) =>
        new EvidenceRecord({
          event: r.event as string,
          txId: r.tx_id as string,
          mandateId: r.mandate_id as string,
          agent: r.agent as string,
          payee: r.payee as string,
          amount: r.amount as number | null,
          currency: (r.currency as string) || "",
          allowed: Boolean(r.allowed),
          check: r.check_id as string,
          status: (r.status as string) || "",
          timestamp: r.ts as number,
          prevHash: r.prev_hash as string,
          detail: (r.detail as string) || "",
          reference: (r.reference as string) || "",
          signature: (r.signature as string) || "",
        }),
    );
  }
}
