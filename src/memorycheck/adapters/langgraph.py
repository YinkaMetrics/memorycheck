"""LangGraph store adapter: run the lifecycle suite against a BaseStore.

Unlike Mem0 and Zep this is a local, synchronous, keyed store — no service, no
credentials, no quota. It is also the closest thing in the roadmap to a plain
key-value memory, which makes it a useful control: whatever the lifecycle
scenarios report here is close to the floor of what a correct store does.

Specs:

    langgraph                     InMemoryStore (default)
    langgraph:memory              InMemoryStore, explicitly
    langgraph:sqlite              SqliteStore in a temp file
    langgraph:sqlite:/path/db     SqliteStore at a path you choose

Mapping, kept deliberately parallel to the Mem0 adapter so the scenario pack
is unchanged:

* scope becomes a namespace tuple — `(run_namespace, tenant_id, user_id)`.
  The run namespace is a prefix, not decoration: `SqliteStore` persists, so a
  `reset()` that cleared the store globally would delete data belonging to
  whoever owns the file. Scoping it means we only ever remove our own rows.
* a fact is one store key holding `{"key": ..., "value": ...}`, the same
  metadata shape the Mem0 adapter uses.
* the answering layer templates results exactly as `ReferenceAdapter` does,
  including its key-token relevance filter, because we are measuring the
  store's retrieval rather than an LLM's phrasing.

**Supersession is structural here.** A keyed store overwrites, so a correction
replaces the previous value rather than accumulating beside it. That is a
property of the store, not something this adapter arranges.

`supports_ttl = False`, from observation rather than documentation:
`InMemoryStore.supports_ttl` is False and `put(ttl=...)` raises
`NotImplementedError`; `SqliteStore.supports_ttl` is True but a `ttl` passed
without a `ttl_config` was not enforced, and it is wall-clock in any case,
while memorycheck time is logical. Expiry therefore reports NOT_TESTED.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ..ledger import Scope
from .base import AdapterError, MemoryAdapter, QueryResult, encode_identifier

# `search` defaults to limit=10 and `list_namespaces` to limit=100 — both
# silently truncate. Ceilings here are safety bounds, not relevance filters:
# a dropped row would read as the store forgetting a fact.
_SEARCH_LIMIT = 1000
_NS_PAGE = 500
_MAX_NS_PAGES = 100


def _tokens(text: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


class LangGraphAdapter(MemoryAdapter):
    supports_ttl = False  # see module docstring — observed, not assumed

    def __init__(self, spec: str = "") -> None:
        kind, _, arg = (spec or "memory").partition(":")
        kind = kind or "memory"
        try:
            if kind == "memory":
                from langgraph.store.memory import InMemoryStore

                self._store = InMemoryStore()
                self._closer = None
                self.name = "langgraph:memory"
            elif kind == "sqlite":
                from langgraph.store.sqlite import SqliteStore

                path = arg or str(Path(tempfile.mkdtemp(prefix="memorycheck-lg-")) / "store.db")
                self._closer = SqliteStore.from_conn_string(path)
                self._store = self._closer.__enter__()
                if hasattr(self._store, "setup"):
                    self._store.setup()
                self.name = f"langgraph:sqlite:{path}"
            else:
                raise AdapterError(
                    f"unknown langgraph store {kind!r} "
                    "(use langgraph:memory or langgraph:sqlite[:path])"
                )
        except ImportError as e:  # pragma: no cover - only without the extra
            raise AdapterError(
                "LangGraph not installed. Install the optional extra: "
                'pip install -e ".[langgraph]"'
            ) from e
        self._root = "memorycheck"

    # --------------------------------------------------------------- scoping

    def _ns(self, scope: Scope) -> tuple[str, ...]:
        return (
            self._root,
            encode_identifier(scope.tenant_id),
            encode_identifier(scope.user_id),
        )

    def _namespaces(self) -> list[tuple[str, ...]]:
        """Every namespace under this run's root, following pagination.

        `list_namespaces` defaults to limit=100; measured, that hid 21 of 121
        namespaces. A reset() that cannot see a namespace cannot clear it, and
        the residue would later be scored against the store.
        """
        out: list[tuple[str, ...]] = []
        offset = 0
        for _ in range(_MAX_NS_PAGES):
            page = self._store.list_namespaces(
                prefix=(self._root,), limit=_NS_PAGE, offset=offset
            )
            if not page:
                break
            out.extend(page)
            if len(page) < _NS_PAGE:
                break
            offset += _NS_PAGE
        return out

    # ------------------------------------------------------------- lifecycle

    def reset(self, namespace: str) -> None:
        """Clear only this run's rows.

        Scoped by construction: SqliteStore persists, so clearing the store
        globally would delete rows belonging to whoever owns the file.
        """
        self._root = f"memorycheck-{encode_identifier(namespace or 'default')}"
        for ns in self._namespaces():
            for item in self._store.search(ns, limit=_SEARCH_LIMIT):
                self._store.delete(ns, item.key)

    def write(
        self, scope: Scope, key: str, value: str, ttl_steps: int | None = None
    ) -> None:
        # ttl_steps ignored: supports_ttl is False, so expiry reports
        # NOT_TESTED rather than leaning on wall-clock TTL.
        self._store.put(self._ns(scope), key, {"key": key, "value": value})

    def delete(self, scope: Scope, key: str) -> None:
        self._store.delete(self._ns(scope), key)

    def query(self, scope: Scope, prompt: str, seed: int = 0) -> QueryResult:
        items = self._store.search(self._ns(scope), limit=_SEARCH_LIMIT)
        pool = [
            (it.value.get("key", it.key), it.value.get("value", ""))
            for it in items
            if isinstance(it.value, dict)
        ]
        prompt_tokens = _tokens(prompt)
        relevant = [kv for kv in pool if _tokens(kv[0]) & prompt_tokens] or pool
        if not relevant:
            return QueryResult(answer="I don't have anything stored about that.")
        parts = [f"{k} is {v}" for k, v in relevant]
        return QueryResult(
            answer="Here's what I remember: " + "; ".join(parts) + ".",
            retrieved=[{"key": k, "value": v} for k, v in relevant],
        )

    # ---------------------------------------------------------------- teardown

    def close(self) -> None:
        if self._closer is not None:
            self._closer.__exit__(None, None, None)
            self._closer = None
