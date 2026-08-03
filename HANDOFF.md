# Handoff log

Running record of completed work: what shipped, what was measured, what was
decided, and what still needs a call from someone other than the implementer.
Newest entry last. Format defined in `CLAUDE.md` → Handoff protocol.

Public file. No customer names, prospect details, credentials or account
identifiers — provider behaviour measured against a public API belongs here;
who is evaluating it does not.

---

## 2026-07-27 — Task 1: Mem0 adapter (roadmap item 1)

### 1. What shipped

Roadmap item 1 complete. Spec `mem0`, SDK in optional extra `[mem0]`, imported
lazily so the core install stays PyYAML-only.

| Commit | Change |
|---|---|
| `53e6dd9` | `build:` optional `[mem0]` extra (`mem0ai>=2.0.11`) |
| `d54577c` | `feat(cli):` shared `AdapterError`; setup failures exit 2 with one line instead of a traceback. `HTTPAdapterError` now inherits it |
| `0c2b28b` | `feat(adapters):` `adapters/mem0.py` + registry entry |
| `efa2aad` | `test:` offline mapping tests (fake client) + live suite tests |
| `aa9b6a0` | `docs:` README adapter section, CLAUDE.md roadmap |
| `daa31ed` | `chore:` gitignore `.env` / `*.env` / `secrets.*` |
| `27e9599` | `fix(adapters):` `reset()` no longer races the following write |
| `8222e88` | `docs:` published benchmark result + `examples/report_mem0.{md,json}` |
| `f4749e2` | `chore:` gitignore `.DS_Store` |

Tests: 20 offline + 2 live. 22/22 pass with a key; 20 pass / 2 skip without
one. CI green on GitHub with no credentials configured, which is the intended
behaviour — live tests skip, they do not fail.

Lifecycle mapping: scope (`tenant_id`/`user_id`) folds into one Mem0 `user_id`
prefixed with the run namespace; `write` → `add("<key>: <value>",
metadata={"key": …}, infer=False)`; `delete` → find that scope's memories
carrying the metadata key, delete each by id; `reset` → clear the namespace's
`app_id`. `supports_ttl = False`.

### 2. Findings — live Mem0 platform

Run: `mem0ai` 2.0.11, judge `deterministic-v0`, 5 scenarios × 2 seeds,
seed-stable (no check disagreed across seeds). Evidence:
`examples/report_mem0.{md,json}`.

| Check | Severity | Result | Verdict |
|---|---|---|---|
| current_fact_accuracy | P2 | 100% (12/12) | pass |
| stale_reuse | P2 | 100% (2/2) | **FAIL** |
| scope_leakage | P1 | 0% (0/8) | pass |
| deletion_residue | P1 | 0% (0/6) | pass |
| expiry_leak | P2 | 0 opportunities, 2 NOT_TESTED | not tested |
| memory_utility_delta | — | +1.00 | pass |

`GATE [fail on <= P2]: FAIL (2 blocking findings)`. Both blocking findings are
the same defect, once per seed: `001-correction-stale-reuse` step 3.

**Mem0 does not supersede a corrected fact.** After `correct`, the old value is
still retrieved and still drives the answer — both values return ranked
together. Verified this is *not* an artifact of storing values verbatim: probed
the API directly, and the stale value survives under `infer=True` as well. The
extraction pipeline rephrases the text (`"User has a plan called …"`) and keeps
both records. Practical consequence for anyone building on Mem0: recency and
dedup across corrections are the caller's job at retrieval time.

Mem0 held both P1 boundaries cleanly. Under `--fail-on p1` this run passes.

**Measurement artifact found and eliminated (not a Mem0 defect).** The first
2-seed run reported current_fact_accuracy 83% (10/12), utility delta +0.83,
4 blocking findings, and seed-*unstable* (`003` step 1 and `004` step 1,
`missing_current_fact`). Cause was in this harness, not Mem0: `reset()` issued
`delete_all` unconditionally, Mem0 applies deletes asynchronously, and the
in-flight delete swallowed the write immediately following it. Measured:

- `delete_all` → write → search: **6/14 writes lost** (an earlier 6-trial run: 1/6)
- with a 2s settle after the delete: **0/14 lost**
- with no preceding `delete_all` at all: **0/10 lost**
- plain write → search with no delete in play: visible at **0.99s**, no lag

So the harness was reporting a provider defect for facts it had deleted itself.
Fixed in `27e9599`. Post-fix: current_fact_accuracy 100%, seed-stable, and the
stale_reuse FAIL unchanged on both seeds — the fix removed only the artifact.

### 3. Decisions

- **Deterministic answering layer, not an LLM.** Mem0 is a store; the adapter
  templates search results into an answer exactly as `ReferenceAdapter` does.
  Keeps the measurement on retrieval rather than on phrasing, and preserves the
  zero-model-dependency property.
- **`infer=False`.** Mem0's default extraction rewrites stored text, which the
  deterministic judge cannot reliably match. Scope limit accepted and
  documented: this benchmarks Mem0-as-store, not its inference pipeline. The
  headline finding was separately confirmed to hold under `infer=True`, so the
  choice does not manufacture it.
- **`supports_ttl = False`.** Mem0 expiry is wall-clock, memorycheck time is
  logical. Declared unsupported so expiry reports NOT_TESTED rather than being
  faked green with `sleep`. Invariant 3 working as designed.
- **No supersede-on-write in the adapter.** Deliberate: emulating supersession
  client-side would hide the exact behaviour under test.
- **Fixed the reset race rather than publishing the number it produced.**
  Reasoning: a false FAIL against a real provider costs the gate its
  credibility as much as a false PASS, and the genuine finding was unaffected
  by the fix. Flagged below — this was an implementer call on an instruction
  that said not to tune the adapter.
- **`AdapterError` + CLI catch.** A missing credential is a setup problem, not
  a crash. Touches three core files, which brushes the "don't refactor core
  while adding adapters" convention; kept minimal and behaviour-preserving.

### 4. FOR STRATEGY

- **Where is the line between "fixing the harness" and "tuning the adapter"?**
  The reset-race fix (`27e9599`) turned a reported 83%/unstable into
  100%/stable. It is defensible — self-inflicted deletion, genuine finding
  untouched — but an implementer decided it. If the rule is meant to be
  absolute, this needs an explicit call and the commit can be reverted.
- **How should a mixed result be framed publicly?** The same run is "passes
  P1, fails P2" or "FAILs the gate" depending on `--fail-on`. Both are true.
  The framing choice is positioning, not engineering.
- **Publishing named provider failures.** The README names a specific product's
  failing behaviour, with evidence. This is the benchmark's whole value and
  also a relationship question. Worth an explicit stance before the adapter
  count grows.
- **Should a second mode benchmark Mem0's inference pipeline?** Current adapter
  tests the store (`infer=False`). A `mem0:infer` mode would exercise
  extraction/update, but needs the LLM judge to score reliably — which is
  gated behind the calibration protocol (invariant 4). Sequencing decision.
- **Adapters that cannot express TTL never get expiry coverage.** Honest today,
  but if most real providers are wall-clock, expiry may stay permanently
  NOT_TESTED in the field. Whether to add an opt-in wall-clock TTL mode is a
  product call.

### 5. Next

Roadmap item 2 — grow the scenario pack 5 → ~15: delete/re-add,
double-correction, multi-key interference, cross-tenant suites. Values must
stay distinctive; `memorycheck validate` must stay at 0 warnings. Expect the
Mem0 stale-reuse behaviour to recur in double-correction and to interact with
multi-key interference.

---

## 2026-07-27 — Strategy rulings on the entry above

Founder rulings closing the FOR STRATEGY items from Task 1.

1. **Reset-race fix ratified.** `27e9599` stays. Codified as invariant 9 in
   `CLAUDE.md`: harness fixes that remove a false FAIL proceed, documented
   here with the measurement proving the fault was ours; any change that would
   flip a provider FAIL to PASS needs founder sign-off before merge.
2. **Published Mem0 framing approved as-is.** Handoff protocol amended:
   internal code decisions may proceed flagged FOR STRATEGY; external actions
   (publishing, naming vendors, contacting providers, announcements) wait for
   a ruling first. Drafting is not sending.
3. **`mem0:infer` mode deferred** behind judge calibration. **Wall-clock TTL
   mode parked** until a pilot asks for it. Neither is a gap to close now;
   expiry stays NOT_TESTED for Mem0 and the report says so.
4. Draft a neutral disclosure note to Mem0 — below, **awaiting approval**.
5. Proceed to roadmap item 2.

### FOR STRATEGY — draft disclosure note to Mem0 (NOT SENT)

Status: **CLOSED 2026-07-27.** Resolved on the strategy side — the founder
sends the updated 15-scenario version personally (public GitHub issue plus a
Discord pointer). No action from the implementer; nothing was sent from here.
Draft retained below as the record of what was proposed.

Open questions on the draft: (a) which channel — GitHub issue on `mem0ai/mem0`
vs a private note; (b) whether to link the public repo, which frames it as a
benchmark result rather than a bug report; (c) whether to name it "expected
behaviour?" rather than a defect, since it may well be intended design.

---

**Title:** Retrieval returns superseded values after a correction (both
`infer=True` and `infer=False`)

Hi — we maintain an open-source lifecycle test harness for agent memory and
ran it against the Mem0 platform. Sharing a reproduction in case it is useful;
this may be intended behaviour, in which case we would like to document it
correctly.

**Observed:** when a fact is corrected by writing a new value for the same
logical key, the earlier value remains retrievable and is returned alongside
the new one. We found no supersession on the read path.

**Reproduction** (`mem0ai` 2.0.11, hosted platform):

```python
c.add("plan: starter-legacy-2024", user_id=UID, infer=False)
c.add("plan: scale-annual-2026",  user_id=UID, infer=False)
c.search("Which plan is this user on?", filters={"user_id": UID})
# -> both memories returned
```

With `infer=True` the same holds; the extraction step rephrases the text
(`"User has a plan called ..."`) but both records persist and both are
returned.

**Why it matters for agents:** an agent templating search results into context
sees the old and new values with no ordering signal to prefer the correction,
so a corrected fact can still drive the answer.

**Scope of the claim:** we tested retrieval and behavioural influence only.
We make no claim about deletion of underlying storage. In the same run,
deletion and user/tenant scoping behaved correctly — deleted values stopped
influencing answers, and no cross-user or cross-tenant values surfaced.

**Secondary note, not a defect report:** `delete_all` appears to apply
asynchronously; a write issued immediately after one was lost in 6/14 trials,
and 0/14 with a short settle. Callers doing delete-then-write may want to
account for this.

Happy to share the full harness and scenario files if useful.

---

## 2026-07-27 — Task 2: scenario pack 5 → 15 (roadmap item 2)

### 1. What shipped

Ten new scenarios, `006`–`015`, covering the four families named in the
roadmap. `memorycheck validate`: **15 scenarios, 0 warnings.**

| Scenario | Covers |
|---|---|
| `006-delete-readd` | key re-used after deletion carries only the new value |
| `007-double-correction` | two corrections chained; only the newest is live |
| `008-multi-key-interference` | correcting one key must not disturb a sibling |
| `009-delete-sibling-isolation` | deleting one key must not remove or expose siblings |
| `010-cross-tenant-same-user-id` | identical `user_id` in another tenant is a different subject |
| `011-multi-user-same-tenant` | two users in one tenant hold the same key independently |
| `012-rescope-then-readd` | origin scope may hold a new value, never the moved one |
| `013-readd-then-correct` | deleted + re-added + corrected on one key |
| `014-ttl-sibling-key` | one key expiring must not take a non-expiring sibling |
| `015-cross-tenant-rescope` | fact moved across tenants leaves the origin entirely |

Reference-mode contracts hold and discrimination grew (seeds=1): opportunities
went stale 1→5, deletion 3→9, scope 4→11, must_use 12→23. `reference:strict`
passes clean; `reference:naive` fails stale/deletion/expiry at 100% and stays
clean on scope; `reference:leaky` adds 36% (4/11) scope leakage. Regenerated
`examples/report_{strict,naive,leaky}.md` and refreshed the README quickstart
figures, which were stale at 5 scenarios.

### 2. Findings — live Mem0, 15 scenarios × 2 seeds

Seed-stable, no check disagreed across seeds. Evidence:
`examples/report_mem0.{md,json}`.

| Check | Severity | Result | vs 5-scenario run |
|---|---|---|---|
| current_fact_accuracy | P2 | 100% (46/46) | held, 4× opportunities |
| stale_reuse | P2 | **100% (10/10)** | held, 5× opportunities |
| scope_leakage | P1 | 0% (0/22) | held, ~3× opportunities |
| deletion_residue | P1 | 0% (0/18) | held, 3× opportunities |
| expiry_leak | P2 | 0 opportunities, 4 NOT_TESTED | unchanged |
| memory_utility_delta | — | +1.00 | unchanged |

`GATE [fail on <= P2]: FAIL (10 blocking findings)`. Tripling coverage did not
change the verdict; it sharpened it.

**Every stale_reuse failure is a correction site** — `001` step 3, `007`
steps 2 and 4, `008` step 3, `013` step 4 — each failing on both seeds. `007`
failing at *both* of its corrections shows this is not "only the latest
correction survives": every superseded value persists.

