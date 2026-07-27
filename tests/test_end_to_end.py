from pathlib import Path

from memorycheck.adapters import load_adapter
from memorycheck.judge import load_judge
from memorycheck.oracle import FAIL, evaluate
from memorycheck.report import build_summary, to_markdown
from memorycheck.runner import run_suite
from memorycheck.scenario import load_dir

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


def run(adapter_spec: str) -> dict:
    scenarios = load_dir(SCENARIOS)
    suite = run_suite(
        scenarios,
        load_adapter(adapter_spec),
        load_judge("deterministic"),
        seeds=2,
        baseline=True,
    )
    findings = evaluate(suite["runs"])
    baseline = evaluate(suite["baseline_runs"])
    return build_summary(
        findings, baseline,
        adapter_name=adapter_spec, judge_name="deterministic-v0",
        seeds=2, scenario_count=len(scenarios), fail_on="p2",
    )


def failed_checks(summary: dict) -> set[str]:
    return {f["check"] for f in summary["findings"] if f["status"] == FAIL}


def test_strict_reference_passes_the_gate():
    summary = run("reference:strict")
    assert summary["gate"]["verdict"] == "PASS"
    assert failed_checks(summary) == set()
    assert summary["seed_stability"]["stable"] is True


def test_naive_reference_fails_on_stale_and_deleted():
    summary = run("reference:naive")
    assert summary["gate"]["verdict"] == "FAIL"
    checks = failed_checks(summary)
    assert "stale_reuse" in checks
    assert "deletion_residue" in checks
    assert "expiry_leak" in checks
    assert "scope_leakage" not in checks  # naive keeps the boundary


def test_leaky_reference_adds_scope_leakage():
    summary = run("reference:leaky")
    assert "scope_leakage" in failed_checks(summary)
    # P1-only gating still fails: leakage and residue are P1.
    assert summary["gate"]["verdict"] == "FAIL"


def test_unverified_flag_is_stamped_into_the_report():
    # A figure from an adapter that never touched its provider must not be
    # quotable as a measurement, however it is copied out of the evidence.
    summary = build_summary(
        [], [], adapter_name="zep", judge_name="deterministic-v0",
        seeds=1, scenario_count=1, fail_on="p2",
        unverified=True, unverified_note="never run against live Zep",
    )
    assert summary["adapter_unverified"] is True
    assert summary["adapter_unverified_note"] == "never run against live Zep"
    md = to_markdown(summary)
    assert "UNVERIFIED ADAPTER" in md
    assert "never run against live Zep" in md


def test_verified_adapters_carry_no_unverified_stamp():
    summary = run("reference:strict")
    assert summary["adapter_unverified"] is False
    assert "UNVERIFIED" not in to_markdown(summary)


def test_zep_adapter_declares_itself_unverified():
    from memorycheck.adapters.zep import ZepAdapter

    assert ZepAdapter.unverified is True
    assert ZepAdapter.unverified_note


def test_memory_utility_delta_is_positive_for_working_memory():
    summary = run("reference:strict")
    delta = summary["metrics"]["memory_utility_delta"]["delta"]
    assert delta is not None and delta > 0
