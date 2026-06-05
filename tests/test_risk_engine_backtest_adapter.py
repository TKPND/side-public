"""Pytest-only backtest adapter boundary proof for the common risk engine."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from risk import evaluate_risk, write_risk_artifact
from scripts import validate_risk_contract as validator


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_risk_contract.py"
POLICY_VERSION = "risk-policy.v1.phase133.adapter-proof"
SCENARIO_REF = "risk/contracts/v1/fixtures/valid/base_valid.json#scenario.adapter-proof"


def base_policy() -> dict:
    return {
        "version": POLICY_VERSION,
        "owner": "side-v5.6-risk-engine",
        "effective_from": "2026-05-07",
        "required_fields": [
            "policy.version",
            "candidate.strategy_id",
            "candidate.requested_size",
            "evidence.refs",
            "trace.emitted_artifact_path",
        ],
        "fail_close_rules": [
            {
                "condition": "malformed policy rules",
                "decision_class": "block",
                "fail_close_reason": "malformed_policy",
            },
            {
                "condition": "candidate requested size invalid",
                "decision_class": "block",
                "fail_close_reason": "candidate_validation_failure",
            },
        ],
        "rules": [
            {
                "id": "phase133.adapter.size",
                "decision_class": "size",
                "when": {
                    "path": "candidate.requested_size",
                    "op": "exists",
                    "value": True,
                },
                "fail_close_reason": "insufficient_validation_power",
            }
        ],
    }


def fake_backtest_candidate() -> dict:
    return {
        "strategy_id": "phase133.fake-backtest-candidate",
        "symbol_or_universe": "phase133-fake-universe",
        "timeframe": "candidate-defined",
        "validation_refs": ["phase133.fake-backtest.validation"],
        "requested_size": 4,
    }


def run_fake_backtest_candidate(candidate: dict, artifact_path: Path) -> tuple[object, Path]:
    policy = base_policy()
    evidence = {"refs": [SCENARIO_REF]}
    context = {
        "phase": "133-backtest-adapter-proof-and-evidence-closure",
        "adapter": "pytest-only-fake-backtest-boundary",
        "emitted_artifact_path": str(artifact_path),
    }

    evaluation = evaluate_risk(policy, candidate, evidence, context)
    written = write_risk_artifact(evaluation, artifact_path)
    return evaluation, written


def test_fake_backtest_candidate_calls_common_risk_engine_and_writes_tmp_artifact(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "phase133-fake-backtest-risk-artifact.json"

    evaluation, written = run_fake_backtest_candidate(
        fake_backtest_candidate(),
        artifact_path,
    )

    assert written == artifact_path
    assert written.parent == tmp_path
    assert written.exists()
    assert evaluation.decision["decision_class"] == "size"
    assert evaluation.decision["allowed_size"] == 4
    assert evaluation.trace["candidate_id"] == "phase133.fake-backtest-candidate"
    assert evaluation.trace["emitted_artifact_path"] == str(artifact_path)
    assert validator.validate_contract(json.loads(written.read_text(encoding="utf-8"))) == []


def test_fake_backtest_written_artifact_validates_with_existing_cli(
    tmp_path: Path,
) -> None:
    _, written = run_fake_backtest_candidate(
        fake_backtest_candidate(),
        tmp_path / "risk-artifact.json",
    )

    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(written)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "risk_contract_validator_result.v1"
    assert payload["checked_path"] == str(written)
    assert payload["valid"] is True
    assert payload["errors"] == []


def test_adapter_proof_is_test_only_boundary(tmp_path: Path) -> None:
    evaluation, _ = run_fake_backtest_candidate(
        fake_backtest_candidate(),
        tmp_path / "risk-artifact.json",
    )

    assert run_fake_backtest_candidate.__module__ == __name__
    assert "fake" in fake_backtest_candidate()["strategy_id"]
    assert "pytest-only-fake-backtest-boundary" in evaluation.artifact["context"]["adapter"]
    assert Path(evaluation.trace["emitted_artifact_path"]).parent == tmp_path
    assert (ROOT / "backtest" / "risk_engine_adapter.py").exists() is False
    assert (ROOT / "risk" / "backtest_adapter.py").exists() is False
