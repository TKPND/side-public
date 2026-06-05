"""JSON Schema guards for side.live_preflight.result.v1 examples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs/contracts/live_preflight_result_v1.schema.json"
EXAMPLE_ROOT = ROOT / "docs/examples/live_preflight/result_v1"
VALID_DIR = EXAMPLE_ROOT / "valid"
INVALID_DIR = EXAMPLE_ROOT / "invalid"
RISK_CONTRACT_V2_SCHEMA_PATH = ROOT / "risk/contracts/v2/risk_contract_v2.schema.json"

VALID_FILENAMES = {
    "failed_account_proof_stale.json",
    "failed_duplicate_idempotency.json",
    "passed_no_order_preflight.json",
    "risk_stopped_kill_switch.json",
}
INVALID_FILENAMES = {
    "invalid_broker_mutation_attempted.json",
    "invalid_order_mutation_attempted.json",
    "invalid_protected_output_root_persisted.json",
    "invalid_unsafe_raw_material.json",
}
TOP_LEVEL_REQUIRED = (
    "schema_version",
    "artifact_kind",
    "execution_mode",
    "result",
    "risk_gate",
    "live_preflight",
    "emission",
)
NESTED_REQUIRED = {
    "result": ("status", "failure_class", "failure_reason", "terminal_gate"),
    "risk_gate": (
        "schema_version",
        "contract_version",
        "validator_result_schema_version",
        "schema_ref",
        "validated_schema_ref",
        "validator",
    ),
    "live_preflight": (
        "order_mutation_allowed",
        "order_mutation_attempted",
        "broker_mutation_attempted",
        "account_proof",
        "market_proof",
        "order_intent",
        "kill_switch_proof",
        "idempotency_proof",
    ),
    "emission": ("persisted", "protected_output_root"),
}
EXPECTED_INVALID_SIGNATURES = {
    "invalid_order_mutation_attempted.json": {
        "path": ("live_preflight", "order_mutation_attempted"),
        "validator": "const",
        "reason": "const:false",
    },
    "invalid_broker_mutation_attempted.json": {
        "path": ("live_preflight", "broker_mutation_attempted"),
        "validator": "const",
        "reason": "const:false",
    },
    "invalid_unsafe_raw_material.json": {
        "path": ("live_preflight", "account_proof", "raw_account_id"),
        "validator": "additionalProperties",
        "reason": "additional_property:raw_account_id",
    },
    "invalid_protected_output_root_persisted.json": {
        "path": ("emission", "protected_output_root"),
        "validator": "const",
        "reason": "const:false",
    },
}
RISK_V2_ONLY_TOP_LEVEL_BLOCKS = {
    "candidate",
    "evidence",
    "decision",
    "application",
    "trace",
}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} must be a JSON object"
    return data


def valid_example_paths() -> list[Path]:
    return sorted(VALID_DIR.glob("*.json"))


def invalid_example_paths() -> list[Path]:
    return sorted(INVALID_DIR.glob("*.json"))


def walk_json(value: object) -> list[object]:
    if isinstance(value, dict):
        nested: list[object] = [value]
        for child in value.values():
            nested.extend(walk_json(child))
        return nested
    if isinstance(value, list):
        nested = [value]
        for child in value:
            nested.extend(walk_json(child))
        return nested
    return [value]


def walk_schema_objects(value: object) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("type") == "object":
            objects.append(value)
        for child in value.values():
            objects.extend(walk_schema_objects(child))
    elif isinstance(value, list):
        for child in value:
            objects.extend(walk_schema_objects(child))
    return objects


def validation_signature(error: ValidationError) -> dict[str, object]:
    path = tuple(str(part) for part in error.path)
    reason = error.validator

    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        allowed = set(error.schema.get("properties", {}))
        extras = sorted(set(error.instance) - allowed)
        if extras:
            extra = extras[0]
            path = (*path, extra)
            reason = f"additional_property:{extra}"
    elif error.validator == "const":
        reason = f"const:{str(error.validator_value).lower()}"

    return {
        "path": path,
        "validator": error.validator,
        "reason": reason,
    }


def sorted_signatures(errors: list[ValidationError]) -> list[dict[str, object]]:
    return sorted(
        (validation_signature(error) for error in errors),
        key=lambda signature: (
            tuple(signature["path"]),
            str(signature["validator"]),
            str(signature["reason"]),
        ),
    )


def schema_validator() -> Draft202012Validator:
    schema = load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def assert_schema_has_no_risk_contract_v2_coupling() -> None:
    schema = load_json(SCHEMA_PATH)
    refs = [
        node["$ref"]
        for node in walk_json(schema)
        if isinstance(node, dict) and isinstance(node.get("$ref"), str)
    ]

    assert "risk/contracts/v2/risk_contract_v2.schema.json" not in refs
    assert "risk_contract_v2.schema.json" not in refs
    assert RISK_CONTRACT_V2_SCHEMA_PATH.exists()
    assert RISK_V2_ONLY_TOP_LEVEL_BLOCKS.isdisjoint(schema["properties"])
    assert RISK_V2_ONLY_TOP_LEVEL_BLOCKS.isdisjoint(schema["$defs"])


def test_schema_self_check() -> None:
    Draft202012Validator.check_schema(load_json(SCHEMA_PATH))


def test_schema_pins_frozen_public_contract_shape() -> None:
    schema = load_json(SCHEMA_PATH)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "side.live_preflight.result.v1"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert tuple(schema["required"]) == TOP_LEVEL_REQUIRED
    assert schema["properties"]["schema_version"] == {
        "const": "side.live_preflight.result.v1"
    }
    assert schema["properties"]["artifact_kind"] == {"const": "live_preflight_result"}
    assert schema["properties"]["execution_mode"] == {"const": "no_order_preflight"}

    defs = schema["$defs"]
    for name, expected_required in NESTED_REQUIRED.items():
        nested = defs[name]
        assert nested["type"] == "object"
        assert nested["additionalProperties"] is False
        assert tuple(nested["required"]) == expected_required

    object_schemas = walk_schema_objects(schema)
    assert object_schemas
    for object_schema in object_schemas:
        assert object_schema["additionalProperties"] is False


def test_valid_examples_validate_against_schema() -> None:
    assert {path.name for path in valid_example_paths()} == VALID_FILENAMES
    validator = schema_validator()

    for path in valid_example_paths():
        errors = list(validator.iter_errors(load_json(path)))
        assert errors == [], f"{path.name}: {sorted_signatures(errors)}"


def test_invalid_examples_fail_for_intended_schema_reason() -> None:
    assert {path.name for path in invalid_example_paths()} == INVALID_FILENAMES
    validator = schema_validator()

    for path in invalid_example_paths():
        signatures = sorted_signatures(list(validator.iter_errors(load_json(path))))
        expected = EXPECTED_INVALID_SIGNATURES[path.name]

        assert expected in signatures, f"{path.name}: {signatures}"


def test_schema_has_no_direct_risk_contract_v2_ref_or_embedded_copy() -> None:
    assert_schema_has_no_risk_contract_v2_coupling()


def test_live_preflight_schema_does_not_ref_risk_contract_v2() -> None:
    assert_schema_has_no_risk_contract_v2_coupling()


def test_schema_examples_are_loaded_from_canonical_corpus() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    inline_payload_fragment = '"artifact_kind": ' + '"live_preflight_result"'
    inline_fixture_alias = "acct-alias-" + "primary"

    assert "docs/examples/live_preflight/result_v1" in source
    assert {path.parent for path in valid_example_paths()} == {VALID_DIR}
    assert {path.parent for path in invalid_example_paths()} == {INVALID_DIR}
    assert inline_payload_fragment not in source
    assert inline_fixture_alias not in source
