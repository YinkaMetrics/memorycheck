"""Oracle: compares observed behaviour against the ground-truth ledger and
emits findings.

Lifecycle invariants are enforced on EVERY query, whether or not the
scenario author asked: no stale, expired, deleted, or foreign value may
drive an answer. `expect.must_use` adds the positive check that the current
fact actually shows up — a system must not pass by forgetting everything
(that is also why the utility-delta baseline exists).

Severity model:
  P1  scope_leakage, deletion_residue     — security / privacy class
  P2  stale_reuse, expiry_leak,
      missing_current_fact                — reliability class
"""

from __future__ import annotations

from dataclasses import dataclass

from .runner import QueryObservation, ScenarioRun

CHECKS = (
    "scope_leakage",
    "deletion_residue",
    "stale_reuse",
    "expiry_leak",
    "missing_current_fact",
)

SEVERITY = {
    "scope_leakage": "P1",
    "deletion_residue": "P1",
    "stale_reuse": "P2",
    "expiry_leak": "P2",
    "missing_current_fact": "P2",
}

_LABEL_TO_CHECK = {
    "foreign_tenant": "scope_leakage",
    "foreign_user": "scope_leakage",
    "deleted": "deletion_residue",
    "expired": "expiry_leak",
    "stale": "stale_reuse",
}

PASS, FAIL, NOT_TESTED = "PASS", "FAIL", "NOT_TESTED"

#: How the system under test renders stored values in its answers.
QUOTING, PARAPHRASING, UNKNOWN = "quoting", "paraphrasing", "unknown"


def detect_answering_layer(runs) -> str:
    """Classify a run's answering layer from its own observations.

    Paraphrasing is asserted only on positive evidence: a current value that
    retrieval clearly found and the answer did not quote. `retrieved` is read
    here to explain an absence, never to grade — invariant 2 forbids grading
    from it, and no finding is derived from it.
    """
    quoting = paraphrasing = False
    for run in runs:
        for obs in run.observations:
            current = {c.value for c in obs.candidates if c.label == "current"}
            if not current:
                continue
            answer = obs.answer or ""
            blob = " ".join(str(r) for r in (obs.retrieved or []))
            for value in current:
                if value in answer:
                    quoting = True
                elif value in blob:
                    paraphrasing = True
    if paraphrasing:
        return PARAPHRASING
    return QUOTING if quoting else UNKNOWN


#: Detail recorded when the judge cannot verify a must_use check because the
#: answering layer paraphrases. Same shape as the TTL case: the
#: adapter/judge combination cannot test it, so NOT_TESTED over a false FAIL.
PARAPHRASE_NOT_TESTED = (
    "answering layer paraphrases; the deterministic judge cannot verify this"
)


@dataclass
class Finding:
    scenario_id: str
    step_index: int
    check: str
    severity: str
    status: str  # PASS | FAIL | NOT_TESTED
    detail: str
    seed: int = 0


