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
memorycheck 0.1.0 — adapter=reference:naive judge=deterministic-v0 scenarios=5 seeds=2
  current_fact_accuracy  100% (12/12)
  stale_reuse            100%  (2/2)
  scope_leakage          0%  (0/8)
  deletion_residue       100%  (6/6)
  expiry_leak            100%  (2/2)
  memory_utility_delta   +1.00

  GATE [fail on <= P2]: FAIL (10 blocking findings)
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

Native adapters for **Mem0, Zep and LangGraph stores** are the current roadmap; the adapter contract is deliberately small (`write / delete / query / reset`) and needs no read API.

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
