"""Phase 107 Wave 0 RED scaffold — W5 idempotent invariant.

2 連続 emit で 2 artifact (md + sources.json) が byte-identical (D-V413-07
canonical bytes invariant)。Analog: tests/v4_13/test_phase_106_idempotent.py
(literal copy + path 差し替え)。

Wave 0 では emit_diagnosis_v413.py 不在で pytest.fail RED。Plan 03 (W5 検証)
+ Plan 04 (実 emit) で GREEN 化。

B2 / D-17 / INTEGRITY-V413-03 invariant 維持。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest


EMIT_SCRIPT = Path("scripts/v4.13/emit_diagnosis_v413.py")
DATA_DIR = Path("data/v4.13")
ARTIFACTS = [
    DATA_DIR / "diagnosis_v413.md",
    DATA_DIR / "diagnosis_v413_nextbet_sources.json",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_emit() -> None:
    """直接 path で subprocess 起動 (dot-name dir のため module path 不可)."""
    if not EMIT_SCRIPT.exists():
        pytest.fail(
            f"Plan 02 emit script 未作成 (Wave 0 RED expected): {EMIT_SCRIPT}\n"
            "Phase 107 Plan 02 で emit_diagnosis_v413.py を新規作成する."
        )
    result = subprocess.run(
        [sys.executable, str(EMIT_SCRIPT)],
        check=True,
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"emit_diagnosis_v413.py failed: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_md_byte_identical() -> None:
    """W5: 2 回連続 emit で diagnosis_v413.md が byte-identical."""
    _run_emit()
    md_path = ARTIFACTS[0]
    if not md_path.exists():
        pytest.fail(f"Plan 04 artifact 未 emit (Wave 0 RED expected): {md_path}")
    first = _sha256(md_path)
    _run_emit()
    second = _sha256(md_path)
    assert first == second, f"{md_path} not idempotent: 1st={first} 2nd={second}"


def test_sources_json_byte_identical() -> None:
    """W5: 2 回連続 emit で diagnosis_v413_nextbet_sources.json が byte-identical
    (D-V413-07 canonical bytes: sort_keys=True, indent=2, ensure_ascii=False,
    allow_nan=False, 末尾 \\n)。
    """
    _run_emit()
    sources_path = ARTIFACTS[1]
    if not sources_path.exists():
        pytest.fail(f"Plan 04 artifact 未 emit (Wave 0 RED expected): {sources_path}")
    first = _sha256(sources_path)
    _run_emit()
    second = _sha256(sources_path)
    assert first == second, (
        f"{sources_path} not idempotent (D-V413-07 canonical bytes): "
        f"1st={first} 2nd={second}"
    )
