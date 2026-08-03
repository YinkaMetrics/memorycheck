"""Runner: executes a scenario against an adapter, in lockstep with the
ground-truth ledger, and records what the agent actually did at each query.

The runner never trusts the adapter's return values for correctness — the
adapter is the system under test. Rescopes are replayed from the ledger's
own record of the moved value, so adapters need no read API.
"""

from __future__ import annotations

import time
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


@dataclass
class OpTiming:
    """One adapter call, timed. `number` is cumulative across the whole suite,
    so a slow op can be located on the run's timeline, not just in its
    scenario."""
    number: int
    scenario_id: str
    seed: int
    step_index: int
    op: str
    seconds: float
    failed: bool = False


@dataclass
class ScenarioTiming:
    scenario_id: str
    seed: int
    seconds: float
    ops: int
    cumulative_ops: int
    mean_op_seconds: float
    max_op_seconds: float
    max_op: str


class RunProgress:
    """Collects adapter-call latencies while a suite runs.

    Exists for the abort case. A run that dies partway tells you only *where*
    it died; this tells you whether latency was climbing on the way there — a
    backlog- or load-dependent mechanism looks like a curve, a one-off looks
    like a spike against a flat baseline. Two properties matter:

    * it is mutated in place, so a caller that passes one in still holds the
      timings after an exception unwinds the suite; and
    * ops are timed in a `finally`, so the operation that *caused* the abort
      is recorded with its duration rather than lost.
    """

    def __init__(self, emit=None):
        self.ops: list[OpTiming] = []
        self.scenarios: list[ScenarioTiming] = []
        self.emit = emit

    def record_op(self, scenario_id, seed, step_index, op, seconds, failed=False):
        self.ops.append(OpTiming(len(self.ops) + 1, scenario_id, seed,
                                 step_index, op, seconds, failed))

    def finish_scenario(self, scenario_id, seed, seconds, ops_before):
        mine = self.ops[ops_before:]
        durations = [o.seconds for o in mine] or [0.0]
        slowest = max(mine, key=lambda o: o.seconds, default=None)
        timing = ScenarioTiming(
            scenario_id=scenario_id,
            seed=seed,
            seconds=seconds,
            ops=len(mine),
            cumulative_ops=len(self.ops),
            mean_op_seconds=sum(durations) / len(durations),
            max_op_seconds=max(durations),
            max_op=slowest.op if slowest else "-",
        )
        self.scenarios.append(timing)
        if self.emit:
            self.emit(timing)
        return timing

    # -- reporting ---------------------------------------------------------

    def curve_lines(self) -> list[str]:
        """Per-scenario latency table, oldest first. Read it for a trend."""
        if not self.scenarios:
            return ["  (no scenario completed)"]
        head = (f"  {'scenario':34s}{'seed':>5s}{'ops':>6s}{'cum':>7s}"
                f"{'elapsed':>10s}{'mean op':>10s}{'max op':>10s}  slowest")
        rows = [head]
        for t in self.scenarios:
            rows.append(
                f"  {t.scenario_id[:34]:34s}{t.seed:>5d}{t.ops:>6d}"
                f"{t.cumulative_ops:>7d}{t.seconds:>9.1f}s"
                f"{t.mean_op_seconds:>9.2f}s{t.max_op_seconds:>9.2f}s  {t.max_op}"
            )
        return rows

    def tail_lines(self, n: int = 10) -> list[str]:
        """The last few operations, so the aborting one is visible in context."""
        rows = []
        for o in self.ops[-n:]:
            mark = "  <-- FAILED HERE" if o.failed else ""
            rows.append(
                f"  #{o.number:<5d} {o.scenario_id[:30]:30s} seed {o.seed}  "
                f"step {o.step_index:<3d} {o.op:<9s}{o.seconds:>8.2f}s{mark}"
            )
        return rows or ["  (no operation recorded)"]


