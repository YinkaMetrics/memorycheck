#!/usr/bin/env python3
"""Standalone reproduction of memorycheck's Mem0 correction finding.

No memorycheck import — only the Mem0 SDK — so anyone can run this against
their own account and check the result independently:

    pip install "mem0ai>=2.0.14"
    export MEM0_API_KEY=...
    python examples/repro_correction.py

What it does, once with infer=False and once with infer=True: write a fact,
write a correction to the same logical key, then ask a current-state question
and print exactly what retrieval returns.

What it is testing. Mem0 documents ADD-only extraction as intended design
("Memories accumulate; nothing is overwritten"), so the accumulation itself is
not the finding. The claim under test is the one in the same README section:
temporal reasoning that "ranks the right dated instance for queries about
current state". This script shows what a current-state query actually gets
back.

The script cleans up after itself. Actual output from a real run is pasted at
the bottom of this file.
"""

from __future__ import annotations

import os
import sys
import time

OLD = "starter-legacy-2024"
NEW = "scale-annual-2026"
QUESTION = "Which plan is this user on?"

SETTLE = 3  # seconds; Mem0 writes and deletes are applied asynchronously
MAX_WAIT = 90  # cap on waiting for the correction to become visible


def _search(client, user_id: str) -> list[str]:
    resp = client.search(QUESTION, filters={"user_id": user_id}, top_k=100)
    rows = resp.get("results", []) if isinstance(resp, dict) else (resp or [])
    return [r.get("memory", "") for r in rows]


def run_case(client, infer: bool) -> bool:
    """Returns True if the superseded value came back for a current-state query.

    Waits until the correction itself is visible before judging anything. With
    infer=True the write goes through an extraction LLM and lands seconds later
    — querying too early shows the old value alone, which looks dramatic and
    means nothing except that we raced the pipeline. We want steady state.
    """
    user_id = f"mc_repro__infer_{str(infer).lower()}"
    label = f"infer={infer}"

    client.delete_all(user_id=user_id)
    time.sleep(SETTLE)

    client.add(f"plan: {OLD}", user_id=user_id, metadata={"key": "plan"}, infer=infer)
    time.sleep(SETTLE)
    client.add(f"plan: {NEW}", user_id=user_id, metadata={"key": "plan"}, infer=infer)

    waited, texts = 0.0, []
    while waited < MAX_WAIT:
        time.sleep(SETTLE)
        waited += SETTLE
        texts = _search(client, user_id)
        if any(NEW in t for t in texts):
            break

    new_back = any(NEW in t for t in texts)
    old_back = any(OLD in t for t in texts)

    print(f"\n--- {label} ---")
    print(f"  wrote:    'plan: {OLD}'  then  'plan: {NEW}'")
    print(f"  asked:    {QUESTION!r}")
    if new_back:
        print(f"  correction visible after ~{waited:.0f}s of polling")
    else:
        print(f"  correction NOT visible after {MAX_WAIT}s — reporting anyway")
    print(f"  returned: {len(texts)} memories")
    for t in texts:
        print(f"    - {t}")
    print(f"  superseded value present: {old_back}")
    print(f"  current value present:    {new_back}")

    client.delete_all(user_id=user_id)
    return old_back


def main() -> int:
    if not os.environ.get("MEM0_API_KEY"):
        print("MEM0_API_KEY is not set.", file=sys.stderr)
        return 2
    try:
        from mem0 import MemoryClient
    except ImportError:
        print('Mem0 SDK missing. pip install "mem0ai>=2.0.14"', file=sys.stderr)
        return 2

    import importlib.metadata as md

    print(f"mem0ai version: {md.version('mem0ai')}")
    print(f"run at (UTC):   {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")

    client = MemoryClient()
    stale_false = run_case(client, infer=False)
    stale_true = run_case(client, infer=True)

    print("\n" + "=" * 62)
    print(f"superseded value returned for a current-state query, infer=False: {stale_false}")
    print(f"superseded value returned for a current-state query, infer=True:  {stale_true}")
    print("=" * 62)
    if stale_false or stale_true:
        print(
            "\nThe superseded value is returned alongside the current one, so an\n"
            "agent templating these results sees both with no ordering signal\n"
            "that prefers the correction. Resolving that is left to the caller."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --------------------------------------------------------------------------
# ACTUAL OUTPUT — pasted verbatim from a real run, not illustrative.
# Mem0 hosted platform, api.mem0.ai, mem0ai 2.0.14, 2026-07-27 15:51 UTC.
#
#   mem0ai version: 2.0.14
#   run at (UTC):   2026-07-27T15:51:40Z
#
#   --- infer=False ---
#     wrote:    'plan: starter-legacy-2024'  then  'plan: scale-annual-2026'
#     asked:    'Which plan is this user on?'
#     correction visible after ~3s of polling
#     returned: 2 memories
#       - plan: starter-legacy-2024
#       - plan: scale-annual-2026
#     superseded value present: True
#     current value present:    True
#
#   --- infer=True ---
#     wrote:    'plan: starter-legacy-2024'  then  'plan: scale-annual-2026'
#     asked:    'Which plan is this user on?'
#     correction visible after ~15s of polling
#     returned: 2 memories
#       - User has a plan named starter-legacy-2024
#       - User has a plan named scale-annual-2026
#     superseded value present: True
#     current value present:    True
#
#   ==============================================================
#   superseded value returned for a current-state query, infer=False: True
#   superseded value returned for a current-state query, infer=True:  True
#   ==============================================================
#
# Note on why this script polls. An earlier version slept a fixed 5s and the
# infer=True case returned ONLY the superseded value — the correction had not
# finished extracting yet. That looks like a far more dramatic result and is
# purely an artifact of racing the pipeline. Extraction took ~15s here, so the
# script now waits for the correction to be visible before judging anything.
# Report steady state, not a race you won.
# --------------------------------------------------------------------------
