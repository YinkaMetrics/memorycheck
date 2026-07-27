"""Zep adapter tests, offline and live.

Offline: a fake client shaped like the parts of `zep_cloud` the adapter uses,
pinning the mapping contract — graph-per-scope isolation, verbatim episode
writes, delete removing a key's episodes *and* the edges derived from them,
reset dropping the namespace, and edges Zep marks invalid/expired being
excluded from answers. Runs everywhere, no credentials, no SDK.

Live: skipped unless ZEP_API_KEY is set. Asserts only that the suite executes
end to end and that expiry reports NOT_TESTED. As with Mem0, it never asserts
that Zep passes the lifecycle — a genuine FAIL is a benchmark finding.

NOTE: at time of writing the live layer has never been executed — no Zep
credential was available. The offline layer pins intent against the real SDK
signatures; it cannot prove the live service behaves as assumed. See
HANDOFF.md for the specific assumptions awaiting live confirmation.
"""

import itertools
import os
from pathlib import Path

import pytest

from memorycheck.adapters.zep import ZepAdapter
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


class FakeEpisode:
    def __init__(self, uuid_, content):
        self.uuid_, self.content = uuid_, content


class FakeEdge:
    def __init__(self, uuid_, fact, episodes, invalid_at=None, expired_at=None):
        self.uuid_, self.fact, self.episodes = uuid_, fact, episodes
        self.invalid_at, self.expired_at = invalid_at, expired_at
        self.name = "HAS"


class FakeGraph:
    def __init__(self, graph_id):
        self.graph_id = graph_id


class _Ns:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeNotFound(Exception):
    """Mimics Zep's 404 for a graph that does not exist.

    Carries `status_code` rather than subclassing the SDK's NotFoundError so
    these tests keep running where the zep extra is not installed — CI installs
    only [dev]. The adapter treats a 404 as legitimately-empty and anything
    else as a failure that must abort.
    """

    status_code = 404


class FakeZepClient:
    """Mimics zep_cloud's graph namespace: text goes in as an episode, and one
    derived edge is created per episode (Zep would extract via LLM; the shape
    is what the adapter depends on, not the phrasing)."""

    def __init__(self):
        self.graphs: dict[str, dict] = {}
        self._ids = itertools.count(1)
        self.graph = _Ns(
            create=self._create,
            delete=self._delete_graph,
            add=self._add,
            search=self._search,
            list_all=self._list_all,
            episode=_Ns(
                get_by_graph_id=self._episodes_by_graph,
                delete=self._delete_episode,
            ),
            edge=_Ns(
                get_by_graph_id=self._edges_by_graph,
                delete=self._delete_edge,
            ),
        )

    # -- graph lifecycle
    def _create(self, *, graph_id, **kw):
        if graph_id in self.graphs:
            raise RuntimeError("graph exists")
        self.graphs[graph_id] = {"episodes": [], "edges": []}
        return FakeGraph(graph_id)

    def _delete_graph(self, graph_id, **kw):
        self.graphs.pop(graph_id, None)
        return {}

    def _list_all(self, **kw):
        return _Ns(graphs=[FakeGraph(g) for g in self.graphs])

    # -- writes
    def _add(self, *, graph_id, type, data, **kw):
        g = self.graphs.setdefault(graph_id, {"episodes": [], "edges": []})
        n = next(self._ids)
        ep = FakeEpisode(f"ep{n}", data)
        g["episodes"].append(ep)
        # Derived edge carries the value verbatim inside a phrased fact.
        g["edges"].append(FakeEdge(f"ed{n}", f"Fact: {data}", [ep.uuid_]))
        return {}

    # -- reads
    def _episodes_by_graph(self, graph_id, *, lastn=None, **kw):
        g = self.graphs.get(graph_id)
        if g is None:
            raise FakeNotFound()
        return _Ns(episodes=list(g["episodes"]))

    def _edges_by_graph(self, graph_id, *, limit=None, **kw):
        g = self.graphs.get(graph_id)
        if g is None:
            raise FakeNotFound()
        return list(g["edges"])

    def _search(self, *, graph_id, query, scope=None, limit=None, **kw):
        g = self.graphs.get(graph_id)
        if g is None:
            raise FakeNotFound()
        return _Ns(edges=list(g["edges"]))

    # -- deletes
    def _delete_episode(self, uuid_, **kw):
        for g in self.graphs.values():
            g["episodes"] = [e for e in g["episodes"] if e.uuid_ != uuid_]
        return {}

    def _delete_edge(self, uuid_, **kw):
        for g in self.graphs.values():
            g["edges"] = [e for e in g["edges"] if e.uuid_ != uuid_]
        return {}


@pytest.fixture()
def offline_adapter():
    adapter = ZepAdapter.__new__(ZepAdapter)  # bypass __init__: no SDK, no key
    adapter._client = FakeZepClient()
    adapter._namespace = "default"
    adapter._known_graphs = set()
    return adapter


