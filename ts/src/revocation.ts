/**
 * Revocation registry — distributed revocation at transaction speed.
 *
 * A local `mandate.revoke()` only affects that object. Real deployments need
 * a shared check: many processes enforce the same mandate, and revocation
 * must propagate faster than the next transaction.
 */

/** Shared answer to: is this mandate revoked right now? */
export interface RevocationRegistry {
  isRevoked(mandateId: string): boolean;
  revoke?(mandateId: string): void;
  restore?(mandateId: string): void;
}

/** Process-local shared registry — one source of truth per service instance. */
export class InMemoryRegistry implements RevocationRegistry {
  private revoked = new Set<string>();

  isRevoked(mandateId: string): boolean {
    return this.revoked.has(mandateId);
  }

  revoke(mandateId: string): void {
    this.revoked.add(mandateId);
  }

  restore(mandateId: string): void {
    this.revoked.delete(mandateId);
  }
}
