"""Invariant 10: never judge a provider on a race.

Adapters confirm their own writes and deletes have landed by polling for the
expected transition, never by sleeping a fixed interval. Failure to converge is
reported with its latency rather than passing silently as an empty store.

These tests also pin the boundary that keeps the rule honest: convergence
polling must not be able to launder a genuine failure into a pass.
"""

import time

import pytest

from memorycheck.adapters.base import AdapterError, poll_until
from memorycheck.ledger import Scope

ALICE = Scope("acme", "alice")


def test_already_converged_costs_no_wait():
    # A synchronous provider must pay nothing for the guard.
    started = time.monotonic()
    ok, waited = poll_until(lambda: True, timeout=5, interval=0.5)
    assert ok is True
    assert waited < 0.1
    assert time.monotonic() - started < 0.1


def test_converges_once_the_state_arrives():
    calls = {"n": 0}

    def ready():
        calls["n"] += 1
        return calls["n"] >= 3

    ok, waited = poll_until(ready, timeout=5, interval=0.01)
    assert ok is True
    assert calls["n"] == 3
    assert waited > 0


def test_reports_failure_with_its_latency_rather_than_hanging():
    ok, waited = poll_until(lambda: False, timeout=0.2, interval=0.05)
    assert ok is False
    # The elapsed time is the reportable number — never a silent zero.
    assert waited >= 0.2


def test_timeout_is_respected_even_with_a_long_interval():
    started = time.monotonic()
    ok, waited = poll_until(lambda: False, timeout=0.2, interval=10)
    elapsed = time.monotonic() - started
    assert ok is False
    assert elapsed < 1.0, "interval must not overshoot the timeout"
    assert waited >= 0.2


# ------------------------------------------------------- adapter integration


class _FlakyMem0:
    """Mem0-shaped fake whose writes become visible only after N reads."""

    def __init__(self, delay_reads: int):
        self.delay, self.reads, self.rows = delay_reads, 0, []

    def add(self, messages, user_id=None, app_id=None, metadata=None, infer=None, **kw):
        self.rows.append(
            {"id": f"m{len(self.rows)}", "memory": messages,
             "metadata": metadata or {}, "user_id": user_id, "app_id": app_id}
        )
        return {}

    def get_all(self, filters=None, **kw):
        self.reads += 1
        if self.reads <= self.delay:
            return {"results": []}  # not yet visible
        uid = (filters or {}).get("user_id")
        return {"results": [r for r in self.rows if r["user_id"] == uid]}

    def search(self, query, filters=None, top_k=None, **kw):
        return self.get_all(filters=filters)

    def delete(self, memory_id, **kw):
        self.rows = [r for r in self.rows if r["id"] != memory_id]
        return {}

    def delete_all(self, app_id=None, **kw):
        self.rows = [r for r in self.rows if r["app_id"] != app_id]
        return {}


def _mem0_adapter(client):
    from memorycheck.adapters.mem0 import Mem0Adapter

    a = Mem0Adapter.__new__(Mem0Adapter)
    a._client = client
    a._namespace = "ns"
    return a


def test_mem0_write_waits_for_an_eventually_consistent_store():
    # The value is invisible for the first two reads; the write must not
    # return until it is actually retrievable.
    adapter = _mem0_adapter(_FlakyMem0(delay_reads=2))
    adapter.write(ALICE, "plan", "starter-legacy-2024")
    assert "starter-legacy-2024" in adapter.query(ALICE, "Which plan?").answer


def test_mem0_write_that_never_lands_raises_with_the_latency():
    # A store that silently drops writes must abort the run, not yield an
    # empty answer that would be scored as the provider forgetting the fact.
    adapter = _mem0_adapter(_FlakyMem0(delay_reads=10_000))
    import memorycheck.adapters.mem0 as mem0_mod

    original = mem0_mod._CONVERGE_TIMEOUT
    mem0_mod._CONVERGE_TIMEOUT = 0.2
    try:
        with pytest.raises(AdapterError) as excinfo:
            adapter.write(ALICE, "plan", "starter-legacy-2024")
    finally:
        mem0_mod._CONVERGE_TIMEOUT = original
    assert "not retrievable" in str(excinfo.value)
    assert "s —" in str(excinfo.value), "the error must carry the latency"


def test_convergence_cannot_launder_a_missing_current_fact():
    """The boundary that keeps invariant 10 honest.

    Adapters confirm their *own* writes. They must not poll a query until a
    value the ledger expects appears — that would convert a genuine
    missing_current_fact into a pass. Here the write lands, the store then
    loses it, and query reports the truth instead of waiting for it to return.
    """
    client = _FlakyMem0(delay_reads=0)
    adapter = _mem0_adapter(client)
    adapter.write(ALICE, "plan", "starter-legacy-2024")
    client.rows.clear()  # provider drops it after acknowledging
    answer = adapter.query(ALICE, "Which plan?").answer
    assert "starter-legacy-2024" not in answer, "query must report reality"
