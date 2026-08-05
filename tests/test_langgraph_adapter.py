"""LangGraph store adapter tests.

These run everywhere, including CI without the `langgraph` extra installed:
the fake below implements the slice of `BaseStore` the adapter uses, and the
adapter is constructed via `__new__` so no import of `langgraph` occurs.

The fake mirrors two behaviours measured against the real stores rather than
assumed — `list_namespaces` paginates with a default limit that hides rows,
and a keyed `put` overwrites rather than accumulating. A fake that got either
wrong would validate a fiction.
"""

from pathlib import Path

import pytest

from memorycheck.adapters.langgraph import LangGraphAdapter
from memorycheck.judge import load_judge
from memorycheck.ledger import Scope
from memorycheck.oracle import FAIL, evaluate
from memorycheck.runner import run_suite
from memorycheck.scenario import load_dir

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"

ALICE = Scope("acme", "alice")
BOB = Scope("acme", "bob")
OTHER_TENANT = Scope("globex", "alice")


class FakeItem:
    def __init__(self, key, value):
        self.key, self.value = key, value


class FakeStore:
    """The slice of BaseStore the adapter uses, with the real semantics:
    keyed overwrite, prefix search, and paginated namespace listing."""

    #: real default is 100; kept small so tests exercise the paging path
    DEFAULT_NS_LIMIT = 3

    def __init__(self):
        self.data: dict[tuple, dict[str, dict]] = {}

    def put(self, namespace, key, value, index=None, *, ttl=None):
        self.data.setdefault(tuple(namespace), {})[key] = value  # overwrites

    def get(self, namespace, key, *, refresh_ttl=None):
        v = self.data.get(tuple(namespace), {}).get(key)
        return FakeItem(key, v) if v is not None else None

    def delete(self, namespace, key):
        self.data.get(tuple(namespace), {}).pop(key, None)

    def search(self, namespace_prefix, *, query=None, filter=None, limit=10, offset=0):
        pre = tuple(namespace_prefix)
        out = [
            FakeItem(k, v)
            for ns, items in self.data.items()
            if ns[: len(pre)] == pre
            for k, v in items.items()
        ]
        return out[offset : offset + limit]

    def list_namespaces(self, *, prefix=None, suffix=None, max_depth=None,
                        limit=None, offset=0):
        limit = self.DEFAULT_NS_LIMIT if limit is None else limit
        pre = tuple(prefix or ())
        matches = sorted(ns for ns in self.data if ns[: len(pre)] == pre)
        return matches[offset : offset + limit]


@pytest.fixture()
def adapter():
    a = LangGraphAdapter.__new__(LangGraphAdapter)  # no langgraph import
    a._store = FakeStore()
    a._closer = None
    a._root = "memorycheck"
    a.name = "langgraph:fake"
    return a


def test_ttl_is_declared_unsupported():
    # Observed, not assumed: InMemoryStore.supports_ttl is False and raises on
    # put(ttl=...); SqliteStore advertises TTL but did not enforce it without a
    # ttl_config, and it is wall-clock while memorycheck time is logical.
    assert LangGraphAdapter.supports_ttl is False


def test_scope_maps_to_a_namespace_tuple(adapter):
    adapter.reset("ns")
    assert adapter._ns(ALICE) != adapter._ns(BOB)
    assert adapter._ns(ALICE) != adapter._ns(OTHER_TENANT)


def test_identifier_encoding_keeps_punctuation_variants_distinct(adapter):
    adapter.reset("scope-collision")
    tenant_ids = ["tenant/a", "tenant-a", "tenant.a", "tenant a"]
    mapped = {adapter._ns(Scope(tenant, "alice")) for tenant in tenant_ids}
    assert len(mapped) == len(tenant_ids)


def test_write_then_query_returns_the_value(adapter):
    adapter.reset("ns")
    adapter.write(ALICE, "plan", "starter-legacy-2024")
    assert "starter-legacy-2024" in adapter.query(ALICE, "Which plan?").answer


def test_other_scopes_cannot_see_the_value(adapter):
    adapter.reset("ns")
    adapter.write(ALICE, "plan", "starter-legacy-2024")
    assert "starter-legacy-2024" not in adapter.query(BOB, "Which plan?").answer
    assert "starter-legacy-2024" not in adapter.query(OTHER_TENANT, "Which plan?").answer


def test_correction_supersedes_by_construction(adapter):
    # The store is keyed, so a correction overwrites. This is a property of the
    # store, not something the adapter arranges.
    adapter.reset("ns")
    adapter.write(ALICE, "plan", "starter-legacy-2024")
    adapter.write(ALICE, "plan", "scale-annual-2026")
    answer = adapter.query(ALICE, "Which plan?").answer
    assert "scale-annual-2026" in answer
    assert "starter-legacy-2024" not in answer


def test_delete_removes_only_the_named_key(adapter):
    adapter.reset("ns")
    adapter.write(ALICE, "plan", "starter-legacy-2024")
    adapter.write(ALICE, "city", "reykjavik-77")
    adapter.delete(ALICE, "plan")
    answer = adapter.query(ALICE, "anything?").answer
    assert "starter-legacy-2024" not in answer
    assert "reykjavik-77" in answer


def test_reset_pages_through_namespaces(adapter):
    """list_namespaces defaults to a limit that hides rows — measured at 100
    returning 100 of 121. A reset() that cannot see a namespace cannot clear
    it, and the residue would later be scored against the store."""
    adapter.reset("ns")
    for i in range(10):  # more than FakeStore.DEFAULT_NS_LIMIT
        adapter.write(Scope("acme", f"user{i}"), "plan", f"value-{i}")
    assert len(adapter._namespaces()) == 10, "listing must follow pagination"
    adapter.reset("ns")
    for i in range(10):
        assert adapter.query(Scope("acme", f"user{i}"), "plan?").answer.startswith(
            "I don't have"
        )


def test_reset_is_scoped_and_spares_other_roots(adapter):
    # SqliteStore persists, so a global clear would delete rows belonging to
    # whoever owns the file.
    adapter.reset("run-one")
    adapter.write(ALICE, "plan", "evergreen-4102")
    foreign = ("someone-elses-root", "acme", "alice")
    adapter._store.put(foreign, "plan", {"key": "plan", "value": "not-ours-9001"})
    adapter.reset("run-two")
    assert adapter._store.data.get(foreign, {}).get("plan", {}).get("value") == "not-ours-9001"


def test_full_suite_runs_and_passes_against_a_keyed_store(adapter):
    """End-to-end through the runner: rescope replay, advance_time, every op."""
    scenarios = load_dir(SCENARIOS)
    suite = run_suite(scenarios, adapter, load_judge("deterministic"), seeds=1,
                      baseline=False)
    assert len(suite["runs"]) == len(scenarios)
    findings = evaluate(suite["runs"])
    assert findings
    assert [f for f in findings if f.status == FAIL] == []
