"""Phase 106 Wave 0 RED scaffold — INTEGRITY-V413-02.

2 連続 emit で 3 artifact が byte-identical (W5 invariant、D-V413-07)。
Analog: tests/v4_13/test_phase_105_idempotent.py (literal copy + path 差し替え)。
"""

from __future__ import annotations
import hashlib
import subprocess
import sys
from pathlib import Path
import pytest

EMIT_SCRIPT = Path("scripts/v4.13/emit_ablation_v413.py")
DATA_DIR = Path("data/v4.13")
ARTIFACTS = [
    DATA_DIR / "diagnosis_v413_ablation.parquet",
    DATA_DIR / "ablation_score.json",
    DATA_DIR / "diagnosis_v413_ablation_sources.json",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_emit() -> None:
    """直接 path で subprocess 起動 (dot-name dir のため module path 不可)."""
    if not EMIT_SCRIPT.exists():
        pytest.fail(
            f"Wave 1 emit script 未作成 (Wave 0 RED expected): {EMIT_SCRIPT}\n"
            "Phase 106 Wave 1 で emit_ablation_v413.py を新規作成する."
        )
    result = subprocess.run(
        [sys.executable, str(EMIT_SCRIPT)],
        check=True,
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"emit_ablation_v413.py failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_emit_idempotent() -> None:
    """W5: 2 回連続実行で 3 artifact が byte-identical."""
    _run_emit()
    for p in ARTIFACTS:
        if not p.exists():
            pytest.fail(f"Wave 1 artifact 未 emit (Wave 0 RED expected): {p}")
    first = {p: _sha256(p) for p in ARTIFACTS}
    _run_emit()
    second = {p: _sha256(p) for p in ARTIFACTS}
    for p in ARTIFACTS:
        assert first[p] == second[p], (
            f"{p} not idempotent: 1st={first[p]} 2nd={second[p]}"
        )
