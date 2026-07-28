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
from .base import AdapterError, MemoryAdapter, QueryResult


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
        self._token = os.environ.get(cfg.get("auth_token_env", ""), "")
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

    def write(self, scope: Scope, key: str, value: str, ttl_steps: int | None = None) -> None:
        payload = {**self._ids(scope), "key": key, "value": value}
        if ttl_steps is not None:
            payload["ttl_steps"] = ttl_steps
        self._post("write", payload)

    def delete(self, scope: Scope, key: str) -> None:
        self._post("delete", {**self._ids(scope), "key": key})

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
