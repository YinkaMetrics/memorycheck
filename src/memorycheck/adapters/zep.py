"""Zep adapter: run the lifecycle suite against the hosted Zep platform.

Like Mem0, Zep is a store rather than an agent, so this adapter supplies the
deterministic answering layer: a query searches the subject's graph and
templates the results into an answer the same way `ReferenceAdapter` does.

Zep's model is a temporal knowledge graph, not a flat memory list, which
changes the mapping in two ways that matter:

* **Scope is a graph.** Each (namespace, tenant, user) gets its own Zep
  `graph_id`, so isolation is structural rather than a filter we remember to
  pass. `reset()` deletes every graph under the run's namespace prefix.
* **Zep publishes fact invalidation.** `EntityEdge` carries `valid_at`,
  `invalid_at` and `expired_at` — the platform's own claim about which facts
  are still live. This adapter *honours* that signal: an edge Zep marks
  invalid or expired is not templated into the answer.

That second point is a deliberate, contestable choice, so state it plainly:
we are testing whether Zep's invalidation is *correct*, not whether a naive
caller who ignores it gets burned. Mem0 exposes no equivalent signal, so its
adapter cannot filter this way. A cross-provider comparison must say so —
"Zep passed" would mean "Zep's own liveness metadata was accurate", which is
a different claim from Mem0's, not a better score on an identical test.

Reads use `scope="edges"`: the knowledge layer an agent would actually
consume. Episodes (the raw ingest log) are used only to resolve deletes.

`supports_ttl = False` — Zep's temporal validity is wall-clock, memorycheck's
time is logical, so expiry checks report NOT_TESTED.

Credentials come from `ZEP_API_KEY`. The SDK is an optional extra
(``pip install -e ".[zep]"``) imported lazily, so the core never needs it.
"""

from __future__ import annotations

import os
import re

from ..ledger import Scope
from .base import AdapterError, MemoryAdapter, QueryResult

# Ceiling on results pulled back per query — a safety bound, not a relevance
# filter. Too low would silently drop the current fact and fake a pass.
_SEARCH_LIMIT = 50

# Episodes are the raw ingest log; a scenario writes a handful per scope.
_EPISODE_FETCH = 100


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


