"""Tests for the Python risk gate wrapper used by Rust scan gating."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import validate_risk_contract as validator


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "evaluate_risk_gate.py"
POLICY_VERSION = "risk-policy.v1.phase135.fail-close-gate-test"
SUMMARY_KEYS = {
    "decision_class",
    "allowed_size",
    "binding_rule",
    "fail_close_reason",
    "policy_version",
    "candidate_id",
    "artifact_path",
}
FORBIDDEN_STDOUT_KEYS = {
    "policy",
    "candidate",
    "evidence",
    "context",
    "decision",
    "trace",
    "artifact",
}


def rule(
    decision_class: str,
    *,
    allowed_size: float | None = None,
    fail_close_reason: str = "insufficient_validation_power",
) -> dict:
    payload = {
        "id": f"phase135.{decision_class}",
        "decision_class": decision_class,
        "when": {
            "path": "candidate.strategy_id",
            "op": "eq",
            "value": "phase135-strategy.fixture",
        },
        "fail_close_reason": fail_close_reason,
    }
    if allowed_size is not None:
        payload["allowed_size"] = allowed_size
    return payload


def base_inputs(tmp_path: Path, decision_class: str) -> tuple[dict, dict, dict, dict]:
    fail_close_reason = {
        "block": "malformed_policy",
        "kill": "stale_evidence",
    }.get(decision_class, "insufficient_validation_power")
    policy = {
        "version": POLICY_VERSION,
        "owner": "side-v5.7-risk-gate",
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
            rule(
                decision_class,
                allowed_size=3 if decision_class == "cap" else None,
                fail_close_reason=fail_close_reason,
            )
        ],
    }
    candidate = {
        "strategy_id": "phase135-strategy.fixture",
        "symbol_or_universe": "phase135-universe.fixture",
        "timeframe": "candidate-defined",
        "validation_refs": ["phase135-validation.fixture"],
        "requested_size": 10,
    }
    evidence = {"refs": ["risk/contracts/v1/fixtures/valid/base_valid.json"]}
    context = {
        "phase": "135-fail-close-gate",
        "emitted_artifact_path": str(tmp_path / f"{decision_class}-risk-artifact.json"),
    }
    return policy, candidate, evidence, context


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_wrapper(
    tmp_path: Path,
    *,
    policy: dict,
    candidate: dict,
    evidence: dict,
    context: dict,
    out: Path | None = None,
    contract_version: str | None = None,
) -> subprocess.CompletedProcess[str]:
    policy_path = write_json(tmp_path / "policy.json", policy)
    candidate_path = write_json(tmp_path / "candidate.json", candidate)
    evidence_path = write_json(tmp_path / "evidence.json", evidence)
    context_path = write_json(tmp_path / "context.json", context)
    out_path = out if out is not None else Path(context["emitted_artifact_path"])
    command = [
        sys.executable,
        str(WRAPPER),
        "--policy",
        str(policy_path),
        "--candidate",
        str(candidate_path),
        "--evidence",
        str(evidence_path),
        "--context",
        str(context_path),
        "--out",
        str(out_path),
    ]
    if contract_version is not None:
        command.extend(["--contract-version", contract_version])
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def run_wrapper_paths(
    *,
    policy_path: Path,
    candidate_path: Path,
    evidence_path: Path,
    context_path: Path,
    out_path: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(WRAPPER),
            "--policy",
            str(policy_path),
            "--candidate",
            str(candidate_path),
            "--evidence",
            str(evidence_path),
            "--context",
            str(context_path),
            "--out",
            str(out_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def written_inputs(
    tmp_path: Path,
    *,
    policy: dict | None = None,
    candidate: dict | None = None,
    evidence: dict | None = None,
    context: dict | None = None,
    decision_class: str = "size",
) -> tuple[Path, Path, Path, Path, Path]:
    base_policy, base_candidate, base_evidence, base_context = base_inputs(
        tmp_path,
        decision_class,
    )
    policy = policy if policy is not None else base_policy
    candidate = candidate if candidate is not None else base_candidate
    evidence = evidence if evidence is not None else base_evidence
    context = context if context is not None else base_context
    return (
        write_json(tmp_path / "policy.json", policy),
        write_json(tmp_path / "candidate.json", candidate),
        write_json(tmp_path / "evidence.json", evidence),
        write_json(tmp_path / "context.json", context),
        Path(context["emitted_artifact_path"]),
    )


def v2_backtest_candidate_from(candidate: dict) -> dict:
    return {
        "candidate_schema_version": "risk_contract.v2.candidate.v1",
        "candidate_id": candidate["strategy_id"],
        "strategy_id": candidate["strategy_id"],
        "symbol_or_universe": candidate["symbol_or_universe"],
        "timeframe": candidate["timeframe"],
        "validation_refs": [
            "risk/contracts/v2/risk_contract_v2.schema.json",
            "scripts/validate_risk_contract.py",
            *candidate["validation_refs"],
        ],
        "surface": {
            "runtime_surface": "backtest",
            "surface_status": "implemented",
            "analysis_scope": "none",
            "analysis_scope_status": "not_applicable",
        },
        "sizing": {
            "requested_size": candidate["requested_size"],
            "requested_size_basis": "unit_backtest_run",
        },
        "surface_payload": {
            "backtest_params": {
                "strategy": "tod_edge",
                "fee_bps": 1.0,
            }
        },
        "artifact_root": "target/test-risk-contract-v2",
    }


def v2_scan_candidate_from(candidate: dict) -> dict:
    return {
        "candidate_schema_version": "risk_contract.v2.candidate.v1",
        "candidate_id": "scan_edges.USDJPY.1h.edge0.m0.long.h1",
        "strategy_id": "scan_edges.USDJPY.1h.edge0.m0.long.h1",
        "symbol_or_universe": candidate["symbol_or_universe"],
        "timeframe": candidate["timeframe"],
        "validation_refs": [
            "risk/contracts/v2/risk_contract_v2.schema.json",
            "risk/contracts/v2/risk_contract_validator_result_v2.schema.json",
            "scripts/validate_risk_contract.py",
            *candidate["validation_refs"],
        ],
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
        "surface_payload": {
            "scan_params": {
                "asset": "USDJPY",
                "timeframe": "1h",
                "strategy_name": "tod_edge",
                "params": {
                    "entry_minute": 0,
                    "direction": "long",
                    "hold_h": 1,
                    "exit_type": "time_hold",
                },
                "source_edge_index": 0,
            }
        },
        "artifact_root": "reports/risk-contract-v2/scan-runtime-adoption/test",
    }


def v2_paper_candidate_from(candidate: dict) -> dict:
    return {
        "candidate_schema_version": "risk_contract.v2.candidate.v1",
        "candidate_id": "paper.USDJPY.1h.keltner.pabcdef123456",
        "strategy_id": "paper.USDJPY.1h.keltner.pabcdef123456",
        "symbol_or_universe": "USD/JPY",
        "timeframe": candidate["timeframe"],
        "validation_refs": [
            "risk/contracts/v2/risk_contract_v2.schema.json",
            "risk/contracts/v2/risk_contract_validator_result_v2.schema.json",
            "scripts/validate_risk_contract.py",
        ],
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
            "slot_id": "USD/JPY/keltner/^VIX#1",
            "slot_index": 0,
            "slot_key": "USD_JPY_keltner__VIX_1",
            "allocation_source": "PaperConfig::allocations",
            "allocation_method": "initial_capital_divided_by_slot_count",
            "initial_capital": 10000.0,
            "slot_count": 1,
            "effective_leverage": 500.0,
            "runtime_accounting_mode": "legacy_gross",
            "paper_risk_mode": "apply",
        },
        "artifact_root": "reports/risk-contract-v2/paper-runtime-adoption/test",
    }


@pytest.mark.parametrize(
    ("decision_class", "expected_allowed_size"),
    [
        ("size", 10),
        ("cap", 3),
        ("reject", 0),
        ("kill", 0),
        ("block", 0),
    ],
)
def test_wrapper_success_writes_valid_artifact_and_summary_only_stdout(
    tmp_path: Path,
    decision_class: str,
    expected_allowed_size: float,
) -> None:
    policy, candidate, evidence, context = base_inputs(tmp_path, decision_class)

    result = run_wrapper(
        tmp_path,
        policy=policy,
        candidate=candidate,
        evidence=evidence,
        context=context,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert set(summary) == SUMMARY_KEYS
    assert not (set(summary) & FORBIDDEN_STDOUT_KEYS)
    assert summary["decision_class"] == decision_class
    assert summary["allowed_size"] == expected_allowed_size
    assert summary["binding_rule"] == f"phase135.{decision_class}"
    assert summary["policy_version"] == POLICY_VERSION
    assert summary["candidate_id"] == candidate["strategy_id"]
    assert summary["artifact_path"] == context["emitted_artifact_path"]

    artifact = json.loads(Path(summary["artifact_path"]).read_text(encoding="utf-8"))
    assert validator.validate_contract(artifact) == []
    assert artifact["decision"]["decision_class"] == decision_class


def test_wrapper_v2_opt_in_writes_valid_backtest_artifact_and_version_proof(
    tmp_path: Path,
) -> None:
    policy, candidate, evidence, context = base_inputs(tmp_path, "cap")
    candidate = v2_backtest_candidate_from(candidate)

    result = run_wrapper(
        tmp_path,
        policy=policy,
        candidate=candidate,
        evidence=evidence,
        context=context,
        contract_version="v2",
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert set(summary) == SUMMARY_KEYS | {
        "schema_version",
        "contract_version",
        "validator_result_schema_version",
        "validated_schema_ref",
        "validator",
    }
    assert summary["schema_version"] == "risk_contract.v2"
    assert summary["contract_version"] == "v2"
    assert summary["validator_result_schema_version"] == "risk_contract_validator_result.v2"
    assert summary["validated_schema_ref"] == "risk/contracts/v2/risk_contract_v2.schema.json"
    assert summary["validator"] == "scripts/validate_risk_contract.py"
    assert summary["decision_class"] == "cap"
    assert summary["allowed_size"] == 3

    artifact = json.loads(Path(summary["artifact_path"]).read_text(encoding="utf-8"))
    assert validator.validate_v2_contract(artifact) == []
    assert artifact["schema_version"] == "risk_contract.v2"
    assert artifact["contract_version"] == "v2"
    assert artifact["candidate"]["surface"]["runtime_surface"] == "backtest"
    assert artifact["candidate"]["surface"]["surface_status"] == "implemented"
    assert artifact["candidate"]["sizing"]["requested_size_basis"] == "unit_backtest_run"
    assert artifact["decision"]["decision_class"] == "cap"
    assert artifact["decision"]["fail_close_reason"] == "not_fail_closed"
    assert artifact["application"]["execution_state"] == "continued"
    assert artifact["application"]["application_status"] == "applied"
    assert artifact["application"]["runtime_sizing_applied"] is True
    assert artifact["application"]["sizing_effect"] == "reduced"
    assert artifact["application"]["effective_size"] == 3
    assert artifact["trace"]["validated_schema_version"] == "risk_contract.v2"
    assert artifact["trace"]["validator_result_schema_version"] == "risk_contract_validator_result.v2"


def test_wrapper_v2_opt_in_writes_valid_scan_runtime_artifact_and_version_proof(
    tmp_path: Path,
) -> None:
    policy, candidate, evidence, context = base_inputs(tmp_path, "cap")
    candidate = v2_scan_candidate_from(candidate)
    policy["rules"][0]["when"]["value"] = candidate["strategy_id"]

    result = run_wrapper(
        tmp_path,
        policy=policy,
        candidate=candidate,
        evidence=evidence,
        context=context,
        contract_version="v2",
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert set(summary) == SUMMARY_KEYS | {
        "schema_version",
        "contract_version",
        "validator_result_schema_version",
        "validated_schema_ref",
        "validator",
    }
    assert summary["schema_version"] == "risk_contract.v2"
    assert summary["contract_version"] == "v2"
    assert summary["validator_result_schema_version"] == "risk_contract_validator_result.v2"
    assert summary["validated_schema_ref"] == "risk/contracts/v2/risk_contract_v2.schema.json"
    assert summary["validator"] == "scripts/validate_risk_contract.py"
    assert summary["decision_class"] == "cap"
    assert summary["allowed_size"] == 1.0

    artifact = json.loads(Path(summary["artifact_path"]).read_text(encoding="utf-8"))
    assert validator.validate_v2_contract(artifact) == []
    assert artifact["schema_version"] == "risk_contract.v2"
    assert artifact["contract_version"] == "v2"
    assert artifact["candidate"]["surface"]["runtime_surface"] == "scan"
    assert artifact["candidate"]["surface"]["surface_status"] == "implemented"
    assert artifact["candidate"]["surface"]["analysis_scope"] == "none"
    assert artifact["candidate"]["surface"]["analysis_scope_status"] == "not_applicable"
    assert artifact["candidate"]["sizing"]["requested_size_basis"] == "unit_scan_slot"
    assert artifact["decision"]["decision_class"] == "cap"
    assert artifact["decision"]["fail_close_reason"] == "not_fail_closed"
    assert artifact["application"]["execution_state"] == "continued"
    assert artifact["application"]["application_status"] == "applied"
    assert artifact["application"]["runtime_sizing_applied"] is True
    assert artifact["application"]["sizing_effect"] == "none"
    assert artifact["application"]["effective_size"] == 1.0
    assert artifact["application"]["metrics_rescaled"] is False
    assert artifact["trace"]["validated_schema_version"] == "risk_contract.v2"
    assert artifact["trace"]["validator_result_schema_version"] == "risk_contract_validator_result.v2"


def test_wrapper_v2_opt_in_writes_valid_paper_runtime_artifact_and_version_proof(
    tmp_path: Path,
) -> None:
    policy, candidate, evidence, context = base_inputs(tmp_path, "cap")
    candidate = v2_paper_candidate_from(candidate)
    policy["rules"][0]["when"]["value"] = candidate["strategy_id"]

    result = run_wrapper(
        tmp_path,
        policy=policy,
        candidate=candidate,
        evidence=evidence,
        context=context,
        contract_version="v2",
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["schema_version"] == "risk_contract.v2"
    assert summary["contract_version"] == "v2"
    assert summary["validator_result_schema_version"] == "risk_contract_validator_result.v2"
    assert summary["validated_schema_ref"] == "risk/contracts/v2/risk_contract_v2.schema.json"
    assert summary["validator"] == "scripts/validate_risk_contract.py"
    assert summary["decision_class"] == "cap"
    assert summary["allowed_size"] == 3

    artifact = json.loads(Path(summary["artifact_path"]).read_text(encoding="utf-8"))
    assert validator.validate_v2_contract(artifact) == []
    assert artifact["schema_version"] == "risk_contract.v2"
    assert artifact["contract_version"] == "v2"
    assert artifact["candidate"]["surface"]["runtime_surface"] == "paper"
    assert artifact["candidate"]["surface"]["analysis_scope"] == "none"
    assert artifact["candidate"]["sizing"]["requested_size_basis"] == "unit_paper_slot_allocation"
    assert artifact["decision"]["decision_class"] == "cap"
    assert artifact["decision"]["fail_close_reason"] == "not_fail_closed"
    assert artifact["application"]["execution_state"] == "continued"
    assert artifact["application"]["application_status"] == "applied"
    assert artifact["application"]["runtime_sizing_applied"] is True
    assert artifact["application"]["sizing_effect"] == "reduced"
    assert artifact["application"]["effective_size"] == 3
    assert artifact["application"]["metrics_rescaled"] is False
    assert artifact["trace"]["validated_schema_version"] == "risk_contract.v2"
    assert artifact["trace"]["validator_result_schema_version"] == "risk_contract_validator_result.v2"


def test_wrapper_v2_paper_cap_guard_rejects_expanding_application() -> None:
    from scripts import evaluate_risk_gate as wrapper

    _policy, candidate, _evidence, _context = base_inputs(Path("unused"), "cap")
    candidate = v2_paper_candidate_from(candidate)
    decision = {
        "decision_class": "cap",
        "allowed_size": 10000.01,
    }

    with pytest.raises(ValueError, match="paper cap allowed_size must be <= requested_size"):
        wrapper.v2_application_from_decision(candidate, decision)


def test_wrapper_v2_opt_in_rejects_unapproved_runtime_surface(tmp_path: Path) -> None:
    policy, candidate, evidence, context = base_inputs(tmp_path, "size")
    candidate = v2_backtest_candidate_from(candidate)
    candidate["candidate_id"] = "live-not-accepted"
    candidate["strategy_id"] = "phase135-strategy.fixture"
    candidate["surface"]["runtime_surface"] = "live"
    candidate["sizing"]["requested_size_basis"] = "unit_scan_slot"

    result = run_wrapper(
        tmp_path,
        policy=policy,
        candidate=candidate,
        evidence=evidence,
        context=context,
        contract_version="v2",
    )

    payload = assert_risk_gate_error(result)
    assert "unsupported v2 runtime_surface" in payload["message"]


def assert_risk_gate_error(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 1
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["error"] == "risk_gate_error"
    assert payload["message"]
    assert payload["exception_class"]
    return payload


def test_wrapper_missing_policy_file_exits_nonzero(tmp_path: Path) -> None:
    policy_path, candidate_path, evidence_path, context_path, out_path = written_inputs(
        tmp_path
    )
    policy_path.unlink()

    result = run_wrapper_paths(
        policy_path=policy_path,
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        context_path=context_path,
        out_path=out_path,
    )

    payload = assert_risk_gate_error(result)
    assert payload["exception_class"] == "FileNotFoundError"


def test_wrapper_missing_candidate_file_exits_nonzero(tmp_path: Path) -> None:
    policy_path, candidate_path, evidence_path, context_path, out_path = written_inputs(
        tmp_path
    )
    candidate_path.unlink()

    result = run_wrapper_paths(
        policy_path=policy_path,
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        context_path=context_path,
        out_path=out_path,
    )

    payload = assert_risk_gate_error(result)
    assert payload["exception_class"] == "FileNotFoundError"


def test_wrapper_candidate_validation_risk_input_error_exits_nonzero(
    tmp_path: Path,
) -> None:
    policy, candidate, evidence, context = base_inputs(tmp_path, "size")
    candidate.pop("strategy_id")
    policy_path, candidate_path, evidence_path, context_path, out_path = written_inputs(
        tmp_path,
        policy=policy,
        candidate=candidate,
        evidence=evidence,
        context=context,
    )

    result = run_wrapper_paths(
        policy_path=policy_path,
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        context_path=context_path,
        out_path=out_path,
    )

    payload = assert_risk_gate_error(result)
    assert payload["exception_class"] == "RiskInputError"


def test_wrapper_malformed_json_exits_nonzero(tmp_path: Path) -> None:
    policy_path, candidate_path, evidence_path, context_path, out_path = written_inputs(
        tmp_path
    )
    candidate_path.write_text("{not-json", encoding="utf-8")

    result = run_wrapper_paths(
        policy_path=policy_path,
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        context_path=context_path,
        out_path=out_path,
    )

    payload = assert_risk_gate_error(result)
    assert payload["exception_class"] == "JSONDecodeError"


def test_wrapper_artifact_write_failure_exits_nonzero(tmp_path: Path) -> None:
    policy_path, candidate_path, evidence_path, context_path, _out_path = written_inputs(
        tmp_path
    )

    result = run_wrapper_paths(
        policy_path=policy_path,
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        context_path=context_path,
        out_path=tmp_path,
    )

    payload = assert_risk_gate_error(result)
    assert payload["exception_class"] in {"FileExistsError", "IsADirectoryError", "OSError"}


def test_wrapper_python_import_failure_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import evaluate_risk_gate as wrapper

    policy_path, candidate_path, evidence_path, context_path, out_path = written_inputs(
        tmp_path
    )

    def fail_import():
        raise ImportError("forced import failure for risk gate test")

    monkeypatch.setattr(wrapper, "load_risk_api", fail_import)

    code = wrapper.main(
        [
            "--policy",
            str(policy_path),
            "--candidate",
            str(candidate_path),
            "--evidence",
            str(evidence_path),
            "--context",
            str(context_path),
            "--out",
            str(out_path),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 1
    assert captured.out == ""
    assert payload["error"] == "risk_gate_error"
    assert payload["exception_class"] == "ImportError"


def test_wrapper_validator_failure_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import evaluate_risk_gate as wrapper

    policy_path, candidate_path, evidence_path, context_path, out_path = written_inputs(
        tmp_path
    )

    monkeypatch.setattr(
        wrapper.validator,
        "validate_contract",
        lambda _data: [{"path": "$.decision", "message": "forced validator failure"}],
    )

    code = wrapper.main(
        [
            "--policy",
            str(policy_path),
            "--candidate",
            str(candidate_path),
            "--evidence",
            str(evidence_path),
            "--context",
            str(context_path),
            "--out",
            str(out_path),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 1
    assert captured.out == ""
    assert payload["error"] == "validator_failed"
    assert payload["errors"] == [
        {"path": "$.decision", "message": "forced validator failure"}
    ]


def test_wrapper_argparse_subprocess_usage_failure_exits_nonzero() -> None:
    result = subprocess.run(
        [sys.executable, str(WRAPPER)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
