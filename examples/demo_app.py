"""Demo app — Mandata wired into a simulated real deployment.

Three actors, talking over real HTTP (stdlib only, no dependencies):

    RevocationRegistry server   — shared revocation state (checked at the gate)
    PaymentRail server          — mock settlement endpoint (executes only allows)
    ShopBot agent               — proposes payments; every one gated by Mandata

Scenario: Alice issues ShopBot a signed mandate, delegates a stricter mandate
to a sub-agent, watches normal settlements, a compromise attempt, an injected
prompt attack, and a remote revocation — then audits the evidence chain.

Run:  python examples/demo_app.py
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nacl.signing import SigningKey

from mandata import (
    Bounds,
    EvidenceLog,
    HTTPRegistry,
    Mandate,
    SQLiteStore,
    delegate,
    enforce,
)
from mandata.adapters import x402
from mandata.anchoring import Anchor, verify_anchor
from mandata.credentials import issue_credential, verify_credential


# ---------------------------------------------------------------- services
class RegistryHandler(BaseHTTPRequestHandler):
    revoked: ClassVar[set[str]] = set()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        RegistryHandler.revoked.add(json.loads(body).get("mandate_id", ""))
        self._reply(200, {"ok": True})

    def do_GET(self):
        mid = self.path.rsplit("/", 1)[-1]
        self._reply(200, {"revoked": mid in RegistryHandler.revoked})

    def _reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class RailHandler(BaseHTTPRequestHandler):
    settled: ClassVar[list] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        RailHandler.settled.append(body)
        self._reply(200, {"status": "settled", "txid": f"tx-{len(RailHandler.settled)}"})

    def do_GET(self):
        self._reply(200, {"settled": RailHandler.settled})

    def _reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def start(cls, port):
    srv = HTTPServer(("127.0.0.1", port), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ------------------------------------------------------------------- demo
def main():
    import urllib.request

    reg_srv = start(RegistryHandler, 8390)
    rail_srv = start(RailHandler, 8391)
    print("services up: registry :8390 | rail :8391\n")

    def post(url, obj):
        req = urllib.request.Request(
            url,
            data=json.dumps(obj).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req).read())

    log = EvidenceLog()
    store = SQLiteStore("/tmp/mandata-demo.db")
    registry = HTTPRegistry("http://127.0.0.1:8390")
    decisions_seen = []

    def metrics_hook(d):
        decisions_seen.append(d.allowed)

    # 1. Alice issues ShopBot a signed mandate
    priv = SigningKey.generate()
    pub = priv.verify_key.encode().hex()
    mandate = Mandate(
        principal="user:alice",
        agent="agent:shopbot",
        bounds=Bounds(
            max_amount=500,
            currency="GBP",
            allowed_payees=["tesco.com", "skyscanner.com", "rail-api"],
            expires_in_days=30,
            velocity_limit=4,
            total_budget=900,
            budget_window_days=30,
        ),
    )
    cred = issue_credential(mandate, priv)
    ok, why = verify_credential(cred, mandate, pub)
    assert ok, f"credential must verify at the gate: {why}"
    print("== mandate issued + credential verified ==")

    # 2. ShopBot delegates a stricter mandate to a price-scout sub-agent
    scout = delegate(
        mandate,
        "agent:price-scout",
        Bounds(
            max_amount=100,
            currency="GBP",
            allowed_payees=["tesco.com"],
            expires_in_days=7,
            velocity_limit=2,
            total_budget=100,
            budget_window_days=7,
        ),
    )
    print(f"   delegated stricter mandate -> {scout.agent}\n")

    # 3. Normal traffic — agent proposes, gate decides, rail settles
    print("== normal operation ==")
    for payee, amt in [("tesco.com", 49.99), ("skyscanner.com", 420.0)]:
        d = enforce(
            mandate,
            payee,
            amt,
            "GBP",
            evidence_log=log,
            store=store,
            registry=registry,
            hooks=[metrics_hook],
        )
        if d.allowed:
            r = post(
                "http://127.0.0.1:8391/pay",
                {"payee": payee, "amount": amt, "mandate": mandate.mandate_id},
            )
            print(f"   ALLOW {payee} {amt} -> rail {r['txid']}")
        else:
            print(f"   DENY  {payee} {amt} ({d.reason})")

    # 4. x402-originated payment flows through the same gate
    req = {"maxAmountRequired": "25", "asset": "gbp", "payTo": "tesco.com"}
    m402 = x402.from_payment_requirements(req, "user:alice", "agent:shopbot")
    d = enforce(
        m402,
        "tesco.com",
        25.0,
        "GBP",
        evidence_log=log,
        store=store,
        registry=registry,
        hooks=[metrics_hook],
    )
    print(
        f"   x402 ALLOW tesco.com 25.0 -> rail {post('http://127.0.0.1:8391/pay', {'payee': 'tesco.com', 'amount': 25.0})['txid']}"
        if d.allowed
        else f"   x402 DENY ({d.reason})"
    )

    # 5. Sub-agent stays inside its tighter bounds
    d = enforce(
        scout,
        "tesco.com",
        60.0,
        "GBP",
        evidence_log=log,
        store=store,
        registry=registry,
        hooks=[metrics_hook],
    )
    if d.allowed:
        post("http://127.0.0.1:8391/pay", {"payee": "tesco.com", "amount": 60.0})
    print(f"   scout {'ALLOW' if d.allowed else 'DENY'} tesco.com 60.0")

    # 6. Compromise attempts — injected payee + over-cap amount
    print("\n== compromised agent ==")
    for payee, amt in [("evil-payee.io", 100.0), ("tesco.com", 600.0)]:
        d = enforce(
            mandate,
            payee,
            amt,
            "GBP",
            evidence_log=log,
            store=store,
            registry=registry,
            hooks=[metrics_hook],
        )
        print(f"   {'ALLOW' if d.allowed else 'DENY '} {payee} {amt} ({d.reason})")

    # 7. Alice revokes remotely — next transaction blocked at gate
    print("\n== principal revokes remotely ==")
    post("http://127.0.0.1:8390/revoke", {"mandate_id": mandate.mandate_id})
    d = enforce(
        mandate,
        "tesco.com",
        10.0,
        "GBP",
        evidence_log=log,
        store=store,
        registry=registry,
        hooks=[metrics_hook],
    )
    print(f"   {'ALLOW' if d.allowed else 'DENY '} tesco.com 10.0 ({d.reason})")

    # 8. Audit — evidence chain + anchor + observability summary
    print("\n== audit ==")
    print(f"   evidence chain valid : {log.verify_chain()}")
    anchor = Anchor(
        head_hash=log.head_hash, service_token="demo-tsa-token", service_url="http://tsa.example"
    )
    print(f"   anchor matches head  : {verify_anchor(log, anchor, anchor.service_url)}")
    print(f"   decisions via hooks  : {sum(decisions_seen)} allowed / {len(decisions_seen)} total")
    print(f"   rail settled         : {len(RailHandler.settled)} transactions")
    for r in log.records:
        print(
            f"   [{'ALLOW' if r.allowed else 'DENY '}] {r.agent:<18} {r.payee:<16} {r.amount:>7.2f}  {r.reason}"
        )

    reg_srv.shutdown()
    rail_srv.shutdown()


if __name__ == "__main__":
    main()
