#!/usr/bin/env python3
"""memorycheck HTTP shim — Flask template.

    pip install flask
    python examples/shim/flask_shim.py             # serves on :8808

    memorycheck doctor --adapter http:examples/shim/config.yaml
    memorycheck run scenarios --adapter http:examples/shim/config.yaml

Same contract as the FastAPI template; see fastapi_shim.py for the fuller
commentary. The three decisions are marked DECISION 1/2/3.
"""

from __future__ import annotations

from flask import Flask, jsonify, request

app = Flask(__name__)

# FAKE STORE — delete this and use your own.
_STORE: dict[tuple[str, str, str], str] = {}


# --- DECISION 1: map your tenant/user model onto scope ----------------------
# memorycheck sends (tenant_id, user_id) on every call and you must retrieve
# using BOTH. Filtering on tenant alone means every user in a tenant shares
# memory; `doctor` reports that as scope leakage (P1), and it is the most
# common integration bug we see.
def scope_key() -> tuple[str, str]:
    body = request.get_json(force=True, silent=True) or {}
    return (body.get("tenant_id", ""), body.get("user_id", ""))


@app.post("/reset")
def reset():
    """Clear test state so each scenario starts clean. Never wipe production
    data here — residue is scored against you as deletion residue."""
    _STORE.clear()
    return jsonify(ok=True)


@app.post("/write")
def write():
    """Store under (scope, key). A second write to the same key is a
    correction: afterwards the old value must not drive answers."""
    body = request.get_json(force=True)
    tenant, user = scope_key()
    _STORE[(tenant, user, body["key"])] = body["value"]
    return jsonify(ok=True)


@app.post("/delete")
def delete():
    """Must make the value unreachable from /query. A soft-delete flag your
    retrieval ignores fails `doctor` — correctly, that is deletion residue."""
    body = request.get_json(force=True)
    tenant, user = scope_key()
    _STORE.pop((tenant, user, body["key"]), None)
    return jsonify(ok=True)


@app.post("/query")
def query():
    """
    DECISION 2 — call your agent here.

    Replace the templating below with your real path to measure what you
    ship rather than just your store:

        context = your_retrieval(tenant, user, body["prompt"])
        answer  = your_agent.respond(body["prompt"], context=context)
        return jsonify(answer=answer)

    "answer" must be a string, and stored values must appear verbatim in it —
    the default judge matches exact values, not paraphrases. "retrieved" is
    optional and never graded.
    """
    tenant, user = scope_key()
    facts = [(k, v) for (t, u, k), v in _STORE.items() if (t, u) == (tenant, user)]
    if not facts:
        return jsonify(answer="I don't have anything stored about that.", retrieved=[])
    return jsonify(
        answer="Here's what I remember: "
        + "; ".join(f"{k} is {v}" for k, v in facts)
        + ".",
        retrieved=[{"key": k, "value": v} for k, v in facts],
    )


# --- DECISION 3: TTL ---------------------------------------------------------
# memorycheck's clock is logical — it asks you to age facts by N steps, not to
# wait N seconds. Most stacks cannot, and that is fine: set
# `supports_ttl: false` in config.yaml and expiry reports NOT TESTED instead
# of passing. Only implement this if you can genuinely age facts on demand;
# faking it with sleeps produces a green result that means nothing.
@app.post("/advance_time")
def advance_time():
    return jsonify(ok=True, note="no-op; set supports_ttl: false in config.yaml")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8808)
