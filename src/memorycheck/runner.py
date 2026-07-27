"""Runner: executes a scenario against an adapter, in lockstep with the
ground-truth ledger, and records what the agent actually did at each query.

The runner never trusts the adapter's return values for correctness — the
adapter is the system under test. Rescopes are replayed from the ledger's
own record of the moved value, so adapters need no read API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .adapters.base import MemoryAdapter, NullAdapter
from .judge import Judge, Usage
from .ledger import Candidate, GroundTruthLedger, Scope
from .scenario import Scenario, Step


@dataclass
class QueryObservation:
    scenario_id: str
    step_index: int
    scope: Scope
    prompt: str
    answer: str
    retrieved: list[dict]
    used: list[Usage]
    candidates: list[Candidate]
    expect: dict
    seed: int


@dataclass
class ScenarioRun:
    scenario: Scenario
    adapter_name: str
    seed: int
    observations: list[QueryObservation] = field(default_factory=list)
    ttl_not_supported: bool = False


def run_scenario(
    scenario: Scenario, adapter: MemoryAdapter, judge: Judge, seed: int = 0
) -> ScenarioRun:
    ledger = GroundTruthLedger()
    adapter.reset(namespace=f"memorycheck::{scenario.id}::{seed}")
    run = ScenarioRun(scenario=scenario, adapter_name=adapter.name, seed=seed)
    if scenario.uses_ttl and not adapter.supports_ttl:
        run.ttl_not_supported = True

    for step in scenario.steps:
        _apply(step, ledger, adapter, judge, run, seed)
    return run


def _apply(
    step: Step,
    ledger: GroundTruthLedger,
    adapter: MemoryAdapter,
    judge: Judge,
    run: ScenarioRun,
    seed: int,
) -> None:
    if step.op in ("write", "correct"):
        ledger.write(step.scope, step.key, step.value, ttl_steps=step.ttl_steps)
        adapter.write(step.scope, step.key, step.value, ttl_steps=step.ttl_steps)
    elif step.op == "delete":
        ledger.delete(step.scope, step.key)
        adapter.delete(step.scope, step.key)
    elif step.op == "rescope":
        value = ledger.rescope(step.scope, step.to_scope, step.key)
        # Replay against the adapter from ground truth — no read API required.
        adapter.delete(step.scope, step.key)
        adapter.write(step.to_scope, step.key, value)
    elif step.op == "advance_time":
        ledger.advance_time(step.steps)
        adapter.advance_time(step.steps)
    elif step.op == "query":
        result = adapter.query(step.scope, step.prompt, seed=seed)
        candidates = ledger.snapshot(step.scope)
        used = judge.classify(result.answer, [c.value for c in candidates])
        run.observations.append(
            QueryObservation(
                scenario_id=run.scenario.id,
                step_index=step.index,
                scope=step.scope,
                prompt=step.prompt,
                answer=result.answer,
                retrieved=result.retrieved,
                used=used,
                candidates=candidates,
                expect=step.expect,
                seed=seed,
            )
        )


def run_suite(
    scenarios: list[Scenario],
    adapter: MemoryAdapter,
    judge: Judge,
    seeds: int = 1,
    baseline: bool = True,
) -> dict:
    """Run every scenario across N seeds, plus (optionally) the no-memory
    baseline used for the Memory Utility Delta. Returns raw runs for the
    oracle; interpretation lives in oracle/report, not here."""
    runs: list[ScenarioRun] = []
    for scenario in scenarios:
        for seed in range(seeds):
            runs.append(run_scenario(scenario, adapter, judge, seed=seed))

    baseline_runs: list[ScenarioRun] = []
    if baseline:
        null_adapter = NullAdapter()
        for scenario in scenarios:
            baseline_runs.append(run_scenario(scenario, null_adapter, judge, seed=0))

    return {"runs": runs, "baseline_runs": baseline_runs}
