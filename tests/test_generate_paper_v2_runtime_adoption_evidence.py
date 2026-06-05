"""Contract tests for risk_contract.v2 paper runtime adoption evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import generate_paper_v2_runtime_adoption_evidence as evidence


EVIDENCE_SCHEMA_VERSION = "risk_contract_v2_paper_runtime_adoption_evidence.v1"
EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version",
    "boundary",
    "summary",
    "source_evidence",
    "replay_contract",
    "runs",
    "checks",
    "protected_surface_guard",
}
EXPECTED_RUN_NAMES = {"cap", "size", "reject"}


def sample_paper_evidence(
    *,
    decision_class: str,
    candidate_id: str,
    candidate_path: str,
    artifact_path: str,
) -> dict:
    continued = decision_class in {"cap", "size"}
    cap = decision_class == "cap"
    return {
        "run_id": "paper-risk-once",
        "tick_id": "paper-risk-once",
        "slot_id": "USD/JPY/keltner/^VIX#1",
        "candidate_id": candidate_id,
        "candidate_artifact_path": candidate_path,
        "candidate_artifact_sha256": "sha256:candidate",
        "decision_artifact_path": artifact_path,
        "decision_artifact_sha256": "sha256:decision",
        "policy_path": "policy.json",
        "policy_sha256": "sha256:policy",
        "validator_valid": True,
        "validator_errors": [],
        "risk_mode": "apply",
        "decision_class": decision_class,
        "execution_state": "continued" if continued else "stopped",
        "position_mutation": False,
        "requested_size": 10000.0,
        "requested_size_basis": "unit_paper_slot_allocation",
        "allowed_size": 0.25 if cap else (10000.0 if continued else 0.0),
        "allowed_size_basis": "paper_effective_slot_allocation",
        "would_effective_size": 0.25 if cap else (10000.0 if continued else 0.0),
        "actual_effective_size": 0.25 if cap else (10000.0 if continued else 10000.0),
        "runtime_sizing_applied": cap,
        "cap_application_status": "applied" if cap else None,
        "paper_fee_model_status": "explicit_nonzero_cost_model",
        "fee_bps": 1.5,
        "spread_bps": 0.5,
        "cost_model_schema_version": 1,
        "cost_basis": "paper_notional_round_trip_bps",
        "gross_pnl": 0.0,
        "estimated_cost": 200.0,
        "estimated_net_pnl": -200.0,
        "estimated_net_pnl_claim_allowed": True,
        "parity_claim_allowed": False,
        "alpha_claim_allowed": False,
        "position_mutation_phase": "not_run",
        "risk_contract_schema_version": "risk_contract.v2",
        "risk_contract_version": "v2",
        "validator_result_schema_version": "risk_contract_validator_result.v2",
        "validated_schema_ref": "risk/contracts/v2/risk_contract_v2.schema.json",
        "validator": "scripts/validate_risk_contract.py",
    }


def sample_replay_rows(tmp_path: Path) -> list[dict]:
    rows = []
    for run_name in sorted(EXPECTED_RUN_NAMES):
        candidate_id = f"paper.USDJPY.keltner.p{run_name}v2"
        run_dir = tmp_path / "runs" / run_name
        artifact_path = run_dir / "risk_artifacts" / "decisions" / f"{candidate_id}.json"
        candidate_path = run_dir / "paper_evidence" / "candidates" / f"{candidate_id}.json"
        paper_evidence_path = run_dir / "paper_evidence" / "evidence" / f"{candidate_id}.json"
        continued = run_name in {"cap", "size"}
        cap = run_name == "cap"
        candidate = {
            "candidate_schema_version": "risk_contract.v2.candidate.v1",
            "candidate_id": candidate_id,
            "surface": {
                "runtime_surface": "paper",
                "surface_status": "implemented",
                "analysis_scope": "none",
                "analysis_scope_status": "not_applicable",
            },
            "sizing": {
                "requested_size": 10000.0,
                "requested_size_basis": "unit_paper_slot_allocation",
            },
            "surface_payload": {
                "paper_risk_mode": "apply",
                "allocation_source": "PaperConfig::allocations",
            },
        }
        artifact = {
            "schema_version": "risk_contract.v2",
            "contract_version": "v2",
            "candidate": candidate,
            "decision": {"decision_class": run_name},
            "application": {
                "execution_state": "continued" if continued else "stopped",
                "application_status": "applied" if cap else "not_applicable",
                "runtime_sizing_applied": cap,
                "sizing_effect": "reduced" if cap else "none",
                "effective_size": 0.25 if cap else (10000.0 if continued else 0),
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
                "command_vector": [
                    "cargo",
                    "run",
                    "-p",
                    "side-cli",
                    "--example",
                    "paper_v2_evidence_replay",
                ],
                "return_code": 0,
                "stdout_path": (run_dir / "stdout.json").as_posix(),
                "raw_stdout_path": (run_dir / "stdout.raw.json").as_posix(),
                "stderr": "",
                "config_path": (run_dir / "paper_config.json").as_posix(),
                "policy_path": (run_dir / "policy.json").as_posix(),
                "artifact_root": (run_dir / "risk_artifacts").as_posix(),
                "evidence_root": (run_dir / "paper_evidence").as_posix(),
                "candidate_id": candidate_id,
                "candidate_path": candidate_path.as_posix(),
                "paper_evidence_path": paper_evidence_path.as_posix(),
                "artifact_path": artifact_path.as_posix(),
                "stdout": {
                    "schema_version": "side-cli.paper_v2_evidence_replay.result.v1",
                    "paper_risk_mode": "apply",
                    "contract_version": "v2",
                    "should_run_tick": continued,
                    "evidence_paths": [paper_evidence_path.as_posix()],
                    "runtime_size_overrides": (
                        [{"slot_id": "USD/JPY/keltner/^VIX#1", "effective_size": 0.25}]
                        if cap
                        else []
                    ),
                },
                "paper_evidence": sample_paper_evidence(
                    decision_class=run_name,
                    candidate_id=candidate_id,
                    candidate_path=candidate_path.as_posix(),
                    artifact_path=artifact_path.as_posix(),
                ),
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


def test_builds_top_level_v2_paper_evidence_contract(tmp_path: Path) -> None:
    report = evidence.build_paper_v2_runtime_adoption_evidence(
        report_dir=tmp_path,
        diff_base="HEAD",
        replay_rows=sample_replay_rows(tmp_path),
    )

    assert evidence.SCHEMA_VERSION == EVIDENCE_SCHEMA_VERSION
    assert evidence.DEFAULT_REPORT_DIR == Path("reports/risk-contract-v2/paper-runtime-adoption")
    assert set(report) == EXPECTED_TOP_LEVEL_KEYS
    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["boundary"] == "risk_contract_v2_paper_runtime_adoption_evidence"
    assert report["summary"]["overall_status"] == "PASS"
    assert report["summary"]["checks_failed"] == 0
    assert report["summary"]["implementation_scope"] == "paper_v2_evidence_replay_only"
    assert set(report["runs"]) == EXPECTED_RUN_NAMES
    source_paths = {row["path"] for row in report["source_evidence"]}
    assert "docs/plans/2026-05-18-risk-contract-v2-paper-runtime-adoption-tdd.md" in source_paths
    assert "scripts/generate_paper_v2_runtime_adoption_evidence.py" in source_paths
    assert "tests/test_generate_paper_v2_runtime_adoption_evidence.py" in source_paths
    assert "rust/side-cli/examples/paper_v2_evidence_replay.rs" in source_paths


def test_replay_checks_cover_version_proof_runtime_application_and_stop(tmp_path: Path) -> None:
    report = evidence.build_paper_v2_runtime_adoption_evidence(
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
    assert cap["requested_size_basis"] == "unit_paper_slot_allocation"
    assert cap["effective_size_equals_allowed_size"] is True
    assert cap["metrics_rescaled"] is False
    assert cap["should_run_tick"] is True
    assert cap["runtime_size_override_count"] == 1

    size = checks["size_continue_replay"]
    assert size["passed"] is True
    assert size["runtime_sizing_applied"] is False
    assert size["should_run_tick"] is True
    assert size["runtime_size_override_count"] == 0

    stop = checks["reject_stop_replay"]
    assert stop["passed"] is True
    assert stop["execution_state"] == "stopped"
    assert stop["should_run_tick"] is False
    assert stop["position_mutation"] is False


def test_paper_replay_command_uses_helper_without_paper_tick_loop(tmp_path: Path) -> None:
    command = evidence.paper_v2_replay_command(
        config_path=tmp_path / "config.json",
        policy_path=tmp_path / "policy.json",
        artifact_root=tmp_path / "risk_artifacts",
        evidence_root=tmp_path / "paper_evidence",
    )

    assert command[:5] == ["cargo", "run", "-p", "side-cli", "--example"]
    assert "paper_v2_evidence_replay" in command
    assert "paper" not in command
    assert "--once" not in command


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
            evidence.build_paper_v2_runtime_adoption_evidence(
                report_dir=report_dir,
                diff_base="HEAD",
                replay_rows=sample_replay_rows(tmp_path),
            )

    report = evidence.build_paper_v2_runtime_adoption_evidence(
        report_dir=Path("reports/risk-contract-v2/paper-runtime-adoption"),
        diff_base="HEAD",
        replay_rows=sample_replay_rows(tmp_path),
    )
    assert report["protected_surface_guard"]["passed"] is True


def test_direct_replay_runner_rejects_protected_output_before_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_args: object) -> dict:
        raise AssertionError("runner should reject protected output before replay")

    monkeypatch.setattr(evidence, "run_paper_v2_replay", fail_if_called)

    with pytest.raises(ValueError, match="protected output"):
        evidence.run_paper_v2_replays(Path("reports/v5.7"))


def test_render_markdown_summarizes_replay_without_python_repr(tmp_path: Path) -> None:
    report = evidence.build_paper_v2_runtime_adoption_evidence(
        report_dir=tmp_path,
        diff_base="origin/master",
        replay_rows=sample_replay_rows(tmp_path),
    )

    markdown = evidence.render_markdown(report)

    assert "# risk_contract.v2 Paper Runtime Adoption Evidence" in markdown
    assert "## Replay Contract" in markdown
    assert "## Run Manifest" in markdown
    assert "## Check Results" in markdown
    assert "cap_runtime_application" in markdown
    assert "validator_replay" in markdown
    assert "risk_contract.v2" in markdown
    assert "cargo run -p side-cli --example paper_v2_evidence_replay" in markdown
    assert "{'" not in markdown
    assert "['" not in markdown


def test_main_writes_json_and_markdown_with_injected_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evidence,
        "run_paper_v2_replays",
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
    json_path = tmp_path / "paper_v2_runtime_adoption_evidence.json"
    markdown_path = tmp_path / "paper_v2_runtime_adoption_evidence.md"
    assert json_path.exists()
    assert markdown_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert payload["summary"]["overall_status"] == "PASS"
    assert "## Check Results" in markdown_path.read_text(encoding="utf-8")
