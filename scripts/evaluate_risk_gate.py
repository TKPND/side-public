#!/usr/bin/env python3
"""Evaluate a risk gate input bundle and print a compact Rust control summary."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import validate_risk_contract as validator

V1_CONTRACT_VERSION = "v1"
V2_CONTRACT_VERSION = "v2"
V2_RUNTIME_CANDIDATE_SCHEMA_VERSION = "risk_contract.v2.candidate.v1"
V2_NON_FAIL_CLOSED_REASON = "not_fail_closed"
V1_NON_FAIL_CLOSED_REASON = "insufficient_validation_power"
V2_RUNTIME_SURFACE_SIZE_BASIS = {
    "backtest": "unit_backtest_run",
    "scan": "unit_scan_slot",
    "paper": "unit_paper_slot_allocation",
}
V2_VALID_DECISION_REASONS = {
    "not_fail_closed",
    "missing_required_policy_field",
    "stale_evidence",
    "policy_evidence_contradiction",
    "evidence_acquisition_failure",
    "absent_source_proof",
    "malformed_policy",
    "candidate_validation_failure",
}
V2_VALID_FAIL_CLOSE_RULE_REASONS = V2_VALID_DECISION_REASONS - {"not_fail_closed"}


def load_risk_api():
    from risk import RiskInputError, evaluate_risk, write_risk_artifact

    return RiskInputError, evaluate_risk, write_risk_artifact


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--contract-version",
        choices=(V1_CONTRACT_VERSION, V2_CONTRACT_VERSION),
        default=V1_CONTRACT_VERSION,
    )
    return parser


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} top-level JSON must be an object")
    return value


def success_summary(evaluation, written: Path) -> dict[str, Any]:
    return {
        "decision_class": evaluation.decision["decision_class"],
        "allowed_size": evaluation.decision["allowed_size"],
        "binding_rule": evaluation.decision["binding_rule"],
        "fail_close_reason": evaluation.decision["fail_close_reason"],
        "policy_version": evaluation.decision["policy_version"],
        "candidate_id": evaluation.trace["candidate_id"],
        "artifact_path": str(written),
    }


def v2_success_summary(artifact: dict[str, Any], written: Path) -> dict[str, Any]:
    summary = {
        "decision_class": artifact["decision"]["decision_class"],
        "allowed_size": artifact["decision"]["allowed_size"],
        "binding_rule": artifact["decision"]["binding_rule"],
        "fail_close_reason": artifact["decision"]["fail_close_reason"],
        "policy_version": artifact["decision"]["policy_version"],
        "candidate_id": artifact["trace"]["candidate_id"],
        "artifact_path": str(written),
        "schema_version": artifact["schema_version"],
        "contract_version": artifact["contract_version"],
        "validator_result_schema_version": validator.V2_RESULT_SCHEMA_VERSION,
        "validated_schema_ref": validator.V2_CONTRACT_SCHEMA_PATH.as_posix(),
        "validator": "scripts/validate_risk_contract.py",
    }
    return summary


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def require_v2_runtime_candidate(candidate: dict[str, Any]) -> str:
    if candidate.get("candidate_schema_version") != V2_RUNTIME_CANDIDATE_SCHEMA_VERSION:
        raise ValueError("v2 runtime risk gate requires risk_contract.v2.candidate.v1")

    surface = require_mapping(candidate.get("surface"), "candidate.surface")
    sizing = require_mapping(candidate.get("sizing"), "candidate.sizing")
    runtime_surface = surface.get("runtime_surface")
    expected_size_basis = V2_RUNTIME_SURFACE_SIZE_BASIS.get(runtime_surface)

    if expected_size_basis is None:
        raise ValueError(f"unsupported v2 runtime_surface: {runtime_surface}")
    if surface.get("surface_status") != "implemented":
        raise ValueError("v2 runtime candidate requires surface_status = implemented")
    if surface.get("analysis_scope") != "none":
        raise ValueError("v2 runtime candidate requires analysis_scope = none")
    if surface.get("analysis_scope_status") != "not_applicable":
        raise ValueError("v2 runtime candidate requires analysis_scope_status = not_applicable")
    if sizing.get("requested_size_basis") != expected_size_basis:
        raise ValueError(
            f"v2 {runtime_surface} candidate requires requested_size_basis = {expected_size_basis}"
        )
    requested_size = sizing.get("requested_size")
    if isinstance(requested_size, bool) or not isinstance(requested_size, (int, float)) or requested_size <= 0:
        raise ValueError("v2 runtime candidate requires positive requested_size")
    return str(runtime_surface)


def v1_engine_candidate_from_v2(candidate: dict[str, Any]) -> dict[str, Any]:
    runtime_surface = require_v2_runtime_candidate(candidate)
    engine_candidate = {
        "strategy_id": candidate["strategy_id"],
        "symbol_or_universe": candidate["symbol_or_universe"],
        "timeframe": candidate["timeframe"],
        "validation_refs": list(candidate["validation_refs"]),
        "requested_size": candidate["sizing"]["requested_size"],
        "requested_size_basis": candidate["sizing"]["requested_size_basis"],
        "artifact_root": candidate.get("artifact_root"),
    }
    surface_payload = candidate.get("surface_payload", {})
    if runtime_surface == "backtest":
        engine_candidate["backtest_params"] = surface_payload.get("backtest_params", {})
    if runtime_surface == "scan":
        engine_candidate["scan_params"] = surface_payload.get("scan_params", {})
    return engine_candidate


def v2_fail_close_rule_reason(reason: Any) -> str:
    if reason not in V2_VALID_FAIL_CLOSE_RULE_REASONS:
        raise ValueError(f"unsupported v2 fail_close_rule reason: {reason}")
    return str(reason)


def v2_decision_reason(decision_class: str, reason: Any) -> str:
    if reason == V1_NON_FAIL_CLOSED_REASON and decision_class in {"size", "cap", "reject"}:
        return V2_NON_FAIL_CLOSED_REASON
    if reason in V2_VALID_DECISION_REASONS:
        return str(reason)
    raise ValueError(f"unsupported v2 decision fail_close_reason: {reason}")


def v2_policy_from_v1(policy: dict[str, Any]) -> dict[str, Any]:
    fail_close_rules = [
        {
            "condition": rule["condition"],
            "decision_class": rule["decision_class"],
            "fail_close_reason": v2_fail_close_rule_reason(rule["fail_close_reason"]),
        }
        for rule in policy["fail_close_rules"]
    ]
    return {
        "version": policy["version"],
        "owner": policy["owner"],
        "effective_from": policy["effective_from"],
        "required_fields": [
            "candidate.surface.runtime_surface",
            "candidate.sizing.requested_size",
            "evidence.refs",
            "trace.emitted_artifact_path",
        ],
        "fail_close_rules": fail_close_rules,
        "rules": {
            "legacy_v1_rules": policy.get("rules", []),
            "non_stop_fail_close_reason": V2_NON_FAIL_CLOSED_REASON,
        },
    }


def v2_decision_from_v1(decision: dict[str, Any]) -> dict[str, Any]:
    decision_class = decision["decision_class"]
    return {
        "decision_class": decision_class,
        "allowed_size": decision["allowed_size"],
        "binding_rule": decision["binding_rule"],
        "supporting_rules": list(decision["supporting_rules"]),
        "fail_close_reason": v2_decision_reason(decision_class, decision["fail_close_reason"]),
        "evidence_refs": list(decision["evidence_refs"]),
        "policy_version": decision["policy_version"],
    }


def v2_application_from_decision(
    candidate: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    decision_class = decision["decision_class"]
    runtime_surface = candidate["surface"]["runtime_surface"]
    requested_size = candidate["sizing"]["requested_size"]
    allowed_size = decision["allowed_size"]

    if decision_class == "cap":
        if runtime_surface == "paper":
            if not isinstance(allowed_size, (int, float)) or isinstance(allowed_size, bool):
                raise ValueError("paper cap allowed_size must be numeric")
            if not math.isfinite(allowed_size):
                raise ValueError("paper cap allowed_size must be finite")
            if allowed_size < 0:
                raise ValueError("paper cap allowed_size must be non-negative")
            if allowed_size > requested_size:
                raise ValueError("paper cap allowed_size must be <= requested_size")
        application_reason = {
            "backtest": "backtest v2 opt-in applied validated cap sizing",
            "scan": "scan v2 opt-in applied validated scan-slot cap sizing",
            "paper": "paper v2 opt-in applied validated slot-allocation cap sizing",
        }.get(runtime_surface, "v2 opt-in applied validated cap sizing")
        return {
            "execution_state": "continued",
            "application_status": "applied",
            "runtime_sizing_applied": True,
            "sizing_effect": "reduced" if allowed_size < requested_size else "none",
            "effective_size": allowed_size,
            "metrics_rescaled": False,
            "application_reason": application_reason,
        }
    if decision_class == "size":
        application_reason = {
            "backtest": "backtest v2 opt-in continued at requested size",
            "scan": "scan v2 opt-in continued at requested scan-slot size",
            "paper": "paper v2 opt-in continued at requested slot allocation",
        }.get(runtime_surface, "v2 opt-in continued at requested size")
        return {
            "execution_state": "continued",
            "application_status": "not_applicable",
            "runtime_sizing_applied": False,
            "sizing_effect": "none",
            "effective_size": allowed_size,
            "metrics_rescaled": False,
            "application_reason": application_reason,
        }
    application_reason = {
        "backtest": "risk decision stopped before metric-producing backtest execution",
        "scan": "risk decision stopped before scan metric and WFD execution",
        "paper": "risk decision stopped before paper tick execution",
    }.get(runtime_surface, "risk decision stopped before metric-producing execution")
    return {
        "execution_state": "stopped",
        "application_status": "not_applicable",
        "runtime_sizing_applied": False,
        "sizing_effect": "not_applicable",
        "effective_size": 0,
        "metrics_rescaled": False,
        "application_reason": application_reason,
    }


def compose_v2_artifact(
    *,
    policy: dict[str, Any],
    candidate: dict[str, Any],
    evidence: dict[str, Any],
    context: dict[str, Any],
    evaluation,
) -> dict[str, Any]:
    require_v2_runtime_candidate(candidate)
    decision = v2_decision_from_v1(evaluation.decision)
    return {
        "schema_version": validator.V2_CONTRACT_SCHEMA_VERSION,
        "contract_version": validator.V2_CONTRACT_VERSION,
        "policy": v2_policy_from_v1(policy),
        "candidate": candidate,
        "evidence": evidence,
        "context": context,
        "decision": decision,
        "application": v2_application_from_decision(candidate, decision),
        "trace": {
            "policy_version": evaluation.trace["policy_version"],
            "candidate_id": evaluation.trace["candidate_id"],
            "input_evidence_refs": list(evaluation.trace["input_evidence_refs"]),
            "binding_rule": evaluation.trace["binding_rule"],
            "decision_class": evaluation.trace["decision_class"],
            "emitted_artifact_path": evaluation.trace["emitted_artifact_path"],
            "validated_schema_version": validator.V2_CONTRACT_SCHEMA_VERSION,
            "validator_result_schema_version": validator.V2_RESULT_SCHEMA_VERSION,
        },
    }


def write_json_artifact(artifact: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(payload)
    return path


def print_risk_gate_error(exc: BaseException) -> None:
    print(
        json.dumps(
            {
                "error": "risk_gate_error",
                "message": str(exc),
                "exception_class": exc.__class__.__name__,
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _risk_input_error, evaluate_risk, write_risk_artifact = load_risk_api()

        policy = load_json(args.policy)
        candidate = load_json(args.candidate)
        evidence = load_json(args.evidence)
        context = load_json(args.context)

        if args.contract_version == V2_CONTRACT_VERSION:
            engine_candidate = v1_engine_candidate_from_v2(candidate)
            evaluation = evaluate_risk(policy, engine_candidate, evidence, context)
            artifact = compose_v2_artifact(
                policy=policy,
                candidate=candidate,
                evidence=evidence,
                context=context,
                evaluation=evaluation,
            )
            written = write_json_artifact(artifact, args.out)
            errors = validator.validate_v2_contract(artifact)
            if errors:
                print(
                    json.dumps(
                        {"error": "validator_failed", "errors": errors},
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
                return 1

            print(json.dumps(v2_success_summary(artifact, written), sort_keys=True))
            return 0

        evaluation = evaluate_risk(policy, candidate, evidence, context)
        written = write_risk_artifact(evaluation, args.out)
        artifact = load_json(written)
        errors = validator.validate_contract(artifact)
        if errors:
            print(
                json.dumps(
                    {"error": "validator_failed", "errors": errors},
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1

        print(json.dumps(success_summary(evaluation, written), sort_keys=True))
        return 0
    except (ImportError, OSError, ValueError, json.JSONDecodeError) as exc:
        print_risk_gate_error(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
