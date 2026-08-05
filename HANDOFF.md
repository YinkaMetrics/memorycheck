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

**Both runs are blocked. Egress is still refused.** ~~A Mem0 credential is now
present in the session (`~/.mem0/config.json`)~~ — **CORRECTED 2026-08-03: this
was wrong, there is no credential here; see the correction entry below** — and
`api.mem0.ai` remains blocked:

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
  ~~The credential is in place; the network is not.~~ **CORRECTED
  2026-08-03: neither is in place — see the correction entry below.**
  Everything else is staged
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
## 2026-08-03 — Run instrumentation: turning the 012 abort into a curve

### 1. What shipped

Per-scenario timing and cumulative operation count on `memorycheck run`, so a
future abort carries a latency trend rather than only a location. Plus the
standing caveat below, which is the more important half of this entry.

- `runner.py` — `OpTiming`, `ScenarioTiming`, `RunProgress`. Every adapter
  call is timed individually; rescope is timed as two ops (`rescope:del`,
  `rescope:add`) because the `012` abort is the *write* half and averaging it
  with the delete would blur the one number we are looking for.
- `cli.py` — live per-scenario progress to **stderr** (stdout stays the
  report), and a trace dump on `AdapterError`.

Two design points, both load-bearing for the abort case:

1. **Ops are timed in a `finally`**, so the operation that *causes* the abort
   is recorded with its duration. A naive record-on-success would drop
   precisely the most informative timing in the run.
2. **`RunProgress` is mutated in place and held by the caller**, so it
   survives the exception unwinding the suite. Same reason.

The no-memory baseline is deliberately **not** instrumented — it never touches
the provider, and its in-process timings would flatten the curve.

### 2. Findings

**Verified against a simulated aborting provider** — latency climbing with
cumulative ops, timing out on the 012 rescope write, no quota spent:

```
  scenario                           seed   ops    cum   elapsed   mean op    max op
  001-correction-stale-reuse            0     7      7      0.0s     0.00s     0.00s
  ...
  011-multi-user-same-tenant            1     5    120      0.1s     0.01s     0.01s
  012-rescope-then-readd                0     2    122      0.0s     0.01s     0.01s

  last operations before the abort:
  #121   012-rescope-then-readd    seed 0  step -1  reset     0.01s
  #122   012-rescope-then-readd    seed 0  step 0   write     0.01s  <-- FAILED HERE
```

The partial scenario is recorded despite the abort, the failing op is marked,
and the mean-op column carries the trend. Note the cumulative count at the
abort — **122 operations** — which matches the "~100 prior operations" the
original run had accumulated by the time it reached 012.

**`pytest -q`: 67 passed, 4 skipped.** One defect found and fixed during the
work: adapter `query` takes its own `seed=` keyword, which collided with the
timing helper's parameter of the same name. The helper's metadata parameters
are now positional-only.

**Deliberately not done: the timings are not in the JSON/MD evidence.** They
print to stderr only. Adding them to the report would touch `report.py`, which
is scoring path, immediately before a regeneration whose entire purpose is
reproducing published figures. Not worth confounding the regen for. Revisit
after it lands.

### 3. STANDING CAVEAT — what no arm can show

**Arms (a)-(e) test an isolated, freshly-scoped system.** Each runs a handful
of operations against brand-new `user_id`s with no accumulated state, no
concurrent load, and no backlog. The original `012` abort occurred roughly 120
operations into a full run, against scopes carrying everything that came
before them.

**If `012`'s mechanism is load-, backlog- or sequence-dependent, no arm will
reproduce it — and a clean sweep across ALL FIVE arms still would not close
the question.** Only a full 15 × 2 run recreates the original conditions.

This is now recorded in the diagnostic's own docstring as well, because that
is where the next person reading the arms will look, and a caveat that lives
only in the log is a caveat that gets missed.

It follows that the regen is **two things at once**, and both readings are
legitimate:

- the clean-provenance regeneration needed to unblock publication, and
- the only faithful reproduction attempt for `012`.

Neither outcome is a non-result. Completing clean gives provenance and says
012 did not reproduce under load; aborting at 012 again *is* the
characterisation that has been missing, because it establishes
load-or-sequence dependence — which is exactly what the isolated arms cannot
establish.

### 4. Decisions

- **(d)/(e) were not run this session.** Still no egress to `api.mem0.ai` from
  here; the instruction was to run them on the Mac, and that is where they
  have to happen. Nothing was spent.
- **The second stability run of (a)-(c) is deferred, not cancelled.** The two
  exclusions — content-level dedup, same-scope delete reaping — stand as
  **single-execution results** and are labelled as such wherever they appear.
  They are not yet stable findings.
- **Instrumentation before the regen, not after.** It is cheap, it cannot
  change a verdict (it only observes), and its entire value is realised on a
  run that aborts — so shipping it after the regen would waste the one run it
  was built for.

### 5. FOR STRATEGY

- Unchanged and still blocking: an environment with egress. (d)/(e) cost ~10
  units if they pass; the regen ~106.
- Unchanged: whether to pin `mem0ai` 2.0.14 for the regen, whether the extras
  should be exact pins, whether an open and contested #6017 changes the
  publication plan, and when to fix the `get_all` pagination limitation.
- **Reminder for whoever runs the regen:** the run now prints progress to
  stderr. If it is piped, capture stderr too (`2>&1 | tee`), or the latency
  curve — the whole point of this change — is lost precisely when it aborts.

### 6. Next

1. Run (d)/(e) on a machine with egress (~10 units if passing).
2. Full 15 × 2 regen on current `main` (~106 units), capturing stderr.
3. Second stability run of (a)-(c) (~15 units), deferred but owed.
4. Founder ruling before any of it touches published text — the regen and any
   provenance edit are gated tier.

External launch remains **HELD**.

