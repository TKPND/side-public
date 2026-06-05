import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "risk/contracts/v2/risk_contract_validator_result_v2.schema.json"
CONTRACT_SCHEMA_PATH = ROOT / "risk/contracts/v2/risk_contract_v2.schema.json"
VALIDATOR_PATH = ROOT / "scripts/validate_risk_contract.py"

EXPECTED_TOP_LEVEL = (
    "schema_version",
    "checked_path",
    "valid",
    "contract_identity",
    "validated_schema",
    "dispatch",
    "errors",
)
EXPECTED_DISPATCH_STATUSES = (
    "validated",
    "validation_failed",
    "unsupported_contract_version",
    "missing_contract_identity",
)
EXPECTED_ERROR_CODES = (
    "unsupported_contract_version",
    "missing_contract_identity",
    "missing_required_block_or_field",
    "invalid_decision_class",
    "invalid_fail_close_reason",
    "decision_trace_mismatch",
    "invalid_runtime_surface_scope",
    "invalid_surface_candidate_envelope",
    "invalid_live_contract_proof",
)


def load_schema() -> dict:
    assert SCHEMA_PATH.exists(), f"missing v2 validator result schema file: {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_result_schema_declares_distinct_identity_and_required_envelope() -> None:
    schema = load_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "risk_contract_validator_result_v2.schema.json"
    assert schema["title"] == "Side Risk Contract Validator Result v2"
    assert schema["description"].endswith(
        "Validator acceptance boundary only; runtime acceptance remains out of scope."
    )
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert tuple(schema["required"]) == EXPECTED_TOP_LEVEL

    properties = schema["properties"]
    assert properties["schema_version"] == {"const": "risk_contract_validator_result.v2"}
    assert properties["checked_path"] == {"type": "string", "minLength": 1}
    assert properties["valid"] == {"type": "boolean"}
    assert properties["contract_identity"] == {"$ref": "#/$defs/contract_identity"}
    assert properties["validated_schema"] == {
        "anyOf": [{"$ref": "#/$defs/validated_schema"}, {"type": "null"}]
    }
    assert properties["dispatch"] == {"$ref": "#/$defs/dispatch"}
    assert properties["errors"] == {
        "type": "array",
        "items": {"$ref": "#/$defs/error"},
    }


def test_result_schema_defines_contract_identity_dispatch_and_validated_schema() -> None:
    schema = load_schema()
    defs = schema["$defs"]
    identity = defs["contract_identity"]
    validated_schema = defs["validated_schema"]
    dispatch = defs["dispatch"]

    assert identity["type"] == "object"
    assert identity["additionalProperties"] is False
    assert tuple(identity["required"]) == ("schema_version", "contract_version")
    assert identity["properties"]["schema_version"] == {"type": ["string", "null"]}
    assert identity["properties"]["contract_version"] == {"type": ["string", "null"]}

    assert validated_schema["type"] == "object"
    assert validated_schema["additionalProperties"] is False
    assert tuple(validated_schema["required"]) == (
        "schema_version",
        "contract_version",
        "path",
    )
    assert validated_schema["properties"]["schema_version"] == {"const": "risk_contract.v2"}
    assert validated_schema["properties"]["contract_version"] == {"const": "v2"}
    assert validated_schema["properties"]["path"] == {
        "const": "risk/contracts/v2/risk_contract_v2.schema.json"
    }
    assert validated_schema["properties"]["path"] != {
        "const": "risk/contracts/v1/risk_contract_v1.schema.json"
    }
    assert CONTRACT_SCHEMA_PATH.exists()

    assert dispatch["type"] == "object"
    assert dispatch["additionalProperties"] is False
    assert tuple(dispatch["required"]) == ("status", "reason")
    assert tuple(dispatch["properties"]["status"]["enum"]) == EXPECTED_DISPATCH_STATUSES
    assert dispatch["properties"]["reason"] == {"type": ["string", "null"]}


def test_result_schema_defines_deterministic_error_shape_and_required_codes() -> None:
    schema = load_schema()
    defs = schema["$defs"]
    error = defs["error"]

    assert tuple(defs["validator_error_code"]["enum"]) == EXPECTED_ERROR_CODES
    assert error["type"] == "object"
    assert error["additionalProperties"] is False
    assert tuple(error["required"]) == ("code", "path", "message")
    assert error["properties"]["code"] == {"$ref": "#/$defs/validator_error_code"}
    assert error["properties"]["path"] == {"type": "string"}
    assert error["properties"]["message"] == {"type": "string", "minLength": 1}


def test_result_schema_file_is_used_by_validator_acceptance_boundary() -> None:
    schema = load_schema()
    validator_source = VALIDATOR_PATH.read_text(encoding="utf-8")

    assert schema["description"].endswith(
        "Validator acceptance boundary only; runtime acceptance remains out of scope."
    )
    assert "risk_contract_validator_result.v1" in validator_source
    assert "risk_contract_validator_result.v2" in validator_source
    assert "risk_contract_validator_result_v2.schema.json" in validator_source
