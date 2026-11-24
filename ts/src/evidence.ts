/**
 * Hash-chained evidence log — every gate decision and lifecycle event is
 * appended and linked.
 *
 * Integrity comes from the hash chain; authenticity from an optional gate
 * signing key (each record's hash is signed with Ed25519).
 */

import type { KeyObject } from "node:crypto";

import { privateKeyFromSeedHex, publicKeyHex, signHex, verifyHex } from "./ed25519";
import { ValueError } from "./errors";
import { EVENTS, EvidenceRecord } from "./models";
import type { StateStore } from "./state";

export interface EvidenceFields {
  mandateId: string;
  agent: string;
  payee: string;
  amount: number | null;
  allowed: boolean;
  check: string;
  timestamp?: number;
  detail?: string;
  txId?: string;
  event?: string;
  currency?: string;
  status?: string;
  reference?: string;
}

/**
 * Append-only chain of enforcement records; each record hashes its predecessor.
 *
 * Pass `signingKeyHex` (the gate's Ed25519 seed, hex) to sign every record;
 * `verifyChain({publicKeyHex})` then also proves each record was written by
 * that gate.
 *
 * Pass `store` (a `StateStore` implementing `appendEvidence` /
 * `evidenceRecords`) to persist each record with the gate's state and ledger
 * updates, so a crash cannot leave a decided transaction without its
 * mandatory record (MMF §5.2).
 */
export class EvidenceLog {
  static readonly GENESIS = "0".repeat(64);

  readonly records: EvidenceRecord[] = [];
  private readonly signingKey?: KeyObject;
  private readonly store?: StateStore;

  constructor(opts: { signingKeyHex?: string; store?: StateStore } = {}) {
    this.signingKey = opts.signingKeyHex ? privateKeyFromSeedHex(opts.signingKeyHex) : undefined;
    this.store = opts.store;
    if (this.store?.evidenceRecords) this.records.push(...this.store.evidenceRecords());
  }

  /** Hex verify key of the gate signing key, if records are signed. */
  get publicKey(): string | null {
    return this.signingKey ? publicKeyHex(this.signingKey) : null;
  }

  get headHash(): string {
    return this.records.length ? this.records[this.records.length - 1].hash : EvidenceLog.GENESIS;
  }

  append(fields: EvidenceFields): EvidenceRecord {
    const event = fields.event ?? "DECISION";
    if (!EVENTS.includes(event as (typeof EVENTS)[number])) {
      throw new ValueError(`unknown evidence event '${event}'`);
    }
    let record = new EvidenceRecord({
      mandateId: fields.mandateId,
      agent: fields.agent,
      payee: fields.payee,
      amount: fields.amount,
      allowed: fields.allowed,
      check: fields.check,
      timestamp: fields.timestamp ?? Date.now() / 1000,
      prevHash: this.headHash,
      detail: fields.detail ?? "",
      reference: fields.reference ?? "",
      txId: fields.txId ?? "",
      event,
      currency: fields.currency ?? "",
      status: fields.status ?? "",
    });
    if (this.signingKey) {
      record.signature = signHex(this.signingKey, record.hash);
    }
    this.store?.appendEvidence?.(record);
    this.records.push(record);
    return record;
  }

  /**
   * Recompute every record hash and check each link.
   *
   * Detects modification, insertion, deletion, or reordering of any record
   * that has a successor. Pass `anchoredHead` — a head hash previously
   * published to an external anchor — to also require that the chain still
   * contains that point. Pass `publicKeyHex` to also require that every
   * record carries a valid signature by that key (authenticity).
   */
  verifyChain(opts: { anchoredHead?: string; publicKeyHex?: string } = {}): boolean {
    let prev = EvidenceLog.GENESIS;
    let seenAnchor = opts.anchoredHead === undefined || opts.anchoredHead === EvidenceLog.GENESIS;
    for (const rec of this.records) {
      if (rec.prevHash !== prev) return false;
      prev = rec.hash;
      if (opts.publicKeyHex !== undefined) {
        if (!verifyHex(opts.publicKeyHex, prev, rec.signature)) return false;
      }
      if (prev === opts.anchoredHead) seenAnchor = true;
    }
    return seenAnchor;
  }
}
