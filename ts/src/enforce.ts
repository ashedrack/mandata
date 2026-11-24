/**
 * The mandate gate — deterministic enforcement evaluated BEFORE settlement.
 *
 * Lifecycle (MMF §5.3–5.4): `enforce()` decides a transaction identified by
 * `txId` and, on allow, reserves its amount against every mandate in the
 * delegation chain (status AUTHORIZED). The rail adapter then calls
 * `settle()` once funds move, or `void()` if they never will, which releases
 * the reservation. Retrying `enforce()` with the same `txId` returns the
 * original decision without re-evaluating or reserving again.
 */

import { randomUUID } from "node:crypto";

import { assertScalars } from "./canonical";
import { Credential } from "./credentials";
import { verifyCredential } from "./credentials";
import { LifecycleError } from "./errors";
import { EvidenceLog } from "./evidence";
import { Decision, isFiniteNumber, Mandate } from "./models";
import { isMinorAmount, MAX_AMOUNT } from "./money";
import type { RevocationRegistry } from "./revocation";
import { AUTHORIZED, CLAIMED, DENIED, SETTLED, VOIDED, TxRecord, type StateStore } from "./state";

const DEFAULT_LOG = new EvidenceLog();

const now = () => Date.now() / 1000;

function label(node: Mandate, mandate: Mandate): string {
  return node === mandate ? "mandate" : `ancestor mandate ${node.mandateId}`;
}

export interface EnforceOptions {
  /** Conformance/replay timestamp. Production callers MUST NOT pass a timestamp supplied by the agent. */
  at?: number;
  evidenceLog?: EvidenceLog;
  /** Shared ledger for a delegation chain or a multi-mandate gate. */
  store?: StateStore;
  registry?: RevocationRegistry;
  hooks?: Array<(d: Decision) => void>;
  /** Credential binding: verify the principal's signature first (check CREDENTIAL). */
  credential?: Credential | Record<string, Credential>;
  principalKey?: string;
  clock?: () => number;
  /** Stable payment-attempt id (e.g. the rail's idempotency key) — required for retries to be recognized. */
  txId?: string;
}

/**
 * Evaluate a proposed transaction against its mandate (MMF §5).
 *
 * `amount` is an integer in minor units of the mandate currency (see
 * `toMinor`); anything else denies as `NON_POSITIVE`.
 *
 * `txId` identifies the payment attempt and makes the call idempotent: a
 * repeat with the same `txId` and the same request returns the original
 * decision (`replayed=true`) and reserves nothing; a repeat with a different
 * request denies as `TX_CONFLICT`. Production callers MUST supply a stable
 * `txId`; if omitted a random one is generated and retries cannot be
 * recognized.
 *
 * Every call appends exactly one evidence record (event `DECISION` or
 * `REPLAY`).
 */
