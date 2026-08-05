"""Mem0 adapter tests, in two layers.

Offline: a fake client mimicking the mem0ai surface we depend on, pinning the
mapping contract (namespacing, scope isolation, verbatim storage, delete by
metadata key, reset by app_id). Runs everywhere, no credentials, no SDK.

Live: the real hosted platform, skipped cleanly when MEM0_API_KEY is absent so
CI stays green without credentials. The live tests assert only that the harness
executes end to end and that the honesty model holds (expiry -> NOT_TESTED,
since the adapter declares supports_ttl = False). They deliberately do NOT
assert that Mem0 passes the lifecycle checks: a genuine FAIL — say stale reuse
after a correction — is a benchmark finding to publish, not a reason to fail
the build or to tune the adapter into a pass. Run the scorecard with:

    memorycheck run scenarios --adapter mem0
"""

import itertools
import os
from pathlib import Path

import pytest

from memorycheck.adapters.base import AdapterError
from memorycheck.adapters.mem0 import Mem0Adapter
from memorycheck.judge import load_judge
from memorycheck.ledger import Scope
from memorycheck.oracle import NOT_TESTED, evaluate
from memorycheck.runner import run_suite
from memorycheck.scenario import load_dir

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"

ALICE = Scope("acme", "alice")
BOB = Scope("acme", "bob")
OTHER_TENANT = Scope("globex", "alice")


# --------------------------------------------------------------- offline layer


