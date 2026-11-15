"""Core mandate model: principal -> agent -> credential -> transaction -> evidence."""

from __future__ import annotations

import hashlib
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from .money import is_minor_amount

# Normative check identifiers (MMF §5), in evaluation order. TX_CONFLICT is
# the pre-evaluation idempotency rule (MMF §5.3), not one of the ten checks.
CHECKS = (
    "TX_CONFLICT",
    "CREDENTIAL",
    "REVOKED_REGISTRY",
    "REVOKED",
    "EXPIRED",
    "NON_POSITIVE",
    "CAP_EXCEEDED",
    "CURRENCY",
    "PAYEE",
    "VELOCITY",
    "BUDGET",
    "WITHIN",
)

# Evidence events (MMF §5.4): a gate decision, an idempotent replay of one,
# and the two settlement-lifecycle transitions of an authorization.
EVENTS = ("DECISION", "REPLAY", "SETTLE", "VOID")


def canonical_number(x: float) -> str:
    """Language-neutral decimal rendering used in every MMF hash (MMF §5.2).

    Shortest round-trip decimal, no exponent, no trailing zeros:
    100.0 -> "100", 49.990 -> "49.99", 1e-7 -> "0.0000001".
    """
    if isinstance(x, bool) or not math.isfinite(float(x)):
        raise ValueError(f"non-canonicalizable number: {x!r}")
    d = Decimal(x if isinstance(x, int) else repr(float(x))).normalize()
    s = format(d, "f")
    return "0" if s in ("-0", "") else s


def _is_finite_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


@dataclass(frozen=True)
class Bounds:
    """The permitted envelope an agent must transact within.

    Amounts (``max_amount``, ``total_budget``) are integers in the minor unit
    of ``currency`` — pence for GBP, cents for USD (see ``mandata.money``).
    Malformed bounds are rejected at construction (fail-safe defaults): a
    mandate that cannot be evaluated is never created.
    """

    max_amount: int
    currency: str = "GBP"
    allowed_payees: tuple[str, ...] | list[str] = ()
    expires_in_days: int = 30
    velocity_limit: int = 0  # max transactions per trailing 24h; 0 = unlimited
    total_budget: int | None = None  # cumulative spend cap over window, minor units
    budget_window_days: int = 30  # window for total_budget

    def __post_init__(self) -> None:
        errors = []
        if not is_minor_amount(self.max_amount):
            errors.append("max_amount must be an integer > 0 in minor units")
        if not isinstance(self.currency, str) or not self.currency:
            errors.append("currency must be a non-empty string")
        if isinstance(self.allowed_payees, str) or not all(
            isinstance(p, str) and p for p in self.allowed_payees
        ):
            errors.append("allowed_payees must be a list of non-empty strings")
        if not isinstance(self.expires_in_days, int) or self.expires_in_days < 1:
            errors.append("expires_in_days must be an integer >= 1")
        if not isinstance(self.velocity_limit, int) or self.velocity_limit < 0:
            errors.append("velocity_limit must be an integer >= 0")
        if self.total_budget is not None and not is_minor_amount(self.total_budget):
            errors.append("total_budget must be null or an integer > 0 in minor units")
        if not isinstance(self.budget_window_days, int) or self.budget_window_days < 1:
            errors.append("budget_window_days must be an integer >= 1")
        if errors:
            raise ValueError("invalid bounds: " + "; ".join(errors))


@dataclass
class Mandate:
    """A principal's grant of bounded spending authority to an agent."""

    principal: str
    agent: str
    bounds: Bounds
    mandate_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    issued_at: float = field(default_factory=time.time)
    revoked: bool = False
    parent_mandate_id: str | None = None
    _state: object = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # Resolved parent (set by delegate()); the gate evaluates the whole chain.
    _parent: Mandate | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._state is None:
            from .state import InMemoryStore

            self._state = InMemoryStore()

    @property
    def expires_at(self) -> float:
        return self.issued_at + self.bounds.expires_in_days * 86400

    def is_expired(self, at: float | None = None) -> bool:
        return (time.time() if at is None else at) > self.expires_at

    def chain(self) -> list[Mandate]:
        """This mandate followed by its resolved ancestors (leaf -> root)."""
        out, node = [], self
        while node is not None:
            out.append(node)
            node = node._parent
        return out

    def revoke(self) -> None:
        self.revoked = True

    def record(self, at: float, amount: int, tx_id: str | None = None) -> None:
        self._state.record(self.mandate_id, at, amount, tx_id or uuid.uuid4().hex)

    def spend_in_window(self, at: float) -> tuple[int, int]:
        since = at - self.bounds.budget_window_days * 86400
        return self._state.spend(self.mandate_id, since)


@dataclass(frozen=True)
class Decision:
    """The outcome of a mandate gate check.

    ``check`` is the normative MMF check identifier; ``reason`` is advisory,
    human-readable text. ``payee``/``amount``/``currency`` are exactly what
    the gate evaluated; adapters MUST settle only an instruction that
    ``binds()`` to them (MMF §5.5). ``replayed`` is true when the decision
    was returned from the idempotency ledger rather than evaluated;
    ``status`` is the transaction's lifecycle state (AUTHORIZED, SETTLED,
    VOIDED or DENIED) at the time of the call.
    """

    allowed: bool
    reason: str
    evidence: EvidenceRecord
    check: str = "WITHIN"
    tx_id: str = ""
    payee: str = ""
    amount: int | None = None
    currency: str | None = None
    replayed: bool = False
    status: str = ""

    def binds(self, payee: str, amount: int, currency: str | None = None) -> bool:
        """True iff a settlement instruction matches what the gate approved
        and the authorization is still open (not yet settled or voided), so a
        retried request can never be settled twice."""
        return (
            self.allowed
            and self.status == "AUTHORIZED"
            and payee == self.payee
            and amount == self.amount
            and (currency is None or self.currency is None or currency == self.currency)
        )


@dataclass(frozen=True)
class EvidenceRecord:
    """An auditable record of a gate decision or lifecycle event.

    The canonical fields are covered by the hash; ``detail`` (advisory text)
    is not, so implementations may word it differently and still produce
    identical chains. ``signature`` is the gate's Ed25519 signature over the
    hash (authenticity, MMF §5.2); it is not part of the hash itself.
    """

    mandate_id: str
    agent: str
    payee: str
    amount: int | None
    allowed: bool
    check: str
    timestamp: float
    prev_hash: str
    detail: str = ""
    tx_id: str = ""
    event: str = "DECISION"
    signature: str = ""

    @property
    def reason(self) -> str:
        """Backward-compatible alias: advisory detail, falling back to the check ID."""
        return self.detail or self.check

    def canonical_payload(self) -> str:
        """MMF §5.2 canonical serialization — identical bytes in every implementation."""
        amount = str(self.amount) if is_minor_amount(self.amount) else "invalid"
        return "|".join(
            [
                self.event,
                self.tx_id,
                self.mandate_id,
                self.agent,
                self.payee,
                amount,
                "true" if self.allowed else "false",
                self.check,
                canonical_number(self.timestamp),
                self.prev_hash,
            ]
        )

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.canonical_payload().encode("utf-8")).hexdigest()
