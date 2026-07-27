"""memorycheck CLI.

  memorycheck validate scenarios/
  memorycheck run scenarios/ --adapter reference:naive --report-md out/report.md
  memorycheck list-adapters

`run` exits non-zero when the gate fails — wire it straight into CI.
"""

from __future__ import annotations

import argparse
import sys

from .adapters import load_adapter
from .judge import load_judge
from .ledger import ScenarioError
from .oracle import evaluate
from .report import build_summary, print_summary, write_reports
from .runner import run_suite
from .scenario import load_dir


def _cmd_validate(args: argparse.Namespace) -> int:
    scenarios = load_dir(args.scenarios)
    warnings = 0
    for sc in scenarios:
        print(f"  ok  {sc.id}  ({len(sc.steps)} steps)  {sc.title}")
        for w in sc.warnings:
            warnings += 1
            print(f"      warning: {w}")
    print(f"\n{len(scenarios)} scenario(s) valid, {warnings} warning(s).")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    scenarios = load_dir(args.scenarios)
    adapter = load_adapter(args.adapter)
    judge = load_judge(args.judge)

    suite = run_suite(
        scenarios, adapter, judge, seeds=args.seeds, baseline=not args.no_baseline
    )
    findings = evaluate(suite["runs"])
    baseline_findings = evaluate(suite["baseline_runs"])

    summary = build_summary(
        findings=findings,
        baseline_findings=baseline_findings,
        adapter_name=adapter.name,
        judge_name=judge.name,
        seeds=args.seeds,
        scenario_count=len(scenarios),
        fail_on=args.fail_on,
    )
    print_summary(summary)
    write_reports(summary, args.report_json, args.report_md)
    if args.report_json:
        print(f"  evidence (json): {args.report_json}")
    if args.report_md:
        print(f"  evidence (md):   {args.report_md}")
    return 1 if summary["gate"]["verdict"] == "FAIL" else 0


def _cmd_list_adapters(_: argparse.Namespace) -> int:
    print("  reference:strict   in-process store honouring the full lifecycle (demo: passes)")
    print("  reference:naive    ignores supersession, deletion and TTL (demo: fails P1/P2)")
    print("  reference:leaky    naive + tenant-only boundary (demo: adds scope leakage)")
    print("  http:CONFIG.yaml   your stack behind a 4-endpoint shim (see adapters/http.py)")
    print("  null               no-memory baseline (used internally for utility delta)")
    print("\n  roadmap: mem0, zep, langgraph")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="memorycheck",
        description="Behavioural release gate for agent memory lifecycles.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="validate scenario files")
    p_val.add_argument("scenarios", help="scenario file or directory")
    p_val.set_defaults(func=_cmd_validate)

    p_run = sub.add_parser("run", help="run the lifecycle suite against an adapter")
    p_run.add_argument("scenarios", help="scenario file or directory")
    p_run.add_argument("--adapter", default="reference:strict", help="adapter spec")
    p_run.add_argument("--judge", default="deterministic", help="usage judge")
    p_run.add_argument("--seeds", type=int, default=1, help="repeat runs per scenario")
    p_run.add_argument("--no-baseline", action="store_true",
                       help="skip the no-memory baseline (utility delta reports NOT TESTED)")
    p_run.add_argument("--fail-on", choices=["p1", "p2"], default="p2",
                       help="gate threshold: p1 = block only on P1; p2 = block on P1+P2")
    p_run.add_argument("--report-json", default=None, help="write JSON evidence here")
    p_run.add_argument("--report-md", default=None, help="write markdown evidence here")
    p_run.set_defaults(func=_cmd_run)

    p_list = sub.add_parser("list-adapters", help="show available adapters")
    p_list.set_defaults(func=_cmd_list_adapters)

    args = parser.parse_args(argv)
    try:
        sys.exit(args.func(args))
    except ScenarioError as e:
        print(f"scenario error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
