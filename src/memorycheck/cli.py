"""memorycheck CLI.

  memorycheck doctor --adapter http:your-config.yaml   # check a shim first
  memorycheck validate scenarios/
  memorycheck run scenarios/ --adapter reference:naive --report-md out/report.md
  memorycheck list-adapters

`run` exits non-zero when the gate fails or is inconclusive — wire it straight
into CI.
"""

from __future__ import annotations

import argparse
import sys

from .adapters import load_adapter
from .adapters.base import AdapterError
from .doctor import detect_answering_layer, print_report, run_doctor
from .judge import load_judge
from .ledger import ScenarioError
from .oracle import evaluate
from .report import build_summary, print_summary, write_reports
from .runner import RunProgress, run_suite
from .scenario import load_dir


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


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


UNVERIFIED_BANNER = (
    "UNVERIFIED ADAPTER — never run against the live provider. "
    "No results from this run should be quoted as a measurement."
)


def _warn_if_unverified(adapter) -> None:
    if not getattr(adapter, "unverified", False):
        return
    line = "=" * 72
    print(f"\n{line}\n  WARNING: {UNVERIFIED_BANNER}", file=sys.stderr)
    note = getattr(adapter, "unverified_note", "")
    if note:
        print(f"  adapter={adapter.name}: {note}", file=sys.stderr)
    print(f"{line}\n", file=sys.stderr)


#: Holds the in-flight RunProgress so the top-level AdapterError handler can
#: print the latency curve. A run that aborts is the case the instrumentation
#: exists for, so the trace must outlive the stack that produced it.
_RUN_PROGRESS: list = []


def _dump_progress() -> None:
    """On abort, print what the run had done and how long it was taking.

    Turns 'it failed at 012' into 'it failed at 012, and here is whether
    latency was climbing on the way there'. A backlog-dependent mechanism
    shows as a rising mean; a one-off shows as a spike on a flat line.
    """
    if not _RUN_PROGRESS:
        return
    progress = _RUN_PROGRESS[-1]
    if not progress.ops:
        return
    print("\n  --- run trace up to the abort ---", file=sys.stderr)
    for line in progress.curve_lines():
        print(line, file=sys.stderr)
    print("\n  last operations before the abort:", file=sys.stderr)
    for line in progress.tail_lines():
        print(line, file=sys.stderr)
    print("\n  Read the mean/max columns for a trend before reading the abort\n"
          "  as a property of the scenario it landed on.", file=sys.stderr)


def _cmd_run(args: argparse.Namespace) -> int:
    scenarios = load_dir(args.scenarios)
    adapter = load_adapter(args.adapter)
    judge = load_judge(args.judge)
    _warn_if_unverified(adapter)

    # Instrumentation. Held by this frame, so an AdapterError partway through
    # still leaves the latency curve in hand — see the handler in main().
    # Progress goes to stderr so stdout stays the report.
    progress = RunProgress(emit=lambda t: print(
        f"  [{t.cumulative_ops:>4d} ops] {t.scenario_id[:34]:34s} seed {t.seed}  "
        f"{t.seconds:>6.1f}s  mean {t.mean_op_seconds:>5.2f}s  "
        f"max {t.max_op_seconds:>5.2f}s ({t.max_op})", file=sys.stderr))
    _RUN_PROGRESS.append(progress)

    suite = run_suite(
        scenarios, adapter, judge, seeds=args.seeds,
        baseline=not args.no_baseline, progress=progress,
    )
    # Classify once and apply to both, so the baseline is graded on the same
    # terms as the run it is compared against.
    answering_layer = detect_answering_layer(suite["runs"])
    findings = evaluate(suite["runs"], answering_layer)
    baseline_findings = evaluate(suite["baseline_runs"], answering_layer)

    summary = build_summary(
        findings=findings,
        baseline_findings=baseline_findings,
        adapter_name=adapter.name,
        judge_name=judge.name,
        seeds=args.seeds,
        scenario_count=len(scenarios),
        fail_on=args.fail_on,
        unverified=getattr(adapter, "unverified", False),
        unverified_note=getattr(adapter, "unverified_note", ""),
        answering_layer=answering_layer,
        run_id=suite["run_id"],
    )
    print_summary(summary)
    write_reports(summary, args.report_json, args.report_md)
    if args.report_json:
        print(f"  evidence (json): {args.report_json}")
    if args.report_md:
        print(f"  evidence (md):   {args.report_md}")
    return 0 if summary["gate"]["verdict"] == "PASS" else 1


def _cmd_doctor(args: argparse.Namespace) -> int:
    adapter = load_adapter(args.adapter)
    _warn_if_unverified(adapter)
    report = run_doctor(adapter, timeout=args.timeout)
    print_report(report, adapter.name)
    return 1 if report.failed else 0


def _cmd_list_adapters(_: argparse.Namespace) -> int:
    print("  reference:strict   in-process store honouring the full lifecycle (demo: passes)")
    print("  reference:naive    ignores supersession, deletion and TTL (demo: fails P1/P2)")
    print("  reference:leaky    naive + tenant-only boundary (demo: adds scope leakage)")
    print("  http:CONFIG.yaml   your stack behind a 4-endpoint shim (see adapters/http.py)")
    print("  mem0               hosted Mem0 platform (needs MEM0_API_KEY; pip install '.[mem0]')")
    print("  zep                hosted Zep platform (needs ZEP_API_KEY; pip install '.[zep]')")
    print("  langgraph[:KIND]   local LangGraph store: memory (default) or sqlite[:path]")
    print("  null               no-memory baseline (used internally for utility delta)")
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
    p_run.add_argument("--seeds", type=_positive_int, default=1,
                       help="repeat runs per scenario (must be >= 1)")
    p_run.add_argument("--no-baseline", action="store_true",
                       help="skip the no-memory baseline (utility delta reports NOT TESTED)")
    p_run.add_argument("--fail-on", choices=["p1", "p2"], default="p2",
                       help="gate threshold: p1 = block only on P1; p2 = block on P1+P2")
    p_run.add_argument("--report-json", default=None, help="write JSON evidence here")
    p_run.add_argument("--report-md", default=None, help="write markdown evidence here")
    p_run.set_defaults(func=_cmd_run)

    p_doc = sub.add_parser(
        "doctor",
        help="check a shim against the adapter contract before running scenarios",
    )
    p_doc.add_argument("--adapter", default="http:memorycheck_http.yaml",
                       help="adapter spec, e.g. http:your-config.yaml")
    p_doc.add_argument("--timeout", type=float, default=30.0,
                       help="seconds to wait for a write to become readable")
    p_doc.set_defaults(func=_cmd_doctor)

    p_list = sub.add_parser("list-adapters", help="show available adapters")
    p_list.set_defaults(func=_cmd_list_adapters)

    args = parser.parse_args(argv)
    try:
        sys.exit(args.func(args))
    except ScenarioError as e:
        print(f"scenario error: {e}", file=sys.stderr)
        sys.exit(2)
    except AdapterError as e:
        print(f"adapter error: {e}", file=sys.stderr)
        _dump_progress()
        sys.exit(2)


if __name__ == "__main__":
    main()
