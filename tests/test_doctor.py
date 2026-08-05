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
from memorycheck.doctor import (
    FAIL, INFO, PARAPHRASING, PASS, QUOTING, SKIP, UNKNOWN, WARN, run_doctor,
)


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


class _Paraphrasing(_Store):
    """A perfectly good agent that summarises instead of quoting. Retrieval
    finds the value; the answer never says it."""

    def answer(self, tenant, user, prompt):
        found = self.visible(tenant, user)
        if not found:
            return "I don't have anything on file for them."
        return f"I found {len(found)} thing(s) on file for this user."

    def query_payload(self, tenant, user, prompt):
        return {
            "answer": self.answer(tenant, user, prompt),
            "retrieved": [{"key": k, "value": v} for k, v in self.visible(tenant, user)],
        }


class _ParaphrasingNoRetrieved(_Paraphrasing):
    """Paraphrases and returns no `retrieved`, so nothing can tell the
    difference between this and a lost write."""

    def query_payload(self, tenant, user, prompt):
        return {"answer": self.answer(tenant, user, prompt)}


class _Accumulating(_Store):
    """Correction appends rather than overwriting — Mem0's documented design."""

    def __init__(self):
        super().__init__()
        self.history: dict[tuple, list[str]] = {}

    def reset(self, namespace):
        super().reset(namespace)
        self.history.clear()

    def write(self, tenant, user, key, value):
        self.history.setdefault((tenant, user, key), []).append(value)

    def delete(self, tenant, user, key):
        self.history.pop((tenant, user, key), None)

    def visible(self, tenant, user):
        return [
            (k, v)
            for (t, u, k), vals in self.history.items()
            if (t, u) == (tenant, user)
            for v in vals
        ]


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


# ------------------------------------------- paraphrasing and correction info


def test_detects_a_paraphrasing_answering_layer(serve):
    report = run_doctor(serve(_Paraphrasing()), timeout=3)
    assert report.answering_layer == PARAPHRASING
    assert _status(report, "answering_layer") == WARN
    # A paraphrasing agent is legitimate, so it must not fail the contract.
    assert report.ok, [c.title for c in report.failed]
    fix = next(c.fix for c in report.checks if c.id == "answering_layer")
    assert "missing_current_fact" in fix
    assert "INCONCLUSIVE" in fix


def test_paraphrasing_without_retrieved_is_indistinguishable_from_a_lost_write(serve):
    # Nothing can separate these two, so doctor must not guess: it fails the
    # convergence check and says how to make the difference visible.
    report = run_doctor(serve(_ParaphrasingNoRetrieved()), timeout=2)
    assert report.answering_layer == UNKNOWN
    assert _status(report, "convergence") == FAIL
    assert "retrieved" in next(c.fix for c in report.checks if c.id == "convergence")


def test_quoting_layer_is_reported_as_quoting(serve):
    report = run_doctor(serve(_Store()), timeout=3)
    assert report.answering_layer == QUOTING
    assert _status(report, "answering_layer") == PASS


def test_correction_semantics_reported_as_info_without_a_verdict(serve):
    superseding = run_doctor(serve(_Store()), timeout=3)
    accumulating = run_doctor(serve(_Accumulating()), timeout=3)

    for report in (superseding, accumulating):
        check = next(c for c in report.checks if c.id == "correction_semantics")
        assert check.status == INFO, "correction is INFO, never pass/fail"
        assert "Not a verdict" in check.fix
    assert "supersedes" in next(
        c.detail for c in superseding.checks if c.id == "correction_semantics"
    )
    assert "accumulates" in next(
        c.detail for c in accumulating.checks if c.id == "correction_semantics"
    )
    # An accumulating store is not a contract failure — the pack judges it.
    assert accumulating.ok, [c.title for c in accumulating.failed]


def test_delete_check_uses_the_current_value_not_the_superseded_one(serve):
    # After the correction probe a superseding store has already dropped the
    # first value, so testing it here would pass trivially.
    report = run_doctor(serve(_NoOpDelete()), timeout=3)
    assert _status(report, "delete") == FAIL


