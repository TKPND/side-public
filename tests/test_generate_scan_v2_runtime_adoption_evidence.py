"""Contract tests for risk_contract.v2 scan runtime adoption evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import generate_scan_v2_runtime_adoption_evidence as evidence


EVIDENCE_SCHEMA_VERSION = "risk_contract_v2_scan_runtime_adoption_evidence.v1"
EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version",
    "boundary",
    "summary",
    "source_evidence",
    "replay_contract",
    "runs",
    "checks",
    "statistical_sidecar",
    "protected_surface_guard",
}
EXPECTED_RUN_NAMES = {"cap", "size", "reject"}


def sample_slot(
    *,
    decision_class: str,
    candidate_id: str,
    artifact_path: str,
) -> dict:
    continued = decision_class in {"cap", "size"}
    risk_gate = {
        "decision_class": decision_class,
        "allowed_size": 0.25 if decision_class == "cap" else (1.0 if continued else 0.0),
        "binding_rule": f"phase-v2-scan-evidence.{decision_class}",
        "fail_close_reason": "not_fail_closed",
        "policy_version": "risk-policy.v1.v2-scan-evidence-test",
        "candidate_id": candidate_id,
        "artifact_path": artifact_path,
        "execution_state": "continued" if continued else "stopped",
        "validation_status": "validated",
        "validator": "scripts/validate_risk_contract.py",
        "schema_ref": "risk/contracts/v2/risk_contract_v2.schema.json",
        "schema_version": "risk_contract.v2",
        "contract_version": "v2",
        "validator_result_schema_version": "risk_contract_validator_result.v2",
        "validated_schema_ref": "risk/contracts/v2/risk_contract_v2.schema.json",
    }
    if decision_class == "cap":
        risk_gate.update(
            {
                "application_status": "applied",
                "runtime_sizing_applied": True,
                "sizing_effect": "reduced",
                "requested_size": 1.0,
                "requested_size_basis": "unit_scan_slot",
                "effective_size": 0.25,
            }
        )
    return {
        "asset": "USDJPY",
        "strategy": "tod_edge",
        "timeframe": "1h",
        "entry_minute": 0,
        "direction": "long",
        "hold_h": 3,
        "fee_curve": (
            [{"fee_bps_rt": 2.0, "pf": 0.8681126376137095, "mean_pip": -0.386, "trades": 41}]
            if continued
            else []
        ),
        "pf_gross": 0.887 if continued else None,
        "pf_net@2bps_rt": 0.868 if continued else None,
        "alpha_cliff": False if continued else None,
        "verdict": {"passed": False} if continued else None,
        "verdicts_per_fee": [{"fee_bps_rt": 2.0, "passed": False}] if continued else None,
        "risk_gate": risk_gate,
    }


def sample_replay_rows(tmp_path: Path) -> list[dict]:
    rows = []
    for run_name in sorted(EXPECTED_RUN_NAMES):
        candidate_id = f"scan.USDJPY.1h.tod_edge.p{run_name}v2"
        run_dir = tmp_path / "runs" / run_name
        artifact_path = run_dir / "risk_artifacts" / "decisions" / f"{candidate_id}.json"
        candidate_path = run_dir / "risk_artifacts" / "candidates" / f"{candidate_id}.json"
        output_path = run_dir / "scan.json"
        raw_stdout_path = run_dir / "stdout.txt"
        slot = sample_slot(
            decision_class=run_name,
            candidate_id=candidate_id,
            artifact_path=artifact_path.as_posix(),
        )
        candidate = {
            "candidate_schema_version": "risk_contract.v2.candidate.v1",
            "candidate_id": candidate_id,
            "surface": {
                "runtime_surface": "scan",
                "surface_status": "implemented",
                "analysis_scope": "none",
                "analysis_scope_status": "not_applicable",
            },
            "sizing": {
                "requested_size": 1.0,
                "requested_size_basis": "unit_scan_slot",
            },
        }
        artifact = {
            "schema_version": "risk_contract.v2",
            "contract_version": "v2",
            "candidate": candidate,
            "decision": {"decision_class": run_name},
            "application": {
                "execution_state": "continued" if run_name in {"cap", "size"} else "stopped",
                "application_status": "applied" if run_name == "cap" else "not_applicable",
                "runtime_sizing_applied": run_name == "cap",
                "sizing_effect": "reduced" if run_name == "cap" else "none",
                "effective_size": 0.25 if run_name == "cap" else (1.0 if run_name == "size" else 0),
                "metrics_rescaled": False,
            },
            "trace": {
                "validator_result_schema_version": "risk_contract_validator_result.v2",
            },
        }
        rows.append(
            {
                "run_name": run_name,
                "decision_class": run_name,
                "command_vector": ["cargo", "run", "-p", "side-cli", "--bin", "side"],
                "return_code": 0,
                "output_path": output_path.as_posix(),
                "raw_stdout_path": raw_stdout_path.as_posix(),
                "stderr": "",
                "policy_path": (run_dir / "policy.json").as_posix(),
                "artifact_root": (run_dir / "risk_artifacts").as_posix(),
                "candidate_id": candidate_id,
                "candidate_path": candidate_path.as_posix(),
                "artifact_path": artifact_path.as_posix(),
                "slots": [slot],
                "candidate": candidate,
                "artifact": artifact,
                "validator_payload": {
                    "schema_version": "risk_contract_validator_result.v2",
                    "valid": True,
                    "contract_identity": {
                        "schema_version": "risk_contract.v2",
                        "contract_version": "v2",
                    },
                    "validated_schema": {
                        "path": "risk/contracts/v2/risk_contract_v2.schema.json",
                    },
                    "dispatch": {"status": "validated", "reason": None},
                    "errors": [],
                },
                "passed": True,
            }
        )
    return rows


def test_builds_top_level_v2_scan_evidence_contract(tmp_path: Path) -> None:
    report = evidence.build_scan_v2_runtime_adoption_evidence(
        report_dir=tmp_path,
        diff_base="HEAD",
        replay_rows=sample_replay_rows(tmp_path),
    )

    assert evidence.SCHEMA_VERSION == EVIDENCE_SCHEMA_VERSION
    assert evidence.DEFAULT_REPORT_DIR == Path("reports/risk-contract-v2/scan-runtime-adoption")
    assert set(report) == EXPECTED_TOP_LEVEL_KEYS
    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["boundary"] == "risk_contract_v2_scan_runtime_adoption_evidence"
    assert report["summary"]["overall_status"] == "PASS"
    assert report["summary"]["checks_failed"] == 0
    assert report["summary"]["implementation_scope"] == "scan_v2_evidence_replay_only"
    assert set(report["runs"]) == EXPECTED_RUN_NAMES
    source_paths = {row["path"] for row in report["source_evidence"]}
    assert "docs/plans/2026-05-18-risk-contract-v2-scan-runtime-statistical-split-design.md" in source_paths
    assert "docs/superpowers/plans/2026-05-18-scan-v2-runtime-adoption-tdd.md" in source_paths
    assert "scripts/generate_scan_v2_runtime_adoption_evidence.py" in source_paths
    assert "tests/test_generate_scan_v2_runtime_adoption_evidence.py" in source_paths


def test_replay_checks_cover_runtime_application_stop_and_statistical_sidecar(
    tmp_path: Path,
) -> None:
    report = evidence.build_scan_v2_runtime_adoption_evidence(
        report_dir=tmp_path,
        diff_base="origin/master",
        replay_rows=sample_replay_rows(tmp_path),
    )

    checks = report["checks"]
    assert checks["v2_version_proof"]["passed"] is True
    assert checks["v2_version_proof"]["contract_version"] == "v2"
    assert checks["v2_version_proof"]["validator_result_schema_version"] == "risk_contract_validator_result.v2"

    validation = checks["validator_replay"]
    assert validation["passed"] is True
    assert validation["validated_artifacts"] == 3

    cap = checks["cap_runtime_application"]
    assert cap["passed"] is True
    assert cap["runtime_sizing_applied"] is True
    assert cap["requested_size_basis"] == "unit_scan_slot"
    assert cap["effective_size_equals_allowed_size"] is True
    assert cap["metrics_rescaled"] is False
    assert cap["fee_curve_preserved_as_statistical_output"] is True

    size = checks["size_continue_replay"]
    assert size["passed"] is True
    assert size["runtime_sizing_applied"] is False
    assert size["fee_curve_present"] is True

    stop = checks["reject_stop_replay"]
    assert stop["passed"] is True
    assert stop["execution_state"] == "stopped"
    assert stop["metrics_are_null"] is True
    assert stop["fee_curve_empty"] is True

    sidecar = checks["statistical_sidecar"]
    assert sidecar["passed"] is True
    assert sidecar["metrics_rescaled"] is False
    assert sidecar["statistical_basis"] == "full_size_ungated_basis"
    assert sidecar["runtime_contract_scope"] == "scan_slot_runtime_only"


def test_protected_output_directories_are_rejected(tmp_path: Path) -> None:
    protected_dirs = [
        Path("reports/v5.7"),
        Path("reports/v5.8"),
        Path("reports/v8.3"),
        Path(".planning"),
        Path("docs/reports/v4"),
        Path("data/v4"),
        Path("risk/contracts"),
    ]

    for report_dir in protected_dirs:
        with pytest.raises(ValueError, match="protected output"):
            evidence.build_scan_v2_runtime_adoption_evidence(
                report_dir=report_dir,
                diff_base="HEAD",
                replay_rows=sample_replay_rows(tmp_path),
            )

    report = evidence.build_scan_v2_runtime_adoption_evidence(
        report_dir=Path("reports/risk-contract-v2/scan-runtime-adoption"),
        diff_base="HEAD",
        replay_rows=sample_replay_rows(tmp_path),
    )
    assert report["protected_surface_guard"]["passed"] is True


def test_direct_replay_runner_rejects_protected_output_before_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_args: object) -> dict:
        raise AssertionError("runner should reject protected output before replay")

    monkeypatch.setattr(evidence, "run_scan_v2_replay", fail_if_called)

    with pytest.raises(ValueError, match="protected output"):
        evidence.run_scan_v2_replays(Path("reports/v5.7"))


def test_render_markdown_summarizes_replay_without_python_repr(tmp_path: Path) -> None:
    report = evidence.build_scan_v2_runtime_adoption_evidence(
        report_dir=tmp_path,
        diff_base="origin/master",
        replay_rows=sample_replay_rows(tmp_path),
    )

    markdown = evidence.render_markdown(report)

    assert "# risk_contract.v2 Scan Runtime Adoption Evidence" in markdown
    assert "## Replay Contract" in markdown
    assert "## Statistical Sidecar" in markdown
    assert "## Run Manifest" in markdown
    assert "## Check Results" in markdown
    assert "cap_runtime_application" in markdown
    assert "validator_replay" in markdown
    assert "risk_contract.v2" in markdown
    assert "cargo run -p side-cli" in markdown
    assert "{'" not in markdown
    assert "['" not in markdown


def test_main_writes_json_and_markdown_with_injected_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evidence,
        "run_scan_v2_replays",
        lambda report_dir: sample_replay_rows(report_dir),
    )

    code = evidence.main(
        [
            "--report-dir",
            str(tmp_path),
            "--diff-base",
            "origin/master",
        ]
    )

    assert code == 0
    json_path = tmp_path / "scan_v2_runtime_adoption_evidence.json"
    markdown_path = tmp_path / "scan_v2_runtime_adoption_evidence.md"
    assert json_path.exists()
    assert markdown_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert payload["summary"]["overall_status"] == "PASS"
    assert "## Check Results" in markdown_path.read_text(encoding="utf-8")
