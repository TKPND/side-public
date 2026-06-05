import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "risk/contracts/v2/fixture_matrix.json"
SCHEMA_PATH = ROOT / "risk/contracts/v2/risk_contract_v2.schema.json"

EXPECTED_FIXTURES = (
    ("base_valid", "risk/contracts/v2/fixtures/valid/base_valid.json", True, None),
    ("scan_metadata_valid", "risk/contracts/v2/fixtures/valid/scan_metadata_valid.json", True, None),
    (
        "live_not_claimable_valid",
        "risk/contracts/v2/fixtures/valid/live_not_claimable_valid.json",
        True,
        None,
    ),
    (
        "paper_cap_applied_valid",
        "risk/contracts/v2/fixtures/valid/paper_cap_applied_valid.json",
        True,
        None,
    ),
    (
        "invalid_fail_close_reason",
        "risk/contracts/v2/fixtures/invalid/invalid_fail_close_reason.json",
        False,
        "invalid_fail_close_reason",
    ),
    (
        "invalid_runtime_surface_scope",
        "risk/contracts/v2/fixtures/invalid/invalid_runtime_surface_scope.json",
        False,
        "invalid_runtime_surface_scope",
    ),
    (
        "invalid_scan_sizing_basis",
        "risk/contracts/v2/fixtures/invalid/invalid_scan_sizing_basis.json",
        False,
        "invalid_surface_candidate_envelope",
    ),
    (
        "invalid_paper_sizing_basis",
        "risk/contracts/v2/fixtures/invalid/invalid_paper_sizing_basis.json",
        False,
        "invalid_surface_candidate_envelope",
    ),
    (
        "missing_required_application_block",
        "risk/contracts/v2/fixtures/invalid/missing_required_application_block.json",
        False,
        "missing_required_block_or_field",
    ),
    (
        "live_order_notional_claimable_valid",
        "risk/contracts/v2/fixtures/valid/live_order_notional_claimable_valid.json",
        True,
        None,
    ),
    (
        "invalid_live_sizing_basis",
        "risk/contracts/v2/fixtures/invalid/invalid_live_sizing_basis.json",
        False,
        "invalid_live_contract_proof",
    ),
    (
        "invalid_live_missing_account_proof",
        "risk/contracts/v2/fixtures/invalid/invalid_live_missing_account_proof.json",
        False,
        "invalid_live_contract_proof",
    ),
    (
        "invalid_live_unsafe_secret_field",
        "risk/contracts/v2/fixtures/invalid/invalid_live_unsafe_secret_field.json",
        False,
        "invalid_live_contract_proof",
    ),
)


def load_json(path: Path) -> dict:
    assert path.exists(), f"missing expected JSON file: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def schema_enums() -> dict[str, tuple[str, ...]]:
    schema = load_json(SCHEMA_PATH)
    return {
        "runtime_surface": tuple(
            schema["$defs"]["surface"]["properties"]["runtime_surface"]["enum"]
        ),
        "analysis_scope": tuple(
            schema["$defs"]["surface"]["properties"]["analysis_scope"]["enum"]
        ),
        "fail_close_reason": tuple(schema["$defs"]["fail_close_reason_v2"]["enum"]),
        "decision_class": tuple(schema["$defs"]["decision_class"]["enum"]),
        "requested_size_basis": tuple(
            schema["$defs"]["sizing"]["properties"]["requested_size_basis"]["enum"]
        ),
    }


def has_forbidden_live_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = key.lower()
            if any(
                fragment in lowered
                for fragment in (
                    "raw_account_id",
                    "account_id",
                    "credential",
                    "token",
                    "secret",
                    "private_key",
                    "cookie",
                    "endpoint",
                )
            ):
                return True
            if has_forbidden_live_key(nested):
                return True
    elif isinstance(value, list):
        return any(has_forbidden_live_key(nested) for nested in value)
    return False


