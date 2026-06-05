"""Phase 72 unit tests for scripts/v4.6/verdict_classifier.py.

Covers:
- classify_verdict: 8 boundary cases (Phase 69 D-02/D-03/D-04 strict `<`)
- apply_multiple_testing: schema + NaN drop (CONTEXT D-11)
- CLI --help smoke
- End-to-end integration against Phase 71 real sign_breakdown.json
- Fleiss scalar type assertion (VERDICT-02 v4.5 tech-debt closure observable)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
V44_DIR = SCRIPT_DIR.parent / "v4.4"
for _p in (SCRIPT_DIR, V44_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import verdict_classifier  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHASE71_OUTPUT = (
    PROJECT_ROOT / "docs/reports/v4.6-verdict-resolution/sign-forensics/sign_breakdown.json"
)


# -----------------------------------------------------------------------------
# 1) Boundary unit tests (Phase 69 D-13 — 4 corners per axis)
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kappa, max_vif, n_eff, expected",
    [
        # D-02 kappa boundary — strict `<`
        (0.40, 1.0, 12.0, "noise"),
        (0.41, 1.0, 12.0, "drift"),
        (0.60, 1.0, 12.0, "drift"),
        (0.61, 1.0, 12.0, "signal"),
        # D-03 VIF boundary — 10.0 -> noise (>= cutoff)
        (0.80, 9.99, 12.0, "signal"),
        (0.80, 10.0, 12.0, "noise"),
        # D-04 n_eff boundary — 4.0 passes (strict `<`)
        (0.80, 1.0, 3.99, "noise"),
        (0.80, 1.0, 4.0, "signal"),
    ],
)
def test_classify_verdict_boundaries(kappa, max_vif, n_eff, expected):
    verdict, rationale = verdict_classifier.classify_verdict(kappa, max_vif, n_eff)
    assert verdict == expected, f"{kappa}/{max_vif}/{n_eff} -> {verdict}, expected {expected}"
    assert isinstance(rationale, str) and rationale


# -----------------------------------------------------------------------------
# 2) Multiple-testing schema + NaN handling (CONTEXT D-11)
# -----------------------------------------------------------------------------
def test_apply_multiple_testing_schema():
    out = verdict_classifier.apply_multiple_testing(
        [0.01, 0.05, 0.5, float("nan")], n_slots=96
    )
    assert out["n_slots"] == 96
    assert abs(out["bonferroni_alpha"] - 0.05 / 96) < 1e-12  # ≈ 5.208e-4
    assert out["bh_q"] == 0.10
    assert out["method"] == "BH"
    assert out["pass_count_bonf"] >= 0
    assert out["pass_count_bh"] >= 0
    # NaN must be dropped: the sum of valid counts cannot exceed len([0.01,0.05,0.5]) = 3
    assert out["pass_count_bonf"] <= 3
    assert out["pass_count_bh"] <= 3


# -----------------------------------------------------------------------------
# 3) CLI --help smoke (fail-fast wiring check)
# -----------------------------------------------------------------------------
def test_cli_help_exits_zero():
    r = subprocess.run(
        [
            "uv", "run", "python",
            str(SCRIPT_DIR / "verdict_classifier.py"), "--help",
        ],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr
    for flag in ("--sign", "--output-dir", "--threshold-commit"):
        assert flag in r.stdout, f"missing flag {flag} in --help output"


# -----------------------------------------------------------------------------
# 4) Integration: Phase 71 sign_breakdown.json -> verdict=noise (v4.5 HELD)
# -----------------------------------------------------------------------------
@pytest.mark.skipif(not PHASE71_OUTPUT.exists(), reason="Phase 71 output not generated yet")
def test_integration_phase71_input_yields_noise(tmp_path):
    """With Phase 71 fresh output (kappa=-0.0509), verdict must be `noise`.

    v4.5 baseline already reported max VIF=1e12 / n_eff=1.2e-11 -> noise (HELD).
    The verdict cascade is expected to hit `VIF>=10` (or `kappa<0.41`) and emit
    the null-ship-path rationale. `v45_baseline_diff.held` must be True.
    """
    result = subprocess.run(
        [
            "uv", "run", "python",
            str(SCRIPT_DIR / "verdict_classifier.py"),
            "--sign", str(PHASE71_OUTPUT),
            "--output-dir", str(tmp_path),
            "--threshold-commit", "432a885",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    report_path = tmp_path / "report.json"
    assert report_path.exists(), "report.json was not emitted"
    report = json.loads(report_path.read_text())

    assert report["verdict"] == "noise", f"verdict={report['verdict']}"
    assert "null ship path" in report["verdict_rationale"]
    assert report["v45_baseline_diff"]["held"] is True
    assert report["meta"]["input_provenance_stamp"] == "fresh-wfd-rerun-2026-04-19-70303ac"
    assert report["meta"]["threshold_commit"] == "432a885"
    assert report["multiple_testing"]["n_slots"] == 96
    assert report["multiple_testing"]["bh_q"] == 0.10


# -----------------------------------------------------------------------------
# 5) Fleiss kappa scalar type (VERDICT-02 tech-debt closure / D-10 observable)
# -----------------------------------------------------------------------------
@pytest.mark.skipif(not PHASE71_OUTPUT.exists(), reason="Phase 71 output not generated yet")
def test_fleiss_kappa_is_numeric_not_dict(tmp_path):
    """`fleiss_kappa` MUST be a scalar number (int/float, not bool/dict/list).

    v4.5 leaked a dict/None here; the v4.6 contract promotes the scalar value.
    """
    subprocess.run(
        [
            "uv", "run", "python",
            str(SCRIPT_DIR / "verdict_classifier.py"),
            "--sign", str(PHASE71_OUTPUT),
            "--output-dir", str(tmp_path),
        ],
        check=True,
    )
    report = json.loads((tmp_path / "report.json").read_text())

    fk = report["fleiss_kappa"]
    assert isinstance(fk, (int, float)), f"fleiss_kappa is {type(fk).__name__}"
    # bool is an int subclass; reject it explicitly.
    assert not isinstance(fk, bool), "fleiss_kappa must not be bool"
    assert not isinstance(fk, dict), "fleiss_kappa must not be dict (v4.5 tech-debt regression)"
    assert not isinstance(fk, list), "fleiss_kappa must not be list"
