# CLAUDE.md — memorycheck

## What this is

A behavioural release gate for agent memory. It runs lifecycle scenarios
(write → correct → rescope → expire → delete) against a memory stack and
proves whether each operation changed the agent's *behaviour* — not whether
the store returned 200. Provider-neutral, local-first, sold on trust:
the honesty model below is the product's differentiator, not decoration.

Status: v0.1, built in public. `pytest -q` must stay green after every change.

## Architecture (src/memorycheck/)

- `ledger.py` — deterministic ground-truth state machine. Owns what is
  CURRENT / SUPERSEDED / EXPIRED / DELETED / foreign at every step. Logical
  time only moves on explicit `advance_time`.
- `scenario.py` — YAML DSL loader/validator. Warns on non-distinctive values.
- `judge.py` — usage classifier. v0 is deterministic (normalised containment).
- `runner.py` — executes steps against an adapter in lockstep with the ledger.
  Replays rescopes from ledger ground truth (adapters need no read API).
- `oracle.py` — findings + severities. Invariants enforced on EVERY query.
- `report.py` — metrics, scorecard, JSON/MD evidence, and
  PASS/FAIL/INCONCLUSIVE gate verdict/exit code.
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
   TTL) or a check with zero opportunities reports NOT_TESTED. An unverified
   adapter, a paraphrasing layer whose clean absences cannot be evidenced, or
   a run where every metric is NOT_TESTED yields INCONCLUSIVE and exits
   non-zero. A broken, partial or unmeasurable adapter must never produce PASS.
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
    landed, then the runner performs a separate scored query. Native adapters
    use their raw store read surface. The HTTP pilot has no read API beyond
    the customer shim's `/query`, so it polls that endpoint for the exact
    just-mutated value (or its absence) with a bounded timeout. That poll is
    mutation confirmation only; the oracle never grades it. Non-convergence
    aborts rather than being laundered into PASS or scored as a provider bug.
11. **Live-run provenance is a publication gate.** From adoption of this rule,
    every live-provider run
    records four facts contemporaneously: who executed it, the execution
    environment, the provider's SEARCH quota immediately before and after,
    and the results filename. Missing any field makes the run **reported,
    unverified**. It may be logged internally, but it must not update README,
    `examples/report_*`, clear an adapter's `unverified` flag, or support any
    other gated claim. Estimates and reconstructed quota arithmetic are useful
    diagnostics, never substitutes for the before/after pair. The 2026-08-05
    Mem0 evidence is a documented legacy exception: it was verified
    retrospectively by the founder's independent live counter reading of 599
    against approximately 885 before the work. Its provenance was not
    self-recorded at execution time — the reason this prospective rule exists.

## Conventions

- Python ≥3.10, stdlib-first. Core deps: PyYAML only. Per-adapter SDKs go in
  optional extras (e.g. `pip install -e ".[mem0]"`), imported lazily inside
  the adapter module so the core never requires them.
- Scenario values must be distinctive strings (the validator warns).
- Live-service tests must skip cleanly without credentials
  (`pytest.mark.skipif` on the env var) so CI stays green.
- Small, focused commits. Don't refactor core modules while adding adapters.

## Environment notes

**Default sandbox networking does not reach live providers.** A normal command
could not resolve `api.mem0.ai` on 2026-08-05. An explicitly approved escalated
run did reach it and completed the Mem0 diagnostic plus full 15 × 2 suite.
Treat live access as approval-dependent, not generally available, and verify
both credentials and egress before planning a metered run. Assume the same
applies to Zep and future hosted adapters until measured otherwise.

**A TCP probe is not a valid reachability check.** Opening a socket to
`api.mem0.ai:443` *succeeds* inside the sandbox because it connects to the
local proxy, not to Mem0 — so the naive check reports reachable for a host
that is entirely blocked. Check the proxy instead:

```bash
curl -sS "$HTTPS_PROXY/__agentproxy/status"     # see recentRelayFailures
```

Confirm reachability before planning a metered run, not after spending quota
on one that cannot connect.

## Handoff protocol

