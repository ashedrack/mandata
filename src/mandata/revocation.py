"""Revocation registry — distributed revocation at transaction speed.

A local `mandate.revoke()` only affects that object. Real deployments need a
shared check: many processes enforce the same mandate, and revocation must
propagate faster than the next transaction.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from typing import Protocol


class RevocationRegistry(Protocol):
    """Shared answer to: is this mandate revoked right now?"""

    def is_revoked(self, mandate_id: str) -> bool: ...
    def revoke(self, mandate_id: str) -> None: ...
    def restore(self, mandate_id: str) -> None: ...


class InMemoryRegistry:
    """Process-local shared registry — one source of truth per service instance."""

    def __init__(self) -> None:
        self._revoked: set[str] = set()
        self._lock = threading.Lock()

    def is_revoked(self, mandate_id: str) -> bool:
        with self._lock:
            return mandate_id in self._revoked

    def revoke(self, mandate_id: str) -> None:
        with self._lock:
            self._revoked.add(mandate_id)

    def restore(self, mandate_id: str) -> None:
        with self._lock:
            self._revoked.discard(mandate_id)


class HTTPRegistry:
    """Remote registry — checks an external revocation service over HTTP.

    `is_revoked` GETs `{base_url}/revoked/{mandate_id}` expecting
    `{"revoked": true|false}`. `revoke` POSTs to the same path.
    Stdlib-only: no requests dependency.
    """

    def __init__(self, base_url: str, timeout: float = 2.0, headers: dict | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._headers = headers or {}

    def _request(self, method: str, path: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{self._base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **self._headers},
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read() or b"{}")

    def is_revoked(self, mandate_id: str) -> bool:
        return bool(self._request("GET", f"/revoked/{mandate_id}").get("revoked"))

    def revoke(self, mandate_id: str) -> None:
        self._request("POST", f"/revoked/{mandate_id}", {"revoked": True})

    def restore(self, mandate_id: str) -> None:
        self._request("POST", f"/revoked/{mandate_id}", {"revoked": False})
