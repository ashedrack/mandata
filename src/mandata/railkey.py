"""Gate-scoped rail idempotency keys (MMF §5.4).

When several gates share one rail idempotency namespace, a bare ``tx_id`` can
collide across gates. The key below is a deterministic, collision-resistant
function of the gate's identity and the ``tx_id``: stable across retries and
recomputable by an auditor who retains the ``gate_id``.
"""

from __future__ import annotations

import hashlib

from .models import lp_fields


def rail_idempotency_key(gate_id: str, tx_id: str, *, hashed: bool = True) -> str:
    """Derive the rail idempotency key for ``tx_id`` under ``gate_id``.

    ``hashed=True`` (default): lower-case hex SHA-256 of the length-prefixed
    form ``len:gate_id|len:tx_id`` — 64 ASCII characters, within the key-length
    limits of common rails. ``hashed=False`` returns the length-prefixed form
    itself, injective but unbounded in length.
    """
    if not gate_id:
        raise ValueError("gate_id must be a non-empty, deployment-unique string")
    form = lp_fields(gate_id, tx_id)
    return hashlib.sha256(form.encode("utf-8")).hexdigest() if hashed else form
