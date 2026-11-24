/**
 * Amounts as integers in minor units (MMF-v0.3 §3.1).
 *
 * The gate compares and sums integers only: no binary floating point ever
 * reaches an authorization decision. Conversion from decimal major-unit
 * values happens at the edge (adapters), here, and refuses — never rounds —
 * values with more precision than the currency's minor unit.
 */

import { ValueError } from "./errors";

/** ISO 4217 minor-unit exponents that differ from the default of 2. */
const EXPONENTS: Record<string, number> = {
  // exponent 0
  BIF: 0, CLP: 0, DJF: 0, GNF: 0, ISK: 0, JPY: 0, KMF: 0, KRW: 0, PYG: 0,
  RWF: 0, UGX: 0, UYI: 0, VND: 0, VUV: 0, XAF: 0, XOF: 0, XPF: 0,
  // exponent 3
  BHD: 3, IQD: 3, JOD: 3, KWD: 3, LYD: 3, OMR: 3, TND: 3,
  CLF: 4, UYW: 4,
};
export const DEFAULT_EXPONENT = 2;

/**
 * Largest amount every implementation can represent exactly (IEEE-754 double
 * safe-integer limit), so a JavaScript gate and a Python gate never disagree.
 */
export const MAX_AMOUNT = 2 ** 53 - 1;

/** Declare the minor-unit exponent of a non-ISO currency (e.g. "USDC" = 6). */
export function registerCurrency(code: string, exp: number): void {
  if (!Number.isInteger(exp) || exp < 0 || exp > 18) {
    throw new ValueError("exponent must be an integer in [0, 18]");
  }
  EXPONENTS[code.toUpperCase()] = exp;
}

/** Number of decimal places in one major unit of `currency`. */
export function exponent(currency: string): number {
  if (typeof currency !== "string" || !currency) {
    throw new ValueError(`currency must be a non-empty string, got ${currency}`);
  }
  return EXPONENTS[currency.toUpperCase()] ?? DEFAULT_EXPONENT;
}

/** True for a valid wire amount: a safe integer in [1, 2^53 - 1]. */
export function isMinorAmount(x: unknown): x is number {
  return typeof x === "number" && Number.isSafeInteger(x) && x > 0 && x <= MAX_AMOUNT;
}

const DEC_RE = /^([+-]?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/;

/**
 * Convert a major-unit decimal (`"49.99"`) to minor units (`4999`).
 *
 * Non-integer numbers are refused: pass the original decimal string from the
 * rail. Values with more decimals than the currency allows throw rather than
 * being rounded, so a conversion can never change an amount.
 */
export function toMinor(value: string | number | bigint, currency: string): number {
  if (typeof value === "number" && !Number.isInteger(value)) {
    throw new TypeError("pass amounts as decimal strings, ints or Decimal — never float");
  }
  const m = DEC_RE.exec(String(value).trim());
  if (!m) throw new ValueError(`not a decimal amount: ${value}`);
  const shift = exponent(currency) + (m[4] ? Number(m[4]) : 0) - (m[3]?.length ?? 0);
  let scaled = BigInt((m[1] === "-" ? "-" : "") + m[2] + (m[3] ?? ""));
  if (shift >= 0) {
    scaled *= 10n ** BigInt(shift);
  } else {
    const div = 10n ** BigInt(-shift);
    if (scaled % div !== 0n) {
      throw new ValueError(`${value} has more precision than ${currency} allows`);
    }
    scaled /= div;
  }
  return Number(scaled);
}

/** Render minor units as a major-unit decimal string (`4999` -> `"49.99"`). */
export function fromMinor(amount: number, currency: string): string {
  const e = exponent(currency);
  const neg = amount < 0;
  const digits = String(Math.abs(amount)).padStart(e + 1, "0");
  const head = digits.slice(0, digits.length - e) || "0";
  const tail = e ? "." + digits.slice(-e) : "";
  return (neg ? "-" : "") + head + tail;
}
