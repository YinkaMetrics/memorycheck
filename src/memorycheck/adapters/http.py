"""Custom HTTP adapter: point memorycheck at your own stack.

You expose four small endpoints (a thin shim in front of your memory store
and agent — typically <100 lines in your codebase); memorycheck drives the
lifecycle through them. Runs entirely inside your network: the harness is
local-first and no raw content leaves your infrastructure.

Contract (all POST, JSON in/out):

  {base}/reset   {"namespace": str}
  {base}/write   {"tenant_id", "user_id", "key", "value", "ttl_steps"?}
  {base}/delete  {"tenant_id", "user_id", "key"}
  {base}/query   {"tenant_id", "user_id", "prompt", "seed"}
                 -> {"answer": str, "retrieved": [{"key"?, "value"?, ...}]}

Config YAML (pass as  --adapter http:path/to/config.yaml):

  base_url: "http://localhost:8808"
  supports_ttl: false        # be honest; expiry checks report NOT_TESTED
  timeout_seconds: 30
  convergence_timeout_seconds: 30
  auth_token_env: MEMORYCHECK_HTTP_TOKEN   # optional bearer token env var
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from ..ledger import Scope
from .base import AdapterError, MemoryAdapter, QueryResult, poll_until


class HTTPAdapterError(AdapterError):
    pass


class HTTPAdapter(MemoryAdapter):
    def __init__(self, config_path: str) -> None:
        cfg = yaml.safe_load(Path(config_path).read_text()) or {}
        if "base_url" not in cfg:
            raise HTTPAdapterError(f"{config_path}: base_url is required")
        self.base_url: str = cfg["base_url"].rstrip("/")
        self.supports_ttl = bool(cfg.get("supports_ttl", False))
        self.timeout = float(cfg.get("timeout_seconds", 30))
        self.convergence_timeout = float(
            cfg.get("convergence_timeout_seconds", self.timeout)
        )
        self.convergence_interval = float(
            cfg.get("convergence_interval_seconds", 0.25)
        )
        self._token = os.environ.get(cfg.get("auth_token_env", ""), "")
        self._known_values: dict[tuple[str, str, str], set[str]] = {}
        # Doctor performs its own diagnostic polling so it can distinguish a
        # bad endpoint contract from slow convergence. Normal scenario runs
        # leave this enabled and cannot race accepted mutations.
        self.confirm_mutations = True
        self.last_write_convergence_seconds: float | None = None
        self.last_delete_convergence_seconds: float | None = None
        self.name = f"http:{self.base_url}"

    # ------------------------------------------------------------- plumbing

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode() or "{}"
        except urllib.error.URLError as e:
            raise HTTPAdapterError(f"POST /{path} failed: {e}") from e
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise HTTPAdapterError(f"POST /{path}: response is not JSON") from e

    @staticmethod
    def _ids(scope: Scope) -> dict:
        return {"tenant_id": scope.tenant_id, "user_id": scope.user_id}

    # ------------------------------------------------------------ interface

    def reset(self, namespace: str) -> None:
        self._post("reset", {"namespace": namespace})
        self._known_values.clear()

    def write(self, scope: Scope, key: str, value: str, ttl_steps: int | None = None) -> None:
        payload = {**self._ids(scope), "key": key, "value": value}
        if ttl_steps is not None:
            payload["ttl_steps"] = ttl_steps
        self._post("write", payload)
        if not self.confirm_mutations:
            self._known_values.setdefault(self._known_key(scope, key), set()).add(value)
            return
        converged, waited = poll_until(
            lambda: self._query_has_value(scope, key, value),
            timeout=self.convergence_timeout,
            interval=self.convergence_interval,
        )
        self.last_write_convergence_seconds = waited
        if not converged:
            raise HTTPAdapterError(
                f"POST /write accepted {key!r}, but /query did not expose "
                f"{value!r} within {waited:.1f}s; refusing to let the scenario "
                "race an unconfirmed write"
            )
        self._known_values.setdefault(self._known_key(scope, key), set()).add(value)

    def delete(self, scope: Scope, key: str) -> None:
        known = set(self._known_values.get(self._known_key(scope, key), set()))
        self._post("delete", {**self._ids(scope), "key": key})
        if not self.confirm_mutations:
            self._known_values.pop(self._known_key(scope, key), None)
            return
        if not known:
            self.last_delete_convergence_seconds = 0.0
            return
        converged, waited = poll_until(
            lambda: not any(self._query_has_value(scope, key, value) for value in known),
            timeout=self.convergence_timeout,
            interval=self.convergence_interval,
        )
        self.last_delete_convergence_seconds = waited
        if not converged:
            raise HTTPAdapterError(
                f"POST /delete accepted {key!r}, but /query still exposed a "
                f"known value after {waited:.1f}s; refusing to score the "
                "provider on a delete that has not converged"
            )
        self._known_values.pop(self._known_key(scope, key), None)

    def advance_time(self, steps: int) -> None:
        if self.supports_ttl:
            self._post("advance_time", {"steps": steps})

    def query(self, scope: Scope, prompt: str, seed: int = 0) -> QueryResult:
        data = self._post("query", {**self._ids(scope), "prompt": prompt, "seed": seed})
        if "answer" not in data:
            raise HTTPAdapterError(
                "query response must contain 'answer' (got keys: "
                f"{sorted(data)[:8]})"
            )
        answer = data["answer"]
        if not isinstance(answer, str):
            # Deliberately not coerced. str() on a dict or list yields a
            # repr that the judge would then match against — turning a broken
            # response shape into arbitrary findings instead of a clear error.
            raise HTTPAdapterError(
                f"query 'answer' must be a string, got {type(answer).__name__}"
            )
        return QueryResult(answer=answer, retrieved=list(data.get("retrieved", [])))

    @staticmethod
    def _known_key(scope: Scope, key: str) -> tuple[str, str, str]:
        return (scope.tenant_id, scope.user_id, key)

    def _query_has_value(self, scope: Scope, key: str, value: str) -> bool:
        """Confirm a mutation through the same /query surface the pack reads.

        Raw `retrieved` hits may establish convergence even when the answering
        layer paraphrases. They are evidence for mutation arrival only; the
        oracle still grades exclusively from the answer.
        """
        result = self.query(scope, f"What is the {key} for this user?")
        if value in result.answer:
            return True
        return value in " ".join(str(row) for row in result.retrieved)
