#!/usr/bin/env python3
"""Discriminating experiment: why is a re-added value not retrievable?

STATUS: written, NOT YET RUN. It requires live Mem0 calls and the account's
SEARCH quota is exhausted until 2026-08-01. No finding is claimed here and
none should be quoted from this file. This is an open investigation.

Background. A full 15 x 2 run aborted on `012-rescope-then-readd`, where the
runner deletes a key from one scope and immediately writes the *same value*
into another. The write was acknowledged and then not retrievable for 30s.
Whether that is a Mem0 behaviour or an artifact of how this harness sequences
delete-then-write is exactly what is unresolved.

Three arms, because two cannot separate the candidate mechanisms:

  (a) delete, then re-add IDENTICAL text
      Baseline. Expected to reproduce the abort.

  (b) delete, then re-add VARIED text
      Isolates content-level deduplication. If (a) fails and (b) succeeds,
      the blocker is tied to the content being identical.

  (c) delete, poll until search reads empty, WAIT an additional 60s,
      then re-add IDENTICAL text
      The important arm. If (a) fails and (c) succeeds, the finding is that
      *polling until empty is insufficient* — deletion keeps reaping writes
      after it has stopped being observable through search. That would mean
      no amount of "confirm the delete landed" is sufficient, which is
      sharper and more consequential than the correction finding, and hits
      any customer doing delete-then-re-add.

Read the arms together. (a) alone says nothing; the pairs (a) vs (b) and
(a) vs (c) are what carry information.

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

# Worst-case SEARCH reads per arm: 1 pre-read + write-confirm polls
# + 1 delete lookup + delete-confirm polls + re-add-confirm polls.
_MAX_POLLS = int(CONFIRM_TIMEOUT / POLL_INTERVAL)
COST_ESTIMATE = {
    "a_identical": 2 + _MAX_POLLS + 1 + _MAX_POLLS,
    "b_varied": 2 + 2 + 1 + 4,
    "c_settle_then_identical": 2 + 2 + 1 + _MAX_POLLS,
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
    def __init__(self, client, name: str, user_id: str):
        self.c, self.name, self.uid = client, name, user_id
        self.reads = 0

    # -- primitives ---------------------------------------------------------

    def _rows(self) -> list[dict]:
        self.reads += 1
        resp = self.c.get_all(filters={"user_id": self.uid})
        return resp.get("results", []) if isinstance(resp, dict) else (resp or [])

    def _visible(self, value: str) -> bool:
        return any(value in (r.get("memory") or "") for r in self._rows())

    def _wait_until(self, predicate, timeout: float) -> tuple[bool, float]:
        started = time.monotonic()
        while True:
            if predicate():
                return True, time.monotonic() - started
            if time.monotonic() - started >= timeout:
                return False, time.monotonic() - started
            time.sleep(POLL_INTERVAL)

    def _add(self, value: str) -> None:
        self.c.add(f"{KEY}: {value}", user_id=self.uid, app_id=APP_ID,
                   metadata={"key": KEY}, infer=False)

    def _delete_key(self) -> int:
        doomed = [r.get("id") for r in self._rows()
                  if (r.get("metadata") or {}).get("key") == KEY and r.get("id")]
        for mem_id in doomed:
            self.c.delete(mem_id)
        return len(doomed)

    # -- the experiment -----------------------------------------------------

    def run(self, first: str, second: str, extra_settle: float) -> dict:
        self.c.delete_all(user_id=self.uid)
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

        self._add(second)
        landed, took = self._wait_until(lambda: self._visible(second), CONFIRM_TIMEOUT)
        self.c.delete_all(user_id=self.uid)

        return {
            "arm": self.name,
            "outcome": "RE_ADD_VISIBLE" if landed else "RE_ADD_LOST",
            "detail": (f"re-added value retrievable after {took:.0f}s"
                       if landed else
                       f"re-added value NOT retrievable within {CONFIRM_TIMEOUT:.0f}s"),
            "delete_observed_empty_after_s": round(empty_after),
            "extra_settle_s": extra_settle,
            "reads": self.reads,
        }


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

    client = MemoryClient()
    stamp = int(time.time())
    plan = [
        ("a_identical", f"wintergreen-{stamp}", f"wintergreen-{stamp}", 0.0),
        ("b_varied", f"clearwater-{stamp}", f"riverstone-{stamp}", 0.0),
        ("c_settle_then_identical", f"lantern-{stamp}", f"lantern-{stamp}", EXTRA_SETTLE),
    ]

    results = []
    for name, first, second, settle in plan:
        print(f"\n--- arm {name} ---")
        before = quota_remaining(api_key)
        result = Arm(client, name, f"mc_diag__{name}_{stamp}").run(first, second, settle)
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
    print("\nHow to read this (stated in advance, so the reading is not fitted "
          "to whatever came back):")
    if a == "RE_ADD_LOST" and c == "RE_ADD_VISIBLE":
        print("  (a) failed, (c) succeeded -> deletion keeps reaping writes after it\n"
              "  has stopped being observable through search. Confirming a delete by\n"
              "  polling until empty is therefore insufficient. Affects any caller\n"
              "  doing delete-then-re-add. Needs a founder ruling before publication.")
    elif a == "RE_ADD_LOST" and b == "RE_ADD_VISIBLE" and c == "RE_ADD_LOST":
        print("  (a) and (c) failed, (b) succeeded -> tied to the content being\n"
              "  identical, and waiting does not help. Points at content-level\n"
              "  deduplication rather than delete propagation.")
    elif a == "RE_ADD_VISIBLE":
        print("  (a) succeeded -> the abort did not reproduce in isolation. It may\n"
              "  depend on load or on preceding scenarios; do NOT conclude the\n"
              "  original failure was spurious without reproducing it under a run.")
    else:
        print("  Pattern not anticipated. Record the outcomes and reason from them\n"
              "  rather than forcing one of the branches above.")
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
