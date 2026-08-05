"""Smoke test: drive the full lifecycle suite through the HTTP adapter
against an in-process server that implements the 4-endpoint shim contract,
backed by the strict reference store. Proves the wire contract end-to-end."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from memorycheck.adapters.http import HTTPAdapter, HTTPAdapterError
from memorycheck.adapters.reference import ReferenceAdapter
from memorycheck.judge import load_judge
from memorycheck.ledger import Scope
from memorycheck.oracle import FAIL, evaluate
from memorycheck.runner import run_suite
from memorycheck.scenario import Scenario, Step, load_dir

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"

STORE = ReferenceAdapter(mode="strict")


class ShimHandler(BaseHTTPRequestHandler):
    store = STORE

    def log_message(self, *args):  # silence test output
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        route = self.path.strip("/")
        out = {"ok": True}
        if route == "reset":
            self.store.reset(payload.get("namespace", ""))
        elif route == "write":
            self.store.write(
                Scope(payload["tenant_id"], payload["user_id"]),
                payload["key"], payload["value"], payload.get("ttl_steps"),
            )
        elif route == "delete":
            self.store.delete(Scope(payload["tenant_id"], payload["user_id"]), payload["key"])
        elif route == "advance_time":
            self.store.advance_time(payload["steps"])
        elif route == "query":
            result = self.store.query(
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
def serve_store():
    servers = []

    def _serve(store):
        class BoundHandler(ShimHandler):
            pass

        BoundHandler.store = store
        server = HTTPServer(("127.0.0.1", 0), BoundHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _serve
    for server in servers:
        server.shutdown()


@pytest.fixture()
def shim_url(serve_store):
    return serve_store(STORE)


class DelayedReferenceStore:
    """Accept mutations immediately; expose them to query after a short lag."""

    def __init__(self, delay=0.15):
        self.store = ReferenceAdapter(mode="strict")
        self.delay = delay
        self.pending = []

    def _flush(self):
        now = time.monotonic()
        ready = [item for item in self.pending if item[0] <= now]
        self.pending = [item for item in self.pending if item[0] > now]
        for _, operation, args in ready:
            getattr(self.store, operation)(*args)

    def reset(self, namespace):
        self.pending.clear()
        self.store.reset(namespace)

    def write(self, scope, key, value, ttl_steps=None):
        self.pending.append(
            (time.monotonic() + self.delay, "write", (scope, key, value, ttl_steps))
        )

    def delete(self, scope, key):
        self.pending.append((time.monotonic() + self.delay, "delete", (scope, key)))

    def advance_time(self, steps):
        self._flush()
        self.store.advance_time(steps)

    def query(self, scope, prompt, seed=0):
        self._flush()
        return self.store.query(scope, prompt, seed)


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


def test_eventually_consistent_writes_and_deletes_converge_before_runner_continues(
    serve_store, tmp_path,
):
    url = serve_store(DelayedReferenceStore(delay=0.15))
    config = tmp_path / "delayed.yaml"
    config.write_text(
        f"base_url: {url}\nsupports_ttl: false\ntimeout_seconds: 2\n"
        "convergence_timeout_seconds: 1\nconvergence_interval_seconds: 0.02\n"
    )
    adapter = HTTPAdapter(str(config))
    scope = Scope("acme", "alice")
    scenario = Scenario(
        id="delayed-http",
        title="HTTP mutations must converge before the runner advances",
        tags=[],
        subject=scope,
        steps=[
            Step("write", scope, key="plan", value="scale-2026", index=0),
            Step("query", scope, prompt="Which plan?",
                 expect={"must_use": ["plan"]}, index=1),
            Step("delete", scope, key="plan", index=2),
            Step("query", scope, prompt="Which plan?", index=3),
        ],
        path="test",
    )

    suite = run_suite(
        [scenario], adapter, load_judge("deterministic"), seeds=1, baseline=False
    )
    findings = evaluate(suite["runs"])
    assert [f for f in findings if f.status == FAIL] == []
    assert adapter.last_write_convergence_seconds >= 0.1
    assert adapter.last_delete_convergence_seconds >= 0.1


def test_write_convergence_timeout_aborts_instead_of_scoring_a_race(
    serve_store, tmp_path,
):
    url = serve_store(DelayedReferenceStore(delay=1.0))
    config = tmp_path / "timeout.yaml"
    config.write_text(
        f"base_url: {url}\nsupports_ttl: false\ntimeout_seconds: 2\n"
        "convergence_timeout_seconds: 0.05\nconvergence_interval_seconds: 0.01\n"
    )
    adapter = HTTPAdapter(str(config))
    with pytest.raises(HTTPAdapterError, match="unconfirmed write"):
        adapter.write(Scope("acme", "alice"), "plan", "scale-2026")
