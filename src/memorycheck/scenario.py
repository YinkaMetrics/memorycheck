"""Lifecycle scenario DSL: load and validate YAML scenario files.

A scenario is a subject (default scope) plus an ordered list of lifecycle
steps. Supported ops:

  write   key, value, [ttl_steps]      — store a fact
  correct key, value                   — alias of write (supersedes)
  delete  key                          — delete a key's full history
  rescope key, to: {user_id/tenant_id} — move a fact between scopes
  advance_time steps                   — move logical time (drives TTL expiry)
  query   prompt, [expect: {must_use: [keys]}], [as: {scope override}]

Every query is *always* checked against the full lifecycle invariants
(no stale / expired / deleted / foreign value may drive the answer);
`expect.must_use` adds the positive requirement that the current value
for a key actually shows up in behaviour.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .ledger import Scope, ScenarioError

VALID_OPS = {"write", "correct", "delete", "rescope", "advance_time", "query"}


@dataclass
class Step:
    op: str
    scope: Scope
    key: str | None = None
    value: str | None = None
    ttl_steps: int | None = None
    to_scope: Scope | None = None
    steps: int | None = None
    prompt: str | None = None
    expect: dict = field(default_factory=dict)
    index: int = 0


@dataclass
class Scenario:
    id: str
    title: str
    tags: list[str]
    subject: Scope
    steps: list[Step]
    path: str
    warnings: list[str] = field(default_factory=list)
    uses_ttl: bool = False


def _require(cond: bool, msg: str, path: str) -> None:
    if not cond:
        raise ScenarioError(f"{path}: {msg}")


def load_file(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text())
    p = str(path)
    _require(isinstance(raw, dict), "scenario file must be a mapping", p)
    for req in ("id", "title", "subject", "steps"):
        _require(req in raw, f"missing required field {req!r}", p)
    subj = raw["subject"]
    _require(
        isinstance(subj, dict) and "tenant_id" in subj and "user_id" in subj,
        "subject must contain tenant_id and user_id",
        p,
    )
    subject = Scope(tenant_id=str(subj["tenant_id"]), user_id=str(subj["user_id"]))

    steps: list[Step] = []
    uses_ttl = False
    for i, s in enumerate(raw["steps"]):
        _require(isinstance(s, dict) and "op" in s, f"step {i}: missing op", p)
        op = s["op"]
        _require(op in VALID_OPS, f"step {i}: unknown op {op!r}", p)
        scope = subject.merged(s.get("as"))
        step = Step(op=op, scope=scope, index=i)

        if op in ("write", "correct"):
            _require("key" in s and "value" in s, f"step {i}: {op} needs key and value", p)
            step.key = str(s["key"])
            step.value = str(s["value"])
            if "ttl_steps" in s:
                step.ttl_steps = int(s["ttl_steps"])
                uses_ttl = True
        elif op == "delete":
            _require("key" in s, f"step {i}: delete needs key", p)
            step.key = str(s["key"])
        elif op == "rescope":
            _require("key" in s and "to" in s, f"step {i}: rescope needs key and to", p)
            step.key = str(s["key"])
            step.to_scope = scope.merged(s["to"])
            _require(step.to_scope != scope, f"step {i}: rescope target equals source scope", p)
        elif op == "advance_time":
            _require("steps" in s, f"step {i}: advance_time needs steps", p)
            step.steps = int(s["steps"])
        elif op == "query":
            _require("prompt" in s, f"step {i}: query needs prompt", p)
            step.prompt = str(s["prompt"])
            expect = s.get("expect") or {}
            _require(isinstance(expect, dict), f"step {i}: expect must be a mapping", p)
            must_use = expect.get("must_use", [])
            _require(isinstance(must_use, list), f"step {i}: expect.must_use must be a list", p)
            step.expect = {"must_use": [str(k) for k in must_use]}
        steps.append(step)

    scenario = Scenario(
        id=str(raw["id"]),
        title=str(raw["title"]),
        tags=[str(t) for t in raw.get("tags", [])],
        subject=subject,
        steps=steps,
        path=p,
        uses_ttl=uses_ttl,
    )

    # Distinctiveness warning: the deterministic judge matches value strings,
    # so re-using the same value across facts blurs classification.
    values = Counter(st.value for st in steps if st.value is not None)
    for v, n in values.items():
        if n > 1:
            scenario.warnings.append(
                f"value {v!r} used {n} times — keep fact values distinctive "
                "so usage classification stays unambiguous"
            )
    return scenario


def load_dir(path: str | Path) -> list[Scenario]:
    root = Path(path)
    if root.is_file():
        return [load_file(root)]
    files = sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml"))
    if not files:
        raise ScenarioError(f"no scenario .yaml files found under {root}")
    return [load_file(f) for f in files]
