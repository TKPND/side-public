"""Contract guards for side.live_preflight.result.v1 docs examples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.generate_risk_contract_v2_adoption_closure_audit import (
    LIVE_RUNTIME_IMPLEMENTATION_PATHS,
    LIVE_RUNTIME_SURFACE_SOURCE,
    live_runtime_surface_absent_check,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DOC = ROOT / "docs/contracts/live_preflight_result_v1.md"
SCHEMA_PATH = ROOT / "docs/contracts/live_preflight_result_v1.schema.json"
EXAMPLE_ROOT = ROOT / "docs/examples/live_preflight/result_v1"
VALID_DIR = EXAMPLE_ROOT / "valid"
INVALID_DIR = EXAMPLE_ROOT / "invalid"

VALID_FILENAMES = {
    "passed_no_order_preflight.json",
    "risk_stopped_kill_switch.json",
    "failed_account_proof_stale.json",
    "failed_duplicate_idempotency.json",
}
INVALID_FILENAMES = {
    "invalid_order_mutation_attempted.json",
    "invalid_broker_mutation_attempted.json",
    "invalid_unsafe_raw_material.json",
    "invalid_protected_output_root_persisted.json",
}

TOP_LEVEL_KEYS = (
    "schema_version",
    "artifact_kind",
    "execution_mode",
    "result",
    "risk_gate",
    "live_preflight",
    "emission",
)
RISK_GATE_KEYS = (
    "schema_version",
    "contract_version",
    "validator_result_schema_version",
    "schema_ref",
    "validated_schema_ref",
    "validator",
)
LIVE_PREFLIGHT_KEYS = (
    "order_mutation_allowed",
    "order_mutation_attempted",
    "broker_mutation_attempted",
    "account_proof",
    "market_proof",
    "order_intent",
    "kill_switch_proof",
    "idempotency_proof",
)
PROOF_BLOCKS = {
    "account_proof",
    "market_proof",
    "order_intent",
    "kill_switch_proof",
    "idempotency_proof",
}
FORBIDDEN_PUBLIC_FRAGMENTS = (
    "raw_account_id",
    "account_id",
    "credential",
    "token",
    "secret",
    "private_key",
    "cookie",
    "endpoint",
    "broker_secret",
    "raw_idempotency_key",
)

EXPECTED_VALID_RESULTS = {
    "passed_no_order_preflight.json": {
        "status": "passed",
        "failure_class": None,
        "failure_reason": None,
        "terminal_gate": None,
        "persisted": True,
    },
    "risk_stopped_kill_switch.json": {
        "status": "risk_stopped",
        "failure_class": "risk_decision",
        "failure_reason": "kill_switch_active",
        "terminal_gate": "risk_decision",
        "persisted": True,
    },
    "failed_account_proof_stale.json": {
        "status": "failed",
        "failure_class": "stale_proof",
        "failure_reason": "account_proof_stale",
        "terminal_gate": "account_proof",
        "persisted": True,
    },
    "failed_duplicate_idempotency.json": {
        "status": "failed",
        "failure_class": "duplicate_idempotency",
        "failure_reason": "idempotency_replay_detected",
        "terminal_gate": "idempotency",
        "persisted": True,
    },
}

EXPECTED_INVALID_VIOLATIONS = {
    "invalid_order_mutation_attempted.json": "order_mutation_attempted",
    "invalid_broker_mutation_attempted.json": "broker_mutation_attempted",
    "invalid_unsafe_raw_material.json": "unsafe_raw_material",
    "invalid_protected_output_root_persisted.json": "protected_output_root_persisted",
}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} must be a JSON object"
    return data


def walk_keys_and_string_values(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else key
            found.append((path, key))
            found.extend(walk_keys_and_string_values(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(walk_keys_and_string_values(nested, f"{prefix}[{index}]"))
    elif isinstance(value, str):
        found.append((prefix, value))
    return found


def forbidden_public_material_paths(value: Any) -> list[str]:
    violations = []
    for path, text in walk_keys_and_string_values(value):
        lowered = text.lower()
        if any(fragment in lowered for fragment in FORBIDDEN_PUBLIC_FRAGMENTS):
            violations.append(path)
    return violations


def valid_example_paths() -> list[Path]:
    return sorted(VALID_DIR.glob("*.json"))


def invalid_example_paths() -> list[Path]:
    return sorted(INVALID_DIR.glob("*.json"))


def assert_valid_artifact_shape(doc: dict[str, Any]) -> None:
    assert tuple(doc) == TOP_LEVEL_KEYS
    assert doc["schema_version"] == "side.live_preflight.result.v1"
    assert doc["artifact_kind"] == "live_preflight_result"
    assert doc["execution_mode"] == "no_order_preflight"

    assert tuple(doc["risk_gate"]) == RISK_GATE_KEYS
    assert doc["risk_gate"] == {
        "schema_version": "risk_contract.v2",
        "contract_version": "v2",
        "validator_result_schema_version": "risk_contract_validator_result.v2",
        "schema_ref": "risk/contracts/v2/risk_contract_v2.schema.json",
        "validated_schema_ref": "risk/contracts/v2/risk_contract_v2.schema.json",
        "validator": "scripts/validate_risk_contract.py",
    }

    preflight = doc["live_preflight"]
    assert tuple(preflight) == LIVE_PREFLIGHT_KEYS
    assert preflight["order_mutation_allowed"] is False
    assert preflight["order_mutation_attempted"] is False
    assert preflight["broker_mutation_attempted"] is False
    assert PROOF_BLOCKS <= preflight.keys()
    assert doc["emission"]["protected_output_root"] is False


def public_artifact_violations(doc: dict[str, Any]) -> set[str]:
    violations: set[str] = set()
    preflight = doc["live_preflight"]
    emission = doc["emission"]
    result = doc["result"]

    if preflight["order_mutation_attempted"] is not False:
        violations.add("order_mutation_attempted")
    if preflight["broker_mutation_attempted"] is not False:
        violations.add("broker_mutation_attempted")
    if forbidden_public_material_paths(preflight):
        violations.add("unsafe_raw_material")
    if emission["protected_output_root"] is True and emission["persisted"] is True:
        violations.add("protected_output_root_persisted")
    if result["failure_class"] in {
        "unsafe_material",
        "mutation_attempt",
        "protected_output_root",
    } and emission["persisted"] is True:
        violations.add("dangerous_failure_persisted")

    return violations


def test_contract_doc_declares_frozen_contract_and_non_goals() -> None:
    text = CONTRACT_DOC.read_text(encoding="utf-8")

    assert "schema_version = side.live_preflight.result.v1" in text
    assert "artifact_kind = live_preflight_result" in text
    assert "execution_mode = no_order_preflight" in text
    for field in TOP_LEVEL_KEYS:
        assert f"`{field}`" in text
    for field in RISK_GATE_KEYS:
        assert f"`{field}`" in text
    assert "docs/contracts/live_preflight_result_v1.schema.json" in text
    assert "validation scope is docs/examples/tests only" in text
    assert "JSON Schema for this artifact is intentionally deferred" not in text
    assert "live CLI/runtime wiring" in text
    assert "account fetchers" in text
    assert "credential/network paths" in text
    assert "broker order paths" in text
    assert "runtime public emission" in text


def test_example_directories_contain_exact_matrix() -> None:
    assert {path.name for path in valid_example_paths()} == VALID_FILENAMES
    assert {path.name for path in invalid_example_paths()} == INVALID_FILENAMES


def test_valid_examples_match_frozen_shape_and_are_sanitized() -> None:
    for path in valid_example_paths():
        doc = load_json(path)
        assert_valid_artifact_shape(doc)
        assert forbidden_public_material_paths(doc) == []
        assert public_artifact_violations(doc) == set()


def test_valid_examples_pin_result_and_emission_matrix() -> None:
    for path in valid_example_paths():
        doc = load_json(path)
        expected = EXPECTED_VALID_RESULTS[path.name]
        assert doc["result"] == {
            "status": expected["status"],
            "failure_class": expected["failure_class"],
            "failure_reason": expected["failure_reason"],
            "terminal_gate": expected["terminal_gate"],
        }
        assert doc["emission"]["persisted"] is expected["persisted"]


def test_invalid_examples_fail_for_their_intended_reason() -> None:
    for path in invalid_example_paths():
        doc = load_json(path)
        expected_violation = EXPECTED_INVALID_VIOLATIONS[path.name]
        violations = public_artifact_violations(doc)

        assert expected_violation in violations
        assert violations, f"{path.name} must not be accepted as a valid public artifact"

    unsafe = load_json(INVALID_DIR / "invalid_unsafe_raw_material.json")
    unsafe_paths = forbidden_public_material_paths(unsafe["live_preflight"])
    assert unsafe_paths == ["account_proof.raw_account_id"]


def test_dangerous_invalid_examples_are_not_public_emitted_artifacts() -> None:
    for path in invalid_example_paths():
        doc = load_json(path)
        failure_class = doc["result"]["failure_class"]
        if failure_class in {"unsafe_material", "mutation_attempt", "protected_output_root"}:
            assert public_artifact_violations(doc)
            if path.name != "invalid_protected_output_root_persisted.json":
                assert doc["emission"]["persisted"] is False


def test_live_preflight_result_schema_is_present_only_at_docs_contract_path() -> None:
    schema_name_matches = []
    for base in (ROOT / "risk/contracts", ROOT / "docs/contracts", ROOT / "docs/examples"):
        for pattern in ("*live_preflight_result*.schema.json", "*live_preflight*result*.schema.json"):
            schema_name_matches.extend(base.rglob(pattern))

    assert sorted(set(schema_name_matches)) == [SCHEMA_PATH]

    schema_claims = []
    for base in (ROOT / "risk/contracts", ROOT / "docs/contracts", ROOT / "docs/examples"):
        for path in base.rglob("*.json"):
            if EXAMPLE_ROOT in path.parents:
                continue
            try:
                data = load_json(path)
            except json.JSONDecodeError:
                continue
            claims_live_preflight = (
                data.get("$id") == "side.live_preflight.result.v1"
                or data.get("schema_version") == "side.live_preflight.result.v1"
            )
            looks_like_schema = any(key in data for key in ("$schema", "type", "properties"))
            if claims_live_preflight and looks_like_schema:
                schema_claims.append(path)

    assert sorted(set(schema_claims)) == [SCHEMA_PATH]


def test_live_runtime_and_broker_paths_remain_absent() -> None:
    assert LIVE_RUNTIME_SURFACE_SOURCE.as_posix() == "rust/side-cli/src/main.rs"
    assert tuple(path.as_posix() for path in LIVE_RUNTIME_IMPLEMENTATION_PATHS) == (
        "rust/side-cli/src/cmd/live.rs",
        "rust/side-cli/src/cmd/live",
        "rust/side-cli/src/cmd/broker.rs",
        "rust/side-cli/src/cmd/broker",
        "rust/side-engine/src/live.rs",
        "rust/side-engine/src/live",
        "rust/side-engine/src/broker.rs",
        "rust/side-engine/src/broker",
    )
    assert live_runtime_surface_absent_check()["passed"] is True

    forbidden_matches = []
    for pattern in (
        "rust/side-cli/src/cmd/live*",
        "rust/side-cli/src/cmd/broker*",
        "rust/side-engine/src/live*",
        "rust/side-engine/src/broker*",
    ):
        forbidden_matches.extend(ROOT.glob(pattern))

    assert forbidden_matches == []
