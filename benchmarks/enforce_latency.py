"""enforce() latency benchmark — reproduces Chart 2 of the MMF paper.

Run:  python benchmarks/enforce_latency.py [--trials 5] [--n 2000]

Measures per-decision latency and single-thread throughput for three
backends: in-memory store, SQLite store, and an HTTP revocation registry
served on loopback. These are sequential microbenchmarks, not production
performance: the HTTP backend crosses the loopback interface only; remote
network latency, cross-process contention and production durability
settings are not measured. Each trial uses a fresh mandate and evidence log and
runs a 200-call warm-up before timing.
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import nacl

import mandata
from mandata import Bounds, Mandate, enforce
from mandata.evidence import EvidenceLog
from mandata.revocation import HTTPRegistry
from mandata.state import InMemoryStore, SQLiteStore

WARMUP = 200


def _mandate() -> Mandate:
    return Mandate(
        mandate_id="m1",
        principal="p:alice",
        agent="a:bot",
        issued_at=0,
        bounds=Bounds(
            max_amount=500,
            currency="GBP",
            allowed_payees=["tesco.com"],
            expires_in_days=3650,
            velocity_limit=10**9,
            total_budget=None,
        ),
    )


def _trial(n: int, **kw) -> tuple[list[float], float]:
    m = _mandate()
    for _ in range(WARMUP):
        enforce(m, "tesco.com", 10, currency="GBP", at=1000, evidence_log=EvidenceLog(), **kw)
    log = EvidenceLog()
    lat: list[float] = []
    start = time.perf_counter()
    for _ in range(n):
        t = time.perf_counter()
        enforce(m, "tesco.com", 10, currency="GBP", at=1000, evidence_log=log, **kw)
        lat.append((time.perf_counter() - t) * 1e6)
    return lat, n / (time.perf_counter() - start)


def _run(name: str, factory, n: int, trials: int) -> None:
    samples: list[float] = []
    means: list[float] = []
    throughputs: list[float] = []
    for _ in range(trials):
        lat, tp = _trial(n, **factory())
        samples += lat
        means.append(statistics.mean(lat))
        throughputs.append(tp)
    samples.sort()

    def q(p: float) -> float:
        return samples[min(len(samples) - 1, int(len(samples) * p))]

    ci = 1.96 * statistics.stdev(means) / len(means) ** 0.5 if trials > 1 else 0.0
    print(
        f"{name:20s} N={len(samples):6d} p50={q(0.5):7.1f} p95={q(0.95):7.1f} "
        f"p99={q(0.99):7.1f} mean={statistics.mean(means):7.1f}±{ci:5.1f}us "
        f"thr={statistics.mean(throughputs):8.0f}/s"
    )


class _Registry(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"revoked": False}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--n", type=int, default=2000)
    args = ap.parse_args()

    print(
        f"# {platform.platform()} | {platform.processor() or platform.machine()} | "
        f"Python {platform.python_version()} | SQLite {sqlite3.sqlite_version} | "
        f"PyNaCl {nacl.__version__} | mandata {mandata.__version__}"
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Registry)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    tmp = tempfile.mkdtemp()
    counter = iter(range(10**6))
    try:
        _run("in-memory", lambda: {"store": InMemoryStore()}, args.n, args.trials)
        _run(
            "sqlite",
            lambda: {"store": SQLiteStore(Path(tmp) / f"bench{next(counter)}.db")},
            args.n,
            args.trials,
        )
        _run(
            "http-registry(lo)",
            lambda: {"store": InMemoryStore(), "registry": HTTPRegistry(url)},
            args.n,
            args.trials,
        )
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
