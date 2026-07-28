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
from .base import AdapterError, MemoryAdapter, QueryResult, poll_until

# Ceiling on results pulled back per query — a safety bound, not a relevance
# filter. Too low would silently drop the current fact and fake a pass.
_SEARCH_LIMIT = 50

# Episodes are the raw ingest log; a scenario writes a handful per scope.
_EPISODE_FETCH = 100

# Zep ingests asynchronously: an episode is queued, then an LLM extracts edges
# from it. Queries read *edges*, so a write is only confirmed once its edge
# exists — confirming the episode alone would still leave the query racing
# extraction. Mem0's comparable pipeline measured ~15s, and a graph build is
# unlikely to be faster, so the ceiling is generous (invariant 10).
# Sized from measurement, not analogy. A single 25-byte episode took **329s**
# to reach processed=True and produce its edge (measured 2026-07-27, free tier,
# new project). The previous 120s was guessed from Mem0's ~15s pipeline and was
# wrong by roughly 3x — every write would have aborted. 900s gives ~2.7x
# headroom over the one measurement we have; refine as more accumulate.
_EXTRACTION_TIMEOUT = 900.0
_DELETE_TIMEOUT = 120.0

# Extraction is minutes-scale, so second-by-second polling buys nothing and
# spends the ~300 req/min rate limit. 5s keeps a write's poll count near 180
# in the worst case.
_CONVERGE_INTERVAL = 5.0

# Per-request ceiling. Zep's rate limit measured at ~300 requests/minute, so
# polling is cheap here in credits — but a request that hangs must not stall
# the run forever.
_HTTP_TIMEOUT = 60.0

# reset() must see every graph in the project, so graph listing pages until
# total_count is reached. The page cap is a runaway guard, not a limit.
_PAGE_SIZE = 100
_MAX_GRAPH_PAGES = 50


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def _why(exc: Exception) -> str:
    """Readable one-liner from a Zep SDK error.

    The SDK's own str() leads with a full header dump, which buries the part
    that tells you what to fix (`401 unauthorized`) at the end of a long line.
    """
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    if status is not None:
        detail = str(body).strip() if body else exc.__class__.__name__
        return f"HTTP {status}: {detail[:200]}"
    return f"{exc.__class__.__name__}: {str(exc)[:200]}"


def _absent(exc: Exception) -> bool:
    """True when the error means 'this graph does not exist yet'.

    Everything else — auth, transport, server faults — must NOT be read as an
    empty memory. Swallowing those would let a broken credential masquerade as
    a provider that forgot every fact, manufacturing false failures against a
    real vendor (invariant 9). Absence is legitimately empty; failure is not.
    """
    # Status code first: it is SDK-independent, so this stays correct where the
    # zep extra is not installed. Checking the exception type first and bailing
    # out on ImportError would skip this and treat every 404 as a hard failure.
    if getattr(exc, "status_code", None) == 404:
        return True
    try:
        from zep_cloud.errors import NotFoundError
    except ImportError:
        return False
    return isinstance(exc, NotFoundError)


