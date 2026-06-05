"""Contract tests for the common risk engine API."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from risk import RiskEvaluation, RiskInputError, evaluate_risk, write_risk_artifact
from scripts import validate_risk_contract as validator


ROOT = Path(__file__).resolve().parents[1]


def valid_inputs(tmp_path: Path) -> tuple[dict, dict, dict, dict]:
    policy = {
        "version": "risk-policy.v1.phase132",
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
            }
        ],
        "rules": [
            {
                "id": "policy.size.default",
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
    candidate = {
        "strategy_id": "phase132-strategy.fixture",
        "symbol_or_universe": "phase132-universe.fixture",
        "timeframe": "candidate-defined",
        "validation_refs": ["phase132-validation.fixture"],
        "requested_size": 2.5,
    }
    evidence = {"refs": ["risk/contracts/v1/fixtures/valid/base_valid.json"]}
    context = {
        "phase": "132-policy-evaluation-and-validator-alignment",
        "emitted_artifact_path": str(tmp_path / "risk-artifact.json"),
    }
    return policy, candidate, evidence, context


def test_risk_package_exposes_phase131_public_api() -> None:
    assert callable(evaluate_risk)
    assert issubclass(RiskInputError, Exception)


def test_evaluate_risk_returns_typed_artifact_centered_result(
    tmp_path: Path,
) -> None:
    policy, candidate, evidence, context = valid_inputs(tmp_path)

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    assert isinstance(evaluation, RiskEvaluation)
    assert not isinstance(evaluation, dict)
    assert evaluation.artifact["schema_version"] == "risk_contract.v1"
    assert evaluation.artifact["contract_version"] == "v1"
    assert evaluation.artifact["policy"] == policy
    assert evaluation.artifact["candidate"] == candidate
    assert evaluation.artifact["evidence"] == evidence
    assert evaluation.artifact["context"] == context
    assert evaluation.decision is evaluation.artifact["decision"]
    assert evaluation.trace is evaluation.artifact["trace"]
    assert evaluation.artifact_path == context["emitted_artifact_path"]


def test_phase132_size_decision_is_deterministic_for_valid_inputs(
    tmp_path: Path,
) -> None:
    policy, candidate, evidence, context = valid_inputs(tmp_path)

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    assert evaluation.decision == {
        "decision_class": "size",
        "allowed_size": 2.5,
        "binding_rule": "policy.size.default",
        "supporting_rules": ["policy.size.default"],
        "fail_close_reason": "insufficient_validation_power",
        "evidence_refs": evidence["refs"],
        "policy_version": policy["version"],
    }


def test_trace_identity_fields_are_copied_from_inputs(tmp_path: Path) -> None:
    policy, candidate, evidence, context = valid_inputs(tmp_path)

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    assert evaluation.trace == {
        "policy_version": policy["version"],
        "candidate_id": candidate["strategy_id"],
        "input_evidence_refs": evidence["refs"],
        "binding_rule": "policy.size.default",
        "decision_class": "size",
        "emitted_artifact_path": context["emitted_artifact_path"],
    }


def test_evaluate_risk_does_not_mutate_inputs(tmp_path: Path) -> None:
    policy, candidate, evidence, context = valid_inputs(tmp_path)
    original_policy = copy.deepcopy(policy)
    original_candidate = copy.deepcopy(candidate)
    original_evidence = copy.deepcopy(evidence)
    original_context = copy.deepcopy(context)

    evaluate_risk(policy, candidate, evidence, context)

    assert policy == original_policy
    assert candidate == original_candidate
    assert evidence == original_evidence
    assert context == original_context


@pytest.mark.parametrize(
    ("block", "field", "expected_message"),
    [
        ("policy", "version", "policy.version"),
        ("candidate", "strategy_id", "candidate.strategy_id"),
        ("evidence", "refs", "evidence.refs"),
        ("context", "emitted_artifact_path", "context.emitted_artifact_path"),
    ],
)
def test_missing_required_construction_inputs_raise_risk_input_error(
    tmp_path: Path,
    block: str,
    field: str,
    expected_message: str,
) -> None:
    policy, candidate, evidence, context = valid_inputs(tmp_path)
    inputs = {
        "policy": policy,
        "candidate": candidate,
        "evidence": evidence,
        "context": context,
    }
    del inputs[block][field]

    with pytest.raises(RiskInputError, match=expected_message):
        evaluate_risk(policy, candidate, evidence, context)


def test_write_risk_artifact_emits_canonical_json(tmp_path: Path) -> None:
    policy, candidate, evidence, context = valid_inputs(tmp_path)
    evaluation = evaluate_risk(policy, candidate, evidence, context)

    written = write_risk_artifact(
        evaluation,
        Path(context["emitted_artifact_path"]),
    )

    assert written == Path(context["emitted_artifact_path"])
    assert written.exists()
    assert written.read_text(encoding="utf-8") == (
        json.dumps(
            evaluation.artifact,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )


def test_write_risk_artifact_rejects_existing_file_without_overwrite(
    tmp_path: Path,
) -> None:
    policy, candidate, evidence, context = valid_inputs(tmp_path)
    evaluation = evaluate_risk(policy, candidate, evidence, context)
    target = Path(context["emitted_artifact_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("sentinel\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="risk artifact already exists"):
        write_risk_artifact(evaluation, target)

    assert target.read_text(encoding="utf-8") == "sentinel\n"


def test_phase132_artifact_validates_with_existing_validator(tmp_path: Path) -> None:
    policy, candidate, evidence, context = valid_inputs(tmp_path)

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    assert validator.validate_contract(evaluation.artifact) == []


def test_missing_policy_rules_returns_malformed_policy_artifact(
    tmp_path: Path,
) -> None:
    policy, candidate, evidence, context = valid_inputs(tmp_path)
    del policy["rules"]

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    assert evaluation.decision["decision_class"] == "block"
    assert evaluation.decision["allowed_size"] == 0
    assert evaluation.decision["fail_close_reason"] == "malformed_policy"
    assert evaluation.decision["binding_rule"].startswith("policy.rules.malformed")
    assert validator.validate_contract(evaluation.artifact) == []


def test_risk_engine_module_does_not_import_scripts() -> None:
    source = (ROOT / "risk" / "engine.py").read_text(encoding="utf-8")

    assert "from scripts" not in source
    assert "import scripts" not in source
