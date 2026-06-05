import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import validate_risk_contract as validator


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_risk_contract.py"
MATRIX = ROOT / "risk" / "contracts" / "v1" / "fixture_matrix.json"
V2_MATRIX = ROOT / "risk" / "contracts" / "v2" / "fixture_matrix.json"
RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
V2_RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v2"
V2_VALIDATED_SCHEMA = {
    "schema_version": "risk_contract.v2",
    "contract_version": "v2",
    "path": "risk/contracts/v2/risk_contract_v2.schema.json",
}
UNSUPPORTED_CONTRACT_VERSION = "unsupported_contract_version"
INVALID_SURFACE_CANDIDATE_ENVELOPE = "invalid_surface_candidate_envelope"
INVALID_LIVE_CONTRACT_PROOF = "invalid_live_contract_proof"
REQUIRED_ERROR_CODES = {
    "missing_required_block_or_field",
    "invalid_decision_class",
    "invalid_fail_close_reason",
    "decision_trace_mismatch",
}
LIVE_ORDER_NOTIONAL_CLAIMABLE_FIXTURE = (
    "risk/contracts/v2/fixtures/valid/live_order_notional_claimable_valid.json"
)
LIVE_NOT_CLAIMABLE_FIXTURE = (
    "risk/contracts/v2/fixtures/valid/live_not_claimable_valid.json"
)
INVALID_LIVE_MISSING_ACCOUNT_PROOF_FIXTURE = (
    "risk/contracts/v2/fixtures/invalid/invalid_live_missing_account_proof.json"
)
INVALID_LIVE_UNSAFE_SECRET_FIELD_FIXTURE = (
    "risk/contracts/v2/fixtures/invalid/invalid_live_unsafe_secret_field.json"
)
EXPECTED_SCHEMA_FACTS = {
    "decision_classes": ("block", "cap", "kill", "reject", "size"),
    "fail_close_rule_decision_classes": ("block", "kill"),
    "fail_close_reasons": (
        "absent_source_proof",
        "candidate_validation_failure",
        "evidence_acquisition_failure",
        "insufficient_validation_power",
        "malformed_policy",
        "missing_required_policy_field",
        "policy_evidence_contradiction",
        "stale_evidence",
    ),
    "required_top_level": (
        "schema_version",
        "contract_version",
        "policy",
        "candidate",
        "evidence",
        "context",
        "decision",
        "trace",
    ),
    "required_nested_fields": {
        "policy": (
            "version",
            "owner",
            "effective_from",
            "required_fields",
            "fail_close_rules",
        ),
        "candidate": (
            "strategy_id",
            "symbol_or_universe",
            "timeframe",
            "validation_refs",
        ),
        "evidence": ("refs",),
        "decision": (
            "decision_class",
            "allowed_size",
            "binding_rule",
            "supporting_rules",
            "fail_close_reason",
            "evidence_refs",
            "policy_version",
        ),
        "trace": (
            "policy_version",
            "candidate_id",
            "input_evidence_refs",
            "binding_rule",
            "decision_class",
            "emitted_artifact_path",
        ),
    },
    "required_fail_close_rule_fields": (
        "condition",
        "decision_class",
        "fail_close_reason",
    ),
}


def load_matrix() -> list[dict]:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    return matrix["fixtures"]


def load_v2_matrix() -> list[dict]:
    matrix = json.loads(V2_MATRIX.read_text(encoding="utf-8"))
    return matrix["fixtures"]


