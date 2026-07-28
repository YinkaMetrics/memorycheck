# Integration guide — test your own stack

You expose four small HTTP endpoints in front of your memory store and agent.
memorycheck drives the lifecycle through them and reports whether your agent
actually forgets, updates and scopes correctly. Everything runs inside your
network; no content leaves it.

Budget half a day. Most of that is decision 1 below, not code.

---

## 1. Copy a template

| Template | Use it if |
|---|---|
| [`fastapi_shim.py`](fastapi_shim.py) | you use FastAPI — start here |
| [`flask_shim.py`](flask_shim.py) | you use Flask |
| [`langgraph_shim.py`](langgraph_shim.py) | you want a known-good reference to diff against |

Each is standalone and runs before you wire anything up, so you can see green
before touching your own code:

```bash
pip install fastapi uvicorn
python examples/shim/fastapi_shim.py     # serves on :8808
```

## 2. The contract

Four POST endpoints, JSON in and out.

| Endpoint | Request | Must do |
|---|---|---|
| `/reset` | `{"namespace"}` | clear test state so each scenario starts clean |
| `/write` | `{"tenant_id","user_id","key","value","ttl_steps"?}` | store the value against that scope and key |
| `/delete` | `{"tenant_id","user_id","key"}` | make the value **unreachable from `/query`** |
| `/query` | `{"tenant_id","user_id","prompt","seed"}` | return `{"answer": str, "retrieved"?: [...]}` |

Only `answer` is graded, and it must be a **string**. `retrieved` is for your
own debugging and is deliberately never scored — the harness grades behaviour,
not what your retrieval claims it found.

A repeated `/write` to the same key is a **correction**. Afterwards the old
value must no longer drive answers.

## 3. The three decisions

**Decision 1 — how your tenant/user model maps onto scope.** Every call carries
`tenant_id` and `user_id`, and you must retrieve using **both**. Filtering on
tenant alone means every user inside a tenant shares memory. That is the most
common integration bug we see, `doctor` reports it as scope leakage, and it is
a P1. If you have one tenant, pass a constant; if your memory is per agent or
per session, map that onto `user_id`.

**Decision 2 — measure your store, or your agent.** The templates retrieve and
template a sentence, which exercises your store. To measure what you actually
ship, call your agent in `/query` instead:

```python
context = your_retrieval(tenant, user, prompt)
answer  = your_agent.respond(prompt, context=context)
return {"answer": answer}
```

Both are valid; they answer different questions. Note that the default judge
matches **exact values**, so an agent that paraphrases (`"the annual plan"`
instead of `"scale-annual-2026"`) reads as a miss. That is a known limit of the
deterministic judge, not a bug in your stack — start with the store, then move
to the agent once the store is clean.

**Decision 3 — TTL.** memorycheck's clock is logical: it asks you to age facts
by N *steps*, not to wait N seconds. Most stacks cannot, so set
`supports_ttl: false` and expiry checks report **NOT TESTED** rather than
passing. That is the honest setting. Implement `/advance_time` only if you can
genuinely age facts on demand — a no-op endpoint with `supports_ttl: true`
produces a green result that means nothing.

## 4. Run doctor first

```bash
memorycheck doctor --adapter http:examples/shim/config.yaml
```

It exercises each endpoint, checks the response shape, round-trips scope
isolation, confirms deletion really removes, and measures how long a write
takes to become readable so timeouts are sized from your stack:

```
  [ok  ] reset endpoint responds             reset accepted
  [ok  ] write endpoint accepts a fact       write accepted
  [ok  ] query returns an answer string      answer is a 57-char string
  [ok  ] a written fact reaches the answer   visible after 0.04s
  [ok  ] another user cannot see it          no cross-scope visibility
  [ok  ] another tenant cannot see it        no cross-scope visibility
  [ok  ] delete makes a fact unreachable     value no longer influences answers
  [ok  ] reset clears prior state            state cleared
  [skip] advance_time accepted               supports_ttl is false — expiry will report NOT TESTED
```

Every failure prints the exact fix. **Fix them all before running scenarios.**
A scenario run against a misconfigured shim produces findings that look like
memory bugs and are actually integration bugs — the most expensive kind of
false result, because it sends you hunting through your retrieval layer for a
defect the harness caused.

Exit code is 0 when clean, 1 when anything failed.

## 5. Run the pack

```bash
memorycheck run scenarios --adapter http:examples/shim/config.yaml --seeds 2
```

15 scenarios covering correction, deletion, delete-then-re-add, multi-key
interference, expiry and scope boundaries. Exits non-zero when the gate fails,
so it drops straight into CI:

```yaml
- name: Memory lifecycle gate
  run: memorycheck run scenarios --adapter http:memorycheck_http.yaml --seeds 3 --fail-on p2
```

Add `--report-md report.md` for evidence you can attach to a PR.

## 6. Reading a failure

| Check | Severity | What it means |
|---|---|---|
| `scope_leakage` | **P1** | another user's or tenant's value drove the answer |
| `deletion_residue` | **P1** | a deleted value drove the answer |
| `stale_reuse` | P2 | a superseded value drove the answer |
| `expiry_leak` | P2 | an expired value drove the answer |
| `missing_current_fact` | P2 | the current value failed to show up |

Findings name the scenario, step and the exact value that drove the answer, so
each one is reproducible on its own.

A note on what a clean run does and does not prove: deletion evidence covers
accessible retrieval and behavioural influence. It is not a claim that your
provider physically erased every backup, and it is not legal advice.

## Troubleshooting

**Everything fails after `reset`.** The shim is not reachable at `base_url`, or
it is not returning JSON. `doctor` stops at the first failure rather than
printing a cascade.

**`a written fact reaches the answer` fails.** Either retrieval is scoped
differently from the write, or the value is not appearing verbatim in the
answer text. Check the scope pair first.

**`another user cannot see it` fails.** Decision 1 — you are filtering on
tenant but not user.

**`delete makes a fact unreachable` fails.** You have a soft delete that your
retrieval ignores. That is exactly the P1 this tool exists to catch, so the
check is working; the fix is in your retrieval filter.

**Convergence takes seconds.** Normal for an eventually consistent store.
`doctor` prints a suggested timeout; the harness polls for each write rather
than assuming it landed.
