/**
 * MMF conformance — run spec/vectors/*.json against the TypeScript gate.
 *
 * Mirrors tests/test_conformance.py in the Python reference: any MMF
 * implementation must produce the same `allowed` + `check` results, the same
 * canonical payloads and the same evidence hashes.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import {
  Bounds,
  Credential,
  EvidenceLog,
  EvidenceRecord,
  InMemoryRegistry,
  LifecycleError,
  Mandate,
  boundsHash,
  canonicalNumber,
  claim,
  credentialPayload,
  delegate,
  enforce,
  lpFields,
  privateKeyFromSeedHex,
  publicKeyHex,
  settle,
  signHex,
  verifyCredential,
  void_,
  type Mandate as MandateT,
  type RevocationRegistry,
} from "../src/index";

const SPEC = path.resolve(__dirname, "../../../spec/vectors");
const load = (name: string) => JSON.parse(readFileSync(path.join(SPEC, name), "utf8"));

const CORE = load("core.json").vectors;
const DELEGATION = load("delegation.json").vectors;
const LIFECYCLE = load("lifecycle.json").vectors;
const EVIDENCE = load("evidence.json").vectors;
const CREDENTIAL: any[] = load("credential.json");

class UnavailableRegistry implements RevocationRegistry {
  isRevoked(): boolean {
    throw new Error("registry unreachable");
  }
}

function buildBounds(b: any): Bounds {
  return new Bounds({
    maxAmount: b.max_amount ?? 10 ** 9,
    currency: b.currency ?? "GBP",
    allowedPayees: b.allowed_payees ?? [],
    expiresInDays: b.expires_in_days ?? 30,
    velocityLimit: b.velocity_limit ?? 0,
    totalBudget: b.total_budget ?? null,
    budgetWindowDays: b.budget_window_days ?? 30,
  });
}

function buildMandate(spec: any): MandateT {
  const m = new Mandate(spec.principal ?? "u", spec.agent ?? "a", buildBounds(spec.bounds ?? {}), {
    issuedAt: spec.issued_at ?? 0,
  });
  if ("mandate_id" in spec) m.mandateId = spec.mandate_id;
  if (spec.revoked) m.revoke();
  return m;
}

function buildRegistry(spec: any): RevocationRegistry | undefined {
  if (spec === undefined || spec === null) return undefined;
  if (spec.unavailable) return new UnavailableRegistry();
  const reg = new InMemoryRegistry();
  for (const mid of spec.revoked ?? []) reg.revoke(mid);
  return reg;
}

/**
 * "amount_json" carries the amount as raw JSON text, so lexical forms such
 * as 1.0, 1e0 and -0 reach the gate exactly as a wire parser would yield
 * them. CPython's json.loads returns `int` for integer literals and `float`
 * otherwise; JS numbers can't carry that distinction, so a non-integer
 * lexical form is passed through unparsed (string, float or NaN stand-in) —
 * anything that fails `isMinorAmount`, exactly as a Python float would.
 */
function amountOf(tx: any): number {
  if (!("amount_json" in tx)) return tx.amount;
  const raw = String(tx.amount_json).trim();
  if (!/^-?\d+$/.test(raw)) {
    const parsed = JSON.parse(raw);
    return typeof parsed === "number" && Number.isInteger(parsed) ? (NaN as number) : (parsed as number);
  }
  return JSON.parse(raw);
}

function run(m: MandateT, tx: any, log: EvidenceLog, registry?: RevocationRegistry) {
  return enforce(m, tx.payee, amountOf(tx), {
    currency: tx.currency ?? null,
    at: tx.at,
    evidenceLog: log,
    registry,
    txId: tx.tx_id,
  });
}

const FIELD: Record<string, string> = {
  tx_id: "txId",
  mandate_id: "mandateId",
  prev_hash: "prevHash",
};
const field = (rec: any, k: string) => rec[FIELD[k] ?? k];

