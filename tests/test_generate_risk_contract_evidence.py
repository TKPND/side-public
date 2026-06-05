"""Contract tests for Phase 128 risk contract evidence generation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import generate_risk_contract_evidence as evidence
from scripts import generate_risk_contract_drift_gate_evidence as drift_evidence


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_risk_contract_evidence.py"
MATRIX = ROOT / "risk" / "contracts" / "v1" / "fixture_matrix.json"
REPORT_JSON = ROOT / "reports" / "v5.4" / "risk_contract_validation_evidence.json"
REPORT_MD = ROOT / "reports" / "v5.4" / "risk_contract_validation_evidence.md"
RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
EVIDENCE_SCHEMA_VERSION = "risk_contract_validation_evidence.v1"
DRIFT_EVIDENCE_SCHEMA_VERSION = "risk_contract_drift_gate_evidence.v1"
DRIFT_REPORT_JSON = ROOT / "reports" / "v5.5" / "risk_contract_drift_gate_evidence.json"
DRIFT_REPORT_MD = ROOT / "reports" / "v5.5" / "risk_contract_drift_gate_evidence.md"
ARCHIVE_DIFF_COMMAND = (
    "git diff --name-only -- .planning/milestones/v4* data/v4* docs/reports/v4*"
)
DEFERRED_NOTES = [
    "common risk module",
    "paper guard",
    "strategy integration",
    "Rust CLI parity",
    "runtime behavior changes",
]


def load_matrix() -> list[dict]:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    return matrix["fixtures"]


def fixture_by_id(fixtures: list[dict]) -> dict[str, dict]:
    return {fixture["id"]: fixture for fixture in fixtures}


def load_committed_report() -> dict:
    return json.loads(REPORT_JSON.read_text(encoding="utf-8"))


def test_d01_d02_d04_builds_top_level_evidence_contract() -> None:
    report = evidence.build_risk_contract_evidence()

    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["phase"] == 128
    assert report["requirements_addressed"] == [
        "RISKVAL-08",
        "RISKVAL-09",
        "RISKVAL-10",
    ]


def test_d02_d06_d07_replays_every_fixture_with_required_fields() -> None:
    report = evidence.build_risk_contract_evidence()
    matrix_fixtures = fixture_by_id(load_matrix())
    replay_fields = {
        "id",
        "path",
        "description",
        "command",
        "exit_code",
        "validator_payload",
        "expected_valid",
        "expected_error",
        "actual_error",
        "exit_code_matches",
        "validity_matches",
        "error_code_matches",
        "passed",
    }

    assert {fixture["id"] for fixture in report["fixtures"]} == set(matrix_fixtures)
    assert len(report["fixtures"]) == len(matrix_fixtures)

    for fixture in report["fixtures"]:
        expected = matrix_fixtures[fixture["id"]]
        assert replay_fields <= set(fixture)
        assert fixture["path"] == expected["path"]
        assert fixture["description"] == expected["description"]
        assert fixture["expected_valid"] is expected["valid"]
        assert fixture["expected_error"] == expected["expected_error"]
        assert fixture["command"] == [
            "python",
            "scripts/validate_risk_contract.py",
            expected["path"],
        ]
        assert isinstance(fixture["exit_code"], int)
        assert isinstance(fixture["exit_code_matches"], bool)
        assert isinstance(fixture["validity_matches"], bool)
        assert isinstance(fixture["error_code_matches"], bool)
        assert fixture["passed"] is (
            fixture["exit_code_matches"]
            and fixture["validity_matches"]
            and fixture["error_code_matches"]
        )


def test_d09_preserves_validator_result_schema_for_every_fixture() -> None:
    report = evidence.build_risk_contract_evidence()

    for fixture in report["fixtures"]:
        assert fixture["validator_payload"]["schema_version"] == RESULT_SCHEMA_VERSION


def test_d10_d11_d12_archive_proof_uses_scoped_diff_without_hash_manifest() -> None:
    working = subprocess.CompletedProcess(
        args=["git", "diff"],
        returncode=0,
        stdout="",
        stderr="",
    )
    committed = subprocess.CompletedProcess(
        args=["git", "diff"],
        returncode=0,
        stdout="docs/reports/v4.13/archive.md\n",
        stderr="",
    )

    report = evidence.build_risk_contract_evidence(
        diff_base="phase-base",
        archive_diff_result=working,
        committed_archive_diff_result=committed,
        staged_archive_diff_result=working,
    )

    assert report["archive_proof"]["command"] == ARCHIVE_DIFF_COMMAND
    assert report["archive_proof"]["committed_command"]["command"] == (
        "git diff --name-only phase-base..HEAD -- "
        ".planning/milestones/v4* data/v4* docs/reports/v4*"
    )
    assert report["archive_proof"]["modified_archive_paths"] == [
        "docs/reports/v4.13/archive.md"
    ]
    assert report["archive_proof"]["passed"] is False
    assert "hash_manifest" not in report["archive_proof"]


def test_d13_d14_d16_runtime_scope_classifies_allowed_unrelated_and_forbidden_paths() -> None:
    report = evidence.build_risk_contract_evidence(
        diff_base="phase-base",
        committed_changed_paths=[
            ".planning/ROADMAP.md",
            "scripts/generate_risk_contract_evidence.py",
            "tests/test_generate_risk_contract_evidence.py",
        ],
        changed_paths=[
            "reports/v5.4/risk_contract_validation_evidence.json",
            "AGENTS.md",
            ".planning/PROJECT.md",
            "backtest/modified_runtime.py",
            "docs/reports/v4.13/archive.md",
        ],
        staged_changed_paths=["reports/v5.4/risk_contract_validation_evidence.md"],
        untracked_paths=[
            ".planning/phases/128-evidence-report-and-closure-gates/128-01-SUMMARY.md",
            "backtest/untracked_runtime.py",
        ],
    )

    proof = report["runtime_scope_proof"]

    assert {
        "committed_changed_paths",
        "unstaged_changed_paths",
        "staged_changed_paths",
        "untracked_paths",
        "allowed_phase128_paths",
        "pre_existing_unrelated_paths",
        "forbidden_runtime_paths",
        "forbidden_v4_archive_paths",
    } <= set(proof)
    assert "backtest/untracked_runtime.py" in proof["forbidden_runtime_paths"]
    assert "backtest/modified_runtime.py" in proof["forbidden_runtime_paths"]
    assert "AGENTS.md" in proof["pre_existing_unrelated_paths"]
    assert ".planning/PROJECT.md" in proof["pre_existing_unrelated_paths"]
    assert "docs/reports/v4.13/archive.md" in proof["forbidden_v4_archive_paths"]
    assert ".planning/ROADMAP.md" in proof["allowed_phase128_paths"]
    assert "tests/test_generate_risk_contract_evidence.py" in proof["allowed_phase128_paths"]
    assert "reports/v5.4/risk_contract_validation_evidence.md" in proof["allowed_phase128_paths"]
    assert proof["committed_command"]["command"] == "git diff --name-only phase-base..HEAD"


def test_d13_runtime_scope_fails_closed_when_git_path_commands_fail() -> None:
    failed = subprocess.CompletedProcess(
        args=["git", "diff"],
        returncode=128,
        stdout="",
        stderr="fatal: not a git repository",
    )

    report = evidence.build_risk_contract_evidence(
        diff_base="phase-base",
        committed_result=failed,
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
    )

    proof = report["runtime_scope_proof"]

    assert proof["commands_passed"] is False
    assert proof["passed"] is False
    assert report["summary"]["runtime_scope_within_allowlist"] is False
    assert report["summary"]["close_readiness"] == "blocked"


def test_d10_d13_missing_phase_diff_base_blocks_closure_proofs() -> None:
    clean = subprocess.CompletedProcess(
        args=["git", "diff"],
        returncode=0,
        stdout="",
        stderr="",
    )

    archive_proof = evidence.collect_archive_proof(
        archive_diff_result=clean,
        staged_archive_diff_result=clean,
        diff_base=None,
    )
    runtime_scope_proof = evidence.collect_runtime_scope_proof(
        diff_base=None,
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
    )

    assert archive_proof["committed_command"]["exit_code"] == 1
    assert "missing Phase 128 diff base" in (
        archive_proof["committed_command"]["stderr"]
    )
    assert archive_proof["passed"] is False
    assert runtime_scope_proof["committed_command"]["exit_code"] == 1
    assert "missing Phase 128 diff base" in (
        runtime_scope_proof["committed_command"]["stderr"]
    )
    assert runtime_scope_proof["passed"] is False


def test_d03_d05_d15_markdown_includes_fixture_table_and_deferrals() -> None:
    report = evidence.build_risk_contract_evidence()
    markdown = evidence.render_markdown(report)

    assert "| Fixture | Expected valid | Exit code | Actual error | Passed |" in markdown
    for fixture in load_matrix():
        assert fixture["id"] in markdown
    for note in DEFERRED_NOTES:
        assert note in markdown


def test_d01_d03_cli_writes_json_and_markdown_reports(tmp_path: Path) -> None:
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

    json_report = tmp_path / "risk_contract_validation_evidence.json"
    md_report = tmp_path / "risk_contract_validation_evidence.md"

    assert json_report.exists()
    assert md_report.exists()
    assert json.loads(json_report.read_text(encoding="utf-8"))["schema_version"] == (
        EVIDENCE_SCHEMA_VERSION
    )
    assert "| Fixture | Expected valid | Exit code | Actual error | Passed |" in (
        md_report.read_text(encoding="utf-8")
    )


def test_riskval_08_committed_reports_contain_replayable_fixture_details() -> None:
    report = load_committed_report()
    markdown = REPORT_MD.read_text(encoding="utf-8")
    matrix_fixtures = fixture_by_id(load_matrix())

    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["phase"] == 128
    assert report["requirements_addressed"] == [
        "RISKVAL-08",
        "RISKVAL-09",
        "RISKVAL-10",
    ]
    assert report["summary"]["fixture_count"] == len(matrix_fixtures)
    assert report["summary"]["failed_count"] == 0
    assert {fixture["id"] for fixture in report["fixtures"]} == set(matrix_fixtures)

    for fixture in report["fixtures"]:
        expected = matrix_fixtures[fixture["id"]]
        assert fixture["path"] == expected["path"]
        assert fixture["description"] == expected["description"]
        assert fixture["command"] == [
            "python",
            "scripts/validate_risk_contract.py",
            expected["path"],
        ]
        assert isinstance(fixture["exit_code"], int)
        assert isinstance(fixture["stdout"], str)
        assert fixture["validator_payload"]["schema_version"] == RESULT_SCHEMA_VERSION
        assert fixture["validator_payload"]["checked_path"] == expected["path"]
        assert fixture["expected_valid"] is expected["valid"]
        assert fixture["expected_error"] == expected["expected_error"]
        assert fixture["passed"] is True

        assert f"### {fixture['id']}" in markdown
        assert f"- Command: `python scripts/validate_risk_contract.py {expected['path']}`" in markdown
        assert RESULT_SCHEMA_VERSION in markdown

    assert "| Fixture | Expected valid | Exit code | Actual error | Passed |" in markdown


def test_riskval_09_10_committed_reports_prove_archive_and_runtime_closure() -> None:
    report = load_committed_report()
    markdown = REPORT_MD.read_text(encoding="utf-8")
    archive = report["archive_proof"]
    scope = report["runtime_scope_proof"]

    assert archive["command"] == ARCHIVE_DIFF_COMMAND
    assert archive["modified_archive_paths"] == []
    assert archive["passed"] is True
    assert ARCHIVE_DIFF_COMMAND in markdown

    assert "unstaged_changed_paths" in scope
    assert "staged_changed_paths" in scope
    assert "untracked_paths" in scope
    assert "allowed_phase128_paths" in scope
    assert "pre_existing_unrelated_paths" in scope
    assert scope["forbidden_runtime_paths"] == []
    assert scope["forbidden_v4_archive_paths"] == []
    assert scope["passed"] is True
    assert report["summary"]["runtime_scope_within_allowlist"] is True
    assert report["summary"]["close_readiness"] == "ready_for_milestone_completion"

    for note in DEFERRED_NOTES:
        assert any(note in closure_note for closure_note in report["closure_notes"])
        assert note in markdown


def build_clean_drift_report() -> dict:
    return drift_evidence.build_drift_gate_evidence(
        diff_base="HEAD",
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
        committed_changed_paths=[],
    )


def test_gate_01_02_builds_drift_gate_evidence_contract() -> None:
    report = build_clean_drift_report()

    assert report["schema_version"] == DRIFT_EVIDENCE_SCHEMA_VERSION
    assert report["phase"] == 130
    assert report["requirements_addressed"] == ["GATE-01", "GATE-02", "EVID-01"]
    assert set(report["checks"]) == {
        "schema_fact_snapshot",
        "synthetic_mutations",
        "fixture_replay",
        "runtime_scope",
        "v4_archive_scope",
    }


def test_gate_01_02_evidence_records_schema_snapshot_and_synthetic_mutations() -> None:
    report = build_clean_drift_report()

    assert report["checks"]["schema_fact_snapshot"]["passed"] is True
    assert report["checks"]["synthetic_mutations"]["passed"] is True
    assert [
        case["id"]
        for case in report["checks"]["synthetic_mutations"]["cases"]
    ] == [
        "decision_class_vocab_added",
        "fail_close_reason_added",
        "top_level_required_added",
        "policy_required_added",
        "fail_close_rule_required_added",
    ]
    assert all(
        case["drift_detected"] is True
        for case in report["checks"]["synthetic_mutations"]["cases"]
    )


def test_evid_01_v55_runtime_and_archive_scope_use_phase130_allowlist() -> None:
    report = drift_evidence.build_drift_gate_evidence(
        diff_base="phase-base",
        committed_changed_paths=[
            ".planning/REQUIREMENTS.md",
            ".planning/phases/130-drift-gate-and-evidence-closure/130-01-SUMMARY.md",
            "scripts/generate_risk_contract_drift_gate_evidence.py",
        ],
        changed_paths=[
            "reports/v5.5/risk_contract_drift_gate_evidence.json",
            "backtest/modified_runtime.py",
            "docs/reports/v4.13/archive.md",
        ],
        staged_changed_paths=["reports/v5.5/risk_contract_drift_gate_evidence.md"],
        untracked_paths=["paper_trading/new_guard.py", "side-engine/src/lib.rs"],
    )

    runtime = report["checks"]["runtime_scope"]
    archive = report["checks"]["v4_archive_scope"]

    assert "backtest/modified_runtime.py" in runtime["forbidden_runtime_paths"]
    assert "paper_trading/new_guard.py" in runtime["forbidden_runtime_paths"]
    assert "side-engine/src/lib.rs" in runtime["forbidden_runtime_paths"]
    assert ".planning/REQUIREMENTS.md" in runtime["allowed_phase130_paths"]
    assert "docs/reports/v4.13/archive.md" in archive["modified_archive_paths"]
    assert runtime["passed"] is False
    assert archive["passed"] is False


def test_evid_01_phase130_diff_base_discovery_uses_phase130_planning_path(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        assert kwargs["cwd"] == ROOT
        assert kwargs["text"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        if command == [
            "git",
            "log",
            "--reverse",
            "--format=%H",
            "--",
            drift_evidence.PHASE130_DIR,
        ]:
            return subprocess.CompletedProcess(command, 0, "first-phase130-commit\n", "")
        if command == ["git", "rev-parse", "first-phase130-commit^"]:
            return subprocess.CompletedProcess(command, 0, "phase130-parent\n", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(drift_evidence.subprocess, "run", fake_run)

    assert drift_evidence.discover_phase130_diff_base() == "phase130-parent"
    assert commands == [
        [
            "git",
            "log",
            "--reverse",
            "--format=%H",
            "--",
            drift_evidence.PHASE130_DIR,
        ],
        ["git", "rev-parse", "first-phase130-commit^"],
    ]


def test_evid_01_v55_scope_proofs_fail_closed_without_phase130_diff_base() -> None:
    runtime = drift_evidence.collect_runtime_scope(
        diff_base=None,
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
    )
    archive = drift_evidence.collect_v4_archive_scope(
        diff_base=None,
        changed_paths=[],
        staged_changed_paths=[],
    )

    assert runtime["committed_command"]["exit_code"] == 1
    assert "missing Phase 130 diff base" in runtime["committed_command"]["stderr"]
    assert runtime["passed"] is False
    assert archive["committed_command"]["exit_code"] == 1
    assert "missing Phase 130 diff base" in archive["committed_command"]["stderr"]
    assert archive["passed"] is False


def test_evid_01_markdown_report_is_audit_summary_first() -> None:
    markdown = DRIFT_REPORT_MD.read_text(encoding="utf-8")

    audit_summary = markdown.index("## Audit Summary")
    check_table = markdown.index("| Check | Status | Detail |")
    summary = markdown.index("## Summary")
    schema_details = markdown.index("## Schema Fact Snapshot")

    assert audit_summary < check_table < summary < schema_details
    assert markdown.index("| Overall status | PASS |") < schema_details


def test_evid_01_drift_gate_cli_writes_v55_json_and_markdown_reports(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_risk_contract_drift_gate_evidence.py"),
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

    json_report = tmp_path / "risk_contract_drift_gate_evidence.json"
    md_report = tmp_path / "risk_contract_drift_gate_evidence.md"

    assert json_report.exists()
    assert md_report.exists()
    assert json.loads(json_report.read_text(encoding="utf-8"))["schema_version"] == (
        DRIFT_EVIDENCE_SCHEMA_VERSION
    )
    assert "## Audit Summary" in md_report.read_text(encoding="utf-8")
    assert "| Check | Status | Detail |" in md_report.read_text(encoding="utf-8")


def test_evid_01_committed_v55_reports_include_drift_gate_status() -> None:
    report = json.loads(DRIFT_REPORT_JSON.read_text(encoding="utf-8"))
    markdown = DRIFT_REPORT_MD.read_text(encoding="utf-8")

    assert report["schema_version"] == DRIFT_EVIDENCE_SCHEMA_VERSION
    assert report["summary"]["overall_status"] == "PASS"
    assert set(report["checks"]) == {
        "schema_fact_snapshot",
        "synthetic_mutations",
        "fixture_replay",
        "runtime_scope",
        "v4_archive_scope",
    }
    assert "# v5.5 Risk Contract Drift Gate Evidence" in markdown
    assert "## Audit Summary" in markdown
    assert "| schema_fact_snapshot | PASS |" in markdown
    assert "| synthetic_mutations | PASS |" in markdown
    assert "| fixture_replay | PASS |" in markdown
    assert "| runtime_scope | PASS |" in markdown
    assert "| v4_archive_scope | PASS |" in markdown