class ZepAdapter(MemoryAdapter):
    name = "zep"
    supports_ttl = False  # Zep validity is wall-clock; ours is logical
    unverified = True  # staged verification incomplete — see HANDOFF.md
    unverified_note = (
        "not measurable by the current instrument — staged verification "
        "stopped at stage 3. Some scenario values never materialise as graph "
        "edges (selective silent extraction), so those writes cannot be "
        "confirmed and any aggregate figure would describe our own value "
        "choices rather than Zep. No unblock date."
    )

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
        # The SDK defaults to timeout=None, so a hung request would block a
        # run indefinitely rather than failing where it can be reported.
        self._client = Zep(api_key=os.environ["ZEP_API_KEY"], timeout=_HTTP_TIMEOUT)
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
        doomed = [g for g in self._list_graph_ids() if g.startswith(prefix)]
        for graph_id in doomed:
            try:
                self._client.graph.delete(graph_id)
            except Exception:  # noqa: BLE001 - already gone is fine
                pass
        if not doomed:
            return
        cleared, waited = poll_until(
            lambda: not [
                g for g in self._list_graph_ids() if g.startswith(prefix)
            ],
            timeout=_DELETE_TIMEOUT,
            interval=_CONVERGE_INTERVAL,
        )
        if not cleared:
            raise AdapterError(
                f"zep reset: graphs under {prefix!r} survived {waited:.1f}s "
                "— refusing to run, since leftovers would be scored against "
                "the provider"
            )

    def write(
        self, scope: Scope, key: str, value: str, ttl_steps: int | None = None
    ) -> None:
        # ttl_steps ignored: supports_ttl is False, so expiry reports
        # NOT_TESTED rather than leaning on wall-clock validity.
        graph_id = self._graph_id(scope)
        self._ensure_graph(graph_id)
        try:
            self._client.graph.add(
                graph_id=graph_id,
                type="text",
                data=f"{key}: {value}",
            )
        except Exception as e:  # noqa: BLE001
            # A dropped write must abort the run, never pass silently — the
            # missing value would otherwise be scored against the provider.
            raise AdapterError(f"zep write failed for key {key!r}: {_why(e)}") from e

        # Wait for extraction to produce a *live* edge carrying the value —
        # the layer query() reads. Confirming the episode instead would leave
        # the next query racing the extractor and score the gap as a missing
        # current fact (invariant 10).
        landed, waited = poll_until(
            lambda: any(
                value in (getattr(e_, "fact", "") or "")
                for e_ in self._edges(graph_id)
                if self._live(e_)
            ),
            timeout=_EXTRACTION_TIMEOUT,
            interval=_CONVERGE_INTERVAL,
        )
        if not landed:
            raise AdapterError(
                f"zep write for key {key!r} produced no live edge carrying the "
                f"value after {waited:.1f}s. Either extraction is slower than "
                "the ceiling or it does not preserve the literal value — both "
                "are findings to report with this latency, not to score as a "
                "missing fact"
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

        # Confirm the removal before the runner queries, or a still-in-flight
        # delete surfaces as P1 deletion residue that we manufactured.
        gone, waited = poll_until(
            lambda: not any(
                self._is_key(content, key) for _, content in self._episodes(graph_id)
            )
            and not (
                doomed
                & {
                    ep
                    for e_ in self._edges(graph_id)
                    for ep in (getattr(e_, "episodes", None) or [])
                }
            ),
            timeout=_DELETE_TIMEOUT,
            interval=_CONVERGE_INTERVAL,
        )
        if not gone:
            raise AdapterError(
                f"zep delete for key {key!r} had not taken effect after "
                f"{waited:.1f}s — reporting this rather than scoring the "
                "surviving value as deletion residue"
            )

    def query(self, scope: Scope, prompt: str, seed: int = 0) -> QueryResult:
        graph_id = self._graph_id(scope)
        try:
            results = self._client.graph.search(
                graph_id=graph_id,
                query=prompt,
                scope="edges",
                limit=_SEARCH_LIMIT,
            )
        except Exception as e:  # noqa: BLE001
            if not _absent(e):
                raise AdapterError(f"zep query failed: {_why(e)}") from e
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
        except Exception as e:  # noqa: BLE001
            if not _absent(e):
                raise AdapterError(f"zep episode fetch failed: {_why(e)}") from e
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
        except Exception as e:  # noqa: BLE001
            if not _absent(e):
                raise AdapterError(f"zep edge fetch failed: {_why(e)}") from e
            return []

    def _list_graph_ids(self) -> list[str]:
        """Every graph id in the project, following pagination.

        A single page is not enough. One full run creates roughly 45 graphs
        (one per namespace/tenant/user), so an account crosses a 100-row page
        within a few runs — and a `reset()` that cannot see a graph cannot
        clear it, leaving residue that would later be scored against the
        provider. `total_count` is authoritative; keep paging until we have it.
        """
        ids: list[str] = []
        page = 1
        while page <= _MAX_GRAPH_PAGES:
            try:
                resp = self._client.graph.list_all(page_number=page, page_size=_PAGE_SIZE)
            except Exception as e:  # noqa: BLE001
                if page == 1:
                    return []  # listing unavailable at all
                raise AdapterError(f"zep graph listing failed on page {page}: {_why(e)}") from e
            rows = list(getattr(resp, "graphs", None) or [])
            ids.extend(g.graph_id for g in rows if getattr(g, "graph_id", None))
            total = getattr(resp, "total_count", None)
            if not rows or (total is not None and len(ids) >= total):
                break
            page += 1
        return ids

    @staticmethod
    def _safe_delete(fn, uuid_: str) -> None:
        try:
            fn(uuid_)
        except Exception:  # noqa: BLE001 - already gone is success for us
            pass