def classify_fixture_error(contract: dict) -> str | None:
    schema = load_json(SCHEMA_PATH)
    required_top_level = tuple(schema["required"])
    for key in required_top_level:
        if key not in contract:
            return "missing_required_block_or_field"

    enums = schema_enums()
    surface = contract["candidate"]["surface"]
    sizing = contract["candidate"]["sizing"]
    if surface["runtime_surface"] not in enums["runtime_surface"]:
        return "invalid_runtime_surface_scope"
    if surface["analysis_scope"] not in enums["analysis_scope"]:
        return "invalid_runtime_surface_scope"
    if sizing["requested_size_basis"] not in enums["requested_size_basis"]:
        return "invalid_surface_candidate_envelope"
    if (
        surface["runtime_surface"] == "backtest"
        and sizing["requested_size_basis"] != "unit_backtest_run"
    ):
        return "invalid_surface_candidate_envelope"
    if (
        surface["runtime_surface"] == "scan"
        and sizing["requested_size_basis"] != "unit_scan_slot"
    ):
        return "invalid_surface_candidate_envelope"
    if (
        surface["runtime_surface"] == "paper"
        and sizing["requested_size_basis"] != "unit_paper_slot_allocation"
    ):
        return "invalid_surface_candidate_envelope"
    if (
        surface["runtime_surface"] == "live"
        and surface["surface_status"] == "guarded"
        and sizing["requested_size_basis"] != "unit_live_order_notional"
    ):
        return "invalid_live_contract_proof"
    if (
        surface["runtime_surface"] == "live"
        and surface["surface_status"] == "guarded"
        and sizing["requested_size_basis"] == "unit_live_order_notional"
    ):
        payload = contract["candidate"].get("surface_payload")
        if not isinstance(payload, dict):
            return "invalid_live_contract_proof"
        if "account_proof" not in payload:
            return "invalid_live_contract_proof"
        proof_blocks = (
            "account_proof",
            "market_proof",
            "order_intent",
            "kill_switch_proof",
            "idempotency_proof",
        )
        if any(has_forbidden_live_key(payload[block]) for block in proof_blocks if block in payload):
            return "invalid_live_contract_proof"

    decision = contract["decision"]
    trace = contract["trace"]
    if decision["decision_class"] not in enums["decision_class"]:
        return "invalid_decision_class"
    if trace["decision_class"] not in enums["decision_class"]:
        return "invalid_decision_class"
    if decision["decision_class"] != trace["decision_class"]:
        return "decision_trace_mismatch"
    if decision["fail_close_reason"] not in enums["fail_close_reason"]:
        return "invalid_fail_close_reason"
    return None


def test_v2_fixture_matrix_declares_schema_and_ordered_fixture_set() -> None:
    matrix = load_json(MATRIX_PATH)

    assert matrix["schema_version"] == "risk_contract_fixture_matrix.v2"
    assert matrix["contract_schema"] == "risk/contracts/v2/risk_contract_v2.schema.json"
    assert [
        (fixture["id"], fixture["path"], fixture["valid"], fixture["expected_error"])
        for fixture in matrix["fixtures"]
    ] == list(EXPECTED_FIXTURES)


def test_v2_fixture_matrix_paths_exist_and_stay_under_v2_fixture_roots() -> None:
    matrix = load_json(MATRIX_PATH)

    for fixture in matrix["fixtures"]:
        path = fixture["path"]
        expected_root = "risk/contracts/v2/fixtures/valid/" if fixture["valid"] else "risk/contracts/v2/fixtures/invalid/"
        assert path.startswith(expected_root), fixture
        assert (ROOT / path).exists(), path


def test_v2_valid_fixtures_match_schema_identity_and_matrix_expectations() -> None:
    matrix = load_json(MATRIX_PATH)

    for fixture in matrix["fixtures"]:
        contract = load_json(ROOT / fixture["path"])
        if not fixture["valid"]:
            continue

        assert contract["schema_version"] == "risk_contract.v2"
        assert contract["contract_version"] == "v2"
        assert contract["candidate"]["candidate_schema_version"] == "risk_contract.v2.candidate.v1"
        assert contract["trace"]["validated_schema_version"] == "risk_contract.v2"
        assert contract["trace"]["validator_result_schema_version"] == "risk_contract_validator_result.v2"
        assert classify_fixture_error(contract) is None
        assert fixture["expected_error"] is None


