"""Adapter registry. Spec strings:

  reference:strict | reference:naive | reference:leaky
  http:path/to/config.yaml
  mem0
  null

Roadmap adapters (zep, langgraph) register here as they land.
"""

from __future__ import annotations

from .base import MemoryAdapter, NullAdapter, QueryResult
from .reference import ReferenceAdapter


def load_adapter(spec: str) -> MemoryAdapter:
    name, _, arg = spec.partition(":")
    if name == "reference":
        return ReferenceAdapter(mode=arg or "strict")
    if name == "http":
        if not arg:
            raise ValueError("http adapter needs a config path: --adapter http:config.yaml")
        from .http import HTTPAdapter

        return HTTPAdapter(arg)
    if name == "mem0":
        from .mem0 import Mem0Adapter

        return Mem0Adapter()
    if name == "null":
        return NullAdapter()
    raise ValueError(
        f"unknown adapter {name!r} (available: reference, http, mem0, null; "
        "zep/langgraph are on the roadmap)"
    )


__all__ = ["MemoryAdapter", "NullAdapter", "QueryResult", "ReferenceAdapter", "load_adapter"]