---
## 2026-08-03 — Falsified abort message replaced; arms (f)/(g); credential claim corrected

### 1. What shipped

Three things, all internal tier.

| Change | File |
|---|---|
| Abort message: falsified trigger replaced with the evidenced one | `adapters/mem0.py` |
| Arms (f)/(g): the `delete_all` namespace condition | `diagnostics/readd_after_delete.py` |
| Credential claim corrected in two prior entries | `HANDOFF.md` |

### 2. Findings

**The abort message was asserting a falsified mechanism.** `write()` said:

> Known trigger: re-adding text identical to a value deleted moments earlier
> (see HANDOFF, scenario 012)

Three things now contradict that. The **2026-08-03 abort fired at
`003-scope-boundaries`, op #26** — reported, not observed here — with no
re-add of deleted text anywhere near it. `003` does not delete at all. And
arms (a)-(e) were built to test exactly that mechanism. Replaced with the
evidenced trigger: **a write issued after a `delete_all`, even when the
namespace was polled until it read empty**, citing `reset()`'s own measurement
of **6/14 writes lost following a delete_all versus 0/10 with no preceding
delete**. That measurement was in the codebase the whole time, one method
above the message that ignored it.

This mattered beyond tidiness: the message is what an operator reads at the
moment a run dies, and it was pointing them at a mechanism five arms had
already excluded.

**Arms (f)/(g) — and why (a)-(e) were the wrong instrument.** Every arm so far
uses a **per-key** delete (fetch scope, filter on metadata key, delete by id).
`reset()` does something categorically different: one
`delete_all(app_id=...)` wiping the whole namespace across every scope, then a
poll until empty, then return — after which the runner immediately writes.

That per-key substitution was **my call**, made when (d)/(e) were added: the
brief said `delete_all` and I used per-key to keep the delete mechanism fixed
across arms for comparability. Comparability was preserved and the mechanism
under investigation was not tested. It is a plausible reason all five passed.

- **(f)** seeds `_RESIDUE_N`=5 values so the namespace holds real residue,
  calls the same `delete_all(app_id=...)`, polls until the namespace reads
  empty, then writes and polls for retrievability.
- **(g)** identical, plus a 60s settle after the namespace reads empty.

Together they separate *"reads empty means propagated"* from *"reads empty but
still reaping"*.

**A design flaw in (f)/(g), caught by simulation before any spend.** The first
draft opened with a cleanup `delete_all` before seeding. Under the very
hypothesis being tested, that opening call would swallow the seed writes and
the arm would die in setup having measured nothing — the simulation returned
`SETUP_FAILED` for both reaping behaviours. Fixed by giving each namespace arm
a **fresh `app_id`**, so the namespace is empty by construction and the arm
contains exactly one `delete_all`: the one under test.

Validated offline against three known `delete_all` behaviours, spending
nothing:

| delete_all behaviour | (f) no settle | (g) 60s settle |
|---|---|---|
| clean | WRITE_VISIBLE | WRITE_VISIBLE |
| reaps, transient | **WRITE_LOST** | WRITE_VISIBLE |
| reaps, persistent | **WRITE_LOST** | **WRITE_LOST** |

In every simulated case the namespace **read empty before the write**, which
is the whole point: emptiness is what `reset()` currently trusts.

**Reading stated in advance.** If (f) fails and (g) passes, polling a
namespace until empty is not sufficient — and since that is precisely what
`reset()` does before returning, **every run is exposed**, with the first
writes after any reset liable to be reaped. That would be a harness-side
defect as much as a provider behaviour, and the fix is a settle sized from the
measured empty-to-safe gap, not a fixed sleep.

**Cost.** All seven arms now ~228 units worst case; (f)+(g) are ~66 of that,
~10-12 if they pass. Worst case remains the failing case.

**Credential claim corrected.** Two prior entries state a Mem0 credential is
present in this environment. **It is not.** `~/.mem0/config.json` is 57 bytes
containing a single `user_id` field — the Mem0 CLI's local identity file, not
an API key. There is no `platform.api_key`, so the script's `resolve_api_key()`
returns `None` from it, and `MEM0_API_KEY` is unset. I inferred the credential
from the file existing and never opened it. Both entries are struck through in
place and point here.

So **both** blockers are live, not one: no key, and no egress
(`connect_rejected api.mem0.ai:443`, re-verified). Either alone is sufficient
to stop every Mem0 run from this environment.

### 3. Decisions

- **Per-key delete is not substituted in (f)/(g).** Explicit instruction, and
  correct: the substitution is what made the earlier arms miss.
- **Fresh `app_id` per namespace arm**, so the arm holds exactly one
  `delete_all`. Adopted after the simulation showed the alternative aborts in
  setup under the hypothesis being tested.
- **The struck-through claims were left visible** rather than deleted. The
  entries are merged and public; silently rewriting them would hide that the
  log was wrong for several hours.

### 4. FOR STRATEGY

- **The `003` abort is a stronger data point than anything the arms have
  produced, and it is second-hand here.** Worth promoting properly: the
  results filename, the full latency curve from the new instrumentation, and
  whether it was the regen or a shorter run. Per the promotion rule, an
  unlogged run is invisible to the next session — right now this one is a
  single sentence.
- If (f)/(g) implicate `reset()`, the fix lands in `adapters/mem0.py` and
  **could flip provider findings**, so it is gated tier and needs a ruling
  before merge.
- Unchanged: pinning `mem0ai` 2.0.14 for the regen, exact pins for extras,
  whether #6017 changes the publication plan, and the `get_all` pagination
  limitation.

### 5. Next

1. Run (f)/(g) on the Mac — the leading hypothesis, ~66 units worst case.
2. Promote the `003` abort properly, with its curve and results filename.
3. Full 15 × 2 regen, capturing stderr for the latency curve.
4. Founder ruling before any of it touches published text.

