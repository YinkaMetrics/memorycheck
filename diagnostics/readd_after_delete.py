#!/usr/bin/env python3
"""Discriminating experiment: why is a re-added value not retrievable?

STATUS. All seven arms have now RUN, outside this environment (sandboxed
sessions cannot reach `api.mem0.ai` — 403 at CONNECT; see CLAUDE.md,
Environment notes). Results, one execution each:

  (a)-(c)  RE_ADD_VISIBLE, 0s   `readd_after_delete_1785797173.json`
  (d)-(e)  RE_ADD_VISIBLE       `readd_after_delete_1785800147.json`
  (f)      WRITE_LOST, 120s     `readd_after_delete_1785828208.json`
  (g)      WRITE_VISIBLE, 0s    (same file, after a 60s settle)

**The result: (f) failed and (g) passed.** Every per-key arm passed; the one
arm using `delete_all` — the delete `reset()` performs — lost its write, and
a 60s settle cleared it. So the reaping window after a `delete_all` is REAL
and BOUNDED: polling a namespace until empty is not sufficient, but the store
does become writable again. `delete_all` stays usable as a reset primitive
provided the caller verifies writability rather than trusting emptiness.

Note what (f) does and does not measure. A write swallowed by the reaping
`delete_all` is dead permanently — 120s of polling never resurrected it. That
bounds nothing about the WINDOW; it says a lost write stays lost. (g) is what
bounds the window, from above, at 60s. The lower bound is unmeasured, which
is why `reset()` measures `empty_to_writable_s` on every deleting reset
rather than assuming a figure. No finding is claimed here and none should be
quoted from this file. See HANDOFF.md.

STANDING CAVEAT — WHAT NO ARM CAN SHOW. Every arm here runs against an
isolated, freshly-scoped, otherwise-idle system: a handful of operations
against brand-new `user_id`s with no accumulated state. The original `012`
abort happened ~120 operations into a full run, against scopes carrying the
backlog of everything before them. If the mechanism is load-, backlog- or
sequence-dependent, **no arm in this file will reproduce it**, and a clean
sweep across ALL FIVE arms still would not close the question. Only a full
15 x 2 run recreates the original conditions.

Background. A full 15 x 2 run aborted on `012-rescope-then-readd`, where the
runner deletes a key from one scope and immediately writes the *same value*
into another. The write was acknowledged and then not retrievable for 30s.
Whether that is a Mem0 behaviour or an artifact of how this harness sequences
delete-then-write is exactly what is unresolved.

SAME-SCOPE ARMS (a)-(c). All three delete and re-add under **one** `user_id`:

  (a) delete, then re-add IDENTICAL text
      Baseline for the same-scope case.

  (b) delete, then re-add VARIED text
      Isolates content-level deduplication. If (a) fails and (b) succeeds,
      the blocker is tied to the content being identical.

  (c) delete, poll until search reads empty, WAIT an additional 60s,
      then re-add IDENTICAL text
      If (a) fails and (c) succeeds, the finding is that *polling until empty
      is insufficient* — deletion keeps reaping writes after it has stopped
      being observable through search. That would mean no amount of "confirm
      the delete landed" is sufficient, and hits any customer doing
      delete-then-re-add.

CROSS-SCOPE ARMS (d)-(e). **These are the actual `012` condition.**

`012-rescope-then-readd` deletes a key from one scope and writes the same
value to a *different* scope: `delete(ivor, handover-note)` then
`write(jonas, handover-note, <same text>)`. Arms (a)-(c) never cross a scope
boundary, so **they cannot confirm or rule out the leading hypothesis** —
they were built to test it and do not. That gap is why (d) and (e) exist.

  (d) delete the key from scope A, poll until A reads empty, then IMMEDIATELY
      write the SAME text to scope B (different `user_id`, same `app_id`)
      The real condition. Poll for retrievability under B.

  (e) as (d), but WAIT an additional 60s before writing to B
      Separates propagation timing from the cross-scope factor itself.

NAMESPACE ARMS (f)-(g). **The leading hypothesis, and the only arms that use
the delete the harness actually performs.**

Arms (a)-(e) all use a PER-KEY delete: fetch the scope, filter on the metadata
key, delete by id. `reset()` does something different — a single
`delete_all(app_id=...)` that wipes the whole namespace across every scope.
The 2026-08-03 abort at `003-scope-boundaries` (op #26) followed a `reset()`,
with no re-add of deleted text anywhere near it, which per-key arms cannot
model. Substituting per-key for delete_all is very likely why (a)-(e) passed.

  (f) write N values so the namespace holds real residue, call the SAME
      `delete_all(app_id=...)` that `reset()` calls, poll until the namespace
      reads empty, then write and poll for retrievability
      The evidenced condition. `reset()`'s own docstring records 6/14 writes
      lost after a delete_all versus 0/10 without one.

  (g) as (f), but WAIT an additional 60s after the namespace reads empty
      Separates "reads empty means propagated" from "reads empty but the
      delete is still reaping". If (f) fails and (g) passes, then polling a
      namespace until empty — which is exactly what `reset()` does before
      returning — is NOT sufficient, and every run is exposed.

Read the arms together and across the pair boundaries. (a) alone says nothing;
the informative comparisons are (a) vs (b), (a) vs (c), (a) vs (d), (d) vs (e),
and — now the important one — (a)-(e) vs (f)/(g), which is per-key delete
versus namespace delete_all. In particular a clean sweep of (a)-(c) does
**not** license "012 refuted" — it licenses only "not reproduced same-scope,
with a per-key delete".

    python diagnostics/readd_after_delete.py             # prints cost, then asks
    python diagnostics/readd_after_delete.py --yes       # no prompt
    python diagnostics/readd_after_delete.py --dry-run   # cost only, spends nothing

The key is read from MEM0_API_KEY, falling back to ~/.mem0/config.json, so no
export is needed. Results are written to diagnostics/results/ as well as
printed, because a transcript is easy to lose and this run is not cheap to
repeat.

SEARCH is the scarce counter, so the script prints its estimated cost before
spending anything and reports actual usage per arm from response headers.
Poll interval is deliberately coarse (5s, not the adapter's 0.5s) because each
poll is a metered SEARCH call; a failing arm at 0.5s would burn ~60 units on
its own, which is how the original investigation exhausted the quota.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

POLL_INTERVAL = 5.0     # seconds between reads; each read costs one SEARCH unit
CONFIRM_TIMEOUT = 120.0  # ceiling on waiting for a value to become retrievable
EXTRA_SETTLE = 60.0     # arm (c): wait this long AFTER the delete reads empty

APP_ID = "mc_diag_readd"
KEY = "handover-note"

# How many values (f)/(g) seed before wiping, so the namespace holds real
# residue rather than a single row. A delete_all over one memory is not the
# condition a run hits — `reset()` fires when a namespace has accumulated.
_RESIDUE_N = 5

# Worst-case SEARCH reads per arm: 1 pre-read + write-confirm polls
# + 1 delete lookup + delete-confirm polls + re-add-confirm polls.
#
# These are WORST cases, and the worst case is the *failing* one: an arm whose
# re-add never lands polls all the way to CONFIRM_TIMEOUT. A sweep where every
# arm succeeds promptly costs far less — roughly 7-8 reads per arm. So (d)+(e)
# together are ~15 units if they behave and ~58 if they reproduce the abort;
# budget for the latter, and be pleasantly surprised.
_MAX_POLLS = int(CONFIRM_TIMEOUT / POLL_INTERVAL)
COST_ESTIMATE = {
    "a_identical": 2 + _MAX_POLLS + 1 + _MAX_POLLS,
    "b_varied": 2 + 2 + 1 + 4,
    "c_settle_then_identical": 2 + 2 + 1 + _MAX_POLLS,
    "d_cross_scope_identical": 2 + 2 + 1 + _MAX_POLLS,
    "e_cross_scope_settle": 2 + 2 + 1 + _MAX_POLLS,
    # (f)/(g) seed N values, so add N confirm reads to the usual shape.
    "f_namespace_delete_all": 2 + _RESIDUE_N + 2 + _MAX_POLLS,
    "g_namespace_settle": 2 + _RESIDUE_N + 2 + _MAX_POLLS,
}


def resolve_api_key() -> str | None:
    """Environment first, then the Mem0 CLI's own config, so Saturday needs no
    export. The value is never printed."""
    key = os.environ.get("MEM0_API_KEY")
    if key:
        return key
    cfg = Path.home() / ".mem0" / "config.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text()).get("platform", {}).get("api_key") or None
        except Exception:  # noqa: BLE001
            return None
    return None


def quota_remaining(api_key: str) -> int | None:
    """Read the SEARCH counter. Costs one SEARCH unit itself."""
    try:
        r = httpx.post(
            "https://api.mem0.ai/v3/memories/",
            headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
            json={"filters": {"user_id": "mc_diag__quota_probe"}},
            timeout=30,
        )
        return int(r.headers.get("x-quota-remaining", -1))
    except Exception:  # noqa: BLE001
        return None


class Arm:
    def __init__(self, client, name: str, user_id: str,
                 target_user_id: str | None = None, app_id: str = APP_ID):
        self.c, self.name, self.uid = client, name, user_id
        # Per-arm namespace for (f)/(g): a fresh `app_id` starts empty by
        # construction, so the arm contains exactly ONE delete_all — the one
        # under test. An opening cleanup delete_all would, under the very
        # hypothesis being tested, swallow the seed writes and abort the arm
        # in setup before it measured anything.
        self.app_id = app_id
        # Where the re-add goes. Same scope for (a)-(c); a different `user_id`
        # under the same `app_id` for the cross-scope arms (d)-(e), which is
        # what the 012 rescope actually does.
        self.target_uid = target_user_id or user_id
        self.reads = 0

    @property
    def cross_scope(self) -> bool:
        return self.target_uid != self.uid

    # -- primitives ---------------------------------------------------------

    def _rows(self, uid: str | None = None) -> list[dict]:
        self.reads += 1
        resp = self.c.get_all(filters={"user_id": uid or self.uid})
        return resp.get("results", []) if isinstance(resp, dict) else (resp or [])

    def _visible(self, value: str, uid: str | None = None) -> bool:
        return any(value in (r.get("memory") or "") for r in self._rows(uid))

    def _wait_until(self, predicate, timeout: float) -> tuple[bool, float]:
        started = time.monotonic()
        while True:
            if predicate():
                return True, time.monotonic() - started
            if time.monotonic() - started >= timeout:
                return False, time.monotonic() - started
            time.sleep(POLL_INTERVAL)

    def _add(self, value: str, uid: str | None = None) -> None:
        self.c.add(f"{KEY}: {value}", user_id=uid or self.uid, app_id=self.app_id,
                   metadata={"key": KEY}, infer=False)

    def _delete_key(self) -> int:
        """Delete this key's memories from the SOURCE scope only.

        Deliberately the same per-key delete the adapter performs (fetch scope,
        filter on metadata key, delete by id) rather than a `delete_all` on the
        scope. That is what `012` does, and holding the delete mechanism fixed
        keeps (d)/(e) comparable with (a)-(c) — otherwise a cross-scope arm
        would change two variables at once and be uninterpretable against the
        baseline.
        """
        doomed = [r.get("id") for r in self._rows()
                  if (r.get("metadata") or {}).get("key") == KEY and r.get("id")]
        for mem_id in doomed:
            self.c.delete(mem_id)
        return len(doomed)

    # -- the experiment -----------------------------------------------------

    def run(self, first: str, second: str, extra_settle: float) -> dict:
        self.c.delete_all(user_id=self.uid)
        if self.cross_scope:
            self.c.delete_all(user_id=self.target_uid)
        time.sleep(POLL_INTERVAL)

        self._add(first)
        seeded, _ = self._wait_until(lambda: self._visible(first), CONFIRM_TIMEOUT)
        if not seeded:
            return {"arm": self.name, "outcome": "SETUP_FAILED",
                    "detail": "the initial write never became retrievable",
                    "reads": self.reads}

        n = self._delete_key()
        emptied, empty_after = self._wait_until(
            lambda: not self._visible(first), CONFIRM_TIMEOUT)
        if not emptied:
            return {"arm": self.name, "outcome": "DELETE_NEVER_OBSERVED",
                    "detail": f"value still readable {CONFIRM_TIMEOUT}s after "
                              f"deleting {n} memories", "reads": self.reads}

        if extra_settle:
            time.sleep(extra_settle)

        # The re-add goes to the target scope, which is the source scope for
        # (a)-(c) and a different one for (d)-(e).
        self._add(second, self.target_uid)
        landed, took = self._wait_until(
            lambda: self._visible(second, self.target_uid), CONFIRM_TIMEOUT)
        self.c.delete_all(user_id=self.uid)
        if self.cross_scope:
            self.c.delete_all(user_id=self.target_uid)

        where = "in the target scope" if self.cross_scope else ""
        return {
            "arm": self.name,
            "outcome": "RE_ADD_VISIBLE" if landed else "RE_ADD_LOST",
            "detail": (f"re-added value retrievable {where} after {took:.0f}s"
                       if landed else
                       f"re-added value NOT retrievable {where} within "
                       f"{CONFIRM_TIMEOUT:.0f}s").replace("  ", " "),
            "delete_mechanism": "per-key delete by id",
            "cross_scope": self.cross_scope,
            "delete_observed_empty_after_s": round(empty_after),
            "extra_settle_s": extra_settle,
            "reads": self.reads,
        }

    def run_namespace(self, values: list[str], probe: str,
                      extra_settle: float) -> dict:
        """Arms (f)/(g): the `reset()` condition, reproduced exactly.

        Deliberately NOT the per-key delete used by (a)-(e). `reset()` issues
        `delete_all(app_id=...)` — one call wiping the namespace across every
        scope — then polls the namespace until it reads empty and returns,
        after which the runner immediately writes. That sequence is what this
        reproduces, including the emptiness poll, because the open question is
        whether reading empty actually means the delete has propagated.
        """
        # No opening cleanup: this arm uses a fresh `app_id`, so the namespace
        # is empty already and the delete_all below is the only one in the arm.
        # Seed real residue across a couple of scopes, as a live namespace has.
        for i, value in enumerate(values):
            self._add(value, f"{self.uid}__s{i % 2}")
        seeded, _ = self._wait_until(
            lambda: len(self._namespace_rows()) >= len(values), CONFIRM_TIMEOUT)
        if not seeded:
            return {"arm": self.name, "outcome": "SETUP_FAILED",
                    "detail": f"only {len(self._namespace_rows())} of "
                              f"{len(values)} seed writes became retrievable",
                    "reads": self.reads}

        # The call reset() makes, verbatim.
        self.c.delete_all(app_id=self.app_id)
        emptied, empty_after = self._wait_until(
            lambda: not self._namespace_rows(), CONFIRM_TIMEOUT)
        if not emptied:
            return {"arm": self.name, "outcome": "NAMESPACE_NEVER_EMPTIED",
                    "detail": f"namespace still non-empty {CONFIRM_TIMEOUT}s "
                              "after delete_all", "reads": self.reads}

        if extra_settle:
            time.sleep(extra_settle)

        target = f"{self.uid}__s0"
        self._add(probe, target)
        landed, took = self._wait_until(
            lambda: self._visible(probe, target), CONFIRM_TIMEOUT)
        self.c.delete_all(app_id=self.app_id)

        return {
            "arm": self.name,
            "outcome": "WRITE_VISIBLE" if landed else "WRITE_LOST",
            "detail": (f"post-reset write retrievable after {took:.0f}s"
                       if landed else
                       f"post-reset write NOT retrievable within "
                       f"{CONFIRM_TIMEOUT:.0f}s"),
            "delete_mechanism": "delete_all(app_id) — as reset()",
            "residue_seeded": len(values),
            "namespace_read_empty_after_s": round(empty_after),
            "extra_settle_s": extra_settle,
            "reads": self.reads,
        }

    def _namespace_rows(self) -> list[dict]:
        self.reads += 1
        resp = self.c.get_all(filters={"app_id": self.app_id})
        return resp.get("results", []) if isinstance(resp, dict) else (resp or [])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="skip the cost prompt")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the cost estimate and exit without spending")
    args = ap.parse_args()

    # Four quota probes (one up front, one either side of each arm) are
    # themselves SEARCH calls; count them so the estimate is not optimistic.
    probes = 1 + 2 * len(COST_ESTIMATE)
    total = sum(COST_ESTIMATE.values()) + probes
    print("Estimated SEARCH cost (worst case, the scarce counter):")
    for arm, cost in COST_ESTIMATE.items():
        print(f"  {arm:26s} ~{cost:3d} units")
    print(f"  {'quota probes':26s} ~{probes:3d} units")
    print(f"  {'TOTAL':26s} ~{total:3d} units\n")

    if args.dry_run:
        print("dry run: nothing spent")
        return 0

    api_key = resolve_api_key()
    if not api_key:
        print("No Mem0 key: set MEM0_API_KEY or populate ~/.mem0/config.json.",
              file=sys.stderr)
        return 2
    try:
        from mem0 import MemoryClient
    except ImportError:
        print('Mem0 SDK missing. pip install "mem0ai>=2.0.14"', file=sys.stderr)
        return 2

    remaining = quota_remaining(api_key)
    if remaining is not None and remaining >= 0:
        print(f"SEARCH quota remaining now: {remaining}")
        if remaining < total:
            print(f"REFUSING TO RUN: need ~{total}, have {remaining}. A run that "
                  "dies partway proves nothing and spends the rest.", file=sys.stderr)
            return 3
    if not args.yes:
        if input(f"Spend ~{total} SEARCH units? [y/N] ").strip().lower() != "y":
            print("aborted; nothing spent beyond the quota probe")
            return 0

    # Pass the resolved key explicitly. MemoryClient() reads MEM0_API_KEY from
    # the environment and nothing else, so the documented ~/.mem0/config.json
    # fallback above would otherwise resolve a key used only for the quota
    # probe and then die here with "Mem0 API Key not provided" — after the
    # operator had already confirmed the spend.
    client = MemoryClient(api_key=api_key)
    stamp = int(time.time())
    # (name, first value, second value, extra settle, cross-scope)
    plan = [
        ("a_identical", f"wintergreen-{stamp}", f"wintergreen-{stamp}", 0.0, False),
        ("b_varied", f"clearwater-{stamp}", f"riverstone-{stamp}", 0.0, False),
        ("c_settle_then_identical", f"lantern-{stamp}", f"lantern-{stamp}",
         EXTRA_SETTLE, False),
        ("d_cross_scope_identical", f"kingfisher-{stamp}", f"kingfisher-{stamp}",
         0.0, True),
        ("e_cross_scope_settle", f"saltmarsh-{stamp}", f"saltmarsh-{stamp}",
         EXTRA_SETTLE, True),
    ]
    # (name, extra settle) — namespace arms, run via run_namespace().
    namespace_plan = [
        ("f_namespace_delete_all", 0.0),
        ("g_namespace_settle", EXTRA_SETTLE),
    ]

    results = []
    for name, first, second, settle, cross in plan:
        print(f"\n--- arm {name}{' (cross-scope)' if cross else ''} ---")
        print(f"    expected SEARCH cost, worst case: ~{COST_ESTIMATE[name]} units")
        before = quota_remaining(api_key)
        source = f"mc_diag__{name}_{stamp}"
        target = f"mc_diag__{name}_{stamp}__scope_b" if cross else None
        result = Arm(client, name, source, target).run(first, second, settle)
        after = quota_remaining(api_key)
        if before is not None and after is not None and before >= 0 and after >= 0:
            result["search_units_spent"] = before - after
        results.append(result)
        for k, v in result.items():
            print(f"  {k}: {v}")

    for name, settle in namespace_plan:
        print(f"\n--- arm {name} (namespace delete_all — the reset() condition) ---")
        print(f"    expected SEARCH cost, worst case: ~{COST_ESTIMATE[name]} units")
        before = quota_remaining(api_key)
        arm = Arm(client, name, f"mc_diag__{name}_{stamp}",
                  app_id=f"{APP_ID}_{name}_{stamp}")
        result = arm.run_namespace(
            [f"residue-{i}-{stamp}" for i in range(_RESIDUE_N)],
            f"probe-{name}-{stamp}", settle)
        after = quota_remaining(api_key)
        if before is not None and after is not None and before >= 0 and after >= 0:
            result["search_units_spent"] = before - after
        results.append(result)
        for k, v in result.items():
            print(f"  {k}: {v}")

    print("\n" + "=" * 68)
    for r in results:
        print(f"  {r['arm']:26s} {r['outcome']}")
    print("=" * 68)

    by = {r["arm"]: r["outcome"] for r in results}
    a, b, c = (by.get("a_identical"), by.get("b_varied"),
               by.get("c_settle_then_identical"))
    d, e = by.get("d_cross_scope_identical"), by.get("e_cross_scope_settle")
    LOST, OK = "RE_ADD_LOST", "RE_ADD_VISIBLE"
    print("\nHow to read this (stated in advance, so the reading is not fitted "
          "to whatever came back):")

    print("\n  SAME-SCOPE (a)-(c):")
    if a == LOST and c == OK:
        print("  (a) failed, (c) succeeded -> deletion keeps reaping writes after it\n"
              "  has stopped being observable through search. Confirming a delete by\n"
              "  polling until empty is therefore insufficient. Affects any caller\n"
              "  doing delete-then-re-add. Needs a founder ruling before publication.")
    elif a == LOST and b == OK and c == LOST:
        print("  (a) and (c) failed, (b) succeeded -> tied to the content being\n"
              "  identical, and waiting does not help. Points at content-level\n"
              "  deduplication rather than delete propagation.")
    elif a == OK:
        print("  (a) succeeded -> not reproduced SAME-SCOPE. This does NOT refute\n"
              "  the 012 hypothesis: 012 is a cross-scope rescope, which (a)-(c) do\n"
              "  not exercise at all. Read (d)/(e) before concluding anything.")
    else:
        print("  Pattern not anticipated. Record the outcomes and reason from them\n"
              "  rather than forcing one of the branches above.")

    print("\n  CROSS-SCOPE (d)-(e) — the actual 012 condition:")
    if d == LOST and a == OK:
        print("  (d) failed where (a) succeeded -> the blocker is the SCOPE CROSSING,\n"
              "  not the identical content. Deleting a value from scope A suppresses\n"
              "  writing the same text to scope B. That is a coupling ACROSS scopes,\n"
              "  which for a memory store sold on tenant isolation is a serious\n"
              "  finding and needs a founder ruling before it goes anywhere.")
    elif d == LOST and e == OK:
        print("  (d) failed, (e) succeeded -> cross-scope suppression is TRANSIENT:\n"
              "  a settle before the write clears it. Propagation timing, not a\n"
              "  permanent cross-scope rule. The adapter would need a settle on\n"
              "  rescope, and the timeout must be sized from measurement.")
    elif d == LOST and e == LOST:
        print("  (d) and (e) both failed -> cross-scope suppression is NOT merely\n"
              "  timing; waiting does not clear it. The strongest form of the\n"
              "  finding, and the one with the clearest customer impact.")
    elif d == OK:
        print("  (d) succeeded -> 012 does not reproduce even under the true\n"
              "  cross-scope condition, in isolation. That points at load- or\n"
              "  sequence-dependence, which only the full 15 x 2 regen can settle.\n"
              "  Do NOT read this as 'the original failure was spurious'.")
    else:
        print("  Pattern not anticipated. Record the outcomes and reason from them.")

    f, g = by.get("f_namespace_delete_all"), by.get("g_namespace_settle")
    W_LOST, W_OK = "WRITE_LOST", "WRITE_VISIBLE"
    print("\n  NAMESPACE (f)-(g) — the reset() condition, and the leading "
          "hypothesis:")
    if f == W_LOST and g == W_OK:
        print("  (f) failed, (g) succeeded -> POLLING A NAMESPACE UNTIL EMPTY IS\n"
              "  NOT SUFFICIENT. reset() does exactly that and then returns, so\n"
              "  every run is exposed: the first writes after any reset can be\n"
              "  reaped by a delete_all that has stopped being visible. This is\n"
              "  a harness-side defect as much as a provider behaviour — the fix\n"
              "  is a settle after reset, sized from the measured empty-to-safe\n"
              "  gap, not a fixed sleep. Founder ruling before publication.")
    elif f == W_LOST and g == W_LOST:
        print("  (f) and (g) both failed -> waiting does not clear it either. A\n"
              "  delete_all suppresses subsequent writes to the namespace for\n"
              "  longer than 60s, or until something other than time changes.\n"
              "  The most serious form, and it would make delete_all unusable as\n"
              "  a reset primitive.")
    elif f == W_OK and all(x == "RE_ADD_VISIBLE" for x in (a, b, c, d, e) if x):
        print("  (f) succeeded and every other arm passed -> no isolated arm\n"
              "  reproduces the abort. Combined with the standing caveat, that\n"
              "  points hard at load/backlog dependence, which only a full run\n"
              "  can exhibit. Do NOT read it as 'nothing is wrong'.")
    elif f == W_OK:
        print("  (f) succeeded -> the reset() condition does not reproduce in\n"
              "  isolation. Read it against the per-key arms before concluding.")
    else:
        print("  Pattern not anticipated. Record the outcomes and reason from them.")

    print("\nNo conclusion may be published from a single execution of this "
          "script. Re-run to establish stability first.")

    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"readd_after_delete_{stamp}.json"
    out.write_text(json.dumps({
        "run_at_epoch": stamp,
        "poll_interval_s": POLL_INTERVAL,
        "confirm_timeout_s": CONFIRM_TIMEOUT,
        "extra_settle_s": EXTRA_SETTLE,
        "estimated_search_units": total,
        "results": results,
        "caveat": "single execution; not a finding until reproduced",
    }, indent=2))
    print(f"results written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