export function enforce(
  mandate: Mandate,
  payee: string,
  amount: number,
  opts: EnforceOptions & { currency?: string | null } = {},
): Decision {
  const at = opts.at ?? (opts.clock ?? now)();
  if (!isFiniteNumber(at)) throw new Error(`evaluation time must be a finite number, got ${at}`);
  const log = opts.evidenceLog ?? DEFAULT_LOG;
  const txId = opts.txId ?? randomUUID().replace(/-/g, "");
  const currency = opts.currency ?? null;
  // Malformed request strings are refused before any evaluation or ledger
  // access (MMF §5.6): they could never be canonicalized into evidence.
  assertScalars("tx_id", txId);
  assertScalars("payee", payee);
  assertScalars("currency", currency);
  assertScalars("mandate_id", mandate.mandateId);
  assertScalars("agent", mandate.agent);

  let [chain, chainError] = mandate.resolveChain();
  const ledger = opts.store ?? mandate.state;
  const storedAmount = isMinorAmount(amount) ? amount : null;

  // Single-threaded JS: the Python gate's lock + store.atomic() section is
  // the synchronous block below — check-and-record cannot interleave.
  const prior = ledger.getTx(txId);
  if (prior === undefined) {
    // Authoritative resolution applies to NEW transactions only; re-resolve
    // and fail closed if the chain identity changed mid-evaluation.
    const [chain2, err2] = mandate.resolveChain();
    if (chain2.map((n) => n.mandateId).join() !== chain.map((n) => n.mandateId).join()) {
      chainError = chainError ?? err2 ?? "delegation chain mutated during evaluation";
      chain = chain2.length ? chain2 : [mandate];
    }
  }

  let check: string, allowed: boolean, status: string, detail: string, event: string;
  let replayed = false;

  if (prior !== undefined && prior.sameRequest(mandate.mandateId, payee, storedAmount, currency)) {
    check = prior.check;
    allowed = prior.allowed;
    status = prior.status;
    replayed = true;
    detail = `idempotent replay of ${txId} (${prior.status})`;
    event = "REPLAY";
  } else if (prior !== undefined) {
    check = "TX_CONFLICT";
    allowed = false;
    status = DENIED;
    event = "DECISION";
    detail = `tx_id ${txId} was already used for a different request`;
  } else if (chainError !== null) {
    check = "INVALID_CHAIN";
    allowed = false;
    status = DENIED;
    event = "DECISION";
    detail = chainError;
    ledger.putTx(
      new TxRecord(txId, mandate.mandateId, payee, storedAmount, currency, allowed, check, status),
    );
  } else {
    [check, detail] = evaluate(
      mandate, chain, payee, amount, currency, at,
      opts.store, opts.registry, opts.credential, opts.principalKey,
    );
    allowed = check === "WITHIN";
    event = "DECISION";
    status = allowed ? AUTHORIZED : DENIED;
    if (allowed) {
      for (const node of chain) {
        (opts.store ?? node.state).record(node.mandateId, at, amount, txId);
      }
    }
    ledger.putTx(
      new TxRecord(txId, mandate.mandateId, payee, storedAmount, currency, allowed, check, status),
    );
  }

  const record = log.append({
    mandateId: mandate.mandateId,
    agent: mandate.agent,
    payee,
    amount: storedAmount,
    allowed,
    check,
    timestamp: at,
    detail,
    txId,
    event,
    currency: currency ?? "",
    status,
  });

  const decision = new Decision({
    allowed,
    reason: detail,
    evidence: record,
    check,
    txId,
    payee,
    amount: storedAmount,
    currency,
    replayed,
    status,
  });
  for (const hook of opts.hooks ?? []) hook(decision);
  return decision;
}

/** Return `[checkId, advisory detail]`. First failure wins (MMF §5). */
function evaluate(
  mandate: Mandate,
  chain: Mandate[],
  payee: string,
  amount: number,
  currency: string | null,
  at: number,
  store: StateStore | undefined,
  registry: RevocationRegistry | undefined,
  credential: Credential | Record<string, Credential> | undefined,
  key: string | undefined,
): [string, string] {
  if (credential !== undefined || key !== undefined) {
    if (credential === undefined || key === undefined) {
      return ["CREDENTIAL", "credential and principal_key must both be supplied"];
    }
    const creds: Record<string, Credential> =
      credential instanceof Credential ? { [mandate.mandateId]: credential } : credential;
    for (const node of chain) {
      const nodeCredential = creds[node.mandateId];
      if (nodeCredential === undefined) {
        return ["CREDENTIAL", `no credential supplied for ${label(node, mandate)}`];
      }
      const [ok, why] = verifyCredential(nodeCredential, node, key, at);
      if (!ok) return ["CREDENTIAL", `${label(node, mandate)}: ${why}`];
    }
  }

  if (registry !== undefined) {
    for (const node of chain) {
      let revoked: boolean;
      try {
        revoked = registry.isRevoked(node.mandateId);
      } catch (exc) {
        // fail closed: any registry fault denies
        return ["REVOKED_REGISTRY", `revocation registry unavailable (${(exc as Error).name})`];
      }
      if (revoked) return ["REVOKED_REGISTRY", `${label(node, mandate)} revoked (registry)`];
    }
  }

  for (const node of chain) {
    if (node.revoked) return ["REVOKED", `${label(node, mandate)} revoked`];
  }

  for (const node of chain) {
    if (node.isExpired(at)) return ["EXPIRED", `${label(node, mandate)} expired`];
  }

  if (!isMinorAmount(amount)) {
    return ["NON_POSITIVE", `amount ${amount} is not a positive integer in minor units`];
  }

  for (const node of chain) {
    if (amount > node.bounds.maxAmount) {
      return ["CAP_EXCEEDED", `amount ${amount} exceeds cap ${node.bounds.maxAmount}`];
    }
  }

  for (const node of chain) {
    if (currency !== node.bounds.currency) {
      return [
        "CURRENCY",
        currency
          ? `currency '${currency}' outside mandate (${node.bounds.currency})`
          : "transaction currency is required",
      ];
    }
  }

  for (const node of chain) {
    if (node.bounds.allowedPayees.length && !node.bounds.allowedPayees.includes(payee)) {
      return ["PAYEE", `payee '${payee}' not in allowed set`];
    }
  }

  for (const node of chain) {
    const limit = node.bounds.velocityLimit;
    if (limit && spend(node, store, at, 1)[0] >= limit) {
      return ["VELOCITY", `${label(node, mandate)} velocity limit ${limit} reached`];
    }
  }

  for (const node of chain) {
    const budget = node.bounds.totalBudget;
    if (budget !== null) {
      const [, spent] = spend(node, store, at, node.bounds.budgetWindowDays);
      // Checked arithmetic (MMF §3.1): an accumulated total beyond 2^53-1 is
      // outside the exact-representation bound — fail safe.
      if (spent + amount > MAX_AMOUNT || spent + amount > budget) {
        return ["BUDGET", `${label(node, mandate)} budget ${budget} would be exceeded`];
      }
    }
  }

  return ["WITHIN", "within mandate"];
}