for (const v of CORE) {
  test(`core ${v.id}: ${v.desc}`, () => {
    const m = buildMandate(v.mandate);
    const registry = buildRegistry(v.registry);
    const log = new EvidenceLog();
    for (const p of v.prior_txns ?? []) {
      // seed prior completed (resolved) spend
      m.record(p.at, p.amount, `prior-${p.at}`);
      m.state.resolve(m.mandateId, `prior-${p.at}`);
    }

    if (v.expect.rejected) {
      // malformed input refused before evaluation (§5.6)
      assert.throws(() => run(m, v.tx, log, registry));
      assert.equal(log.records.length, 0);
      return;
    }

    const d = run(m, v.tx, log, registry);
    assert.deepEqual([d.allowed, d.check], [v.expect.allowed, v.expect.check], d.reason);
    assert.equal(d.evidence.check, d.check);

    if ("then_tx" in v) {
      const d2 = run(m, v.then_tx, log, registry);
      assert.deepEqual([d2.allowed, d2.check], [v.then_expect.allowed, v.then_expect.check], d2.reason);
    }

    assert.equal(log.records.length, "then_tx" in v ? 2 : 1); // denials recorded too
    assert.ok(log.verifyChain());
  });
}

for (const v of DELEGATION) {
  test(`delegation ${v.id}: ${v.desc}`, () => {
    const mandates: Record<string, MandateT> = {
      parent: buildMandate({ bounds: v.parent, issued_at: 0 }),
    };
    const log = new EvidenceLog();
    for (const step of v.steps) {
      if ("revoke" in step) {
        mandates[step.revoke].revoke();
      } else if ("delegate" in step) {
        const source = mandates[step.from ?? "parent"];
        const bounds = buildBounds(step.bounds);
        if (step.expect === "invalid") {
          assert.throws(() => delegate(source, step.delegate, bounds, 1000));
        } else {
          const child = delegate(source, step.delegate, bounds, 1000);
          assert.equal(child.parentMandateId, source.mandateId);
          mandates[step.delegate] = child;
        }
      } else if ("claim" in step) {
        const c = step.claim;
        claim(mandates[c.as], c.tx_id, c.payee, c.amount, c.currency, {
          evidenceLog: log,
          at: step.at,
        });
      } else if ("settle" in step) {
        const c = step.settle;
        settle(mandates[c.as], c.tx_id, { evidenceLog: log, at: step.at });
      } else {
        const tx = step.tx;
        const d = run(mandates[tx.as], tx, log);
        assert.deepEqual([d.allowed, d.check], [step.expect.allowed, step.expect.check], d.reason);
      }
    }
    assert.ok(log.verifyChain());
  });
}

for (const v of LIFECYCLE) {
  test(`lifecycle ${v.id}: ${v.desc}`, () => {
    const m = buildMandate(v.mandate);
    const mandates: Record<string, MandateT> = { m };
    if ("mandate2" in v) {
      // a second mandate sharing the gate-wide ledger (MMF §5.3)
      mandates.m2 = buildMandate(v.mandate2);
      mandates.m2.state = m.state;
    }
    const registry = buildRegistry(v.registry);
    const log = new EvidenceLog();
    for (const step of v.steps) {
      const expect = step.expect;
      if ("tx" in step) {
        const d = run(mandates[step.tx.as ?? "m"], step.tx, log);
        const got: Record<string, unknown> = {
          allowed: d.allowed,
          check: d.check,
          replayed: d.replayed,
          status: d.status,
        };
        for (const [k, val] of Object.entries(expect)) {
          if (k !== "record") assert.equal(got[k], val, `${v.id}: ${k}`);
        }
        for (const [k, val] of Object.entries(expect.record ?? {})) {
          assert.equal(field(log.records.at(-1), k), val, `${v.id}: record.${k}`);
        }
        continue;
      }
      const before = log.records.length;
      try {
        let tx;
        if ("claim" in step) {
          const c = step.claim;
          tx = claim(m, c.tx_id, c.payee, c.amount, c.currency, {
            evidenceLog: log,
            at: step.at,
            registry,
          });
        } else if ("settle" in step) {
          tx = settle(m, step.settle, { evidenceLog: log, at: step.at });
        } else {
          tx = void_(m, step.void, {
            evidenceLog: log,
            at: step.at,
            cancellation: step.cancellation,
          });
        }
        assert.ok(!expect.error, `${v.id}: expected rejection`);
        assert.equal(tx.status, expect.status);
      } catch (e) {
        assert.ok(e instanceof LifecycleError, `${v.id}: unexpected error ${e}`);
        assert.ok(expect.error, `${v.id}: unexpected rejection`);
        // rejected transitions append exactly one REJECTED evidence record
        assert.equal(log.records.length, before + 1);
        const rec = log.records.at(-1)!;
        assert.equal(rec.event, "REJECTED");
        if ("check" in expect) assert.equal(rec.check, expect.check); // <OP>:<CAUSE> (MMF §5.6)
        for (const [k, val] of Object.entries(expect.record ?? {})) {
          assert.equal(field(rec, k), val, `${v.id}: record.${k}`);
        }
      }
    }
    assert.deepEqual(
      log.records.map((r) => r.event),
      v.events,
    );
    assert.ok(log.verifyChain());
  });
}

