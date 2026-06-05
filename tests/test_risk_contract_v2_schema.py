import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "risk/contracts/v2/risk_contract_v2.schema.json"

EXPECTED_TOP_LEVEL = (
    "schema_version",
    "contract_version",
    "policy",
    "candidate",
    "evidence",
    "context",
    "decision",
    "application",
    "trace",
)
EXPECTED_DECISION_CLASSES = ("size", "cap", "reject", "kill", "block")
EXPECTED_FAIL_CLOSE_REASONS_V2 = (
    "not_fail_closed",
    "missing_required_policy_field",
    "stale_evidence",
    "policy_evidence_contradiction",
    "evidence_acquisition_failure",
    "absent_source_proof",
    "malformed_policy",
    "candidate_validation_failure",
)


def load_schema() -> dict:
    assert SCHEMA_PATH.exists(), f"missing v2 schema file: {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_v2_schema_declares_distinct_identity_and_required_top_level_blocks() -> None:
    schema = load_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "risk_contract_v2.schema.json"
    assert schema["title"] == "Side Risk Contract v2"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert tuple(schema["required"]) == EXPECTED_TOP_LEVEL
    assert schema["properties"]["schema_version"] == {"const": "risk_contract.v2"}
    assert schema["properties"]["contract_version"] == {"const": "v2"}


def test_v2_schema_defines_candidate_surface_and_sizing_envelope() -> None:
    schema = load_schema()
    candidate = schema["$defs"]["candidate"]
    surface = schema["$defs"]["surface"]
    sizing = schema["$defs"]["sizing"]

    assert candidate["additionalProperties"] is False
    assert tuple(candidate["required"]) == (
        "candidate_schema_version",
        "candidate_id",
        "strategy_id",
        "symbol_or_universe",
        "timeframe",
        "validation_refs",
        "surface",
        "sizing",
        "surface_payload",
    )
    assert candidate["properties"]["candidate_schema_version"] == {
        "const": "risk_contract.v2.candidate.v1"
    }
    assert candidate["properties"]["surface"] == {"$ref": "#/$defs/surface"}
    assert candidate["properties"]["sizing"] == {"$ref": "#/$defs/sizing"}

    assert tuple(surface["required"]) == (
        "runtime_surface",
        "surface_status",
        "analysis_scope",
        "analysis_scope_status",
    )
    assert tuple(surface["properties"]["runtime_surface"]["enum"]) == (
        "backtest",
        "paper",
        "scan",
        "live",
    )
    assert "wfd_statistical_output" not in surface["properties"]["runtime_surface"]["enum"]
    assert tuple(surface["properties"]["surface_status"]["enum"]) == (
        "implemented",
        "guarded",
        "not_wired",
        "not_claimable",
    )
    assert tuple(surface["properties"]["analysis_scope"]["enum"]) == (
        "none",
        "wfd_statistical_output",
    )
    assert tuple(surface["properties"]["analysis_scope_status"]["enum"]) == (
        "not_applicable",
        "metadata_only",
        "metrics_rescaled",
    )

    assert sizing["additionalProperties"] is False
    assert tuple(sizing["required"]) == ("requested_size", "requested_size_basis")
    assert sizing["properties"]["requested_size"] == {
        "type": "number",
        "exclusiveMinimum": 0,
    }
    assert tuple(sizing["properties"]["requested_size_basis"]["enum"]) == (
        "unit_backtest_run",
        "unit_scan_slot",
        "unit_paper_slot_allocation",
        "unit_live_order_notional",
    )


def test_v2_schema_defines_decision_application_and_trace_contracts() -> None:
    schema = load_schema()
    decision = schema["$defs"]["decision"]
    application = schema["$defs"]["application"]
    trace = schema["$defs"]["trace"]

    assert tuple(schema["$defs"]["decision_class"]["enum"]) == EXPECTED_DECISION_CLASSES
    assert tuple(schema["$defs"]["fail_close_reason_v2"]["enum"]) == EXPECTED_FAIL_CLOSE_REASONS_V2
    assert "insufficient_validation_power" not in schema["$defs"]["fail_close_reason_v2"]["enum"]
    assert "unsupported_contract_version" not in schema["$defs"]["fail_close_reason_v2"]["enum"]

    assert tuple(decision["required"]) == (
        "decision_class",
        "allowed_size",
        "binding_rule",
        "supporting_rules",
        "fail_close_reason",
        "evidence_refs",
        "policy_version",
    )
    assert decision["properties"]["decision_class"] == {"$ref": "#/$defs/decision_class"}
    assert decision["properties"]["fail_close_reason"] == {
        "$ref": "#/$defs/fail_close_reason_v2"
    }

    assert tuple(application["required"]) == (
        "execution_state",
        "application_status",
        "runtime_sizing_applied",
        "sizing_effect",
        "metrics_rescaled",
    )
    assert tuple(application["properties"]["execution_state"]["enum"]) == (
        "continued",
        "stopped",
        "gate_error",
    )
    assert tuple(application["properties"]["application_status"]["enum"]) == (
        "not_applicable",
        "deferred",
        "applied",
        "not_claimable",
        "metadata_only",
    )
    assert application["properties"]["runtime_sizing_applied"] == {"type": "boolean"}
    assert tuple(application["properties"]["sizing_effect"]["enum"]) == (
        "none",
        "reduced",
        "clamped",
        "rejected",
        "not_applicable",
    )
    assert application["properties"]["metrics_rescaled"] == {"type": "boolean"}

    assert tuple(trace["required"]) == (
        "policy_version",
        "candidate_id",
        "input_evidence_refs",
        "binding_rule",
        "decision_class",
        "emitted_artifact_path",
        "validated_schema_version",
        "validator_result_schema_version",
    )
    assert trace["properties"]["decision_class"] == {"$ref": "#/$defs/decision_class"}
    assert trace["properties"]["validated_schema_version"] == {"const": "risk_contract.v2"}
    assert trace["properties"]["validator_result_schema_version"] == {
        "const": "risk_contract_validator_result.v2"
    }


def test_v2_schema_keeps_runtime_acceptance_out_of_scope_after_validator_acceptance() -> None:
    schema = load_schema()

    assert schema["description"].endswith(
        "Validator acceptance boundary only; runtime acceptance remains out of scope."
    )
