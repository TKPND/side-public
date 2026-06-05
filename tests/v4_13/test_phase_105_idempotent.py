"""Phase 105 emit_degeneracy_proof.py の冪等性 invariant (W5 revision 1).

W5 / D-V413-07 検証:
    - 2 回連続実行で 4 artifact (parquet ×2 + JSON ×2) が byte-identical
    - phase104_backup は 1-shot (再 emit で上書き禁止)

Note:
    plan-内 module path `python -m scripts.v4_13.emit_degeneracy_proof` は
    `scripts/v4.13/` の dot-name dir で resolve できないため、test では
    直接 path (`scripts/v4.13/emit_degeneracy_proof.py`) で subprocess 起動する。
    在 process import は state leak するので必ず subprocess。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path("data/v4.13")
EMIT_SCRIPT = Path("scripts/v4.13/emit_degeneracy_proof.py")

ARTIFACTS = [
    DATA_DIR / "diagnosis_v413.parquet",
    DATA_DIR / "diagnosis_v413_failure_modes.parquet",
    DATA_DIR / "diagnosis_v413_degeneracy_evidence.json",
    DATA_DIR / "diagnosis_v413_failure_modes_sources.json",
]
BACKUP = DATA_DIR / "diagnosis_v413.parquet.phase104_backup"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_emit() -> None:
    """直接 path で subprocess 起動 (dot-name dir のため module path 不可)."""
    result = subprocess.run(
        [sys.executable, str(EMIT_SCRIPT)],
        check=True,
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"emit_degeneracy_proof.py failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_emit_idempotent() -> None:
    """W5: 2 回連続実行で 4 artifact が byte-identical."""
    _run_emit()
    first = {p: _sha256(p) for p in ARTIFACTS}
    _run_emit()
    second = {p: _sha256(p) for p in ARTIFACTS}
    for p in ARTIFACTS:
        assert first[p] == second[p], (
            f"{p} not idempotent: 1st={first[p]} 2nd={second[p]}"
        )


def test_phase104_backup_one_shot() -> None:
    """W5: backup は 1 回だけ作成、再 emit でも byte-identical."""
    if not BACKUP.exists():
        _run_emit()
    first_backup_hash = _sha256(BACKUP)
    _run_emit()
    second_backup_hash = _sha256(BACKUP)
    assert first_backup_hash == second_backup_hash, (
        f"Phase 104 backup must be 1-shot: 1st={first_backup_hash} 2nd={second_backup_hash}"
    )
