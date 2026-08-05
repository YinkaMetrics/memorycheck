"""Mem0 adapter: run the lifecycle suite against the hosted Mem0 platform.

Mem0 is a memory *store*, not an agent, so this adapter supplies the missing
deterministic answering layer itself: a query does a Mem0 `search` scoped to
the subject, then templates the returned memories into an answer *exactly*
the way `ReferenceAdapter` does. Nothing here grades Mem0 — the runner feeds
that answer to the judge, which is scored against the ledger. What the
benchmark actually measures is whether Mem0's *retrieval* still surfaces
values we have superseded, deleted, or that belong to another scope.

Design (fixed for v0):

* Facts are stored verbatim as ``"<key>: <value>"`` with ``infer=False`` so
  Mem0's extraction LLM never rewrites them — the deterministic judge needs
  the exact value string back. ``metadata={"key": <key>}`` lets delete find a
  key's memories again (Mem0 exposes no key concept of its own).
* Scope maps onto Mem0 identifiers. ``tenant_id``/``user_id`` are folded into
  a single Mem0 ``user_id``; every write is also tagged with an ``app_id``
  derived from a losslessly encoded namespace containing a unique invocation
  id, so ``reset()`` can wipe one scenario run in one ``delete_all`` without
  merging punctuation-distinct scopes or colliding with another invocation.
* ``supports_ttl = False``. Mem0 expiry is wall-clock; ours is logical time,
  so it is genuinely inexpressible here and expiry checks must report
  NOT_TESTED — the honesty model, not a gap to paper over.

Credentials come from ``MEM0_API_KEY``. The SDK is an optional extra
(``pip install -e ".[mem0]"``) and is imported lazily so the core never
depends on it.
"""

from __future__ import annotations

import os
import sys
import time
import uuid

from ..ledger import Scope
from .base import (
    AdapterError, MemoryAdapter, QueryResult, encode_identifier, poll_until,
)

# Cap on how many memories a scoped query pulls back. Scenarios hold a handful
# of facts per scope; this is a safety ceiling, not a relevance filter — we do
# not want a low top_k to silently drop the current fact and fake a pass.
_SEARCH_TOP_K = 100

# Mem0 applies writes and deletes asynchronously, so every mutation is
# confirmed by polling for its own effect before returning (invariant 10).
# Measured behaviour: writes land sub-second, deletes take a few seconds; the
# ceilings are generous so a slow day is waited out rather than mis-scored.
_CONVERGE_TIMEOUT = 30.0
_CONVERGE_INTERVAL = 0.5

# Key/scope fragment for the post-delete_all sentinel. Distinctive so it can
# never collide with a scenario value, and scoped to identifiers no scenario
# maps onto, so a stray sentinel could not be read by a scenario query.
_SENTINEL = "mc_reset_sentinel"

# Per-attempt window for a sentinel write to become retrievable. Short, because
# a swallowed write never appears however long we wait — the useful move is to
# try again, not to wait longer. The retry loop overall stays bounded by
# _CONVERGE_TIMEOUT.
_SENTINEL_ATTEMPT_TIMEOUT = 5.0


