"""Amounts as integers in minor units (MMF-v0.3 §3.1).

The gate compares and sums integers only: no binary floating point ever
reaches an authorization decision. Conversion from decimal major-unit
values happens at the edge (adapters), here, and refuses — never rounds —
values with more precision than the currency's minor unit.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

# ISO 4217 minor-unit exponents that differ from the default of 2.
_EXPONENTS: dict[str, int] = {
    **dict.fromkeys(
        [
            "BIF",
            "CLP",
            "DJF",
            "GNF",
            "ISK",
            "JPY",
            "KMF",
            "KRW",
            "PYG",
            "RWF",
            "UGX",
            "UYI",
            "VND",
            "VUV",
            "XAF",
            "XOF",
            "XPF",
        ],
        0,
    ),
    **dict.fromkeys(["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"], 3),
    "CLF": 4,
    "UYW": 4,
}
DEFAULT_EXPONENT = 2
# Largest amount every implementation can represent exactly (IEEE-754 double
# safe-integer limit), so a JavaScript gate and a Python gate never disagree.
MAX_AMOUNT = 2**53 - 1


def register_currency(code: str, exponent: int) -> None:
    """Declare the minor-unit exponent of a non-ISO currency (e.g. ``USDC`` = 6)."""
    if not isinstance(exponent, int) or isinstance(exponent, bool) or not 0 <= exponent <= 18:
        raise ValueError("exponent must be an integer in [0, 18]")
    _EXPONENTS[code.upper()] = exponent


def exponent(currency: str) -> int:
    """Number of decimal places in one major unit of ``currency``."""
    return _EXPONENTS.get(currency.upper(), DEFAULT_EXPONENT)


def is_minor_amount(x) -> bool:
    """True for a valid wire amount: an integer (not bool) in [1, 2^53 - 1]."""
    return isinstance(x, int) and not isinstance(x, bool) and 0 < x <= MAX_AMOUNT


def to_minor(value: str | int | Decimal, currency: str) -> int:
    """Convert a major-unit decimal (``"49.99"``) to minor units (``4999``).

    Floats are refused: pass the original decimal string from the rail.
    Values with more decimals than the currency allows raise ValueError
    rather than being rounded, so a conversion can never change an amount.
    """
    if isinstance(value, (float, bool)):
        raise TypeError("pass amounts as decimal strings, ints or Decimal — never float")
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a decimal amount: {value!r}") from exc
    if not d.is_finite():
        raise ValueError(f"non-finite amount: {value!r}")
    scaled = d.scaleb(exponent(currency))
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{value!r} has more precision than {currency} allows")
    return int(scaled)


def from_minor(amount: int, currency: str) -> str:
    """Render minor units as a major-unit decimal string (``4999`` -> ``"49.99"``)."""
    e = exponent(currency)
    return format(Decimal(amount).scaleb(-e), f".{e}f")
