/**
 * Persistent mandate state — spend tracking and the transaction ledger.
 *
 * The enforcement gate is only as strong as its counters: if velocity limits
 * or spend caps live in process memory, restarting the agent resets them.
 * StateStore abstracts where that state lives. It holds two things:
 *
 * - spend rows `(mandate_id, ts, amount, tx_id)` — one per mandate in the
 *   chain for every authorized transaction; voiding releases them;
 * - the transaction ledger `tx_id -> TxRecord` — what the gate decided for
 *   each transaction identifier, and its settlement status (MMF §5.3–5.4).
 */

import type { EvidenceRecord } from "./models";

// Settlement lifecycle of a transaction (MMF §5.4). CLAIMED is the dispatch
// state: exactly one worker may claim an authorization before submitting it
// to the rail, so concurrent dispatchers cannot settle the same approval twice.
export const AUTHORIZED = "AUTHORIZED";
export const CLAIMED = "CLAIMED";
export const SETTLED = "SETTLED";
export const VOIDED = "VOIDED";
export const DENIED = "DENIED";

export class TxRecord {
  constructor(
    public txId: string,
    public mandateId: string,
    public payee: string,
    public amount: number | null,
    public currency: string | null,
    public allowed: boolean,
    public check: string,
    public status: string,
  ) {}

  sameRequest(mandateId: string, payee: string, amount: number | null, currency: string | null): boolean {
    return (
      this.mandateId === mandateId &&
      this.payee === payee &&
      this.amount === amount &&
      this.currency === currency
    );
  }
}

/** Where spend rows and the transaction ledger live. */
export interface StateStore {
  record(mandateId: string, timestamp: number, amount: number, txId: string): void;
  release(mandateId: string, txId: string): void;
  resolve(mandateId: string, txId: string): void;
  /** Return `[transaction count, outstanding amount]` since `since`. */
  spend(mandateId: string, since: number): [number, number];
  getTx(txId: string): TxRecord | undefined;
  putTx(tx: TxRecord): void;
  /** Durable stores persist an evidence record inside the gate's commit (optional). */
  appendEvidence?(record: EvidenceRecord): void;
  /** Load persisted evidence records in chain order (optional). */
  evidenceRecords?(): EvidenceRecord[];
}

interface SpendRow {
  ts: number;
  amount: number;
  txId: string;
  released: boolean;
  resolved: boolean;
}

/** Process-memory store. JS is single-threaded, so atomicity needs no lock. */
export class InMemoryStore implements StateStore {
  private records = new Map<string, SpendRow[]>();
  private txs = new Map<string, TxRecord>();

  record(mandateId: string, timestamp: number, amount: number, txId: string): void {
    const rows = this.records.get(mandateId) ?? [];
    rows.push({ ts: timestamp, amount, txId, released: false, resolved: false });
    this.records.set(mandateId, rows);
  }

  release(mandateId: string, txId: string): void {
    for (const row of this.records.get(mandateId) ?? []) {
      if (row.txId === txId) row.released = true;
    }
  }

  /** Mark a transaction's reservations resolved (settled). */
  resolve(mandateId: string, txId: string): void {
    for (const row of this.records.get(mandateId) ?? []) {
      if (row.txId === txId) row.resolved = true;
    }
  }

  /**
   * `(issued authorizations in window, outstanding reserved amount)`.
   *
   * Velocity counts every authorization issued — released ones included: a
   * voided payment still consumed an authorization slot. Budget counts
   * unreleased reservations in the window, PLUS unresolved open reservations
   * at any age: a claim that outlives its window is still money possibly
   * committed (MMF §5.1).
   */
  spend(mandateId: string, since: number): [number, number] {
    let issued = 0;
    let outstanding = 0;
    for (const row of this.records.get(mandateId) ?? []) {
      if (row.ts >= since) {
        issued++;
        if (!row.released) outstanding += row.amount;
      } else if (!row.released && !row.resolved) {
        outstanding += row.amount;
      }
    }
    return [issued, outstanding];
  }

  getTx(txId: string): TxRecord | undefined {
    return this.txs.get(txId);
  }

  putTx(tx: TxRecord): void {
    this.txs.set(tx.txId, tx);
  }
}
