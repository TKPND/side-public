"""Policy evaluation tests for the common risk engine."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk import evaluate_risk, write_risk_artifact
from scripts import validate_risk_contract as validator


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_risk_contract.py"
STRATEGY_ID = "phase132-strategy.fixture"
POLICY_VERSION = "risk-policy.v1.phase132"
NON_FAIL_CLOSE_REASON = "insufficient_validation_power"
MISSING_REQUESTED_SIZE = object()


def base_inputs(tmp_path: Path, rules: list[dict]) -> tuple[dict, dict, dict, dict]:
    policy = {
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
        "rules": copy.deepcopy(rules),
    }
    candidate = {
        "strategy_id": STRATEGY_ID,
        "symbol_or_universe": "phase132-universe.fixture",
        "timeframe": "candidate-defined",
        "validation_refs": ["phase132-validation.fixture"],
        "requested_size": 10,
    }
    evidence = {"refs": ["risk/contracts/v1/fixtures/valid/base_valid.json"]}
    context = {
        "phase": "132-policy-evaluation-and-validator-alignment",
        "emitted_artifact_path": str(tmp_path / "risk-artifact.json"),
    }
    return policy, candidate, evidence, context


def rule(
    rule_id: str,
    decision_class: str,
    *,
    path: str = "candidate.strategy_id",
    op: str = "eq",
    value: object = STRATEGY_ID,
    allowed_size: float | None = None,
    fail_close_reason: str = NON_FAIL_CLOSE_REASON,
) -> dict:
    payload = {
        "id": rule_id,
        "decision_class": decision_class,
        "when": {"path": path, "op": op, "value": value},
        "fail_close_reason": fail_close_reason,
    }
    if allowed_size is not None:
        payload["allowed_size"] = allowed_size
    return payload


def assert_valid_engine_artifact(evaluation) -> None:
    assert validator.validate_contract(evaluation.artifact) == []
    assert evaluation.decision["binding_rule"] == evaluation.trace["binding_rule"]
    assert evaluation.decision["decision_class"] == evaluation.trace["decision_class"]


def test_precedence_orders_matching_rules_and_supporting_rules(
    tmp_path: Path,
) -> None:
    rules = [
        rule("rule.size", "size"),
        rule("rule.cap", "cap", allowed_size=3),
        rule("rule.reject", "reject"),
        rule("rule.kill", "kill", fail_close_reason="stale_evidence"),
        rule("rule.block", "block", fail_close_reason="malformed_policy"),
    ]
    policy, candidate, evidence, context = base_inputs(tmp_path, rules)

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    assert evaluation.decision["decision_class"] == "block"
    assert evaluation.decision["allowed_size"] == 0
    assert evaluation.decision["binding_rule"] == "rule.block"
    assert evaluation.decision["supporting_rules"] == [
        "rule.block",
        "rule.kill",
        "rule.reject",
        "rule.cap",
        "rule.size",
    ]
    assert evaluation.decision["fail_close_reason"] == "malformed_policy"
    assert_valid_engine_artifact(evaluation)


@pytest.mark.parametrize(
    ("decision_class", "rule_kwargs", "expected_allowed_size", "expected_reason"),
    [
        ("size", {}, 10, "insufficient_validation_power"),
        ("cap", {"allowed_size": 3}, 3, "insufficient_validation_power"),
        ("reject", {}, 0, "insufficient_validation_power"),
        ("kill", {"fail_close_reason": "stale_evidence"}, 0, "stale_evidence"),
        ("block", {"fail_close_reason": "malformed_policy"}, 0, "malformed_policy"),
    ],
)
def test_allowed_size_semantics_for_each_decision_class(
    tmp_path: Path,
    decision_class: str,
    rule_kwargs: dict,
    expected_allowed_size: float,
    expected_reason: str,
) -> None:
    rules = [rule(f"rule.{decision_class}", decision_class, **rule_kwargs)]
    policy, candidate, evidence, context = base_inputs(tmp_path, rules)

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    assert evaluation.decision["decision_class"] == decision_class
    assert evaluation.decision["allowed_size"] == expected_allowed_size
    assert evaluation.decision["binding_rule"] == f"rule.{decision_class}"
    assert evaluation.decision["supporting_rules"] == [f"rule.{decision_class}"]
    assert evaluation.decision["fail_close_reason"] == expected_reason
    assert_valid_engine_artifact(evaluation)


@pytest.mark.parametrize(
    "requested_size",
    [
        MISSING_REQUESTED_SIZE,
        "10",
        0,
        -1,
        True,
    ],
)
def test_invalid_requested_size_emits_candidate_validation_block(
    tmp_path: Path,
    requested_size: object,
) -> None:
    policy, candidate, evidence, context = base_inputs(
        tmp_path,
        [rule("rule.size", "size")],
    )
    if requested_size is MISSING_REQUESTED_SIZE:
        del candidate["requested_size"]
    else:
        candidate["requested_size"] = requested_size

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    binding_rule = evaluation.decision["binding_rule"]
    assert evaluation.decision["decision_class"] == "block"
    assert evaluation.decision["allowed_size"] == 0
    assert evaluation.decision["fail_close_reason"] == "candidate_validation_failure"
    assert binding_rule.startswith("candidate.requested_size.invalid")
    assert evaluation.decision["supporting_rules"] == [binding_rule]
    assert_valid_engine_artifact(evaluation)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda policy: policy.__setitem__("rules", "not-a-list"),
        lambda policy: policy.__setitem__("rules", []),
        lambda policy: policy["rules"][0].pop("id"),
        lambda policy: policy["rules"][0].pop("decision_class"),
        lambda policy: policy["rules"][0].pop("when"),
        lambda policy: policy.__setitem__(
            "rules",
            [rule("rule.cap", "cap")],
        ),
        lambda policy: policy.__setitem__(
            "rules",
            [
                {
                    key: value
                    for key, value in rule(
                        "rule.block",
                        "block",
                        fail_close_reason="malformed_policy",
                    ).items()
                    if key != "fail_close_reason"
                }
            ],
        ),
    ],
)
def test_malformed_policy_rules_emit_valid_malformed_policy_block(
    tmp_path: Path,
    mutate,
) -> None:
    policy, candidate, evidence, context = base_inputs(
        tmp_path,
        [rule("rule.size", "size")],
    )
    mutate(policy)

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    assert evaluation.decision["decision_class"] == "block"
    assert evaluation.decision["allowed_size"] == 0
    assert evaluation.decision["fail_close_reason"] == "malformed_policy"
    assert evaluation.decision["binding_rule"].startswith("policy.rules.malformed")
    assert_valid_engine_artifact(evaluation)


def test_missing_condition_path_is_non_match(tmp_path: Path) -> None:
    rules = [
        rule(
            "rule.block.missing_path",
            "block",
            path="candidate.missing",
            op="exists",
            value=True,
            fail_close_reason="malformed_policy",
        ),
        rule("rule.size", "size"),
    ]
    policy, candidate, evidence, context = base_inputs(tmp_path, rules)

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    assert evaluation.decision["decision_class"] == "size"
    assert evaluation.decision["binding_rule"] == "rule.size"
    assert evaluation.decision["supporting_rules"] == ["rule.size"]
    assert evaluation.decision["allowed_size"] == 10
    assert_valid_engine_artifact(evaluation)


def test_condition_ops_exists_eq_lte_gte_match(tmp_path: Path) -> None:
    rules = [
        rule(
            "rule.size.exists",
            "size",
            path="candidate.requested_size",
            op="exists",
            value=True,
        ),
        rule(
            "rule.cap.gte",
            "cap",
            path="candidate.requested_size",
            op="gte",
            value=5,
            allowed_size=6,
        ),
        rule(
            "rule.reject.lte",
            "reject",
            path="candidate.requested_size",
            op="lte",
            value=10,
        ),
        rule(
            "rule.kill.eq",
            "kill",
            path="candidate.strategy_id",
            op="eq",
            value=STRATEGY_ID,
            fail_close_reason="stale_evidence",
        ),
    ]
    policy, candidate, evidence, context = base_inputs(tmp_path, rules)

    evaluation = evaluate_risk(policy, candidate, evidence, context)

    assert evaluation.decision["decision_class"] == "kill"
    assert evaluation.decision["supporting_rules"] == [
        "rule.kill.eq",
        "rule.reject.lte",
        "rule.cap.gte",
        "rule.size.exists",
    ]
    assert_valid_engine_artifact(evaluation)


def test_written_engine_artifact_validates_with_existing_cli(tmp_path: Path) -> None:
    policy, candidate, evidence, context = base_inputs(
        tmp_path,
        [rule("rule.size", "size")],
    )
    evaluation = evaluate_risk(policy, candidate, evidence, context)
    written = write_risk_artifact(
        evaluation,
        Path(context["emitted_artifact_path"]),
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
