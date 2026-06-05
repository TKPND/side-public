"""Common risk engine API for risk_contract.v1 artifacts."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


CONTRACT_SCHEMA_VERSION = "risk_contract.v1"
CONTRACT_VERSION = "v1"
CONTRACT_SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "contracts"
    / "v1"
    / "risk_contract_v1.schema.json"
)
MALFORMED_POLICY_BINDING = "policy.rules.malformed"
INVALID_REQUESTED_SIZE_BINDING = "candidate.requested_size.invalid"
MALFORMED_POLICY_REASON = "malformed_policy"
CANDIDATE_VALIDATION_REASON = "candidate_validation_failure"
NON_FAIL_CLOSE_REASON = "insufficient_validation_power"
CONDITION_OPS = {"exists", "eq", "lte", "gte"}
DECISION_PRECEDENCE = {
    "block": 0,
    "kill": 1,
    "reject": 2,
    "cap": 3,
    "size": 4,
}


class RiskInputError(ValueError):
    """Raised when risk evaluator construction inputs are missing or invalid."""


@dataclass(frozen=True)
class RiskEvaluation:
    artifact: dict[str, Any]

    @property
    def decision(self) -> dict[str, Any]:
        return self.artifact["decision"]

    @property
    def trace(self) -> dict[str, Any]:
        return self.artifact["trace"]

    @property
    def artifact_path(self) -> str:
        return str(self.trace["emitted_artifact_path"])


def _require_mapping(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RiskInputError(f"{name} must be a mapping")
    return value


def _require_non_empty_string(
    block: Mapping[str, Any],
    field: str,
    path: str,
) -> str:
    value = block.get(field)
    if not isinstance(value, str) or value == "":
        raise RiskInputError(f"missing required input: {path}")
    return value


def _require_refs(block: Mapping[str, Any]) -> list[str]:
    refs = block.get("refs")
    if (
        not isinstance(refs, (list, tuple))
        or not refs
        or not all(isinstance(ref, str) and ref != "" for ref in refs)
    ):
        raise RiskInputError("missing required input: evidence.refs")
    return list(refs)


def _load_contract_schema() -> dict[str, Any]:
    return json.loads(CONTRACT_SCHEMA_PATH.read_text(encoding="utf-8"))


def _schema_decision_classes() -> set[str]:
    schema = _load_contract_schema()
    decision_enum = schema["properties"]["decision"]["properties"]["decision_class"]["enum"]
    trace_enum = schema["properties"]["trace"]["properties"]["decision_class"]["enum"]
    if decision_enum != trace_enum:
        raise RiskInputError("decision and trace decision_class enums differ")
    return set(decision_enum)


def _schema_fail_close_reasons() -> set[str]:
    schema = _load_contract_schema()
    return set(schema["$defs"]["fail_close_reason"]["enum"])


def _positive_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return value


def _resolve_path(
    path: str,
    *,
    policy: Mapping[str, Any],
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[bool, Any]:
    roots = {
        "policy": policy,
        "candidate": candidate,
        "evidence": evidence,
        "context": context,
    }
    parts = path.split(".")
    if not parts or parts[0] not in roots:
        return False, None

    current: Any = roots[parts[0]]
    for part in parts[1:]:
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _condition_matches(
    when: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
    context: Mapping[str, Any],
) -> bool:
    exists, observed = _resolve_path(
        when["path"],
        policy=policy,
        candidate=candidate,
        evidence=evidence,
        context=context,
    )
    if not exists:
        return False

    op = when["op"]
    expected = when.get("value")
    if op == "exists":
        return True
    if op == "eq":
        return observed == expected

    try:
        if op == "lte":
            return observed <= expected
        if op == "gte":
            return observed >= expected
    except TypeError:
        return False
    return False


def _malformed_binding(suffix: str) -> str:
    return f"{MALFORMED_POLICY_BINDING}.{suffix}"


def _normalize_rule(
    rule: Any,
    index: int,
    decision_classes: set[str],
    fail_close_reasons: set[str],
) -> dict[str, Any]:
    if not isinstance(rule, Mapping):
        raise RiskInputError(_malformed_binding(f"{index}.not_mapping"))

    rule_id = rule.get("id")
    if not isinstance(rule_id, str) or rule_id == "":
        raise RiskInputError(_malformed_binding(f"{index}.id"))

    decision_class = rule.get("decision_class")
    if decision_class not in decision_classes:
        raise RiskInputError(_malformed_binding(f"{index}.decision_class"))

    when = rule.get("when")
    if not isinstance(when, Mapping):
        raise RiskInputError(_malformed_binding(f"{index}.when"))

    path = when.get("path")
    if not isinstance(path, str) or path == "":
        raise RiskInputError(_malformed_binding(f"{index}.when.path"))

    op = when.get("op")
    if op not in CONDITION_OPS:
        raise RiskInputError(_malformed_binding(f"{index}.when.op"))
    if op in {"eq", "lte", "gte"} and "value" not in when:
        raise RiskInputError(_malformed_binding(f"{index}.when.value"))

    normalized = {
        "id": rule_id,
        "decision_class": decision_class,
        "when": {"path": path, "op": op, "value": when.get("value")},
        "index": index,
    }

    if decision_class == "cap":
        allowed_size = _positive_number(rule.get("allowed_size"))
        if allowed_size is None:
            raise RiskInputError(_malformed_binding(f"{index}.allowed_size"))
        normalized["allowed_size"] = allowed_size

    reason = rule.get("fail_close_reason")
    if decision_class in {"kill", "block"}:
        if reason not in fail_close_reasons:
            raise RiskInputError(_malformed_binding(f"{index}.fail_close_reason"))
    elif reason not in fail_close_reasons:
        reason = NON_FAIL_CLOSE_REASON
    normalized["fail_close_reason"] = reason

    return normalized


def _normalize_rules(
    policy: Mapping[str, Any],
    decision_classes: set[str],
    fail_close_reasons: set[str],
) -> list[dict[str, Any]]:
    rules = policy.get("rules")
    if not isinstance(rules, (list, tuple)) or not rules:
        raise RiskInputError(_malformed_binding("missing_or_empty"))
    return [
        _normalize_rule(rule, index, decision_classes, fail_close_reasons)
        for index, rule in enumerate(rules)
    ]


def _requested_size(candidate: Mapping[str, Any]) -> float | int | None:
    return _positive_number(candidate.get("requested_size"))


def _block_decision(
    binding_rule: str,
    fail_close_reason: str,
    evidence_refs: list[str],
    policy_version: str,
) -> dict[str, Any]:
    return {
        "decision_class": "block",
        "allowed_size": 0,
        "binding_rule": binding_rule,
        "supporting_rules": [binding_rule],
        "fail_close_reason": fail_close_reason,
        "evidence_refs": evidence_refs,
        "policy_version": policy_version,
    }


def _decision_from_rule(
    rule: Mapping[str, Any],
    requested_size: float | int,
    supporting_rules: list[str],
    evidence_refs: list[str],
    policy_version: str,
) -> dict[str, Any]:
    decision_class = rule["decision_class"]
    if decision_class == "size":
        allowed_size = requested_size
    elif decision_class == "cap":
        allowed_size = min(requested_size, rule["allowed_size"])
    else:
        allowed_size = 0

    return {
        "decision_class": decision_class,
        "allowed_size": allowed_size,
        "binding_rule": rule["id"],
        "supporting_rules": supporting_rules,
        "fail_close_reason": rule["fail_close_reason"],
        "evidence_refs": evidence_refs,
        "policy_version": policy_version,
    }


def _trace(
    policy_version: str,
    candidate_id: str,
    evidence_refs: list[str],
    binding_rule: str,
    decision_class: str,
    artifact_path: str,
) -> dict[str, Any]:
    return {
        "policy_version": policy_version,
        "candidate_id": candidate_id,
        "input_evidence_refs": evidence_refs,
        "binding_rule": binding_rule,
        "decision_class": decision_class,
        "emitted_artifact_path": artifact_path,
    }


def _artifact(
    policy: Mapping[str, Any],
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
    context: Mapping[str, Any],
    decision: Mapping[str, Any],
    trace: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "policy": copy.deepcopy(dict(policy)),
        "candidate": copy.deepcopy(dict(candidate)),
        "evidence": copy.deepcopy(dict(evidence)),
        "context": copy.deepcopy(dict(context)),
        "decision": dict(decision),
        "trace": dict(trace),
    }


def _matching_rules(
    rules: list[dict[str, Any]],
    policy: Mapping[str, Any],
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    matched_rules = [
        rule
        for rule in rules
        if _condition_matches(
            rule["when"],
            policy=policy,
            candidate=candidate,
            evidence=evidence,
            context=context,
        )
    ]
    return sorted(
        matched_rules,
        key=lambda rule: (DECISION_PRECEDENCE[rule["decision_class"]], rule["index"]),
    )


def evaluate_risk(
    policy: Mapping[str, Any],
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
    context: Mapping[str, Any],
) -> RiskEvaluation:
    policy = _require_mapping(policy, "policy")
    candidate = _require_mapping(candidate, "candidate")
    evidence = _require_mapping(evidence, "evidence")
    context = _require_mapping(context, "context")

    policy_version = _require_non_empty_string(policy, "version", "policy.version")
    candidate_id = _require_non_empty_string(candidate, "strategy_id", "candidate.strategy_id")
    evidence_refs = _require_refs(evidence)
    artifact_path = _require_non_empty_string(context, "emitted_artifact_path", "context.emitted_artifact_path")

    decision_classes = _schema_decision_classes()
    fail_close_reasons = _schema_fail_close_reasons()

    requested_size = _requested_size(candidate)
    if requested_size is None:
        binding_rule = f"{INVALID_REQUESTED_SIZE_BINDING}.requested_size"
        decision = _block_decision(
            binding_rule,
            CANDIDATE_VALIDATION_REASON,
            evidence_refs,
            policy_version,
        )
        trace = _trace(
            policy_version,
            candidate_id,
            evidence_refs,
            binding_rule,
            "block",
            artifact_path,
        )
        return RiskEvaluation(
            artifact=_artifact(policy, candidate, evidence, context, decision, trace),
        )

    try:
        rules = _normalize_rules(policy, decision_classes, fail_close_reasons)
    except RiskInputError as exc:
        binding_rule = str(exc)
        if not binding_rule.startswith(MALFORMED_POLICY_BINDING):
            binding_rule = _malformed_binding("unknown")
        decision = _block_decision(
            binding_rule,
            MALFORMED_POLICY_REASON,
            evidence_refs,
            policy_version,
        )
        trace = _trace(
            policy_version,
            candidate_id,
            evidence_refs,
            binding_rule,
            "block",
            artifact_path,
        )
        return RiskEvaluation(
            artifact=_artifact(policy, candidate, evidence, context, decision, trace),
        )

    matched_rules = _matching_rules(rules, policy, candidate, evidence, context)
    if not matched_rules:
        binding_rule = _malformed_binding("no_match")
        decision = _block_decision(
            binding_rule,
            MALFORMED_POLICY_REASON,
            evidence_refs,
            policy_version,
        )
        trace = _trace(
            policy_version,
            candidate_id,
            evidence_refs,
            binding_rule,
            "block",
            artifact_path,
        )
        return RiskEvaluation(
            artifact=_artifact(policy, candidate, evidence, context, decision, trace),
        )

    winner = matched_rules[0]
    supporting_rules = [rule["id"] for rule in matched_rules]
    decision = _decision_from_rule(
        winner,
        requested_size,
        supporting_rules,
        evidence_refs,
        policy_version,
    )
    trace = _trace(
        policy_version,
        candidate_id,
        evidence_refs,
        decision["binding_rule"],
        decision["decision_class"],
        artifact_path,
    )
    return RiskEvaluation(
        artifact=_artifact(policy, candidate, evidence, context, decision, trace),
    )


def write_risk_artifact(evaluation: RiskEvaluation, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            evaluation.artifact,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise FileExistsError(f"risk artifact already exists: {target}") from exc
    return target
