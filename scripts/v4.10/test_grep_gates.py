"""SHIP-02: pytest wrapper for grep_gates.sh CI enforcement."""

from __future__ import annotations
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_grep_gates_clean() -> None:
    """SHIP-02: grep_gates.sh must exit 0 on scripts/v4.10/ (all gates clean)."""
    result = subprocess.run(
        ["sh", "grep_gates.sh"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"grep_gates.sh failed (rc={result.returncode}):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
