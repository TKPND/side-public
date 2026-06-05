"""Contract tests for Phase 133 risk engine closure evidence generation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import generate_risk_engine_closure_evidence as closure


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_risk_engine_closure_evidence.py"
REPORT_JSON = ROOT / "reports" / "v5.6" / "risk_engine_closure_evidence.json"
REPORT_MD = ROOT / "reports" / "v5.6" / "risk_engine_closure_evidence.md"
EVIDENCE_SCHEMA_VERSION = "risk_engine_closure_evidence.v1"
RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
EXPECTED_CHECKS = {
    "adapter_proof",
    "decision_replay",
    "validator_alignment",
    "schema_drift_alignment",
    "scope_guard",
}
EXPECTED_SCENARIOS = {
    "scenario.size",
    "scenario.cap",
    "scenario.reject",
    "scenario.kill",
    "scenario.block",
}


def build_clean_report() -> dict:
    return closure.build_risk_engine_closure_evidence(
        diff_base="HEAD",
        committed_changed_paths=[],
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
    )


def load_committed_report() -> dict:
    return json.loads(REPORT_JSON.read_text(encoding="utf-8"))


def test_phase133_builds_top_level_closure_evidence_contract() -> None:
    report = build_clean_report()

    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["phase"] == 133
    assert report["requirements_addressed"] == [
        "BTADAPT-01",
        "BTADAPT-02",
        "BTADAPT-03",
        "EVID-01",
        "EVID-02",
    ]
    assert set(report["checks"]) == EXPECTED_CHECKS
    assert report["summary"]["overall_status"] == "PASS"
    assert report["summary"]["checks_failed"] == 0


def test_decision_replay_covers_representative_engine_decision_classes() -> None:
    report = build_clean_report()
    decision_replay = report["checks"]["decision_replay"]

    assert decision_replay["passed"] is True
    assert {case["id"] for case in decision_replay["scenarios"]} == EXPECTED_SCENARIOS
    assert {case["decision_class"] for case in decision_replay["scenarios"]} == {
        "size",
        "cap",
        "reject",
        "kill",
        "block",
    }
    for case in decision_replay["scenarios"]:
        assert case["validator_payload"]["schema_version"] == RESULT_SCHEMA_VERSION
        assert case["validator_payload"]["valid"] is True
        assert case["decision_trace_aligned"] is True
        assert case["artifact"]["decision"]["decision_class"] == case["artifact"]["trace"]["decision_class"]
        assert case["artifact"]["decision"]["binding_rule"] == case["artifact"]["trace"]["binding_rule"]
        assert case["scenario_ref"].endswith("#" + case["id"])

    by_id = {case["id"]: case for case in decision_replay["scenarios"]}
    assert by_id["scenario.kill"]["artifact"]["decision"]["fail_close_reason"] == "stale_evidence"
    assert by_id["scenario.block"]["artifact"]["decision"]["fail_close_reason"] == "malformed_policy"


def test_adapter_proof_check_records_tmp_path_validator_alignment() -> None:
    report = build_clean_report()
    adapter = report["checks"]["adapter_proof"]

    assert adapter["passed"] is True
    assert adapter["artifact"]["decision"]["decision_class"] == "size"
    assert adapter["validator_payload"]["valid"] is True
    assert "phase133-adapter-proof-" in adapter["artifact_path"]


def test_scope_guard_classifies_allowed_forbidden_and_unexpected_paths() -> None:
    report = closure.build_risk_engine_closure_evidence(
        diff_base="phase-base",
        committed_changed_paths=[
            ".planning/ROADMAP.md",
            ".planning/phases/133-backtest-adapter-proof-and-evidence-closure/133-01-SUMMARY.md",
            "scripts/generate_risk_engine_closure_evidence.py",
            "tests/test_risk_engine_backtest_adapter.py",
        ],
        changed_paths=[
            "reports/v5.6/risk_engine_closure_evidence.json",
            "backtest/modified_runtime.py",
            "risk/engine.py",
            "docs/reports/v4.13/archive.md",
        ],
        staged_changed_paths=["reports/v5.6/risk_engine_closure_evidence.md"],
        untracked_paths=[
            "paper_trading/new_guard.py",
            "rust/side-cli/src/main.rs",
            "unexpected.txt",
        ],
    )

    scope = report["checks"]["scope_guard"]

    assert scope["passed"] is False
    assert "backtest/modified_runtime.py" in scope["forbidden_runtime_paths"]
    assert "paper_trading/new_guard.py" in scope["forbidden_runtime_paths"]
    assert "rust/side-cli/src/main.rs" in scope["forbidden_runtime_paths"]
    assert "risk/engine.py" in scope["forbidden_runtime_paths"]
    assert "docs/reports/v4.13/archive.md" in scope["forbidden_v4_archive_paths"]
    assert "unexpected.txt" in scope["unexpected_paths"]
    assert (
        ".planning/phases/133-backtest-adapter-proof-and-evidence-closure/133-01-SUMMARY.md"
        in scope["allowed_phase133_paths"]
    )
    assert "scripts/generate_risk_engine_closure_evidence.py" in scope["allowed_phase133_paths"]
    assert "tests/test_risk_engine_backtest_adapter.py" in scope["allowed_phase133_paths"]
    assert "reports/v5.6/risk_engine_closure_evidence.json" in scope["allowed_phase133_paths"]
    assert "reports/v5.6/risk_engine_closure_evidence.md" in scope["allowed_phase133_paths"]


def test_scope_guard_fails_closed_without_phase133_diff_base() -> None:
    scope = closure.collect_scope_guard(
        diff_base=None,
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
    )

    assert scope["committed_command"]["exit_code"] == 1
    assert "missing Phase 133 diff base" in scope["committed_command"]["stderr"]
    assert scope["passed"] is False


def test_phase133_diff_base_discovery_uses_phase133_planning_path(
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
            closure.PHASE133_DIR,
        ]:
            return subprocess.CompletedProcess(command, 0, "first-phase133-commit\n", "")
        if command == ["git", "rev-parse", "first-phase133-commit^"]:
            return subprocess.CompletedProcess(command, 0, "phase133-parent\n", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(closure.subprocess, "run", fake_run)

    assert closure.discover_phase133_diff_base() == "phase133-parent"
    assert commands == [
        [
            "git",
            "log",
            "--reverse",
            "--format=%H",
            "--",
            closure.PHASE133_DIR,
        ],
        ["git", "rev-parse", "first-phase133-commit^"],
    ]


def test_markdown_report_is_audit_summary_first() -> None:
    markdown = closure.render_markdown(build_clean_report())

    assert (
        markdown.index("## Audit Summary")
        < markdown.index("| Check | Status | Detail |")
        < markdown.index("## Summary")
    )
    for check in EXPECTED_CHECKS:
        assert f"| {check} | PASS |" in markdown


def test_cli_writes_v56_json_and_markdown_reports(tmp_path: Path) -> None:
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

    json_report = tmp_path / "risk_engine_closure_evidence.json"
    md_report = tmp_path / "risk_engine_closure_evidence.md"

    assert json_report.exists()
    assert md_report.exists()
    assert json.loads(json_report.read_text(encoding="utf-8"))["schema_version"] == (
        EVIDENCE_SCHEMA_VERSION
    )
    markdown = md_report.read_text(encoding="utf-8")
    assert "## Audit Summary" in markdown
    assert "| Check | Status | Detail |" in markdown


def test_committed_v56_reports_include_required_closure_checks() -> None:
    report = load_committed_report()
    markdown = REPORT_MD.read_text(encoding="utf-8")

    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["phase"] == 133
    assert report["requirements_addressed"] == [
        "BTADAPT-01",
        "BTADAPT-02",
        "BTADAPT-03",
        "EVID-01",
        "EVID-02",
    ]
    assert set(report["checks"]) == EXPECTED_CHECKS
    assert report["summary"]["overall_status"] == "PASS"
    for check in (
        "adapter_proof",
        "decision_replay",
        "validator_alignment",
        "schema_drift_alignment",
        "scope_guard",
    ):
        assert f"| {check} | PASS |" in markdown


def test_committed_v56_decision_replay_is_replayable_and_validator_aligned() -> None:
    report = load_committed_report()
    markdown = REPORT_MD.read_text(encoding="utf-8")
    scenarios = report["checks"]["decision_replay"]["scenarios"]

    assert {scenario["id"] for scenario in scenarios} == EXPECTED_SCENARIOS
    for scenario in scenarios:
        assert scenario["validator_payload"]["schema_version"] == RESULT_SCHEMA_VERSION
        assert scenario["validator_payload"]["valid"] is True
        assert scenario["passed"] is True
        assert scenario["decision_trace_aligned"] is True
        assert scenario["artifact"]["decision"]["decision_class"] == scenario["artifact"]["trace"]["decision_class"]
        assert scenario["artifact"]["decision"]["binding_rule"] == scenario["artifact"]["trace"]["binding_rule"]
        assert scenario["id"] in markdown

    assert (
        "| Scenario | Decision class | Allowed size | Fail-close reason | "
        "Validator valid | Trace aligned | Passed |"
    ) in markdown


def test_committed_v56_scope_guard_proves_phase_boundary() -> None:
    report = load_committed_report()
    scope = report["checks"]["scope_guard"]

    assert scope["passed"] is True
    assert scope["forbidden_runtime_paths"] == []
    assert scope["forbidden_v4_archive_paths"] == []
    assert scope["unexpected_paths"] == []
    assert "committed_changed_paths" in scope
    assert "unstaged_changed_paths" in scope
    assert "staged_changed_paths" in scope
    assert "untracked_paths" in scope
    assert any(
        path.startswith("reports/v5.6/")
        for path in scope["allowed_phase133_paths"]
    )
    assert "risk/engine.py" not in scope["allowed_phase133_paths"]
