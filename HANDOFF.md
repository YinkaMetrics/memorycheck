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
