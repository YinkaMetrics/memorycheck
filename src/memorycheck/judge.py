"""Usage classification: which fact values does an agent's answer rely on?

v0 is deliberately deterministic — normalised token-sequence matching — so
that ground truth stays with the ledger and the harness has zero model
dependency out of the box. An LLM judge for paraphrased/semantic reliance
slots in behind the same interface, but only after the calibration protocol
has been run (≥200 labelled examples, ≥90% precision on release-blocking
classes). Until then, semantic classification would be the tool grading
itself, which this project refuses to do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass
class Usage:
    value: str
    evidence: str


class Judge(Protocol):
    name: str

    def classify(self, answer: str, candidate_values: list[str]) -> list[Usage]:
        """Return the candidate values the answer relies on."""
        ...


class DeterministicJudge:
    """Exact (normalised) containment. High precision, known recall limit:
    it will not catch paraphrased reliance — that is the LLM judge's job,
    post-calibration. A miss here is therefore conservative for gating."""

    name = "deterministic-v0"

    def classify(self, answer: str, candidate_values: list[str]) -> list[Usage]:
        norm_answer = f" {_norm(answer)} "
        used: list[Usage] = []
        seen: set[str] = set()
        for value in candidate_values:
            if value in seen:
                continue
            nv = _norm(value)
            if nv and f" {nv} " in norm_answer:
                used.append(Usage(value=value, evidence=value))
                seen.add(value)
        return used


def load_judge(name: str) -> Judge:
    if name in ("deterministic", "deterministic-v0"):
        return DeterministicJudge()
    if name == "llm":
        raise NotImplementedError(
            "The LLM judge ships only after the calibration protocol "
            "(200 labelled examples, >=90% precision on release-blocking "
            "classes). See README: 'Judge calibration'."
        )
    raise ValueError(f"unknown judge {name!r}")
