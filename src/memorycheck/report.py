"""Report: aggregate findings into the scorecard, evidence files and the
gate verdict.

Metric definitions (rates are violations / opportunities; a check with zero
opportunities across the suite is reported NOT TESTED, never PASS):

  current_fact_accuracy   share of tested must_use checks satisfied
  stale_reuse_rate        queries relying on a superseded value
  scope_leakage_rate      queries relying on another user/tenant's value
  deletion_residual_rate  queries relying on a deleted value
  expiry_leak_rate        queries relying on an expired value
  memory_utility_delta    current_fact_accuracy(with memory) minus the
                          no-memory baseline — forgetting everything is
                          not a passing strategy
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from . import __version__
from .oracle import CHECKS, FAIL, NOT_TESTED, PASS, SEVERITY, Finding, current_fact_accuracy

RATE_CHECKS = ("scope_leakage", "deletion_residue", "stale_reuse", "expiry_leak")

_SEV_RANK = {"p1": 1, "p2": 2}


def _rate(findings: list[Finding], check: str) -> dict:
    tested = [f for f in findings if f.check == check and f.status != NOT_TESTED]
    fails = sum(1 for f in tested if f.status == FAIL)
    skipped = sum(1 for f in findings if f.check == check and f.status == NOT_TESTED)
    return {
        "opportunities": len(tested),
        "violations": fails,
        "not_tested": skipped,
        "rate": (fails / len(tested)) if tested else None,
    }


def build_summary(
    findings: list[Finding],
    baseline_findings: list[Finding],
    adapter_name: str,
    judge_name: str,
    seeds: int,
    scenario_count: int,
    fail_on: str,
) -> dict:
    checks = {c: _rate(findings, c) for c in RATE_CHECKS}
    ok, total = current_fact_accuracy(findings)
    cfa = (ok / total) if total else None
    b_ok, b_total = current_fact_accuracy(baseline_findings)
    baseline_cfa = (b_ok / b_total) if b_total else None
    utility_delta = (
        cfa - baseline_cfa if cfa is not None and baseline_cfa is not None else None
    )

    threshold = _SEV_RANK[fail_on.lower()]
    blocking = [
        f
        for f in findings
        if f.status == FAIL and _SEV_RANK[f.severity.lower()] <= threshold
    ]
    # Seed stability: same (scenario, step, check) failing in some seeds only.
    by_key: dict[tuple, set[str]] = {}
    for f in findings:
        by_key.setdefault((f.scenario_id, f.step_index, f.check), set()).add(f.status)
    flaky = sorted({k for k, statuses in by_key.items() if len(statuses - {NOT_TESTED}) > 1})

    return {
        "tool": {"name": "memorycheck", "version": __version__},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "adapter": adapter_name,
        "judge": judge_name,
        "seeds": seeds,
        "scenarios": scenario_count,
        "gate": {
            "fail_on": fail_on.upper(),
            "verdict": FAIL if blocking else PASS,
            "blocking_findings": len(blocking),
        },
        "metrics": {
            "current_fact_accuracy": {"satisfied": ok, "total": total, "value": cfa},
            **checks,
            "memory_utility_delta": {
                "with_memory": cfa,
                "no_memory_baseline": baseline_cfa,
                "delta": utility_delta,
            },
        },
        "seed_stability": {"stable": not flaky, "unstable_checks": [list(k) for k in flaky]},
        "findings": [asdict(f) for f in findings if f.status != PASS],
    }


# ------------------------------------------------------------------ output


def _fmt_rate(entry: dict) -> str:
    if entry["opportunities"] == 0:
        return "NOT TESTED"
    pct = f"{entry['rate'] * 100:.0f}%"
    return f"{pct}  ({entry['violations']}/{entry['opportunities']})"


def to_markdown(summary: dict) -> str:
    m = summary["metrics"]
    cfa = m["current_fact_accuracy"]
    cfa_str = (
        f"{cfa['value'] * 100:.0f}%  ({cfa['satisfied']}/{cfa['total']})"
        if cfa["total"]
        else "NOT TESTED"
    )
    ud = m["memory_utility_delta"]
    ud_str = f"{ud['delta']:+.2f}" if ud["delta"] is not None else "NOT TESTED"
    lines = [
        "# memorycheck evidence report",
        "",
        f"- **Adapter:** `{summary['adapter']}`   **Judge:** `{summary['judge']}`",
        f"- **Scenarios:** {summary['scenarios']}   **Seeds:** {summary['seeds']}   "
        f"**Generated:** {summary['generated_at']}",
        f"- **Gate (fail on ≤{summary['gate']['fail_on']}):** "
        f"**{summary['gate']['verdict']}** "
        f"({summary['gate']['blocking_findings']} blocking findings)",
        "",
        "| Check | Severity | Result |",
        "|---|---|---|",
        f"| Current-fact accuracy | P2 | {cfa_str} |",
        f"| Stale-memory reuse | P2 | {_fmt_rate(m['stale_reuse'])} |",
        f"| Scope leakage | P1 | {_fmt_rate(m['scope_leakage'])} |",
        f"| Deletion residue | P1 | {_fmt_rate(m['deletion_residue'])} |",
        f"| Expiry leak | P2 | {_fmt_rate(m['expiry_leak'])} |",
        f"| Memory utility delta | — | {ud_str} |",
        "",
    ]
    failures = [f for f in summary["findings"] if f["status"] == FAIL]
    skipped = [f for f in summary["findings"] if f["status"] == NOT_TESTED]
    if failures:
        lines += ["## Failures", ""]
        for f in failures:
            lines.append(
                f"- **{f['severity']} {f['check']}** — `{f['scenario_id']}` "
                f"step {f['step_index']} (seed {f['seed']}): {f['detail']}"
            )
        lines.append("")
    if skipped:
        lines += ["## Not tested", ""]
        for f in skipped:
            lines.append(
                f"- {f['check']} — `{f['scenario_id']}` step {f['step_index']}: {f['detail']}"
            )
        lines.append("")
    if summary["seed_stability"]["unstable_checks"]:
        lines += [
            "## Seed instability",
            "",
            "The following checks disagreed across seeds (flaky behaviour):",
            "",
        ]
        for sc_id, step, check in summary["seed_stability"]["unstable_checks"]:
            lines.append(f"- `{sc_id}` step {step}: {check}")
        lines.append("")
    lines.append(
        "*Rates are violations/opportunities. A check with no opportunities is "
        "NOT TESTED — never silently passed.*"
    )
    return "\n".join(lines)


def print_summary(summary: dict) -> None:
    m = summary["metrics"]
    print(f"\nmemorycheck {__version__} — adapter={summary['adapter']} "
          f"judge={summary['judge']} scenarios={summary['scenarios']} seeds={summary['seeds']}")
    cfa = m["current_fact_accuracy"]
    rows = [
        ("current_fact_accuracy",
         f"{cfa['value'] * 100:.0f}% ({cfa['satisfied']}/{cfa['total']})" if cfa["total"] else "NOT TESTED"),
        ("stale_reuse", _fmt_rate(m["stale_reuse"])),
        ("scope_leakage", _fmt_rate(m["scope_leakage"])),
        ("deletion_residue", _fmt_rate(m["deletion_residue"])),
        ("expiry_leak", _fmt_rate(m["expiry_leak"])),
    ]
    ud = m["memory_utility_delta"]
    rows.append(("memory_utility_delta",
                 f"{ud['delta']:+.2f}" if ud["delta"] is not None else "NOT TESTED"))
    width = max(len(r[0]) for r in rows) + 2
    for name, value in rows:
        print(f"  {name.ljust(width)}{value}")
    print(f"\n  GATE [fail on <= {summary['gate']['fail_on']}]: {summary['gate']['verdict']} "
          f"({summary['gate']['blocking_findings']} blocking findings)\n")


def write_reports(summary: dict, json_path: str | None, md_path: str | None) -> None:
    if json_path:
        with open(json_path, "w") as fh:
            json.dump(summary, fh, indent=2)
    if md_path:
        with open(md_path, "w") as fh:
            fh.write(to_markdown(summary))
