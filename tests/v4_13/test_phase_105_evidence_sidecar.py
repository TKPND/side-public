"""tests/v4_13/test_phase_105_evidence_sidecar.py — Phase 105 Wave 0 RED scaffold.

degeneracy_evidence.json sidecar (D-105-06) の不変条件を機械固定する。

検証対象 invariant:
    - D-V413-07 canonical bytes: json.dumps(d, sort_keys=True, indent=2,
      ensure_ascii=False) + "\\n" で再シリアライズ → 元 file bytes と一致
    - D-105-06 research_commit pin: "fcff705"
    - D-105-06 4 milestone (v4.9/v4.10/v4.11/v4.12) 完備、各 entry に
      causal_fields (list[str], len>=1), row_count, intended_threshold_scale
    - schema_version pin: "v4.13.1"
    - B4 NEW: intended_threshold_scale 4 milestone literal pin
        v4.9  == "TBD (Kelly fraction natural unit candidate)"
        v4.10 == "pf_median=1.0"
        v4.11 == "edge_count_p_adj_005=m_prime=64"
        v4.12 == "edge_count_p_adj_005=m_prime=32"

Citations:
    - 105-01-PLAN.md Task 2
    - 105-CONTEXT.md D-105-06 / D-V413-07
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

EXPECTED_RESEARCH_COMMIT = "fcff705"
EXPECTED_SCHEMA_VERSION = "v4.13.1"
EXPECTED_MILESTONES = ("v4.9", "v4.10", "v4.11", "v4.12")

# B4 LOCKED literals (CONTEXT.md D-105-06 表)
EXPECTED_INTENDED_THRESHOLD_SCALES = {
    "v4.9": "TBD (Kelly fraction natural unit candidate)",
    "v4.10": "pf_median=1.0",
    "v4.11": "edge_count_p_adj_005=m_prime=64",
    "v4.12": "edge_count_p_adj_005=m_prime=32",
}


def _load_evidence_bytes(path: Path) -> bytes:
    """artifact 未存在時は明示的に pytest.fail で RED させる (Wave 0 RED 状態)."""
    if not path.exists():
        pytest.fail(
            f"Wave 2 artifact 未 emit (Wave 0 RED expected): {path}\n"
            "Phase 105 Wave 2 で emit_degeneracy_proof.py が degeneracy_evidence.json を emit する."
        )
    return path.read_bytes()


def test_evidence_sidecar_canonical_bytes(phase105_evidence_path: Path) -> None:
    """degeneracy_evidence.json が canonical bytes form (D-V413-07).

    json.loads → json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False) + "\\n"
    で再シリアライズ → 元 bytes と完全一致.
    """
    raw = _load_evidence_bytes(phase105_evidence_path)
    d = json.loads(raw)
    canonical = (
        json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    assert raw == canonical, (
        "canonical bytes invariant 違反: file bytes と "
        "json.dumps(sort_keys=True, indent=2, ensure_ascii=False)+\\n の再シリアライズ結果が不一致"
    )


def test_evidence_sidecar_research_commit_pinned(
    phase105_evidence_path: Path,
) -> None:
    """research_commit が "fcff705" に pin (D-105-06).

    Phase 105 RESEARCH.md commit が変わっても evidence sidecar の audit trail を
    機械固定するため、test 側で hard-pin.
    """
    raw = _load_evidence_bytes(phase105_evidence_path)
    d = json.loads(raw)
    assert "research_commit" in d, "research_commit key が存在すること"
    assert d["research_commit"] == EXPECTED_RESEARCH_COMMIT, (
        f"research_commit must be {EXPECTED_RESEARCH_COMMIT!r}: got {d['research_commit']!r}"
    )


def test_evidence_sidecar_4_milestones_present(
    phase105_evidence_path: Path,
) -> None:
    """4 milestone 完備かつ各 entry に causal_fields / row_count / intended_threshold_scale (D-105-06)."""
    raw = _load_evidence_bytes(phase105_evidence_path)
    d = json.loads(raw)
    assert "milestones" in d, "milestones key が存在すること"
    milestones = d["milestones"]
    for ms in EXPECTED_MILESTONES:
        assert ms in milestones, f"milestone {ms} が milestones dict に存在すること"
        entry = milestones[ms]
        assert "causal_fields" in entry, f"{ms}.causal_fields が存在すること"
        assert isinstance(entry["causal_fields"], list), (
            f"{ms}.causal_fields は list であること: got {type(entry['causal_fields'])}"
        )
        assert len(entry["causal_fields"]) >= 1, (
            f"{ms}.causal_fields は len >= 1 であること: got {entry['causal_fields']}"
        )
        assert all(isinstance(x, str) for x in entry["causal_fields"]), (
            f"{ms}.causal_fields は list[str] であること"
        )
        assert "row_count" in entry, f"{ms}.row_count が存在すること"
        assert "intended_threshold_scale" in entry, (
            f"{ms}.intended_threshold_scale が存在すること"
        )


def test_evidence_sidecar_schema_version_v413_1(
    phase105_evidence_path: Path,
) -> None:
    """top-level schema_version が "v4.13.1" であること (D-105-05 + D-105-06)."""
    raw = _load_evidence_bytes(phase105_evidence_path)
    d = json.loads(raw)
    assert "schema_version" in d, "schema_version key が存在すること"
    assert d["schema_version"] == EXPECTED_SCHEMA_VERSION, (
        f"schema_version must be {EXPECTED_SCHEMA_VERSION!r}: got {d['schema_version']!r}"
    )


def test_intended_threshold_scale_pinned(phase105_evidence_path: Path) -> None:
    """B4 NEW: 4 milestone 全 intended_threshold_scale が CONTEXT.md D-105-06
    LOCKED 表の literal と完全一致 (B4 反映).

    Expected (from CONTEXT.md D-105-06):
        v4.9  == "TBD (Kelly fraction natural unit candidate)"
        v4.10 == "pf_median=1.0"
        v4.11 == "edge_count_p_adj_005=m_prime=64"
        v4.12 == "edge_count_p_adj_005=m_prime=32"
    """
    raw = _load_evidence_bytes(phase105_evidence_path)
    d = json.loads(raw)
    milestones = d.get("milestones", {})
    for ms, expected in EXPECTED_INTENDED_THRESHOLD_SCALES.items():
        assert ms in milestones, f"milestone {ms} が存在すること"
        actual = milestones[ms].get("intended_threshold_scale")
        assert actual == expected, (
            f"{ms}.intended_threshold_scale: expected {expected!r}, got {actual!r}"
        )
