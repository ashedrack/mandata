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
from .money import MAX_AMOUNT, is_minor_amount
from .state import AUTHORIZED, CLAIMED, DENIED, SETTLED, VOIDED, TxRecord

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
    chain, chain_error = mandate.resolve_chain()
    ledger = store or mandate._state
    stored_amount = amount if is_minor_amount(amount) else None

    with _serialized(chain, store):
        # The ledger lookup is the FIRST authoritative operation inside the
        # store transaction (MMF §5.3). Chain resolution before the locks is
        # pure — links are immutable once delegate() sets them — but we
        # re-resolve inside the serialization and fail closed if the chain
        # identity changed, so a replay can never be shadowed by graph edits.
        prior = ledger.get_tx(tx_id)
        if prior is None:
            # Authoritative resolution applies to NEW transactions only: a
            # decided tx_id (replay or conflict) never depends on the
            # current chain. The lock set was planned from the preliminary
            # resolution; an identity change means link immutability was
            # violated, so fail closed rather than acquire further locks.
            chain2, chain2_err2 = mandate.resolve_chain()
            if [n.mandate_id for n in (chain2 or [])] != [n.mandate_id for n in chain]:
                chain_error = chain_error or chain2_err2 or "delegation chain mutated during evaluation"
                chain = chain2 or [mandate]
        replayed = False
        if prior is not None and prior.same_request(
            mandate.mandate_id, payee, stored_amount, currency
        ):
            check, allowed, status, replayed = prior.check, prior.allowed, prior.status, True
            detail, event = f"idempotent replay of {tx_id} ({prior.status})", "REPLAY"
        elif prior is not None:
            check, allowed, status, event = "TX_CONFLICT", False, DENIED, "DECISION"
            detail = f"tx_id {tx_id} was already used for a different request"
        elif chain_error is not None:
            check, allowed, status, event = "INVALID_CHAIN", False, DENIED, "DECISION"
            detail = chain_error
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
            currency=currency or "",
            status=status,
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


def claim(
    mandate: Mandate,
    tx_id: str,
    payee: str,
    amount: int,
    currency: str | None = None,
    evidence_log: EvidenceLog | None = None,
    store=None,
    at: float | None = None,
    clock: Callable[[], float] = time.time,
    registry=None,
) -> TxRecord:
    """Atomically claim an AUTHORIZED transaction for dispatch (MMF §5.5).

    Exactly one worker can move a transaction from AUTHORIZED to CLAIMED:
    the transition verifies that the instruction about to be submitted
    matches the stored request (payee, amount, currency), then claims it in
    the same serialized step. Concurrent dispatchers cannot both succeed —
    the loser raises ``LifecycleError`` and MUST NOT submit. After a
    successful claim, invoke the rail idempotently on ``tx_id``, then call
    ``settle()`` or ``void()`` with its outcome.

    This is the settlement-half of idempotency: ``enforce()`` guarantees a
    request is not authorized twice; ``claim()`` guarantees an authorization
    is not dispatched twice.

    Revocation is re-verified at claim time (MMF §5.4): an authorization
    granted before a mandate was revoked MUST NOT be dispatched afterward —
    the kill switch has to reach the dispatch point, not just new
    evaluations. ``settle`` and ``void`` are unaffected: they record rail
    facts, not grant new authority.

    Raises LifecycleError for unknown, denied, already claimed, settled or
    voided transactions, or when the instruction does not match the stored
    request.
    """
    at = clock() if at is None else at
    log = evidence_log or _DEFAULT_LOG
    chain, chain_error = mandate.resolve_chain()
    if chain_error is not None:
        raise LifecycleError(chain_error)
    ledger = store or mandate._state
    with _serialized(chain, store):
        tx = ledger.get_tx(tx_id)
        # a transaction of another mandate is UNKNOWN_TX: its stored fields
        # must never be copied into this mandate's evidence (MMF §5.6)
        own = tx if tx is not None and tx.mandate_id == mandate.mandate_id else None
        err = cause = None
        if tx is None or tx.mandate_id != mandate.mandate_id:
            err = f"unknown transaction {tx_id} for mandate {mandate.mandate_id}"
            cause = "UNKNOWN_TX"
        elif tx.status != AUTHORIZED:
            err = f"cannot claim {tx_id}: status is {tx.status}, not AUTHORIZED"
            cause = "STATUS"
        elif (tx.payee, tx.amount, tx.currency) != (payee, amount, currency):
            err = (
                f"instruction ({payee!r}, {amount!r}, {currency!r}) does not bind to "
                f"authorized transaction {tx_id} ({tx.payee!r}, {tx.amount!r}, {tx.currency!r})"
            )
            cause = "BINDING"
        else:
            for node in chain:
                if node.revoked:
                    err = f"{_label(node, mandate)} revoked — authorization cannot be dispatched"
                    cause = "REVOKED"
                    break
            if err is None:
                for node in chain:
                    if node.is_expired(at):
                        err = f"{_label(node, mandate)} expired — cannot dispatch"
                        cause = "EXPIRED"
                        break
            if err is None and registry is not None:
                for node in chain:
                    try:
                        if registry.is_revoked(node.mandate_id):
                            err = f"{_label(node, mandate)} revoked (registry) — cannot dispatch"
                            cause = "REVOKED_REGISTRY"
                            break
                    except Exception as exc:  # noqa: BLE001 — fail closed on registry faults
                        err = f"revocation registry unavailable ({exc.__class__.__name__})"
                        cause = "REGISTRY"
                        break
        if err is not None:
            # A rejected claim is security evidence: a binding mismatch is
            # exactly the T6 substitution signal, and a duplicate claim is
            # the double-dispatch attempt the slot exists to stop.
            # Normative cause: <OP>:<CAUSE> — two conforming implementations
            # rejecting the same attempt produce identical records (MMF §5.6).
            log.append(
                mandate_id=mandate.mandate_id,
                agent=mandate.agent,
                payee=payee,
                amount=amount if is_minor_amount(amount) else None,
                allowed=False,
                check=f"CLAIM:{cause}",
                timestamp=at,
                detail=err,
                tx_id=tx_id,
                event="REJECTED",
                currency=currency or "",
                status=own.status if own is not None else "",
            )
        else:
            tx = dataclasses.replace(tx, status=CLAIMED)
            ledger.put_tx(tx)
            log.append(
                mandate_id=mandate.mandate_id,
                agent=mandate.agent,
                payee=tx.payee,
                amount=tx.amount,
                allowed=True,
                check=tx.check,
                timestamp=at,
                detail=f"{tx_id} claimed for dispatch",
                tx_id=tx_id,
                event="CLAIM",
                currency=tx.currency or "",
                status=CLAIMED,
            )
    if err is not None:
        raise LifecycleError(err)
    return tx