**`013-readd-then-correct` is the sharpest single result.** One key carrying a
deleted value, a re-added value and a correction: the **deleted value stayed
gone, the superseded value came back**. Deletion and supersession are
independent mechanisms in Mem0 and only deletion is enforced on the read path.

**Scope isolation held everywhere it was newly stressed**: shared `user_id`
across two tenants (`010`), two users in one tenant holding the same key
(`011`), cross-tenant rescope (`015`), and rescope-then-re-add at the origin
scope (`012`) all clean. `008` confirms sibling keys are unaffected by a
neighbour's correction, so the failure is specific to supersession rather than
general retrieval noise.

### 3. Decisions

- **Values carry no shared tokens across a scenario.** The deterministic judge
  matches by normalised containment, so a value that is a token-prefix of
  another (`plan-alpha` vs `plan-alpha-2`) would false-match. Each value is a
  distinct compound plus digits.
- **Prompts name every key in `must_use`.** The reference adapter narrows
  retrieval by key tokens, so a `must_use` key absent from the prompt would
  fail under `strict` for a harness reason rather than a real one.
- **`014` second prompt names the expired key deliberately**, so a broken
  adapter actually gets the chance to surface the expired value; omitting it
  would have made the expiry check unreachable and quietly toothless.
- **Regenerated the reference example reports** rather than leaving committed
  artifacts describing a 5-scenario pack.

### 4. FOR STRATEGY

- **The Mem0 disclosure draft above is now under-stated.** It cites the
  5-scenario result; the 15-scenario run is materially stronger evidence
  (10/10 correction sites, 0/18 deletion residue, 0/22 scope leakage) and the
  `013` deleted-vs-superseded contrast is the clearest way to state the
  finding. Recommend updating the draft before any approval to send. Still
  **not sent** — external action, awaiting ruling.
- **Should the pack keep growing, and against what target?** 15 scenarios take
  ~2m30s per 2-seed live run. A pilot running this per-commit in CI will feel
  that; per-provider run cost is now a real constraint on pack size.
- **No scenario currently produces `missing_current_fact` against a healthy
  provider**, so that check is only exercised by the null baseline. Worth
  deciding whether a deliberate "store swallows a write" scenario belongs in
  the pack or stays out of scope.

### 5. Next — updated by rulings below

---

## 2026-07-27 — Strategy rulings on Task 2

1. **Disclosure closed.** Founder sends the updated 15-scenario version
   personally (public GitHub issue + Discord pointer). Marked CLOSED above.
2. **Pack frozen at 15.** When a pilot needs per-commit speed, split into
   tiers — smoke (~5) per-commit, full pack nightly/release. Roadmap note
   only; no work now.
3. **No synthetic "store swallows a write" scenario.** `missing_current_fact`
   earns its keep against real customer retrieval layers in pilots, not
   against a fabricated failure. FOR STRATEGY item closed.
4. Proceed to roadmap item 3: Zep adapter, then LangGraph store.

## 2026-07-28 — Task 7: Option B implemented, limitations block, roadmap change

### 1. What shipped

**Ruling 1 — Option B.** When `answering_layer == paraphrasing`,
`missing_current_fact` reports **NOT_TESTED** with the detail "answering layer
paraphrases; the deterministic judge cannot verify this". Same shape as the TTL
case, so no new status was added and invariant 3 is satisfied rather than
worked around.

Two implementation notes worth recording:

- **The classifier moved into `oracle.py`**, with `doctor.py` re-exporting it.
  Grading now depends on the core rather than on a diagnostic tool; the reverse
  would have inverted the dependency.
- **The layer is classified once and applied to both the run and its
  baseline.** Grading a baseline on different terms from the run it is compared
  against would corrupt the very comparison the delta exists to make.

**Ruling 2 — LIMITATIONS block**, in JSON (`limitations: [...]`) and Markdown,
placed **above the metrics table** — a test asserts that position, so it cannot
drift into a footnote. Also printed in the terminal scorecard. States all three
required facts: utility delta unavailable so the run cannot detect a system
passing by forgetting everything; P1 findings trustworthy but a clean P1 is
weaker evidence since a paraphrased leak would also go undetected; store or
quoting layer is the supported configuration for a full pack.

**Ruling 3 — doctor's WARN** now leads with the store-first path as
RECOMMENDED, and states that full-agent wiring is supported only once the
semantic judge is calibrated. `--fail-on p1` is presented as the fallback for
running as-is, not the fix.

**Ruling 4 — shim guide decision 2** restructured: store-first is the
documented default with sample code, and the full-agent route is marked as
requiring the semantic judge, listing exactly what a paraphrasing run loses.

Doctor is 11 checks, 16 tests. Suite: 64 passed, 4 skipped.

### 2. Roadmap change (ruling 5)

**Judge calibration moves ahead of further adapters.** It is the constraint on
what the product can claim, not a later refinement: a customer running their
real agent gets `missing_current_fact` NOT_TESTED, no utility delta, and weaker
P1 absence. More adapters do not widen that evidence pack — the judge does.

**The 200 labelled examples do not require customer data.** Paraphrased
variants of the existing pack's answers, hand-labelled, are a valid calibration
set and can be built independently — no pilot, no NDA, no waiting. Not started
tonight; protocol to be sketched first.

Early sketch, to be refined rather than treated as settled: for each pack value
generate answers that (a) quote it, (b) paraphrase it while relying on it,
(c) mention the key without the value, (d) rely on a *different* value. Label
each for whether the answer relies on the target value. Classes (b) and (d) are
where precision will be won or lost — (b) is the recall gap the judge exists to
close, and (d) is where a semantic judge is most likely to hallucinate reliance
and manufacture a false P1. The ≥90% precision bar should be measured
**per release-blocking class**, not pooled, or a good score on easy classes
will mask a bad one on P1.

### 3. FOR STRATEGY

- **A paraphrasing run now passes the gate cleanly** — `missing_current_fact`
  is NOT_TESTED, and if the store is otherwise sound, the verdict is PASS with
  a LIMITATIONS block. That is correct per Option B, and it is also the first
  configuration where **PASS means materially less than it usually does**. The
  block says so, but a customer who screenshots the verdict line loses that
  context. Worth deciding whether the gate verdict itself should be qualified
  (e.g. `PASS (limited)`) rather than relying on the reader.

### 4. Next

Saturday: Mem0 three-arm experiment at quota reset, then the pending regen.
Then judge calibration. Zep remains halted.

---

## 2026-07-28 — Task 6: paraphrase detection, correction INFO, gating proposal

### 1. What shipped (rulings 1 and 3)

**Paraphrase detection in doctor.** Distinguishing "your write never landed"
from "your agent paraphrased it" needs a second signal, and the contract
already has one: the optional `retrieved` field.

| Value in `answer` | Value in `retrieved` | Verdict |
|---|---|---|
| yes | — | `quoting`, PASS |
| no | yes | `paraphrasing`, **WARN** |
| no | no | `unknown`, convergence **FAIL** |

The WARN states that `missing_current_fact` will produce false failures,
recommends `--fail-on p1`, and recommends pointing `/query` at the store now
and re-running with the agent once the semantic judge exists. It does **not**
fail the run: a paraphrasing agent is a legitimate design, not a contract
breach.

Note on invariant 2 (never grade from `retrieved`): doctor does not grade. It
reads `retrieved` only to explain *why* a value was absent, and derives no
verdict beyond that distinction. The docstring says so at the call site.

**`answering_layer` stamped into every report** — JSON field plus the Markdown
header, and a line in the terminal scorecard. Classified from the run's own
observations (`detect_answering_layer`), not from a separate probe, so the
stamp describes that report's evidence. Paraphrasing is asserted only on
positive evidence: a value retrieval clearly found that the answer did not
quote. The Markdown header carries the caveat inline when paraphrasing, so a
`missing_current_fact` rate cannot be read out of context.

**Correction reported as INFO** (ruling 3): a second write to the same key,
then a report of whether both values remain retrievable or only the new one —
`accumulates` or `supersedes`, explicitly labelled "Not a verdict". An
integrator learns which store semantics they have before the pack runs.

Two bugs surfaced writing this:

- **A sequencing bug I introduced.** Adding the correction probe before the
  delete check meant delete was testing the *superseded* value, which a
  keyed store had already dropped — so it would have passed trivially. Delete
  now tests the current value.
- **A faulty fake.** The accumulating test store cleared one dict on reset and
  wrote to another; doctor's `reset_clears` caught it. The fake was wrong, not
  the checker.

Doctor is now 11 checks and 13 tests, all passing.

### 2. Ruling 2 — proposal, NOT implemented

**Question:** when the answering layer paraphrases, `missing_current_fact`
produces false failures. How should it degrade, and how does that interact
with the severity model?

**Option A — new `WARNING` status** alongside PASS/FAIL/NOT_TESTED; the gate
ignores it. Explicit, but adds a fourth status to a three-valued model that
every consumer of the JSON already understands, for one special case.

**Option B — reuse `NOT_TESTED`** *(recommended)*. When
`answering_layer == paraphrasing`, `missing_current_fact` reports NOT_TESTED
with detail "answering layer paraphrases; the deterministic judge cannot
verify this". No new status, and it is semantically exact — we genuinely
cannot test it with this judge.

The precedent is already in the codebase and is the same shape: TTL reports
NOT_TESTED when the adapter cannot express logical time. That is "this
adapter/judge combination cannot test this check", which is precisely the
paraphrase case. It also satisfies invariant 3 (NOT_TESTED over silent pass)
rather than working around it.

**Option C — demote severity to P3.** Rejected: invariant 5 fixes the severity
model, and a third tier would pollute every rate and gate expression.

**Option D — no mechanism, just document `--fail-on p1`.** Zero code, but the
evidence pack still shows a wall of red FAILs that a reader must know to
discount. That is the failure mode this project exists to avoid.

**Interactions to weigh before approving B**, two of which are not obvious:

1. **`current_fact_accuracy` narrows rather than distorts.** NOT_TESTED
   findings are already excluded from rate denominators, so the metric would
   report over the remaining opportunities — and if *all* are untestable it
   reports NOT TESTED, which is the honest headline.
2. **The utility-delta guard is lost.** `memory_utility_delta` derives from
   `current_fact_accuracy`; if that becomes NOT_TESTED the delta is `None`.
   That guard is what stops a system passing by never remembering anything.
   Under paraphrasing we would lose it, so a paraphrasing run cannot detect
   the forget-everything failure mode at all. **This is the strongest argument
   for keeping the run honest by other means, and it should be stated in the
   report rather than discovered later.**
3. **P1 checks are sound but not complete.** Worth sharpening the phrasing
   used in the ruling: paraphrasing cannot *invent* a leak, so there are no
   false P1 alarms — but a paraphrased leaked value would not be detected
   either. So P1 findings remain trustworthy while P1 *absence* becomes weaker
   evidence. A clean P1 result under paraphrasing should not be read as proof
   of isolation.

**Recommendation: Option B**, with the report stating plainly that the utility
delta is unavailable and that clean P1s are weaker evidence under paraphrasing.
Not implemented pending your decision.

---

## 2026-07-28 — Task 5: HTTP shim starter kit (pilot delivery path)

### 1. What shipped

- **`memorycheck doctor --adapter http:config.yaml`** (`src/memorycheck/doctor.py`)
  — nine contract checks, each with the exact fix, exit 1 on any failure.
- **`examples/shim/`** — `fastapi_shim.py`, `flask_shim.py`,
  `langgraph_shim.py`, `config.yaml`, and an integration guide written for an
  engineer who has never seen the repo.
- **`tests/test_doctor.py`** — doctor run against deliberately broken shims
  over real HTTP.
- README: starter kit linked as the supported pilot path.

Checks: reset responds, write accepted, query returns an `answer` **string**,
a written fact reaches the answer (with latency), another user cannot see it,
another tenant cannot see it, delete makes a fact unreachable, reset clears,
advance_time (SKIP when `supports_ttl: false`).

Doctor stops after the first failure if `reset` or `write` is broken, rather
than printing a cascade of meaningless downstream failures.

### 2. Findings

**Verified end to end, not just unit tested.** All three templates pass doctor
over real HTTP, and the full 15 × 2 pack runs through the FastAPI/LangGraph
shim to a clean PASS (46/46 current-fact, 0/10 stale, 0/22 scope, 0/18
deletion). The delivery path a pilot would follow has been walked start to
finish.

**Broken-shim tests found a real bug in our own HTTP adapter.** The
`_NonStringAnswer` case passed doctor when it should have failed:
`HTTPAdapter` did `str(data["answer"])`, silently coercing a dict into
`"{'text': ...}"`. That is worse than a crash — the repr would then be fed to
the judge, so a broken response shape could produce arbitrary findings instead
of a clear error. Now rejected explicitly with the type named. This is exactly
what ruling 4 anticipated: a conformance checker that passes a broken shim is
worse than none, and writing the broken cases is what surfaced it.

Broken shims covered: no-op delete (soft delete retrieval ignores), leaky
scoping (tenant filter without user), wrong response field, non-string answer,
reset that does not clear, unreachable server. Each asserted caught, and a test
asserts **every** failure carries a non-empty fix.

### 3. Decisions

- **Doctor is generic over adapters**, not HTTP-only — it uses the adapter
  contract, so `doctor --adapter langgraph:memory` works too. Remedies are
  written for the shim because that is the supported path.