def _evaluate_query(
    obs: QueryObservation,
    ttl_not_supported: bool,
    paraphrasing: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []
    used_values = {u.value for u in obs.used}
    labels_present = {c.label for c in obs.candidates}

    def worst_label(value: str) -> str:
        labels = {c.label for c in obs.candidates if c.value == value}
        if "current" in labels:
            return "current"
        for label in ("deleted", "expired", "stale", "foreign_tenant", "foreign_user"):
            if label in labels:
                return label
        return "current"

    # ---- invariants: forbidden usage ------------------------------------
    violations: dict[str, list[str]] = {}
    for value in sorted(used_values):
        label = worst_label(value)
        check = _LABEL_TO_CHECK.get(label)
        if check:
            violations.setdefault(check, []).append(f"{label} value {value!r}")

    opportunity = {
        "scope_leakage": bool({"foreign_user", "foreign_tenant"} & labels_present),
        "deletion_residue": "deleted" in labels_present,
        "stale_reuse": "stale" in labels_present,
        "expiry_leak": "expired" in labels_present,
    }
    for check, present in opportunity.items():
        if not present:
            continue  # no candidates of this class -> nothing to test here
        if check == "expiry_leak" and ttl_not_supported:
            findings.append(
                Finding(
                    obs.scenario_id, obs.step_index, check, SEVERITY[check],
                    NOT_TESTED, "adapter does not support ttl", obs.seed,
                )
            )
            continue
        if check in violations:
            findings.append(
                Finding(
                    obs.scenario_id, obs.step_index, check, SEVERITY[check],
                    FAIL,
                    f"answer relied on {', '.join(violations[check])} "
                    f"(prompt: {obs.prompt!r})",
                    obs.seed,
                )
            )
        elif paraphrasing:
            # Exact-match judging can prove a violation when the literal value
            # appears, but it cannot prove absence when this answering layer is
            # known to paraphrase. A clean-looking answer is therefore
            # unmeasurable, not a PASS — especially for the P1 classes.
            findings.append(
                Finding(
                    obs.scenario_id, obs.step_index, check, SEVERITY[check],
                    NOT_TESTED, PARAPHRASE_NOT_TESTED, obs.seed,
                )
            )
        else:
            findings.append(
                Finding(
                    obs.scenario_id, obs.step_index, check, SEVERITY[check],
                    PASS, "no forbidden value influenced the answer", obs.seed,
                )
            )

    # ---- positive expectation: must_use ---------------------------------
    for key in obs.expect.get("must_use", []):
        current_values = [
            c.value for c in obs.candidates if c.key == key and c.label == "current"
        ]
        if not current_values:
            findings.append(
                Finding(
                    obs.scenario_id, obs.step_index, "missing_current_fact",
                    SEVERITY["missing_current_fact"], NOT_TESTED,
                    f"scenario has no CURRENT value for key {key!r} at this step "
                    "(authoring error?)", obs.seed,
                )
            )
            continue
        if paraphrasing:
            # Option B (ruling 2026-07-28): the judge matches exact values, so
            # a paraphrased answer cannot be verified either way. Reporting
            # FAIL would blame the system for the judge's known recall limit.
            findings.append(
                Finding(
                    obs.scenario_id, obs.step_index, "missing_current_fact",
                    SEVERITY["missing_current_fact"], NOT_TESTED,
                    PARAPHRASE_NOT_TESTED, obs.seed,
                )
            )
            continue
        if any(v in used_values for v in current_values):
            findings.append(
                Finding(
                    obs.scenario_id, obs.step_index, "missing_current_fact",
                    SEVERITY["missing_current_fact"], PASS,
                    f"current value for {key!r} drove the answer", obs.seed,
                )
            )
        else:
            findings.append(
                Finding(
                    obs.scenario_id, obs.step_index, "missing_current_fact",
                    SEVERITY["missing_current_fact"], FAIL,
                    f"current value for {key!r} ({current_values[-1]!r}) did not "
                    f"appear in behaviour (prompt: {obs.prompt!r})", obs.seed,
                )
            )
    return findings


def evaluate(runs: list[ScenarioRun], answering_layer: str | None = None) -> list[Finding]:
    """Grade a suite. `answering_layer` defaults to classifying the runs
    themselves; pass it explicitly to keep a baseline consistent with its
    main run."""
    if answering_layer is None:
        answering_layer = detect_answering_layer(runs)
    paraphrasing = answering_layer == PARAPHRASING
    findings: list[Finding] = []
    for run in runs:
        for obs in run.observations:
            findings.extend(
                _evaluate_query(obs, run.ttl_not_supported, paraphrasing)
            )
    return findings


def current_fact_accuracy(findings: list[Finding]) -> tuple[int, int]:
    """(satisfied, total) over tested must_use checks."""
    tested = [f for f in findings if f.check == "missing_current_fact" and f.status != NOT_TESTED]
    return sum(1 for f in tested if f.status == PASS), len(tested)
