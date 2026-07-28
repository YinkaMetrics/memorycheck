"""Conformance check for a memory shim, run before any scenario.

`memorycheck doctor --adapter http:config.yaml` exercises each endpoint,
checks the response contract, round-trips scope isolation, confirms deletion
actually removes, and measures write→read convergence so invariant 10 timeouts
are sized from the customer's stack rather than guessed.

The point is to fail early and specifically. A scenario run against a
misconfigured shim produces findings that look like memory bugs and are
actually integration bugs — the most expensive kind of false result, because
it sends someone hunting through their retrieval layer for a defect we caused.
Every failure below therefore carries the exact fix.

Runs against any adapter, not only the HTTP one; the remedies are written for
the shim because that is the supported pilot path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .adapters.base import MemoryAdapter
from .ledger import Scope

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# Probe values are distinctive so a match cannot be coincidence, and carry the
# marker below so a customer can find and purge anything left behind.
_MARK = "memorycheck-doctor"
_A = Scope(tenant_id=f"{_MARK}-tenant-a", user_id=f"{_MARK}-user-a")
_B = Scope(tenant_id=f"{_MARK}-tenant-a", user_id=f"{_MARK}-user-b")
_C = Scope(tenant_id=f"{_MARK}-tenant-b", user_id=f"{_MARK}-user-a")

_KEY = "doctor-probe"
_V1 = "quartzline-40817"
_V2 = "sablefish-92336"


@dataclass
class Check:
    id: str
    title: str
    status: str
    detail: str
    fix: str = ""


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)
    convergence_seconds: float | None = None
    suggested_timeout: float | None = None

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def ok(self) -> bool:
        return not self.failed


def _visible(adapter: MemoryAdapter, scope: Scope, value: str, prompt: str) -> bool:
    try:
        return value in (adapter.query(scope, prompt).answer or "")
    except Exception:  # noqa: BLE001 - transport errors are the caller's problem
        return False


def _wait_visible(
    adapter: MemoryAdapter, scope: Scope, value: str, prompt: str, timeout: float
) -> tuple[bool, float]:
    """Poll rather than sleep, and return how long it actually took — that
    number is what sizes the run's timeouts (invariant 10)."""
    started = time.monotonic()
    interval = 0.25
    while True:
        if _visible(adapter, scope, value, prompt):
            return True, time.monotonic() - started
        waited = time.monotonic() - started
        if waited >= timeout:
            return False, waited
        time.sleep(min(interval, timeout - waited))
        interval = min(interval * 1.5, 2.0)


