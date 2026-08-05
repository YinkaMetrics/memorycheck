from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "diagnostics" / "readd_after_delete.py"


def _environment_without_provenance() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("MEMORYCHECK_EXECUTED_BY", None)
    env.pop("MEMORYCHECK_RUN_ENVIRONMENT", None)
    return env


def test_live_diagnostic_refuses_missing_provenance_before_api_access():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--yes"],
        capture_output=True,
        check=False,
        env=_environment_without_provenance(),
        text=True,
    )

    assert completed.returncode == 2
    assert "Live-run provenance required" in completed.stderr
    assert "No Mem0 key" not in completed.stderr


def test_dry_run_does_not_require_live_provenance():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        capture_output=True,
        check=False,
        env=_environment_without_provenance(),
        text=True,
    )

    assert completed.returncode == 0
    assert "dry run: nothing spent" in completed.stdout