class ZepAdapter(MemoryAdapter):
    name = "zep"
    supports_ttl = False  # Zep validity is wall-clock; ours is logical

    def __init__(self) -> None:
        if not os.environ.get("ZEP_API_KEY"):
            raise AdapterError(
                "Zep adapter needs ZEP_API_KEY in the environment "
                "(get a key at https://app.getzep.com)."
            )
        try:
            from zep_cloud.client import Zep
        except ImportError as e:  # pragma: no cover - only without the extra
            raise AdapterError(
                "Zep SDK not installed. Install the optional extra: "
                'pip install -e ".[zep]"'
            ) from e
        self._client = Zep(api_key=os.environ["ZEP_API_KEY"])
        self._namespace = "default"
        self._known_graphs: set[str] = set()

    # --------------------------------------------------------------- scoping

    def _prefix(self) -> str:
        return f"mc-{_slug(self._namespace)}"

    def _graph_id(self, scope: Scope) -> str:
        return f"{self._prefix()}--{_slug(scope.tenant_id)}--{_slug(scope.user_id)}"

    def _ensure_graph(self, graph_id: str) -> None:
        """Create the graph once per process. Zep rejects duplicate ids, which
        is benign — another run already created it."""
        if graph_id in self._known_graphs:
            return
        try:
            self._client.graph.create(graph_id=graph_id)
        except Exception:  # noqa: BLE001 - already-exists is the expected case
            pass
        self._known_graphs.add(graph_id)

    # ------------------------------------------------------------- lifecycle

    def reset(self, namespace: str) -> None:
        """Drop every graph belonging to this run's namespace.

        Deleting the graph removes its episodes and derived edges together, so
        unlike the Mem0 adapter there is no delete/write race to sidestep: the
        graphs are recreated on the next write under a fresh id.
        """
        self._namespace = namespace or "default"
        self._known_graphs.clear()
        prefix = self._prefix()
        for graph_id in self._list_graph_ids():
            if graph_id.startswith(prefix):
                try:
                    self._client.graph.delete(graph_id)
                except Exception:  # noqa: BLE001 - already gone is fine
                    pass

    def write(
        self, scope: Scope, key: str, value: str, ttl_steps: int | None = None
    ) -> None:
        # ttl_steps ignored: supports_ttl is False, so expiry reports
        # NOT_TESTED rather than leaning on wall-clock validity.
        graph_id = self._graph_id(scope)
        self._ensure_graph(graph_id)
        self._client.graph.add(
            graph_id=graph_id,
            type="text",
            data=f"{key}: {value}",
        )

    def delete(self, scope: Scope, key: str) -> None:
        """Remove a key's source episodes and every edge derived from them.

        Zep has no key concept, so the episode text is the handle. Deleting
        only the episode would leave the extracted edges standing and the fact
        would keep driving answers — so the derived edges go too. This is the
        thorough delete a competent integrator would perform; anything less
        would be measuring our own laziness rather than Zep's behaviour.
        """
        graph_id = self._graph_id(scope)
        doomed = {
            ep_uuid
            for ep_uuid, content in self._episodes(graph_id)
            if self._is_key(content, key)
        }
        if not doomed:
            return
        for edge in self._edges(graph_id):
            if doomed & set(getattr(edge, "episodes", None) or []):
                self._safe_delete(self._client.graph.edge.delete, edge.uuid_)
        for ep_uuid in doomed:
            self._safe_delete(self._client.graph.episode.delete, ep_uuid)

    def query(self, scope: Scope, prompt: str, seed: int = 0) -> QueryResult:
        graph_id = self._graph_id(scope)
        try:
            results = self._client.graph.search(
                graph_id=graph_id,
                query=prompt,
                scope="edges",
                limit=_SEARCH_LIMIT,
            )
        except Exception:  # noqa: BLE001 - absent graph reads as empty memory
            return QueryResult(answer="I don't have anything stored about that.")

        live = [e for e in (getattr(results, "edges", None) or []) if self._live(e)]
        if not live:
            return QueryResult(answer="I don't have anything stored about that.")
        parts, retrieved = [], []
        for edge in live:
            fact = getattr(edge, "fact", "") or ""
            parts.append(fact)
            retrieved.append({"fact": fact, "name": getattr(edge, "name", None)})
        return QueryResult(
            answer="Here's what I remember: " + "; ".join(parts) + ".",
            retrieved=retrieved,
        )

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _live(edge) -> bool:
        """Honour Zep's own liveness metadata — see the module docstring."""
        return not getattr(edge, "invalid_at", None) and not getattr(
            edge, "expired_at", None
        )

    @staticmethod
    def _is_key(content: str, key: str) -> bool:
        """Episodes are written as ``"<key>: <value>"``, so the prefix
        identifies them without matching a key name that merely appears in
        some other fact's value."""
        return (content or "").strip().lower().startswith(f"{key.lower()}:")

    def _episodes(self, graph_id: str) -> list[tuple[str, str]]:
        try:
            resp = self._client.graph.episode.get_by_graph_id(
                graph_id, lastn=_EPISODE_FETCH
            )
        except Exception:  # noqa: BLE001 - no graph yet
            return []
        return [
            (ep.uuid_, getattr(ep, "content", "") or "")
            for ep in (getattr(resp, "episodes", None) or [])
            if getattr(ep, "uuid_", None)
        ]

    def _edges(self, graph_id: str) -> list:
        try:
            return list(
                self._client.graph.edge.get_by_graph_id(graph_id, limit=_SEARCH_LIMIT)
                or []
            )
        except Exception:  # noqa: BLE001 - no graph yet
            return []

    def _list_graph_ids(self) -> list[str]:
        try:
            resp = self._client.graph.list_all(page_size=100)
        except Exception:  # noqa: BLE001 - listing unavailable
            return []
        return [
            g.graph_id
            for g in (getattr(resp, "graphs", None) or [])
            if getattr(g, "graph_id", None)
        ]

    @staticmethod
    def _safe_delete(fn, uuid_: str) -> None:
        try:
            fn(uuid_)
        except Exception:  # noqa: BLE001 - already gone is success for us
            pass