function spend(node: Mandate, store: StateStore | undefined, at: number, windowDays: number | null): [number, number] {
  // A null window means "all recorded history" (the accumulated total).
  const since = windowDays === null ? 0 : at - windowDays * 86400;
  return (store ?? node.state).spend(node.mandateId, since);
}

export interface LifecycleOptions {
  evidenceLog?: EvidenceLog;
  store?: StateStore;
  at?: number;
  clock?: () => number;
  registry?: RevocationRegistry;
}

/**
 * Atomically claim an AUTHORIZED transaction for dispatch (MMF §5.5).
 *
 * Exactly one worker can move a transaction from AUTHORIZED to CLAIMED: the
 * transition verifies that the instruction about to be submitted matches the
 * stored request (payee, amount, currency), then claims it in the same
 * serialized step. After a successful claim, invoke the rail idempotently on
 * `txId`, then call `settle()` or `void()` with its outcome.
 *
 * Revocation is re-verified at claim time (MMF §5.4): an authorization
 * granted before a mandate was revoked MUST NOT be dispatched afterward.
 *
 * Throws LifecycleError for unknown, denied, already claimed, settled or
 * voided transactions, or when the instruction does not match the stored
 * request.
 */
export function claim(
  mandate: Mandate,
  txId: string,
  payee: string,
  amount: number,
  currency: string | null | undefined,
  opts: LifecycleOptions = {},
): TxRecord {
  const at = opts.at ?? (opts.clock ?? now)();
  const log = opts.evidenceLog ?? DEFAULT_LOG;
  const cur = currency ?? null;
  assertScalars("tx_id", txId);
  assertScalars("payee", payee);
  assertScalars("currency", cur);
  assertScalars("mandate_id", mandate.mandateId);
  assertScalars("agent", mandate.agent);
  const [chain, chainError] = mandate.resolveChain();
  if (chainError !== null) throw new LifecycleError(chainError);
  const ledger = opts.store ?? mandate.state;

  const tx = ledger.getTx(txId);
  // a transaction of another mandate is UNKNOWN_TX: its stored fields must
  // never be copied into this mandate's evidence (MMF §5.6)
  const own = tx !== undefined && tx.mandateId === mandate.mandateId ? tx : undefined;
  let err: string | null = null;
  let cause: string | null = null;

  if (tx === undefined || tx.mandateId !== mandate.mandateId) {
    err = `unknown transaction ${txId} for mandate ${mandate.mandateId}`;
    cause = "UNKNOWN_TX";
  } else if (tx.status !== AUTHORIZED) {
    err = `cannot claim ${txId}: status is ${tx.status}, not AUTHORIZED`;
    cause = "STATUS";
  } else if (tx.payee !== payee || tx.amount !== amount || tx.currency !== cur) {
    err =
      `instruction ('${payee}', ${amount}, '${cur}') does not bind to ` +
      `authorized transaction ${txId} ('${tx.payee}', ${tx.amount}, '${tx.currency}')`;
    cause = "BINDING";
  } else {
    for (const node of chain) {
      if (node.revoked) {
        err = `${label(node, mandate)} revoked — authorization cannot be dispatched`;
        cause = "REVOKED";
        break;
      }
    }
    if (err === null) {
      for (const node of chain) {
        if (node.isExpired(at)) {
          err = `${label(node, mandate)} expired — cannot dispatch`;
          cause = "EXPIRED";
          break;
        }
      }
    }
    if (err === null && opts.registry !== undefined) {
      for (const node of chain) {
        try {
          if (opts.registry.isRevoked(node.mandateId)) {
            err = `${label(node, mandate)} revoked (registry) — cannot dispatch`;
            cause = "REVOKED_REGISTRY";
            break;
          }
        } catch (exc) {
          err = `revocation registry unavailable (${(exc as Error).name})`;
          cause = "REGISTRY";
          break;
        }
      }
    }
  }

  let out = tx!;
  if (err !== null) {
    // A rejected claim is security evidence: a binding mismatch is exactly
    // the T6 substitution signal. Normative cause: <OP>:<CAUSE>.
    log.append({
      mandateId: mandate.mandateId,
      agent: mandate.agent,
      payee,
      amount: isMinorAmount(amount) ? amount : null,
      allowed: false,
      check: `CLAIM:${cause}`,
      timestamp: at,
      detail: err,
      txId,
      event: "REJECTED",
      currency: cur ?? "",
      status: own?.status ?? "",
    });
  } else {
    const t = tx!;
    out = new TxRecord(t.txId, t.mandateId, t.payee, t.amount, t.currency, t.allowed, t.check, CLAIMED);
    ledger.putTx(out);
    log.append({
      mandateId: mandate.mandateId,
      agent: mandate.agent,
      payee: t.payee,
      amount: t.amount,
      allowed: true,
      check: t.check,
      timestamp: at,
      detail: `${txId} claimed for dispatch`,
      txId,
      event: "CLAIM",
      currency: t.currency ?? "",
      status: CLAIMED,
    });
  }
  if (err !== null) throw new LifecycleError(err);
  return out;
}