def run_doctor(adapter: MemoryAdapter, timeout: float = 30.0) -> DoctorReport:
    report = DoctorReport()
    add = report.checks.append
    prompt = f"What is the {_KEY} for this user?"

    # ---- C1 reset ------------------------------------------------------
    try:
        adapter.reset(namespace=f"{_MARK}-{int(time.time())}")
        add(Check("reset", "reset endpoint responds", PASS, "reset accepted"))
    except Exception as e:  # noqa: BLE001
        add(Check(
            "reset", "reset endpoint responds", FAIL, f"{type(e).__name__}: {e}",
            "POST /reset must accept {\"namespace\": str} and return 2xx with a "
            "JSON body. Clear any state your shim holds for that namespace.",
        ))
        return report  # nothing downstream is meaningful

    # ---- C2 write ------------------------------------------------------
    try:
        adapter.write(_A, _KEY, _V1)
        add(Check("write", "write endpoint accepts a fact", PASS, "write accepted"))
    except Exception as e:  # noqa: BLE001
        add(Check(
            "write", "write endpoint accepts a fact", FAIL, f"{type(e).__name__}: {e}",
            "POST /write must accept {\"tenant_id\",\"user_id\",\"key\",\"value\","
            "\"ttl_steps\"?} and return 2xx. Store the value against the "
            "(tenant_id, user_id, key) triple.",
        ))
        return report

    # ---- C3 query response contract ------------------------------------
    try:
        result = adapter.query(_A, prompt)
        if not isinstance(result.answer, str):
            add(Check(
                "query_shape", "query returns an answer string", FAIL,
                f"answer was {type(result.answer).__name__}, not str",
                "POST /query must return JSON with a string \"answer\" field. "
                "Optional: \"retrieved\": [...] for evidence only — it is never "
                "graded.",
            ))
        else:
            add(Check("query_shape", "query returns an answer string", PASS,
                      f"answer is a {len(result.answer)}-char string"))
    except Exception as e:  # noqa: BLE001
        add(Check(
            "query_shape", "query returns an answer string", FAIL,
            f"{type(e).__name__}: {e}",
            "POST /query must accept {\"tenant_id\",\"user_id\",\"prompt\","
            "\"seed\"} and return JSON containing \"answer\": str.",
        ))
        return report

    # ---- C4 write is readable, and how fast ----------------------------
    seen, waited = _wait_visible(adapter, _A, _V1, prompt, timeout)
    if seen:
        report.convergence_seconds = waited
        report.suggested_timeout = max(5.0, round(waited * 4 + 2, 1))
        add(Check("convergence", "a written fact reaches the answer", PASS,
                  f"visible after {waited:.2f}s"))
    else:
        add(Check(
            "convergence", "a written fact reaches the answer", FAIL,
            f"not present in any answer within {waited:.0f}s",
            "Your /query must surface stored facts in the answer text. Check "
            "that retrieval is scoped to the same (tenant_id, user_id) the "
            "write used, and that the value appears verbatim in the answer — "
            "the default judge matches exact values, not paraphrases.",
        ))

    # ---- C5/C6 scope isolation -----------------------------------------
    for scope, cid, label, why in (
        (_B, "scope_user", "another user cannot see it",
         "user_id is being ignored in retrieval"),
        (_C, "scope_tenant", "another tenant cannot see it",
         "tenant_id is being ignored in retrieval"),
    ):
        if _visible(adapter, scope, _V1, prompt):
            add(Check(
                scope_id := cid, label, FAIL,
                f"{_V1!r} leaked into {scope.tenant_id}/{scope.user_id} — {why}",
                "Filter retrieval by BOTH tenant_id and user_id, and scope the "
                "context you pass your agent the same way. A P1 leak here means "
                "one customer's memory can drive another's answer.",
            ))
        else:
            add(Check(cid, label, PASS, "no cross-scope visibility"))

    # ---- C7 delete removes ---------------------------------------------
    try:
        adapter.delete(_A, _KEY)
        gone, dwait = _wait_visible(adapter, _A, _V1, prompt, timeout)
        if gone:
            add(Check(
                "delete", "delete makes a fact unreachable", FAIL,
                f"{_V1!r} still drives the answer {dwait:.0f}s after delete",
                "POST /delete must make the value unreachable from /query. A "
                "soft-delete flag that retrieval ignores fails here — that is "
                "deletion residue, the P1 this tool exists to catch.",
            ))
        else:
            add(Check("delete", "delete makes a fact unreachable", PASS,
                      "value no longer influences answers"))
    except Exception as e:  # noqa: BLE001
        add(Check(
            "delete", "delete makes a fact unreachable", FAIL,
            f"{type(e).__name__}: {e}",
            "POST /delete must accept {\"tenant_id\",\"user_id\",\"key\"} and "
            "return 2xx.",
        ))

    # ---- C8 reset clears ------------------------------------------------
    try:
        adapter.write(_A, _KEY, _V2)
        _wait_visible(adapter, _A, _V2, prompt, timeout)
        adapter.reset(namespace=f"{_MARK}-{int(time.time())}-second")
        if _visible(adapter, _A, _V2, prompt):
            add(Check(
                "reset_clears", "reset clears prior state", FAIL,
                f"{_V2!r} survived a reset",
                "POST /reset must clear state for the namespace so each "
                "scenario starts clean. Leftovers from a previous run are "
                "scored as residue against you.",
            ))
        else:
            add(Check("reset_clears", "reset clears prior state", PASS,
                      "state cleared"))
    except Exception as e:  # noqa: BLE001
        add(Check("reset_clears", "reset clears prior state", FAIL,
                  f"{type(e).__name__}: {e}",
                  "POST /reset must clear state for the given namespace."))

    # ---- C9 advance_time -------------------------------------------------
    if getattr(adapter, "supports_ttl", False):
        try:
            adapter.advance_time(1)
            add(Check("advance_time", "advance_time accepted", PASS,
                      "logical clock advanced"))
        except Exception as e:  # noqa: BLE001
            add(Check(
                "advance_time", "advance_time accepted", FAIL,
                f"{type(e).__name__}: {e}",
                "You declared supports_ttl: true, so POST /advance_time must "
                "accept {\"steps\": int} and age your stored facts by that many "
                "logical steps. If you cannot, set supports_ttl: false and "
                "expiry will report NOT TESTED instead.",
            ))
    else:
        add(Check("advance_time", "advance_time accepted", SKIP,
                  "supports_ttl is false — expiry will report NOT TESTED",
                  "This is the honest setting unless your stack can age facts "
                  "by logical steps on demand."))

    return report


def print_report(report: DoctorReport, adapter_name: str) -> None:
    icon = {PASS: "ok  ", FAIL: "FAIL", SKIP: "skip"}
    print(f"\nmemorycheck doctor — adapter={adapter_name}\n")
    width = max(len(c.title) for c in report.checks) + 2
    for c in report.checks:
        print(f"  [{icon[c.status]}] {c.title.ljust(width)} {c.detail}")

    if report.convergence_seconds is not None:
        print(f"\n  write→read convergence: {report.convergence_seconds:.2f}s")
        print(f"  suggested timeout for your stack: {report.suggested_timeout:.0f}s")
        if report.convergence_seconds > 1.0:
            print("  (your store is eventually consistent — memorycheck polls for "
                  "each write rather than assuming it landed)")

    if report.failed:
        print(f"\n  {len(report.failed)} contract failure(s) — fix before running "
              "scenarios:\n")
        for c in report.failed:
            print(f"  * {c.title}")
            print(f"      what happened: {c.detail}")
            print(f"      fix:           {c.fix}")
        print("\n  Running the pack now would report these as memory findings "
              "when they are integration bugs.\n")
    else:
        print("\n  All contract checks passed. Run the pack:\n"
              "    memorycheck run scenarios --adapter http:your-config.yaml\n")