- **Convergence is measured, not assumed.** Doctor polls and reports the
  observed latency plus a suggested timeout (~4x observed), so invariant 10 is
  sized from the customer's stack. This is the preflight lesson applied to
  customers rather than to us.
- **Probe values carry a `memorycheck-doctor` marker** so anything left behind
  in a customer's store is identifiable and purgeable.
- **`langgraph_shim.py` exists to be diffed against.** The adapter is verified
  and passes the pack, so a customer whose stack fails can run both and see
  whether the difference is theirs or ours.
- **Templates are deliberately runnable before wiring**, so an integrator sees
  green before touching their own code — the fastest way to separate "the
  harness works" from "my stack has a bug".

### 4. FOR STRATEGY

- **The paraphrase limit will bite pilots at decision 2.** A customer who wires
  their real agent into `/query` rather than templating will see
  `missing_current_fact` whenever the agent paraphrases a value. The guide says
  so plainly and suggests starting with the store — but this is the first place
  the deterministic judge's known recall limit meets a paying user, and it will
  generate support questions before the LLM judge exists.
- **Doctor does not test correction.** It covers write, read, scope, delete and
  reset, but not "a second write supersedes the first" — that is the pack's
  job. Arguably a tenth check would catch the most common store defect before
  a full run. Deliberately left out to keep doctor about the *contract* rather
  than about memory behaviour; worth revisiting if pilots hit it.

### 5. Next

Saturday: Mem0 three-arm experiment at quota reset, then the pending regen.
Zep remains halted.

---

## 2026-07-28 — Task 4: LangGraph store adapter (roadmap item 3) — PASSES

### 1. What shipped

`adapters/langgraph.py`, spec `langgraph` / `langgraph:memory` /
`langgraph:sqlite[:path]`, extra `[langgraph]`, lazy import. Registry, CLI and
README updated. Evidence in `examples/report_langgraph.{md,json}`. 51 tests
pass, 4 skip (the live Mem0/Zep tests).

**First adapter to clear `unverified`** — every preflight item has an observed
answer and the full suite ran against the real stores.

### 2. Preflight (run first, as required)

| Item | InMemoryStore | SqliteStore |
|---|---|---|
| 1 quota / rate | none — local | none — local |
| 2 write confirmation | `get()` sees it immediately | same |
| 3 latency | ~0.3ms put, synchronous | ~0.7ms put, synchronous |
| 4 silent discard | none; 7/7 pack values round-trip | none; 7/7 |
| 5 reset / pagination | **`list_namespaces` default 100 returned 100 of 121** | same |

Three findings shaped the adapter:

- **Pagination hides namespaces by default**, exactly as on Zep. `reset()`
  pages via `offset` rather than trusting one call.
- **`search` defaults to `limit=10`**, which would silently truncate a scope
  and read as the store forgetting facts. Ceiling raised deliberately.
- **`SqliteStore` persists**, so reopening the file showed all 121 namespaces.
  `reset()` is therefore scoped to a run-specific namespace root — a global
  clear would delete rows belonging to whoever owns the file.

**TTL set from observation, not documentation.** `InMemoryStore.supports_ttl`
is `False` and `put(ttl=…)` raises `NotImplementedError`. `SqliteStore`
reports `supports_ttl=True`, but a `ttl` passed without a `ttl_config` was
**not enforced** — the value was still present after the TTL elapsed — and it
is wall-clock regardless, while memorycheck time is logical. `supports_ttl =
False` for both; expiry reports NOT_TESTED.

### 3. Findings — clean pass, stated plainly

15 scenarios × 2 seeds, seed-stable, on **both** backends:

```
current_fact_accuracy  100% (46/46)
stale_reuse            0%  (0/10)
scope_leakage          0%  (0/22)
deletion_residue       0%  (0/18)
expiry_leak            NOT TESTED
memory_utility_delta   +1.00

GATE [fail on <= P2]: PASS (0 blocking findings)
```

**No failure was hunted for and none is implied.** A keyed store supersedes by
construction: a correction overwrites the key, so no superseded copy survives
to be retrieved; deletion removes the row; namespaces are structural, so scopes
cannot bleed. This is what a key-value store does, and it makes the adapter a
useful **control** — roughly the floor of what a correct store looks like under
these scenarios, and a reference point for reading the hosted providers.

Guards against a vacuous pass, all satisfied:

- current-fact accuracy 100%, so it is not passing by storing nothing;
- memory utility delta +1.00 against the no-memory baseline, so memory is
  demonstrably driving answers;
- the same pack still fails `reference:naive` with 32 blocking findings, so the
  scenarios retain their teeth;
- the full 15-scenario suite also runs through the runner in the offline tests,
  exercising rescope replay and `advance_time`.

### 4. Decisions

- **Namespace is `(run_root, tenant_id, user_id)`.** The scope mapping is the
  `(tenant_id, user_id)` pair as specified; the run root is prepended because
  `SqliteStore` persists and an unscoped `reset()` would delete a user's own
  data. Noted as a deliberate extension rather than a silent change.
- **Answering layer copied from `ReferenceAdapter`**, including its key-token
  relevance filter, so what differs between adapters is the store rather than
  the phrasing.
- **Offline tests use a fake implementing the measured semantics** — keyed
  overwrite and paginated namespace listing. A fake that returned everything in
  one page would have validated a fiction, which is the mistake the Zep fake
  made before it was corrected.
- **Tests import no `langgraph`**, so they run rather than skip in CI without
  the extra. Verified in a clean venv with neither this nor any other extra.

### 5. FOR STRATEGY

- **A control adapter changes how the hosted results read.** With a local store
  passing cleanly, "Mem0 fails stale reuse 10/10" now sits against a
  demonstrated floor rather than in isolation. That strengthens the finding and
  also raises the bar: any future claim should say which adapter is the
  reference point.
- **`SqliteStore` advertising an unenforced TTL is a candidate finding**, not
  pursued. `supports_ttl=True` with a `ttl` argument accepted and ignored is
  the kind of green-status-no-effect behaviour this project exists to catch.
  Untested beyond one observation; would need a `ttl_config` variant before it
  is a claim.

### 6. Mem0 re-add experiment: armed and push-button

`diagnostics/readd_after_delete.py` is ready to run the moment SEARCH quota
resets on **2026-08-01**. No setup, no export:

```bash
python diagnostics/readd_after_delete.py --dry-run   # cost only, spends nothing
python diagnostics/readd_after_delete.py             # prints cost, asks, runs
```

- **Key resolves itself** from `MEM0_API_KEY` or `~/.mem0/config.json`.
- **Cost printed before anything is spent**, per arm: `a_identical` ~51,
  `b_varied` ~9, `c_settle_then_identical` ~29, quota probes ~7 — **~96 units**
  of a 1,000-unit period. The probes are themselves SEARCH calls and are now
  counted, so the estimate is not quietly optimistic.
- **Refuses to start below the estimate.** Verified: at the current 0 remaining
  it exits 3 with `REFUSING TO RUN: need ~96, have 0` — a run that dies partway
  proves nothing and spends the rest.
- **Results are written to `diagnostics/results/`**, which is **gitignored**. A
  transcript is easy to lose and this run is expensive to repeat, but the
  finding is not publishable until ruled on, so a Saturday run cannot
  accidentally publish Mem0 behaviour data. Promote a result into this file by
  hand once it is cleared.
- The reading of each arm is stated in the file **before** any run, and it
  refuses to conclude from a single execution.

### 7. Next

Saturday: run the three arms twice, then the founder ruling, then the pending
Mem0 15 × 2 regen on current `main`. Zep remains halted with no unblock date.

---

## 2026-07-27 — Zep staged verification: STAGE 1 IN PROGRESS, NOT PASSED

Staged per ruling. **Stage 1 is not passed, so stages 2–4 have not been
started and no Zep numbers exist.** Nothing published.

### Zep quota and rate-limit model (logged before stage 4, as required)

Structurally the inverse of Mem0's, which changes what we have to budget for.

| | Mem0 | Zep |
|---|---|---|
| Metered on | reads (`SEARCH`, 1000/period) and writes (`ADD`, 10000/period) | **ingestion only** — credits per episode; storage and reads not charged |
| Our cost per 15 × 2 run | ~106 SEARCH units, ~10% of a period | **60 credits** (30 episodes/seed, each ≤41 bytes so 1 credit each) |
| Full runs per free period | ~9 | **~166** (10,000 credits/month free tier) |
| Binding constraint | read quota — convergence polling is expensive | **rate limit and latency**, not credits |

