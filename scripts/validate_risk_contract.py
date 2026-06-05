#!/usr/bin/env python3
"""Validate Side risk_contract v1/v2 artifacts with deterministic JSON output."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
CONTRACT_SCHEMA_VERSION = "risk_contract.v1"
CONTRACT_VERSION = "v1"
CONTRACT_SCHEMA_PATH = Path("risk/contracts/v1/risk_contract_v1.schema.json")
V2_RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v2"
V2_CONTRACT_SCHEMA_VERSION = "risk_contract.v2"
V2_CONTRACT_VERSION = "v2"
V2_CONTRACT_SCHEMA_PATH = Path("risk/contracts/v2/risk_contract_v2.schema.json")
V2_RESULT_SCHEMA_PATH = Path("risk/contracts/v2/risk_contract_validator_result_v2.schema.json")

UNSUPPORTED_CONTRACT_VERSION = "unsupported_contract_version"
MISSING_REQUIRED = "missing_required_block_or_field"
INVALID_DECISION_CLASS = "invalid_decision_class"
INVALID_FAIL_CLOSE_REASON = "invalid_fail_close_reason"
DECISION_TRACE_MISMATCH = "decision_trace_mismatch"
INVALID_RUNTIME_SURFACE_SCOPE = "invalid_runtime_surface_scope"
INVALID_SURFACE_CANDIDATE_ENVELOPE = "invalid_surface_candidate_envelope"
INVALID_LIVE_CONTRACT_PROOF = "invalid_live_contract_proof"
LIVE_ORDER_NOTIONAL_BASIS = "unit_live_order_notional"
LIVE_PROOF_BLOCKS = (
    "account_proof",
    "market_proof",
    "order_intent",
    "kill_switch_proof",
    "idempotency_proof",
)
LIVE_FORBIDDEN_KEY_FRAGMENTS = (
    "raw_account_id",
    "account_id",
    "credential",
    "token",
    "secret",
    "private_key",
    "cookie",
    "endpoint",
)
LIVE_FORBIDDEN_VALUE_FRAGMENTS = (
    "raw_account_id",
    "raw-account-id",
    "raw_idempotency_key",
    "raw-idempotency-key",
    "credential",
    "token",
    "secret",
    "private_key",
    "password",
    "api_key",
    "api-key",
    "apikey",
    "cookie",
    "endpoint",
)
LIVE_NOT_WIRED_ALLOWED_SURFACE_PAYLOAD_FIELDS = (
    "fixture_kind",
    "live_runtime",
    "live_runtime_claim_allowed",
)
LIVE_ACCOUNT_PROOF_FIELDS = (
    "account_alias",
    "broker_alias",
    "account_snapshot_id",
    "snapshot_ts",
    "snapshot_max_age_ms",
    "base_currency",
    "equity",
    "cash_available",
    "buying_power",
    "open_exposure_digest",
    "open_orders_digest",
)
LIVE_MARKET_PROOF_FIELDS = (
    "symbol",
    "market_snapshot_id",
    "market_ts",
    "market_max_age_ms",
    "bid",
    "ask",
    "spread_bps",
    "price_source",
)
LIVE_ORDER_INTENT_FIELDS = (
    "side",
    "order_type",
    "time_in_force",
    "requested_notional",
    "allowed_notional",
    "notional_currency",
    "notional_source",
    "price_bounds",
    "max_slippage_bps",
    "idempotency_key_hash",
    "order_mutation_allowed",
)
LIVE_KILL_SWITCH_PROOF_FIELDS = (
    "global_gate_status",
    "global_gate_ref",
    "strategy_gate_status",
    "strategy_gate_ref",
    "symbol_gate_status",
    "symbol_gate_ref",
    "broker_account_gate_status",
    "broker_account_gate_ref",
    "exposure_gate_status",
    "exposure_gate_ref",
    "data_quality_gate_status",
    "data_quality_gate_ref",
    "proof_ts",
    "proof_max_age_ms",
)
LIVE_IDEMPOTENCY_PROOF_FIELDS = (
    "candidate_identity_digest",
    "idempotency_key_hash",
    "duplicate_check_status",
    "duplicate_check_ref",
    "proof_ts",
    "proof_max_age_ms",
)
LIVE_REQUIRED_PROOF_FIELDS = {
    "account_proof": LIVE_ACCOUNT_PROOF_FIELDS,
    "market_proof": LIVE_MARKET_PROOF_FIELDS,
    "order_intent": LIVE_ORDER_INTENT_FIELDS,
    "kill_switch_proof": LIVE_KILL_SWITCH_PROOF_FIELDS,
    "idempotency_proof": LIVE_IDEMPOTENCY_PROOF_FIELDS,
}
LIVE_STATUS_VALUES = {"passed"}
LIVE_ALLOWED_SURFACE_PAYLOAD_FIELDS = (
    "runtime_adoption",
    "order_mutation_allowed",
    "broker_mutation_attempted",
    *LIVE_PROOF_BLOCKS,
)


def error(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def result(checked_path: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "checked_path": checked_path,
        "valid": not errors,
        "errors": errors,
    }


def v2_validated_schema() -> dict[str, str]:
    return {
        "schema_version": V2_CONTRACT_SCHEMA_VERSION,
        "contract_version": V2_CONTRACT_VERSION,
        "path": V2_CONTRACT_SCHEMA_PATH.as_posix(),
    }


def contract_identity(data: dict[str, Any]) -> dict[str, str | None]:
    schema_version = data.get("schema_version")
    contract_version = data.get("contract_version")
    return {
        "schema_version": schema_version if isinstance(schema_version, str) else None,
        "contract_version": contract_version if isinstance(contract_version, str) else None,
    }


def v2_result(checked_path: str, data: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    valid = not errors
    return {
        "schema_version": V2_RESULT_SCHEMA_VERSION,
        "checked_path": checked_path,
        "valid": valid,
        "contract_identity": contract_identity(data),
        "validated_schema": v2_validated_schema(),
        "dispatch": {
            "status": "validated" if valid else "validation_failed",
            "reason": None if valid else errors[0]["code"],
        },
        "errors": errors,
    }


def load_contract(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"could not read contract: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data


def contract_schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / CONTRACT_SCHEMA_PATH


def v2_contract_schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / V2_CONTRACT_SCHEMA_PATH


def v2_result_schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / V2_RESULT_SCHEMA_PATH


def load_contract_schema(schema_path: Path | None = None) -> dict[str, Any]:
    path = schema_path if schema_path is not None else contract_schema_path()
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed schema JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"could not read schema: {exc}") from exc
    if not isinstance(schema, dict):
        raise ValueError("schema top-level JSON must be an object")
    return schema


def schema_decision_classes(schema: dict[str, Any]) -> set[str]:
    decision_enum = schema["properties"]["decision"]["properties"]["decision_class"]["enum"]
    trace_enum = schema["properties"]["trace"]["properties"]["decision_class"]["enum"]
    if decision_enum != trace_enum:
        raise ValueError("decision and trace decision_class enums differ")
    return set(decision_enum)


def schema_fail_close_rule_decision_classes(schema: dict[str, Any]) -> set[str]:
    enum = schema["$defs"]["fail_close_rule"]["properties"]["decision_class"]["enum"]
    return set(enum)


def schema_fail_close_reasons(schema: dict[str, Any]) -> set[str]:
    return set(schema["$defs"]["fail_close_reason"]["enum"])


def schema_required_top_level(schema: dict[str, Any]) -> tuple[str, ...]:
    return tuple(schema["required"])


def schema_required_nested_fields(schema: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    required_blocks = ("policy", "candidate", "evidence", "decision", "trace")
    return {
        block: tuple(schema["properties"][block]["required"])
        for block in required_blocks
    }


def schema_required_fail_close_rule_fields(schema: dict[str, Any]) -> tuple[str, ...]:
    return tuple(schema["$defs"]["fail_close_rule"]["required"])


def schema_facts(schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema if schema is not None else load_contract_schema()
    return {
        "decision_classes": schema_decision_classes(schema),
        "fail_close_rule_decision_classes": schema_fail_close_rule_decision_classes(schema),
        "fail_close_reasons": schema_fail_close_reasons(schema),
        "required_top_level": schema_required_top_level(schema),
        "required_nested_fields": schema_required_nested_fields(schema),
        "required_fail_close_rule_fields": schema_required_fail_close_rule_fields(schema),
    }


def schema_v2_decision_classes(schema: dict[str, Any]) -> set[str]:
    return set(schema["$defs"]["decision_class"]["enum"])


def schema_v2_fail_close_rule_decision_classes(schema: dict[str, Any]) -> set[str]:
    enum = schema["$defs"]["fail_close_rule"]["properties"]["decision_class"]["enum"]
    return set(enum)


def schema_v2_decision_fail_close_reasons(schema: dict[str, Any]) -> set[str]:
    return set(schema["$defs"]["fail_close_reason_v2"]["enum"])


def schema_v2_fail_close_rule_reasons(schema: dict[str, Any]) -> set[str]:
    return set(schema["$defs"]["fail_close_rule_reason_v2"]["enum"])


def schema_v2_runtime_surfaces(schema: dict[str, Any]) -> set[str]:
    return set(schema["$defs"]["surface"]["properties"]["runtime_surface"]["enum"])


def schema_v2_analysis_scopes(schema: dict[str, Any]) -> set[str]:
    return set(schema["$defs"]["surface"]["properties"]["analysis_scope"]["enum"])


def schema_v2_required_top_level(schema: dict[str, Any]) -> tuple[str, ...]:
    return tuple(schema["required"])


def schema_v2_required_nested_fields(schema: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    required_blocks = ("policy", "candidate", "evidence", "decision", "application", "trace")
    return {
        block: tuple(schema["$defs"][block]["required"])
        for block in required_blocks
    }


def schema_v2_required_surface_fields(schema: dict[str, Any]) -> tuple[str, ...]:
    return tuple(schema["$defs"]["surface"]["required"])


def schema_v2_required_sizing_fields(schema: dict[str, Any]) -> tuple[str, ...]:
    return tuple(schema["$defs"]["sizing"]["required"])


def schema_v2_required_fail_close_rule_fields(schema: dict[str, Any]) -> tuple[str, ...]:
    return tuple(schema["$defs"]["fail_close_rule"]["required"])


def schema_v2_facts(schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema if schema is not None else load_contract_schema(v2_contract_schema_path())
    # Load the result schema as a boundary guard so the v2 result contract cannot
    # silently disappear while exact v2/v2 artifacts are accepted.
    load_contract_schema(v2_result_schema_path())
    return {
        "decision_classes": schema_v2_decision_classes(schema),
        "fail_close_rule_decision_classes": schema_v2_fail_close_rule_decision_classes(schema),
        "decision_fail_close_reasons": schema_v2_decision_fail_close_reasons(schema),
        "fail_close_rule_reasons": schema_v2_fail_close_rule_reasons(schema),
        "runtime_surfaces": schema_v2_runtime_surfaces(schema),
        "analysis_scopes": schema_v2_analysis_scopes(schema),
        "required_top_level": schema_v2_required_top_level(schema),
        "required_nested_fields": schema_v2_required_nested_fields(schema),
        "required_surface_fields": schema_v2_required_surface_fields(schema),
        "required_sizing_fields": schema_v2_required_sizing_fields(schema),
        "required_fail_close_rule_fields": schema_v2_required_fail_close_rule_fields(schema),
    }


def first_unsupported_contract_version(data: dict[str, Any]) -> dict[str, str] | None:
    if "schema_version" not in data or "contract_version" not in data:
        return None

    schema_version = data.get("schema_version")
    contract_version = data.get("contract_version")
    if schema_version == CONTRACT_SCHEMA_VERSION and contract_version == CONTRACT_VERSION:
        return None

    path = "schema_version"
    if schema_version == CONTRACT_SCHEMA_VERSION:
        path = "contract_version"
    return error(
        UNSUPPORTED_CONTRACT_VERSION,
        path,
        f"unsupported contract version: {schema_version} / {contract_version}",
    )


def is_exact_v2_contract(data: dict[str, Any]) -> bool:
    return (
        data.get("schema_version") == V2_CONTRACT_SCHEMA_VERSION
        and data.get("contract_version") == V2_CONTRACT_VERSION
    )


def first_missing_required(data: dict[str, Any], facts: dict[str, Any]) -> dict[str, str] | None:
    for field in facts["required_top_level"]:
        if field not in data:
            return error(MISSING_REQUIRED, field, f"missing required field: {field}")

    for block, fields in facts["required_nested_fields"].items():
        value = data.get(block)
        if not isinstance(value, dict):
            return error(MISSING_REQUIRED, block, f"{block} must be an object")
        for field in fields:
            if field not in value:
                path = f"{block}.{field}"
                return error(MISSING_REQUIRED, path, f"missing required field: {path}")

    fail_close_rules = data["policy"].get("fail_close_rules")
    if not isinstance(fail_close_rules, list) or not fail_close_rules:
        return error(
            MISSING_REQUIRED,
            "policy.fail_close_rules",
            "policy.fail_close_rules must be a non-empty array",
        )
    for index, rule in enumerate(fail_close_rules):
        path = f"policy.fail_close_rules[{index}]"
        if not isinstance(rule, dict):
            return error(MISSING_REQUIRED, path, f"{path} must be an object")
        for field in facts["required_fail_close_rule_fields"]:
            if field not in rule:
                return error(MISSING_REQUIRED, f"{path}.{field}", f"missing required field: {path}.{field}")

    return None


def first_v2_missing_required(data: dict[str, Any], facts: dict[str, Any]) -> dict[str, str] | None:
    for field in facts["required_top_level"]:
        if field not in data:
            return error(MISSING_REQUIRED, field, f"missing required field: {field}")

    for block, fields in facts["required_nested_fields"].items():
        value = data.get(block)
        if not isinstance(value, dict):
            return error(MISSING_REQUIRED, block, f"{block} must be an object")
        for field in fields:
            if field not in value:
                path = f"{block}.{field}"
                return error(MISSING_REQUIRED, path, f"missing required field: {path}")

    fail_close_rules = data["policy"].get("fail_close_rules")
    if not isinstance(fail_close_rules, list) or not fail_close_rules:
        return error(
            MISSING_REQUIRED,
            "policy.fail_close_rules",
            "policy.fail_close_rules must be a non-empty array",
        )
    for index, rule in enumerate(fail_close_rules):
        path = f"policy.fail_close_rules[{index}]"
        if not isinstance(rule, dict):
            return error(MISSING_REQUIRED, path, f"{path} must be an object")
        for field in facts["required_fail_close_rule_fields"]:
            if field not in rule:
                return error(MISSING_REQUIRED, f"{path}.{field}", f"missing required field: {path}.{field}")

    surface = data["candidate"].get("surface")
    if not isinstance(surface, dict):
        return error(MISSING_REQUIRED, "candidate.surface", "candidate.surface must be an object")
    for field in facts["required_surface_fields"]:
        if field not in surface:
            path = f"candidate.surface.{field}"
            return error(MISSING_REQUIRED, path, f"missing required field: {path}")

    sizing = data["candidate"].get("sizing")
    if not isinstance(sizing, dict):
        return error(MISSING_REQUIRED, "candidate.sizing", "candidate.sizing must be an object")
    for field in facts["required_sizing_fields"]:
        if field not in sizing:
            path = f"candidate.sizing.{field}"
            return error(MISSING_REQUIRED, path, f"missing required field: {path}")

    return None


def first_invalid_decision_class(data: dict[str, Any], facts: dict[str, Any]) -> dict[str, str] | None:
    for path, value in (
        ("decision.decision_class", data["decision"].get("decision_class")),
        ("trace.decision_class", data["trace"].get("decision_class")),
    ):
        if value not in facts["decision_classes"]:
            return error(
                INVALID_DECISION_CLASS,
                path,
                f"{path} must be one of: {', '.join(sorted(facts['decision_classes']))}",
            )

    for index, rule in enumerate(data["policy"].get("fail_close_rules", [])):
        value = rule.get("decision_class")
        if value not in facts["fail_close_rule_decision_classes"]:
            return error(
                INVALID_DECISION_CLASS,
                f"policy.fail_close_rules[{index}].decision_class",
                "policy.fail_close_rules decision_class must be one of: "
                + ", ".join(sorted(facts["fail_close_rule_decision_classes"])),
            )

    return None


def first_v2_invalid_runtime_surface_scope(data: dict[str, Any], facts: dict[str, Any]) -> dict[str, str] | None:
    surface = data["candidate"]["surface"]
    runtime_surface = surface.get("runtime_surface")
    if runtime_surface not in facts["runtime_surfaces"]:
        return error(
            INVALID_RUNTIME_SURFACE_SCOPE,
            "candidate.surface.runtime_surface",
            "candidate.surface.runtime_surface must be one of: "
            + ", ".join(sorted(facts["runtime_surfaces"])),
        )

    analysis_scope = surface.get("analysis_scope")
    if analysis_scope not in facts["analysis_scopes"]:
        return error(
            INVALID_RUNTIME_SURFACE_SCOPE,
            "candidate.surface.analysis_scope",
            "candidate.surface.analysis_scope must be one of: "
            + ", ".join(sorted(facts["analysis_scopes"])),
        )

    return None


def first_v2_invalid_surface_candidate_envelope(data: dict[str, Any]) -> dict[str, str] | None:
    surface = data["candidate"]["surface"]
    sizing = data["candidate"]["sizing"]
    application = data["application"]

    runtime_surface = surface["runtime_surface"]
    analysis_scope = surface["analysis_scope"]
    analysis_scope_status = surface["analysis_scope_status"]
    requested_size_basis = sizing["requested_size_basis"]
    decision_class = data["decision"].get("decision_class")
    execution_state = application["execution_state"]
    application_status = application["application_status"]

    if runtime_surface == "scan" and requested_size_basis != "unit_scan_slot":
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "candidate.sizing.requested_size_basis",
            "scan runtime_surface requires requested_size_basis = unit_scan_slot",
        )

    if runtime_surface == "backtest" and requested_size_basis != "unit_backtest_run":
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "candidate.sizing.requested_size_basis",
            "backtest runtime_surface requires requested_size_basis = unit_backtest_run",
        )

    if runtime_surface == "paper" and requested_size_basis != "unit_paper_slot_allocation":
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "candidate.sizing.requested_size_basis",
            "paper runtime_surface requires requested_size_basis = unit_paper_slot_allocation",
        )

    if runtime_surface == "backtest" and analysis_scope != "none":
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "candidate.surface.analysis_scope",
            "backtest runtime_surface requires analysis_scope = none",
        )

    if runtime_surface == "paper" and analysis_scope != "none":
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "candidate.surface.analysis_scope",
            "paper runtime_surface requires analysis_scope = none",
        )

    if analysis_scope == "none" and analysis_scope_status != "not_applicable":
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "candidate.surface.analysis_scope_status",
            "analysis_scope = none requires analysis_scope_status = not_applicable",
        )

    if analysis_scope == "wfd_statistical_output" and analysis_scope_status != "metadata_only":
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "candidate.surface.analysis_scope_status",
            "wfd_statistical_output analysis_scope requires analysis_scope_status = metadata_only",
        )

    if analysis_scope == "wfd_statistical_output" and application_status != "metadata_only":
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "application.application_status",
            "wfd_statistical_output analysis_scope requires application_status = metadata_only",
        )

    if application["runtime_sizing_applied"] is True and application_status != "applied":
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "application.runtime_sizing_applied",
            "runtime_sizing_applied requires application_status = applied",
        )

    if (
        runtime_surface == "paper"
        and application_status == "applied"
        and decision_class in {"size", "reject", "kill", "block"}
    ):
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "application.application_status",
            "paper application_status = applied requires decision_class = cap",
        )

    if (
        runtime_surface == "paper"
        and execution_state == "stopped"
        and application["runtime_sizing_applied"] is True
    ):
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "application.runtime_sizing_applied",
            "stopped paper execution cannot claim runtime sizing was applied",
        )

    if application_status == "metadata_only" and application["runtime_sizing_applied"] is not False:
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "application.runtime_sizing_applied",
            "metadata_only application cannot claim runtime sizing was applied",
        )

    if application_status == "metadata_only" and application["metrics_rescaled"] is not False:
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "application.metrics_rescaled",
            "metadata_only application cannot claim metrics were rescaled",
        )

    if application_status == "metadata_only" and application["sizing_effect"] != "none":
        return error(
            INVALID_SURFACE_CANDIDATE_ENVELOPE,
            "application.sizing_effect",
            "metadata_only application requires sizing_effect = none",
        )

    return None


def live_proof_error(path: str, message: str) -> dict[str, str]:
    return error(INVALID_LIVE_CONTRACT_PROOF, path, message)


def has_forbidden_live_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in LIVE_FORBIDDEN_KEY_FRAGMENTS)


def first_forbidden_live_proof_key(value: Any, path: str) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            if has_forbidden_live_key(key):
                return nested_path
            found = first_forbidden_live_proof_key(nested, nested_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = first_forbidden_live_proof_key(nested, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def first_forbidden_live_proof_value(value: Any, path: str) -> str | None:
    if isinstance(value, str):
        lowered = value.lower()
        if any(fragment in lowered for fragment in LIVE_FORBIDDEN_VALUE_FRAGMENTS):
            return path
    elif isinstance(value, dict):
        for key, nested in value.items():
            found = first_forbidden_live_proof_value(nested, f"{path}.{key}")
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = first_forbidden_live_proof_value(nested, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def first_live_runtime_or_broker_claim(
    data: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, str] | None:
    context = data.get("context")
    if not isinstance(context, dict):
        return live_proof_error("context", "context must be an object for live contracts")

    false_claims = (
        ("candidate.surface_payload.live_runtime_claim_allowed", payload.get("live_runtime_claim_allowed")),
        ("candidate.surface_payload.order_mutation_allowed", payload.get("order_mutation_allowed")),
        ("candidate.surface_payload.broker_mutation_attempted", payload.get("broker_mutation_attempted")),
        ("context.live_runtime_claim_allowed", context.get("live_runtime_claim_allowed")),
        ("context.order_mutation_allowed", context.get("order_mutation_allowed")),
        ("context.broker_mutation_attempted", context.get("broker_mutation_attempted")),
    )
    for path, actual in false_claims:
        if actual is not None and actual is not False:
            return live_proof_error(path, f"{path} must remain false for live validator fixtures")

    live_runtime = payload.get("live_runtime")
    if live_runtime is not None and live_runtime != "not_wired_not_claimable":
        return live_proof_error(
            "candidate.surface_payload.live_runtime",
            "candidate.surface_payload.live_runtime must remain 'not_wired_not_claimable'",
        )

    not_approved_claims = (
        ("candidate.surface_payload.runtime_adoption", payload.get("runtime_adoption")),
        ("context.runtime_adoption", context.get("runtime_adoption")),
    )
    for path, actual in not_approved_claims:
        if actual is not None and actual != "not_approved":
            return live_proof_error(path, f"{path} must remain 'not_approved'")

    return None


def first_not_wired_live_payload_claim(payload: dict[str, Any]) -> dict[str, str] | None:
    allowed = set(LIVE_NOT_WIRED_ALLOWED_SURFACE_PAYLOAD_FIELDS)
    for field in payload:
        if field not in allowed:
            return live_proof_error(
                f"candidate.surface_payload.{field}",
                f"unexpected not-wired live payload field: candidate.surface_payload.{field}",
            )
    return None


def is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def is_timestamp_like(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return (
        len(value) >= 20
        and value[4:5] == "-"
        and value[7:8] == "-"
        and "T" in value
        and value.endswith("Z")
    )


def validate_live_proof_block_fields(
    block_name: str,
    block: dict[str, Any],
) -> dict[str, str] | None:
    required = LIVE_REQUIRED_PROOF_FIELDS[block_name]
    allowed = set(required)
    block_path = f"candidate.surface_payload.{block_name}"
    for field in required:
        if field not in block:
            return live_proof_error(
                f"{block_path}.{field}",
                f"missing required live proof field: {block_path}.{field}",
            )
    for field in block:
        if field not in allowed:
            return live_proof_error(
                f"{block_path}.{field}",
                f"unexpected live proof field: {block_path}.{field}",
            )
    return None


def validate_live_positive_bound(
    block: dict[str, Any],
    block_name: str,
    field: str,
) -> dict[str, str] | None:
    path = f"candidate.surface_payload.{block_name}.{field}"
    if not is_positive_number(block.get(field)):
        return live_proof_error(path, f"{path} must be a positive freshness bound")
    return None


def validate_live_timestamp(
    block: dict[str, Any],
    block_name: str,
    field: str,
) -> dict[str, str] | None:
    path = f"candidate.surface_payload.{block_name}.{field}"
    if not is_timestamp_like(block.get(field)):
        return live_proof_error(path, f"{path} must be a timestamp string")
    return None


def validate_live_status(
    block: dict[str, Any],
    block_name: str,
    field: str,
) -> dict[str, str] | None:
    path = f"candidate.surface_payload.{block_name}.{field}"
    if block.get(field) not in LIVE_STATUS_VALUES:
        return live_proof_error(path, f"{path} must be an explicit live proof status")
    return None


def first_v2_invalid_live_contract_proof(data: dict[str, Any]) -> dict[str, str] | None:
    surface = data["candidate"]["surface"]
    sizing = data["candidate"]["sizing"]
    application = data["application"]

    if surface.get("runtime_surface") != "live":
        return None

    surface_status = surface.get("surface_status")
    if surface_status not in {"not_wired", "guarded"}:
        return live_proof_error(
            "candidate.surface.surface_status",
            "live runtime_surface is not approved for implemented runtime claims",
        )

    payload = data["candidate"].get("surface_payload")
    if not isinstance(payload, dict):
        return live_proof_error(
            "candidate.surface_payload",
            "candidate.surface_payload must be an object for live contracts",
        )

    unsafe_payload_path = first_forbidden_live_proof_key(payload, "candidate.surface_payload")
    if unsafe_payload_path is not None:
        return live_proof_error(
            unsafe_payload_path,
            f"forbidden live proof key: {unsafe_payload_path}",
        )

    unsafe_value_path = first_forbidden_live_proof_value(payload, "candidate.surface_payload")
    if unsafe_value_path is not None:
        return live_proof_error(
            unsafe_value_path,
            f"forbidden live proof value: {unsafe_value_path}",
        )

    runtime_claim = first_live_runtime_or_broker_claim(data, payload)
    if runtime_claim is not None:
        return runtime_claim

    if application.get("runtime_sizing_applied") is not False:
        return live_proof_error(
            "application.runtime_sizing_applied",
            "live fixtures cannot claim runtime sizing was applied",
        )

    if application.get("execution_state") != "stopped":
        return live_proof_error(
            "application.execution_state",
            "live fixtures must remain stopped",
        )

    if application.get("sizing_effect") != "not_applicable":
        return live_proof_error(
            "application.sizing_effect",
            "live fixtures cannot claim sizing effects",
        )

    if application.get("metrics_rescaled") is not False:
        return live_proof_error(
            "application.metrics_rescaled",
            "live fixtures cannot claim metrics were rescaled",
        )

    if surface_status == "not_wired":
        not_wired_claim = first_not_wired_live_payload_claim(payload)
        if not_wired_claim is not None:
            return not_wired_claim

        if application.get("application_status") != "not_claimable":
            return live_proof_error(
                "application.application_status",
                "not_wired live fixtures require application_status = not_claimable",
            )
        return None

    if application.get("application_status") != "not_applicable":
        return live_proof_error(
            "application.application_status",
            "guarded live fixtures require application_status = not_applicable",
        )

    requested_size_basis = sizing.get("requested_size_basis")
    if requested_size_basis != LIVE_ORDER_NOTIONAL_BASIS:
        return live_proof_error(
            "candidate.sizing.requested_size_basis",
            "guarded live runtime_surface requires requested_size_basis = "
            f"{LIVE_ORDER_NOTIONAL_BASIS}",
        )

    for field in payload:
        if field not in LIVE_ALLOWED_SURFACE_PAYLOAD_FIELDS:
            return live_proof_error(
                f"candidate.surface_payload.{field}",
                f"unexpected live proof field: candidate.surface_payload.{field}",
            )

    expected_payload_values = (
        ("runtime_adoption", "not_approved"),
        ("order_mutation_allowed", False),
        ("broker_mutation_attempted", False),
    )
    for field, expected_value in expected_payload_values:
        path = f"candidate.surface_payload.{field}"
        if payload.get(field) != expected_value:
            return live_proof_error(path, f"{path} must be {expected_value!r}")

    blocks: dict[str, dict[str, Any]] = {}
    for block_name in LIVE_PROOF_BLOCKS:
        path = f"candidate.surface_payload.{block_name}"
        block = payload.get(block_name)
        if block is None:
            return live_proof_error(path, f"missing required live proof block: {path}")
        if not isinstance(block, dict):
            return live_proof_error(path, f"{path} must be an object")
        blocks[block_name] = block

    for block_name, block in blocks.items():
        invalid_fields = validate_live_proof_block_fields(block_name, block)
        if invalid_fields is not None:
            return invalid_fields

    if blocks["order_intent"].get("order_mutation_allowed") is not False:
        return live_proof_error(
            "candidate.surface_payload.order_intent.order_mutation_allowed",
            "candidate.surface_payload.order_intent.order_mutation_allowed must be false",
        )

    for block_name, field in (
        ("account_proof", "snapshot_max_age_ms"),
        ("market_proof", "market_max_age_ms"),
        ("kill_switch_proof", "proof_max_age_ms"),
        ("idempotency_proof", "proof_max_age_ms"),
    ):
        invalid_bound = validate_live_positive_bound(blocks[block_name], block_name, field)
        if invalid_bound is not None:
            return invalid_bound

    for block_name, field in (
        ("account_proof", "snapshot_ts"),
        ("market_proof", "market_ts"),
        ("kill_switch_proof", "proof_ts"),
        ("idempotency_proof", "proof_ts"),
    ):
        invalid_timestamp = validate_live_timestamp(blocks[block_name], block_name, field)
        if invalid_timestamp is not None:
            return invalid_timestamp

    for field in (
        "global_gate_status",
        "strategy_gate_status",
        "symbol_gate_status",
        "broker_account_gate_status",
        "exposure_gate_status",
        "data_quality_gate_status",
    ):
        invalid_status = validate_live_status(blocks["kill_switch_proof"], "kill_switch_proof", field)
        if invalid_status is not None:
            return invalid_status

    invalid_duplicate_status = validate_live_status(
        blocks["idempotency_proof"],
        "idempotency_proof",
        "duplicate_check_status",
    )
    if invalid_duplicate_status is not None:
        return invalid_duplicate_status

    return None


def first_v2_invalid_decision_class(data: dict[str, Any], facts: dict[str, Any]) -> dict[str, str] | None:
    for path, value in (
        ("decision.decision_class", data["decision"].get("decision_class")),
        ("trace.decision_class", data["trace"].get("decision_class")),
    ):
        if value not in facts["decision_classes"]:
            return error(
                INVALID_DECISION_CLASS,
                path,
                f"{path} must be one of: {', '.join(sorted(facts['decision_classes']))}",
            )

    for index, rule in enumerate(data["policy"].get("fail_close_rules", [])):
        value = rule.get("decision_class")
        if value not in facts["fail_close_rule_decision_classes"]:
            return error(
                INVALID_DECISION_CLASS,
                f"policy.fail_close_rules[{index}].decision_class",
                "policy.fail_close_rules decision_class must be one of: "
                + ", ".join(sorted(facts["fail_close_rule_decision_classes"])),
            )

    return None


def first_invalid_fail_close_reason(data: dict[str, Any], facts: dict[str, Any]) -> dict[str, str] | None:
    decision_reason = data["decision"].get("fail_close_reason")
    if decision_reason not in facts["fail_close_reasons"]:
        return error(
            INVALID_FAIL_CLOSE_REASON,
            "decision.fail_close_reason",
            "decision.fail_close_reason must be in the v1 fail-close reason vocabulary",
        )

    for index, rule in enumerate(data["policy"].get("fail_close_rules", [])):
        reason = rule.get("fail_close_reason")
        if reason not in facts["fail_close_reasons"]:
            return error(
                INVALID_FAIL_CLOSE_REASON,
                f"policy.fail_close_rules[{index}].fail_close_reason",
                "policy.fail_close_rules fail_close_reason must be in the v1 fail-close reason vocabulary",
            )

    return None


def first_v2_invalid_fail_close_reason(data: dict[str, Any], facts: dict[str, Any]) -> dict[str, str] | None:
    decision_reason = data["decision"].get("fail_close_reason")
    if decision_reason not in facts["decision_fail_close_reasons"]:
        return error(
            INVALID_FAIL_CLOSE_REASON,
            "decision.fail_close_reason",
            "decision.fail_close_reason must be in the v2 fail-close reason vocabulary",
        )

    for index, rule in enumerate(data["policy"].get("fail_close_rules", [])):
        reason = rule.get("fail_close_reason")
        if reason not in facts["fail_close_rule_reasons"]:
            return error(
                INVALID_FAIL_CLOSE_REASON,
                f"policy.fail_close_rules[{index}].fail_close_reason",
                "policy.fail_close_rules fail_close_reason must be in the v2 fail-close reason vocabulary",
            )

    return None


def validate_contract(data: dict[str, Any]) -> list[dict[str, str]]:
    unsupported = first_unsupported_contract_version(data)
    if unsupported is not None:
        return [unsupported]

    facts = schema_facts()
    missing = first_missing_required(data, facts)
    if missing is not None:
        return [missing]

    invalid_decision = first_invalid_decision_class(data, facts)
    if invalid_decision is not None:
        return [invalid_decision]

    invalid_reason = first_invalid_fail_close_reason(data, facts)
    if invalid_reason is not None:
        return [invalid_reason]

    if data["decision"].get("decision_class") != data["trace"].get("decision_class"):
        return [
            error(
                DECISION_TRACE_MISMATCH,
                "trace.decision_class",
                "trace.decision_class must match decision.decision_class",
            )
        ]

    return []


def validate_v2_contract(data: dict[str, Any]) -> list[dict[str, str]]:
    facts = schema_v2_facts()
    missing = first_v2_missing_required(data, facts)
    if missing is not None:
        return [missing]

    invalid_runtime_surface = first_v2_invalid_runtime_surface_scope(data, facts)
    if invalid_runtime_surface is not None:
        return [invalid_runtime_surface]

    invalid_live_contract_proof = first_v2_invalid_live_contract_proof(data)
    if invalid_live_contract_proof is not None:
        return [invalid_live_contract_proof]

    invalid_surface_candidate_envelope = first_v2_invalid_surface_candidate_envelope(data)
    if invalid_surface_candidate_envelope is not None:
        return [invalid_surface_candidate_envelope]

    invalid_decision = first_v2_invalid_decision_class(data, facts)
    if invalid_decision is not None:
        return [invalid_decision]

    invalid_reason = first_v2_invalid_fail_close_reason(data, facts)
    if invalid_reason is not None:
        return [invalid_reason]

    if data["decision"].get("decision_class") != data["trace"].get("decision_class"):
        return [
            error(
                DECISION_TRACE_MISMATCH,
                "trace.decision_class",
                "trace.decision_class must match decision.decision_class",
            )
        ]

    return []


def print_result(checked_path: str, errors: list[dict[str, str]]) -> None:
    print(json.dumps(result(checked_path, errors), ensure_ascii=False, sort_keys=False))


def print_v2_result(checked_path: str, data: dict[str, Any], errors: list[dict[str, str]]) -> None:
    print(json.dumps(v2_result(checked_path, data, errors), ensure_ascii=False, sort_keys=False))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_risk_contract.py <contract.json>", file=sys.stderr)
        return 2

    checked_path = argv[1]
    try:
        data = load_contract(Path(checked_path))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if is_exact_v2_contract(data):
        errors = validate_v2_contract(data)
        print_v2_result(checked_path, data, errors)
        return 0 if not errors else 1

    errors = validate_contract(data)
    print_result(checked_path, errors)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