/**
 * Mark an AUTHORIZED or CLAIMED transaction as SETTLED (funds moved).
 *
 * Idempotent: settling an already-settled transaction returns it unchanged.
 * Throws LifecycleError for unknown, denied or voided transactions.
 */
export function settle(mandate: Mandate, txId: string, opts: LifecycleOptions = {}): TxRecord {
  return transition(mandate, txId, SETTLED, opts);
}

export interface VoidOptions extends LifecycleOptions {
  /** Authoritative reference proving the rail did not execute (required to void a CLAIMED tx). */
  cancellation?: string;
  verifyCancellation?: (cancellation: string | undefined, tx: TxRecord) => boolean;
}

/**
 * Release an AUTHORIZED or CLAIMED transaction that will never settle.
 *
 * Its reserved amount is returned to every mandate in the chain; the
 * velocity slot is retained — velocity counts authorizations issued (MMF §5.1).
 *
 * Voiding a CLAIMED transaction requires a `cancellation` reference: a
 * non-empty identifier of the authoritative confirmation that the rail did
 * not and cannot subsequently execute the instruction (MMF §5.4). An
 * uncertain in-flight outcome MUST stay CLAIMED.
 *
 * Idempotent for already-voided transactions. A SETTLED transaction cannot
 * be voided: refunds are a rail concern (MMF §8).
 */
export function void_(mandate: Mandate, txId: string, opts: VoidOptions = {}): TxRecord {
  return transition(mandate, txId, VOIDED, opts);
}
export { void_ as void };