def test_unreachable_shim_fails_at_the_first_check(tmp_path):
    cfg = tmp_path / "dead.yaml"
    cfg.write_text("base_url: http://127.0.0.1:9\nsupports_ttl: false\ntimeout_seconds: 2\n")
    report = run_doctor(HTTPAdapter(str(cfg)), timeout=2)
    assert _status(report, "reset") == FAIL
    # and it stops there rather than reporting a cascade of meaningless failures
    assert len(report.checks) == 1


# ------------------------------------- Option B: paraphrase degrades to NOT_TESTED


def test_paraphrasing_degrades_missing_current_fact_to_not_tested(serve):
    """Ruling 2026-07-28, Option B. A paraphrasing layer cannot be verified by
    the exact-match judge either way, so blaming it with a FAIL would report
    the judge's known recall limit as a defect in the customer's stack."""
    from memorycheck.judge import load_judge
    from memorycheck.oracle import (
        NOT_TESTED, PARAPHRASE_NOT_TESTED, detect_answering_layer, evaluate,
    )
    from memorycheck.report import PARAPHRASE_LIMITATIONS, build_summary, to_markdown
    from memorycheck.runner import run_suite
    from memorycheck.scenario import load_dir
    from pathlib import Path

    scenarios = load_dir(Path(__file__).resolve().parents[1] / "scenarios")
    adapter = serve(_Paraphrasing())
    suite = run_suite(scenarios[:1], adapter, load_judge("deterministic"),
                      seeds=1, baseline=False)
    layer = detect_answering_layer(suite["runs"])
    assert layer == PARAPHRASING

    findings = evaluate(suite["runs"], layer)
    mcf = [f for f in findings if f.check == "missing_current_fact"]
    assert mcf, "the scenario has must_use checks"
    assert all(f.status == NOT_TESTED for f in mcf), "must not FAIL a paraphraser"
    assert all(f.detail == PARAPHRASE_NOT_TESTED for f in mcf)
    absence_checks = [
        f for f in findings
        if f.check in {"scope_leakage", "deletion_residue", "stale_reuse", "expiry_leak"}
    ]
    assert absence_checks, "the scenario has stale/deleted opportunities"
    assert all(f.status == NOT_TESTED for f in absence_checks)
    assert all(f.detail == PARAPHRASE_NOT_TESTED for f in absence_checks)

    summary = build_summary(findings, [], adapter_name="http:x",
                            judge_name="deterministic-v0", seeds=1,
                            scenario_count=1, fail_on="p2", run_id=suite["run_id"],
                            answering_layer=layer)
    assert summary["answering_layer"] == PARAPHRASING
    assert summary["limitations"] == PARAPHRASE_LIMITATIONS
    assert summary["gate"]["verdict"] == "INCONCLUSIVE"
    assert "paraphrasing" in " ".join(summary["gate"]["inconclusive_reasons"])

    md = to_markdown(summary)
    assert "LIMITATIONS OF THIS RUN" in md
    assert "forgetting everything" in md
    assert "INCONCLUSIVE" in md
    # prominent: above the metrics table, not a footnote
    assert md.index("LIMITATIONS OF THIS RUN") < md.index("| Check |")


def test_quoting_runs_carry_no_limitations_block(serve):
    from memorycheck.report import build_summary, to_markdown

    summary = build_summary([], [], adapter_name="x", judge_name="j", seeds=1,
                            scenario_count=1, fail_on="p2", run_id="quoting-test",
                            answering_layer=QUOTING)
    assert summary["limitations"] == []
    assert "LIMITATIONS" not in to_markdown(summary)


def test_doctor_warn_recommends_the_store_first_path(serve):
    report = run_doctor(serve(_Paraphrasing()), timeout=3)
    fix = next(c.fix for c in report.checks if c.id == "answering_layer")
    assert "RECOMMENDED" in fix
    assert "point /query at" in fix
    assert "only once the semantic judge is calibrated" in fix
    assert "INCONCLUSIVE" in fix
    assert "non-zero exit" in fix
