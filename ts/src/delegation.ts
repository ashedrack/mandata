/** Delegation — agent -> sub-agent mandates that can only tighten, never loosen. */

import { ValueError } from "./errors";
import { Bounds, Mandate } from "./models";

/**
 * Create a child mandate for a sub-agent.
 *
 * Child bounds must be a subset of the parent's: no bound may be wider
 * (equal is permitted). "Unlimited" sentinels count as the widest possible
 * value, so a child cannot escape a parent limit by setting
 * `velocityLimit=0` or `allowedPayees=[]`. The child's effective expiry is
 * the earlier of its own and every ancestor's, because the gate checks
 * expiry across the whole chain.
 *
 * The child keeps a reference to its parent; the gate evaluates the whole
 * chain, so parent revocation or expiry stops the child and the child's
 * spending counts against every ancestor's velocity and budget.
 *
 * Throws ValueError on any attempt to widen scope.
 */
export function delegate(parent: Mandate, subAgent: string, bounds: Bounds, at?: number): Mandate {
  const now = at ?? Date.now() / 1000;
  if (parent.revoked) throw new ValueError("cannot delegate from a revoked mandate");
  if (parent.isExpired(now)) throw new ValueError("cannot delegate from an expired mandate");

  const pb = parent.bounds;
  const child = new Mandate(parent.principal, subAgent, bounds, { issuedAt: now });

  const violations: string[] = [];
  if (bounds.maxAmount > pb.maxAmount)
    violations.push(`amount cap ${bounds.maxAmount} exceeds parent ${pb.maxAmount}`);
  if (bounds.currency !== pb.currency) violations.push("currency differs from parent's");
  if (pb.allowedPayees.length &&
    (!bounds.allowedPayees.length || !bounds.allowedPayees.every((p) => pb.allowedPayees.includes(p))))
    violations.push("payees outside parent's allowlist (empty = any payee)");
  if (bounds.expiresInDays > pb.expiresInDays) violations.push("expiry exceeds parent's window");
  if (pb.velocityLimit && (bounds.velocityLimit === 0 || bounds.velocityLimit > pb.velocityLimit))
    violations.push("velocity exceeds parent's limit (0 = unlimited)");
  if (pb.totalBudget !== null && (bounds.totalBudget === null || bounds.totalBudget > pb.totalBudget))
    violations.push("budget exceeds parent's (null = unlimited)");
  if (pb.totalBudget !== null && bounds.budgetWindowDays < pb.budgetWindowDays)
    // A shorter window cycles the same allowance faster (1000/1d is 30k/30d),
    // which is wider, not narrower. Longer-or-equal windows are required so
    // the child's own bound never exceeds the parent's rate. Chain-wide
    // charging makes the effective authority the intersection regardless.
    violations.push(
      `budget window ${bounds.budgetWindowDays}d is shorter than parent's ` +
        `${pb.budgetWindowDays}d (cycles the allowance faster)`,
    );
  if (violations.length) throw new ValueError("delegation widens scope: " + violations.join("; "));

  child.parentMandateId = parent.mandateId;
  child.parent = parent;
  return child;
}
