"""Contract tests for the risk_contract.v2 adoption closure audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import generate_risk_contract_v2_adoption_closure_audit as audit


SCHEMA_VERSION = "risk_contract_v2_adoption_closure_audit.v1"
EXPECTED_SURFACES = {"backtest", "scan", "paper"}
EXPECTED_DECISIONS = {"cap", "size", "reject"}


def sample_surface_report(tmp_path: Path, surface: str) -> dict:
    report_dir = tmp_path / surface
    runs: dict[str, dict] = {}
    for decision_class in sorted(EXPECTED_DECISIONS):
        artifact_path = (
            report_dir
            / "runs"
            / decision_class
            / "risk_artifacts"
            / "decisions"
            / f"{surface}.{decision_class}.json"
        )
        runs[decision_class] = {
            "run_name": decision_class,
            "decision_class": decision_class,
            "execution_state": "stopped" if decision_class == "reject" else "continued",
            "contract_version": "v2",
            "schema_version": "risk_contract.v2",
            "validator_result_schema_version": "risk_contract_validator_result.v2",
            "validator_valid": True,
            "artifact_path": artifact_path.as_posix(),
            "passed": True,
        }
    return {
        "schema_version": f"risk_contract_v2_{surface}_runtime_adoption_evidence.v1",
        "boundary": f"risk_contract_v2_{surface}_runtime_adoption_evidence",
        "summary": {
            "overall_status": "PASS",
            "checks_passed": 7,
            "checks_failed": 0,
            "implementation_scope": f"{surface}_v2_evidence_replay_only",
        },
        "runs": runs,
        "checks": {
            "validator_replay": {
                "passed": True,
                "validated_artifacts": 3,
            }
        },
    }


def sample_live_fixture() -> dict:
    return {
        "schema_version": "risk_contract.v2",
        "contract_version": "v2",
        "candidate": {
            "surface": {
                "runtime_surface": "live",
                "surface_status": "not_wired",
                "analysis_scope": "none",
            }
        },
        "application": {
            "execution_state": "stopped",
            "application_status": "not_claimable",
            "runtime_sizing_applied": False,
            "sizing_effect": "not_applicable",
            "metrics_rescaled": False,
        },
    }


def sample_validator_payload(*, valid: bool = True) -> dict:
    return {
        "schema_version": "risk_contract_validator_result.v2",
        "valid": valid,
        "contract_identity": {
            "schema_version": "risk_contract.v2",
            "contract_version": "v2",
        },
        "validated_schema": {
            "path": "risk/contracts/v2/risk_contract_v2.schema.json",
        },
        "dispatch": {"status": "validated", "reason": None},
        "errors": [] if valid else [{"path": "$", "message": "invalid"}],
    }


def test_builds_top_level_v2_adoption_closure_contract(tmp_path: Path) -> None:
    reports = {
        surface: sample_surface_report(tmp_path, surface)
        for surface in sorted(EXPECTED_SURFACES)
    }
    report = audit.build_adoption_closure_audit(
        report_dir=tmp_path / "adoption-closure",
        diff_base="HEAD",
        surface_reports=reports,
        live_fixture=sample_live_fixture(),
        validator_payloads={
            (surface, decision): sample_validator_payload()
            for surface in EXPECTED_SURFACES
            for decision in EXPECTED_DECISIONS
        },
        live_validator_payload=sample_validator_payload(),
        changed_paths=[
            "scripts/generate_risk_contract_v2_adoption_closure_audit.py",
            "tests/test_generate_risk_contract_v2_adoption_closure_audit.py",
            "reports/risk-contract-v2/adoption-closure/risk_contract_v2_adoption_closure_audit.json",
        ],
    )

    assert audit.SCHEMA_VERSION == SCHEMA_VERSION
    assert audit.DEFAULT_REPORT_DIR == Path("reports/risk-contract-v2/adoption-closure")
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["boundary"] == "risk_contract_v2_adoption_closure_audit"
    assert report["summary"]["overall_status"] == "PASS"
    assert report["summary"]["implementation_scope"] == "v2_adoption_closure_audit_only"
    assert report["summary"]["surfaces_closed"] == ["backtest", "paper", "scan"]
    assert report["summary"]["live_runtime_claim"] == "not_claimable"
    assert report["summary"]["checks_failed"] == 0


def test_closure_checks_cover_surface_matrix_validator_replay_live_and_scope(tmp_path: Path) -> None:
    reports = {
        surface: sample_surface_report(tmp_path, surface)
        for surface in sorted(EXPECTED_SURFACES)
    }
    report = audit.build_adoption_closure_audit(
        report_dir=tmp_path / "adoption-closure",
        diff_base="origin/master",
        surface_reports=reports,
        live_fixture=sample_live_fixture(),
        validator_payloads={
            (surface, decision): sample_validator_payload()
            for surface in EXPECTED_SURFACES
            for decision in EXPECTED_DECISIONS
        },
        live_validator_payload=sample_validator_payload(),
        changed_paths=[
            "docs/plans/2026-05-18-risk-contract-v2-adoption-closure-audit-tdd.md",
            "reports/risk-contract-v2/adoption-closure/risk_contract_v2_adoption_closure_audit.md",
        ],
    )

    checks = report["checks"]
    assert checks["surface_reports_pass"]["passed"] is True
    assert checks["decision_class_matrix"]["passed"] is True
    assert checks["decision_class_matrix"]["expected_decisions"] == ["cap", "reject", "size"]
    assert checks["validator_replay"]["passed"] is True
    assert checks["validator_replay"]["validated_artifacts"] == 9
    assert checks["validator_replay"]["live_fixture_validated"] is True
    assert checks["live_not_claimable"]["passed"] is True
    assert checks["live_not_claimable"]["surface_status"] == "not_wired"
    assert checks["live_not_claimable"]["application_status"] == "not_claimable"
    assert checks["protected_surface_guard"]["passed"] is True
    assert checks["no_runtime_expansion"]["passed"] is True
    assert checks["no_runtime_expansion"]["live_runtime_adoption"] == "not_approved"


def test_closure_audit_carries_live_runtime_surface_absence_guard(tmp_path: Path) -> None:
    report = audit.build_adoption_closure_audit(
        report_dir=tmp_path / "adoption-closure",
        diff_base="HEAD",
        surface_reports={
            surface: sample_surface_report(tmp_path, surface)
            for surface in sorted(EXPECTED_SURFACES)
        },
        live_fixture=sample_live_fixture(),
        validator_payloads={
            (surface, decision): sample_validator_payload()
            for surface in EXPECTED_SURFACES
            for decision in EXPECTED_DECISIONS
        },
        live_validator_payload=sample_validator_payload(),
        changed_paths=[],
    )

    check = report["checks"]["live_runtime_surface_absent"]
    assert check["passed"] is True
    assert check["live_runtime_adoption"] == "not_approved"
    assert check["live_subcommand_present"] is False
    assert check["live_runtime_paths"] == []
    assert check["claim_block_reason"] == "live_runtime_not_implemented"

    markdown = audit.render_markdown(report)
    assert "live_runtime_surface_absent" in markdown
    assert "live_runtime_paths=0" in markdown


def test_phase_150_pins_focused_live_and_broker_runtime_absence_guard_range() -> None:
    assert audit.LIVE_RUNTIME_SURFACE_SOURCE.as_posix() == "rust/side-cli/src/main.rs"
    assert tuple(
        path.as_posix() for path in audit.LIVE_RUNTIME_IMPLEMENTATION_PATHS
    ) == (
        "rust/side-cli/src/cmd/live.rs",
        "rust/side-cli/src/cmd/live",
        "rust/side-cli/src/cmd/broker.rs",
        "rust/side-cli/src/cmd/broker",
        "rust/side-engine/src/live.rs",
        "rust/side-engine/src/live",
        "rust/side-engine/src/broker.rs",
        "rust/side-engine/src/broker",
    )
    assert audit.RUNTIME_EXPANSION_PREFIXES == (
        "rust/side-cli/src/cmd/live",
        "rust/side-cli/src/cmd/broker",
        "rust/side-engine/src/live",
        "rust/side-engine/src/broker",
    )


def test_live_runtime_surface_absence_guard_fails_when_live_runtime_is_wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main_rs = tmp_path / "rust" / "side-cli" / "src" / "main.rs"
    live_mod = tmp_path / "rust" / "side-engine" / "src" / "live" / "mod.rs"
    main_rs.parent.mkdir(parents=True)
    live_mod.parent.mkdir(parents=True)
    main_rs.write_text(
        "enum Commands { Live(LiveArgs) }\n"
        "match command { Commands::Live(args) => cmd::live::run(args).await }\n",
        encoding="utf-8",
    )
    live_mod.write_text("pub fn run_live_runtime() {}\n", encoding="utf-8")
    monkeypatch.setattr(audit, "repo_root", lambda: tmp_path)

    check = audit.live_runtime_surface_absent_check()

    assert check["passed"] is False
    assert check["live_subcommand_present"] is True
    assert check["live_runtime_paths"] == ["rust/side-engine/src/live/mod.rs"]
    assert check["claim_block_reason"] == "live_runtime_not_implemented"


def test_fails_when_a_surface_report_is_not_pass(tmp_path: Path) -> None:
    reports = {
        surface: sample_surface_report(tmp_path, surface)
        for surface in sorted(EXPECTED_SURFACES)
    }
    reports["scan"]["summary"]["overall_status"] = "FAIL"

    report = audit.build_adoption_closure_audit(
        report_dir=tmp_path / "adoption-closure",
        diff_base="HEAD",
        surface_reports=reports,
        live_fixture=sample_live_fixture(),
        validator_payloads={
            (surface, decision): sample_validator_payload()
            for surface in EXPECTED_SURFACES
            for decision in EXPECTED_DECISIONS
        },
        live_validator_payload=sample_validator_payload(),
        changed_paths=[],
    )

    assert report["summary"]["overall_status"] == "FAIL"
    assert report["checks"]["surface_reports_pass"]["passed"] is False
    assert "scan" in report["checks"]["surface_reports_pass"]["failing_surfaces"]


def test_fails_when_live_fixture_becomes_claimable(tmp_path: Path) -> None:
    live_fixture = sample_live_fixture()
    live_fixture["candidate"]["surface"]["surface_status"] = "implemented"
    live_fixture["application"]["application_status"] = "applied"
    live_fixture["application"]["runtime_sizing_applied"] = True

    report = audit.build_adoption_closure_audit(
        report_dir=tmp_path / "adoption-closure",
        diff_base="HEAD",
        surface_reports={
            surface: sample_surface_report(tmp_path, surface)
            for surface in sorted(EXPECTED_SURFACES)
        },
        live_fixture=live_fixture,
        validator_payloads={
            (surface, decision): sample_validator_payload()
            for surface in EXPECTED_SURFACES
            for decision in EXPECTED_DECISIONS
        },
        live_validator_payload=sample_validator_payload(),
        changed_paths=[],
    )

    assert report["summary"]["overall_status"] == "FAIL"
    assert report["checks"]["live_not_claimable"]["passed"] is False


def test_protected_output_and_protected_changed_paths_fail_closed(tmp_path: Path) -> None:
    reports = {
        surface: sample_surface_report(tmp_path, surface)
        for surface in sorted(EXPECTED_SURFACES)
    }

    for protected_report_dir in [Path("reports/v5.8"), Path("reports/v8.3")]:
        with pytest.raises(ValueError, match="protected output"):
            audit.build_adoption_closure_audit(
                report_dir=protected_report_dir,
                diff_base="HEAD",
                surface_reports=reports,
                live_fixture=sample_live_fixture(),
                validator_payloads={
                    (surface, decision): sample_validator_payload()
                    for surface in EXPECTED_SURFACES
                    for decision in EXPECTED_DECISIONS
                },
                live_validator_payload=sample_validator_payload(),
                changed_paths=[],
            )

    report = audit.build_adoption_closure_audit(
        report_dir=tmp_path / "adoption-closure",
        diff_base="HEAD",
        surface_reports=reports,
        live_fixture=sample_live_fixture(),
        validator_payloads={
            (surface, decision): sample_validator_payload()
            for surface in EXPECTED_SURFACES
            for decision in EXPECTED_DECISIONS
        },
        live_validator_payload=sample_validator_payload(),
        changed_paths=[
            "risk/contracts/v2/risk_contract_v2.schema.json",
            "reports/v8.3/scan_wfd_runtime_cap_application_evidence.md",
        ],
    )

    assert report["summary"]["overall_status"] == "FAIL"
    assert report["checks"]["protected_surface_guard"]["passed"] is False
    assert report["checks"]["protected_surface_guard"]["violations"] == [
        "reports/v8.3/scan_wfd_runtime_cap_application_evidence.md",
        "risk/contracts/v2/risk_contract_v2.schema.json"
    ]


def test_dot_planning_paths_keep_dot_and_milestones_remain_protected(tmp_path: Path) -> None:
    reports = {
        surface: sample_surface_report(tmp_path, surface)
        for surface in sorted(EXPECTED_SURFACES)
    }

    report = audit.build_adoption_closure_audit(
        report_dir=tmp_path / "adoption-closure",
        diff_base="HEAD",
        surface_reports=reports,
        live_fixture=sample_live_fixture(),
        validator_payloads={
            (surface, decision): sample_validator_payload()
            for surface in EXPECTED_SURFACES
            for decision in EXPECTED_DECISIONS
        },
        live_validator_payload=sample_validator_payload(),
        changed_paths=[
            ".planning/STATE.md",
            ".planning/milestones/v5.8-ROADMAP.md",
        ],
    )

    assert ".planning/STATE.md" in report["changed_paths"]
    assert "planning/STATE.md" not in report["changed_paths"]
    assert report["checks"]["protected_surface_guard"]["passed"] is False
    assert report["checks"]["protected_surface_guard"]["violations"] == [
        ".planning/milestones/v5.8-ROADMAP.md"
    ]


def test_render_markdown_and_write_report(tmp_path: Path) -> None:
    report = audit.build_adoption_closure_audit(
        report_dir=tmp_path / "adoption-closure",
        diff_base="HEAD",
        surface_reports={
            surface: sample_surface_report(tmp_path, surface)
            for surface in sorted(EXPECTED_SURFACES)
        },
        live_fixture=sample_live_fixture(),
        validator_payloads={
            (surface, decision): sample_validator_payload()
            for surface in EXPECTED_SURFACES
            for decision in EXPECTED_DECISIONS
        },
        live_validator_payload=sample_validator_payload(),
        changed_paths=[],
    )

    markdown = audit.render_markdown(report)
    assert "# risk_contract.v2 Adoption Closure Audit" in markdown
    assert "Overall status: `PASS`" in markdown
    assert "live runtime adoption remains not approved" in markdown
    assert "{'" not in markdown

    paths = audit.write_report(report, tmp_path / "adoption-closure")
    assert paths["json"].name == "risk_contract_v2_adoption_closure_audit.json"
    assert paths["markdown"].name == "risk_contract_v2_adoption_closure_audit.md"
    loaded = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert loaded["schema_version"] == SCHEMA_VERSION