def test_v2_live_not_claimable_fixture_makes_no_runtime_claims() -> None:
    matrix = load_json(MATRIX_PATH)
    fixture = next(
        fixture
        for fixture in matrix["fixtures"]
        if fixture["id"] == "live_not_claimable_valid"
    )
    contract = load_json(ROOT / fixture["path"])

    assert fixture["valid"] is True
    assert fixture["expected_error"] is None
    assert contract["candidate"]["surface"] == {
        "runtime_surface": "live",
        "surface_status": "not_wired",
        "analysis_scope": "none",
        "analysis_scope_status": "not_applicable",
    }
    assert contract["application"]["application_status"] == "not_claimable"
    assert contract["application"]["runtime_sizing_applied"] is False
    assert contract["application"]["sizing_effect"] == "not_applicable"
    assert contract["application"]["metrics_rescaled"] is False
    assert contract["context"]["runtime_adoption"] == "not_approved"
    assert contract["context"]["live_runtime_claim_allowed"] is False


def test_v2_live_order_notional_claimable_fixture_is_guarded_but_not_runtime_applied() -> None:
    matrix = load_json(MATRIX_PATH)
    fixture = next(
        fixture
        for fixture in matrix["fixtures"]
        if fixture["id"] == "live_order_notional_claimable_valid"
    )
    contract = load_json(ROOT / fixture["path"])

    assert fixture["valid"] is True
    assert fixture["expected_error"] is None
    assert contract["candidate"]["surface"] == {
        "runtime_surface": "live",
        "surface_status": "guarded",
        "analysis_scope": "none",
        "analysis_scope_status": "not_applicable",
    }
    assert contract["candidate"]["sizing"]["requested_size_basis"] == "unit_live_order_notional"
    assert contract["candidate"]["surface_payload"]["runtime_adoption"] == "not_approved"
    assert contract["candidate"]["surface_payload"]["order_mutation_allowed"] is False
    assert contract["candidate"]["surface_payload"]["broker_mutation_attempted"] is False
    assert (
        contract["candidate"]["surface_payload"]["order_intent"]["order_mutation_allowed"]
        is False
    )
    assert contract["application"]["application_status"] == "not_applicable"
    assert contract["application"]["runtime_sizing_applied"] is False
    assert contract["application"]["metrics_rescaled"] is False


def test_v2_paper_cap_fixture_uses_paper_slot_allocation_unit() -> None:
    matrix = load_json(MATRIX_PATH)
    fixture = next(
        fixture
        for fixture in matrix["fixtures"]
        if fixture["id"] == "paper_cap_applied_valid"
    )
    contract = load_json(ROOT / fixture["path"])

    assert fixture["valid"] is True
    assert fixture["expected_error"] is None
    assert contract["candidate"]["surface"] == {
        "runtime_surface": "paper",
        "surface_status": "implemented",
        "analysis_scope": "none",
        "analysis_scope_status": "not_applicable",
    }
    assert contract["candidate"]["sizing"]["requested_size_basis"] == "unit_paper_slot_allocation"
    assert contract["decision"]["decision_class"] == "cap"
    assert contract["application"]["execution_state"] == "continued"
    assert contract["application"]["application_status"] == "applied"
    assert contract["application"]["runtime_sizing_applied"] is True
    assert contract["application"]["effective_size"] == contract["decision"]["allowed_size"]
    assert contract["application"]["metrics_rescaled"] is False
    assert contract["candidate"]["surface_payload"]["allocation_source"] == "PaperConfig::allocations"


def test_v2_invalid_fixtures_encode_their_expected_matrix_error() -> None:
    matrix = load_json(MATRIX_PATH)

    for fixture in matrix["fixtures"]:
        if fixture["valid"]:
            continue
        contract = load_json(ROOT / fixture["path"])

        assert classify_fixture_error(contract) == fixture["expected_error"]


def test_v2_fixture_matrix_is_now_accepted_by_the_validator_boundary() -> None:
    validator = (ROOT / "scripts/validate_risk_contract.py").read_text(encoding="utf-8")

    assert "V2_CONTRACT_SCHEMA_PATH" in validator
    assert "risk_contract_v2.schema.json" in validator