def test_absence_detection_works_without_the_zep_sdk(monkeypatch):
    """CI installs only [dev], so zep_cloud is absent there while present on a
    dev machine. An earlier version checked the SDK's exception type first and
    returned False on ImportError, which skipped the status-code check — every
    404 became a hard failure and the suite passed locally but broke in CI.
    Simulate the import failing so the two environments cannot diverge again."""
    import builtins

    from memorycheck.adapters import zep as zep_mod

    real_import = builtins.__import__

    def no_zep(name, *args, **kw):
        if name.startswith("zep_cloud"):
            raise ImportError("simulated: zep extra not installed")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", no_zep)
    assert zep_mod._absent(FakeNotFound()) is True, "404 must read as absent"
    assert zep_mod._absent(RuntimeError("boom")) is False, "other errors must not"


def test_ttl_is_declared_unsupported():
    # Zep validity is wall-clock, memorycheck time is logical.
    assert ZepAdapter.supports_ttl is False


def test_each_scope_gets_its_own_graph(offline_adapter):
    a = offline_adapter
    a.reset("memorycheck::001::0")
    assert a._graph_id(ALICE) != a._graph_id(BOB)
    assert a._graph_id(ALICE) != a._graph_id(OTHER_TENANT)
    first_run = a._graph_id(ALICE)
    a.reset("memorycheck::001::1")
    assert a._graph_id(ALICE) != first_run, "runs must not collide"


def test_write_then_query_returns_the_value(offline_adapter):
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    assert "starter-legacy-2024" in a.query(ALICE, "Which plan?").answer


def test_other_scopes_cannot_see_the_value(offline_adapter):
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    assert "starter-legacy-2024" not in a.query(BOB, "Which plan?").answer
    assert "starter-legacy-2024" not in a.query(OTHER_TENANT, "Which plan?").answer


def test_delete_removes_episodes_and_derived_edges(offline_adapter):
    # Deleting the episode alone would leave the extracted edge standing and
    # the fact would keep driving answers.
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    a.delete(ALICE, "plan")
    graph = a._client.graphs[a._graph_id(ALICE)]
    assert graph["episodes"] == []
    assert graph["edges"] == []
    assert "starter-legacy-2024" not in a.query(ALICE, "Which plan?").answer


def test_delete_removes_only_the_named_key(offline_adapter):
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    a.write(ALICE, "city", "reykjavik-77")
    a.delete(ALICE, "plan")
    answer = a.query(ALICE, "anything?").answer
    assert "starter-legacy-2024" not in answer
    assert "reykjavik-77" in answer


def test_edges_zep_marks_invalid_are_not_used(offline_adapter):
    # Zep publishes invalid_at/expired_at; the adapter honours that signal.
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    for edge in a._client.graphs[a._graph_id(ALICE)]["edges"]:
        edge.invalid_at = "2026-01-01T00:00:00Z"
    assert "starter-legacy-2024" not in a.query(ALICE, "Which plan?").answer


def test_reset_drops_the_namespace(offline_adapter):
    a = offline_adapter
    a.reset("ns")
    a.write(ALICE, "plan", "starter-legacy-2024")
    a.reset("ns")
    assert "starter-legacy-2024" not in a.query(ALICE, "Which plan?").answer


def test_reset_leaves_other_namespaces_alone(offline_adapter):
    a = offline_adapter
    a.reset("keep-me")
    a.write(ALICE, "plan", "evergreen-4102")
    kept = a._graph_id(ALICE)
    a.reset("other-run")
    assert kept in a._client.graphs, "reset must not touch a foreign namespace"


def test_full_suite_drives_the_adapter_without_error(offline_adapter):
    """Exercise every op the runner emits — including rescope replay and
    advance_time — against the fake. Proves the adapter satisfies the runner's
    contract without needing a credential. Says nothing about whether the real
    service behaves like the fake."""
    scenarios = load_dir(SCENARIOS)
    suite = run_suite(
        scenarios, offline_adapter, load_judge("deterministic"),
        seeds=1, baseline=False,
    )
    assert len(suite["runs"]) == len(scenarios)
    assert all(run.observations for run in suite["runs"])
    assert evaluate(suite["runs"])


# ------------------------------------------------------------------ live layer

live = pytest.mark.skipif(
    not os.environ.get("ZEP_API_KEY"),
    reason="ZEP_API_KEY not set; skipping live Zep adapter test",
)


def _live_suite():
    scenarios = load_dir(SCENARIOS)
    suite = run_suite(
        scenarios, ZepAdapter(), load_judge("deterministic"), seeds=1, baseline=False
    )
    return scenarios, suite


@live
def test_full_suite_runs_end_to_end_over_zep():
    scenarios, suite = _live_suite()
    runs = suite["runs"]
    assert len(runs) == len(scenarios)
    assert all(run.observations for run in runs)
    assert evaluate(runs)


@live
def test_expiry_is_not_tested_because_ttl_unsupported():
    _, suite = _live_suite()
    expiry = [f for f in evaluate(suite["runs"]) if f.check == "expiry_leak"]
    assert expiry, "the TTL scenarios should create expiry_leak opportunities"
    assert all(f.status == NOT_TESTED for f in expiry)