**Start every session by reconciling the log against the repo.** Before doing
anything else — before reading the roadmap, before touching code — run:

```bash
git log --oneline -5
git status
```

and compare them against the last `HANDOFF.md` entry. They must agree: the
commits the entry claims are the commits that exist, and the tree is clean.
**If they disagree, say so before proceeding** — an uncommitted change, a
commit the log does not mention, or an entry citing a commit that is not there
all mean the previous session ended in an unknown state, and the first task is
establishing what actually happened, not continuing on top of it.

After completing any task, update `HANDOFF.md` in the repo root **before the
final commit**. Append a dated entry containing:

1. **What shipped** — with commit refs.
2. **Findings** — especially real provider behaviour observed: what passed,
   what failed, exact metrics from the actual run. Numbers, not impressions.
3. **Decisions** — and why.
4. **FOR STRATEGY** — open questions needing founder/advisor input. Flag them
   explicitly; do not silently resolve a question that is not yours to close.
5. **Next** — the next task per the roadmap.

**A task is not complete until its `HANDOFF.md` entry is written, committed,
pushed, and _merged to `main`_.** All four, in that order. A local commit is
invisible to review; so is a pushed branch nobody merges — both are
indistinguishable from work never done, and the second is the one that
actually bit us (2026-08-03: a session reported "done" on work that sat
unmerged on a branch). So "done" means **on `main`**, and a session that
stops short has not finished its task and must say which of the four steps it
stopped at. Blocked work still gets an entry: the blocker, what was
established anyway, and what remains unverified.

**Every live-provider run after adoption of invariant 11, here or elsewhere,
must be promoted into
`HANDOFF.md` with its complete invariant-11 provenance block:** executor,
environment, SEARCH quota before, SEARCH quota after, and results filename.
If one is absent, label it **reported, unverified** and do not change a gated
file from it. `diagnostics/results/` is
gitignored by design — those are open investigations whose output is not
publishable until ruled on. The cost of that choice is that a run done on a
laptop leaves no commit, no artefact and no trace here, so it is invisible to
every future session. Write the numbers into the entry and cite the file
(e.g. `readd_after_delete_1785797173.json`) so the raw output can be found
again on the machine holding it. Say plainly when figures are reported rather
than observed in-session — a promoted result is second-hand, and the log
should not read as though the session measured it. **An unlogged run did not
happen**, as far as the next session can tell.

### Merge authority (ruling 2026-08-03)

Two tiers. Which tier a PR is in depends on **what it touches**, not on how
large it is.

**Self-merge — no gate.** Source code, tests, and internal docs: `src/`,
`tests/`, `CLAUDE.md`, `HANDOFF.md`, `ADAPTER_PREFLIGHT.md`, `diagnostics/`,
`examples/shim/*`. Land these; do not wait on anyone.

**Founder approval required before merge, whatever the PR size:**

- `README.md`
- `examples/report_*.md` / `examples/report_*.json` (published evidence)
- any change to a published figure, claim, or provider framing
- clearing `unverified` on any adapter
- anything that would flip a provider FAIL to PASS (invariant 9, unchanged)

Mechanism: label the PR **`needs-founder-review`**, state in the body
**exactly which public claim changes and why**, and **do not merge until the
founder comments approval**. Record that approval in the `HANDOFF.md` entry.
For these PRs the completion rule above pauses at "pushed": an unmerged branch
awaiting a ruling is the **correct** state, not an unfinished task, and the
entry should say so rather than merging to satisfy the rule.

Be honest about what this is: **it is not independent review** — the founder
is the only human here, and a gate with one participant cannot catch what that
participant misses. It is a deliberate look before anything public changes.
Two near-misses on published claims justify that friction; nothing in the
internal tier does, which is why the internal tier has no gate at all.

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
   exact matched violations still FAIL, but clean lifecycle absence checks
   and `missing_current_fact` are NOT_TESTED, the utility delta is unavailable,
   and the gate is INCONCLUSIVE — so a customer running their real agent gets
   a materially narrower evidence pack. More adapters do not widen that; the
   judge does.

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
