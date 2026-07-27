# CLAUDE.md — memorycheck

## What this is

A behavioural release gate for agent memory. It runs lifecycle scenarios
(write → correct → rescope → expire → delete) against a memory stack and
proves whether each operation changed the agent's *behaviour* — not whether
the store returned 200. Provider-neutral, local-first, sold on trust:
the honesty model below is the product's differentiator, not decoration.

Status: v0.1, 12/12 tests green, built in public. `pytest -q` must stay green
after every change.

## Architecture (src/memorycheck/)

- `ledger.py` — deterministic ground-truth state machine. Owns what is
  CURRENT / SUPERSEDED / EXPIRED / DELETED / foreign at every step. Logical
  time only moves on explicit `advance_time`.
- `scenario.py` — YAML DSL loader/validator. Warns on non-distinctive values.
- `judge.py` — usage classifier. v0 is deterministic (normalised containment).
- `runner.py` — executes steps against an adapter in lockstep with the ledger.
  Replays rescopes from ledger ground truth (adapters need no read API).
- `oracle.py` — findings + severities. Invariants enforced on EVERY query.
- `report.py` — metrics, scorecard, JSON/MD evidence, gate verdict/exit code.
- `adapters/` — `base.py` (contract + `AdapterError`), `reference.py`
  (strict/naive/leaky demo), `http.py` (customer shim), `mem0.py` (hosted
  Mem0), `NullAdapter` (no-memory baseline).
- `cli.py` — `validate` / `run` / `list-adapters`.

## Non-negotiable invariants

1. **The ledger owns ground truth.** Never a model, never the adapter, never
   the system under test grading itself.
2. **Adapters are untrusted.** Never ask an adapter to self-report
   correctness; never grade from its `retrieved` metadata — grade from the
   answer via the judge against the ledger.
3. **NOT_TESTED over silent pass.** Anything an adapter can't express (e.g.
   TTL) or a check with zero opportunities reports NOT_TESTED. A broken or
   partial adapter must never produce a PASS.
4. **No LLM judge until calibrated.** The semantic judge ships only after the
   protocol: ≥200 human-labelled examples, ≥90% precision on release-blocking
   classes. Until then `judge=llm` raises NotImplementedError. Do not wire a
   model in "temporarily".
5. **Severity model is fixed**: P1 = scope_leakage, deletion_residue;
   P2 = stale_reuse, expiry_leak, missing_current_fact. Don't add checks or
   change severities without an explicit decision from the founder.
6. **Classification precedence**: current wins; then in-scope dead states
   (deleted > expired > stale) before foreign labels — a rescoped value
   resurfacing at its old scope is deletion residue, not a false P1 leak.
7. **Adapter contract stays small**: write / delete / query / reset
   (+ optional advance_time, supports_ttl). No read/list API. Breaking this
   contract breaks the pitch ("thin shim, <100 lines, nothing leaves your
   infra").
8. **Deletion claims are bounded.** We evidence accessible retrieval and
   behavioural influence — never physical erasure of provider backups. Keep
   that language in all docs and reports.

## Conventions

- Python ≥3.10, stdlib-first. Core deps: PyYAML only. Per-adapter SDKs go in
  optional extras (e.g. `pip install -e ".[mem0]"`), imported lazily inside
  the adapter module so the core never requires them.
- Scenario values must be distinctive strings (the validator warns).
- Live-service tests must skip cleanly without credentials
  (`pytest.mark.skipif` on the env var) so CI stays green.
- Small, focused commits. Don't refactor core modules while adding adapters.

## Handoff protocol

After completing any task, update `HANDOFF.md` in the repo root **before the
final commit**. Append a dated entry containing:

1. **What shipped** — with commit refs.
2. **Findings** — especially real provider behaviour observed: what passed,
   what failed, exact metrics from the actual run. Numbers, not impressions.
3. **Decisions** — and why.
4. **FOR STRATEGY** — open questions needing founder/advisor input. Flag them
   explicitly; do not silently resolve a question that is not yours to close.
5. **Next** — the next task per the roadmap.

Entries stay factual and terse. **`HANDOFF.md` is public**: never include
customer names, prospect details, credentials, account identifiers, or
anything commercially sensitive. Provider behaviour measured from a public
API is a benchmark finding and belongs here; who is evaluating it does not.

## Commands

```bash
pip install -e ".[dev]"
pytest -q                                             # must be 100% green
memorycheck validate scenarios
memorycheck run scenarios --adapter reference:naive   # demo: gate FAILs
memorycheck run scenarios --adapter reference:strict  # demo: gate PASSes
```

## Roadmap (in order — do not skip ahead)

1. ~~**Mem0 adapter**~~ — done (`adapters/mem0.py`, spec `mem0`, extra
   `[mem0]`). Store-only, so the adapter supplies a deterministic answering
   layer over `search`; writes are verbatim (`infer=False`) so the judge can
   match exact values; `supports_ttl = False` (Mem0 expiry is wall-clock,
   ours is logical) so expiry reports NOT_TESTED.
2. Scenario pack growth: 5 → ~15 (delete/re-add, double correction,
   multi-key interference, cross-tenant suites).
3. Zep adapter, then LangGraph store adapter.
4. LLM judge — only after the calibration protocol has been run.

## Out of scope — do not build

Dashboards or hosted control planes; storing customer memory content;
automatic remediation; "certification" language anywhere; frontier-model
anything. This stays a local-first CLI + CI gate.