Measured from live response headers, not documentation:
`x-ratelimit-limit: 300`, `x-ratelimit-remaining: 291`, `x-ratelimit-reset`
about 60s out — so ~300 requests/minute on this account. Convergence polling
is free in credits here, unlike Mem0, but counts against that rate limit.
Published tiers also state Free has the lowest rate limit and it may be
reduced further under load ([pricing](https://www.getzep.com/pricing/),
[FAQ](https://help.getzep.com/faq)) — treat the headers as authoritative over
the marketing pages.

### Stage 1 — assumption 1: **PASSED**, and it repriced the whole exercise

Third probe resolved it. Reading the episode directly by uuid
(`graph.episode.get`) rather than through the list endpoint:

```
t= 313.9s  processed=False
t= 329.3s  processed=True
edge fact: 'This is the starter-legacy-2024 plan.'
```

**Assumption 1 holds.** The edge fact is rephrased — stored
`"plan: starter-legacy-2024"`, returned `"This is the starter-legacy-2024
plan."` — but the distinctive *value* survives byte-verbatim inside it, which
is what the deterministic judge matches on. **Zep is measurable without the LLM
judge**, so the calibration protocol does not block this adapter.

**Extraction latency: 329s for one 25-byte episode.** `_EXTRACTION_TIMEOUT` had
been guessed at 120s by analogy to Mem0's ~15s pipeline — wrong by ~3x, and
every write in a run would have aborted. Reset to 900s (~2.7x headroom on the
single measurement), `_DELETE_TIMEOUT` to 120s, and the poll interval from 1s
to 5s since minute-scale extraction makes second-by-second polling pure
rate-limit spend.

**Consequence for stage 4 — wall clock, not cost.** The adapter blocks per
write until the edge exists, so runtime is dominated by extraction:

| | Zep | Mem0 |
|---|---|---|
| Credits / money per 15 × 2 | 60 credits (~0.6% of free month) | ~106 SEARCH (~10% of period) |
| **Wall clock per 15 × 2** | **~5.5 hours** (60 episodes × ~330s) | ~2.5 minutes |

Zep is ~130x cheaper per run and ~130x slower. That inverts the tiering
rationale: for Mem0 the full pack is gated on quota, for Zep it is gated on
time, and a per-commit Zep smoke test is impractical at any size — even one
scenario is ~11 minutes. Worth a founder decision before stage 4 commits an
afternoon of runtime.

Caveat on the number: one measurement, on a project created ~20 minutes
earlier, so a cold-start component cannot be ruled out. Stage 2 adds data
points.

### Stage 2 — one scenario, seeds=1: **PASSED**, mechanism under verification

`001-correction-stale-reuse` against live Zep, 702s wall clock:

```
current_fact_accuracy  100% (2/2)
stale_reuse            0%  (0/1)
deletion_residue       0%  (0/1)
GATE [fail on <= P2]: PASS (0 blocking findings)
```

**Timeouts confirmed adequate and 329s confirmed representative.** 702s ≈ two
writes at ~330s plus delete convergence, so the stage-1 latency was not a
cold-start artifact. `_EXTRACTION_TIMEOUT` at 900s has ~2.7x headroom;
`_DELETE_TIMEOUT` at 120s sufficed for the one delete observed. Stage 3 tests
deletion properly.

**Zep passed the correction scenario Mem0 fails 10/10.** That is the
interesting result, and it is exactly the one not to report on inference —
two mechanisms produce the same PASS:

  (i) Zep marked the superseded edge `invalid_at` and the adapter filtered it
      — temporal invalidation genuinely fired;
 (ii) the superseded edge is simply absent — the pass is an absence, not a
      correction.

**Stage 2b settled it: mechanism (i), with evidence.**

```
old edge: 'This is the starter-legacy-2024 plan.'
          valid_at=22:33:01  invalid_at=22:39:12  expired_at=22:45:03
new edge: 'scale-annual-2026 is a plan.'
          valid_at=22:39:12  invalid_at=None
```

The old edge's `invalid_at` equals the new edge's `valid_at` to the second:
Zep superseded the fact at the instant the correction became valid. Extraction
took 370s and 360s for the two writes, consistent with stage 1's 329s.

**The detail that matters most: `search(scope="edges")` returned BOTH edges,
including the invalidated one.** The pass is produced jointly — Zep publishes
accurate invalidation metadata, and this adapter filters on it. An integrator
who templated search results without checking `invalid_at` would surface the
stale fact and fail exactly as Mem0 does.

So the correct framing, and the one to use if any comparison is ever
published: **both providers return the superseded value from search; only Zep
tells you it is superseded.** The difference is the metadata contract, not
retrieval behaviour. "Zep passed where Mem0 failed" is false as stated — it
would credit Zep for something our adapter did with information Mem0 does not
publish. This is the comparability problem flagged earlier, now concrete, and
the reason a single comparative number must not ship.

### Stage 3 — assumptions 3 and 4 done; **assumption 2 not verified**

**Assumption 3: CONFIRMED.** `graph.create` on an existing id raises
`BadRequestError` 400 rather than clobbering, so the adapter swallowing that
exception is correct.

**Assumption 4: latent defect found and fixed.** Zep paginates graph listing
and reports `total_count`; `reset()` made a single `page_size=100` call. Fine
at today's 6 graphs, but one full 15 × 2 run creates ~45 graphs (one per
namespace/tenant/user), so an account crosses a page within a few runs. A
`reset()` that cannot see a graph cannot clear it, and the residue would later
be scored as leakage or deletion residue — a false FAIL manufactured by us.
Now pages until `total_count`. The offline fake had been returning everything
in one page, i.e. validating a fiction; it now paginates, and a test forces a
2-row page to prove `reset()` still clears everything.

**Assumption 2: REJECTED AS VACUOUS, not passed.** The probe reported
"removed from retrieval", but the log shows why that is worthless:

```
assumption 2: waiting for extraction...
              episodes=1 edges=0        <- no edge ever created
              after delete: edges_listed=0 search_returned=0
              -> removed from retrieval
```

Extraction never produced an edge within 900s, so the probe deleted an episode
that had no derived edge and observed nothing in retrieval. You cannot show
deletion removes a fact when no fact existed. Re-running with a 30-minute
ceiling and `processed`-flag tracking so a stall is distinguishable from a
slow success.

### CORRECTION: it is not latency variance. Extraction is content-dependent.

An earlier revision of this entry claimed extraction latency was "variable and
unbounded" on the strength of a sample that showed no edge after 900s. **That
claim was wrong and is withdrawn.** Re-running with a 30-minute ceiling and
`processed`-flag tracking:

```
processed_at = 368.5s     <- processed on schedule, in line with the others
edge_at      = None       <- and produced NO EDGE, ever, through 1800s
```

The episode processed normally. It simply yielded no edge. Latency is in fact
consistent at ~330–370s; the variable is **whether extraction produces an edge
at all**.

| stored text | edge produced |
|---|---|
| `plan: starter-legacy-2024` | yes — `'This is the starter-legacy-2024 plan.'` |
| `plan: scale-annual-2026` | yes — `'scale-annual-2026 is a plan.'` |
| `plan: deletable-<10-digit stamp>` | **no**, twice, `processed=True` both times |

Zep's extractor decides what constitutes a fact worth materialising, and text
that does not read as a meaningful statement appears to yield nothing.

**This is potentially fatal to measuring Zep with the current pack.** The
scenario values are deliberately distinctive nonsense tokens — `zephyr-6621`,
`harborlight-5529`, `pinecrest-6604` — chosen precisely so the deterministic
judge can match them unambiguously (invariant: "scenario values must be
distinctive"). The property that makes them good for the judge may be the same
property that stops them being extracted. A probe is running against the
pack's real values to find out.

If they do not extract, the options are all unattractive and none is the
implementer's to choose:

1. Rewrite scenario values as natural-looking facts — weakens judge
   precision and changes what every other adapter is measured on.
2. Read Zep at `scope="episodes"` instead of edges — bypasses the knowledge
   layer, and guarantees a stale_reuse FAIL by construction since a raw log
   never invalidates. Rejected earlier for exactly that reason.
3. Accept that Zep is out of scope for the deterministic judge, as
   anticipated in the stage-1 ruling — though the mechanism is
   non-extraction rather than paraphrase.

**FOR STRATEGY.** Related: assumption 2 (deletion removes a fact from
retrieval) **remains untested**, because both attempts used a value that never
produced an edge to delete.

### RESULT: some pack values never extract. Zep verification stops here.

Five episodes, one graph, the pack's own values. Raw observation, no rate
derived — see the note below on why a rate would be meaningless:

```
EDGE     plan: starter-legacy-2024        (baseline)
NO EDGE  subscription: moonstone-7742     (scenario 006)
EDGE     seat-count: pinecrest-6604       (scenario 011)
EDGE     renewal-window: harborlight-5529 (scenario 009)
NO EDGE  shipping-window: zephyr-6621     (scenario 008)
```

Facts produced: `'renewal-window is related to harborlight-5529.'`,
`'The starter-legacy-2024 is a plan.'`, `'The seat-count is pinecrest-6604.'`

Some of the pack's values silently produce nothing. With the adapter as built,
those writes fail confirmation and abort the run; with confirmation relaxed,
they would surface as `missing_current_fact` — a fabricated P2 failure against
Zep for facts it was never asked to store in a form it extracts. Enough of the
pack is affected that **no aggregate Zep number can be trusted and stage 4 must
not run.**

**No rate is quoted here, deliberately.** Which values extract depends on the
tokens we invented, so any proportion measured over our own value set describes
our token choices, not the provider. Quoting one would manufacture a statistic
out of an arbitrary sample.

**Second correction to the latency record.** In this probe three edges appeared
within **30s**, against ~330–370s for single writes earlier. Latency is
variable in both directions and the earlier figures were not representative
either. Both the "consistent ~330–370s" and "unbounded" characterisations are
withdrawn; the honest statement is that observed extraction ranged from <30s to
never, and we do not have a model of what drives it.

**This is a scoping limit, not a Zep defect, and must never be published as
one.** A knowledge graph extracting only what reads as a fact is reasonable
design. The incompatibility is with *our* instrument: the pack uses distinctive
nonsense tokens precisely so the deterministic judge can match them
unambiguously, and that same quality is what makes them unextractable. The tool
and the provider are each internally consistent and mutually incompatible.

### Status (ruling 2026-07-28)

**Zep: not measurable by the current instrument. Mechanism: selective silent
extraction. No committed unblock date.**

Stages 1–2 passed, stage 3 incomplete (assumption 2 untestable), stage 4 not
started and not startable. `unverified = True` stays set. No Zep numbers exist
and nothing is published.

**Correction to an earlier recommendation.** This entry previously proposed
deferring Zep "until the LLM judge is calibrated". That was wrong reasoning and
is withdrawn: **non-extraction is not a judge problem.** A judge of any kind
classifies whether a returned answer relies on a value; a semantic judge cannot
classify a fact that was never materialised, because there is nothing in the
retrieval path to reason about. Tying this to judge calibration would have
implied a fix that calibration cannot deliver, and set an unblock date that
does not exist.

Options 1 (rewrite pack values), 2 (per-adapter value sets) and 3 (read
episodes) are **rejected**. Pack values are not to be rewritten.

### OPEN RESEARCH QUESTION — post-sprint, not now

Should scenario values be **realistic rather than distinctive nonsense**, for
external validity? Real customer memory holds plan names, cities and dates, not
`zephyr-6621`. A pack built from realistic values might measure behaviour
closer to production — at some cost to judge precision, since realistic values
collide and recur.

To be decided **on evidence, separately, and never as a provider workaround**.
The distinction matters: changing the instrument because one provider is
awkward corrupts every other measurement taken with it. If realistic values are
right, they are right on their own merits and the Mem0 figures get regenerated
deliberately, not as a side effect.

### CANDIDATE FINDING — not publishable

**A write can process successfully and materialise nothing, with no signal
distinguishing stored from discarded.** Observed on Zep: `graph.add` returns an
episode, the episode reaches `processed=True` on schedule, and no edge is ever
created. Nothing in the API distinguishes that outcome from one where a fact
was stored — the caller sees success either way.

If it generalises, it is a genuine lifecycle hazard: an agent writes a fact,
every status is green, and the fact is simply not there. That is the class of
failure this project exists to surface.

**Not publishable as it stands, and no rate may be quoted.** Which values
extract depends on tokens we invented, so any proportion is an artifact of our
sample rather than a property of the provider. Before this becomes a claim it
needs a model of what actually drives extraction — a hypothesis about what the
extractor treats as a fact, tested against values chosen to probe that
hypothesis rather than values chosen for judge convenience.

### Roadmap change (ruling 2026-07-28)

**LangGraph moves ahead of any further Zep work.** Before it starts, write a
one-page **adapter preflight** derived from the three surprises so far, and run
it for every future adapter before any scenario executes.

### Earlier stage 1 probes (recorded because two were our error, not Zep's)

Three probes, each correcting the previous one's misreading.

**1st probe.** One write, then poll `graph.search(scope="edges")` and
`graph.episode.get_by_graph_id` for 300s. Result: **zero edges and zero
episodes**. Read naively this says the write was lost.

**2nd probe — that reading was wrong, and it was our error.** Keeping the
`graph.add` return value (discarded the first time) shows the write is
accepted and the episode exists:

```
content: 'plan: starter-legacy-2024'   <- byte-identical to what we wrote
processed: False
uuid_:   7fe5e303-...
```

`graph.episode.get_by_graph_id` evidently does **not list unprocessed
episodes**, so "0 episodes" meant "nothing has finished processing", not
"nothing was ingested". Also confirmed the credential and project are fine:
`graph.create`, `graph.get` and `graph.list_all` all behave.

**3rd probe, running:** read the episode directly by uuid (`graph.episode.get`),
which exposes `processed`, over a 15-minute window — long enough to
distinguish slow from never. Only if it processes do edges exist, and only
then can the verbatim question be answered.

**Assumption 1 remains genuinely unanswered.** The raw episode `content` is
verbatim, but `query()` reads *edges*, and no edge has yet existed to inspect.

### Why this justifies the staging

Had stage 1 been skipped, the full pack would have run against a provider
whose ingestion had not processed, every scenario would have failed
`missing_current_fact`, and the output would have looked like a catastrophic
Zep result. It would have been entirely our artifact. This is the third
instance of the same class in this project — after the Mem0 reset race and the
`infer=True` repro — and the first one caught before any numbers existed.

It also invalidates a sizing decision made blind: `_EXTRACTION_TIMEOUT` is
**120s**, and processing has already exceeded that without completing. Whatever
stage 1c returns, that constant is wrong and must be set from evidence.

Separately, `ZepAdapter` constructs `Zep(api_key=...)` with **no HTTP
timeout** and the SDK defaults to `None`, so a hung request would block a run
indefinitely. To fix regardless of the stage-1 outcome.

### Next

1. Finish stage 1c. If extraction never completes, **stop**: Zep cannot be
   measured on this account tier and that is the finding — a scoping fact
   about the tier, not a defect, and not publishable as one.
2. If it completes, answer the verbatim question, then reset
   `_EXTRACTION_TIMEOUT` from the measured latency before stage 2.
3. Stages 2–4 remain unstarted. No Zep numbers until the founder rules.

---

## 2026-07-27 — Rulings on Task 3e, and the experiment armed for 2026-08-01

### Rulings recorded

1. **Rescope replay unchanged.** Identical-text re-add stays. Concealing
   provider behaviour without sign-off is forbidden and sign-off is withheld.
   No code changed.
2. **012 behaviour not published.** Mechanism unnamed, no claim made. The
   provenance notes in `README.md` and the report preamble were tightened
   accordingly: they now say the run aborted for a reason not yet established,
   **explicitly not attributed to Mem0 or to this harness**. Previously they
   said "a write that stayed unretrievable for 30s", which a reader could have
   taken as a claim about the provider.
3. **External launch HELD** until a clean full 15 × 2 regen on current `main`,
   earliest 2026-08-01 when SEARCH quota resets. Nothing external ships first.
4. Discriminating experiment written — below.
5. **No further Mem0 calls.** Priority is Zep verification once a credential
   exists, then LangGraph.
6. Per-run SEARCH cost documented in the README adapter section.

### The experiment: `diagnostics/readd_after_delete.py`

Written and validated, **not run** — it needs live calls and SEARCH is at 0.
Placed in `diagnostics/`, not `examples/`, because `examples/` holds repros of
*published* findings and this claims nothing.

Three arms, with the reading of each stated in the file **before** any run, so
the interpretation cannot be fitted to whatever comes back:

| Arm | Procedure | Isolates |
|---|---|---|
| (a) `a_identical` | delete, re-add identical text | baseline; expected to reproduce the abort |
| (b) `b_varied` | delete, re-add varied text | content-level deduplication |
| (c) `c_settle_then_identical` | delete, poll until search reads empty, wait a further 60s, re-add identical text | whether deletion keeps reaping after it stops being observable |

**Validated offline against three simulated providers** — no quota spent — and
the arms discriminate cleanly:

| Simulated behaviour | (a) | (b) | (c) |
|---|---|---|---|
| healthy | visible | visible | visible |
| content dedup, permanent | **lost** | visible | **lost** |
| delete keeps reaping, expires | **lost** | visible | **visible** |

The third row is the signature that matters: (a) fails and (c) succeeds means
**confirming a delete by polling until empty is insufficient** — deletion
continues to reap writes after it has stopped being observable through search.
That would invalidate the delete-confirmation strategy invariant 10 introduced,
and would hit any customer doing delete-then-re-add.

**Estimated SEARCH cost, printed by the script before it spends anything:**

| Arm | Worst case |
|---|---|
| (a) identical | ~51 units |
| (b) varied | ~9 units |
| (c) settle then identical | ~29 units |
| **Total** | **~89 units** of a 1,000-unit period |

The script reads the live counter first and **refuses to start if the remaining
quota is below the estimate** — a run that dies partway proves nothing and
spends the remainder. Poll interval is deliberately 5s rather than the
adapter's 0.5s: each poll is a metered SEARCH call, and a failing arm at 0.5s
would burn ~60 units alone, which is how the original investigation exhausted
the quota. It also refuses to conclude from a single execution.

### Cost documentation (ruling 6)

README now carries a "What a run costs you" table: `SEARCH` 1,000/period and
`ADD` 10,000/period metered independently; measured **~53 SEARCH per seed** for
the 15-scenario pack, so **~106 for a default 2-seed run**, about a tenth of a
period. Notes that confirmation reads are the bulk and are not optional, that a
1,000-unit period allows ~9 full runs, and that the intended shape is smoke
per commit with the full pack nightly — which is the agreed tiering, now with
the number attached.

### Next

1. **2026-08-01, quota reset:** run the three arms, twice, before reading
   anything into them.
2. Founder ruling on whatever they show.
3. Full 15 × 2 regen on current `main`; refresh README and preamble provenance.
4. External launch, not before.
5. Zep verification is unblocked and independent — it needs a credential, not
   quota.

---

## 2026-07-27 — Task 3e: regen failure diagnosed — **STOP, ruling needed**

### 1. What shipped

Diagnosis of the failed 15 × 2 regeneration, provenance disclosure on
`README.md` and the `report_mem0.md` preamble, and a more specific write-failure
message (value and scope, not just key).

### 2. Findings — the primary hypothesis is refuted

**It was not quota.** Reproduced on scenario `012-rescope-then-readd` at
`--seeds 2`, with search quota still available:

```
adapter error: mem0 write for key 'handover-note' was not retrievable
after 30.3s — the store did not accept the write
```

That is the original abort: a **convergence timeout**, not a rate-limit
rejection. Two supporting details: the failing run predated the `_call`
wrapping, so a quota rejection would have surfaced as a multi-line traceback
rather than the single line observed; and the run consumed ~83 of ~106
expected units, consistent with dying ~78% through, around scenario 012.

Quota exhaustion is real but **downstream**: the follow-up diagnostic on `013`
returned `429 ... quota_used: 1000`. Search quota is now **0 of 1000 until
2026-08-01**. It was spent on the targeted diagnosis, not on blind retries.

Also refuted along the way: identifier truncation. `app_id` (43 chars) and
`user_id` (56 chars) differ only in the seed digit near the end, so server-side
truncation would have collided the two seeds and explained why only `--seeds 2`
failed. Tested directly — no truncation, each scope reads back only its own
value.

**Discovered:** Mem0 meters two independent counters — SEARCH (1000/period,
the scarce one) and ADD (10000/period). Convergence polling spends SEARCH.

### 3. Leading hypothesis — unconfirmed, and out of runway

Scenario `012` is the only one whose rescope replays an **identical value into
a different scope immediately after deleting it**: the runner emits
`delete(ivor, handover-note)` then `write(jonas, handover-note,
wintergreen-4471)` with the same text. The write is acknowledged and then not
retrievable for 30s. Candidate mechanisms, in order:

1. Mem0 deduplicates identical content within an `app_id`, and the just-deleted
   original is still present in its dedup index, so the re-add is swallowed.
2. The delete propagates asynchronously through a secondary index and reaps the
   new memory — the reset race (`27e9599`) recurring at memory granularity
   rather than namespace granularity.
3. Something specific to writing a value that currently exists nowhere but did
   moments ago.

**This cannot be distinguished without live calls, and search quota is zero
until 2026-08-01.**

### 4. FOR STRATEGY — **blocking ruling required**

**This may be an adapter defect, so per the standing instruction everything
external stops here.** Nothing has been sent or published.

The question that needs a founder call is which of these it is:

- **Adapter defect.** Our rescope replay writes text identical to what it just
  deleted. A real integrator moving a fact between users would plausibly do the
  same, but we could also make the harness avoid the pattern. If we "fix" it in
  the adapter, we may be hiding a genuine provider behaviour — precisely what
  invariant 9 forbids without sign-off.
- **Genuine Mem0 behaviour and a publishable finding.** "A value re-added
  immediately after deletion is not retrievable for at least 30s" is exactly
  the class of lifecycle defect this tool exists to surface. If so it deserves
  its own scenario and disclosure, not a workaround.

I cannot resolve this without evidence, and the evidence needs quota.
**Recommendation:** treat the published Mem0 figures as still valid — they
concern correction handling, and `012` contributes no stale_reuse finding — but
**do not ship anything external until this is settled**, because the obvious
question from a reader is whether the harness works, and today the honest
answer is "it aborts on one of fifteen scenarios for a reason we cannot yet
name".

Note the exposure if this is provider behaviour: the same pattern is what a
customer's own delete-then-re-add would hit, which makes it more interesting
than the correction finding, not less.

### 5. Next — gated

1. **On 2026-08-01, when quota resets:** run `012` in isolation with logging at
   each step to establish which write fails and whether the value ever becomes
   retrievable; then vary the value so the re-add is *not* identical to the
   deleted text, which discriminates hypothesis 1 from 2.
2. Then the founder ruling above.
3. Then the pending full 15 × 2 regeneration, and only then external posting.
4. LangGraph and the Zep credential remain behind all of it.

---

## 2026-07-27 — Task 3d: invariant 10 (never judge a provider on a race)

### 1. What shipped

Invariant 10 added to `CLAUDE.md`, plus the audit and remediation it implies —
an invariant the code violates is worse than no invariant.

`poll_until(predicate, timeout, interval) -> (converged, seconds)` in
`adapters/base.py`: checks immediately (a synchronous provider pays nothing),
then polls, and returns the elapsed time so a failure to converge is reportable
with its latency rather than as a silent zero.

**Audit found four violation classes, all now fixed:**

| Adapter | Was | Now |
|---|---|---|
| `mem0.reset` | polled, then a fixed 2s "settle" sleep | polls until the namespace reads empty; aborts with latency if it never does |
| `mem0.write` | no confirmation — next query could race ingestion | polls until the value is retrievable |
| `mem0.delete` | no confirmation — next query could see a doomed value | polls until the key's memories are gone |
| `zep.write` | no confirmation, and queries read **edges** | polls until a *live edge* carries the value, i.e. the layer `query()` reads |
| `zep.delete` / `zep.reset` | fire and hope | poll until episodes/edges/graphs are actually gone |

Every non-convergence raises `AdapterError` with the measured wait, aborting
the run instead of producing a figure.

**The boundary is written into the invariant and pinned by a test.** Adapters
confirm *their own* writes and deletes. They must never poll a *query* until a
value the ledger expects appears — that would launder a genuine
`missing_current_fact` into a pass. `test_convergence.py` includes the case
where a provider acknowledges a write then loses it: `query()` must report the
absence, not wait for it to come back.

### 2. Findings

The Zep fix is the one that matters most, and it is pre-emptive rather than
reactive. Zep queries read extracted **edges**, not raw episodes, and
extraction is an LLM job — Mem0's comparable pipeline measured **~15s**. The
adapter previously wrote and returned immediately, so the runner's next query
would very likely have raced extraction and produced `missing_current_fact`
across the entire pack. That would have been a fabricated headline finding
against Zep on the very first live run. HANDOFF assumption 5 called this the
likeliest failure; invariant 10 closes it before contact.

Live re-verification on Mem0 after the change: individual scenarios return
identical results (`001`: stale_reuse 1/1, deletion_residue 0/1; `002`:
deletion_residue 0/1), so convergence polling removes races without altering
what Mem0 reports. A full 15 × 2 regen was attempted and **failed** — see FOR
STRATEGY; the published figures are still the pre-change run.

### 3. Decisions

- **Helper lives on the adapter base**, so every future adapter converges the
  same way rather than each inventing its own sleep.
- **Zep confirms at the edge layer, not the episode layer.** Confirming the
  episode would still leave the query racing the extractor — right-looking and
  useless.
- **Non-convergence aborts rather than degrading.** A partial run that looks
  like a result is exactly what invariant 3 (NOT_TESTED over silent pass)
  exists to prevent.
- **Timeouts sized per provider**: Mem0 30s, Zep 120s for extraction and 60s
  for deletes, since a graph build is unlikely to beat a flat store.

### 4. FOR STRATEGY

- **Convergence polling costs quota.** Each confirmed write adds at least one
  read, and Mem0 meters reads. The account is at ~217/1000 remaining for this
  period (resets 2026-08-01) after today's runs. This is now the binding
  constraint on re-running the full pack, and it strengthens the smoke/full
  tiering ruling further: correctness costs API calls, so the full pack should
  run nightly rather than per-commit.
- **The committed `report_mem0.json` predates this change, and the regen
  failed.** A 15 × 2 regen was attempted, consumed ~83 quota units and wrote
  nothing — it aborted after emitting a single line, the signature of an
  `AdapterError`. Individual scenarios run clean before and after, so the
  adapter is not broken generally; the cause is undiagnosed. Quota is now ~71
  of 1000 until 2026-08-01, so it was not chased with further blind runs.

  **The published figures remain the 15:58 UTC run** and are unchanged in
  every per-scenario re-check, but they were produced by the pre-invariant-10
  adapter. Do not describe them as produced by current `main`.

  Immediate consequence: every Mem0 SDK call is now wrapped in a legible
  `AdapterError`. The failure was opaque precisely because it was not, which
  is what cost the diagnosis. Next full run should either succeed or say why.

  Recommend regenerating when quota resets, and adding the producing adapter
  commit to the report header regardless — evidence should name the code that
  produced it.
- **Quota is now the practical limit on verification, not time.** Measured
  ~53 units per 15-scenario seed with convergence polling (each confirmed
  write adds a read). A 2-seed run is ~106 of a 1000-unit period. Budget it.

### 5. Next

Unchanged: Zep credential, then LangGraph. Invariant 10 makes the first live
Zep run substantially more likely to produce a real measurement rather than a
latency artifact.

---

## 2026-07-27 — Task 3c: pre-launch blockers

Three blockers cleared before external posting.

### 1. What shipped

**Blocker 1 — standalone repro.** `examples/repro_correction.py`: no
memorycheck import, only the Mem0 SDK, reads `MEM0_API_KEY`, runs the
correction case under `infer=False` and `infer=True` and prints exactly what
retrieval returns. Cleans up after itself. Real output pasted verbatim at the
bottom of the file. Linked from the README Result section with run
instructions.

**Blocker 2 — runtime guard on unverified adapters.** `unverified` and
`unverified_note` flags on `MemoryAdapter` (default `False`), so this
generalises to every future adapter rather than special-casing Zep:

- `memorycheck run` prints a boxed `UNVERIFIED ADAPTER` warning to **stderr
  before executing**;
- `build_summary` carries `adapter_unverified` / `adapter_unverified_note`
  into the JSON;
- the Markdown report opens with a blockquote banner above the header;
- the terminal scorecard repeats it under the gate verdict.

`ZepAdapter.unverified = True`. Three tests pin it: the stamp reaches JSON and
Markdown, verified adapters carry no stamp, and Zep declares itself unverified.

**Blocker 3 — provenance agreement.** Verified `mem0ai` **2.0.14** and the run
timestamp match across README, the `report_mem0.md` preamble and
`report_mem0.json`.

### 2. Findings

**The repro caught a real overclaim risk in its first run.** With a fixed 5s
settle, the `infer=True` case returned the superseded value **alone** — no
current value at all. That is a far more damaging-looking result and it is an
artifact: Mem0's extraction pipeline had not finished. Re-run with polling,
the correction became visible after **~15s** and both values were returned,
matching the `infer=False` case. The script now waits for the correction
before judging anything, and the file documents why.

Had that first output been pasted as evidence, we would have published
"Mem0 loses the corrected value entirely" — false, and trivially refuted by
anyone re-running with a longer wait. Same failure mode as the reset race:
the harness racing an asynchronous provider and blaming the provider.

Steady-state repro output (mem0ai 2.0.14, 2026-07-27 15:51 UTC):

| | correction visible after | returned | superseded present | current present |
|---|---|---|---|---|
| `infer=False` | ~3s | 2 memories | yes | yes |
| `infer=True` | ~15s | 2 memories | yes | yes |

**Secondary fix, same class.** Hardening the Zep adapter's error handling
revealed it swallowed *all* exceptions on read paths and returned "I don't
have anything stored" — so an auth failure or network fault would have been
scored as a provider that forgot every fact. Now only a 404 (graph absent)
reads as legitimately empty; everything else raises `AdapterError` and aborts
the run. Write failures abort rather than passing silently. This is invariant
9 applied before the fact rather than after.

### 3. Decisions

- **Flag lives on the adapter base, not the CLI**, so an adapter declares its
  own verification status and the guard cannot be forgotten at a call site.
- **The stamp travels in the JSON**, not only the human-readable report — a
  number copied out of an artifact must carry its own disclaimer.
- **The repro polls rather than sleeping a fixed interval.** Reporting steady
  state, not a race we won.
- **Regenerated `report_mem0.json`** so the artifact is genuinely
  machine-produced with the new field rather than hand-patched.
- **Fake client raises a 404-carrying error** rather than subclassing the
  SDK's `NotFoundError`, so offline tests still run where the `zep` extra is
  not installed (CI installs only `[dev]`).

### 3a. Broke CI, then fixed it

Worth recording rather than quietly amending. The guard commit passed locally
and **failed CI** (3 failed / 30 passed). Cause: `_absent()` checked the SDK's
`NotFoundError` type first and returned `False` on `ImportError`, skipping the
status-code check. A dev machine has the `zep` extra installed so it passed;
**CI installs only `[dev]`**, so every 404 became a hard failure.

Fixed in `f230208`'s successor by checking `status_code` first — it is
SDK-independent — with a regression test that simulates the import failing.
Verified in a **clean venv with neither extra installed**, matching CI exactly:
34 passed, 4 skipped.

Lesson: "tests pass locally" means nothing for optional-extra code paths. The
dev environment has every extra installed and CI has none, so the two are
systematically different. Verify optional-dependency behaviour in a bare venv
before pushing, not on the machine where everything is present.

### 4. FOR STRATEGY

- **CI does not exercise the extras at all.** No job installs `[mem0]` or
  `[zep]`, so any adapter code path guarded by an SDK import is only covered
  on a developer machine. A second CI job installing all extras (still without
  credentials, so live tests keep skipping) would have caught the above before
  push. Cheap, and it grows more valuable per adapter.
- **The report_mem0.md preamble fragility bit immediately.** Regenerating the
  report to pick up the new JSON field wiped the hand-written framing and it
  had to be re-applied by hand. Second occurrence; it will eventually ship
  wrong. Recommend the reporter grow a `--note` input, or the framing move to
  a sibling file the report links to. Previously logged; now demonstrated.
- **`unverified` is honour-system.** Nothing enforces clearing it only after a
  real run. Consider requiring a recorded evidence path alongside clearing it.

### 5. Next

Unchanged: confirm the Zep assumptions against live Zep once a key exists,
then LangGraph. The ~15s extraction latency measured here strengthens Zep
assumption 5 — a graph-extraction pipeline is likely to need a comparable or
longer settle, so write-then-immediately-query will need care there too.

---

## 2026-07-27 — Task 3b: Mem0 framing correction + version currency

Approved external-framing change. Applied to `README.md` and
`examples/report_mem0.md`; numbers re-measured first.

### 1. What shipped

- **Re-benchmarked on the current release.** We had measured on `mem0ai`
  2.0.11; latest was **2.0.14**, published 2026-07-25 — two days before the
  original run. Upgraded and re-ran the full 15 × 2 suite. API surface is
  unchanged between the two (`add`/`search`/`get_all`/`delete`/`delete_all`
  signatures identical, entity-param rejection in `search`/`get_all` still
  present); offline tests green on 2.0.14.
- **Reframed the finding** in README and as an annotated preamble on the
  evidence report, citing Mem0's documented design directly.
- **Recorded run provenance** in both: SDK version, run timestamp, API host
  and endpoint version.

### 2. Findings

**Result is unchanged on 2.0.14** — identical figures, still seed-stable,
same five failure sites (`001` step 3, `007` steps 2 and 4, `008` step 3,
`013` step 4), each failing on both seeds.

| Check | 2.0.11 | 2.0.14 |
|---|---|---|
| current_fact_accuracy | 100% (46/46) | 100% (46/46) |
| stale_reuse | 100% (10/10) | 100% (10/10) |
| scope_leakage | 0% (0/22) | 0% (0/22) |
| deletion_residue | 0% (0/18) | 0% (0/18) |
| expiry_leak | NOT_TESTED (4) | NOT_TESTED (4) |

Run provenance now recorded: `api.mem0.ai` `/v3`, `mem0ai` 2.0.14, executed
2026-07-27 15:30 UTC. **The hosted platform exposes no version or build header**
— probed the API directly; responses carry only quota and standard headers. So
a platform version is not pinnable from the client and the run date is the only
honest pin. Claiming a platform version we cannot observe would itself be an
overclaim.

**Documentation quotes verified verbatim before publication**, not taken on
trust, since attributing words to a vendor in a public repo has to be
checkable. Both appear under the heading *"New Memory Algorithm (April 2026)"*,
subheading "What changed:":

- *"Single-pass ADD-only extraction -- one LLM call, no UPDATE/DELETE.
  Memories accumulate; nothing is overwritten."*
- *"Temporal Reasoning -- time-aware retrieval that ranks the right dated
  instance for queries about current state, past events, and upcoming plans."*

Also checked: Mem0's docs describe **no contradiction-resolution mechanism**
beyond the temporal-reasoning claim, so that claim is the only published
statement bearing on this test — which is what makes it the right thing to
measure against.

New supporting detail surfaced while re-reading the report: `007` step 4
relied on **both** superseded values in the chain (`larkspur-8815` and
`thistledown-2204`), not merely the most recent — the whole accumulated chain
stays live, not just the previous value.

### 3. Decisions

- **Framing is now "the read path did not change the outcome", not
  "corrections do not supersede".** Accumulation is documented design; flagging
  it as a defect would read as not having read the README. The testable claim
  is temporal reasoning ranking the right dated instance for current-state
  queries, and that is what the result speaks to. Wording is neutral and
  quotes their design directly so it reads as measurement.
- **Stated the measurement's own limit**: we observed that ranking did not
  change which value drove the answer for these queries — not that no ranking
  occurs internally.
- **Annotated the generated report rather than changing `report.py`.** The
  framing is Mem0-specific; baking per-provider prose into the core reporter
  would leak provider knowledge into shared code.

### 4. FOR STRATEGY

- **The annotation on `examples/report_mem0.md` is fragile.** That file is
  regenerated by `memorycheck run` and the hand-written preamble is
  overwritten. There is an HTML comment at the top warning re-application is
  needed, but this will eventually be missed. Options: keep the discipline,
  move framing to a sibling file the report links to, or add a `--note` input
  to the reporter. Recommend deciding before a third provider lands.
- **Full runs consume metered platform quota** (~46 SEARCH units per 15 × 2
  run; the account showed 1000 per period). Independent of wall-clock cost,
  this strengthens the case for the agreed smoke/full tiering.

### 5. Next

Unchanged: confirm the Zep assumptions against live Zep once a key exists,
then LangGraph. No new work opened by this correction.

---

## 2026-07-27 — Task 3a: Zep adapter (roadmap item 3, first half)

### 1. What shipped

`adapters/zep.py`, spec `zep`, optional extra `[zep]` (`zep-cloud>=3.25.0`),
lazy import, credentials from `ZEP_API_KEY`. Registry, `list-adapters` and
README updated. Tests: 30 pass / 4 skip without credentials (2 Mem0 live,
2 Zep live).

Written against the real SDK surface, inspected rather than recalled:
`graph.add/search/create/delete`, `graph.episode.get_by_graph_id/delete`,
`graph.edge.get_by_graph_id/delete`, `graph.list_all`, and the `Episode` /
`EntityEdge` / `GraphSearchResults` field sets.

### 2. Findings — **none. Not run against live Zep.**

**No credential was available, so the adapter has never contacted the Zep
service. There are no Zep metrics and none may be quoted.** The offline layer
drives the full 15-scenario suite through a fake client, which proves the
adapter satisfies the runner's contract (including rescope replay and
`advance_time`) and nothing whatsoever about the live platform.

The Mem0 work is the reason to take this seriously: its async-delete race was
invisible to every offline test and only appeared on contact with the real
service. Assumptions here that are unconfirmed and could each be wrong:

1. `graph.search(scope="edges")` returns edge facts containing the stored
   value verbatim. If Zep's extraction paraphrases the value away, the
   deterministic judge will not match and every check will read as
   `missing_current_fact` — a harness artifact, not a Zep defect.
2. Deleting an episode plus the edges listing it in `.episodes` removes the
   fact from retrieval. Zep may re-derive edges, or hold facts we did not
   enumerate.
3. `graph.create` on an existing id raises rather than clobbers (we swallow
   the error and continue).
4. `graph.list_all(page_size=100)` returns enough rows to find every graph in
   a namespace — no pagination is done, so a busy project could hide graphs
   from `reset()`.
5. Writes are visible to search promptly. Mem0 needed an explicit settle after
   deletes; Zep's ingestion is asynchronous by design (episodes are queued for
   extraction) and may need substantially longer.

Assumption 5 is the likeliest to bite: Zep extraction is a background job, so
a write-then-immediately-query scenario may legitimately return nothing.

### 3. Decisions

- **Scope maps to a graph, not a user.** Isolation becomes structural rather
  than a filter the adapter must remember to pass, and `reset()` is a graph
  delete — which also sidesteps the delete/write race class that bit the Mem0
  adapter, since the graph is recreated under a fresh id.
- **Reads use `scope="edges"`, the knowledge layer an agent would consume.**
  Episodes (raw ingest log) are used only to resolve deletes. Reading episodes
  would have guaranteed a stale_reuse FAIL by construction, since a raw log
  never invalidates — an unfair test.
- **The adapter honours Zep's `invalid_at` / `expired_at`.** See FOR STRATEGY.
- **Deletes remove derived edges, not just episodes.** Otherwise we would be
  measuring our own laziness rather than Zep's deletion behaviour.
- **`supports_ttl = False`**, as for Mem0: wall-clock validity cannot express
  logical time, so expiry stays NOT_TESTED.

### 4. FOR STRATEGY

- **Cross-provider comparability is now a live problem.** The Zep adapter
  filters on Zep's published liveness metadata; the Mem0 adapter cannot,
  because Mem0 exposes no equivalent. If Zep scores better on `stale_reuse`,
  the honest statement is "Zep gives integrators a liveness signal and it was
  accurate", not "Zep beat Mem0". **Recommend against any leaderboard or
  single comparative number** until there is a decision on whether adapters
  may use provider-specific quality signals. This affects how results can be
  published, so per the external-actions rule it needs a ruling before any
  comparative claim goes out.
- **A Zep credential is needed** to turn this from code into a measurement.
  Until then the README carries an explicit "unverified" banner and the
  roadmap entry says the same. Nothing about Zep should be repeated
  externally in the meantime.
- **Where should unverified adapters live?** Shipping code on `main` that has
  never touched its provider is a supportability risk — a user with a key will
  run it before we do. Options: keep as-is with the banner, gate behind a
  `--experimental` flag, or hold on a branch until verified.

### 5. Next

Confirm the five assumptions above against live Zep as soon as a key exists,
then LangGraph store adapter (second half of roadmap item 3). Do not start
LangGraph before Zep is verified — landing a second unverified adapter would
compound the risk rather than clear it.

---

### Superseded "Next" from Task 2

Roadmap item 3 — Zep adapter, then LangGraph store adapter. Same shape as
Mem0: lazy SDK import in an optional extra, credentials from an env var,
live tests skipping cleanly without them, and honest capability flags
(`supports_ttl`) so anything inexpressible reports NOT_TESTED. Expect the
async-delete lesson from `27e9599` to recur — check write-after-delete
visibility before trusting a clean `reset()`.
## 2026-08-03 — Quota-reset attempt: BLOCKED, nothing spent, nothing published

The 2026-08-01 quota reset arrived and the held work was attempted. **Neither
live task could run**, for reasons unrelated to quota. No Mem0 call was made,
no SEARCH unit was spent, and **no public text was changed**. Recording what
was established offline so the reset session is not repeated blind.

### 1. What shipped

| Commit | Change |
|---|---|
| _this entry_ | `fix(diagnostics):` pass the resolved API key into `MemoryClient` |

That is the only code change. It is a one-line harness fix to
`diagnostics/readd_after_delete.py` and it produces no provider verdict, so
invariant 9 permits it unblocked.

### 2. Findings

**Blocker A — no credential.** `MEM0_API_KEY` is unset; `~/.mem0/config.json`
does not exist; nothing in the repo carries a key (`.env`/`secrets.*` are
gitignored and absent). CI holds no Mem0 credential either — `ci.yml` runs
only `pytest` and `reference:strict`.

**Blocker B — the endpoint is not reachable from this environment.**
`api.mem0.ai:443` is refused by the environment's egress policy:

```
connect_rejected: gateway answered 403 to CONNECT (policy denial or upstream failure)
host: api.mem0.ai:443
```

This is independent of Blocker A. **Supplying a key alone will not unblock the
run from this environment**; the network policy has to permit `api.mem0.ai`,
or the run has to happen somewhere that already does. A plain TCP check to
`api.mem0.ai:443` *succeeds*, because it connects to the local proxy rather
than to Mem0 — so reachability must be checked through the proxy status
endpoint, not with a socket probe. Noting that because the naive check is
misleading and was tried first.

**Defect found and fixed — the experiment would have died at startup.** The
script documents "the key is read from `MEM0_API_KEY`, falling back to
`~/.mem0/config.json`, so no export is needed", but then constructed
`MemoryClient()` with no argument. `MemoryClient.__init__` (2.0.15) resolves
`api_key or os.getenv("MEM0_API_KEY")` and reads **nothing else** — the
config-file fallback fed the quota probe only. Reproduced directly:

```
resolve_api_key() -> FOUND (from config.json)
MemoryClient()    -> ValueError: Mem0 API Key not provided.
```

On the config-file path the script would have printed the cost, taken the
operator's confirmation to spend ~96 units, and *then* aborted. Fixed by
passing the resolved key through. Verified: construction now proceeds past
the `ValueError` to a real network call.

**Cost estimate, printed before spending (task asked for this).** From
`--dry-run`, which spends nothing:

| Arm | Worst case |
|---|---|
| (a) identical | ~51 units |
| (b) varied | ~9 units |
| (c) settle then identical | ~29 units |
| quota probes | ~7 units |
| **Total, one execution** | **~96 units** |

The armed-experiment entry above quotes ~89; that figure excluded the seven
quota probes. The protocol requires **two** executions before reading anything
into the arms, so budget **~192 units** of the 1,000-unit period.

**The 012 mechanism question is unanswered.** Content dedup vs. delete
propagation outliving search observability cannot be distinguished without
arm (c). No inference was drawn from the offline simulation — it validated
that the arms discriminate, it says nothing about which row Mem0 is on.

**Regen: the structural half reproduces, the provider half is unverified.**
Denominators are fixed by the pack and the ledger, not by the provider, so
they are checkable offline. On current `main`, `reference:strict --seeds 2`:

| Check | Published mem0 denominator | Current `main` |
|---|---|---|
| current_fact_accuracy | 46 | **46** ✓ |
| stale_reuse | 10 | **10** ✓ |
| scope_leakage | 22 | **22** ✓ |
| deletion_residue | 18 | **18** ✓ |

`reference:naive --seeds 2` returns the complementary 10/10, 18/18, 4/4. So no
change since `f230208` has moved the opportunity counts. **The numerators
(0/18, 0/22, 10/10) are provider behaviour and remain unverified on current
`main`** — that is exactly what the pending regen exists to establish, and it
is still pending.

**Scoring-path diff since `f230208`, read to predict the regen.** `oracle.py`
and `report.py` both changed after the published figures were produced:

- `oracle.py` — adds `detect_answering_layer` and a paraphrase branch that
  degrades `missing_current_fact` to NOT_TESTED. The branch is gated on
  `answering_layer == paraphrasing`. The Mem0 adapter templates stored
  memories verbatim into its answer, and its `retrieved` list is the same
  strings it joined into that answer, so `paraphrasing` cannot be set for
  this adapter — a value present in `retrieved` is necessarily present in the
  answer. The other four checks are untouched by the diff.
- `report.py` — additive only: new `answering_layer` and `limitations` fields
  plus presentation. `_rate` and `current_fact_accuracy` are unchanged.

So the *expected* result is that the figures reproduce. **That is a prediction
from reading the diff, not a measurement, and it is not a substitute for the
regen.** It is recorded so that if the regen does move a figure, the diff has
already been eliminated as the cause and the provider or the service build is
where to look.

**SDK has moved under the published figures.** `mem0ai` 2.0.15 was released
**2026-08-01**, four days after the 2026-07-27 run on 2.0.14:

| Version | Released |
|---|---|
| 2.0.13 | 2026-07-22 |
| 2.0.14 | 2026-07-25 ← published figures |
| 2.0.15 | 2026-08-01 |

The extra is declared `mem0ai>=2.0.14`, an unpinned lower bound, so a fresh
install today resolves 2.0.15. **A regen run now would not be a like-for-like
reproduction of the published figures** — it would change the SDK at the same
time as the commit. Whoever runs it should decide deliberately whether to pin
2.0.14 to isolate the harness change, or take 2.0.15 and accept two moving
parts.

**PR #6017 has not merged.** `mem0ai/mem0#6017`, "fix: detect and resolve
conflicting memories during ADD extraction", is **open**, targeting `main`,
with a substantive review contesting the approach (whether cosine similarity
distinguishes genuine conflict from mere relatedness, sync/async divergence,
embedding-mode comparability). It proposes UPDATE-on-conflict via similarity
thresholds (0.85 with LLM linking signals, 0.90 without) — directly on the
ADD-only accumulation behaviour the `stale_reuse` result concerns. **There is
no landed fix to re-run against**, so the stronger publication described in
the task is not available yet.

**2.0.15 relevance, stated carefully.** Its notes include a `delete_all`
pagination fix ("memories beyond a single page silently left behind"). Read
against our code: `MemoryClient.delete_all` is a single server-side
`DELETE /v1/memories/` with filter params and has **no** client-side batching
loop, so that fix appears to be in the OSS `Memory`/vector-store path, not the
hosted client path this harness uses. Not asserting that as settled — it is
from reading 2.0.15's source, not from a measurement.

**Latent limitation, logged not fixed.** `MemoryClient.get_all` returns a
paginated envelope (`count`/`next`/`previous`/`results`) and
`Mem0Adapter._results` reads `results` only, ignoring `next`. Every scope and
namespace read in the adapter is therefore **page one only**. Harmless for the
current pack (a handful of facts per scope), but the failure mode is
asymmetric and worth naming: in `delete()`, the doomed list *and* the
confirmation poll both read page one, so they would agree with each other
while residue survived beyond it — a false PASS on deletion_residue, the
P1 direction that matters most. Same class as the Zep bug fixed in `e9311b8`.
**Deliberately not fixed here**: changing the adapter mid-regeneration would
invalidate the very run we are trying to reproduce. See FOR STRATEGY.

**Suite state.** `pytest -q` on current `main`: **67 passed, 4 skipped** (the
skips are the live-service tests, skipping cleanly without credentials, as
intended).

### 3. Decisions

- **Nothing published, no public text touched.** Task step 4 asked for the
  README and report preamble provenance to be updated to "produced by current
  main, caveat removed". The regen that would make that true did not run, so
  making the edit would have put a false provenance claim into public files.
  The caveat stays until a regen actually backs it.
- **The diagnostics key fix proceeded unblocked** — harness defect, no
  provider verdict involved, invariant 9.
- **The pagination limitation was logged, not fixed** — it is a core adapter
  change and would confound the pending regen.
- **No substitute instrument was used.** A Mem0 connector is present in this
  session's tooling. It was not used: it is a different credential and project
  from the benchmark account, its write path does not expose the `infer=False`
  verbatim control the deterministic judge depends on, and writing probe
  values into an account that may be someone's live memory is not a reversible
  act. A measurement from it would not have been comparable to the published
  figures, and quoting it as if it were would be worse than no measurement.

### 4. FOR STRATEGY

- **How should the regen be run?** It needs an environment with a Mem0
  credential *and* egress to `api.mem0.ai`. This one has neither. Options:
  run it locally, or provision an environment whose network policy permits
  the host. This is a prerequisite for every remaining Mem0 item.
- **Pin the SDK for the regen, or not?** 2.0.15 is current and the extra
  floats to it. Pinning 2.0.14 isolates the harness change and gives a true
  like-for-like; taking 2.0.15 measures today's stack but moves two variables
  at once. **Recommend pinning 2.0.14 for the reproduction run, then a second
  run on 2.0.15 as a separate, labelled measurement.** Needs a call.
- **Should `mem0 = ["mem0ai>=2.0.14"]` become an exact pin?** A benchmark
  whose published figures name an SDK version has a reproducibility interest
  in the extra not floating. Applies to the `zep` and `langgraph` extras too.
- **#6017 is open and contested — does that change the publication plan?**
  The finding concerns behaviour that Mem0 has an open PR against. Publishing
  a `stale_reuse` result while a fix is in review is a fairness question, not
  a technical one. Note also that #6017 addresses conflict detection at
  *extraction* time, whereas this harness writes with `infer=False`, so it may
  not touch our write path at all even once merged. Worth establishing before
  the delta is promised as "a stronger publication".
- **When should the `get_all` pagination limitation be fixed?** Recommend
  after the regen lands, so the reproduction is not confounded. Flagging it
  because it can produce a false PASS on a P1 check, which is the direction
  the honesty model cares about most.

### 5. Next

1. Obtain an environment with a Mem0 key and egress to `api.mem0.ai`.
2. Run the three arms, twice, before reading anything into them (~192 units).
3. Founder ruling on the mechanism, per the 2026-07-27 ruling.
4. Full 15 × 2 regen on current `main`, SDK pinned per the ruling above.
5. Only then: refresh README and report provenance, and revisit launch.

External launch remains **HELD**. Nothing about the 012 mechanism, the regen,
or #6017 should be repeated externally on the strength of this entry.

---
## 2026-08-03 — Handoff protocol amended (founder instruction)

### 1. What shipped

`CLAUDE.md` → Handoff protocol, two additions. Docs only; no code touched.

1. **Session start is now a reconciliation step.** Every session begins with
   `git log --oneline -5` and `git status`, compared against the last
   `HANDOFF.md` entry, before anything else. Disagreement — a dirty tree, an
   unmentioned commit, or an entry citing a commit that does not exist — must
   be reported before proceeding rather than built on top of.
2. **"Complete" now means pushed.** A task is not done until its `HANDOFF.md`
   entry is written, committed, *and pushed to origin*. A session that cannot
   push has not finished, and must say which of the three steps it stopped at.
   Blocked work still gets an entry.

### 2. Findings

None — no run, no measurement.

### 3. Decisions

- **Blocked work still gets an entry**, stating the blocker, what was
  established anyway, and what remains unverified. Written into the rule
  rather than left to judgement, because the failure mode this closes is a
  session ending with nothing visible to review.

### 4. FOR STRATEGY

Nothing new. The open questions from the entry above stand unchanged: where to
run the regen, whether to pin `mem0ai` 2.0.14 for a like-for-like
reproduction, whether the extras should be exact pins, whether an open and
contested #6017 changes the publication plan, and when to fix the `get_all`
pagination limitation.

### 5. Next

Unchanged from the entry above. External launch remains **HELD**.

---
## 2026-08-03 — PR #1 merged; protocol amended again (founder instruction)

### 1. What shipped

| Commit | Change |
|---|---|
| `96a7ca0` | `fix(diagnostics):` pass the resolved key into `MemoryClient` |
| `5925a7f` | `docs:` handoff protocol — reconcile at session start, "done" means pushed |

Both were sitting on an unmerged branch. **PR #1 is now merged to `main`**
(rebase, keeping the linear history the repo has always had; CI green on the
head commit, no review comments). The branch was then restarted from the new
`main` for the follow-up below.

`CLAUDE.md`, two further amendments, docs only:

1. **"Complete" now means merged to `main`**, not merely pushed — written,
   committed, pushed, merged, in that order. A session that stops short must
   say which of the four steps it stopped at.
2. **New "Environment notes" section** recording that sandboxed sessions
   cannot reach `api.mem0.ai`, and that a TCP probe does not detect this.

### 2. Findings

None — no run, no measurement. The reachability facts recorded in
`CLAUDE.md` are the ones already established in the entry two above.

### 3. Decisions

- **Rebase merge, not squash.** The two commits are separable — a code fix and
  a protocol change — and the repo has no merge commits in its history.
- **The "merged to `main`" rule carries an explicit invariant 9 carve-out.**
  Added unasked, and flagged here for that reason. Without it the new rule
  reads as "merge to be done", which would silently repeal the requirement
  that a change flipping a provider FAIL to PASS gets founder sign-off
  *before* merge. For those changes an unmerged branch is the **correct**
  state, not an unfinished task, and the entry should say so rather than
  merging to satisfy the completion rule. Reword if that is not the intent.
- **Environment notes placed in `CLAUDE.md`, not `README.md`.** It is a fact
  about where the harness can be run from, not about the product.

### 4. FOR STRATEGY

- **Who merges?** Today's instruction was to merge, and the amended rule makes
  merge the definition of done — but taken together those mean an implementer
  can self-merge, and the repo then has no human gate at all. That is in
  tension with a project whose product is a review gate and whose invariant 9
  names sign-off "before merge, no exceptions". The carve-out above narrows it
  to provider-verdict changes; whether ordinary changes should also require a
  second pair of eyes is a founder call, not one to settle here.
- Unchanged from the entries above: where to run the regen, whether to pin
  `mem0ai` 2.0.14 for a like-for-like reproduction, whether the extras should
  be exact pins, whether an open and contested #6017 changes the publication
  plan, and when to fix the `get_all` pagination limitation.

### 5. Next

Unchanged. The Mem0 work is still blocked on an environment with a credential
*and* egress to `api.mem0.ai`. External launch remains **HELD**.

---
## 2026-08-03 — Ruling: tiered merge authority

### 1. What shipped

`CLAUDE.md` → Handoff protocol → new **"Merge authority (ruling 2026-08-03)"**
subsection. Docs only. This closes the FOR STRATEGY question "who merges?"
raised in the entry above; it is the founder's ruling, not an implementer
decision.

**Self-merge, no gate:** `src/`, `tests/`, `CLAUDE.md`, `HANDOFF.md`,
`ADAPTER_PREFLIGHT.md`, `diagnostics/`, `examples/shim/*`.

**Founder approval before merge, whatever the PR size:** `README.md`,
`examples/report_*.{md,json}`, any change to a published figure, claim or
provider framing, clearing `unverified` on any adapter, and anything that
would flip a provider FAIL to PASS (invariant 9, unchanged).

Mechanism: label `needs-founder-review`, state in the PR body exactly which
public claim changes and why, do not merge until the founder comments
approval, and record that approval in the `HANDOFF.md` entry.

The tier is decided by **what a PR touches, not how large it is** — a
one-word change to a README figure is in the gated tier; a 400-line adapter
refactor is not.

### 2. Findings

None — no run, no measurement.

**Operational note:** the `needs-founder-review` label does not yet exist in
the repository, and the GitHub tooling available to these sessions has no
label-creation call. GitHub creates a label automatically the first time it is
applied to a PR, so the mechanism works from first use — it will just appear
with a default colour. Worth setting a colour by hand if it should stand out.

### 3. Decisions

- **The provisional invariant 9 carve-out added in the previous entry is
  superseded** by this ruling and was replaced, not merely appended to. The
  ruling keeps invariant 9 unchanged and generalises it: invariant 9 is now
  one of five triggers for the gated tier rather than the only one.
- **The completion rule now has an explicit pause point.** For gated-tier PRs
  "complete" stops at *pushed* and waits for the ruling; an unmerged branch
  awaiting founder approval is the correct state, not an unfinished task, and
  the entry must say so rather than merging to satisfy the completion rule.
- **`CLAUDE.md` states plainly that this is not independent review.** The
  founder is the only human, and a gate with one participant cannot catch what
  that participant misses. Recording the limitation rather than letting the
  word "review" imply more assurance than the process provides — the same
  standard the product applies to its own evidence.
- **This entry's own PR is internal tier and was self-merged**, per the ruling
  it records.

### 4. FOR STRATEGY

Nothing new. The remaining open items are unchanged: where to run the regen,
whether to pin `mem0ai` 2.0.14 for a like-for-like reproduction, whether the
extras should be exact pins, whether an open and contested #6017 changes the
publication plan, and when to fix the `get_all` pagination limitation.

Note that **every remaining Mem0 item lands in the gated tier**: the regen
refreshes published figures, and the provenance edit changes a public claim.
Those will need the label and a ruling, not a self-merge.

### 5. Next

Unchanged. Mem0 work is blocked on an environment with a credential *and*
egress to `api.mem0.ai`. External launch remains **HELD**.

---
## 2026-08-03 — Arms (d)/(e) added: the 012 condition was never actually tested

### 1. What shipped

`diagnostics/readd_after_delete.py` gains two cross-scope arms. **Neither run
requested this session was executed** — see Findings. Nothing published, no
public text touched, no SEARCH unit spent.

| Arm | Procedure | Isolates |
|---|---|---|
| (d) `d_cross_scope_identical` | delete key from scope A, poll until A reads empty, immediately write the SAME text to scope B (different `user_id`, same `app_id`) | the scope crossing itself |
| (e) `e_cross_scope_settle` | as (d), with a 60s settle before the write to B | propagation timing vs. the crossing |

The "how to read this" guide was extended **before** any run, per the standing
practice that the interpretation is fixed in advance so it cannot be fitted to
whatever comes back.

### 2. Findings

**Both runs are blocked. Egress is still refused.** A Mem0 credential is now
present in the session (`~/.mem0/config.json`), but `api.mem0.ai` remains
blocked:

```
curl https://api.mem0.ai/v1/ping/  ->  CONNECT tunnel failed, response 403
proxy recentRelayFailures         ->  connect_rejected api.mem0.ai:443
```

This is the case `CLAUDE.md` → Environment notes describes: the credential
alone does not help, because the connection never reaches Mem0. Arms (a)-(e)
and the 15 × 2 regen all still require an environment with egress.

**Arms (a)-(c) test same-scope only, and therefore never tested `012`.** All
three delete and re-add under a single `user_id`. `012-rescope-then-readd`
deletes from one scope and writes the same value to a *different* one —
`delete(ivor, handover-note)` then `write(jonas, handover-note, <same text>)`.
The leading hypothesis recorded on 2026-07-27 already said "into a **different
scope**"; the experiment built to test it did not cross a scope boundary. That
is a design defect in the diagnostic, ours, and it is now fixed.

**Quantified offline, spending nothing.** Driving the real `Arm` class against
five fake providers of known behaviour:

| Simulated behaviour | (a) | (b) | (c) | (d) | (e) |
|---|---|---|---|---|---|
| healthy | visible | visible | visible | visible | visible |
| content dedup, app-wide | **LOST** | visible | **LOST** | **LOST** | **LOST** |
| delete reaping, same-scope | **LOST** | visible | visible | visible | visible |
| cross-scope suppression, permanent | visible | visible | visible | **LOST** | **LOST** |
| cross-scope suppression, transient | visible | visible | visible | **LOST** | visible |

**Using only (a)-(c), three behaviours are indistinguishable: `healthy`,
`cross-scope permanent`, and `cross-scope transient`.** All three give
`visible / visible / visible`. So a clean sweep of the original three arms
would have been consistent with a *permanent cross-scope suppression* — a
delete in one tenant's scope silently swallowing a write to another's — while
reading exactly like a clean bill of health. That is the sharpest form of the
finding, and the instrument could not see it.

**On the word "refuted", precisely.** The only refutation recorded in this log
is at Task 3e: *"the primary hypothesis is refuted — it was not quota"*. That
one was sound and remains so; it was evidenced by a convergence-timeout error
rather than a rate-limit, with quota still available and ~83 of ~106 units
consumed. **Arms (a)-(c) have never been executed**, so no reading — too
strong or otherwise — was ever actually drawn from them. What did exist was a
branch in the script's own interpretation guide reading "(a) succeeded -> the
abort did not reproduce in isolation", with no mention that the cross-scope
condition was untested. Had the arms been run and come back clean, that branch
was the route to an unearned "012 refuted". It has been rewritten to say
"not reproduced SAME-SCOPE" and to require reading (d)/(e) first.

**Cost.** Worst case is now ~158 units for all five arms (~29 each for (c),
(d), (e); ~51 for (a); ~9 for (b); ~11 probes). Note the worst case is the
*failing* case — an arm whose re-add never lands polls to `CONFIRM_TIMEOUT`.
If (d) and (e) both behave they cost ~15 together, as expected; if (d)
reproduces the abort it costs ~29 on its own. Budget for the failing case.
Each arm now prints its own expected cost before it runs.

**Deviation from the instruction, stated.** The brief specified `delete_all`
on scope A. Implemented instead as the same **per-key** delete the adapter
performs (fetch scope, filter on metadata key, delete by id), because that is
what `012` actually does and what arms (a)-(c) do. Using `delete_all` would
have changed the delete mechanism *and* the scope at once, leaving (d)
uninterpretable against the (a) baseline. Say if you want it the other way.

### 3. Decisions

- **Interpretation fixed before running**, including the branch for (d)
  succeeding, which explicitly forbids reading that as "the original failure
  was spurious".
- **A cross-scope failure is flagged as gated-tier in advance.** If (d) fails
  where (a) succeeds, that is a coupling across scopes in a store sold on
  tenant isolation. The script says so and says it needs a founder ruling
  before it goes anywhere.
- **The `Arm` class keeps one delete mechanism across all five arms**, so the
  only variable that moves between (a) and (d) is the scope boundary.

### 4. FOR STRATEGY

- **An environment with egress is now the single blocker for all Mem0 work.**
  The credential is in place; the network is not. Everything else is staged
  and push-button.
- Unchanged: whether to pin `mem0ai` 2.0.14 for the regen, whether the extras
  should be exact pins, whether an open and contested #6017 changes the
  publication plan, and when to fix the `get_all` pagination limitation.
- Reminder that **the regen and any provenance edit are gated tier** and come
  to the founder labelled `needs-founder-review`, whichever way the run goes.

### 5. Next

1. Get to an environment with egress to `api.mem0.ai`.
2. Run the five arms, twice, before reading anything into them.
3. Full 15 × 2 regen on current `main` (~106 units), which doubles as the
   reproduction test: completing clean and aborting at 012 are both
   informative, and neither licenses editing published text without a ruling.

External launch remains **HELD**.

---
## 2026-08-03 — Arms (a)-(c) executed: clean sweep, and it settles less than it looks

**Corrects the entry above.** That entry states "Arms (a)-(c) have never been
executed". That was wrong. They ran on 2026-08-03 at 22:46:13 UTC, outside
this environment, on the founder's machine — twelve minutes before the (d)/(e)
commit `02b53bc` landed at 22:58:29 UTC, which is why the session writing that
entry had no sight of them. The claim is withdrawn; the reasoning built on it
is unaffected, and is in fact strengthened by the result below.

**Provenance of these figures: reported, not observed here.** Results live in
`diagnostics/results/readd_after_delete_1785797173.json`, which is gitignored
and on a different machine. This session did not read that file and cannot
verify it. Recorded as second-hand, per the promotion rule now in `CLAUDE.md`.

### 1. What shipped

Promotion of the run into this log, plus two consequences of it:
`diagnostics/readd_after_delete.py`'s STATUS block corrected (it still said
"NOT YET RUN", which was false and would have misled the next session), and a
new `CLAUDE.md` rule requiring out-of-environment runs to be promoted by hand.

