import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

CLAIMABLE_LIVE_FIXTURE = (
    "risk/contracts/v2/fixtures/valid/live_order_notional_claimable_valid.json"
)
LIVE_NOT_CLAIMABLE_FIXTURE = (
    "risk/contracts/v2/fixtures/valid/live_not_claimable_valid.json"
)
INVALID_LIVE_SIZING_BASIS_FIXTURE = (
    "risk/contracts/v2/fixtures/invalid/invalid_live_sizing_basis.json"
)
INVALID_LIVE_MISSING_ACCOUNT_PROOF_FIXTURE = (
    "risk/contracts/v2/fixtures/invalid/invalid_live_missing_account_proof.json"
)
INVALID_LIVE_UNSAFE_SECRET_FIELD_FIXTURE = (
    "risk/contracts/v2/fixtures/invalid/invalid_live_unsafe_secret_field.json"
)

REQUIRED_LIVE_PROOF_BLOCKS = {
    "account_proof",
    "market_proof",
    "order_intent",
    "kill_switch_proof",
    "idempotency_proof",
}
REQUIRED_ACCOUNT_PROOF_FIELDS = {
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
}
REQUIRED_MARKET_PROOF_FIELDS = {
    "symbol",
    "market_snapshot_id",
    "market_ts",
    "market_max_age_ms",
    "bid",
    "ask",
    "spread_bps",
    "price_source",
}
REQUIRED_ORDER_INTENT_FIELDS = {
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
}
REQUIRED_KILL_SWITCH_FIELDS = {
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
}
REQUIRED_KILL_SWITCH_REFS = {
    "global_gate_ref",
    "strategy_gate_ref",
    "symbol_gate_ref",
    "broker_account_gate_ref",
    "exposure_gate_ref",
    "data_quality_gate_ref",
}
REQUIRED_IDEMPOTENCY_FIELDS = {
    "candidate_identity_digest",
    "idempotency_key_hash",
    "duplicate_check_status",
    "duplicate_check_ref",
    "proof_ts",
    "proof_max_age_ms",
}
FORBIDDEN_KEY_FRAGMENTS = (
    "raw_account_id",
    "account_id",
    "credential",
    "token",
    "secret",
    "private_key",
    "cookie",
    "endpoint",
)
FORBIDDEN_VALUE_FRAGMENTS = (
    "raw_account_id",
    "credential",
    "token",
    "secret",
    "private_key",
    "cookie",
    "endpoint",
)


