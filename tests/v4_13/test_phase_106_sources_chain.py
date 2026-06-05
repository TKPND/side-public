"""Phase 106 Wave 0 RED scaffold — INTEGRITY-V413-01.

sources sidecar SHA256 chain pin: parent (8ca18543...) + sibling chain。
Analog: tests/v4_13/test_d17_invariant.py::test_sidecar_sha256_matches_conftest_expected
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import pytest

EXPECTED_PARENT_SHA = "8ca18543a433a82aaaabd1adb7679f838e5d37cfc0f7a541c0dde601a7c3b83e"


def _load_sources(path: Path) -> dict:
    if not path.exists():
        pytest.fail(
            f"Wave 1 artifact 未 emit (Wave 0 RED expected): {path}\n"
            "Phase 106 Wave 1 で emit_ablation_v413.py が sources sidecar を emit する."
        )
    return json.loads(path.read_bytes())


def test_sources_parent_sha_pinned(phase106_sources_path: Path) -> None:
    """sources.json の parent SHA が Phase 105 contract と一致 (literal pin)."""
    d = _load_sources(phase106_sources_path)
    assert d["parent_diagnosis_v413_sha256"] == EXPECTED_PARENT_SHA


def test_sources_self_sha_matches_artifact(
    phase106_sources_path: Path,
    phase106_ablation_path: Path,
    phase106_score_path: Path,
) -> None:
    """sibling artifact の SHA が sources.json 記載値と一致 (chain pin invariant)."""
    d = _load_sources(phase106_sources_path)
    if not phase106_ablation_path.exists():
        pytest.fail(f"ablation parquet 未 emit: {phase106_ablation_path}")
    if not phase106_score_path.exists():
        pytest.fail(f"score json 未 emit: {phase106_score_path}")
    assert (
        d["ablation_parquet_sha256"]
        == hashlib.sha256(phase106_ablation_path.read_bytes()).hexdigest()
    )
    assert (
        d["ablation_score_sha256"]
        == hashlib.sha256(phase106_score_path.read_bytes()).hexdigest()
    )


def test_sources_canonical_bytes(phase106_sources_path: Path) -> None:
    """sources.json も canonical bytes form (D-V413-07)."""
    if not phase106_sources_path.exists():
        pytest.fail(f"sources json 未 emit: {phase106_sources_path}")
    raw = phase106_sources_path.read_bytes()
    d = json.loads(raw)
    canonical = (
        json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    assert raw == canonical


def test_sources_research_ref_pinned(phase106_sources_path: Path) -> None:
    """research_ref が Phase 106 RESEARCH.md path 文字列と一致."""
    d = _load_sources(phase106_sources_path)
    assert (
        d["research_ref"] == ".planning/phases/106-ablation-5-top-axis/106-RESEARCH.md"
    )


def test_sources_expected_row_count(phase106_sources_path: Path) -> None:
    d = _load_sources(phase106_sources_path)
    assert d["expected_row_count_ablation"] == 20
