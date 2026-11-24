/** Core mandate model: principal -> agent -> credential -> transaction -> evidence. */

import { createHash, randomUUID } from "node:crypto";

import { canonicalNumber, lpFields } from "./canonical";
import { ValueError } from "./errors";
import { isMinorAmount } from "./money";
import { InMemoryStore, type StateStore } from "./state";

/**
 * Normative check identifiers (MMF §5), in precedence order. TX_CONFLICT is
 * the pre-evaluation idempotency rule (MMF §5.3) and INVALID_CHAIN the
 * preliminary structural check (MMF §5.1); neither is one of the ten checks.
 */
export const CHECKS = [
  "TX_CONFLICT",
  "INVALID_CHAIN",
  "CREDENTIAL",
  "REVOKED_REGISTRY",
  "REVOKED",
  "EXPIRED",
  "NON_POSITIVE",
  "CAP_EXCEEDED",
  "CURRENCY",
  "PAYEE",
  "VELOCITY",
  "BUDGET",
  "WITHIN",
] as const;

/**
 * Evidence events (MMF §5.4): a gate decision, an idempotent replay of one,
 * and the settlement-lifecycle transitions of an authorization.
 */
export const EVENTS = ["DECISION", "REPLAY", "CLAIM", "REJECTED", "SETTLE", "VOID"] as const;

export function isFiniteNumber(x: unknown): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

const now = () => Date.now() / 1000;

/**
 * The permitted envelope an agent must transact within.
 *
 * Amounts (`maxAmount`, `totalBudget`) are integers in the minor unit of
 * `currency` — pence for GBP, cents for USD (see `money.ts`). Malformed
 * bounds are rejected at construction (fail-safe defaults): a mandate that
 * cannot be evaluated is never created.
 */
export class Bounds {
  readonly maxAmount: number;
  readonly currency: string;
  readonly allowedPayees: readonly string[];
  readonly expiresInDays: number;
  readonly velocityLimit: number;
  readonly totalBudget: number | null;
  readonly budgetWindowDays: number;

  constructor(init: {
    maxAmount: number;
    currency?: string;
    allowedPayees?: string[];
    expiresInDays?: number;
    velocityLimit?: number;
    totalBudget?: number | null;
    budgetWindowDays?: number;
  }) {
    this.maxAmount = init.maxAmount;
    this.currency = init.currency ?? "GBP";
    this.allowedPayees = init.allowedPayees ?? [];
    this.expiresInDays = init.expiresInDays ?? 30;
    this.velocityLimit = init.velocityLimit ?? 0;
    this.totalBudget = init.totalBudget ?? null;
    this.budgetWindowDays = init.budgetWindowDays ?? 30;

    const errors: string[] = [];
    if (!isMinorAmount(this.maxAmount))
      errors.push("max_amount must be an integer > 0 in minor units");
    if (typeof this.currency !== "string" || !this.currency)
      errors.push("currency must be a non-empty string");
    if (!Array.isArray(this.allowedPayees) || !this.allowedPayees.every((p) => typeof p === "string" && p))
      errors.push("allowed_payees must be a list of non-empty strings");
    if (!Number.isInteger(this.expiresInDays) || this.expiresInDays < 1)
      errors.push("expires_in_days must be an integer >= 1");
    if (!Number.isInteger(this.velocityLimit) || this.velocityLimit < 0)
      errors.push("velocity_limit must be an integer >= 0");
    if (this.totalBudget !== null && !isMinorAmount(this.totalBudget))
      errors.push("total_budget must be null or an integer > 0 in minor units");
    if (!Number.isInteger(this.budgetWindowDays) || this.budgetWindowDays < 1)
      errors.push("budget_window_days must be an integer >= 1");
    if (errors.length) throw new ValueError("invalid bounds: " + errors.join("; "));
  }
}

/** A principal's grant of bounded spending authority to an agent. */
export class Mandate {
  mandateId: string;
  issuedAt: number;
  revoked = false;
  parentMandateId: string | null = null;
  /** Spend rows + tx ledger for this mandate (shared across a delegation chain via the gate's `store` option). */
  state: StateStore;
  /** Resolved parent (set by `delegate()`); the gate evaluates the whole chain. */
  parent: Mandate | null = null;

  constructor(
    public principal: string,
    public agent: string,
    public bounds: Bounds,
    opts: { mandateId?: string; issuedAt?: number; state?: StateStore } = {},
  ) {
    this.mandateId = opts.mandateId ?? randomUUID().replace(/-/g, "");
    this.issuedAt = opts.issuedAt ?? now();
    this.state = opts.state ?? new InMemoryStore();
  }

  get expiresAt(): number {
    return this.issuedAt + this.bounds.expiresInDays * 86400;
  }

  isExpired(at?: number): boolean {
    return (at ?? now()) > this.expiresAt;
  }

  /** This mandate followed by its resolved ancestors (leaf -> root). */
  chain(): Mandate[] {
    const out: Mandate[] = [];
    const seen = new Set<Mandate>();
    let node: Mandate | null = this;
    while (node !== null && !seen.has(node)) {
      seen.add(node);
      out.push(node);
      node = node.parent;
    }
    return out;
  }