function transition(
  mandate: Mandate,
  txId: string,
  target: string,
  opts: VoidOptions,
): TxRecord {
  const at = opts.at ?? (opts.clock ?? now)();
  const log = opts.evidenceLog ?? DEFAULT_LOG;
  assertScalars("tx_id", txId);
  assertScalars("cancellation", opts.cancellation);
  assertScalars("mandate_id", mandate.mandateId);
  assertScalars("agent", mandate.agent);
  const [chain, chainError] = mandate.resolveChain();
  if (chainError !== null) throw new LifecycleError(chainError);
  const ledger = opts.store ?? mandate.state;

  const tx = ledger.getTx(txId);
  // a transaction of another mandate is UNKNOWN_TX: its stored fields must
  // never be copied into this mandate's evidence (MMF §5.6)
  const own = tx !== undefined && tx.mandateId === mandate.mandateId ? tx : undefined;
  let err: string | null = null;
  let cause: string | null = null;
  const op = target === SETTLED ? "SETTLE" : "VOID";

  if (tx === undefined || tx.mandateId !== mandate.mandateId) {
    err = `unknown transaction ${txId} for mandate ${mandate.mandateId}`;
    cause = "UNKNOWN_TX";
  } else if (tx.status === target) {
    return tx; // idempotent repeat: unchanged, no evidence
  } else if (target === SETTLED && tx.status !== CLAIMED) {
    // Settle records that the rail executed — only reachable via the claim
    // slot. An AUTHORIZED transaction was never dispatched, so it cannot
    // settle; it can only be claimed or voided.
    err = `cannot settle ${txId}: status is ${tx.status}, not CLAIMED`;
    cause = "STATUS";
  } else if (target === VOIDED && tx.status !== AUTHORIZED && tx.status !== CLAIMED) {
    err = `cannot void ${txId}: status is ${tx.status}`;
    cause = "STATUS";
  } else if (target === VOIDED && tx.status === CLAIMED && !opts.cancellation) {
    err =
      `cannot void claimed transaction ${txId} without authoritative ` +
      "confirmation the rail did not execute — an uncertain in-flight " +
      "outcome stays CLAIMED";
    cause = "CANCELLATION";
  } else if (target === VOIDED && tx.status === CLAIMED && opts.verifyCancellation !== undefined) {
    // Optional validation by the trusted component named in MMF §5.4: the
    // reference must bind to THIS transaction's rail instruction. A verifier
    // fault is a failed validation — fail closed.
    let bound: boolean;
    try {
      bound = Boolean(opts.verifyCancellation(opts.cancellation, tx));
    } catch {
      bound = false;
    }
    if (!bound) {
      err =
        `cancellation reference for ${txId} failed validation — the ` +
        "reservation stays held while the in-flight outcome is unconfirmed";
      cause = "CANCELLATION";
    }
  }

  let out = tx!;
  if (err !== null) {
    // Rejected transitions are security evidence too (R6).
    log.append({
      mandateId: mandate.mandateId,
      agent: mandate.agent,
      payee: own?.payee ?? "",
      amount: own?.amount ?? null,
      allowed: false,
      check: `${op}:${cause}`,
      timestamp: at,
      detail: err,
      txId,
      event: "REJECTED",
      currency: own?.currency ?? "",
      status: own?.status ?? "",
    });
  } else {
    if (target === VOIDED) {
      for (const node of chain) (opts.store ?? node.state).release(node.mandateId, txId);
    }
    const t = tx!;
    out = new TxRecord(t.txId, t.mandateId, t.payee, t.amount, t.currency, t.allowed, t.check, target);
    ledger.putTx(out);
    if (target === SETTLED) {
      // Resolved reservations age out of the budget window like any
      // completed spend; unresolved ones keep consuming budget.
      for (const node of chain) (opts.store ?? node.state).resolve(node.mandateId, txId);
    }
    log.append({
      mandateId: mandate.mandateId,
      agent: mandate.agent,
      payee: t.payee,
      amount: t.amount,
      allowed: true,
      check: t.check,
      timestamp: at,
      detail: `${txId} ${target.toLowerCase()}`,
      txId,
      event: target === SETTLED ? "SETTLE" : "VOID",
      reference: opts.cancellation ?? "",
      currency: t.currency ?? "",
      status: target,
    });
  }
  if (err !== null) throw new LifecycleError(err);
  return out;
}
