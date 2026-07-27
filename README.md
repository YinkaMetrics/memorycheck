# memorycheck

**A behavioural release gate for agent memory.** It exercises the full memory lifecycle — write → correct → rescope → expire → delete — and proves whether each operation actually changed your agent's *behaviour*, not just whether the store returned 200.

Provider-neutral. Local-first: runs inside your infra, raw content never leaves it. Zero model dependency out of the box.

## Why

When a user corrects a fact or invokes deletion, your memory store says success. That tells you nothing about whether the agent's next answer still relies on the superseded or deleted fact. Every write path in a memory stack can return 200 while the agent keeps using stale facts, resurfacing deleted ones, and crossing user boundaries.

`memorycheck` tests the contract that matters:

| Lifecycle moment | Failure that matters |
|---|---|
| Write | fact stored but never surfaces in behaviour |
| Correct | the obsolete fact still drives the answer (**stale reuse**) |
| Rescope | the old scope can still see it (**leakage / residue**) |
| Expire | TTL passed, fact still active (**expiry leak**) |
| Delete | content still influences answers (**deletion residue**) |
| Scope | one user's or tenant's facts drive another's answers (**scope leakage**) |

Plus a check that prevents a false pass: the **memory utility delta** compares against a no-memory baseline, so a system can't pass the lifecycle by simply never remembering anything.

## Quickstart (60 seconds, no external services)

```bash
pip install -e .
memorycheck run scenarios --adapter reference:naive
```

The built-in reference adapter has three modes. `strict` honours the lifecycle. `naive` is the classic broken implementation — retrieval ignores the superseded flag, soft-deletes and TTL. `leaky` also filters by tenant only. **Every mode returns success on every write and delete.**

```
memorycheck 0.1.0 — adapter=reference:naive judge=deterministic-v0 scenarios=15 seeds=1
  current_fact_accuracy  100% (23/23)
  stale_reuse            100%  (5/5)
  scope_leakage          0%  (0/11)
  deletion_residue       100%  (9/9)
  expiry_leak            100%  (2/2)
  memory_utility_delta   +1.00

  GATE [fail on <= P2]: FAIL (16 blocking findings)
```

Note what happened: current-fact accuracy is perfect — the system "works" in every demo you'd run by hand — and it is simultaneously running at 100% stale reuse and 100% deletion residue. That gap is the product surface storage status codes cannot see.

