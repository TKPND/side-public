"""Rust scan candidate adapter replay proof for the common risk engine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from risk import evaluate_risk, write_risk_artifact
from scripts import validate_risk_contract as validator


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_risk_contract.py"
POLICY_VERSION = "risk-policy.v1.phase134.rust-adapter-proof"


def base_policy() -> dict:
    return {
        "version": POLICY_VERSION,
        "owner": "side-v5.7-rust-adapter",
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
                "id": "phase134.rust_adapter.size",
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


def emit_rust_candidate(tmp_path: Path) -> dict:
    candidate_path = tmp_path / "rust-candidate.json"
    env = os.environ.copy()
    env["SIDE_RISK_ADAPTER_CANDIDATE_OUT"] = str(candidate_path)
    result = subprocess.run(
        [
            "cargo",
            "test",
            "-p",
            "side-cli",
            "--test",
            "risk_adapter_test",
            "risk_adapter_emits_fixture_candidate_json",
            "--",
            "--exact",
            "--test-threads=1",
        ],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, (
        "Rust risk adapter candidate emission failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(candidate_path.read_text(encoding="utf-8"))


def test_rust_emitted_candidate_calls_common_risk_engine_and_validates(
    tmp_path: Path,
) -> None:
    candidate = emit_rust_candidate(tmp_path)

    assert candidate["strategy_id"] == candidate["candidate_id"]
    assert candidate["requested_size"] == 1.0
    assert candidate["requested_size_basis"] == "unit_scan_slot"
    assert (
        candidate["artifact_path"]
        == f"reports/v5.7/risk_gate/{candidate['candidate_id']}.json"
    )

    relative_artifact_path = Path(candidate["artifact_path"])
    assert not relative_artifact_path.is_absolute()
    assert "\0" not in candidate["artifact_path"]
    assert ".." not in relative_artifact_path.parts
    assert all(part for part in relative_artifact_path.parts)

    artifact_path = tmp_path / relative_artifact_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    evidence = {"refs": candidate["validation_refs"]}
    context = {
        "phase": "134-rust-adapter",
        "adapter": "rust-scan-edges-risk-adapter",
        "candidate_artifact_path": candidate["artifact_path"],
        "emitted_artifact_path": str(artifact_path),
    }

    evaluation = evaluate_risk(base_policy(), candidate, evidence, context)
    written = write_risk_artifact(evaluation, artifact_path)

    assert evaluation.decision["decision_class"] == "size"
    assert evaluation.decision["allowed_size"] == 1.0
    assert evaluation.trace["candidate_id"] == candidate["strategy_id"]
    assert evaluation.trace["emitted_artifact_path"] == str(artifact_path)
    assert written == artifact_path
    assert validator.validate_contract(json.loads(written.read_text(encoding="utf-8"))) == []

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
    assert payload["valid"] is True


def test_phase134_adapter_scope_stays_builder_and_tests_only(tmp_path: Path) -> None:
    candidate = emit_rust_candidate(tmp_path)

    for key in [
        "scan_params",
        "source_edge",
        "fee_refs",
        "data_refs",
        "artifact_path",
        "requested_size_basis",
    ]:
        assert key in candidate

    for key in [
        "fee_curve",
        "verdict",
        "relaxed_pass",
        "verdicts_per_fee",
        "risk_gate",
        "allowed_size",
        "decision_class",
    ]:
        assert key not in candidate

    assert (ROOT / "backtest" / "risk_engine_adapter.py").exists() is False
    assert (ROOT / "risk" / "backtest_adapter.py").exists() is False

    types_source = (ROOT / "rust" / "side-cli" / "src" / "cmd" / "types.rs").read_text(
        encoding="utf-8"
    )
    assert "pub risk_gate: Option" in types_source

    main_source = (ROOT / "rust" / "side-cli" / "src" / "main.rs").read_text(
        encoding="utf-8"
    )
    for source in [types_source, main_source]:
        for forbidden in [
            "SIDE_RISK_ADAPTER",
            "risk_adapter",
            "emit-risk-candidate",
        ]:
            assert forbidden not in source
