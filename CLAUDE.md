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
  Mem0), `zep.py` (hosted Zep, unverified live), `NullAdapter` (no-memory
  baseline).
- `doctor.py` — shim conformance check. Runs before any scenario: endpoint
  contract, response shape, scope isolation round-trip, deletion actually
  removing, and write→read convergence to size timeouts from the customer's
  stack. Every failure carries its exact fix.
- `cli.py` — `doctor` / `validate` / `run` / `list-adapters`.
- `examples/shim/` — the pilot delivery path: FastAPI, Flask and LangGraph
  templates plus the integration guide.

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
9. **Harness fixes vs provider verdicts.** A false FAIL discredits the gate as
   badly as a false PASS, so fixing a harness defect that manufactures a false
   FAIL proceeds without sign-off — but must be documented in `HANDOFF.md`
   with the measurement that proves it was ours (ratified 2026-07-27, re
   `27e9599`). **Any change that would flip a provider FAIL to PASS requires
   founder sign-off before merge**, no exceptions. If a fix would do both, it
   needs sign-off. The test of good faith is that a genuine finding survives
   the fix unchanged.
10. **Never judge a provider on a race.** Any adapter reading after a write,
    delete or correction must poll for the expected state transition with a
    timeout, never sleep a fixed interval. If the expected state never
    arrives, that is a reportable finding with its own latency number — not
    a silent zero. Three near-miss false findings (`27e9599` reset race, Zep
    exception swallowing, ~15s extraction latency) all came from this class.
    Assume every provider is eventually consistent until measured otherwise.

    Note the boundary: adapters confirm **their own writes and deletes** have
    landed. They must never poll a *query* until a value the ledger expects
    shows up — that would launder a genuine `missing_current_fact` into a
    pass. Converge the store, then read once and report what comes back.

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

**Internal vs external, and what may proceed unblocked** (ruling 2026-07-27):

- **Internal code decisions may proceed**, flagged FOR STRATEGY in the same
  entry. Do not stall implementation waiting on a ruling.
- **External actions must wait for a ruling first** — publishing, naming a
  vendor in public material, opening issues or contacting a provider,
  announcements. Draft them into `HANDOFF.md` under FOR STRATEGY and stop
  there. Drafting is not sending.

The asymmetry is deliberate: an internal decision can be reverted in a commit,
an external one cannot be unsent.

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
2. ~~Scenario pack growth: 5 → ~15~~ — done. 15 scenarios; added delete/re-add,
   double correction, re-add-then-correct, multi-key interference, TTL with a
   non-expiring sibling, rescope-then-re-add, and cross-tenant suites
   (shared `user_id` across tenants, cross-tenant rescope).
3. ~~**LangGraph store adapter**~~ — done (`adapters/langgraph.py`, spec
   `langgraph[:memory|:sqlite[:path]]`, extra `[langgraph]`). Preflight run
   first; clean PASS on both backends, seed-stable. `supports_ttl = False`
   from observation. First adapter to clear `unverified`.
4. **LLM judge calibration — moved ahead of further adapters**
   (ruling 2026-07-28). It is now the constraint on what the product can
   claim, not a later refinement: with a paraphrasing answering layer,
   `missing_current_fact` is NOT_TESTED, the utility delta is unavailable,
   and a clean P1 is weaker evidence — so a customer running their real
   agent gets a materially narrower evidence pack. More adapters do not
   widen that; the judge does.

   **The 200 labelled examples do not require customer data.** Paraphrased
   variants of the existing pack's answers, labelled by hand, are a valid
   calibration set and can be built independently. Sketch the protocol
   before starting.
5. Zep — code landed but **not measurable by the current instrument**;
   mechanism is selective silent extraction, no unblock date. Do not resume
   without a founder decision. Not a judge-calibration problem: a judge
   classifies answers, and nothing is materialised to classify.
6. Further adapters, after the judge.


Every new adapter runs the preflight first and keeps `unverified = True`
until every item on it has an observed answer.

Pack size is frozen at 15 (ruling 2026-07-27). If a pilot needs per-commit
speed, split into tiers — smoke (~5) per-commit, full pack nightly/release —
rather than shrinking the pack.

## Out of scope — do not build

Dashboards or hosted control planes; storing customer memory content;
automatic remediation; "certification" language anywhere; frontier-model
anything. This stays a local-first CLI + CI gate.