def run_validator(contract_path: str):
    return subprocess.run(
        [sys.executable, str(VALIDATOR), contract_path],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def base_valid_contract() -> dict:
    return json.loads((ROOT / "risk/contracts/v1/fixtures/valid/base_valid.json").read_text(encoding="utf-8"))


def base_v2_contract() -> dict:
    return json.loads((ROOT / "risk/contracts/v2/fixtures/valid/base_valid.json").read_text(encoding="utf-8"))


def scan_metadata_v2_contract() -> dict:
    return json.loads((ROOT / "risk/contracts/v2/fixtures/valid/scan_metadata_valid.json").read_text(encoding="utf-8"))


def paper_cap_applied_v2_contract() -> dict:
    return json.loads(
        (ROOT / "risk/contracts/v2/fixtures/valid/paper_cap_applied_valid.json").read_text(
            encoding="utf-8"
        )
    )


def live_order_notional_claimable_v2_contract() -> dict:
    return json.loads((ROOT / LIVE_ORDER_NOTIONAL_CLAIMABLE_FIXTURE).read_text(encoding="utf-8"))


def live_not_claimable_v2_contract() -> dict:
    return json.loads((ROOT / LIVE_NOT_CLAIMABLE_FIXTURE).read_text(encoding="utf-8"))


def assert_v2_result_envelope(
    payload: dict,
    *,
    checked_path: str,
    valid: bool,
    dispatch_status: str,
    dispatch_reason: str | None,
) -> None:
    assert payload["schema_version"] == V2_RESULT_SCHEMA_VERSION
    assert payload["checked_path"] == checked_path
    assert payload["valid"] is valid
    assert payload["contract_identity"] == {
        "schema_version": "risk_contract.v2",
        "contract_version": "v2",
    }
    assert payload["validated_schema"] == V2_VALIDATED_SCHEMA
    assert payload["dispatch"] == {
        "status": dispatch_status,
        "reason": dispatch_reason,
    }


def normalized_schema_facts(schema: dict) -> dict:
    facts = validator.schema_facts(schema)
    return {
        "decision_classes": tuple(sorted(facts["decision_classes"])),
        "fail_close_rule_decision_classes": tuple(
            sorted(facts["fail_close_rule_decision_classes"])
        ),
        "fail_close_reasons": tuple(sorted(facts["fail_close_reasons"])),
        "required_top_level": tuple(facts["required_top_level"]),
        "required_nested_fields": {
            key: tuple(value)
            for key, value in facts["required_nested_fields"].items()
        },
        "required_fail_close_rule_fields": tuple(
            facts["required_fail_close_rule_fields"]
        ),
    }


def assert_schema_facts_match_snapshot(schema: dict) -> None:
    observed = normalized_schema_facts(schema)
    for key, expected in EXPECTED_SCHEMA_FACTS.items():
        assert observed[key] == expected, f"schema fact drift in {key}"


def add_decision_class(schema: dict) -> None:
    schema["properties"]["decision"]["properties"]["decision_class"]["enum"].append(
        "halt"
    )
    schema["properties"]["trace"]["properties"]["decision_class"]["enum"].append("halt")


def add_fail_close_reason(schema: dict) -> None:
    schema["$defs"]["fail_close_reason"]["enum"].append("provider_timeout")


def add_top_level_required_field(schema: dict) -> None:
    schema["required"].append("governance")


def add_policy_required_field(schema: dict) -> None:
    schema["properties"]["policy"]["required"].append("risk_budget")


def add_fail_close_rule_required_field(schema: dict) -> None:
    schema["$defs"]["fail_close_rule"]["required"].append("severity")


@pytest.mark.parametrize("fixture", load_matrix(), ids=lambda fixture: fixture["id"])
def test_validator_contract_matches_fixture_matrix(fixture: dict) -> None:
    result = run_validator(fixture["path"])

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == RESULT_SCHEMA_VERSION
    assert payload["checked_path"] == fixture["path"]

    if fixture["valid"]:
        assert result.returncode == 0, result.stderr
        assert payload["valid"] is True
        assert payload["errors"] == []
    else:
        assert result.returncode == 1
        assert payload["valid"] is False
        assert payload["errors"][0]["code"] == fixture["expected_error"]


def test_fixture_matrix_covers_required_error_codes() -> None:
    expected_codes = {
        fixture["expected_error"]
        for fixture in load_matrix()
        if fixture["expected_error"] is not None
    }

    assert REQUIRED_ERROR_CODES <= expected_codes


@pytest.mark.parametrize(
    ("schema_version", "contract_version"),
    [
        ("risk_contract.experimental", "v999"),
    ],
)
def test_validator_rejects_unsupported_contract_versions_before_v1_schema_validation(
    tmp_path: Path, schema_version: str, contract_version: str
) -> None:
    contract = {
        "schema_version": schema_version,
        "contract_version": contract_version,
        "application": {"application_status": "not_claimable"},
    }
    path = tmp_path / "unsupported_contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    result = run_validator(str(path))

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["schema_version"] == RESULT_SCHEMA_VERSION
    assert payload["valid"] is False
    assert payload["errors"] == [
        {
            "code": UNSUPPORTED_CONTRACT_VERSION,
            "path": "schema_version",
            "message": f"unsupported contract version: {schema_version} / {contract_version}",
        }
    ]


def test_validator_accepts_exact_v2_contract_with_v2_result_envelope() -> None:
    contract_path = "risk/contracts/v2/fixtures/valid/base_valid.json"

    result = run_validator(contract_path)

    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert_v2_result_envelope(
        payload,
        checked_path=contract_path,
        valid=True,
        dispatch_status="validated",
        dispatch_reason=None,
    )
    assert payload["errors"] == []


@pytest.mark.parametrize("fixture", load_v2_matrix(), ids=lambda fixture: fixture["id"])
def test_validator_dispatches_exact_v2_contracts_against_v2_fixture_matrix(fixture: dict) -> None:
    result = run_validator(fixture["path"])

    payload = json.loads(result.stdout)
    if fixture["valid"]:
        assert result.returncode == 0, result.stderr
        assert_v2_result_envelope(
            payload,
            checked_path=fixture["path"],
            valid=True,
            dispatch_status="validated",
            dispatch_reason=None,
        )
        assert payload["errors"] == []
    else:
        assert result.returncode == 1
        assert_v2_result_envelope(
            payload,
            checked_path=fixture["path"],
            valid=False,
            dispatch_status="validation_failed",
            dispatch_reason=fixture["expected_error"],
        )
        assert payload["errors"][0]["code"] == fixture["expected_error"]


def write_contract(tmp_path: Path, filename: str, contract: dict) -> str:
    path = tmp_path / filename
    path.write_text(json.dumps(contract), encoding="utf-8")
    return str(path)


def assert_invalid_v2_contract(
    result: subprocess.CompletedProcess[str],
    *,
    checked_path: str,
    expected_path: str,
    expected_code: str = INVALID_SURFACE_CANDIDATE_ENVELOPE,
    expected_message_fragment: str | None = None,
) -> None:
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert_v2_result_envelope(
        payload,
        checked_path=checked_path,
        valid=False,
        dispatch_status="validation_failed",
        dispatch_reason=expected_code,
    )
    assert payload["errors"][0]["code"] == expected_code
    assert payload["errors"][0]["path"] == expected_path
    if expected_message_fragment is not None:
        assert expected_message_fragment in payload["errors"][0]["message"]


def assert_invalid_live_contract_proof(
    result: subprocess.CompletedProcess[str],
    *,
    checked_path: str,
    expected_path: str,
    expected_message_fragment: str | None = None,
) -> None:
    assert_invalid_v2_contract(
        result,
        checked_path=checked_path,
        expected_code=INVALID_LIVE_CONTRACT_PROOF,
        expected_path=expected_path,
        expected_message_fragment=expected_message_fragment,
    )


def mutate_live_fixture_and_validate(
    tmp_path: Path,
    filename: str,
    mutation_path: str,
    value: object = None,
    *,
    remove: bool = False,
) -> subprocess.CompletedProcess[str]:
    contract = live_order_notional_claimable_v2_contract()
    target = contract
    parts = mutation_path.split(".")
    for part in parts[:-1]:
        target = target[part]
    if remove:
        target.pop(parts[-1])
    else:
        target[parts[-1]] = value
    checked_path = write_contract(tmp_path, filename, contract)
    return run_validator(checked_path)


def test_validator_defines_live_contract_proof_boundary() -> None:
    assert validator.INVALID_LIVE_CONTRACT_PROOF == INVALID_LIVE_CONTRACT_PROOF
    assert callable(validator.first_v2_invalid_live_contract_proof)


def test_v2_validator_accepts_live_order_notional_claimable_fixture() -> None:
    result = run_validator(LIVE_ORDER_NOTIONAL_CLAIMABLE_FIXTURE)

    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert_v2_result_envelope(
        payload,
        checked_path=LIVE_ORDER_NOTIONAL_CLAIMABLE_FIXTURE,
        valid=True,
        dispatch_status="validated",
        dispatch_reason=None,
    )
    assert payload["errors"] == []


@pytest.mark.parametrize(
    "requested_size_basis",
    [
        "unit_backtest_run",
        "unit_scan_slot",
        "unit_paper_slot_allocation",
    ],
)
def test_v2_validator_rejects_guarded_live_non_live_sizing_basis(
    tmp_path: Path, requested_size_basis: str
) -> None:
    contract = live_order_notional_claimable_v2_contract()
    contract["candidate"]["sizing"]["requested_size_basis"] = requested_size_basis
    checked_path = write_contract(tmp_path, "live_with_non_live_sizing_basis.json", contract)

    result = run_validator(checked_path)

    assert_invalid_live_contract_proof(
        result,
        checked_path=checked_path,
        expected_path="candidate.sizing.requested_size_basis",
        expected_message_fragment="unit_live_order_notional",
    )


def test_v2_validator_rejects_live_fixture_missing_account_proof() -> None:
    result = run_validator(INVALID_LIVE_MISSING_ACCOUNT_PROOF_FIXTURE)

    assert_invalid_live_contract_proof(
        result,
        checked_path=INVALID_LIVE_MISSING_ACCOUNT_PROOF_FIXTURE,
        expected_path="candidate.surface_payload.account_proof",
        expected_message_fragment="missing required live proof block",
    )


def test_v2_validator_rejects_live_fixture_with_forbidden_raw_account_id() -> None:
    result = run_validator(INVALID_LIVE_UNSAFE_SECRET_FIELD_FIXTURE)

    assert_invalid_live_contract_proof(
        result,
        checked_path=INVALID_LIVE_UNSAFE_SECRET_FIELD_FIXTURE,
        expected_path="candidate.surface_payload.account_proof.raw_account_id",
        expected_message_fragment="forbidden live proof key",
    )


def test_v2_validator_rejects_live_implemented_runtime_claim_without_proof(
    tmp_path: Path,
) -> None:
    contract = live_order_notional_claimable_v2_contract()
    contract["candidate"]["surface"]["surface_status"] = "implemented"
    contract["application"]["application_status"] = "applied"
    contract["application"]["runtime_sizing_applied"] = True
    contract["candidate"]["surface_payload"].pop("account_proof")
    checked_path = write_contract(
        tmp_path,
        "live_implemented_runtime_claim_without_proof.json",
        contract,
    )

    result = run_validator(checked_path)

    assert_invalid_live_contract_proof(
        result,
        checked_path=checked_path,
        expected_path="candidate.surface.surface_status",
        expected_message_fragment="not approved",
    )


def test_v2_validator_rejects_guarded_live_runtime_sizing_claim(tmp_path: Path) -> None:
    contract = live_order_notional_claimable_v2_contract()
    contract["application"]["application_status"] = "applied"
    contract["application"]["runtime_sizing_applied"] = True
    checked_path = write_contract(tmp_path, "guarded_live_runtime_sizing_claim.json", contract)

    result = run_validator(checked_path)

    assert_invalid_live_contract_proof(
        result,
        checked_path=checked_path,
        expected_path="application.runtime_sizing_applied",
        expected_message_fragment="cannot claim runtime sizing",
    )


def test_v2_validator_rejects_live_runtime_sizing_claim_as_live_proof_error(
    tmp_path: Path,
) -> None:
    contract = live_order_notional_claimable_v2_contract()
    contract["application"]["runtime_sizing_applied"] = True
    checked_path = write_contract(tmp_path, "live_runtime_sizing_claim.json", contract)

    result = run_validator(checked_path)

    assert_invalid_live_contract_proof(
        result,
        checked_path=checked_path,
        expected_path="application.runtime_sizing_applied",
        expected_message_fragment="cannot claim runtime sizing",
    )


@pytest.mark.parametrize(
    ("mutation_path", "value", "expected_path"),
    [
        ("application.execution_state", "continued", "application.execution_state"),
        ("application.application_status", "deferred", "application.application_status"),
        ("application.sizing_effect", "reduced", "application.sizing_effect"),
    ],
)
def test_v2_validator_rejects_guarded_live_runtime_application_claims(
    tmp_path: Path,
    mutation_path: str,
    value: object,
    expected_path: str,
) -> None:
    result = mutate_live_fixture_and_validate(
        tmp_path,
        "guarded_live_runtime_application_claim.json",
        mutation_path,
        value,
    )

    assert_invalid_live_contract_proof(
        result,
        checked_path=str(tmp_path / "guarded_live_runtime_application_claim.json"),
        expected_path=expected_path,
    )


@pytest.mark.parametrize("forbidden_key", ["raw_account_id", "token", "endpoint"])
def test_v2_validator_rejects_forbidden_secret_key_directly_under_live_surface_payload(
    tmp_path: Path,
    forbidden_key: str,
) -> None:
    contract = live_order_notional_claimable_v2_contract()
    contract["candidate"]["surface_payload"][forbidden_key] = "unsafe-fixture-value"
    checked_path = write_contract(
        tmp_path,
        f"live_payload_with_{forbidden_key}.json",
        contract,
    )

    result = run_validator(checked_path)

    assert_invalid_live_contract_proof(
        result,
        checked_path=checked_path,
        expected_path=f"candidate.surface_payload.{forbidden_key}",
        expected_message_fragment="forbidden live proof key",
    )


def test_v2_validator_rejects_not_wired_live_applied_status(tmp_path: Path) -> None:
    contract = live_not_claimable_v2_contract()
    contract["application"]["application_status"] = "applied"
    checked_path = write_contract(tmp_path, "not_wired_live_applied_status.json", contract)

    result = run_validator(checked_path)

    assert_invalid_live_contract_proof(
        result,
        checked_path=checked_path,
        expected_path="application.application_status",
        expected_message_fragment="not_claimable",
    )


@pytest.mark.parametrize("forbidden_key", ["raw_account_id", "token", "endpoint"])
def test_v2_validator_rejects_forbidden_secret_key_on_not_wired_live_payload(
    tmp_path: Path,
    forbidden_key: str,
) -> None:
    contract = live_not_claimable_v2_contract()
    contract["candidate"]["surface_payload"][forbidden_key] = "unsafe-fixture-value"
    checked_path = write_contract(
        tmp_path,
        f"not_wired_live_payload_with_{forbidden_key}.json",
        contract,
    )

    result = run_validator(checked_path)

    assert_invalid_live_contract_proof(
        result,
        checked_path=checked_path,
        expected_path=f"candidate.surface_payload.{forbidden_key}",
        expected_message_fragment="forbidden live proof key",
    )


@pytest.mark.parametrize(
    ("mutation_path", "value", "expected_path"),
    [
        ("candidate.surface_payload.runtime_adoption", "approved", "candidate.surface_payload.runtime_adoption"),
        ("candidate.surface_payload.live_runtime", "implemented_runtime_claim", "candidate.surface_payload.live_runtime"),
        ("candidate.surface_payload.live_runtime_claim_allowed", True, "candidate.surface_payload.live_runtime_claim_allowed"),
        ("candidate.surface_payload.order_mutation_allowed", True, "candidate.surface_payload.order_mutation_allowed"),
        ("candidate.surface_payload.broker_mutation_attempted", True, "candidate.surface_payload.broker_mutation_attempted"),
    ],
)
def test_v2_validator_rejects_not_wired_live_payload_runtime_or_broker_claims(
    tmp_path: Path,
    mutation_path: str,
    value: object,
    expected_path: str,
) -> None:
    contract = live_not_claimable_v2_contract()
    target = contract
    parts = mutation_path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    checked_path = write_contract(tmp_path, "not_wired_live_payload_claim.json", contract)

    result = run_validator(checked_path)

    assert_invalid_live_contract_proof(
        result,
        checked_path=checked_path,
        expected_path=expected_path,
    )


@pytest.mark.parametrize("field", ["preflight_proof", "public_live_output_ref"])
def test_v2_validator_rejects_unexpected_not_wired_live_payload_fields(
    tmp_path: Path,
    field: str,
) -> None:
    contract = live_not_claimable_v2_contract()
    contract["candidate"]["surface_payload"][field] = "unexpected-live-claim"
    checked_path = write_contract(tmp_path, "not_wired_live_unexpected_payload_field.json", contract)

    result = run_validator(checked_path)

    assert_invalid_live_contract_proof(
        result,
        checked_path=checked_path,
        expected_path=f"candidate.surface_payload.{field}",
        expected_message_fragment="unexpected not-wired live payload field",
    )


@pytest.mark.parametrize(
    ("mutation_path", "value", "expected_path"),
    [
        ("context.runtime_adoption", "approved", "context.runtime_adoption"),
        ("context.live_runtime_claim_allowed", True, "context.live_runtime_claim_allowed"),
        ("context.order_mutation_allowed", True, "context.order_mutation_allowed"),
        ("context.broker_mutation_attempted", True, "context.broker_mutation_attempted"),
    ],
)
def test_v2_validator_rejects_live_context_runtime_or_broker_claims(
    tmp_path: Path,
    mutation_path: str,
    value: object,
    expected_path: str,
) -> None:
    contract = live_order_notional_claimable_v2_contract()
    target = contract
    parts = mutation_path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    checked_path = write_contract(tmp_path, "live_context_runtime_claim.json", contract)

    result = run_validator(checked_path)

    assert_invalid_live_contract_proof(
        result,
        checked_path=checked_path,
        expected_path=expected_path,
    )


@pytest.mark.parametrize(
    ("mutation_path", "value", "expected_path"),
    [
        (
            "candidate.surface_payload.kill_switch_proof.global_gate_ref",
            "https://private-broker-endpoint.example",
            "candidate.surface_payload.kill_switch_proof.global_gate_ref",
        ),
        (
            "candidate.surface_payload.idempotency_proof.idempotency_key_hash",
            "raw-idempotency-key",
            "candidate.surface_payload.idempotency_proof.idempotency_key_hash",
        ),
        (
            "candidate.surface_payload.idempotency_proof.idempotency_key_hash",
            "raw_idempotency_key",
            "candidate.surface_payload.idempotency_proof.idempotency_key_hash",
        ),
        (
            "candidate.surface_payload.idempotency_proof.idempotency_key_hash",
            "password=letmein",
            "candidate.surface_payload.idempotency_proof.idempotency_key_hash",
        ),
        (
            "candidate.surface_payload.order_intent.idempotency_key_hash",
            "api_key=abc123",
            "candidate.surface_payload.order_intent.idempotency_key_hash",
        ),
        (
            "candidate.surface_payload.order_intent.idempotency_key_hash",
            "raw-idempotency-key",
            "candidate.surface_payload.order_intent.idempotency_key_hash",
        ),
    ],
)
def test_v2_validator_rejects_forbidden_secret_like_live_proof_values(
    tmp_path: Path,
    mutation_path: str,
    value: str,
    expected_path: str,
) -> None:
    result = mutate_live_fixture_and_validate(
        tmp_path,
        "live_secret_like_value.json",
        mutation_path,
        value,
    )

    assert_invalid_live_contract_proof(
        result,
        checked_path=str(tmp_path / "live_secret_like_value.json"),
        expected_path=expected_path,
        expected_message_fragment="forbidden live proof value",
    )


@pytest.mark.parametrize(
    ("mutation_path", "expected_path"),
    [
        (
            "candidate.surface_payload.kill_switch_proof.global_gate_ref",
            "candidate.surface_payload.kill_switch_proof.global_gate_ref",
        ),
        (
            "candidate.surface_payload.idempotency_proof.duplicate_check_ref",
            "candidate.surface_payload.idempotency_proof.duplicate_check_ref",
        ),
    ],
)
def test_v2_validator_rejects_live_proof_missing_sanitized_references(
    tmp_path: Path, mutation_path: str, expected_path: str
) -> None:
    result = mutate_live_fixture_and_validate(
        tmp_path,
        "live_missing_sanitized_reference.json",
        mutation_path,
        remove=True,
    )

    assert_invalid_live_contract_proof(
        result,
        checked_path=str(tmp_path / "live_missing_sanitized_reference.json"),
        expected_path=expected_path,
        expected_message_fragment="missing required live proof field",
    )


@pytest.mark.parametrize(
    ("mutation_path", "expected_path"),
    [
        (
            "candidate.surface_payload.account_proof.snapshot_max_age_ms",
            "candidate.surface_payload.account_proof.snapshot_max_age_ms",
        ),
        (
            "candidate.surface_payload.market_proof.market_max_age_ms",
            "candidate.surface_payload.market_proof.market_max_age_ms",
        ),
        (
            "candidate.surface_payload.kill_switch_proof.proof_max_age_ms",
            "candidate.surface_payload.kill_switch_proof.proof_max_age_ms",
        ),
        (
            "candidate.surface_payload.idempotency_proof.proof_max_age_ms",
            "candidate.surface_payload.idempotency_proof.proof_max_age_ms",
        ),
    ],
)
def test_v2_validator_rejects_live_proof_non_positive_freshness_bounds(
    tmp_path: Path, mutation_path: str, expected_path: str
) -> None:
    result = mutate_live_fixture_and_validate(
        tmp_path,
        "live_non_positive_freshness.json",
        mutation_path,
        0,
    )

    assert_invalid_live_contract_proof(
        result,
        checked_path=str(tmp_path / "live_non_positive_freshness.json"),
        expected_path=expected_path,
        expected_message_fragment="positive",
    )


@pytest.mark.parametrize(
    ("mutation_path", "value", "expected_path"),
    [
        (
            "candidate.surface_payload.account_proof.snapshot_ts",
            "",
            "candidate.surface_payload.account_proof.snapshot_ts",
        ),
        (
            "candidate.surface_payload.market_proof.market_ts",
            "not-a-timestamp",
            "candidate.surface_payload.market_proof.market_ts",
        ),
        (
            "candidate.surface_payload.kill_switch_proof.proof_ts",
            "",
            "candidate.surface_payload.kill_switch_proof.proof_ts",
        ),
        (
            "candidate.surface_payload.idempotency_proof.proof_ts",
            "2026/05/19 00:00:03",
            "candidate.surface_payload.idempotency_proof.proof_ts",
        ),
    ],
)
def test_v2_validator_rejects_live_proof_empty_or_malformed_timestamps(
    tmp_path: Path, mutation_path: str, value: str, expected_path: str
) -> None:
    result = mutate_live_fixture_and_validate(
        tmp_path,
        "live_malformed_timestamp.json",
        mutation_path,
        value,
    )

    assert_invalid_live_contract_proof(
        result,
        checked_path=str(tmp_path / "live_malformed_timestamp.json"),
        expected_path=expected_path,
        expected_message_fragment="timestamp",
    )


@pytest.mark.parametrize(
    ("mutation_path", "value", "expected_path"),
    [
        (
            "candidate.surface_payload.kill_switch_proof.global_gate_status",
            "",
            "candidate.surface_payload.kill_switch_proof.global_gate_status",
        ),
        (
            "candidate.surface_payload.idempotency_proof.duplicate_check_status",
            "maybe",
            "candidate.surface_payload.idempotency_proof.duplicate_check_status",
        ),
    ],
)
def test_v2_validator_rejects_live_proof_empty_or_malformed_statuses(
    tmp_path: Path, mutation_path: str, value: str, expected_path: str
) -> None:
    result = mutate_live_fixture_and_validate(
        tmp_path,
        "live_malformed_status.json",
        mutation_path,
        value,
    )

    assert_invalid_live_contract_proof(
        result,
        checked_path=str(tmp_path / "live_malformed_status.json"),
        expected_path=expected_path,
        expected_message_fragment="status",
    )


def test_v2_validator_keeps_live_not_claimable_fixture_without_live_proof() -> None:
    result = run_validator(LIVE_NOT_CLAIMABLE_FIXTURE)

    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert_v2_result_envelope(
        payload,
        checked_path=LIVE_NOT_CLAIMABLE_FIXTURE,
        valid=True,
        dispatch_status="validated",
        dispatch_reason=None,
    )
    assert payload["errors"] == []


def test_v2_validator_rejects_scan_surface_with_backtest_sizing_basis(tmp_path: Path) -> None:
    contract = scan_metadata_v2_contract()
    contract["candidate"]["sizing"]["requested_size_basis"] = "unit_backtest_run"
    checked_path = write_contract(tmp_path, "scan_with_backtest_basis.json", contract)

    result = run_validator(checked_path)

    assert_invalid_v2_contract(
        result,
        checked_path=checked_path,
        expected_path="candidate.sizing.requested_size_basis",
    )


def test_v2_validator_accepts_paper_cap_applied_fixture() -> None:
    contract_path = "risk/contracts/v2/fixtures/valid/paper_cap_applied_valid.json"

    result = run_validator(contract_path)

    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert_v2_result_envelope(
        payload,
        checked_path=contract_path,
        valid=True,
        dispatch_status="validated",
        dispatch_reason=None,
    )
    assert payload["errors"] == []


def test_v2_validator_rejects_paper_surface_with_backtest_sizing_basis(tmp_path: Path) -> None:
    contract = paper_cap_applied_v2_contract()
    contract["candidate"]["sizing"]["requested_size_basis"] = "unit_backtest_run"
    checked_path = write_contract(tmp_path, "paper_with_backtest_basis.json", contract)

    result = run_validator(checked_path)

    assert_invalid_v2_contract(
        result,
        checked_path=checked_path,
        expected_path="candidate.sizing.requested_size_basis",
    )


def test_v2_validator_rejects_backtest_surface_with_wfd_analysis_scope(tmp_path: Path) -> None:
    contract = base_v2_contract()
    surface = contract["candidate"]["surface"]
    surface["analysis_scope"] = "wfd_statistical_output"
    surface["analysis_scope_status"] = "metadata_only"
    checked_path = write_contract(tmp_path, "backtest_with_wfd_analysis_scope.json", contract)

    result = run_validator(checked_path)

    assert_invalid_v2_contract(
        result,
        checked_path=checked_path,
        expected_path="candidate.surface.analysis_scope",
    )


def test_v2_validator_rejects_paper_surface_with_wfd_analysis_scope(tmp_path: Path) -> None:
    contract = paper_cap_applied_v2_contract()
    surface = contract["candidate"]["surface"]
    surface["analysis_scope"] = "wfd_statistical_output"
    surface["analysis_scope_status"] = "metadata_only"
    contract["application"]["application_status"] = "metadata_only"
    contract["application"]["runtime_sizing_applied"] = False
    contract["application"]["sizing_effect"] = "none"
    checked_path = write_contract(tmp_path, "paper_with_wfd_analysis_scope.json", contract)

    result = run_validator(checked_path)

    assert_invalid_v2_contract(
        result,
        checked_path=checked_path,
        expected_path="candidate.surface.analysis_scope",
    )


def test_v2_validator_rejects_metadata_only_application_that_claims_runtime_sizing(
    tmp_path: Path,
) -> None:
    contract = scan_metadata_v2_contract()
    contract["application"]["runtime_sizing_applied"] = True
    checked_path = write_contract(tmp_path, "metadata_only_with_runtime_sizing.json", contract)

    result = run_validator(checked_path)

    assert_invalid_v2_contract(
        result,
        checked_path=checked_path,
        expected_path="application.runtime_sizing_applied",
    )


def test_v2_validator_rejects_runtime_sizing_claim_without_applied_status(tmp_path: Path) -> None:
    contract = paper_cap_applied_v2_contract()
    contract["application"]["application_status"] = "not_applicable"
    checked_path = write_contract(
        tmp_path, "paper_runtime_sizing_without_applied_status.json", contract
    )

    result = run_validator(checked_path)

    assert_invalid_v2_contract(
        result,
        checked_path=checked_path,
        expected_path="application.runtime_sizing_applied",
    )


def test_v2_validator_rejects_paper_applied_status_for_non_cap_decision(tmp_path: Path) -> None:
    contract = paper_cap_applied_v2_contract()
    contract["decision"]["decision_class"] = "size"
    contract["trace"]["decision_class"] = "size"
    checked_path = write_contract(tmp_path, "paper_size_with_applied_status.json", contract)

    result = run_validator(checked_path)

    assert_invalid_v2_contract(
        result,
        checked_path=checked_path,
        expected_path="application.application_status",
    )


def test_v2_validator_rejects_stopped_paper_runtime_sizing_claim(tmp_path: Path) -> None:
    contract = paper_cap_applied_v2_contract()
    contract["application"]["execution_state"] = "stopped"
    checked_path = write_contract(tmp_path, "paper_stopped_runtime_sizing_claim.json", contract)

    result = run_validator(checked_path)

    assert_invalid_v2_contract(
        result,
        checked_path=checked_path,
        expected_path="application.runtime_sizing_applied",
    )


def test_validator_preserves_v1_result_envelope_for_exact_v1_contracts() -> None:
    contract_path = "risk/contracts/v1/fixtures/valid/base_valid.json"

    result = run_validator(contract_path)

    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert payload["schema_version"] == RESULT_SCHEMA_VERSION
    assert payload["valid"] is True
    assert "contract_identity" not in payload
    assert "validated_schema" not in payload
    assert "dispatch" not in payload


def test_validator_rejects_mismatched_v1_contract_version_as_dispatch_error(tmp_path: Path) -> None:
    contract = base_valid_contract()
    contract["contract_version"] = "v2"
    path = tmp_path / "mismatched_contract_version.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    result = run_validator(str(path))

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["valid"] is False
    assert payload["errors"][0]["code"] == UNSUPPORTED_CONTRACT_VERSION
    assert payload["errors"][0]["path"] == "contract_version"


def test_schema_helpers_derive_decision_class_vocabularies() -> None:
    schema = validator.load_contract_schema()

    assert validator.schema_decision_classes(schema) == {
        "size",
        "cap",
        "reject",
        "kill",
        "block",
    }
    assert validator.schema_fail_close_rule_decision_classes(schema) == {
        "kill",
        "block",
    }


def test_schema_helpers_derive_fail_close_reasons() -> None:
    schema = validator.load_contract_schema()

    assert validator.schema_fail_close_reasons(schema) == {
        "missing_required_policy_field",
        "stale_evidence",
        "policy_evidence_contradiction",
        "evidence_acquisition_failure",
        "absent_source_proof",
        "malformed_policy",
        "insufficient_validation_power",
        "candidate_validation_failure",
    }


def test_schema_helpers_derive_required_fields() -> None:
    schema = validator.load_contract_schema()

    assert validator.schema_required_top_level(schema) == (
        "schema_version",
        "contract_version",
        "policy",
        "candidate",
        "evidence",
        "context",
        "decision",
        "trace",
    )
    assert validator.schema_required_nested_fields(schema) == {
        "policy": (
            "version",
            "owner",
            "effective_from",
            "required_fields",
            "fail_close_rules",
        ),
        "candidate": (
            "strategy_id",
            "symbol_or_universe",
            "timeframe",
            "validation_refs",
        ),
        "evidence": ("refs",),
        "decision": (
            "decision_class",
            "allowed_size",
            "binding_rule",
            "supporting_rules",
            "fail_close_reason",
            "evidence_refs",
            "policy_version",
        ),
        "trace": (
            "policy_version",
            "candidate_id",
            "input_evidence_refs",
            "binding_rule",
            "decision_class",
            "emitted_artifact_path",
        ),
    }
    assert validator.schema_required_fail_close_rule_fields(schema) == (
        "condition",
        "decision_class",
        "fail_close_reason",
    )


def test_schema_fact_snapshot_matches_current_schema() -> None:
    assert_schema_facts_match_snapshot(validator.load_contract_schema())


@pytest.mark.parametrize(
    ("mutation", "expected_key"),
    [
        (add_decision_class, "decision_classes"),
        (add_fail_close_reason, "fail_close_reasons"),
        (add_top_level_required_field, "required_top_level"),
        (add_policy_required_field, "required_nested_fields"),
        (add_fail_close_rule_required_field, "required_fail_close_rule_fields"),
    ],
)
def test_schema_fact_snapshot_detects_representative_schema_only_drift(
    mutation, expected_key: str
) -> None:
    schema = deepcopy(validator.load_contract_schema())
    mutation(schema)

    with pytest.raises(AssertionError, match=expected_key):
        assert_schema_facts_match_snapshot(schema)


def test_validator_module_no_longer_declares_schema_owned_constants() -> None:
    source = VALIDATOR.read_text(encoding="utf-8")

    assert "DECISION_CLASSES =" not in source
    assert "FAIL_CLOSE_REASONS =" not in source
    assert "REQUIRED_TOP_LEVEL =" not in source
    assert "REQUIRED_NESTED_FIELDS =" not in source


def test_validator_usage_error_exits_2() -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2


def test_validator_malformed_json_exits_2(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not valid json", encoding="utf-8")

    result = run_validator(str(malformed))

    assert result.returncode == 2