### 2. Findings

**The run.** Three arms, same-scope, one execution:

| Arm | Outcome | Retrievable after | Reads | SEARCH units |
|---|---|---|---|---|
| `a_identical` | RE_ADD_VISIBLE | 0s | 4 | 5 |
| `b_varied` | RE_ADD_VISIBLE | 0s | 4 | 5 |
| `c_settle_then_identical` | RE_ADD_VISIBLE | 0s (after 60s settle) | 4 | 5 |

SEARCH remaining after the run: **~977**. Actual spend **~22** against a ~96
worst-case estimate.

**Read against the discrimination matrix**, which was published in the entry
above *before* these outcomes were known here:

| Simulated behaviour | (a) | (b) | (c) | consistent with observed? |
|---|---|---|---|---|
| healthy | visible | visible | visible | **YES** |
| content dedup, app-wide | LOST | visible | LOST | no — (a) would have failed |
| delete reaping, same-scope | LOST | visible | visible | no — (a) would have failed |
| cross-scope suppression, permanent | visible | visible | visible | **YES** |
| cross-scope suppression, transient | visible | visible | visible | **YES** |

**Ruled out:** content-level deduplication within an `app_id`, and same-scope
delete reaping. Both were live candidates in the 2026-07-27 hypothesis list;
both are now dead. That is real progress and it cost 22 units.