class FakeMem0Client:
    """Minimal stand-in for mem0ai's MemoryClient: stores rows verbatim and
    filters strictly by the identifiers we pass. A real provider may of course
    behave differently — that is exactly what the live run measures."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._ids = itertools.count(1)
        self.delete_all_calls = 0

    def add(self, messages, user_id=None, app_id=None, metadata=None, infer=None, **kw):
        assert infer is False, "adapter must store verbatim (infer=False)"
        self.rows.append(
            {
                "id": f"m{next(self._ids)}",
                "memory": messages,
                "metadata": metadata or {},
                "user_id": user_id,
                "app_id": app_id,
            }
        )
        return {"results": []}

    def _scoped(self, filters):
        f = filters or {}
        rows = self.rows
        if "user_id" in f:
            rows = [r for r in rows if r["user_id"] == f["user_id"]]
        if "app_id" in f:
            rows = [r for r in rows if r["app_id"] == f["app_id"]]
        return list(rows)

    def get_all(self, filters=None, **kw):
        return {"results": self._scoped(filters)}

    def search(self, query, filters=None, top_k=None, **kw):
        return {"results": self._scoped(filters)}

    def delete(self, memory_id, **kw):
        self.rows = [r for r in self.rows if r["id"] != memory_id]
        return {}

    def delete_all(self, app_id=None, **kw):
        self.delete_all_calls += 1
        self.rows = [r for r in self.rows if r["app_id"] != app_id]
        return {}


@pytest.fixture()
def offline_adapter(monkeypatch):
    # Bypass __init__ so the fixture needs neither the SDK nor a key. The
    # fake client is synchronous, so reset()'s settle delays only add dead
    # wall-clock here — stub them out; the live run covers the real timing.
    monkeypatch.setattr("memorycheck.adapters.mem0.time.sleep", lambda _: None)
    adapter = Mem0Adapter.__new__(Mem0Adapter)
    adapter._client = FakeMem0Client()
    adapter._namespace = "default"
    adapter.reset_convergence = []
    return adapter


def test_ttl_is_declared_unsupported():
    # Mem0 expiry is wall-clock; memorycheck time is logical. Declaring this
    # honestly is what makes expiry checks report NOT_TESTED.
    assert Mem0Adapter.supports_ttl is False


def test_identifiers_isolate_users_tenants_and_runs(offline_adapter):
    a = offline_adapter
    a.reset("memorycheck::001::0")
    assert a._user_id(ALICE) != a._user_id(BOB)
    assert a._user_id(ALICE) != a._user_id(OTHER_TENANT)
    first_run = a._user_id(ALICE)
    a.reset("memorycheck::001::1")
    assert a._user_id(ALICE) != first_run, "runs must not collide"


def test_identifier_encoding_keeps_punctuation_variants_distinct(offline_adapter):
    a = offline_adapter
    a.reset("scope-collision")
    tenant_ids = ["tenant/a", "tenant-a", "tenant.a", "tenant a"]
    mapped = {a._user_id(Scope(tenant, "alice")) for tenant in tenant_ids}
    assert len(mapped) == len(tenant_ids)


def test_write_then_query_returns_the_value(offline_adapter):
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    result = a.query(ALICE, "Which plan is this user on?")
    assert "starter-legacy-2024" in result.answer
    assert result.retrieved[0]["metadata"]["key"] == "plan"


def test_other_scopes_cannot_see_the_value(offline_adapter):
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    assert "starter-legacy-2024" not in a.query(BOB, "plan?").answer
    assert "starter-legacy-2024" not in a.query(OTHER_TENANT, "plan?").answer


def test_delete_removes_only_the_named_key(offline_adapter):
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    a.write(ALICE, "city", "reykjavik-77")
    a.delete(ALICE, "plan")
    answer = a.query(ALICE, "anything?").answer
    assert "starter-legacy-2024" not in answer
    assert "reykjavik-77" in answer


def test_reset_clears_the_whole_namespace(offline_adapter):
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    a.reset("ns")
    assert "starter-legacy-2024" not in a.query(ALICE, "anything?").answer


def test_reset_skips_delete_on_a_fresh_namespace(offline_adapter):
    # Mem0 deletes asynchronously and an in-flight delete_all can swallow the
    # write that follows it, which showed up as phantom missing_current_fact
    # failures. A fresh namespace has nothing to clear, so it must not issue a
    # delete at all — that is what keeps the race off the common path.
    a = offline_adapter
    a.reset("fresh-namespace")
    assert a._client.delete_all_calls == 0


def test_reset_deletes_when_the_namespace_has_residue(offline_adapter):
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    before = a._client.delete_all_calls
    a.reset("ns")  # same namespace, now holds residue
    assert a._client.delete_all_calls == before + 1


class ReapingClient(FakeMem0Client):
    """A client whose delete_all keeps swallowing writes for `reap_writes`
    more calls *after* the namespace already reads empty.

    This is the measured Mem0 behaviour the sentinel exists for: 6/14 writes
    lost when a delete_all preceded them. Reading empty is not proof the
    delete finished.
    """

    def __init__(self, reap_writes: int) -> None:
        super().__init__()
        self.reap_budget = reap_writes
        self._remaining = 0
        self.swallowed = 0

    def delete_all(self, app_id=None, **kw):
        result = super().delete_all(app_id=app_id, **kw)
        # The namespace now reads empty, but the delete keeps reaping — which
        # is precisely the state that reading empty fails to detect.
        self._remaining = self.reap_budget
        return result

    def add(self, messages, **kw):
        if self._remaining > 0:
            self._remaining -= 1
            self.swallowed += 1
            return {"results": []}  # acknowledged, never stored
        return super().add(messages, **kw)


def _adapter_with(client):
    a = Mem0Adapter.__new__(Mem0Adapter)
    a._client = client
    a._namespace = "default"
    a.reset_convergence = []
    return a


def test_reset_proves_the_namespace_is_writable_again(monkeypatch):
    """The sentinel absorbs the reaped writes so the scenario's first real
    write survives — the whole point of the fix."""
    monkeypatch.setattr("memorycheck.adapters.mem0.time.sleep", lambda _: None)
    a = _adapter_with(ReapingClient(reap_writes=2))
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    a.reset("ns")  # now has residue -> issues delete_all -> sentinel path

    # The reaping window was consumed by sentinel attempts, not by the
    # scenario's fact. Without the sentinel this write would have vanished
    # and been scored as a missing current fact against the provider.
    a.write(ALICE, "plan", "growth-2026")
    assert "growth-2026" in a.query(ALICE, "which plan?").answer
    assert a._client.swallowed >= 1


def test_reset_leaves_no_sentinel_behind(monkeypatch):
    monkeypatch.setattr("memorycheck.adapters.mem0.time.sleep", lambda _: None)
    a = _adapter_with(FakeMem0Client())
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    a.reset("ns")
    assert not [r for r in a._client.rows if "sentinel" in str(r.get("memory", ""))]


def test_reset_records_the_empty_to_writable_gap(monkeypatch):
    monkeypatch.setattr("memorycheck.adapters.mem0.time.sleep", lambda _: None)
    a = _adapter_with(FakeMem0Client())
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    assert a.reset_convergence == []      # no delete yet, nothing to measure
    a.reset("ns")
    assert len(a.reset_convergence) == 1
    assert "empty_to_writable_s" in a.reset_convergence[0]


def test_reset_raises_rather_than_starting_an_unwritable_scenario(monkeypatch):
    """If the sentinel never lands, reset must abort. Proceeding would lose
    the scenario's first writes and score them against the provider."""
    monkeypatch.setattr("memorycheck.adapters.mem0.time.sleep", lambda _: None)
    monkeypatch.setattr("memorycheck.adapters.mem0._CONVERGE_TIMEOUT", 0.05)
    a = _adapter_with(ReapingClient(reap_writes=10_000))  # never stops reaping
    a.reset("ns")
    a._client.rows.append(
        {"id": "residue", "memory": "x", "metadata": {}, "user_id": "u",
         "app_id": a._app_id()}
    )
    with pytest.raises(AdapterError, match="not accepting writes"):
        a.reset("ns")


# ------------------------------------------------------------------ live layer

live = pytest.mark.skipif(
    not os.environ.get("MEM0_API_KEY"),
    reason="MEM0_API_KEY not set; skipping live Mem0 adapter test",
)


def _live_suite():
    scenarios = load_dir(SCENARIOS)
    suite = run_suite(
        scenarios, Mem0Adapter(), load_judge("deterministic"), seeds=1, baseline=False
    )
    return scenarios, suite


@live
def test_full_suite_runs_end_to_end_over_mem0():
    scenarios, suite = _live_suite()
    runs = suite["runs"]
    assert len(runs) == len(scenarios)
    assert all(run.observations for run in runs), "every scenario has query steps"
    assert evaluate(runs), "the suite must produce findings"


@live
def test_expiry_is_not_tested_because_ttl_unsupported():
    _, suite = _live_suite()
    expiry = [f for f in evaluate(suite["runs"]) if f.check == "expiry_leak"]
    assert expiry, "the TTL scenario should create expiry_leak opportunities"
    assert all(f.status == NOT_TESTED for f in expiry)