for (const v of EVIDENCE) {
  test(`evidence ${v.id}: ${v.desc}`, () => {
    // Canonical serialization + SHA-256 must match byte-for-byte (MMF §5.2).
    let prev = "0".repeat(64);
    v.records.forEach((r: any, i: number) => {
      const rec = new EvidenceRecord({
        event: r.event,
        txId: r.tx_id,
        mandateId: r.mandate_id,
        agent: r.agent,
        payee: r.payee,
        amount: r.amount,
        allowed: r.allowed,
        check: r.check,
        timestamp: r.timestamp,
        currency: r.currency,
        status: r.status,
        detail: r.detail,
        reference: r.reference,
        prevHash: prev,
      });
      assert.equal(rec.canonicalPayload(), v.expect[i].payload, `${v.id}: payload`);
      assert.equal(rec.hash, v.expect[i].hash, `${v.id}: hash`);
      prev = rec.hash;
    });
    assert.equal(prev, v.head);
  });
}

for (const v of CREDENTIAL) {
  test(`credential ${v.id}: ${v.desc}`, () => {
    // Golden credential vectors: canonical bytes, bounds_hash and the
    // deterministic Ed25519 signature must reproduce exactly for C01, and
    // every negative credential (C02+) MUST fail verification (MMF §7).
    const i = v.inputs ?? v.mandate_inputs;
    const b = i.bounds;
    const bounds = new Bounds({
      maxAmount: b.max_amount,
      currency: b.currency,
      allowedPayees: b.allowed_payees,
      expiresInDays: b.expires_in_days,
      velocityLimit: b.velocity_limit,
      totalBudget: b.total_budget,
      budgetWindowDays: b.budget_window_days,
    });
    const m = new Mandate(i.principal, i.agent, bounds, {
      mandateId: i.mandate_id,
      issuedAt: i.issued_at,
    });
    m.parentMandateId = i.parent_mandate_id || null;

    const boundsPayload = Buffer.from(
      lpFields(
        m.principal,
        canonicalNumber(m.issuedAt),
        m.parentMandateId ?? "",
        String(bounds.maxAmount),
        bounds.currency,
        [...bounds.allowedPayees].sort().join(","),
        String(bounds.expiresInDays),
        String(bounds.velocityLimit),
        bounds.totalBudget === null ? "" : String(bounds.totalBudget),
        String(bounds.budgetWindowDays),
      ),
      "utf8",
    );
    const exp = v.expected;
    if ("credential" in v) {
      // negative vectors: verification MUST fail as specified
      const c = v.credential;
      const cred = new Credential({
        mandateId: c.mandate_id,
        agent: c.agent,
        boundsHash: c.bounds_hash,
        expiresAt: c.expires_at,
        signature: c.signature,
        publicKey: c.public_key,
      });
      const [ok] = verifyCredential(cred, m, v.principal_public_key_hex, m.issuedAt + 100);
      assert.equal(ok, exp.valid, `${v.id}: expected valid=${exp.valid}`);
      return;
    }
    assert.equal(boundsPayload.toString("hex"), exp.bounds_payload_hex);
    assert.equal(boundsHash(m), exp.bounds_hash);
    const payload = credentialPayload(m.mandateId, m.agent, exp.bounds_hash, m.expiresAt);
    assert.equal(payload.toString("hex"), exp.payload_hex);
    const sk = privateKeyFromSeedHex(v.seed_hex);
    assert.equal(publicKeyHex(sk), v.public_key_hex);
    assert.equal(signHex(sk, payload), exp.signature);
  });
}
