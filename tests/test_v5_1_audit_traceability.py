"""Tests for v5.1 Phase 117 traceability and closure audit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
import v5_1_audit_traceability as audit  # noqa: E402


def test_traceability_maps_each_v51_requirement_once() -> None:
    requirements = audit.parse_traceability(audit.V5_1_REQUIREMENTS_PATH)
    roadmap = audit.parse_traceability(audit.V5_1_ROADMAP_PATH)

    assert set(requirements) == set(audit.EXPECTED_REQUIREMENT_PHASES)
    assert set(roadmap) == set(audit.EXPECTED_REQUIREMENT_PHASES)
    for req_id, phase in audit.EXPECTED_REQUIREMENT_PHASES.items():
        assert requirements[req_id].phase == phase
        assert roadmap[req_id].phase == phase


def test_v51_resolver_prefers_archived_planning_scope() -> None:
    assert audit._resolve_v5_1_path(Path(".planning/REQUIREMENTS.md")) == (
        audit.V5_1_REQUIREMENTS_PATH
    )
    assert audit._resolve_v5_1_path(Path(".planning/ROADMAP.md")) == (
        audit.V5_1_ROADMAP_PATH
    )


def test_archive_zone_detector_flags_forbidden_paths() -> None:
    changed = [
        ".planning/milestones/v4.13-phases/104/SUMMARY.md",
        "data/v4.13/diagnosis_v413.md",
        ".planning/milestones/RETROSPECTIVE.md",
        ".planning/milestones/v4.13-MILESTONE-AUDIT.md",
        "reports/v5.1/phase117/audit_traceability.json",
    ]

    assert audit.archive_zone_violations(changed) == changed[:4]
    assert audit.archive_zone_violations(["reports/v5.1/phase117/audit_traceability.json"]) == []


def test_changed_files_from_status_includes_untracked_archive_paths() -> None:
    status_output = "\n".join(
        [
            " M .planning/STATE.md",
            "?? .planning/milestones/v4.13-phases/new.md",
            "R  old/path.md -> data/v4.13/diagnosis_v413.md",
        ]
    )

    assert audit.changed_files_from_status(status_output) == [
        ".planning/STATE.md",
        ".planning/milestones/v4.13-phases/new.md",
        "data/v4.13/diagnosis_v413.md",
    ]


def test_build_audit_report_accepts_current_null_ship_artifacts() -> None:
    report = audit.build_audit_report()

    assert report["phase"] == 117
    assert report["status"] == "complete_null_ship"
    assert report["traceability"]["unmapped"] == []
    assert report["traceability"]["duplicate_mappings"] == []
    assert report["validation_evidence"]["all_required_topics_covered"] is True
    assert report["nyquist"]["fwer_denominator"] == 216
    assert report["nyquist"]["phase115_candidate_count"] == 0
    assert report["final_verdict"]["verdict"] == "null_ship"
    assert report["final_verdict"]["ship_verdict"] is False
    assert report["final_verdict"]["null_ship_reasons"] == ["empty_phase116_candidate_set"]
    assert report["kill_discipline"]["preserved"] is True
    assert report["requirements"] == {
        "AUDIT-V51-01": "PASS",
        "AUDIT-V51-02": "PASS",
        "AUDIT-V51-03": "PASS",
    }


def test_write_outputs_emits_json_and_markdown(tmp_path: Path) -> None:
    report = audit.build_audit_report()

    audit.write_outputs(report, tmp_path)

    loaded = json.loads((tmp_path / "audit_traceability.json").read_text())
    markdown = (tmp_path / "audit_traceability.md").read_text()
    assert loaded == report
    assert "# v5.1 Audit Traceability + Nyquist Closure" in markdown
    assert "AUDIT-V51-01 | PASS" in markdown
    assert "empty_phase116_candidate_set" in markdown
