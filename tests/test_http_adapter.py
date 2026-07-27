"""Smoke test: drive the full lifecycle suite through the HTTP adapter
against an in-process server that implements the 4-endpoint shim contract,
backed by the strict reference store. Proves the wire contract end-to-end."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from memorycheck.adapters.http import HTTPAdapter
from memorycheck.adapters.reference import ReferenceAdapter
from memorycheck.judge import load_judge
from memorycheck.ledger import Scope
from memorycheck.oracle import FAIL, evaluate
from memorycheck.runner import run_suite
from memorycheck.scenario import load_dir

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"

STORE = ReferenceAdapter(mode="strict")


class ShimHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test output
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        route = self.path.strip("/")
        out = {"ok": True}
        if route == "reset":
            STORE.reset(payload.get("namespace", ""))
        elif route == "write":
            STORE.write(
                Scope(payload["tenant_id"], payload["user_id"]),
                payload["key"], payload["value"], payload.get("ttl_steps"),
            )
        elif route == "delete":
            STORE.delete(Scope(payload["tenant_id"], payload["user_id"]), payload["key"])
        elif route == "advance_time":
            STORE.advance_time(payload["steps"])
        elif route == "query":
            result = STORE.query(
                Scope(payload["tenant_id"], payload["user_id"]),
                payload["prompt"], payload.get("seed", 0),
            )
            out = {"answer": result.answer, "retrieved": result.retrieved}
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def shim_url():
    server = HTTPServer(("127.0.0.1", 0), ShimHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_full_suite_over_http(shim_url, tmp_path):
    config = tmp_path / "http.yaml"
    config.write_text(
        f"base_url: {shim_url}\nsupports_ttl: true\ntimeout_seconds: 10\n"
    )
    adapter = HTTPAdapter(str(config))
    scenarios = load_dir(SCENARIOS)
    suite = run_suite(scenarios, adapter, load_judge("deterministic"), seeds=1, baseline=False)
    findings = evaluate(suite["runs"])
    assert [f for f in findings if f.status == FAIL] == []