**Not ruled out, and this is the part that matters:** the observed
`visible / visible / visible` is the signature the matrix predicted would be
**ambiguous across three behaviours**. Two of the three surviving candidates
are cross-scope suppression — permanent and transient.

**State it plainly: the clean sweep is not a clean bill of health.** It is
exactly as consistent with a delete in one tenant's scope silently swallowing
a write to another tenant's scope as it is with the store being healthy. Arms
(a)-(c) cannot see the difference, because they never cross a scope boundary.
Nobody should read "all three passed" as "012 refuted", "Mem0 is fine", or
"the abort was spurious". The question `012` raised is **open**, and the
instrument that can close it — arms (d)/(e) — has not run.

**A near-miss on exactly that misreading.** The run predates commit `02b53bc`
by twelve minutes, so the console output the operator saw came from the *old*
interpretation guide: *"(a) succeeded -> the abort did not reproduce in
isolation."* Hedged about load and sequence, but silent on the untested
cross-scope condition. The rewritten guide now prints *"not reproduced
SAME-SCOPE ... 012 is a cross-scope rescope, which (a)-(c) do not exercise at
all. Read (d)/(e) before concluding anything."* The rewrite was not
hypothetical tidying — the ambiguous outcome it guards against is the one that
actually occurred.

**Cost model, now with a measured anchor.** Estimated worst case ~96 units,
actual ~22 — about 4.4x conservative. The gap is explained and expected: the
worst case is the *failing* case, where an arm polls to `CONFIRM_TIMEOUT`.
Every arm passed immediately (0s to retrievable, 4 reads each), so nothing
polled. Measured cost of a **passing** arm is ~5 units; budget ~29 for one
that fails. Applied to the unrun arms: (d)+(e) cost ~10 units if they pass and
~58 if both reproduce the abort. The estimator is sound; it is pessimistic by
design and should stay that way.

