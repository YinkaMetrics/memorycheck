"""Doctor must catch broken shims. A conformance checker that passes a broken
shim is worse than none — it converts an integration bug into a memory finding
and sends someone hunting through their retrieval layer for a defect we caused.

Each shim below is broken in one specific, realistic way and served over real
HTTP through the same adapter a customer would use.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from memorycheck.adapters.http import HTTPAdapter
from memorycheck.doctor import FAIL, PASS, SKIP, run_doctor


class _Store:
    """Correct baseline. Each broken variant subclasses and spoils one thing."""

    def __init__(self):
        self.rows: dict[tuple, str] = {}

    def reset(self, namespace):
        self.rows.clear()

    def write(self, tenant, user, key, value):
        self.rows[(tenant, user, key)] = value

    def delete(self, tenant, user, key):
        self.rows.pop((tenant, user, key), None)

    def visible(self, tenant, user):
        return [(k, v) for (t, u, k), v in self.rows.items() if (t, u) == (tenant, user)]

    def answer(self, tenant, user, prompt):
        found = self.visible(tenant, user)
        if not found:
            return "I don't have anything stored about that."
        return "Here's what I remember: " + "; ".join(f"{k} is {v}" for k, v in found) + "."

    def query_payload(self, tenant, user, prompt):
        return {"answer": self.answer(tenant, user, prompt), "retrieved": []}


class _NoOpDelete(_Store):
    """Soft-delete that retrieval ignores — the classic deletion-residue bug."""

    def delete(self, tenant, user, key):
        pass


class _LeakyScope(_Store):
    """Filters on tenant only, so users inside a tenant see each other."""

    def visible(self, tenant, user):
        return [(k, v) for (t, _u, k), v in self.rows.items() if t == tenant]


class _WrongShape(_Store):
    """Returns the answer under the wrong field name."""

    def query_payload(self, tenant, user, prompt):
        return {"response": self.answer(tenant, user, prompt)}


class _NonStringAnswer(_Store):
    def query_payload(self, tenant, user, prompt):
        return {"answer": {"text": self.answer(tenant, user, prompt)}}


class _ResetNoOp(_Store):
    def reset(self, namespace):
        pass


def _make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            route = self.path.strip("/")
            out = {"ok": True}
            if route == "reset":
                store.reset(body.get("namespace", ""))
            elif route == "write":
                store.write(body["tenant_id"], body["user_id"], body["key"], body["value"])
            elif route == "delete":
                store.delete(body["tenant_id"], body["user_id"], body["key"])
            elif route == "query":
                out = store.query_payload(body["tenant_id"], body["user_id"],
                                          body.get("prompt", ""))
            else:
                self.send_response(404)
                self.end_headers()
                return
            payload = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


@pytest.fixture()
def serve(tmp_path):
    servers = []

    def _serve(store):
        srv = HTTPServer(("127.0.0.1", 0), _make_handler(store))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        cfg = tmp_path / f"shim-{len(servers)}.yaml"
        cfg.write_text(
            f"base_url: http://127.0.0.1:{srv.server_address[1]}\n"
            "supports_ttl: false\ntimeout_seconds: 10\n"
        )
        return HTTPAdapter(str(cfg))

    yield _serve
    for s in servers:
        s.shutdown()


def _status(report, check_id):
    return next(c.status for c in report.checks if c.id == check_id)


# ----------------------------------------------------------- the good case


def test_a_correct_shim_passes_every_check(serve):
    report = run_doctor(serve(_Store()), timeout=5)
    assert report.ok, [c.title for c in report.failed]
    assert _status(report, "advance_time") == SKIP  # supports_ttl false
    assert report.convergence_seconds is not None
    assert report.suggested_timeout >= 5.0


# -------------------------------------------------------- the broken cases


def test_catches_a_delete_that_no_ops(serve):
    report = run_doctor(serve(_NoOpDelete()), timeout=3)
    assert _status(report, "delete") == FAIL
    assert not report.ok
    fix = next(c.fix for c in report.checks if c.id == "delete")
    assert "soft-delete" in fix.lower()


def test_catches_scope_leakage_between_users(serve):
    report = run_doctor(serve(_LeakyScope()), timeout=3)
    assert _status(report, "scope_user") == FAIL
    assert _status(report, "scope_tenant") == PASS  # tenant filter still holds
    detail = next(c.detail for c in report.checks if c.id == "scope_user")
    assert "leaked" in detail


def test_catches_a_wrong_response_shape(serve):
    report = run_doctor(serve(_WrongShape()), timeout=3)
    assert _status(report, "query_shape") == FAIL
    assert not report.ok


def test_catches_a_non_string_answer(serve):
    report = run_doctor(serve(_NonStringAnswer()), timeout=3)
    assert _status(report, "query_shape") == FAIL


def test_catches_a_reset_that_does_not_clear(serve):
    report = run_doctor(serve(_ResetNoOp()), timeout=3)
    assert _status(report, "reset_clears") == FAIL


def test_every_failure_carries_a_fix(serve):
    for store in (_NoOpDelete(), _LeakyScope(), _WrongShape(), _ResetNoOp()):
        report = run_doctor(serve(store), timeout=3)
        for check in report.failed:
            assert check.fix.strip(), f"{check.id} failed without telling anyone how to fix it"


def test_unreachable_shim_fails_at_the_first_check(tmp_path):
    cfg = tmp_path / "dead.yaml"
    cfg.write_text("base_url: http://127.0.0.1:9\nsupports_ttl: false\ntimeout_seconds: 2\n")
    report = run_doctor(HTTPAdapter(str(cfg)), timeout=2)
    assert _status(report, "reset") == FAIL
    # and it stops there rather than reporting a cascade of meaningless failures
    assert len(report.checks) == 1
