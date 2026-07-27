"""Reference adapter: a tiny in-process memory store + answering layer.

Three modes, so the harness demonstrates its own value with zero setup:

  strict  — honours supersession, soft-deletes, TTL and user/tenant scope.
            The lifecycle gate passes.
  naive   — the classic broken implementation: retrieval ignores the
            superseded flag, ignores soft-deletes, ignores TTL. The store's
            write/delete calls all returned success — and the agent still
            answers from stale and deleted facts. This is exactly the failure
            class memorycheck exists to catch.
  leaky   — naive, plus retrieval filters by tenant only, so one user's
            facts bleed into another user's answers (scope leakage).

The point of the demo: every mode returns HTTP-200-equivalent success on
every write and delete. Storage status codes cannot tell you which mode
you are running in production. Behaviour can.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..ledger import Scope
from .base import MemoryAdapter, QueryResult

MODES = ("strict", "naive", "leaky")


@dataclass
class _Entry:
    scope: Scope
    key: str
    value: str
    written_at: int
    ttl_steps: int | None = None
    superseded: bool = False
    deleted: bool = False


def _tokens(text: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


class ReferenceAdapter(MemoryAdapter):
    supports_ttl = True

    def __init__(self, mode: str = "strict") -> None:
        if mode not in MODES:
            raise ValueError(f"reference adapter mode must be one of {MODES}, got {mode!r}")
        self.mode = mode
        self.name = f"reference:{mode}"
        self._entries: list[_Entry] = []
        self._clock = 0

    # ------------------------------------------------------------- lifecycle

    def reset(self, namespace: str) -> None:
        self._entries = []
        self._clock = 0

    def write(self, scope: Scope, key: str, value: str, ttl_steps: int | None = None) -> None:
        for e in self._entries:
            if e.scope == scope and e.key == key and not e.superseded:
                e.superseded = True
        self._entries.append(
            _Entry(scope=scope, key=key, value=value, written_at=self._clock, ttl_steps=ttl_steps)
        )

    def delete(self, scope: Scope, key: str) -> None:
        # Soft delete — the realistic pattern, and the one that goes wrong.
        for e in self._entries:
            if e.scope == scope and e.key == key:
                e.deleted = True

    def advance_time(self, steps: int) -> None:
        self._clock += steps

    # ----------------------------------------------------------------- query

    def _expired(self, e: _Entry) -> bool:
        return e.ttl_steps is not None and self._clock >= e.written_at + e.ttl_steps

    def _visible(self, e: _Entry, scope: Scope) -> bool:
        if self.mode == "strict":
            return (
                e.scope == scope
                and not e.deleted
                and not e.superseded
                and not self._expired(e)
            )
        if self.mode == "naive":
            # Right boundary, broken lifecycle: deleted/superseded/expired
            # entries still retrievable.
            return e.scope == scope
        # leaky: broken lifecycle AND tenant-only boundary.
        return e.scope.tenant_id == scope.tenant_id

    def query(self, scope: Scope, prompt: str, seed: int = 0) -> QueryResult:
        pool = [e for e in self._entries if self._visible(e, scope)]
        prompt_tokens = _tokens(prompt)
        relevant = [e for e in pool if _tokens(e.key) & prompt_tokens] or pool
        if not relevant:
            return QueryResult(answer="I don't have anything stored about that.")
        parts = [f"{e.key} is {e.value}" for e in relevant]
        return QueryResult(
            answer="Here's what I remember: " + "; ".join(parts) + ".",
            retrieved=[
                {"key": e.key, "value": e.value, "scope": f"{e.scope.tenant_id}/{e.scope.user_id}"}
                for e in relevant
            ],
        )
