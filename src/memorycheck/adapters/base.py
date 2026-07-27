"""Provider-neutral adapter contract.

An adapter binds memorycheck to a system under test: the memory store plus
whatever answers questions on top of it (usually the customer's agent, or a
thin shim in front of it). Adapters need no read/list API — the runner
replays rescopes from the ledger's ground truth — and they are never asked
to self-report correctness.

Capability flags keep the report honest: if an adapter cannot express TTL,
expiry checks are reported NOT_TESTED for that run, never silently passed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..ledger import Scope


class AdapterError(Exception):
    """Adapter configuration or transport failure (missing credentials,
    unreachable endpoint, malformed config). Reported to the user as a clean
    CLI error rather than a traceback — it is a setup problem, not a crash."""


@dataclass
class QueryResult:
    answer: str
    retrieved: list[dict] = field(default_factory=list)


class MemoryAdapter(ABC):
    name: str = "base"
    supports_ttl: bool = False

    #: Set True while an adapter has never been executed against its real
    #: provider. Code written against an SDK's surface is a hypothesis until it
    #: touches the service — the Mem0 adapter passed every offline test while
    #: carrying a delete/write race that only appeared live. The runner warns on
    #: stderr and stamps the report so a number from an unverified adapter can
    #: never be quoted as a measurement by accident. Clear it only after a live
    #: run, in the same change that records the evidence.
    unverified: bool = False

    #: Shown alongside the warning when `unverified` is set.
    unverified_note: str = ""

    def reset(self, namespace: str) -> None:
        """Isolate state for a scenario run. Called once per scenario."""

    @abstractmethod
    def write(
        self, scope: Scope, key: str, value: str, ttl_steps: int | None = None
    ) -> None: ...

    @abstractmethod
    def delete(self, scope: Scope, key: str) -> None: ...

    @abstractmethod
    def query(self, scope: Scope, prompt: str, seed: int = 0) -> QueryResult: ...

    def advance_time(self, steps: int) -> None:
        """Only meaningful when supports_ttl is True."""


class NullAdapter(MemoryAdapter):
    """No-memory baseline: writes are discarded. Used to compute the Memory
    Utility Delta — a system must not 'pass' the lifecycle by simply never
    remembering anything."""

    name = "null"
    supports_ttl = True

    def write(self, scope: Scope, key: str, value: str, ttl_steps: int | None = None) -> None:
        pass

    def delete(self, scope: Scope, key: str) -> None:
        pass

    def query(self, scope: Scope, prompt: str, seed: int = 0) -> QueryResult:
        return QueryResult(answer="I don't have any stored memory about this.")