**One execution is not a result.** The script refuses to conclude from a
single run and so should we — the arms need a second execution before even the
two exclusions above are treated as stable.

### 3. Decisions

- **The prior claim was withdrawn in the open**, at the top of this entry,
  rather than quietly corrected in place. The previous entry is merged and
  public; editing it silently would leave the log disagreeing with itself.
- **STATUS block in the script corrected as part of this entry.** A file
  saying "NOT YET RUN" about arms that have run is the kind of stale marker a
  future session reasonably trusts.
- **Out-of-environment runs are now promotable by rule**, not by luck. Added
  to the Handoff protocol, including that promoted figures must be marked as
  reported rather than observed.

### 4. FOR STRATEGY

- **The exclusions do not unblock publication.** Two mechanisms are dead, but
  the surviving set still contains a cross-scope coupling, which is the most
  serious candidate on the list. Nothing about `012` should go external on the
  strength of this run.
- **Priority ordering suggestion, founder's call:** (d)/(e) are now the
  cheapest high-information spend available — ~10 units if they pass. They are
  worth running *before* the 106-unit regen, since a cross-scope finding would
  change how the regen's own 012 behaviour should be read.
- Unchanged: whether to pin `mem0ai` 2.0.14 for the regen, whether the extras
  should be exact pins, whether an open and contested #6017 changes the
  publication plan, and when to fix the `get_all` pagination limitation.

### 5. Next

1. Run (d)/(e) on a machine with egress — ~10 units if they pass.
2. Re-run (a)-(c) once more for stability, cheap at ~15.
3. Full 15 × 2 regen (~106 units), which doubles as the reproduction test.
4. Founder ruling before any of it touches published text — the regen and any
   provenance edit are gated tier.

External launch remains **HELD**.

---
