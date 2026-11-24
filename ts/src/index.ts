/**
 * Mandata — bounded spending authority for AI agents. TypeScript port of the
 * Python reference gate (mandata 0.4.x, MMF-v0.3).
 */

export { anchor, verifyAnchor } from "./anchoring";
export type { Anchor } from "./anchoring";
export { assertScalars, canonicalNumber, lpField, lpFields } from "./canonical";
export { Credential, boundsHash, credentialPayload, issueCredential, verifyCredential } from "./credentials";
export type { CredentialInit } from "./credentials";
export { delegate } from "./delegation";
export { privateKeyFromSeedHex, publicKeyFromHex, publicKeyHex, signHex, verifyHex } from "./ed25519";
export { claim, enforce, settle, void_, void_ as void } from "./enforce";
export type { EnforceOptions, LifecycleOptions, VoidOptions } from "./enforce";
export { LifecycleError, ValueError } from "./errors";
export { EvidenceLog } from "./evidence";
export type { EvidenceFields } from "./evidence";
export {
  Bounds,
  CHECKS,
  Decision,
  EVENTS,
  EvidenceRecord,
  Mandate,
  isFiniteNumber,
} from "./models";
export type { EvidenceRecordInit } from "./models";
export {
  DEFAULT_EXPONENT,
  MAX_AMOUNT,
  exponent,
  fromMinor,
  isMinorAmount,
  registerCurrency,
  toMinor,
} from "./money";
export { railIdempotencyKey } from "./railkey";
export { HTTPRegistry, InMemoryRegistry } from "./revocation";
export type { RevocationRegistry } from "./revocation";
export { SQLiteStore } from "./sqlite";
export { AUTHORIZED, CLAIMED, DENIED, SETTLED, VOIDED, InMemoryStore, TxRecord } from "./state";
export type { StateStore } from "./state";
