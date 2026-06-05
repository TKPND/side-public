"""Tests for scripts/v4.11/audit_sextuple_pin.sh — AUDIT-02 Gate 5 sextuple-pin drift audit.

Verifies:
- script exists and is executable
- exits 0 on real (clean) repo state
- stdout lists all 6 anchor names and short SHAs
- drift detection via STATE.md path override env var (AUDIT_STATE_MD)

Per D-56 (Phase 95 CONTEXT): extends verify_signal_commit_v411.sh with Gate 5.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "v4.11" / "audit_sextuple_pin.sh"
STATE_MD = REPO_ROOT / ".planning" / "STATE.md"

ANCHOR_SHORT_SHAS = {
    "threshold_commit": "6527cbc",
    "regime_commit": "90bf4b2",
    "sizing_exit_commit": "8a4e49d",
    "sizing_exit_commit_v410": "a5f7183",
    "signal_commit_v411": "f8ccc8a",
    "engine_commit": "a5a1102",
}


def _run_audit(env_override: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def test_audit_script_exists_and_executable() -> None:
    assert SCRIPT.exists(), f"missing: {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"not executable: {SCRIPT}"


def test_audit_exits_zero_on_clean_seal() -> None:
    result = _run_audit()
    assert result.returncode == 0, (
        f"audit failed on clean repo (rc={result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_audit_output_lists_six_anchors() -> None:
    result = _run_audit()
    assert result.returncode == 0
    for anchor in ANCHOR_SHORT_SHAS:
        assert anchor in result.stdout, (
            f"anchor '{anchor}' missing from audit output:\n{result.stdout}"
        )


def test_audit_output_contains_short_shas() -> None:
    result = _run_audit()
    assert result.returncode == 0
    for anchor, short_sha in ANCHOR_SHORT_SHAS.items():
        assert short_sha in result.stdout, (
            f"short SHA '{short_sha}' (anchor={anchor}) missing from audit output:\n"
            f"{result.stdout}"
        )


def test_audit_detects_drift_via_state_md_override(tmp_path: Path) -> None:
    """Patch AUDIT_STATE_MD to a mutated copy where threshold_commit SHA is wrong.

    Expected: audit exits non-zero with a FAIL message mentioning threshold_commit.
    """
    bogus_state = tmp_path / "STATE_mutated.md"
    original = STATE_MD.read_text(encoding="utf-8")
    # Replace threshold_commit SHA with an obviously wrong one
    mutated = original.replace(
        "| threshold_commit | `6527cbc`",
        "| threshold_commit | `deadbee`",
        1,
    )
    assert mutated != original, "mutation no-op — test precondition violated"
    bogus_state.write_text(mutated, encoding="utf-8")

    result = _run_audit(env_override={"AUDIT_STATE_MD": str(bogus_state)})
    assert result.returncode != 0, (
        f"audit should fail on mutated STATE.md but returned 0\n"
        f"stdout:\n{result.stdout}"
    )
    assert "threshold_commit" in result.stdout or "threshold_commit" in result.stderr, (
        "drift message should mention threshold_commit"
    )


def test_audit_checks_d17_carry_in_artifacts() -> None:
    """Gate 5 includes Phase 93/94 carry-in artifacts; verify paths appear in output."""
    result = _run_audit()
    assert result.returncode == 0
    for carry_in in (
        "data/v4.11/vol_per_slot.parquet",
        "data/v4.11/cells_post_filter.parquet",
        "reports/v4.11/neutral_mode/v4_11_ship_decision.json",
    ):
        assert carry_in in result.stdout, (
            f"carry-in path '{carry_in}' missing from Gate 5 output:\n{result.stdout}"
        )
