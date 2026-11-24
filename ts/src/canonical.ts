/**
 * Canonical serialization primitives (MMF §5.2, §5.6).
 *
 * Identical bytes in every conforming implementation — this is what makes
 * evidence hashes, bounds hashes and credential payloads comparable across
 * the Python and TypeScript gates.
 */

import { ValueError } from "./errors";

function repr(value: unknown): string {
  return typeof value === "string" ? `'${value}'` : String(value);
}

/**
 * Refuse strings that cannot enter canonical serialization (MMF §5.6): every
 * string bound for a canonical field MUST be a Unicode scalar sequence. An
 * unpaired surrogate has no well-formed UTF-8 encoding — a lone high or low
 * surrogate is malformed input, rejected before evaluation. Recorded
 * unchecked, such a value could never be serialized and would fail every
 * subsequent chain hash instead.
 */
export function assertScalars(name: string, value: unknown): void {
  if (value === undefined || value === null) return;
  if (typeof value !== "string") {
    throw new TypeError(`${name} must be a string, got ${repr(value)}`);
  }
  for (let i = 0; i < value.length; i++) {
    const c = value.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const low = value.charCodeAt(i + 1);
      if (!(low >= 0xdc00 && low <= 0xdfff)) {
        throw new ValueError(`${name} is not a Unicode scalar sequence: ${repr(value)}`);
      }
      i++;
    } else if (c >= 0xdc00 && c <= 0xdfff) {
      throw new ValueError(`${name} is not a Unicode scalar sequence: ${repr(value)}`);
    }
  }
}

function utf8Length(value: string): number {
  return Buffer.byteLength(value, "utf8");
}

/**
 * One length-prefixed canonical field: `<utf-8 byte length>:<utf-8>`.
 *
 * Self-delimiting, so any field value (including `|`) is unambiguous. The
 * exact Unicode scalar sequence is preserved — no normalization — matching
 * §5.3's exact request equality.
 */
export function lpField(value: string): string {
  assertScalars("canonical field", value);
  return `${utf8Length(value)}:${value}`;
}

/** Length-prefixed fields joined by `|` bytes. */
export function lpFields(...values: string[]): string {
  return values.map(lpField).join("|");
}

/**
 * Language-neutral decimal rendering used in every MMF hash (MMF §5.2).
 *
 * Shortest round-trip decimal, no exponent, no trailing zeros:
 * 100 -> "100", 49.99 -> "49.99", 1e-7 -> "0.0000001".
 *
 * JS `Number.prototype.toString` already produces the shortest round-trip
 * digit sequence (same algorithm as Python repr); the only work is expanding
 * the exponent notation JS emits below 1e-6 or above 1e21.
 */
export function canonicalNumber(x: number): string {
  if (typeof x !== "number" || !Number.isFinite(x)) {
    throw new ValueError(`non-canonicalizable number: ${repr(x)}`);
  }
  let s = x.toString();
  if (s.includes("e") || s.includes("E")) s = expandExponent(s);
  if (s === "-0" || s === "") return "0";
  return s;
}

function expandExponent(s: string): string {
  const m = /^(-?)(\d+)(?:\.(\d+))?[eE]([+-]?\d+)$/.exec(s)!;
  const sign = m[1];
  const digits = m[2] + (m[3] ?? "");
  const point = m[2].length + Number(m[4]);
  let out: string;
  if (point <= 0) out = "0." + "0".repeat(-point) + digits;
  else if (point >= digits.length) out = digits + "0".repeat(point - digits.length);
  else out = digits.slice(0, point) + "." + digits.slice(point);
  // Shortest round-trip digits never carry fractional trailing zeros, so at
  // most a bare "." survives the expansion.
  if (out.endsWith(".")) out = out.slice(0, -1);
  return sign + out;
}
