/**
 * Credential signing — bind a mandate cryptographically to an agent identity.
 *
 * A credential is the portable, verifiable form of a mandate: the principal's
 * key signs (mandate_id, agent, bounds_hash, expiry). Anyone can verify that
 * a transaction was proposed under a mandate the principal actually issued.
 */

import { createHash } from "node:crypto";

import { canonicalNumber, lpFields } from "./canonical";
import { privateKeyFromSeedHex, publicKeyHex, signHex, verifyHex } from "./ed25519";
import type { Mandate } from "./models";

/**
 * Canonical hash over every authority-bearing field of the mandate.
 *
 * Covers all bounds plus principal, issuance time and delegation link, so no
 * field that widens or redirects authority can change without breaking the
 * signature (MMF §6).
 */
export function boundsHash(mandate: Mandate): string {
  const b = mandate.bounds;
  const payload = lpFields(
    mandate.principal,
    canonicalNumber(mandate.issuedAt),
    mandate.parentMandateId ?? "",
    String(b.maxAmount),
    b.currency,
    [...b.allowedPayees].sort().join(","),
    String(b.expiresInDays),
    String(b.velocityLimit),
    b.totalBudget === null ? "" : String(b.totalBudget),
    String(b.budgetWindowDays),
  );
  return createHash("sha256").update(payload, "utf8").digest("hex");
}

/** Canonical credential payload: what the principal's signature covers. */
export function credentialPayload(
  mandateId: string,
  agent: string,
  bh: string,
  expiresAt: number,
): Buffer {
  return Buffer.from(lpFields(mandateId, agent, bh, canonicalNumber(expiresAt)), "utf8");
}

export interface CredentialInit {
  mandateId: string;
  agent: string;
  boundsHash: string;
  expiresAt: number;
  signature: string;
  publicKey: string;
}

/** A signed binding: principal's key vouches for (mandate, agent, bounds, expiry). */
export class Credential {
  mandateId: string;
  agent: string;
  boundsHash: string;
  expiresAt: number;
  signature: string; // hex
  publicKey: string; // hex — principal's verify key

  constructor(init: CredentialInit) {
    this.mandateId = init.mandateId;
    this.agent = init.agent;
    this.boundsHash = init.boundsHash;
    this.expiresAt = init.expiresAt;
    this.signature = init.signature;
    this.publicKey = init.publicKey;
  }

  payload(): Buffer {
    return credentialPayload(this.mandateId, this.agent, this.boundsHash, this.expiresAt);
  }
}

/** Principal signs a credential for a mandate + agent. `seedHex` is the principal's raw Ed25519 seed. */
export function issueCredential(mandate: Mandate, seedHex: string): Credential {
  const key = privateKeyFromSeedHex(seedHex);
  const bh = boundsHash(mandate);
  const expiresAt = mandate.expiresAt;
  return new Credential({
    mandateId: mandate.mandateId,
    agent: mandate.agent,
    boundsHash: bh,
    expiresAt,
    signature: signHex(key, credentialPayload(mandate.mandateId, mandate.agent, bh, expiresAt)),
    publicKey: publicKeyHex(key),
  });
}

/**
 * Verify a credential against a mandate + the principal's known public key.
 *
 * Checks: signature validity, key match, mandate/agent binding, bounds
 * integrity, expiry. Returns `[ok, reason]`.
 */
export function verifyCredential(
  credential: Credential,
  mandate: Mandate,
  expectedPublicKey: string,
  at?: number,
): [boolean, string] {
  const now = at ?? Date.now() / 1000;
  if (credential.publicKey !== expectedPublicKey) return [false, "credential signed by unexpected key"];
  if (credential.mandateId !== mandate.mandateId) return [false, "credential is for a different mandate"];
  if (credential.agent !== mandate.agent) return [false, "credential is for a different agent"];
  if (credential.boundsHash !== boundsHash(mandate)) return [false, "bounds altered since credential issued"];
  if (now > credential.expiresAt) return [false, "credential expired"];
  try {
    if (!verifyHex(credential.publicKey, credential.payload(), credential.signature)) {
      return [false, "invalid signature"];
    }
  } catch {
    return [false, "invalid signature"];
  }
  return [true, "credential valid"];
}
