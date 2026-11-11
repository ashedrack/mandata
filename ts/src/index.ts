import { createHash, createHmac } from "crypto";

export interface Bounds {
  maxAmount: number;
  currency: string;
  allowedPayees?: string[];
  expiresInDays?: number;
  velocityLimit?: number;
  totalBudget?: number;
}

export interface Decision { allowed: boolean; reason: string; }

interface EvidenceRecord { prevHash: string; hash: string; }

export class EvidenceLog {
  private records: EvidenceRecord[] = [];
  private static G = "genesis";
  headHash(): string {
    const last = this.records.at(-1);
    return last ? last.hash : EvidenceLog.G;
  }
  append(payload: string): string {
    const h = createHash("sha256").update(this.headHash() + payload).digest("hex");
    this.records.push({ prevHash: this.headHash(), hash: h });
    return h;
  }
  verify(): boolean {
    return this.records.every((r, i) =>
      r.prevHash === (i === 0 ? EvidenceLog.G : this.records[i - 1].hash));
  }
}

export class Mandate {
  private txToday = 0;
  private spentToday = 0;
  revoked = false;
  constructor(public principal: string, public agent: string, public bounds: Bounds) {}
}

export function enforce(m: Mandate, payee: string, amount: number,
                        currency?: string, log?: EvidenceLog): Decision {
  const b = m.bounds;
  let decision: Decision;
  if (m.revoked) decision = { allowed: false, reason: "mandate revoked" };
  else if (amount <= 0) decision = { allowed: false, reason: "amount must be positive" };
  else if (amount > b.maxAmount)
    decision = { allowed: false, reason: `amount ${amount} exceeds cap ${b.maxAmount}` };
  else if (currency && currency !== b.currency)
    decision = { allowed: false, reason: `currency ${currency} not allowed` };
  else if (b.allowedPayees?.length && !b.allowedPayees.includes(payee))
    decision = { allowed: false, reason: `payee '${payee}' not in allowed set` };
  else if (b.velocityLimit && (m as any).txToday >= b.velocityLimit)
    decision = { allowed: false, reason: "velocity limit exceeded" };
  else decision = { allowed: true, reason: "within mandate" };
  if (decision.allowed) { (m as any).txToday++; (m as any).spentToday += amount; }
  log?.append(JSON.stringify({ payee, amount, allowed: decision.allowed }));
  return decision;
}