def settle(
    mandate: Mandate,
    tx_id: str,
    evidence_log: EvidenceLog | None = None,
    store=None,
    at: float | None = None,
    clock: Callable[[], float] = time.time,
) -> TxRecord:
    """Mark an AUTHORIZED or CLAIMED transaction as SETTLED (funds moved).

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
    cancellation: str | None = None,
    verify_cancellation: Callable[[str, TxRecord], bool] | None = None,
) -> TxRecord:
    """Release an AUTHORIZED or CLAIMED transaction that will never settle.

    Its reserved amount is returned to every mandate in the chain; the
    velocity slot is retained — velocity counts authorizations issued, so an
    authorize-and-void cycle cannot evade the rate limit (MMF §5.1).

    Voiding a CLAIMED transaction releases budget while a payment may be in
    flight, so it is a reconciliation action requiring a ``cancellation``
    reference: a non-empty identifier of the authoritative confirmation that
    the rail did not execute and cannot subsequently execute the
    instruction — a rail decline response ID, an adapter verdict, or a
    reconciliation record from a trusted source (MMF §5.4). The reference is
    hashed into the VOID evidence record's ``detail`` field, so *what*
    justified releasing the reservation is itself auditable. An uncertain
    in-flight outcome MUST stay CLAIMED — releasing the reservation early
    would let spend escape the budget. The claim slot is permanently
    consumed either way, so a retry uses a new ``tx_id``.

    Idempotent for already-voided transactions. A SETTLED transaction cannot
    be voided: refunds are a rail concern (MMF §8).
    """
    return _transition(
        mandate, tx_id, VOIDED, evidence_log, store, at, clock, cancellation, verify_cancellation
    )


def _transition(
    mandate, tx_id, target, evidence_log, store, at, clock, cancellation=None,
    verify_cancellation=None,
) -> TxRecord:
    at = clock() if at is None else at
    log = evidence_log or _DEFAULT_LOG
    chain, chain_error = mandate.resolve_chain()
    if chain_error is not None:
        raise LifecycleError(chain_error)
    ledger = store or mandate._state
    with _serialized(chain, store):
        tx = ledger.get_tx(tx_id)
        # a transaction of another mandate is UNKNOWN_TX: its stored fields
        # must never be copied into this mandate's evidence (MMF §5.6)
        own = tx if tx is not None and tx.mandate_id == mandate.mandate_id else None
        err = cause = None
        op = "SETTLE" if target == SETTLED else "VOID"
        if tx is None or tx.mandate_id != mandate.mandate_id:
            err = f"unknown transaction {tx_id} for mandate {mandate.mandate_id}"
            cause = "UNKNOWN_TX"
        elif tx.status == target:
            return tx  # idempotent repeat: unchanged, no evidence
        elif target == SETTLED and tx.status != CLAIMED:
            # Settle records that the rail executed — only reachable via the
            # claim slot. An AUTHORIZED transaction was never dispatched, so
            # it cannot settle; it can only be claimed or voided.
            err = f"cannot settle {tx_id}: status is {tx.status}, not CLAIMED"
            cause = "STATUS"
        elif target == VOIDED and tx.status not in (AUTHORIZED, CLAIMED):
            err = f"cannot void {tx_id}: status is {tx.status}"
            cause = "STATUS"
        elif target == VOIDED and tx.status == CLAIMED and not cancellation:
            err = (
                f"cannot void claimed transaction {tx_id} without authoritative "
                "confirmation the rail did not execute — an uncertain in-flight "
                "outcome stays CLAIMED"
            )
            cause = "CANCELLATION"
        elif target == VOIDED and tx.status == CLAIMED and verify_cancellation is not None:
            # Optional validation by the trusted component named in MMF §5.4:
            # the reference must bind to THIS transaction's rail instruction.
            # A verifier fault is a failed validation — fail closed.
            try:
                bound = bool(verify_cancellation(cancellation, tx))
            except Exception:  # noqa: BLE001 — a verifier fault never releases a reservation
                bound = False
            if not bound:
                err = (
                    f"cancellation reference for {tx_id} failed validation — the "
                    "reservation stays held while the in-flight outcome is unconfirmed"
                )
                cause = "CANCELLATION"
        if err is not None:
            # Rejected transitions are security evidence too (R6): a settle
            # attempt on a denied or voided transaction is exactly the kind
            # of event an audit exists to catch.
            log.append(
                mandate_id=mandate.mandate_id,
                agent=mandate.agent,
                payee=own.payee if own is not None else "",
                amount=own.amount if own is not None else None,
                allowed=False,
                check=f"{op}:{cause}",
                timestamp=at,
                detail=err,
                tx_id=tx_id,
                event="REJECTED",
                currency=own.currency if own is not None else "",
                status=own.status if own is not None else "",
            )
        else:
            if target == VOIDED:
                for node in chain:
                    (store or node._state).release(node.mandate_id, tx_id)
            tx = dataclasses.replace(tx, status=target)
            ledger.put_tx(tx)
            if target == SETTLED:
                # Resolved reservations age out of the budget window like any
                # completed spend; unresolved ones keep consuming budget.
                for node in chain:
                    (store or node._state).resolve(node.mandate_id, tx_id)
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
                reference=cancellation or "",
                currency=tx.currency or "",
                status=target,
            )
    if err is not None:
        raise LifecycleError(err)
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


def _spend(node: Mandate, store, at: float, window_days: int | None) -> tuple[int, int]:
    # A None window means "all recorded history" (the accumulated total).
    since = 0.0 if window_days is None else at - window_days * 86400
    return (store or node._state).spend(node.mandate_id, since)


def _evaluate(mandate, chain, payee, amount, currency, at, store, registry, credential, key):
    """Return (check_id, advisory_detail). First failure wins (MMF §5)."""
    if credential is not None or key is not None:
        if credential is None or key is None:
            return "CREDENTIAL", "credential and principal_key must both be supplied"
        from .credentials import verify_credential

        creds = credential if isinstance(credential, dict) else {mandate.mandate_id: credential}
        for node in chain:
            node_credential = creds.get(node.mandate_id)
            if node_credential is None:
                return "CREDENTIAL", f"no credential supplied for {_label(node, mandate)}"
            ok, why = verify_credential(node_credential, node, key, at=at)
            if not ok:
                return "CREDENTIAL", f"{_label(node, mandate)}: {why}"

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

    for node in chain:
        if node.is_expired(at):
            return "EXPIRED", f"{_label(node, mandate)} expired"

    if not is_minor_amount(amount):
        return "NON_POSITIVE", f"amount {amount!r} is not a positive integer in minor units"

    for node in chain:
        if amount > node.bounds.max_amount:
            return "CAP_EXCEEDED", f"amount {amount} exceeds cap {node.bounds.max_amount}"

    for node in chain:
        if currency != node.bounds.currency:
            return (
                "CURRENCY",
                f"currency {currency!r} outside mandate ({node.bounds.currency})"
                if currency
                else "transaction currency is required",
            )

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
            # Checked arithmetic (MMF §3.1): an accumulated total beyond
            # 2^53-1 is outside the exact-representation bound — fail safe.
            if spent + amount > MAX_AMOUNT or spent + amount > budget:
                return "BUDGET", f"{_label(node, mandate)} budget {budget} would be exceeded"

    return "WITHIN", "within mandate"
