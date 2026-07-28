#!/usr/bin/env python3
"""memorycheck HTTP shim — FastAPI template.

    pip install fastapi uvicorn
    python examples/shim/fastapi_shim.py           # serves on :8808

    memorycheck doctor --adapter http:examples/shim/config.yaml
    memorycheck run scenarios --adapter http:examples/shim/config.yaml

Copy this into your codebase and replace the FAKE STORE with your own. The
four endpoints are the whole contract. Nothing leaves your network.

Three decisions are yours, marked DECISION 1/2/3 below. They are the only
parts that need thought; everything else is plumbing.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

# ---------------------------------------------------------------------------
# FAKE STORE — delete this. It exists so the template runs before you wire
# anything up, and so `doctor` has something to pass against.
# ---------------------------------------------------------------------------
_STORE: dict[tuple[str, str, str], str] = {}


# ---------------------------------------------------------------------------
# DECISION 1 — how your tenant/user model maps onto scope.
#
# memorycheck sends a (tenant_id, user_id) pair with every call. You must
# retrieve using BOTH. The usual mappings:
#
#   * multi-tenant SaaS   tenant_id -> org/workspace id, user_id -> end user
#   * single tenant       tenant_id -> constant, user_id -> your user id
#   * per-agent memory    user_id  -> agent/session id
#
# If you ignore user_id and filter on tenant alone, every user inside a tenant
# shares memory. `doctor` fails that as scope leakage — a P1 — and it is the
# most common integration bug we see.
# ---------------------------------------------------------------------------
def scope_key(tenant_id: str, user_id: str) -> tuple[str, str]:
    return (tenant_id, user_id)


class WriteBody(BaseModel):
    tenant_id: str
    user_id: str
    key: str
    value: str
    ttl_steps: int | None = None


class DeleteBody(BaseModel):
    tenant_id: str
    user_id: str
    key: str


class QueryBody(BaseModel):
    tenant_id: str
    user_id: str
    prompt: str
    seed: int = 0


class ResetBody(BaseModel):
    namespace: str


@app.post("/reset")
def reset(body: ResetBody):
    """Clear state so each scenario starts clean. Scope this to test data —
    never wipe a production store. Residue from a previous run is scored
    against you as deletion residue."""
    _STORE.clear()
    return {"ok": True}


@app.post("/write")
def write(body: WriteBody):
    """Store value under (scope, key). A second write to the same key is a
    correction: after it, the old value must no longer drive answers."""
    tenant, user = scope_key(body.tenant_id, body.user_id)
    _STORE[(tenant, user, body.key)] = body.value
    return {"ok": True}


@app.post("/delete")
def delete(body: DeleteBody):
    """Delete must make the value unreachable from /query. A soft-delete flag
    that your retrieval ignores will fail `doctor` — correctly."""
    tenant, user = scope_key(body.tenant_id, body.user_id)
    _STORE.pop((tenant, user, body.key), None)
    return {"ok": True}


@app.post("/query")
def query(body: QueryBody):
    """
    DECISION 2 — call your agent here.

    The template below retrieves and templates a sentence, which is enough to
    exercise the store. That measures your *store*. To measure the thing you
    actually ship, replace the body with a call to your agent:

        context = your_retrieval(tenant, user, body.prompt)
        answer  = your_agent.respond(body.prompt, context=context)
        return {"answer": answer}

    Two requirements either way:
      * "answer" must be a string — it is what gets graded;
      * stored values must appear verbatim in it. The default judge matches
        exact values, not paraphrases, so an agent that says "their plan is
        the annual one" instead of "scale-annual-2026" reads as a miss.
        That is a known limit of the deterministic judge, not your bug.

    "retrieved" is optional and never graded — include it for your own
    debugging.
    """
    tenant, user = scope_key(body.tenant_id, body.user_id)
    facts = [(k, v) for (t, u, k), v in _STORE.items() if (t, u) == (tenant, user)]
    if not facts:
        return {"answer": "I don't have anything stored about that.", "retrieved": []}
    return {
        "answer": "Here's what I remember: "
        + "; ".join(f"{k} is {v}" for k, v in facts)
        + ".",
        "retrieved": [{"key": k, "value": v} for k, v in facts],
    }


# ---------------------------------------------------------------------------
# DECISION 3 — TTL, and what to do if you have no advance_time.
#
# memorycheck's clock is logical: it asks you to age facts by N steps, not to
# wait N seconds. Most stacks cannot do that, and that is fine.
#
#   set  supports_ttl: false  in your config.yaml  ->  expiry checks report
#   NOT TESTED rather than a pass. Honest, and nothing else is affected.
#
# Only implement this endpoint if you can genuinely age stored facts on
# demand. Faking it with wall-clock sleeps produces a green result that means
# nothing.
# ---------------------------------------------------------------------------
@app.post("/advance_time")
def advance_time(body: dict):
    return {"ok": True, "note": "no-op; set supports_ttl: false in config.yaml"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8808)
