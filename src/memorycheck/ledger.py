"""Ground-truth ledger: a deterministic state machine over fact lifecycles.

This module is the source of truth for every scenario. The system under test
(the memory provider + agent) is never asked to grade itself; the ledger knows,
at every step, which values are CURRENT, SUPERSEDED (stale), EXPIRED, DELETED,
or out of scope for the querying subject. The oracle compares observed agent
behaviour against this ledger — never against another model's opinion.

Time is logical: only an explicit `advance_time` step moves the clock. This
keeps expiry semantics predictable and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FactStatus(str, Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    DELETED = "deleted"


@dataclass(frozen=True)
class Scope:
    """The subject boundary a fact belongs to."""

    tenant_id: str
    user_id: str

    def merged(self, override: dict | None) -> "Scope":
        if not override:
            return self
        return Scope(
            tenant_id=override.get("tenant_id", self.tenant_id),
            user_id=override.get("user_id", self.user_id),
        )


@dataclass
class FactRecord:
    scope: Scope
    key: str
    value: str
    written_at: int
    ttl_steps: int | None = None
    status: FactStatus = FactStatus.CURRENT
    superseded_at: int | None = None
    deleted_at: int | None = None


# Classification precedence when one value string matches multiple records
# (after "current", which always wins). In-scope interpretations come before
# foreign ones deliberately: the parsimonious explanation for a surfaced
# value is the subject's own (dead) record, not a boundary crossing — e.g. a
# rescoped fact reappearing at its old scope is deletion residue, not
# leakage. Overclaiming P1 scope alarms is how a gate loses trust. Scenario
# authors should still keep values distinctive; the validator warns if not.
LABEL_PRECEDENCE = [
    "deleted",
    "expired",
    "stale",
    "foreign_tenant",
    "foreign_user",
]


@dataclass
class Candidate:
    value: str
    key: str
    label: str  # current | stale | expired | deleted | foreign_user | foreign_tenant
    scope: Scope


class ScenarioError(Exception):
    pass


class GroundTruthLedger:
    def __init__(self) -> None:
        self.records: list[FactRecord] = []
        self.clock: int = 0

    # ------------------------------------------------------------------ ops

    def write(
        self,
        scope: Scope,
        key: str,
        value: str,
        ttl_steps: int | None = None,
    ) -> FactRecord:
        """Write a fact. An existing CURRENT fact for the same (scope, key)
        is superseded — a `correct` is a write."""
        for r in self.records:
            if (
                r.scope == scope
                and r.key == key
                and r.status == FactStatus.CURRENT
            ):
                r.status = FactStatus.SUPERSEDED
                r.superseded_at = self.clock
        rec = FactRecord(
            scope=scope,
            key=key,
            value=value,
            written_at=self.clock,
            ttl_steps=ttl_steps,
        )
        self.records.append(rec)
        return rec

    def delete(self, scope: Scope, key: str) -> int:
        """Delete a key for a scope. Deletion covers the full history of the
        key — superseded and expired values too. After deletion, *no* value
        ever held under this key may influence behaviour (deletion residue)."""
        n = 0
        for r in self.records:
            if r.scope == scope and r.key == key and r.status != FactStatus.DELETED:
                r.status = FactStatus.DELETED
                r.deleted_at = self.clock
                n += 1
        if n == 0:
            raise ScenarioError(
                f"delete: no records for key={key!r} in scope {scope} — "
                "scenario is malformed"
            )
        return n

    def rescope(self, old_scope: Scope, new_scope: Scope, key: str) -> str:
        """Move a fact between scopes. The old scope must no longer surface it
        (modelled as deletion at the old scope); the new scope owns it as
        CURRENT. Returns the value moved, so the runner can replay the move
        against adapters that expose no read API."""
        current = [
            r
            for r in self.records
            if r.scope == old_scope
            and r.key == key
            and r.status == FactStatus.CURRENT
        ]
        if not current:
            raise ScenarioError(
                f"rescope: no CURRENT record for key={key!r} in scope {old_scope}"
            )
        value = current[-1].value
        self.delete(old_scope, key)
        self.write(new_scope, key, value)
        return value

    def advance_time(self, steps: int) -> None:
        if steps < 1:
            raise ScenarioError("advance_time: steps must be >= 1")
        self.clock += steps
        for r in self.records:
            if (
                r.status == FactStatus.CURRENT
                and r.ttl_steps is not None
                and self.clock >= r.written_at + r.ttl_steps
            ):
                r.status = FactStatus.EXPIRED

    # ------------------------------------------------------------ inspection

    def snapshot(self, query_scope: Scope) -> list[Candidate]:
        """Every value in the ledger, labelled relative to the querying scope.
        Foreign records are labelled foreign regardless of their own status:
        a deleted fact from another tenant surfacing here is still leakage."""
        out: list[Candidate] = []
        status_label = {
            FactStatus.CURRENT: "current",
            FactStatus.SUPERSEDED: "stale",
            FactStatus.EXPIRED: "expired",
            FactStatus.DELETED: "deleted",
        }
        for r in self.records:
            if r.scope == query_scope:
                label = status_label[r.status]
            elif r.scope.tenant_id == query_scope.tenant_id:
                label = "foreign_user"
            else:
                label = "foreign_tenant"
            out.append(Candidate(value=r.value, key=r.key, label=label, scope=r.scope))
        return out

    def worst_label_for_value(self, value: str, query_scope: Scope) -> str | None:
        """Classify a used value. If it is legitimately CURRENT in-scope, that
        wins; otherwise the worst matching label wins."""
        labels = {
            c.label for c in self.snapshot(query_scope) if c.value == value
        }
        if not labels:
            return None
        if "current" in labels:
            return "current"
        for label in LABEL_PRECEDENCE:
            if label in labels:
                return label
        return None