def load_fixture(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    assert path.exists(), f"missing fixture: {relative_path}"
    return json.loads(path.read_text(encoding="utf-8"))


def forbidden_key_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, nested_value in value.items():
            path = f"{prefix}.{key}" if prefix else key
            lowered = key.lower()
            if any(fragment in lowered for fragment in FORBIDDEN_KEY_FRAGMENTS):
                paths.append(path)
            paths.extend(forbidden_key_paths(nested_value, path))
    elif isinstance(value, list):
        for index, nested_value in enumerate(value):
            paths.extend(forbidden_key_paths(nested_value, f"{prefix}[{index}]"))
    return paths


def assert_sanitized_reference(value: str) -> None:
    lowered = value.lower()
    assert all(fragment not in lowered for fragment in FORBIDDEN_VALUE_FRAGMENTS)
    assert "example-raw-account-id" not in lowered
    assert "raw-idempotency-key" not in lowered


def test_claimable_live_fixture_has_guarded_order_notional_semantics() -> None:
    contract = load_fixture(CLAIMABLE_LIVE_FIXTURE)
    payload = contract["candidate"]["surface_payload"]

    assert contract["candidate"]["surface"] == {
        "runtime_surface": "live",
        "surface_status": "guarded",
        "analysis_scope": "none",
        "analysis_scope_status": "not_applicable",
    }
    assert contract["candidate"]["sizing"] == {
        "requested_size": 10000.0,
        "requested_size_basis": "unit_live_order_notional",
    }
    assert contract["decision"]["decision_class"] == "cap"
    assert contract["decision"]["allowed_size"] == 7500.0
    assert contract["decision"]["fail_close_reason"] == "not_fail_closed"
    assert contract["application"]["execution_state"] == "stopped"
    assert contract["application"]["application_status"] == "not_applicable"
    assert contract["application"]["runtime_sizing_applied"] is False
    assert contract["application"]["sizing_effect"] == "not_applicable"
    assert contract["application"]["effective_size"] == 7500.0
    assert contract["application"]["metrics_rescaled"] is False

    for block in REQUIRED_LIVE_PROOF_BLOCKS:
        assert block in payload
    assert {
        key for key in payload if key.endswith("_proof") or key == "order_intent"
    } == REQUIRED_LIVE_PROOF_BLOCKS
    assert payload["runtime_adoption"] == "not_approved"
    assert payload["order_mutation_allowed"] is False
    assert payload["broker_mutation_attempted"] is False
    assert contract["context"]["runtime_adoption"] == "not_approved"
    assert contract["context"]["order_mutation_allowed"] is False
    assert contract["context"]["broker_mutation_attempted"] is False
    assert contract["context"]["live_runtime_claim_allowed"] is False


def test_claimable_live_fixture_has_required_sanitized_proof_fields() -> None:
    payload = load_fixture(CLAIMABLE_LIVE_FIXTURE)["candidate"]["surface_payload"]

    assert REQUIRED_ACCOUNT_PROOF_FIELDS <= payload["account_proof"].keys()
    assert REQUIRED_MARKET_PROOF_FIELDS <= payload["market_proof"].keys()
    assert REQUIRED_ORDER_INTENT_FIELDS <= payload["order_intent"].keys()
    assert REQUIRED_KILL_SWITCH_FIELDS <= payload["kill_switch_proof"].keys()
    assert REQUIRED_IDEMPOTENCY_FIELDS <= payload["idempotency_proof"].keys()
    assert payload["order_intent"]["order_mutation_allowed"] is False

    kill_switch = payload["kill_switch_proof"]
    assert REQUIRED_KILL_SWITCH_REFS <= kill_switch.keys()
    for field in REQUIRED_KILL_SWITCH_REFS:
        assert_sanitized_reference(kill_switch[field])
    assert_sanitized_reference(payload["idempotency_proof"]["duplicate_check_ref"])
    assert_sanitized_reference(payload["idempotency_proof"]["idempotency_key_hash"])
    assert_sanitized_reference(payload["order_intent"]["idempotency_key_hash"])
    assert forbidden_key_paths(payload) == []


def test_existing_live_not_claimable_fixture_keeps_no_runtime_claim_semantics() -> None:
    contract = load_fixture(LIVE_NOT_CLAIMABLE_FIXTURE)

    assert contract["candidate"]["surface"]["runtime_surface"] == "live"
    assert contract["candidate"]["surface"]["surface_status"] == "not_wired"
    assert contract["application"]["application_status"] == "not_claimable"
    assert contract["application"]["runtime_sizing_applied"] is False
    assert contract["application"]["sizing_effect"] == "not_applicable"
    assert contract["context"]["runtime_adoption"] == "not_approved"
    assert contract["context"]["live_runtime_claim_allowed"] is False


def test_invalid_live_fixtures_encode_the_locked_mutations() -> None:
    wrong_basis = load_fixture(INVALID_LIVE_SIZING_BASIS_FIXTURE)
    missing_account = load_fixture(INVALID_LIVE_MISSING_ACCOUNT_PROOF_FIXTURE)
    unsafe_secret = load_fixture(INVALID_LIVE_UNSAFE_SECRET_FIELD_FIXTURE)

    assert (
        wrong_basis["candidate"]["sizing"]["requested_size_basis"]
        == "unit_backtest_run"
    )

    missing_payload = missing_account["candidate"]["surface_payload"]
    assert "account_proof" not in missing_payload
    for block in (
        "market_proof",
        "order_intent",
        "kill_switch_proof",
        "idempotency_proof",
    ):
        assert block in missing_payload

    unsafe_account_proof = unsafe_secret["candidate"]["surface_payload"][
        "account_proof"
    ]
    assert unsafe_account_proof["raw_account_id"] == "example-raw-account-id"
    assert forbidden_key_paths(unsafe_secret) == [
        "candidate.surface_payload.account_proof.raw_account_id"
    ]

    for contract in (wrong_basis, missing_account, unsafe_secret):
        assert contract["candidate"]["surface"]["runtime_surface"] == "live"
        assert contract["candidate"]["surface"]["surface_status"] == "guarded"
        assert contract["candidate"]["surface_payload"]["runtime_adoption"] == "not_approved"
        assert contract["candidate"]["surface_payload"]["order_mutation_allowed"] is False
        assert contract["candidate"]["surface_payload"]["broker_mutation_attempted"] is False
        assert contract["context"]["runtime_adoption"] == "not_approved"
        assert contract["context"]["order_mutation_allowed"] is False
        assert contract["context"]["broker_mutation_attempted"] is False
