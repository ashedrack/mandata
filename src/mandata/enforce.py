"""The mandate gate — deterministic enforcement evaluated BEFORE settlement.

Lifecycle (MMF §5.3–5.4): ``enforce()`` decides a transaction identified by
``tx_id`` and, on allow, reserves its amount against every mandate in the
delegation chain (status AUTHORIZED). The rail adapter then calls
``settle()`` once funds move, or ``void()`` if they never will, which
releases the reservation. Retrying ``enforce()`` with the same ``tx_id``
returns the original decision without re-evaluating or reserving again.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager

from .evidence import EvidenceLog
from .models import Decision, Mandate, _is_finite_number
from .money import is_minor_amount
from .state import AUTHORIZED, DENIED, SETTLED, VOIDED, TxRecord

_DEFAULT_LOG = EvidenceLog()


class LifecycleError(ValueError):
    """An invalid settlement-lifecycle transition (unknown tx, settle after void, ...)."""


def enforce(
    mandate: Mandate,
    payee: str,
    amount: int,
    currency: str | None = None,
    at: float | None = None,
    evidence_log: EvidenceLog | None = None,
    store=None,
    registry=None,
    hooks=(),
    credential=None,
    principal_key: str | None = None,
    clock: Callable[[], float] = time.time,
    tx_id: str | None = None,
) -> Decision:
    """Evaluate a proposed transaction against its mandate (MMF §5).

    ``amount`` is an integer in minor units of the mandate currency (see
    ``mandata.money.to_minor``); anything else denies as ``NON_POSITIVE``.

    ``tx_id`` identifies the payment attempt and makes the call idempotent:
    a repeat with the same ``tx_id`` and the same request returns the
    original decision (``replayed=True``) and reserves nothing; a repeat with
    a different request denies as ``TX_CONFLICT``. Production callers MUST
    supply a stable ``tx_id`` (for example the rail's idempotency key); if
    omitted a random one is generated and retries cannot be recognized.

    Time: the gate's own ``clock`` is authoritative. ``at`` exists for
    conformance vectors and replay; production callers MUST NOT pass a
    timestamp supplied by the agent.

    Credential binding: pass ``credential`` and ``principal_key`` to make the
    gate verify the principal's signature first (check ``CREDENTIAL``).

    Every call appends exactly one evidence record (event ``DECISION`` or
    ``REPLAY``). Optional ``hooks`` are callables invoked with the Decision.
    """
    at = clock() if at is None else at
    if not _is_finite_number(at):
        raise ValueError(f"evaluation time must be a finite number, got {at!r}")
    log = evidence_log or _DEFAULT_LOG
    tx_id = tx_id or uuid.uuid4().hex
    chain = mandate.chain()
    ledger = store or mandate._state
    stored_amount = amount if is_minor_amount(amount) else None

    with _serialized(chain, store):
        prior = ledger.get_tx(tx_id)
        replayed = False
        if prior is not None and prior.same_request(
            mandate.mandate_id, payee, stored_amount, currency
        ):
            check, allowed, status, replayed = prior.check, prior.allowed, prior.status, True
            detail, event = f"idempotent replay of {tx_id} ({prior.status})", "REPLAY"
        elif prior is not None:
            check, allowed, status, event = "TX_CONFLICT", False, DENIED, "DECISION"
            detail = f"tx_id {tx_id} was already used for a different request"
        else:
            check, detail = _evaluate(
                mandate,
                chain,
                payee,
                amount,
                currency,
                at,
                store,
                registry,
                credential,
                principal_key,
            )
            allowed, event = check == "WITHIN", "DECISION"
            status = AUTHORIZED if allowed else DENIED
            if allowed:
                for node in chain:
                    (store or node._state).record(node.mandate_id, at, amount, tx_id)
            ledger.put_tx(
                TxRecord(
                    tx_id,
                    mandate.mandate_id,
                    payee,
                    stored_amount,
                    currency,
                    allowed,
                    check,
                    status,
                )
            )
        record = log.append(
            mandate_id=mandate.mandate_id,
            agent=mandate.agent,
            payee=payee,
            amount=stored_amount,
            allowed=allowed,
            check=check,
            timestamp=at,
            detail=detail,
            tx_id=tx_id,
            event=event,
        )

    decision = Decision(
        allowed=allowed,
        reason=detail,
        evidence=record,
        check=check,
        tx_id=tx_id,
        payee=payee,
        amount=stored_amount,
        currency=currency,
        replayed=replayed,
        status=status,
    )
    for hook in hooks:
        hook(decision)
    return decision


def settle(
    mandate: Mandate,
    tx_id: str,
    evidence_log: EvidenceLog | None = None,
    store=None,
    at: float | None = None,
    clock: Callable[[], float] = time.time,
) -> TxRecord:
    """Mark an AUTHORIZED transaction as SETTLED (funds moved on the rail).

    Idempotent: settling an already-settled transaction returns it unchanged.
    Raises LifecycleError for unknown, denied or voided transactions.
    """
    return _transition(mandate, tx_id, SETTLED, evidence_log, store, at, clock)


def void(
    mandate: Mandate,
    tx_id: str,
    evidence_log: EvidenceLog | None = None,
    store=None,
    at: float | None = None,
    clock: Callable[[], float] = time.time,
) -> TxRecord:
    """Release an AUTHORIZED transaction that will never settle.

    Its reserved amount and velocity slot are returned to every mandate in
    the chain. Idempotent for already-voided transactions. A SETTLED
    transaction cannot be voided: refunds are a rail concern (MMF §8).
    """
    return _transition(mandate, tx_id, VOIDED, evidence_log, store, at, clock)


def _transition(mandate, tx_id, target, evidence_log, store, at, clock) -> TxRecord:
    at = clock() if at is None else at
    log = evidence_log or _DEFAULT_LOG
    chain = mandate.chain()
    ledger = store or mandate._state
    with _serialized(chain, store):
        tx = ledger.get_tx(tx_id)
        if tx is None or tx.mandate_id != mandate.mandate_id:
            raise LifecycleError(f"unknown transaction {tx_id} for mandate {mandate.mandate_id}")
        if tx.status == target:
            return tx
        if tx.status != AUTHORIZED:
            raise LifecycleError(f"cannot move {tx_id} from {tx.status} to {target}")
        if target == VOIDED:
            for node in chain:
                (store or node._state).release(node.mandate_id, tx_id)
        tx = dataclasses.replace(tx, status=target)
        ledger.put_tx(tx)
        log.append(
            mandate_id=mandate.mandate_id,
            agent=mandate.agent,
            payee=tx.payee,
            amount=tx.amount,
            allowed=True,
            check=tx.check,
            timestamp=at,
            detail=f"{tx_id} {target.lower()}",
            tx_id=tx_id,
            event="SETTLE" if target == SETTLED else "VOID",
        )
    return tx


@contextmanager
def _serialized(chain: list[Mandate], store) -> Iterator[None]:
    """Hold every chain lock (root first: no deadlock) and every store's
    atomic section, so check-and-record is serializable across threads and,
    for shared stores such as SQLite, across processes."""
    with ExitStack() as stack:
        for node in reversed(chain):
            stack.enter_context(node._lock)
        stores = [store] if store is not None else []
        if store is None:
            for node in reversed(chain):
                if all(node._state is not s for s in stores):
                    stores.append(node._state)
        for s in stores:
            stack.enter_context(s.atomic())
        yield


def _label(node: Mandate, mandate: Mandate) -> str:
    return "mandate" if node is mandate else f"ancestor mandate {node.mandate_id}"


def _spend(node: Mandate, store, at: float, window_days: int) -> tuple[int, int]:
    return (store or node._state).spend(node.mandate_id, at - window_days * 86400)


def _evaluate(mandate, chain, payee, amount, currency, at, store, registry, credential, key):
    """Return (check_id, advisory_detail). First failure wins (MMF §5)."""
    if credential is not None or key is not None:
        if credential is None or key is None:
            return "CREDENTIAL", "credential and principal_key must both be supplied"
        from .credentials import verify_credential

        ok, why = verify_credential(credential, mandate, key, at=at)
        if not ok:
            return "CREDENTIAL", why

    if registry is not None:
        for node in chain:
            try:
                revoked = registry.is_revoked(node.mandate_id)
            except Exception as exc:  # noqa: BLE001 — fail closed: any registry fault denies
                return (
                    "REVOKED_REGISTRY",
                    f"revocation registry unavailable ({exc.__class__.__name__})",
                )
            if revoked:
                return "REVOKED_REGISTRY", f"{_label(node, mandate)} revoked (registry)"

    for node in chain:
        if node.revoked:
            return "REVOKED", f"{_label(node, mandate)} revoked"
        if node.parent_mandate_id and node._parent is None:
            return "REVOKED", f"parent mandate {node.parent_mandate_id} unresolvable"

    for node in chain:
        if node.is_expired(at):
            return "EXPIRED", f"{_label(node, mandate)} expired"

    if not is_minor_amount(amount):
        return "NON_POSITIVE", f"amount {amount!r} is not a positive integer in minor units"

    for node in chain:
        if amount > node.bounds.max_amount:
            return "CAP_EXCEEDED", f"amount {amount} exceeds cap {node.bounds.max_amount}"

    if currency:
        for node in chain:
            if currency != node.bounds.currency:
                return "CURRENCY", f"currency {currency} outside mandate ({node.bounds.currency})"

    for node in chain:
        if node.bounds.allowed_payees and payee not in node.bounds.allowed_payees:
            return "PAYEE", f"payee '{payee}' not in allowed set"

    for node in chain:
        limit = node.bounds.velocity_limit
        if limit and _spend(node, store, at, 1)[0] >= limit:
            return "VELOCITY", f"{_label(node, mandate)} velocity limit {limit} reached"

    for node in chain:
        budget = node.bounds.total_budget
        if budget is not None:
            _, spent = _spend(node, store, at, node.bounds.budget_window_days)
            if spent + amount > budget:
                return "BUDGET", f"{_label(node, mandate)} budget {budget} would be exceeded"

    return "WITHIN", "within mandate"
