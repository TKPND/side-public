"""Contract tests for Phase 137 risk gate closure evidence generation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import generate_risk_gate_closure_evidence as closure


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_risk_gate_closure_evidence.py"
REPORT_JSON = ROOT / "reports" / "v5.7" / "risk_gate_closure_evidence.json"
REPORT_MD = ROOT / "reports" / "v5.7" / "risk_gate_closure_evidence.md"
EVIDENCE_SCHEMA_VERSION = "risk_gate_closure_evidence.v1"
RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
EXPECTED_CHECKS = {
    "decision_replay",
    "stopped_execution",
    "continued_runtime",
    "artifact_validation",
    "schema_drift_alignment",
    "v56_alignment",
    "scope_guard",
}
EXPECTED_DECISIONS = {"size", "cap", "reject", "kill", "block"}


def build_clean_report(tmp_path: Path) -> dict:
    return closure.build_risk_gate_closure_evidence(
        report_dir=tmp_path,
        diff_base="HEAD",
        committed_changed_paths=[],
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
    )


def load_committed_report() -> dict:
    return json.loads(REPORT_JSON.read_text(encoding="utf-8"))


def test_phase137_builds_top_level_closure_evidence_contract(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)

    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["phase"] == 137
    assert report["requirements_addressed"] == [
        "CLOSE-01",
        "CLOSE-02",
        "CLOSE-03",
        "CLOSE-04",
    ]
    assert set(report["checks"]) == EXPECTED_CHECKS
    assert report["summary"]["overall_status"] == "PASS"
    assert report["summary"]["checks_failed"] == 0


def test_decision_replay_uses_real_scan_outputs_for_all_decision_classes(
    tmp_path: Path,
) -> None:
    report = build_clean_report(tmp_path)
    decision_replay = report["checks"]["decision_replay"]

    assert decision_replay["passed"] is True
    rows = decision_replay["decisions"]
    assert {row["decision_class"] for row in rows} == EXPECTED_DECISIONS
    for row in rows:
        decision_class = row["decision_class"]
        for key in (
            "replay_command_vector",
            "replay_command",
            "scan_output_path",
            "risk_gate",
            "artifact_path",
            "validator_payload",
        ):
            assert key in row
        assert row["passed"] is True
        assert row["risk_gate"]["decision_class"] == decision_class
        assert row["risk_gate"]["validation_status"] == "validated"
        assert row["risk_gate"]["validator"] == "scripts/validate_risk_contract.py"
        assert (
            row["risk_gate"]["schema_ref"]
            == "risk/contracts/v1/risk_contract_v1.schema.json"
        )
        assert row["validator_payload"]["schema_version"] == RESULT_SCHEMA_VERSION
        assert row["validator_payload"]["valid"] is True


def test_stopped_decisions_have_no_wfd_or_backtest_metric_claims(
    tmp_path: Path,
) -> None:
    report = build_clean_report(tmp_path)
    stopped = report["checks"]["stopped_execution"]

    assert stopped["passed"] is True
    assert {row["decision_class"] for row in stopped["decisions"]} == {
        "block",
        "kill",
        "reject",
    }
    for row in stopped["decisions"]:
        assert row["execution_state"] == "stopped"
        assert row["sentinel_tripped"] is False
        assert row["fee_curve"] == []
        assert row["pf_gross"] is None
        assert row["pf_net_2bps_rt"] is None
        assert row["alpha_cliff"] is None
        assert row["normal_verdict_fields_absent_or_null"] is True


def test_continued_size_and_cap_runtime_proof(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)
    continued = report["checks"]["continued_runtime"]
    by_decision = {row["decision_class"]: row for row in continued["decisions"]}

    assert continued["passed"] is True
    assert set(by_decision) == {"size", "cap"}
    assert by_decision["size"]["execution_state"] == "continued"
    assert by_decision["cap"]["execution_state"] == "continued"
    assert by_decision["size"]["application_status_present"] is False
    assert by_decision["cap"]["application_status"] == "deferred"
    assert by_decision["cap"]["metrics_match_ungated"] is True


def test_artifact_validation_lists_every_emitted_slot_artifact(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)
    emitted_count = sum(
        row["emitted_slot_count"]
        for row in report["checks"]["decision_replay"]["decisions"]
    )
    artifacts = report["checks"]["artifact_validation"]["artifacts"]

    assert emitted_count == 15
    assert len(artifacts) == emitted_count
    assert {row["decision_class"] for row in artifacts} == EXPECTED_DECISIONS
    for row in artifacts:
        assert row["validator_payload"]["schema_version"] == RESULT_SCHEMA_VERSION
        assert row["validator_payload"]["valid"] is True
        assert row["validator_payload"]["errors"] == []


def test_alignment_checks_cover_v55_drift_helpers_and_v56_closure(
    tmp_path: Path,
) -> None:
    report = build_clean_report(tmp_path)
    schema_drift = report["checks"]["schema_drift_alignment"]
    v56_alignment = report["checks"]["v56_alignment"]

    assert schema_drift["schema_fact_snapshot"]["status"] == "PASS"
    assert schema_drift["synthetic_mutations"]["status"] == "PASS"
    assert v56_alignment["passed"] is True
    assert v56_alignment["schema_version"] == "risk_engine_closure_evidence.v1"
    assert {
        "adapter_proof",
        "decision_replay",
        "validator_alignment",
        "schema_drift_alignment",
        "scope_guard",
    }.issubset(set(v56_alignment["checks_present"]))


def test_scope_guard_classifies_allowed_forbidden_and_unexpected_paths(
    tmp_path: Path,
) -> None:
    report = closure.build_risk_gate_closure_evidence(
        report_dir=tmp_path,
        diff_base="phase-base",
        committed_changed_paths=[
            ".planning/ROADMAP.md",
            ".planning/phases/137-closure-evidence/137-01-SUMMARY.md",
            "scripts/generate_risk_gate_closure_evidence.py",
            "tests/test_generate_risk_gate_closure_evidence.py",
            "reports/v5.7/risk_gate_closure_evidence.json",
            "reports/v5.7/risk_gate/size/example.json",
        ],
        changed_paths=[
            "rust/side-cli/src/cmd/scan.rs",
            "risk/engine.py",
            "docs/reports/v4.13/archive.md",
        ],
        staged_changed_paths=["reports/v5.7/risk_gate_closure_evidence.md"],
        untracked_paths=[
            "paper_trading/new_guard.py",
            "unexpected.txt",
        ],
    )

    scope = report["checks"]["scope_guard"]

    assert scope["passed"] is False
    assert ".planning/phases/137-closure-evidence/137-01-SUMMARY.md" in scope[
        "allowed_phase137_paths"
    ]
    assert "reports/v5.7/risk_gate_closure_evidence.json" in scope[
        "allowed_phase137_paths"
    ]
    assert "reports/v5.7/risk_gate/size/example.json" in scope[
        "allowed_phase137_paths"
    ]
    assert "rust/side-cli/src/cmd/scan.rs" in scope["forbidden_runtime_paths"]
    assert "risk/engine.py" in scope["forbidden_runtime_paths"]
    assert "paper_trading/new_guard.py" in scope["forbidden_runtime_paths"]
    assert "docs/reports/v4.13/archive.md" in scope[
        "forbidden_v4_archive_paths"
    ]
    assert "unexpected.txt" in scope["unexpected_paths"]


def test_scope_guard_fails_closed_without_phase137_diff_base() -> None:
    scope = closure.collect_scope_guard(
        diff_base=None,
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
    )

    assert scope["committed_command"]["exit_code"] == 1
    assert "missing Phase 137 diff base" in scope["committed_command"]["stderr"]
    assert scope["passed"] is False


def test_cli_writes_v57_json_markdown_and_artifacts(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--report-dir",
            str(tmp_path),
            "--diff-base",
            "HEAD",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    json_report = tmp_path / "risk_gate_closure_evidence.json"
    md_report = tmp_path / "risk_gate_closure_evidence.md"
    assert json_report.exists()
    assert md_report.exists()

    report = json.loads(json_report.read_text(encoding="utf-8"))
    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert "## Audit Summary" in md_report.read_text(encoding="utf-8")
    for row in report["checks"]["artifact_validation"]["artifacts"]:
        assert (ROOT / row["artifact_path"]).exists()


def test_committed_v57_reports_and_artifacts_are_replayable() -> None:
    if not REPORT_JSON.exists() or not REPORT_MD.exists():
        pytest.skip("committed Phase 137 reports are generated in Task 3")

    report = load_committed_report()
    markdown = REPORT_MD.read_text(encoding="utf-8")

    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["phase"] == 137
    assert report["requirements_addressed"] == [
        "CLOSE-01",
        "CLOSE-02",
        "CLOSE-03",
        "CLOSE-04",
    ]
    assert set(report["checks"]) == EXPECTED_CHECKS
    assert report["summary"]["overall_status"] == "PASS"
    assert "## Audit Summary" in markdown
    for decision_class in EXPECTED_DECISIONS:
        assert f"| `{decision_class}` |" in markdown
    for row in report["checks"]["artifact_validation"]["artifacts"]:
        assert (ROOT / row["artifact_path"]).exists()
        assert row["validator_payload"]["schema_version"] == RESULT_SCHEMA_VERSION
        assert row["validator_payload"]["valid"] is True
        assert row["validator_payload"]["errors"] == []
