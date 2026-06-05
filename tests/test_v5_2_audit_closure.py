"""Tests for the v5.2 audit-closure helper."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "scripts")
import v5_2_audit_closure as closure  # noqa: E402


def _write_json(path: Path, content: str) -> None:
    path.write_text(content + "\n")


def _fixture_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    requirements_path = tmp_path / "REQUIREMENTS.md"
    roadmap_path = tmp_path / "ROADMAP.md"
    state_path = tmp_path / "STATE.md"
    claim_readiness_path = tmp_path / "verdict.json"
    v5_1_verdict_path = tmp_path / "final_verdict.json"
    v5_1_tick_contract_path = tmp_path / "tick_data_contract_report.json"

    requirements_path.write_text(
        "\n".join(
            [
                "| Requirement | Phase | Status |",
                "|-------------|-------|--------|",
                "| SOURCE-V52-01 | Phase 118 | Complete |",
                "| SOURCE-V52-02 | Phase 118 | Complete |",
                "| SOURCE-V52-03 | Phase 118 | Complete |",
                "| INGEST-V52-01 | Phase 119 | Complete |",
                "| INGEST-V52-02 | Phase 119 | Complete |",
                "| INGEST-V52-03 | Phase 119 | Complete |",
                "| MICRO-V52-01 | Phase 120 | Complete |",
                "| MICRO-V52-02 | Phase 120 | Complete |",
                "| MICRO-V52-03 | Phase 120 | Complete |",
                "| CLAIM52-V52-01 | Phase 121 | Complete |",
                "| CLAIM52-V52-02 | Phase 121 | Complete |",
                "| CLAIM52-V52-03 | Phase 121 | Complete |",
                "| AUDIT52-V52-01 | Phase 122 | Complete |",
                "| AUDIT52-V52-02 | Phase 122 | Complete |",
                "| AUDIT52-V52-03 | Phase 122 | Complete |",
            ]
        )
        + "\n"
    )
    roadmap_path.write_text(requirements_path.read_text())
    state_path.write_text("Last completed phase:** Phase 122\n")
    _write_json(
        claim_readiness_path,
        """{
  "verdict": "null_ship",
  "ship_verdict": false,
  "downstream_claim_ready": false,
  "sealed_for_downstream_claim": false,
  "source_readiness": "source_level_null_ship",
  "blocked_contract_candidate": {
    "downstream_consumable": false
  }
}""",
    )
    _write_json(
        v5_1_verdict_path,
        """{
  "verdict": "null_ship",
  "ship_verdict": false,
  "candidate_count": 0,
  "null_ship_reasons": ["empty_phase116_candidate_set"]
}""",
    )
    _write_json(
        v5_1_tick_contract_path,
        """{
  "claims": {
    "aggressor_flow_claim": false,
    "l2_depth_claim": false,
    "market_depth_claim": false
  }
}""",
    )
    return (
        requirements_path,
        roadmap_path,
        state_path,
        claim_readiness_path,
        v5_1_verdict_path,
        v5_1_tick_contract_path,
    )


def _build_report(tmp_path: Path, changed_paths: list[str] | None = None) -> dict:
    return closure.build_audit_closure(
        *_fixture_paths(tmp_path),
        changed_paths=changed_paths or [],
    )


def test_expected_traceability_maps_every_v5_2_requirement_once() -> None:
    assert closure.expected_v5_2_traceability() == {
        "SOURCE-V52-01": 118,
        "SOURCE-V52-02": 118,
        "SOURCE-V52-03": 118,
        "INGEST-V52-01": 119,
        "INGEST-V52-02": 119,
        "INGEST-V52-03": 119,
        "MICRO-V52-01": 120,
        "MICRO-V52-02": 120,
        "MICRO-V52-03": 120,
        "CLAIM52-V52-01": 121,
        "CLAIM52-V52-02": 121,
        "CLAIM52-V52-03": 121,
        "AUDIT52-V52-01": 122,
        "AUDIT52-V52-02": 122,
        "AUDIT52-V52-03": 122,
    }


def test_protected_path_matches_v5_1_and_v4_archive_paths() -> None:
    matches = closure.protected_path_matches(
        [
            "reports/v5.1/phase116/final_verdict.json",
            "reports/v5.1/tick_data_contract_report.md",
            ".planning/milestones/v4.13-MILESTONE-AUDIT.md",
            ".planning/milestones/RETROSPECTIVE.md",
            "data/v4.13/diagnosis_v413.md",
            "reports/v4.12/v4_12_ship_decision.json",
            "docs/reports/v4.2-audusd/report.md",
            "reports/v5.2/audit_closure/audit.json",
        ]
    )

    assert matches["v5_1"] == [
        "reports/v5.1/phase116/final_verdict.json",
        "reports/v5.1/tick_data_contract_report.md",
    ]
    assert matches["v4_archive"] == [
        ".planning/milestones/v4.13-MILESTONE-AUDIT.md",
        ".planning/milestones/RETROSPECTIVE.md",
        "data/v4.13/diagnosis_v413.md",
        "reports/v4.12/v4_12_ship_decision.json",
        "docs/reports/v4.2-audusd/report.md",
    ]


def test_build_audit_closure_marks_v5_2_ready_for_milestone_completion(
    tmp_path: Path,
) -> None:
    report = _build_report(tmp_path)

    assert report["schema_version"] == "v5.2.audit-closure.1"
    assert report["phase"] == 122
    assert report["requirements_addressed"] == [
        "AUDIT52-V52-01",
        "AUDIT52-V52-02",
        "AUDIT52-V52-03",
    ]
    assert report["traceability_exactly_once"] is True
    assert report["v5_1_preserved"] is True
    assert report["v4_archive_untouched"] is True
    assert report["milestone_verdict"] == "source_level_null_ship"
    assert report["ship_verdict"] is False
    assert report["downstream_claim_ready"] is False
    assert report["sealed_for_downstream_claim"] is False
    assert report["close_readiness"] == "ready_for_milestone_completion"


def test_v5_1_preservation_records_empty_candidate_null_ship(tmp_path: Path) -> None:
    v5_1 = _build_report(tmp_path)["v5_1_evidence"]

    assert v5_1["verdict"] == "null_ship"
    assert v5_1["ship_verdict"] is False
    assert v5_1["candidate_count"] == 0
    assert "empty_phase116_candidate_set" in v5_1["null_ship_reasons"]
    assert v5_1["tick_contract_claims"] == {
        "aggressor_flow_claim": False,
        "l2_depth_claim": False,
        "market_depth_claim": False,
    }


def test_build_audit_closure_blocks_when_v5_1_protected_path_changes(
    tmp_path: Path,
) -> None:
    report = _build_report(
        tmp_path,
        changed_paths=["reports/v5.1/phase116/final_verdict.json"],
    )

    assert report["v5_1_preserved"] is False
    assert report["close_readiness"] == "blocked"


def test_build_audit_closure_blocks_when_v4_archive_path_changes(
    tmp_path: Path,
) -> None:
    report = _build_report(
        tmp_path,
        changed_paths=["data/v4.13/diagnosis_v413.md"],
    )

    assert report["v4_archive_untouched"] is False
    assert report["close_readiness"] == "blocked"


def test_render_audit_markdown_exposes_closure_fields(tmp_path: Path) -> None:
    markdown = closure.render_audit_markdown(_build_report(tmp_path))

    assert "source_level_null_ship" in markdown
    assert "traceability_exactly_once: true" in markdown
    assert "v5_1_preserved: true" in markdown
    assert "v4_archive_untouched: true" in markdown
    assert "ready_for_milestone_completion" in markdown