External launch remains **HELD**.

---
## 2026-08-03 — reset() verifies writability instead of hoping (founder-instructed)

**Invariant 9 note, up front.** This change can convert phantom
`missing_current_fact` FAILs into passes — the guarded direction. It was
**instructed by the founder**, which is the sign-off invariant 9 requires
before merge, and this entry is the record of it. The test of good faith
named in the invariant holds: a genuine finding survives untouched, because
the sentinel only proves the namespace accepts writes again and is deleted
before any scenario value is written. Nothing about retrieval, supersession,
scoping or deletion behaviour is affected.

### 1. What shipped

`reset()` no longer treats "the namespace reads empty" as proof the
`delete_all` finished. When it actually deleted, it now proves the namespace
is writable again before returning.

- Write a sentinel under identifiers no scenario maps onto, poll for it,
  delete it, return.
- **Only on the delete path.** The skip path — namespace already empty, the
  overwhelmingly common case — is untouched and still costs nothing.
- If the namespace never accepts a write within the timeout, **raise**. A
  scenario is not started on a store that would swallow its first facts.
- **No blanket settle was added**, per instruction.

### 2. Findings

**One deliberate refinement of the brief: the sentinel retries.** The brief
said write a sentinel, poll, raise if it never lands. Implemented as a bounded
retry loop instead, because a single-shot sentinel is wrong in exactly the
case it exists for: a write swallowed by the still-reaping delete_all **never
becomes retrievable however long it is polled**. Polling harder cannot help;
only writing again can. A single-shot sentinel would therefore abort runs that
a second attempt moments later would have saved — trading a phantom FAIL for a
phantom abort.

Each attempt gets a short window (`_SENTINEL_ATTEMPT_TIMEOUT` = 5s); the loop
as a whole is bounded by `_CONVERGE_TIMEOUT` = 30s, so it cannot hang. The
attempt count is recorded and logged. Say if you want the strict single-shot
version instead — it is a two-line change.

**The measurement series, gathered free on every run.** Each reset that
deletes appends to `adapter.reset_convergence`:

```
{"app_id": ..., "empty_after_s": ..., "empty_to_writable_s": ...,
 "sentinel_attempts": ...}
```

and prints to stderr:

```
  [reset] mc_ns: namespace read empty after 2.5s, writable 1.5s later (2 sentinel attempt(s))
```

`empty_to_writable_s` is the number nobody has had: **how long a delete_all
keeps reaping after it stops being visible to search**. One value is an
anecdote; a run produces one per deleting reset, and the distribution is the
real characterisation. This makes arms (f)/(g) a confirmation rather than the
only source — and it collects data on runs that pass, not just ones that
break.

**Four tests added, 67 → 71 passing.** They use a `ReapingClient` whose
`delete_all` arms a window of swallowed writes *after* the namespace already
reads empty — the measured 6/14 behaviour:

- the sentinel absorbs the reaping window, so the scenario's first real write
  survives (without it, that write vanishes and is scored as a missing fact);
- no sentinel is left behind;
- the empty-to-writable gap is recorded, and is **not** recorded on the skip
  path;
- reset raises rather than starting a scenario against an unwritable
  namespace.

Building those tests caught a defect in my first fake: it armed the reaping
from construction rather than from `delete_all`, which is not the condition —
reaping is a consequence of the delete. Fixed before it could validate the
wrong thing.

**Sentinel cleanup uses a per-key delete by id, never a `delete_all`**, so the
cleanup cannot restart the condition it just cleared. Arms (a)-(c) measured
per-key delete followed by an immediate write landing at 0s, three for three,
which is the evidence for that choice.

**Residual risk, stated.** A sentinel that lands *after* cleanup has run would
persist. It sits under a tenant/user pair no scenario maps onto, so no
scenario query can retrieve it, and the next `reset()` of that namespace
removes it. It would, however, make the namespace read non-empty, which turns
a future skip-path reset into a delete-path one. Bounded and self-healing, but
worth knowing.

### 3. Decisions

- **Retry rather than single-shot**, for the reason above. Flagged as a
  deviation.
- **Verify only when we deleted.** Verifying on the skip path would add a
  write and several reads per scenario per seed for a condition that cannot
  arise — meaningful SEARCH spend for nothing.
- **Raise rather than continue** when the namespace stays unwritable. The
  alternative is scoring the provider on facts our own reset destroyed.

### 4. FOR STRATEGY

- **This does not close the `012`/`003` question**, and should not be read as
  doing so. It removes one harness-side mechanism by which a run can be
  killed or a provider mis-scored. Whether the aborts were that mechanism is
  still open — but the next run will now *measure* the gap rather than guess.
- The `003` abort still needs promoting properly, with its results filename
  and latency curve.
- Unchanged: pinning `mem0ai` 2.0.14 for the regen, exact pins for extras,
  whether #6017 changes the publication plan, `get_all` pagination.

### 5. Next

1. Run the regen or (f)/(g) on the Mac — either now yields the
   empty-to-writable series as a side effect.
2. Promote the `003` abort with its curve.
3. Founder ruling before any published text changes.

External launch remains **HELD**.

---

## 2026-08-04 — Founder approval recorded; arm (f) FAILED, the mechanism is real

### 1. What shipped

