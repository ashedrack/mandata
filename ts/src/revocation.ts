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
  revoke?(mandateId: string): void | Promise<void>;
  restore?(mandateId: string): void | Promise<void>;
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

/**
 * Remote registry — checks an external revocation service over HTTP.
 *
 * The gate's `isRevoked` is synchronous, so this registry works as a
 * prefetching cache: call `refresh(mandateIds)` before gate evaluation to
 * pull statuses; `isRevoked` then answers from the cache. A mandate id that
 * was never refreshed throws — the gate fails closed on it (MMF §5).
 *
 * GET `{base}/revoked/{mandate_id}` expecting `{"revoked": true|false}`;
 * `revoke`/`restore` POST `{"revoked": true|false}` to the same path.
 */
export class HTTPRegistry implements RevocationRegistry {
  private cache = new Map<string, boolean>();

  constructor(
    private baseUrl: string,
    private opts: { timeoutMs?: number; headers?: Record<string, string> } = {},
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  /** Pull current revocation status for the given mandate ids into the cache. */
  async refresh(mandateIds: string[]): Promise<void> {
    for (const id of mandateIds) {
      this.cache.set(id, await this.fetchStatus(id));
    }
  }

  private async request(method: string, path: string, payload?: unknown): Promise<unknown> {
    const resp = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: { "Content-Type": "application/json", ...this.opts.headers },
      body: payload === undefined ? undefined : JSON.stringify(payload),
      signal: AbortSignal.timeout(this.opts.timeoutMs ?? 2000),
    });
    const text = await resp.text();
    return text ? JSON.parse(text) : {};
  }

  private async fetchStatus(mandateId: string): Promise<boolean> {
    const body = await this.request("GET", `/revoked/${mandateId}`);
    const revoked = (body as { revoked?: unknown }).revoked;
    if (typeof revoked !== "boolean") {
      // Malformed answer: throw so the gate fails closed (MMF §5).
      throw new TypeError(`registry returned malformed revocation status: ${JSON.stringify(body)}`);
    }
    return revoked;
  }

  isRevoked(mandateId: string): boolean {
    if (!this.cache.has(mandateId)) {
      throw new TypeError(`revocation status for ${mandateId} not refreshed — call refresh() first`);
    }
    return this.cache.get(mandateId)!;
  }

  async revoke(mandateId: string): Promise<void> {
    await this.request("POST", `/revoked/${mandateId}`, { revoked: true });
    this.cache.set(mandateId, true);
  }

  async restore(mandateId: string): Promise<void> {
    await this.request("POST", `/revoked/${mandateId}`, { revoked: false });
    this.cache.set(mandateId, false);
  }
}