  /**
   * Return `[resolvable prefix, structural error or null]`.
   *
   * The error is set when an ancestor link cannot be resolved
   * (`parentMandateId` set but no parent bound) or the chain is cyclic —
   * both deny with `INVALID_CHAIN` (MMF §5.1).
   */
  resolveChain(): [Mandate[], string | null] {
    const out: Mandate[] = [];
    const seen = new Set<Mandate>();
    let node: Mandate | null = this;
    while (node !== null) {
      if (seen.has(node)) return [out, "cyclic delegation chain"];
      seen.add(node);
      out.push(node);
      if (node.parent === null && node.parentMandateId) {
        return [out, `parent mandate ${node.parentMandateId} unresolvable`];
      }
      node = node.parent;
    }
    return [out, null];
  }

  revoke(): void {
    this.revoked = true;
  }

  record(at: number, amount: number, txId?: string): void {
    this.state.record(this.mandateId, at, amount, txId ?? randomUUID().replace(/-/g, ""));
  }

  spendInWindow(at: number): [number, number] {
    const since = at - this.bounds.budgetWindowDays * 86400;
    return this.state.spend(this.mandateId, since);
  }
}

/**
 * The outcome of a mandate gate check.
 *
 * `check` is the normative MMF check identifier; `reason` is advisory,
 * human-readable text. `payee`/`amount`/`currency` are exactly what the gate
 * evaluated; adapters MUST settle only an instruction that `binds()` to them
 * (MMF §5.5). `replayed` is true when the decision was returned from the
 * idempotency ledger rather than evaluated; `status` is the transaction's
 * lifecycle state at the time of the call.
 */
export class Decision {
  allowed!: boolean;
  reason!: string;
  evidence!: EvidenceRecord;
  check = "WITHIN";
  txId = "";
  payee = "";
  amount: number | null = null;
  currency: string | null = null;
  replayed = false;
  status = "";

  constructor(init: Partial<Decision> & Pick<Decision, "allowed" | "reason" | "evidence">) {
    Object.assign(this, init);
  }

  /**
   * True iff a settlement instruction matches what the gate approved and the
   * authorization is still open (not yet settled or voided), so a retried
   * request can never be settled twice.
   */
  binds(payee: string, amount: number, currency?: string | null): boolean {
    return (
      this.allowed &&
      this.status === "AUTHORIZED" &&
      payee === this.payee &&
      amount === this.amount &&
      (currency === undefined || currency === null || this.currency === null || currency === this.currency)
    );
  }
}

export interface EvidenceRecordInit {
  mandateId: string;
  agent: string;
  payee: string;
  amount: number | null;
  allowed: boolean;
  check: string;
  timestamp: number;
  prevHash: string;
  detail?: string;
  reference?: string;
  txId?: string;
  event?: string;
  currency?: string;
  status?: string;
  signature?: string;
}

/**
 * An auditable record of a gate decision or lifecycle event.
 *
 * The canonical fields are covered by the hash; `detail` (advisory text) is
 * not, so implementations may word it differently and still produce
 * identical chains. `signature` is the gate's Ed25519 signature over the
 * hash (authenticity, MMF §5.2); it is not part of the hash itself.
 */
export class EvidenceRecord {
  mandateId: string;
  agent: string;
  payee: string;
  amount: number | null;
  allowed: boolean;
  check: string;
  timestamp: number;
  prevHash: string;
  detail: string;
  reference: string;
  txId: string;
  event: string;
  currency: string;
  status: string;
  signature: string;

  constructor(init: EvidenceRecordInit) {
    this.mandateId = init.mandateId;
    this.agent = init.agent;
    this.payee = init.payee;
    this.amount = init.amount;
    this.allowed = init.allowed;
    this.check = init.check;
    this.timestamp = init.timestamp;
    this.prevHash = init.prevHash;
    this.detail = init.detail ?? "";
    this.reference = init.reference ?? "";
    this.txId = init.txId ?? "";
    this.event = init.event ?? "DECISION";
    this.currency = init.currency ?? "";
    this.status = init.status ?? "";
    this.signature = init.signature ?? "";
  }

  /** Backward-compatible alias: advisory detail, falling back to the check ID. */
  get reason(): string {
    return this.detail || this.check;
  }

  /**
   * MMF §5.6 canonical serialization (v3) — identical bytes in every
   * implementation.
   *
   * Each field is `<UTF-8 byte length>:<UTF-8 bytes>` joined by single `|`
   * bytes. Field order is normative. TIMESTAMP is the integer
   * Unix-millisecond value nearest the record timestamp (round-half-up).
   * The advisory `detail` string is deliberately NOT hashed: denial-reason
   * prose is implementation-specific, while the payload must be
   * byte-identical across implementations.
   */
  canonicalPayload(): string {
    return lpFields(
      this.event,
      this.txId,
      this.mandateId,
      this.agent,
      this.payee,
      isMinorAmount(this.amount) ? String(this.amount) : "invalid",
      this.currency,
      this.allowed ? "true" : "false",
      this.check,
      this.status,
      this.reference || "",
      String(Math.floor(this.timestamp * 1000 + 0.5)),
      this.prevHash,
    );
  }

  get hash(): string {
    return createHash("sha256").update(this.canonicalPayload(), "utf8").digest("hex");
  }
}
