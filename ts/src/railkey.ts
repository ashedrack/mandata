/**
 * Gate-scoped rail idempotency keys (MMF §5.4).
 *
 * When several gates share one rail idempotency namespace, a bare `txId` can
 * collide across gates. The key below is a deterministic, collision-resistant
 * function of the gate's identity and the `txId`: stable across retries and
 * recomputable by an auditor who retains the `gateId`.
 */

import { createHash } from "node:crypto";

import { lpFields } from "./canonical";
import { ValueError } from "./errors";

/**
 * Derive the rail idempotency key for `txId` under `gateId`.
 *
 * `hashed=true` (default): lower-case hex SHA-256 of the length-prefixed
 * form `len:gateId|len:txId` — 64 ASCII characters, within the key-length
 * limits of common rails. `hashed=false` returns the length-prefixed form
 * itself, injective but unbounded in length.
 */
export function railIdempotencyKey(gateId: string, txId: string, hashed = true): string {
  if (!gateId) throw new ValueError("gate_id must be a non-empty, deployment-unique string");
  const form = lpFields(gateId, txId);
  return hashed ? createHash("sha256").update(form, "utf8").digest("hex") : form;
}
