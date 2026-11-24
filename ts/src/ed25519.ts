/**
 * Ed25519 signing over raw hex keys — stdlib `node:crypto` only, matching
 * PyNaCl's raw-key API so signatures reproduce byte-for-byte.
 */

import {
  createPrivateKey,
  createPublicKey,
  sign,
  verify,
  type KeyObject,
} from "node:crypto";

// PKCS#8 DER prefix for a 32-byte Ed25519 seed.
const PKCS8_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");

/** Private key from a raw 32-byte Ed25519 seed (hex). */
export function privateKeyFromSeedHex(seedHex: string): KeyObject {
  const der = Buffer.concat([PKCS8_PREFIX, Buffer.from(seedHex, "hex")]);
  return createPrivateKey({ key: der, format: "der", type: "pkcs8" });
}

/** Hex verify key of a private key (the raw 32-byte public key). */
export function publicKeyHex(privateKey: KeyObject): string {
  const jwk = createPublicKey(privateKey).export({ format: "jwk" });
  return Buffer.from(jwk.x as string, "base64url").toString("hex");
}

/** Public key object from a raw 32-byte verify key (hex). */
export function publicKeyFromHex(publicKeyHex: string): KeyObject {
  return createPublicKey({
    key: { kty: "OKP", crv: "Ed25519", x: Buffer.from(publicKeyHex, "hex").toString("base64url") },
    format: "jwk",
  });
}

/** Deterministic Ed25519 signature over `message` (hex output). */
export function signHex(privateKey: KeyObject, message: Buffer | string): string {
  return sign(null, Buffer.isBuffer(message) ? message : Buffer.from(message, "utf8"), privateKey).toString("hex");
}

/** Verify a hex signature over `message` with a raw hex verify key. */
export function verifyHex(publicKeyHex: string, message: Buffer | string, signatureHex: string): boolean {
  const msg = Buffer.isBuffer(message) ? message : Buffer.from(message, "utf8");
  return verify(null, msg, publicKeyFromHex(publicKeyHex), Buffer.from(signatureHex, "hex"));
}