`fix(mem0): reset() proves the namespace is writable, never sleeps` — merged to
`main` as **`e7e89dc`** (PR #7, rebase).

**Founder approval, per the gated-tier mechanism.** PR #7 was labelled
`needs-founder-review` and held unmerged pending a ruling. Approved
2026-08-04 by the founder, verbatim:

> Approved. Retry loop is the right call — arm (f) showed a swallowed write
> never becomes retrievable (25 reads / 120s), so re-issuing is the only thing
> that can resolve it and single-shot would abort runs unnecessarily. Merge.

That closes the invariant 9 requirement: the change can convert phantom
`missing_current_fact` FAILs into passes, so it needed sign-off before merge,
and this is the record of it. The retry-vs-single-shot deviation flagged on the
PR was explicitly ratified.

### 2. Findings — **arm (f) FAILED**

**Reported, not observed here.** From the founder's approval message. This is
the first live result for the namespace arms.

| Arm | Outcome | Evidence |
|---|---|---|
| `f_namespace_delete_all` | **WRITE_LOST** | swallowed write never retrievable — 25 reads over 120s |

25 reads at the diagnostic's 5s poll interval is the full `CONFIRM_TIMEOUT`
window. The write was acknowledged and then never appeared, for two minutes.

**What this establishes.** Read against the arms already run:

| Delete mechanism | Arms | Result |
|---|---|---|
| per-key delete by id | (a), (b), (c) | all RE_ADD_VISIBLE, 0s |
| namespace `delete_all` | **(f)** | **WRITE_LOST, 120s** |

The mechanism is **specific to `delete_all`**. It is not content-level dedup
(ruled out by (b)), not same-scope delete reaping (ruled out by (a)/(c)) — it
is the namespace-wide delete, which is the one `reset()` performs and the one
no arm tested until now. The per-key substitution in (a)-(e) is confirmed as
the reason those arms all passed.

**It also directly validates the retry design.** Polling a swallowed write for
120s produced nothing. That is the empirical form of the argument made on the
PR before this result was known here: polling harder cannot resolve a
swallowed write, only re-issuing can. A single-shot sentinel would have turned
this condition into an aborted run.

### 3. Open — and one of these is a sizing risk

**(g) is not yet reported.** Without it the transient-vs-persistent question
stays open, and the pre-stated readings diverge sharply:

- (f) LOST + (g) VISIBLE → the reaping window is bounded and a settle clears
  it; the sentinel loop will converge.
- (f) LOST + (g) LOST → `delete_all` suppresses writes for longer than 60s,
  which would make it unusable as a reset primitive and would mean the
  sentinel loop cannot converge either.

**Sizing risk, flagged now rather than after a wasted run.** The shipped
sentinel loop is bounded by `_CONVERGE_TIMEOUT` = **30s**, with 5s per
attempt. Arm (f) shows the condition persisting for **at least 120s** when a
single write is polled. If the reaping window genuinely exceeds 30s, then
`reset()` will exhaust its budget and raise — the *safe* outcome, and far
better than silently losing facts, but it would abort runs at every reset that
deletes.

Re-issuing may well converge much sooner than 120s, since each new write is a
fresh attempt rather than a wait on a dead one — that is precisely the
difference the retry loop exploits, and (f) cannot measure it because it never
re-issues. **But it is unmeasured.** The `empty_to_writable_s` series now
emitted on every deleting reset is what sizes `_CONVERGE_TIMEOUT` honestly; if
attempts routinely run to the ceiling, raise it from data rather than guessing.

**Missing for the promotion rule.** No results filename was supplied for the
(f) run, so this entry cites none — contrary to the rule added on 2026-08-03.
Also unknown: whether (a)-(e) re-ran in the same execution, the SEARCH spend,
and (g)'s outcome. Please supply the `diagnostics/results/…json` filename so
the raw output can be found again.

### 4. Decisions

- **Merged on explicit approval**, quoted above, per the gated-tier mechanism.
  The instruction to build the change was not treated as approval to merge it.
- **`_CONVERGE_TIMEOUT` left at 30s for now.** Raising it on the strength of a
  number measured under a different procedure (single write, no re-issue)
  would be guessing. The series will size it.

### 5. Next

1. Report (g), and the (f) results filename.
2. Full 15 × 2 regen, capturing stderr — it now yields the
   `empty_to_writable_s` series, which sizes the sentinel budget.
3. Promote the `003` abort with its curve.
4. Founder ruling before any published text changes.

External launch remains **HELD**.

---
## 2026-08-04 — Citations promoted, (g) recorded: the reaping window is bounded

All figures below are **reported, not observed in-session** — promoted by hand
per the out-of-environment rule, now with the citations that entry was missing.

### 1. What shipped

`HANDOFF.md` citations and (g)'s result; `diagnostics/readd_after_delete.py`
STATUS block brought up to date (it still said (d)-(g) had not run).

### 2. Findings — the full seven-arm picture, with sources

| Arm | Outcome | Detail | Results file |
|---|---|---|---|
| (a) `a_identical` | RE_ADD_VISIBLE | 0s, 4 reads, 5 units | `readd_after_delete_1785797173.json` |
| (b) `b_varied` | RE_ADD_VISIBLE | 0s, 4 reads, 5 units | `readd_after_delete_1785797173.json` |
| (c) `c_settle_then_identical` | RE_ADD_VISIBLE | 0s after 60s settle, 4 reads, 5 units | `readd_after_delete_1785797173.json` |
| (d) `d_cross_scope_identical` | RE_ADD_VISIBLE | — | `readd_after_delete_1785800147.json` |
| (e) `e_cross_scope_settle` | RE_ADD_VISIBLE | — | `readd_after_delete_1785800147.json` |
| (f) `f_namespace_delete_all` | **WRITE_LOST** | never retrievable, 25 reads / 120s | `readd_after_delete_1785828208.json` |
| (g) `g_namespace_settle` | **WRITE_VISIBLE** | 0s after 60s settle, 3 reads, 4 units | `readd_after_delete_1785828208.json` |

**(f) LOST + (g) VISIBLE resolves the fork**, and it matches the branch stated
in advance for `reaps, transient`:

- The reaping window after a `delete_all` is **real** — polling a namespace
  until it reads empty is **not** sufficient, which is exactly what `reset()`
  did before `e7e89dc`.
- The window is **bounded** — a 60s settle cleared it completely, write
  retrievable at 0s.
- **`delete_all` stays usable** as a reset primitive, provided the caller
  verifies writability instead of trusting emptiness. That is what the
  sentinel now does, and this is the evidence it converges.

**Every per-key arm passed; the one `delete_all` arm failed.** Six arms across
three executions isolate the mechanism to the namespace-wide delete.

**Correcting my own framing from the previous entry.** That entry said arm (f)
shows "the condition persisting for at least 120s". That reads as a claim
about the window, and it is not one. (f) polls a *single already-swallowed*
write: such a write is dead permanently, so 120s of polling measures how long
we waited, not how long the store reaps. **(g) is the only bound on the
window, and it bounds it from above at 60s.** The lower bound is still
unmeasured.

**What that means for the sentinel budget.** `_CONVERGE_TIMEOUT` is 30s, 5s
per attempt. The window is somewhere in (0s, 60s]. If it typically sits under
30s the loop converges; if it sits above, `reset()` exhausts its budget and
raises — safe, but it would abort runs. **Still not enough information to size
it, and still not worth guessing at**: `empty_to_writable_s`, emitted on every
deleting reset since `e7e89dc`, measures the window directly and under the
retry procedure rather than the single-write one. First real run settles it.

**The `003` abort — terminal-reported, no results file.** Provenance:
`memorycheck run scenarios --adapter mem0 --seeds 2`, founder's Mac,
2026-08-03, terminal output only. Aborted at **op #26 —
`003-scope-boundaries` seed 0, `write`, 31.20s**. There is no
`diagnostics/results/` artefact for this one; it is cited as terminal-reported
rather than as a file, and should not be written up as though a JSON exists.

**An inference about op #26, marked as inference.** Operation counts are
structural — fixed by the scenario pack and step list, not by the provider. On
that ordering, `003-scope-boundaries` seed 0 occupies ops 25-29, with op 25
the `reset` and **op 26 the scenario's first write**. So the abort landed on
the first write immediately after a reset, which is precisely the (f)
condition. 31.20s is also just past the adapter's 30s write-convergence
ceiling, consistent with a write that was swallowed rather than merely slow.

For that reset to have deleted at all, the namespace must have held residue —
which happens when a namespace name recurs, e.g. re-running the suite after an
earlier aborted run. That fits, but it is **not measured**: the run predates
the `[reset]` logging, so nothing recorded whether that reset took the delete
path. The next run answers it directly.

### 3. Decisions

- **Citations added rather than left implicit.** Three files now traceable;
  the `003` abort explicitly marked as having none.
- **The previous entry's "at least 120s" phrasing corrected in this entry**,
  not edited in place — the entry is merged and public.
- **`_CONVERGE_TIMEOUT` still unchanged at 30s.** (g) bounds the window above
  at 60s but says nothing about the typical case, and the sentinel's retry
  procedure differs from the arm's. Size it from the series.

### 4. FOR STRATEGY

- **The harness-side story is now evidenced end to end**: `delete_all` reaps,
  the window is bounded, `reset()` verifies rather than assumes. What remains
  unproven is whether this mechanism *caused* the `012` and `003` aborts. The
  op #26 inference is suggestive and cheap to confirm on the next run.
- Unchanged: pinning `mem0ai` 2.0.14 for the regen, exact pins for extras,
  whether #6017 changes the publication plan, `get_all` pagination.
- **Nothing here is publishable yet** — one execution per arm, and the
  provider-facing claim would need the stability re-runs first.

### 5. Next

1. Full 15 × 2 regen, capturing stderr. It now yields `empty_to_writable_s`
   per deleting reset, which sizes the sentinel budget and confirms or kills
   the op #26 inference.
2. Stability re-runs of the arms before any of it is treated as settled.
3. Founder ruling before any published text changes.

External launch remains **HELD**.

---

## 2026-08-05 — External review fixes: the release gate now fails closed

### 1. What shipped

Implementation commit `b81bf20` addresses all five accepted review findings:

- `INCONCLUSIVE` is a gate verdict distinct from PASS and check-level
  NOT_TESTED. Paraphrasing clean-absence checks, unverified adapters, and runs
  where every metric is NOT_TESTED cannot go green; the CLI exits non-zero.
- `--seeds` rejects values below 1 at argument parsing, and the library guard
  enforces the same floor.
- the HTTP pilot confirms accepted writes and deletes by polling `/query` for
  the exact transition with a bounded timeout. `doctor` retains its own
  diagnostic polling so it can still distinguish bad response shape,
  non-convergence and soft-delete residue.
- Mem0 and LangGraph identifiers use reversible URL-safe base64 encoding.
  `tenant/a`, `tenant-a`, `tenant.a` and `tenant a` map to distinct namespaces.
- every suite invocation generates one unique `run_id`; it is included in
  every scenario and baseline namespace and stamped into JSON, Markdown and
  terminal report headers.

The README is touched, so this is a gated-tier change. The implementation and
handoff commits are pushed on `agent/fail-closed-release-gate`, but no PR was
opened: the publication gate correctly stopped before that external action
because the required live diagnostics and full regeneration have not run.
When those are complete, the PR must carry `needs-founder-review` and remain
unmerged until the founder approves the public wording.

### 2. Findings

No live provider behaviour was observed in this session.

Local verification after the fixes:

- all 15 scenarios validate with 0 warnings;
- offline suite: 87 passed, 4 credential-gated tests skipped;
- full local strict regeneration: 15 scenarios x 2 seeds, PASS, 0 blocking
  findings, report `run_id=4acd8fda77aa4c068928f63347bd92a3`;
- naive control: FAIL with 16 blocking findings at one seed, exit 1;
- `--seeds 0`: rejected by argparse, exit 2;
- eventually-consistent HTTP test: delayed write and delete both converge
  before the runner advances; timeout aborts instead of producing a score.

The requested live diagnostics and full Mem0 regeneration did **not** run.
`MEM0_API_KEY` is absent from this environment. A read-only reachability probe
outside the sandbox returned HTTP 200, so the blocker is the missing
credential, not current endpoint reachability. No published evidence artifact
was regenerated and no provider figure was changed.

The existing Mem0 findings are unaffected by these harness corrections: the
scenario-pack identifiers used in those runs do not slug-collide, the runs
used `seeds=2`, there was no concurrency, and the Mem0 answering layer quotes
stored values verbatim.

### 3. Decisions

- Exact violations still take precedence: a paraphrasing run with a literal
  matched violation is FAIL; otherwise absence cannot be evidenced and the
  gate is INCONCLUSIVE.
- HTTP convergence may inspect the shim's `retrieved` field to confirm a
  mutation arrived, including under paraphrase. The oracle remains unchanged
  on invariant 2 and grades only the separate scenario answer.
- Identifier encoding is centralised in `adapters/base.py` so native adapters
  cannot drift back to mutually incompatible slug rules.
- A fresh `run_id` is created at `run_suite`, not per scenario, so one report
  and all of its namespaces share the same invocation identity.

### 4. FOR STRATEGY

- Founder review is required for the README changes: the public honesty model
  now names INCONCLUSIVE, states that paraphrasing and unverified adapters exit
  non-zero, corrects Mem0 namespace isolation, and documents HTTP `/query`
  convergence.
- Publication remains held until the live diagnostics and full Mem0 15 x 2
  regeneration run in an environment with a credential. The existing choice
  of Mem0 SDK version/pin for that regeneration remains a founder call.
- There is currently no PR and therefore no `needs-founder-review` label. PR
  creation was attempted only after the branch push and was rejected by the
  publication safeguard because the live prerequisites are incomplete; it
  was not retried or bypassed.

### 5. Next

1. Supply `MEM0_API_KEY` in an egress-capable environment and rerun the
   diagnostics plus the full Mem0 15 x 2 regeneration, capturing stderr and
   report artifacts with their `run_id`.
2. Promote those results into this log and confirm the existing Mem0 metrics
   remain unchanged before altering any published evidence.
3. Open a draft PR, add `needs-founder-review`, and obtain founder approval;
   only then merge and publish to `main`.

External launch remains **HELD**.

---

## 2026-08-05 — Live diagnostics and full Mem0 regeneration completed

This entry supersedes the execution blocker in the preceding entry. The key
was already present in the Mem0 CLI config; the fresh clone simply did not
carry the gitignored credential file.

### 1. What shipped

Commit `d9da6de` promotes the completed live evidence:

- `examples/report_mem0.{json,md}` now carry the regenerated report, including
  run ID and answering-layer metadata;
- README provenance no longer says regeneration is pending;
- the diagnostic STATUS block records the second seven-arm execution and the
  intermittent result; and
- environment guidance now distinguishes default sandbox networking from an
  explicitly approved live-provider run.

Implementation remains `b81bf20`; `d25fa1b` recorded the temporary publication
hold before the configured credential was located.

### 2. Findings

**Seven-arm diagnostic.** Live Mem0, SDK 2.0.14, result file
`diagnostics/results/readd_after_delete_1785890141.json`:

| Arms | Result | SEARCH units per arm |
|---|---|---|
| (a)-(c), same-scope per-key delete | RE_ADD_VISIBLE | 5 each |
| (d)-(e), cross-scope per-key delete | RE_ADD_VISIBLE | 5 each |
| (f), namespace `delete_all`, immediate write | WRITE_VISIBLE | 4 |
| (g), namespace `delete_all`, 60s settle | WRITE_VISIBLE | 4 |

Arm (f) previously returned WRITE_LOST and now returned WRITE_VISIBLE. The
namespace-wide post-delete loss is therefore intermittent, not deterministic.
The clean rerun does not erase the earlier observed loss; it rules out framing
it as the outcome of every `delete_all`.

**Full regeneration.** Live Mem0, SDK 2.0.14, implementation `b81bf20`, 15
scenarios x 2 seeds, 170 operations, run ID
`1986edd5512147dca783bc513029b4f3`. Raw files:
`diagnostics/results/full_mem0_20260805.{json,md,log}`; promoted evidence:
`examples/report_mem0.{json,md}`.

| Check | Result |
|---|---|
| current_fact_accuracy | 100% (46/46) |
| stale_reuse | 100% (10/10) — FAIL |
| scope_leakage | 0% (0/22) |
| deletion_residue | 0% (0/18) |
| expiry_leak | NOT TESTED |
| memory_utility_delta | +1.00 |

Gate: **FAIL**, 10 blocking P2 findings. Seed stability: stable. Answering
layer: quoting. Both seeds of the previously aborting scenario 012 completed;
the largest recorded operation latency in the full-run progress output was
2.32s, below the 30s convergence ceiling.

The existing Mem0 findings are unchanged, as predicted: pack identifiers do
not slug-collide, the run used `seeds=2`, there was no concurrency, and the
Mem0 answering layer quotes exact stored values. Unique invocation namespaces
prevented this run from colliding with residue from an earlier invocation.

### 3. Decisions

- Pinned `mem0ai` 2.0.14 for like-for-like reproduction rather than silently
  changing the provider client and the harness in one measurement.
- Promoted the regenerated evidence because its metrics exactly match the
  published figures; no provider verdict or severity changed.
- Kept the earlier failed namespace diagnostic and the new passing execution
  side by side. Reporting only the clean rerun would hide intermittency;
  reporting only the loss would overstate determinism.

### 4. FOR STRATEGY

- The frequency and trigger for intermittent post-`delete_all` write loss are
  still unmeasured. Unique per-invocation namespaces remove that path from a
  normal fresh run; the sentinel remains the fail-closed guard when a reset
  actually encounters residue.
- README and published evidence are gated-tier changes. The PR requires
  `needs-founder-review` and must remain unmerged pending explicit approval.

### 5. Next

1. Push the complete branch.
2. Open a draft PR labeled `needs-founder-review`, stating the exact public
   claim and evidence changes.
3. Do not merge until founder approval is recorded.

External launch remains **HELD pending founder review**.

---

## 2026-08-05 — PR #10 merge and retrospective provenance review

### 1. What changed

GitHub read-back at this handoff found PR #10 already merged at 01:09 UTC at
head `a5aad4e`. The quota reconciliation and invariant-11 process rule are in
later commits on the follow-up branch.

### 2. Findings

The merge was an external state change, not an action taken in this task. Its
published 2026-08-05 evidence is **verified retrospectively by quota
reconciliation; provenance was not self-recorded at execution time — the
reason the rule now exists.** The founder independently read Mem0's live
SEARCH counter at 599 on 2026-08-05, against approximately 885 before the
work. Mem0 owns that counter outside the repository; repository code cannot
write it. No new provider call was made during this documentation review.

### 3. Decisions

- Do not rewrite, revert, or otherwise mutate the completed merge without
  explicit founder authority.
- Open the isolated post-merge documentation/process correction as a new draft
  PR from `agent/fail-closed-release-gate`, labeled `needs-founder-review`.
- Do not merge the follow-up PR automatically.

### 4. FOR STRATEGY

The 2026-08-05 evidence on `main` remains verified. The process gap is that
the run did not self-record its provenance contemporaneously; apply invariant
11 prospectively so the next run does not require retrospective reconciliation.

### 5. Next

1. Open and label the follow-up draft PR.
2. Confirm it retains the verified 2026 evidence and contains only provenance,
   accounting, cost and process-rule corrections.
3. Await explicit founder review; do not merge.

External launch remains **HELD pending founder review**.

---

## 2026-08-05 — Live-run quota reconciliation and provenance gate

### 1. What changed

- Added invariant 11: every live-provider execution must contemporaneously
  record `executed_by`, `environment`, SEARCH quota before, SEARCH quota after,
  and the exact results filename.
- A run missing any field is **reported, unverified**. It may be retained in
  this internal handoff, but it cannot update README, `examples/report_*`, an
  adapter verification flag, or another gated claim. This applies to runs
  executed after the rule's adoption.
- `diagnostics/readd_after_delete.py` now refuses a live execution without the
  executor and environment, writes the initial provenance record before any
  mutation, and records the quota pair and result filename in its JSON output.
- Retained the 2026-08-05 evidence in README and `examples/report_mem0.*`.
  It is verified retrospectively by the founder's independent live SEARCH
  counter reading; its provenance was not self-recorded at execution time.
- Corrected the documented current Mem0 cost from the historical ~106 estimate
  to a structural minimum of 91 SEARCH per seed, or at least 182 for 15 × 2,
  before extra convergence polls.

### 2. Quota accounting

The founder independently took a live Mem0 SEARCH counter reading of **599**
on 2026-08-05, against approximately **885 before the work**: a movement of
approximately 286 SEARCH units. Mem0's counter is external to this repository
and cannot be written by repository code. The surviving artifacts support this
reconciliation:

| Bucket | SEARCH units | Evidence and limit |
|---|---:|---|
| Seven-arm diagnostic | 41 | 33 in per-arm header deltas, plus 7 before-arm probes and the initial quota probe |
| Full 15 × 2 execution | at least 182 | structural minimum: 30 reset pre-reads, 60 write confirmations, 32 delete reads/confirmations, and 60 scenario queries |
| Unallocated before/around the first recorded checkpoint | at most 63 | arithmetic remainder; no complete run-specific quota pair exists, so this cannot be assigned more precisely |
| **Total** | **286** | 41 + 182 + up to 63 |

The diagnostic's first recorded provider checkpoint was **822 remaining**.
Its result file reports arm deltas `5, 5, 5, 5, 5, 4, 4`; those 33 units
include the seven after-arm probes, while the seven before-arm probes and the
initial probe bring the diagnostic to 41.

No surviving artifact evidences a repeat full 15 × 2 run in this work. The
full-run log contains no residue-reset/sentinel sequence because the unique
run namespace started empty; its 30 reset pre-reads are already included in
the 182 minimum. No separate post-checkpoint development run is evidenced.
Any extra convergence poll, standalone quota probe, or earlier development
call must therefore remain inside the unallocated remainder rather than being
invented as a specific cause.

### 3. Provenance status of today's live executions

**Seven-arm diagnostic**

```text
status: verified retrospectively by quota reconciliation; provenance not self-recorded at execution time — the reason the rule now exists
executed_by: Codex (OpenAI), on the founder's instruction
environment: Codex desktop on macOS; local writable clone; explicitly approved live network access
search_quota_before: approximately 885 before the combined work, recorded by the founder
search_quota_after: 599, independently read live from Mem0 by the founder on 2026-08-05
results_file: diagnostics/results/readd_after_delete_1785890141.json
```

**Full 15 × 2 execution**

```text
status: verified retrospectively by quota reconciliation; provenance not self-recorded at execution time — the reason the rule now exists
executed_by: Codex (OpenAI), on the founder's instruction
environment: Codex desktop on macOS; local writable clone; explicitly approved live network access
search_quota_before: approximately 885 before the combined work, recorded by the founder
search_quota_after: 599, independently read live from Mem0 by the founder on 2026-08-05
results_file: diagnostics/results/full_mem0_20260805.json (with matching .md and .log)
```

The combined live work is verified retrospectively by the independent provider
counter reconciliation. It qualifies as evidence; the missing self-recorded
run-level pair is a process deficiency and the reason invariant 11 now applies
prospectively.

### 4. FOR STRATEGY

- The 63-unit maximum remainder is closed as **unallocated**, not
  silently attributed to repeat, sentinel, or development runs that the
  artifacts do not prove. Do not spend more quota merely to reconstruct it.
- The next live run must begin only after the five-field provenance record is
  prepared and must finish with the provider-reported quota pair in the same
  results artifact. That single artifact should settle both evidentiary status
  and spend without a follow-up reconstruction.
- Existing Mem0 findings are unaffected: the scenario-pack identifiers do not
  slug-collide, the execution used `seeds=2`, there was no concurrency, and
  the Mem0 answering layer is quoting. The evidence remains verified and the
  metric values remain unchanged.
- README/report edits remain gated-tier and PR #11 must retain
  `needs-founder-review`; do not merge without explicit founder approval.

### 5. Next

1. Run offline validation only; do not spend additional Mem0 quota.
2. Push the corrected provenance rule and accounting to draft PR #11.
3. Confirm the PR stays draft, labeled `needs-founder-review`, and unmerged.

External launch remains **HELD pending founder review**.

---

## 2026-08-05 — Draft PR #10 opened; founder gate active

### 1. What shipped

Commits through `c112102` are pushed on
`agent/fail-closed-release-gate`. Draft PR
`https://github.com/YinkaMetrics/memorycheck/pull/10` targets `main` and carries
`needs-founder-review`.

### 2. Findings

No new provider measurement. The live diagnostic and full regeneration are
recorded in the preceding entry. GitHub read-back confirmed the PR is OPEN,
DRAFT and unmerged; both CI jobs started and were still in progress at the
handoff point.

### 3. Decisions

- Stopped at the gated publication boundary. Opening a draft for founder
  review is authorised; merging the README and published evidence changes is
  not.

### 4. FOR STRATEGY

- Founder approval is required before merge. Review should focus on the new
  INCONCLUSIVE public contract, corrected namespace claim, intermittent reset
  wording, and regenerated Mem0 provenance.

### 5. Next

1. Let CI finish and address any failure before approval.
2. Founder reviews PR #10 and records approval or requested changes.
3. Merge only after explicit approval; external launch otherwise stays held.

External launch remains **HELD pending founder review**.

---

## 2026-08-05 — Current publication state after PR #10 merge

### 1. What shipped

PR #10 merged at head `a5aad4e`. The later provenance correction is pushed at
`e270191` and is not in `main`.

### 2. Findings

No new provider measurement and no additional quota spend. The detailed
286-unit reconciliation and retrospective-verification provenance blocks are
in the entries above.

### 3. Decisions

Preserve the completed merge and route the prospective provenance rule and
customer-facing cost correction through a separate draft PR with
`needs-founder-review`; do not merge it automatically.

### 4. FOR STRATEGY

Treat the 2026-08-05 live evidence on `main` as **verified retrospectively by
quota reconciliation; provenance not self-recorded at execution time — the
reason the rule now exists.**

### 5. Next

Open the isolated follow-up draft, confirm its scope and gate, then await
explicit founder review.

External launch remains **HELD pending founder review**.

---

## 2026-08-05 — Follow-up PR #11 CI repair

### 1. What shipped

Draft PR #11 opened with `needs-founder-review`. Its first CI run failed only
because the new subprocess tests exercised the diagnostic in the base install,
while `httpx` was imported at module load despite being an optional Mem0-path
dependency.

### 2. Findings

The provenance checks themselves behaved correctly locally. CI failed before
reaching them with `ModuleNotFoundError: httpx`; no provider call was possible
and no quota was spent.

### 3. Decisions

Move the optional `httpx` import into the live quota-probe function. Dry-run
and fail-closed provenance validation must work in the base install, while an
actual Mem0 run continues to use the dependency supplied by the Mem0 extra.

### 4. FOR STRATEGY

This is a packaging boundary correction only. It does not alter the quota
reconciliation, provenance ruling, published metrics, or founder gate.

### 5. Next

Push the repair, confirm PR #11 CI passes, and leave the PR draft and unmerged.

External launch remains **HELD pending founder review**.

---

## 2026-08-05 — Founder confirms retrospective verification

### 1. What changed

Corrected HANDOFF, README, the published Mem0 report framing and PR #11 to
retain the 2026-08-05 evidence as verified. Removed the proposed evidence
rollback. Invariant 11 remains as a prospective self-recording requirement.

### 2. Findings

The evidence is **verified retrospectively by quota reconciliation; provenance
not self-recorded at execution time — the reason the rule now exists.** The
founder independently read Mem0's live SEARCH quota at **599** on 2026-08-05,
against approximately **885 before the work**. That approximately 286-unit
movement is consistent with 41 for the seven-arm diagnostic, at least 182 for
the full 15 × 2, and at most 63 unallocated. Mem0's counter is external to the
repository and cannot be written by repository code.

### 3. Decisions

- Keep the regenerated 2026-08-05 report and run ID
  `1986edd5512147dca783bc513029b4f3` as the current evidence.
- State the customer-facing full-run cost as **at least 182 SEARCH** under
  invariant 10, not the historical ~106 estimate.
- Require future live runs to self-record executor, environment, quota pair
  and exact results filename; retrospective verification is not the default
  operating procedure.

### 4. FOR STRATEGY

This correction changes provenance framing, not provider findings. Existing
Mem0 results remain unaffected: identifiers do not slug-collide, seeds=2, no
concurrency, and a quoting answering layer.

### 5. Next

Update draft PR #11, rerun offline validation and CI, retain
`needs-founder-review`, and do not merge without explicit approval.

External launch remains **HELD pending founder review**.

---
