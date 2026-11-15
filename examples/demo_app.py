"""Demo app — Mandata wired into a simulated real deployment.

Three actors, talking over real HTTP (stdlib only, no dependencies):

    RevocationRegistry server   — shared revocation state (checked at the gate)
    PaymentRail server          — mock settlement endpoint (executes only allows)
    ShopBot agent               — proposes payments; every one gated by Mandata

Scenario: Alice issues ShopBot a signed mandate, delegates a stricter mandate
to a sub-agent, watches normal settlements, a compromise attempt, an injected
prompt attack, a settlement retry, and a remote revocation — then audits the
signed evidence chain.

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
    LifecycleError,
    Mandate,
    SQLiteStore,
    claim,
    delegate,
    enforce,
    from_minor,
    settle,
    to_minor,
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
        # Idempotent on tx_id, as real rails are on their idempotency key.
        done = {b["tx_id"]: i for i, b in enumerate(RailHandler.settled)}
        if body["tx_id"] not in done:
            RailHandler.settled.append(body)
        n = done.get(body["tx_id"], len(RailHandler.settled) - 1) + 1
        self._reply(200, {"status": "settled", "txid": f"tx-{n}"})

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
    import tempfile
    import urllib.request

    reg_srv = start(RegistryHandler, 8390)
    rail_srv = start(RailHandler, 8391)
    print("services up: registry :8390 | rail :8391\n")

    def post(url, obj, timeout=5):
        req = urllib.request.Request(
            url,
            data=json.dumps(obj).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

    gate_key = SigningKey.generate()  # the gate signs every evidence record
    log = EvidenceLog(signing_key=gate_key)
    store = SQLiteStore(Path(tempfile.mkdtemp()) / "mandata-demo.db")
    registry = HTTPRegistry("http://127.0.0.1:8390")
    decisions_seen = []

    def metrics_hook(d):
        decisions_seen.append(d)

    def pay(m, payee, major, tx_id):
        """Agent proposes -> gate decides -> rail settles only a bound approval."""
        amount = to_minor(major, "GBP")
        d = enforce(
            m,
            payee,
            amount,
            "GBP",
            evidence_log=log,
            store=store,
            registry=registry,
            hooks=[metrics_hook],
            tx_id=tx_id,
        )
        if d.allowed:
            try:
                claim(m, tx_id, payee, amount, "GBP", evidence_log=log, store=store)
            except LifecycleError:
                return d, None  # already claimed or does not bind — never dispatched
            r = post(
                "http://127.0.0.1:8391/pay", {"tx_id": tx_id, "payee": payee, "amount": amount}
            )
            settle(m, tx_id, evidence_log=log, store=store)
            return d, r["txid"]
        return d, None

    # 1. Alice issues ShopBot a signed mandate (amounts in pence)
    priv = SigningKey.generate()
    pub = priv.verify_key.encode().hex()
    mandate = Mandate(
        principal="user:alice",
        agent="agent:shopbot",
        bounds=Bounds(
            max_amount=to_minor("500", "GBP"),
            currency="GBP",
            allowed_payees=["tesco.com", "skyscanner.com", "rail-api"],
            expires_in_days=30,
            velocity_limit=4,
            total_budget=to_minor("900", "GBP"),
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
            max_amount=to_minor("100", "GBP"),
            currency="GBP",
            allowed_payees=["tesco.com"],
            expires_in_days=7,
            velocity_limit=2,
            total_budget=to_minor("100", "GBP"),
            budget_window_days=30,  # window ≥ parent's: shorter cycles the allowance faster
        ),
    )
    print(f"   delegated stricter mandate -> {scout.agent}\n")

    # 3. Normal traffic — agent proposes, gate decides, rail settles
    print("== normal operation ==")
    for i, (payee, amt) in enumerate([("tesco.com", "49.99"), ("skyscanner.com", "420.00")]):
        d, txid = pay(mandate, payee, amt, f"order-{i}")
        print(f"   {'ALLOW' if d.allowed else 'DENY '} {payee} £{amt} -> rail {txid}")

    # 4. x402-originated payment: payee and amount come from the server's
    #    requirements, and the signed payment must bind to the approval.
    req = {"maxAmountRequired": "2500", "asset": "gbp", "payTo": "tesco.com"}
    d = x402.enforce_requirements(
        mandate,
        req,
        tx_id="x402-nonce-1",
        evidence_log=log,
        store=store,
        registry=registry,
        hooks=[metrics_hook],
    )
    if d.allowed:
        x402.bind_payment(
            d,
            {"payload": {"authorization": {"to": "tesco.com", "value": "2500"}}},
            mandate=mandate,
            evidence_log=log,
            store=store,
        )
        r = post(
            "http://127.0.0.1:8391/pay", {"tx_id": d.tx_id, "payee": d.payee, "amount": d.amount}
        )
        settle(mandate, d.tx_id, evidence_log=log, store=store)
        print(f"   x402 ALLOW tesco.com £25.00 -> rail {r['txid']} (payment bound to approval)")
    else:
        print(f"   x402 DENY ({d.reason})")

    # 5. Sub-agent stays inside its tighter bounds
    d, txid = pay(scout, "tesco.com", "60.00", "scout-1")
    print(f"   scout {'ALLOW' if d.allowed else 'DENY'} tesco.com £60.00 -> rail {txid}")

    # 6. Settlement timeout: the agent retries the same order. The gate replays
    #    its original decision, the rail is idempotent on tx_id, nothing moves twice.
    print("\n== settlement retry after timeout ==")
    d = enforce(
        mandate,
        "tesco.com",
        to_minor("49.99", "GBP"),
        "GBP",
        evidence_log=log,
        store=store,
        registry=registry,
        hooks=[metrics_hook],
        tx_id="order-0",
    )
    print(
        f"   retry order-0: replayed={d.replayed} status={d.status} "
        f"binds={d.binds('tesco.com', 4999, 'GBP')} -> not re-submitted to rail"
    )

    # 7. Compromise attempts — injected payee + over-cap amount
    print("\n== compromised agent ==")
    for i, (payee, amt) in enumerate([("evil-payee.io", "100.00"), ("tesco.com", "600.00")]):
        d, _ = pay(mandate, payee, amt, f"attack-{i}")
        print(f"   {'ALLOW' if d.allowed else 'DENY '} {payee} £{amt} [{d.check}] ({d.reason})")

    # 8. Alice revokes remotely — next transaction blocked at gate
    print("\n== principal revokes remotely ==")
    post("http://127.0.0.1:8390/revoke", {"mandate_id": mandate.mandate_id})
    d, _ = pay(mandate, "tesco.com", "10.00", "order-after-revoke")
    print(f"   {'ALLOW' if d.allowed else 'DENY '} tesco.com £10.00 [{d.check}] ({d.reason})")

    # 9. Audit — evidence chain, gate signatures, anchor, observability summary
    print("\n== audit ==")
    decisions = [d for d in decisions_seen if not d.replayed]
    print(f"   evidence chain valid : {log.verify_chain()}")
    print(f"   gate signatures valid: {log.verify_chain(public_key=log.public_key)}")
    anchor = Anchor(
        head_hash=log.head_hash, service_token="demo-tsa-token", service_url="http://tsa.example"
    )
    print(f"   anchor matches head  : {verify_anchor(log, anchor, anchor.service_url)}")
    print(
        f"   gate decisions       : {sum(d.allowed for d in decisions)} allowed / "
        f"{len(decisions)} total (+{len(decisions_seen) - len(decisions)} idempotent replay)"
    )
    print(f"   rail settled         : {len(RailHandler.settled)} transactions")
    for r in log.records:
        print(
            f"   [{r.event:<8}] [{'ALLOW' if r.allowed else 'DENY '}] {r.agent:<18} "
            f"{r.payee:<16} {from_minor(r.amount, 'GBP') if r.amount else '-':>7}  {r.check}"
        )

    reg_srv.shutdown()
    rail_srv.shutdown()


if __name__ == "__main__":
    main()