Swap `reference:naive` for `reference:strict` and the gate passes with the same scenarios. Point it at your own stack with the [HTTP adapter](#your-stack-the-http-shim).

## Scenarios

Scenarios are small YAML files. Ground truth is owned by a deterministic state machine (the ledger) — never by a model, never by the system under test.

```yaml
id: "001-correction-stale-reuse"
title: "A corrected fact must replace the stale one"
subject: { tenant_id: acme, user_id: alice }
steps:
  - op: write
    key: plan
    value: starter-legacy-2024
  - op: correct              # supersedes the previous value
    key: plan
    value: scale-annual-2026
  - op: query
    prompt: "Which plan is this user on?"
    expect: { must_use: [plan] }
  - op: delete
    key: plan
  - op: query
    prompt: "Which plan is this user on?"
```

Ops: `write` (`key`, `value`, optional `ttl_steps`), `correct`, `delete`, `rescope` (`key`, `to:`), `advance_time` (`steps` — time is logical and explicit), `query` (`prompt`, optional `expect.must_use`, optional `as:` scope override).

Lifecycle invariants are enforced on **every** query whether or not you ask: no stale, expired, deleted or foreign value may drive an answer. `must_use` adds the positive requirement. Keep fact values distinctive — `memorycheck validate` warns when they aren't.

The bundled pack is 15 scenarios covering correction and double-correction, deletion, delete-then-re-add, re-add-then-correct, multi-key interference, TTL expiry with a non-expiring sibling, rescope (including rescope-then-re-add and cross-tenant moves), and scope boundaries across users, across tenants, and where two tenants share a `user_id`.

## Checks and severities

| Check | Severity | Meaning |
|---|---|---|
| `scope_leakage` | **P1** | another user's or tenant's value drove the answer |
| `deletion_residue` | **P1** | a deleted value drove the answer |
| `stale_reuse` | P2 | a superseded value drove the answer |
| `expiry_leak` | P2 | an expired value drove the answer |
| `missing_current_fact` | P2 | the current value failed to show up in behaviour |

`--fail-on p2` (default) blocks on all of the above; `--fail-on p1` blocks only on the privacy/security class. Rates are reported as violations/opportunities, with counts.

## Your stack: the HTTP shim

Expose four small POST endpoints in front of your memory store + agent (typically <100 lines in your codebase) — `reset`, `write`, `delete`, `query` — and run:

```bash
memorycheck run scenarios --adapter http:memorycheck_http.yaml
```

The contract is documented in [`src/memorycheck/adapters/http.py`](src/memorycheck/adapters/http.py), and `tests/test_http_adapter.py` is a working reference server. Set `supports_ttl: false` honestly: expiry checks will report **NOT TESTED** rather than silently passing.

The adapter contract is deliberately small (`write / delete / query / reset`) and needs no read API. Native adapters for **Zep and LangGraph stores** are next on the roadmap.

## Mem0

```bash
pip install -e ".[mem0]"
export MEM0_API_KEY=...
memorycheck run scenarios --adapter mem0
```

Mem0 is a memory *store*, not an agent, so the adapter supplies the answering layer itself: a query runs a scoped Mem0 `search` and templates the results into an answer exactly the way the reference adapter does. That keeps the measurement pointed at the thing being tested — **whether Mem0's retrieval still surfaces superseded, deleted, or foreign memories** — rather than at an LLM's phrasing.

How the lifecycle maps onto Mem0:

| memorycheck | Mem0 |
|---|---|
| scope (`tenant_id`/`user_id`) | folded into one `user_id`, prefixed with the run namespace so runs never collide |
| `write` | `add("<key>: <value>", metadata={"key": …}, infer=False)` |
| `delete` | find that scope's memories carrying the metadata key, delete each by id |
| `reset` | `delete_all` over the namespace's `app_id` |
| TTL | **not supported** — `supports_ttl = False` |

### Result

**Run:** Mem0 hosted platform via `api.mem0.ai` (`/v3` endpoints), SDK `mem0ai` **2.0.14** (latest release at time of run, published 2026-07-25), executed **2026-07-27 15:58 UTC**, 15 scenarios × 2 seeds, judge `deterministic-v0`. The hosted platform exposes no version or build identifier to clients, so the run date is the only pin available on the service itself. Three consecutive runs — two on 2.0.14, one on 2.0.11 — produced identical figures.

```
  current_fact_accuracy  100% (46/46)
  stale_reuse            100%  (10/10)
  scope_leakage          0%  (0/22)
  deletion_residue       0%  (0/18)
  expiry_leak            NOT TESTED
  memory_utility_delta   +1.00

  GATE [fail on <= P2]: FAIL (10 blocking findings)
```

Mem0 holds the boundaries that carry the P1 severities: **no scope leakage in 22 opportunities and no deletion residue in 18** — deletes stopped the value influencing answers, and no user's or tenant's facts crossed into another's, including where two tenants share a `user_id` and where a fact is moved between tenants. Current-fact accuracy is perfect and every result is stable across seeds.

#### What this result is, and what it is not

**It is not a report that Mem0 fails to overwrite corrected facts.** That is Mem0's documented, intended design. Its README, under *"New Memory Algorithm (April 2026)"*, states:

> "Single-pass ADD-only extraction -- one LLM call, no UPDATE/DELETE. Memories accumulate; nothing is overwritten."

Accumulation is the design. A benchmark that flagged accumulation as a defect would just be reporting that it had not read the README.

The claim this result tests is the one made two bullets away in the same section:

> "Temporal Reasoning -- time-aware retrieval that ranks the right dated instance for queries about current state, past events, and upcoming plans."

**The finding: retrieval did not change which value drove the answer at any correction site.** Given a query about current state — "Which plan is this user on?" — the superseded value was returned alongside the current one and continued to influence the answer, in **10/10 correction opportunities, stable across both seeds**, at every correction site in the pack (`001`, `007` at both of its corrections, `008`, `013`). We did not observe dated-instance ranking altering the outcome for these queries.

The distinction matters because ADD-only storage is only a problem for an agent if the read path cannot resolve the accumulation. Storing everything and ranking correctly at query time is a coherent design; this measures whether the second half of it changed the behavioural outcome, which is the only part an agent's answer depends on.

Three details that close the obvious objections:

- **Not an artifact of bypassing extraction.** Checked directly against the API, the superseded value survives a correction under **`infer=True` as well** — the extraction pipeline rephrases the text but keeps both records and returns both.
- **Not general retrieval noise.** Correcting one key left its siblings untouched (`008`), so this is specific to superseded values.
- **Not the same mechanism as deletion.** `013-readd-then-correct` puts a deleted value, a re-added value and a correction on one key: **the deleted value stayed gone while the superseded one came back.** Deletion is enforced on the read path; supersession resolution was not observed to be.

Mem0's docs describe no contradiction-resolution mechanism beyond the temporal-reasoning claim above, so recency across accumulated values is left to the caller. If your agent corrects facts, that resolution is yours to implement at retrieval time.

**Check it yourself.** [`examples/repro_correction.py`](examples/repro_correction.py) reproduces this in about 40 lines of Mem0 SDK calls with no memorycheck dependency — write a fact, correct it, ask a current-state question, print what comes back:

```bash
pip install "mem0ai>=2.0.14"
export MEM0_API_KEY=...
python examples/repro_correction.py
```

Real output from that script is pasted at the bottom of the file. It waits for the correction to become visible before judging anything: with `infer=True` the extraction pipeline took ~15s, and querying earlier returns the superseded value *alone* — a much more dramatic-looking result that is purely an artifact of racing the pipeline. Full suite evidence in [`examples/report_mem0.md`](examples/report_mem0.md).

Two honest caveats:

- **`infer=False` is deliberate.** Mem0's default `infer=True` sends writes through an extraction LLM that rewrites them, which would make the deterministic judge unable to match the exact value. Storing verbatim keeps the measurement about retrieval, not extraction — but it does mean this benchmark exercises Mem0-as-store, not Mem0's inference pipeline.
- **Expiry reports NOT TESTED, by design.** Mem0's expiry is wall-clock; memorycheck's time is logical. Rather than fake a pass with `sleep`, the adapter declares the capability absent and the report says so.
- **Mem0 deletes asynchronously.** A write issued immediately after a `delete_all` can be swallowed by the in-flight delete — we measured 6/14 writes lost that way, versus 0/10 with no preceding delete. Early runs of this adapter blamed Mem0 for "losing" current facts that the harness had itself just deleted. `reset()` now skips the delete when the namespace is already empty and waits for it to drain when it isn't. Worth knowing if you write your own adapter: a false FAIL against a provider costs a gate its credibility as surely as a false PASS.

## Zep

```bash
pip install -e ".[zep]"
export ZEP_API_KEY=...
memorycheck run scenarios --adapter zep
```

> **Status: unverified against the live service.** The adapter is written
> against the real `zep-cloud` 3.25.0 SDK surface and is exercised offline
> through the full 15-scenario suite with a fake client, but it has never been
> run against Zep itself — no credential was available. **There are no Zep
> results to report, and the assumptions below are untested.** Treat it as a
> starting point, not a measurement.

Zep is a temporal knowledge graph rather than a flat store, which changes the
mapping:

| memorycheck | Zep |
|---|---|
| scope (`tenant_id`/`user_id`) | its own `graph_id`, prefixed with the run namespace — isolation is structural, not a filter |
| `write` | `graph.add(type="text", data="<key>: <value>")` |
| `delete` | delete the key's source episodes **and** the edges derived from them |
| `query` | `graph.search(scope="edges")`, skipping edges Zep marks invalid or expired |
| `reset` | delete every graph under the namespace prefix |
| TTL | **not supported** — `supports_ttl = False` |

Two decisions worth arguing with:

- **The adapter honours Zep's own invalidation signal.** `EntityEdge` carries
  `valid_at` / `invalid_at` / `expired_at` — Zep's published claim about which
  facts are still live — and edges it marks dead are not templated into the
  answer. So this tests whether Zep's invalidation is *correct*, not whether a
  caller who ignores it gets burned. **Mem0 exposes no equivalent signal**, so
  its adapter cannot filter this way. If Zep scores better here, the honest
  reading is "Zep gives integrators a liveness signal and it was accurate" —
  not "Zep beat Mem0 on an identical test". Different providers expose
  different contracts, and a single leaderboard number would hide that.
- **Deletes remove derived edges, not just episodes.** Dropping the raw
  episode while leaving extracted facts standing would let the value keep
  driving answers — measuring our own laziness rather than Zep's behaviour.

## CI gate

`memorycheck run` exits non-zero when the gate fails:

```yaml
- name: Memory lifecycle gate
  run: memorycheck run scenarios --adapter http:memorycheck_http.yaml --seeds 3 --fail-on p2
```

Evidence artifacts: `--report-json` and `--report-md` (see [`examples/`](examples/) for generated reports from all three reference modes).

## Honesty model

An assurance tool that overclaims destroys its own value, so:

- **PASS / FAIL / NOT TESTED** — a check with no opportunities, or one the adapter can't express (e.g. TTL), is reported NOT TESTED, never passed.
- **The judge is deterministic in v0** (normalised value matching): high precision, known recall limit — it will not catch *paraphrased* reliance on a fact. A semantic LLM judge slots in behind the same interface, but ships only after a calibration protocol: ≥200 human-labelled examples with ≥90% precision on release-blocking classes. Until then the harness will not block your release on a model's opinion.
- **Deletion evidence covers accessible retrieval and behavioural influence.** It is not a claim that a provider physically erased every backup, and it is not legal advice.
- Multi-seed runs (`--seeds N`) surface flaky checks explicitly in the report rather than averaging them away.

## Status

v0.1 — built in public. Scenario format and adapter contract may still change; pin the version in CI. Issues and real-world failure reports are very welcome, especially anonymised reproductions of lifecycle bugs from production memory stacks.

MIT licensed.
