/**
 * Evidence anchoring — prove the chain head existed at a point in time.
 *
 * A local hash chain proves internal consistency, but an operator could
 * rewrite history and recompute it. Anchoring `headHash` to an external
 * timestamping service produces independently-verifiable proof of existence
 * at time T.
 */

import type { EvidenceLog } from "./evidence";

/** A timestamped proof that a chain head existed. */
export interface Anchor {
  headHash: string;
  /** The timestamping service's signed proof/receipt. */
  serviceToken: string;
  serviceUrl: string;
}

/**
 * POST the chain head to a timestamping service; store its signed receipt.
 *
 * The service is expected to return JSON with a `token` field — its signed
 * attestation that `headHash` was received at its recorded time. Any
 * RFC 3161-style TSA endpoint or transparency log can be plugged in here.
 */
export async function anchor(log: EvidenceLog, serviceUrl: string, timeoutMs = 5000): Promise<Anchor> {
  const resp = await fetch(serviceUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hash: log.headHash }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const body = (await resp.json().catch(() => ({}))) as { token?: string };
  return { headHash: log.headHash, serviceToken: body.token ?? "", serviceUrl };
}

/**
 * Confirm the chain is intact and still contains the anchored head.
 *
 * Records appended after the anchor are allowed (the log keeps growing);
 * any rewrite or truncation at or before the anchored point fails. Records
 * after the most recent anchor remain protected only by the chain itself,
 * so anchor periodically. Validating `serviceToken` against the
 * timestamping service is service-specific and left to the caller.
 */
export function verifyAnchor(log: EvidenceLog, anchorObj: Anchor): boolean {
  return log.verifyChain({ anchoredHead: anchorObj.headHash });
}