def _call(what: str, fn, *args, **kwargs):
    """Invoke a Mem0 SDK call, turning any failure into a legible AdapterError.

    Every call here is a hard dependency of the measurement: a failed read is
    not an empty store and a failed write is not a forgetful provider. Wrapping
    them means a run aborts with a stated reason — quota exhausted, credential
    rejected, service down — instead of dying opaquely or, worse, degrading
    into numbers that get read as findings about the provider.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        status = getattr(e, "status_code", None)
        detail = f"HTTP {status}: " if status else f"{e.__class__.__name__}: "
        raise AdapterError(f"mem0 {what} failed — {detail}{str(e)[:300]}") from e


class Mem0Adapter(MemoryAdapter):
    name = "mem0"
    supports_ttl = False  # logical-time expiry is not expressible on Mem0

    def __init__(self) -> None:
        if not os.environ.get("MEM0_API_KEY"):
            raise AdapterError(
                "Mem0 adapter needs MEM0_API_KEY in the environment "
                "(get a key at https://app.mem0.ai)."
            )
        try:
            from mem0 import MemoryClient
        except ImportError as e:  # pragma: no cover - exercised only without extra
            raise AdapterError(
                "Mem0 SDK not installed. Install the optional extra: "
                'pip install -e ".[mem0]"'
            ) from e
        self._client = MemoryClient()  # reads MEM0_API_KEY from the environment
        self._namespace = "default"
        #: One entry per reset that actually deleted: how long the namespace
        #: took to read empty, and how much longer until it accepted a write.
        #: Read this after a run — it is the empirical answer to "how long does
        #: a delete_all keep reaping", accumulated at no extra cost.
        self.reset_convergence: list[dict] = []

    # --------------------------------------------------------------- scoping

    def _app_id(self) -> str:
        """One id per run namespace — the handle reset() wipes."""
        return f"mc_{encode_identifier(self._namespace)}"

    def _user_id(self, scope: Scope) -> str:
        """Fold namespace + tenant + user into a single Mem0 identifier, so a
        different run, tenant, or user can never see this scope's memories."""
        return (
            f"{self._app_id()}__{encode_identifier(scope.tenant_id)}"
            f"__{encode_identifier(scope.user_id)}"
        )

    # ------------------------------------------------------------- lifecycle

    def reset(self, namespace: str) -> None:
        """Clear this run's namespace — but only if it actually holds anything.

        Mem0's delete_all is applied asynchronously, and a write issued
        immediately afterwards can be swallowed by the in-flight delete:
        measured at 6/14 writes lost when a delete_all precedes them, versus
        0/10 with no preceding delete. That race manifested as phantom
        `missing_current_fact` failures — the harness blaming the provider for
        losing a fact the harness had itself just deleted.

        So: read the namespace first, and skip the delete entirely when it is
        empty (the overwhelmingly common case — namespaces are per scenario and
        seed). When there really is residue, delete it and wait for the
        namespace to read empty.

        Reading empty is necessary but **not sufficient**: the delete can keep
        reaping writes after it has stopped being visible to search, which is
        how the 6/14 above happened. So when we did delete, we prove the
        namespace is writable again by writing a sentinel and polling for it,
        then removing it. That is correct at any propagation latency, unlike a
        fixed sleep — a sleep is either too short to be safe or too long to be
        affordable, and it can never tell you which.
        """
        self._namespace = namespace or "default"
        app_id = self._app_id()
        if not self._namespace_rows(app_id):
            return  # nothing to clear, so nothing to race, and nothing to prove
        _call("reset delete_all", self._client.delete_all, app_id=app_id)
        cleared, waited = poll_until(
            lambda: not self._namespace_rows(app_id),
            timeout=_CONVERGE_TIMEOUT,
            interval=_CONVERGE_INTERVAL,
        )
        if not cleared:
            raise AdapterError(
                f"mem0 reset: namespace {app_id!r} still holds memories after "
                f"{waited:.1f}s — refusing to run, since leftovers would be "
                "scored against the provider"
            )
        self._confirm_namespace_writable(app_id, empty_after=waited)

    def _confirm_namespace_writable(self, app_id: str, empty_after: float) -> None:
        """Prove a just-wiped namespace accepts writes again, then clean up.

        Called only when `reset()` actually issued a delete_all, so the common
        skip path costs nothing. Writes one sentinel, polls for it, deletes it.

        Why a sentinel rather than a settle: the question is not "has enough
        time passed" but "does a write survive", and only a write can answer
        it. This is correct whether propagation takes 200ms or 20s, and it
        reports the number instead of assuming one.

        The sentinel is removed with a per-key delete by id, not a delete_all,
        so the cleanup cannot restart the condition it just cleared — arms
        (a)-(c) measured per-key delete followed by an immediate write as
        landing at 0s, three for three.
        """
        sentinel_scope = Scope(tenant_id=f"{_SENTINEL}_t", user_id=f"{_SENTINEL}_u")
        uid = self._user_id(sentinel_scope)
        started = time.monotonic()
        attempts = 0
        landed = False

        # Re-issue the sentinel rather than writing one and hoping. A write
        # swallowed by the still-reaping delete_all never becomes retrievable
        # no matter how long we poll it, so a single-shot sentinel would abort
        # a run that a second attempt moments later would have saved. Each
        # attempt gets a short window; the loop as a whole is bounded by
        # _CONVERGE_TIMEOUT, so this cannot hang.
        while not landed:
            attempts += 1
            value = f"{_SENTINEL}-{uuid.uuid4().hex[:12]}"
            _call(
                "reset sentinel write",
                self._client.add,
                f"{_SENTINEL}: {value}",
                user_id=uid,
                app_id=app_id,
                metadata={"key": _SENTINEL},
                infer=False,
            )
            remaining = _CONVERGE_TIMEOUT - (time.monotonic() - started)
            if remaining <= 0:
                break
            landed, _ = poll_until(
                lambda v=value: any(
                    v in (m.get("memory") or "")
                    for m in self._scope_memories(sentinel_scope)
                ),
                timeout=min(_SENTINEL_ATTEMPT_TIMEOUT, remaining),
                interval=_CONVERGE_INTERVAL,
            )
            if not landed and time.monotonic() - started >= _CONVERGE_TIMEOUT:
                break

        took = time.monotonic() - started
        if not landed:
            raise AdapterError(
                f"mem0 reset: namespace {app_id!r} read empty after "
                f"{empty_after:.1f}s but was still not accepting writes "
                f"{took:.1f}s later — {attempts} sentinel write(s) were "
                "acknowledged and never became retrievable, so the delete_all "
                "is still reaping. Refusing to start a scenario, because its "
                "first writes would be lost and scored against the provider "
                "as missing facts."
            )
        # Remove the sentinel so it cannot be seen by any scenario query.
        for mem in self._scope_memories(sentinel_scope):
            if (mem.get("metadata") or {}).get("key") == _SENTINEL and mem.get("id"):
                _call("reset sentinel delete", self._client.delete, mem["id"])

        # The series this produces IS the characterisation of the reset race:
        # empty-to-writable, measured on every reset that actually deleted,
        # gathered for free on every run. A single number is an anecdote; this
        # accumulates the distribution.
        self.reset_convergence.append(
            {"app_id": app_id, "empty_after_s": round(empty_after, 2),
             "empty_to_writable_s": round(took, 2), "sentinel_attempts": attempts}
        )
        print(
            f"  [reset] {app_id}: namespace read empty after {empty_after:.1f}s, "
            f"writable {took:.1f}s later ({attempts} sentinel attempt(s))",
            file=sys.stderr,
        )

    def write(
        self, scope: Scope, key: str, value: str, ttl_steps: int | None = None
    ) -> None:
        # ttl_steps is intentionally ignored: supports_ttl is False, so the
        # runner reports expiry NOT_TESTED rather than trusting wall-clock TTL.
        _call(
            "write",
            self._client.add,
            f"{key}: {value}",
            user_id=self._user_id(scope),
            app_id=self._app_id(),
            metadata={"key": key},
            infer=False,
        )
        # Confirm the write landed before the runner queries. Without this the
        # next query could race ingestion and the missing value would be scored
        # against the provider (invariant 10).
        landed, waited = poll_until(
            lambda: any(
                value in (m.get("memory") or "") for m in self._scope_memories(scope)
            ),
            timeout=_CONVERGE_TIMEOUT,
            interval=_CONVERGE_INTERVAL,
        )
        if not landed:
            raise AdapterError(
                f"mem0 write {key!r}={value!r} for scope "
                f"{scope.tenant_id}/{scope.user_id} was not retrievable after "
                f"{waited:.1f}s — the store did not accept the write, so no "
                "result from this run would be meaningful. Evidenced trigger: "
                "a write issued after a delete_all, even when the namespace "
                "was polled until it read empty first — see reset() above, "
                "where 6/14 writes were lost following a delete_all versus "
                "0/10 with no preceding delete. Reading empty is not proof "
                "the delete finished propagating."
            )

    def delete(self, scope: Scope, key: str) -> None:
        # No key concept in Mem0: fetch this scope's memories, keep the ones
        # tagged with our metadata key, and delete them by id.
        doomed = [
            m.get("id")
            for m in self._scope_memories(scope)
            if (m.get("metadata") or {}).get("key") == key and m.get("id")
        ]
        for mem_id in doomed:
            _call("delete", self._client.delete, mem_id)
        if not doomed:
            return
        # Confirm the delete landed. A query issued while the delete is still
        # in flight would see the value and be scored as deletion residue —
        # a P1 finding manufactured by our own impatience (invariant 10).
        gone, waited = poll_until(
            lambda: not [
                m
                for m in self._scope_memories(scope)
                if (m.get("metadata") or {}).get("key") == key
            ],
            timeout=_CONVERGE_TIMEOUT,
            interval=_CONVERGE_INTERVAL,
        )
        if not gone:
            raise AdapterError(
                f"mem0 delete for key {key!r} had not taken effect after "
                f"{waited:.1f}s — reporting this rather than scoring the "
                "surviving value as deletion residue"
            )

    def query(self, scope: Scope, prompt: str, seed: int = 0) -> QueryResult:
        resp = _call(
            "query search",
            self._client.search,
            prompt,
            filters={"user_id": self._user_id(scope)},
            top_k=_SEARCH_TOP_K,
        )
        results = self._results(resp)
        if not results:
            return QueryResult(answer="I don't have anything stored about that.")
        parts: list[str] = []
        retrieved: list[dict] = []
        for mem in results:
            text = mem.get("memory") or ""
            parts.append(text)
            retrieved.append({"memory": text, "metadata": mem.get("metadata")})
        return QueryResult(
            answer="Here's what I remember: " + "; ".join(parts) + ".",
            retrieved=retrieved,
        )

    # ---------------------------------------------------------------- helpers

    def _scope_memories(self, scope: Scope) -> list[dict]:
        resp = _call(
            "scope read",
            self._client.get_all,
            filters={"user_id": self._user_id(scope)},
        )
        return self._results(resp)

    def _namespace_rows(self, app_id: str) -> list[dict]:
        """Everything stored under this run's namespace, across all scopes."""
        return self._results(
            _call("namespace read", self._client.get_all, filters={"app_id": app_id})
        )

    @staticmethod
    def _results(resp) -> list[dict]:
        """Mem0 returns either a bare list or a paginated ``{"results": [...]}``
        depending on endpoint/version — normalise to a list of dicts."""
        if isinstance(resp, dict):
            return list(resp.get("results", []))
        if isinstance(resp, list):
            return resp
        return []
