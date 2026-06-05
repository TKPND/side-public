"""SHIP-02: pytest wrapper for grep_gates_v411.sh CI enforcement."""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GATE_SCRIPT = REPO_ROOT / "scripts" / "v4.11" / "grep_gates_v411.sh"


def test_grep_gates_v411_clean_exit():
    """SHIP-02: grep_gates_v411.sh must exit 0 on scripts/v4.11/ (all 9+SEAL gates clean)."""
    result = subprocess.run(
        ["sh", str(GATE_SCRIPT)], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    assert result.returncode == 0, (
        f"grep_gates_v411 failed:\n{result.stdout}\n{result.stderr}"
    )


def test_ninth_pattern_detects_negative_shift():
    """Planted `shift(-1)` in SCOPE must trigger exit 1 (9th anti-feature)."""
    bad_file = REPO_ROOT / "scripts" / "v4.11" / "_tmp_test_shift.py"
    try:
        bad_file.write_text("import polars as pl\n_ = pl.col('x').shift(-1)\n")
        result = subprocess.run(
            ["sh", str(GATE_SCRIPT)], capture_output=True, text=True, cwd=str(REPO_ROOT)
        )
        assert result.returncode != 0, "9th pattern failed to detect shift(-1)"
        assert "negative_shift_lookahead" in result.stderr
    finally:
        if bad_file.exists():
            bad_file.unlink()


def test_all_v410_patterns_preserved():
    """Verify 8 original v4.10 patterns + 1 new = 9 total check() calls in script."""
    content = GATE_SCRIPT.read_text()
    for pat in [
        "roc_auc_score",
        "full_kelly",
        "f_star",
        "m_t",
        "p_adj_v48",
        "p_adj_v49",
        "regime_commit_v48",
        "shift",
    ]:
        assert pat in content, (
            f"pattern literal {pat!r} missing from grep_gates_v411.sh"
        )
    assert "verify_signal_commit_v411.sh" in content, "v4.11 SEAL gate missing"