def run_scenario(
    scenario: Scenario,
    adapter: MemoryAdapter,
    judge: Judge,
    seed: int = 0,
    progress: RunProgress | None = None,
) -> ScenarioRun:
    ledger = GroundTruthLedger()
    started = time.perf_counter()
    ops_before = len(progress.ops) if progress else 0
    _timed(progress, scenario.id, seed, -1, "reset",
           adapter.reset, namespace=f"memorycheck::{scenario.id}::{seed}")
    run = ScenarioRun(scenario=scenario, adapter_name=adapter.name, seed=seed)
    if scenario.uses_ttl and not adapter.supports_ttl:
        run.ttl_not_supported = True

    try:
        for step in scenario.steps:
            _apply(step, ledger, adapter, judge, run, seed, progress)
    finally:
        # Recorded even on abort: a partial scenario is still a data point on
        # the latency curve, and the abort case is why this exists.
        if progress:
            progress.finish_scenario(scenario.id, seed,
                                     time.perf_counter() - started, ops_before)
    return run


def _timed(progress, scenario_id, seed, step_index, op_name, fn, /, *args, **kwargs):
    """Run one adapter call, recording its duration even if it raises.

    The `finally` is the point: the operation that aborts a run is the most
    informative timing in the whole run, and it is exactly the one a naive
    `record after success` would drop.

    The parameters before `/` are positional-only on purpose: adapter calls
    take their own `seed=` keyword, which would otherwise collide with this
    function's own `seed` and fail at call time.
    """
    if progress is None:
        return fn(*args, **kwargs)
    started = time.perf_counter()
    failed = False
    try:
        return fn(*args, **kwargs)
    except BaseException:
        failed = True
        raise
    finally:
        progress.record_op(scenario_id, seed, step_index, op_name,
                           time.perf_counter() - started, failed)


def _apply(
    step: Step,
    ledger: GroundTruthLedger,
    adapter: MemoryAdapter,
    judge: Judge,
    run: ScenarioRun,
    seed: int,
    progress: RunProgress | None = None,
) -> None:
    sid = run.scenario.id
    if step.op in ("write", "correct"):
        ledger.write(step.scope, step.key, step.value, ttl_steps=step.ttl_steps)
        _timed(progress, sid, seed, step.index, step.op,
               adapter.write, step.scope, step.key, step.value,
               ttl_steps=step.ttl_steps)
    elif step.op == "delete":
        ledger.delete(step.scope, step.key)
        _timed(progress, sid, seed, step.index, "delete",
               adapter.delete, step.scope, step.key)
    elif step.op == "rescope":
        value = ledger.rescope(step.scope, step.to_scope, step.key)
        # Replay against the adapter from ground truth — no read API required.
        # Timed as two ops: the 012 abort is the *write* half, and averaging it
        # with the delete would blur exactly the number we are looking for.
        _timed(progress, sid, seed, step.index, "rescope:del",
               adapter.delete, step.scope, step.key)
        _timed(progress, sid, seed, step.index, "rescope:add",
               adapter.write, step.to_scope, step.key, value)
    elif step.op == "advance_time":
        ledger.advance_time(step.steps)
        _timed(progress, sid, seed, step.index, "advance_time",
               adapter.advance_time, step.steps)
    elif step.op == "query":
        result = _timed(progress, sid, seed, step.index, "query",
                        adapter.query, step.scope, step.prompt, seed=seed)
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
    progress: RunProgress | None = None,
) -> dict:
    """Run every scenario across N seeds, plus (optionally) the no-memory
    baseline used for the Memory Utility Delta. Returns raw runs for the
    oracle; interpretation lives in oracle/report, not here.

    `progress` is optional instrumentation. Pass one in and it is filled as the
    suite runs — the caller keeps it if the suite raises, which is the whole
    point. The baseline is deliberately NOT instrumented: it never touches the
    provider, so its timings would flatten the curve with in-process noise."""
    runs: list[ScenarioRun] = []
    for scenario in scenarios:
        for seed in range(seeds):
            runs.append(run_scenario(scenario, adapter, judge, seed=seed,
                                     progress=progress))

    baseline_runs: list[ScenarioRun] = []
    if baseline:
        null_adapter = NullAdapter()
        for scenario in scenarios:
            baseline_runs.append(run_scenario(scenario, null_adapter, judge, seed=0))

    return {"runs": runs, "baseline_runs": baseline_runs}
