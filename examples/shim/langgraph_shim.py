#!/usr/bin/env python3
"""memorycheck HTTP shim backed by a real LangGraph store — a known-good
reference to diff your own agent against.

    pip install fastapi uvicorn langgraph
    python examples/shim/langgraph_shim.py         # serves on :8808

    memorycheck doctor --adapter http:examples/shim/config.yaml
    memorycheck run scenarios --adapter http:examples/shim/config.yaml

Why this one exists. The `langgraph` adapter is verified and passes the full
pack cleanly (see the README), so this shim gives you a working reference: run
the pack against it, run the pack against your stack, and diff. If this passes
and yours does not, the difference is in your retrieval or scoping rather than
in the harness — which is usually the fastest way to locate the problem.

It is also the smallest honest example of the three decisions, because the
store settles them for you:

  DECISION 1 (scope)  -> a namespace tuple, so scopes cannot bleed
  DECISION 2 (agent)  -> templated here; swap in your agent to measure it
  DECISION 3 (TTL)    -> unsupported, declared as such

Use `supports_ttl: false` in config.yaml.
"""

from __future__ import annotations

from fastapi import FastAPI
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel

app = FastAPI()
STORE = InMemoryStore()
ROOT = "memorycheck-shim"


# --- DECISION 1 -------------------------------------------------------------
# Scope becomes a namespace tuple. Isolation is then structural: a search under
# (root, tenant, alice) cannot return rows under (root, tenant, bob), so the
# most common integration bug is impossible by construction rather than by
# remembering to add a WHERE clause. If your store has namespaces, prefer this.
def ns(tenant_id: str, user_id: str) -> tuple[str, str, str]:
    return (ROOT, tenant_id, user_id)


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
    # Scoped to our own root: a global clear would delete rows belonging to
    # whoever else uses the store. list_namespaces paginates, so page through
    # it — its default limit is 100 and it will quietly hide the rest.
    offset = 0
    while True:
        page = STORE.list_namespaces(prefix=(ROOT,), limit=500, offset=offset)
        if not page:
            break
        for namespace in page:
            for item in STORE.search(namespace, limit=1000):
                STORE.delete(namespace, item.key)
        if len(page) < 500:
            break
        offset += 500
    return {"ok": True}


@app.post("/write")
def write(body: WriteBody):
    # A keyed store supersedes by construction: writing the same key replaces
    # the old value, so a correction leaves nothing stale behind. If your store
    # appends instead, you must enforce recency yourself at retrieval time.
    STORE.put(ns(body.tenant_id, body.user_id), body.key,
              {"key": body.key, "value": body.value})
    return {"ok": True}


@app.post("/delete")
def delete(body: DeleteBody):
    STORE.delete(ns(body.tenant_id, body.user_id), body.key)
    return {"ok": True}


@app.post("/query")
def query(body: QueryBody):
    # DECISION 2 — swap this for your agent to measure what you actually ship.
    # Note limit=1000: `search` defaults to 10 and would silently truncate,
    # which reads as the store forgetting facts.
    items = STORE.search(ns(body.tenant_id, body.user_id), limit=1000)
    facts = [(i.value["key"], i.value["value"]) for i in items
             if isinstance(i.value, dict)]
    if not facts:
        return {"answer": "I don't have anything stored about that.", "retrieved": []}
    return {
        "answer": "Here's what I remember: "
        + "; ".join(f"{k} is {v}" for k, v in facts)
        + ".",
        "retrieved": [{"key": k, "value": v} for k, v in facts],
    }


# DECISION 3 — InMemoryStore has no TTL and cannot age facts by logical steps,
# so declare supports_ttl: false and expiry reports NOT TESTED. That is the
# honest setting; a no-op endpoint plus supports_ttl: true would report a pass
# that means nothing.

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8808)
