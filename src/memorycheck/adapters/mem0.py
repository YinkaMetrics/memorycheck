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
  derived from the run namespace, so ``reset()`` can wipe an entire run in one
  ``delete_all``. Ids are prefixed with the namespace so runs never collide.
* ``supports_ttl = False``. Mem0 expiry is wall-clock; ours is logical time,
  so it is genuinely inexpressible here and expiry checks must report
  NOT_TESTED — the honesty model, not a gap to paper over.

Credentials come from ``MEM0_API_KEY``. The SDK is an optional extra
(``pip install -e ".[mem0]"``) and is imported lazily so the core never
depends on it.
"""

from __future__ import annotations

import os
import re

from ..ledger import Scope
from .base import AdapterError, MemoryAdapter, QueryResult

# Cap on how many memories a scoped query pulls back. Scenarios hold a handful
# of facts per scope; this is a safety ceiling, not a relevance filter — we do
# not want a low top_k to silently drop the current fact and fake a pass.
_SEARCH_TOP_K = 100


def _slug(text: str) -> str:
    """Collapse arbitrary text to a Mem0-safe identifier fragment."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


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

    # --------------------------------------------------------------- scoping

    def _app_id(self) -> str:
        """One id per run namespace — the handle reset() wipes."""
        return f"mc_{_slug(self._namespace)}"

    def _user_id(self, scope: Scope) -> str:
        """Fold namespace + tenant + user into a single Mem0 identifier, so a
        different run, tenant, or user can never see this scope's memories."""
        return f"{self._app_id()}__{_slug(scope.tenant_id)}__{_slug(scope.user_id)}"

    # ------------------------------------------------------------- lifecycle

    def reset(self, namespace: str) -> None:
        # Adopt the namespace first, then delete everything tagged with its
        # app_id — clears any residue from a previous run of this scenario/seed.
        self._namespace = namespace or "default"
        self._client.delete_all(app_id=self._app_id())

    def write(
        self, scope: Scope, key: str, value: str, ttl_steps: int | None = None
    ) -> None:
        # ttl_steps is intentionally ignored: supports_ttl is False, so the
        # runner reports expiry NOT_TESTED rather than trusting wall-clock TTL.
        self._client.add(
            f"{key}: {value}",
            user_id=self._user_id(scope),
            app_id=self._app_id(),
            metadata={"key": key},
            infer=False,
        )

    def delete(self, scope: Scope, key: str) -> None:
        # No key concept in Mem0: fetch this scope's memories, keep the ones
        # tagged with our metadata key, and delete them by id.
        for mem in self._scope_memories(scope):
            if (mem.get("metadata") or {}).get("key") == key:
                mem_id = mem.get("id")
                if mem_id:
                    self._client.delete(mem_id)

    def query(self, scope: Scope, prompt: str, seed: int = 0) -> QueryResult:
        resp = self._client.search(
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
        resp = self._client.get_all(filters={"user_id": self._user_id(scope)})
        return self._results(resp)

    @staticmethod
    def _results(resp) -> list[dict]:
        """Mem0 returns either a bare list or a paginated ``{"results": [...]}``
        depending on endpoint/version — normalise to a list of dicts."""
        if isinstance(resp, dict):
            return list(resp.get("results", []))
        if isinstance(resp, list):
            return resp
        return []
