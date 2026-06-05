"""Phase 160 integration tests for projector output into the builder."""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.helpers import live_preflight_projector_builder as bridge
from tests.helpers import live_preflight_account_proof_projector as projector
from tests.helpers import live_preflight_result_builder as builder
from tests.helpers.live_preflight_account_proof_projector import (
    AccountProofProjectorResult,
    ProjectorViolation,
)
from tests.helpers.live_preflight_result_builder import OutcomeSpec


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_HELPER_PATH = ROOT / "tests/helpers/live_preflight_projector_builder.py"
SCHEMA_PATH = ROOT / "docs/contracts/live_preflight_result_v1.schema.json"
NON_ACCOUNT_PROOF_BLOCKS = (
    "market_proof",
    "order_intent",
    "kill_switch_proof",
    "idempotency_proof",
)


def _deep_public_material(depth: int) -> dict[str, Any]:
    value: object = "public-redacted"
    for _ in range(depth):
        value = {"child": value}
    assert isinstance(value, dict)
    return value


def _now(*, milliseconds_after_snapshot: int = 10000) -> datetime:
    return datetime(2026, 5, 28, tzinfo=timezone.utc) + timedelta(
        milliseconds=milliseconds_after_snapshot
    )


def _account_proof(
    *,
    snapshot_ts: str = "2026-05-28T00:00:00Z",
    snapshot_max_age_ms: int = 30000,
    omit_snapshot_ts: bool = False,
    omit_snapshot_max_age_ms: bool = False,
) -> dict[str, Any]:
    account_proof = {
        "account_alias": "public-account-alias",
        "broker_alias": "public-broker-alias",
        "account_snapshot_ref": "public-account-snapshot-ref-phase160bridge001",
        "snapshot_ts": snapshot_ts,
        "snapshot_max_age_ms": snapshot_max_age_ms,
        "base_currency": "USD",
        "equity_ref": "public-equity-ref-phase160bridge001",
        "cash_available_ref": "public-cash-available-ref-phase160bridge001",
        "buying_power_ref": "public-buying-power-ref-phase160bridge001",
        "open_exposure_digest": "public-open-exposure-digest-phase160bridge001",
        "open_orders_digest": "public-open-orders-digest-phase160bridge001",
        "staleness_ref": "public-staleness-ref-phase160bridge001",
    }
    if omit_snapshot_ts:
        account_proof.pop("snapshot_ts")
    if omit_snapshot_max_age_ms:
        account_proof.pop("snapshot_max_age_ms")
    return account_proof


def _projector_result(
    *,
    provenance_kind: str = "synthetic_public",
    source_ref: str = "phase160-synthetic-public-input",
    account_proof: dict[str, Any] | None = None,
) -> AccountProofProjectorResult:
    return AccountProofProjectorResult(
        account_proof=account_proof or _account_proof(),
        evidence_label="non-runtime/non-broker/non-account-fetch evidence",
        input_provenance_kind=provenance_kind,
        source_ref=source_ref,
        runtime_evidence_claim=False,
        broker_evidence_claim=False,
        account_fetch_evidence_claim=False,
    )


def _safe_market_proof() -> dict[str, Any]:
    return {
        "symbol": "EURUSD",
        "market_snapshot_ref": "public-market-snapshot-ref-phase160bridge001",
        "market_ts": "2026-05-28T00:00:01Z",
        "market_max_age_ms": 5000,
        "price_ref": "public-price-ref-phase160bridge001",
        "spread_bps": 1.85,
        "price_source_alias": "phase160_quote",
    }


def _safe_order_intent() -> dict[str, Any]:
    return {
        "side": "buy",
        "order_type": "limit",
        "time_in_force": "IOC",
        "requested_notional": 10000.0,
        "allowed_notional": 7500.0,
        "notional_currency": "USD",
        "notional_source": "manual_preflight",
        "price_bounds_ref": "public-price-bounds-ref-phase160bridge001",
        "max_slippage_bps": 5.0,
        "idempotency_key_hash": "public-idempotency-digest-phase160bridge001",
        "order_mutation_allowed": False,
    }


def _safe_kill_switch_proof() -> dict[str, Any]:
    return {
        "global_gate_status": "passed",
        "global_gate_ref": "public-global-gate-ref-phase160bridge001",
        "strategy_gate_status": "passed",
        "strategy_gate_ref": "public-strategy-gate-ref-phase160bridge001",
        "symbol_gate_status": "passed",
        "symbol_gate_ref": "public-symbol-gate-ref-phase160bridge001",
        "broker_account_gate_status": "passed",
        "broker_account_gate_ref": (
            "public-broker-account-gate-ref-phase160bridge001"
        ),
        "proof_ts": "2026-05-28T00:00:02Z",
        "proof_max_age_ms": 10000,
    }


def _safe_idempotency_proof() -> dict[str, Any]:
    return {
        "candidate_identity_digest": (
            "public-candidate-identity-digest-phase160bridge001"
        ),
        "idempotency_key_hash": "public-idempotency-digest-phase160bridge001",
        "duplicate_check_status": "passed",
        "duplicate_check_ref": "public-duplicate-check-ref-phase160bridge001",
        "proof_ts": "2026-05-28T00:00:03Z",
        "proof_max_age_ms": 10000,
    }


def _outcome(
    status: str = "passed",
    failure_class: str | None = None,
    failure_reason: str | None = None,
    terminal_gate: str | None = None,
    *,
    persisted: bool = True,
) -> OutcomeSpec:
    return OutcomeSpec(
        status=status,
        failure_class=failure_class,
        failure_reason=failure_reason,
        terminal_gate=terminal_gate,
        persisted=persisted,
    )


def _build_artifact(
    projector_result: AccountProofProjectorResult | None = None,
    *,
    now: datetime | None = None,
    outcome: OutcomeSpec | None = None,
    order_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return bridge.build_no_order_artifact_from_projector(
        projector_result or _projector_result(),
        now=now or _now(),
        outcome=outcome or _outcome(),
        market_proof=_safe_market_proof(),
        order_intent=order_intent or _safe_order_intent(),
        kill_switch_proof=_safe_kill_switch_proof(),
        idempotency_proof=_safe_idempotency_proof(),
    )


def _assert_projector_violation(
    error: pytest.ExceptionInfo[ProjectorViolation],
    *,
    path: str,
    reason: str,
    category: str = "projector_contract",
) -> None:
    assert error.value.path == path
    assert error.value.reason == reason
    assert error.value.category == category


def test_projector_validation_and_freshness_run_before_builder_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    original_project = bridge.project_account_proof
    original_freshness = bridge.classify_account_proof_freshness
    original_build = bridge.build_no_order_artifact

    def project_probe(input_data: object) -> object:
        events.append("projector_validation")
        return original_project(input_data)

    def freshness_probe(account_proof: dict[str, Any], *, now: datetime) -> object:
        events.append("freshness")
        return original_freshness(account_proof, now=now)

    def build_probe(input_data: object, outcome: OutcomeSpec) -> dict[str, Any]:
        events.append("builder")
        return original_build(input_data, outcome)

    monkeypatch.setattr(bridge, "project_account_proof", project_probe)
    monkeypatch.setattr(bridge, "classify_account_proof_freshness", freshness_probe)
    monkeypatch.setattr(bridge, "build_no_order_artifact", build_probe)

    artifact = _build_artifact()

    assert events == ["projector_validation", "freshness", "builder"]
    builder.validate_public_artifact(artifact)
    builder.assert_public_material_safe(artifact)


def test_stale_projector_freshness_maps_to_public_account_proof_failure() -> None:
    artifact = _build_artifact(
        now=_now(milliseconds_after_snapshot=30001),
        outcome=_outcome("passed"),
    )

    assert artifact["result"] == {
        "status": "failed",
        "failure_class": "stale_proof",
        "failure_reason": "account_proof_stale",
        "terminal_gate": "account_proof",
    }
    assert "age_exceeds_snapshot_max_age_ms" not in json.dumps(
        artifact,
        sort_keys=True,
    )


def test_current_projector_freshness_preserves_caller_outcome() -> None:
    artifact = _build_artifact(
        outcome=_outcome(
            "risk_stopped",
            "risk_decision",
            "kill_switch_active",
            "risk_decision",
        )
    )

    assert artifact["result"] == {
        "status": "risk_stopped",
        "failure_class": "risk_decision",
        "failure_reason": "kill_switch_active",
        "terminal_gate": "risk_decision",
    }


def test_invalid_projector_result_fails_before_builder_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_builder_reached(*_args: object, **_kwargs: object) -> None:
        pytest.fail("builder construction must not be reached")

    monkeypatch.setattr(bridge, "build_no_order_artifact", fail_if_builder_reached)
    invalid_account_proof = _account_proof()
    invalid_account_proof["account_alias"] = "synthetic-secret-fragment"

    with pytest.raises(ProjectorViolation) as error:
        _build_artifact(_projector_result(account_proof=invalid_account_proof))

    _assert_projector_violation(
        error,
        path="account_proof.account_alias",
        reason="invalid_public_alias",
    )


def test_wrapper_does_not_import_or_call_legacy_v8_5_fixture_mapper() -> None:
    source = BRIDGE_HELPER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = set()
    call_names = set()
    import_roots = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                import_roots.add(alias.name.split(".")[0])
                imported_names.add(alias.asname or alias.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                import_roots.add(node.module.split(".")[0])
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                call_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                call_names.add(node.func.attr)

    assert "map_v8_5_live_fixture_input" not in imported_names
    assert "map_v8_5_live_fixture_input" not in call_names
    assert "_ref_digest" not in imported_names
    assert "_ref_digest" not in call_names
    assert "hashlib" not in import_roots

    artifact = _build_artifact(
        _projector_result(provenance_kind="fixture_sanitized_public")
    )
    assert artifact["live_preflight"]["account_proof"] == _account_proof()
    assert artifact["result"]["status"] == "passed"


def test_bridge_reuses_projector_public_material_guard_fragments() -> None:
    assert bridge._UNSAFE_KEY_FRAGMENTS is projector._UNSAFE_KEY_FRAGMENTS
    assert bridge._UNSAFE_VALUE_FRAGMENTS is projector._UNSAFE_VALUE_FRAGMENTS
    assert bridge._RAW_DERIVATION_FRAGMENT_PATTERN == (
        projector._RAW_DERIVATION_FRAGMENT_PATTERN
    )


def test_bridge_reuses_projector_key_classifier_for_shared_key_logic() -> None:
    assert bridge._projector_unsafe_key_reason is projector._unsafe_key_reason
    for key in (
        "rawAccountId",
        "account_number",
        "credentialToken",
        "privateEndpointRef",
        "safe_public_ref",
    ):
        assert bridge._unsafe_key_reason(key) == projector._unsafe_key_reason(key)


def test_bridge_public_taxonomy_tracks_frozen_schema_defs() -> None:
    schema_defs = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]
    expected_string_paths: set[tuple[str, str]] = set()
    expected_numeric_paths: set[tuple[str, str]] = set()
    expected_boolean_paths: dict[tuple[str, str], bool] = {}
    expected_enum_by_path: dict[tuple[str, str], set[str]] = {}

    for block_name in NON_ACCOUNT_PROOF_BLOCKS:
        for field_name, field_schema in schema_defs[block_name]["properties"].items():
            path = (block_name, field_name)
            field_ref = field_schema.get("$ref")
            if field_ref in {"#/$defs/non_empty_string", "#/$defs/timestamp"}:
                expected_string_paths.add(path)
            elif field_schema.get("type") == "string" and "enum" in field_schema:
                expected_string_paths.add(path)
                expected_enum_by_path[path] = set(field_schema["enum"])
            elif field_ref in {
                "#/$defs/non_negative_number",
                "#/$defs/non_negative_integer",
            }:
                expected_numeric_paths.add(path)
            elif field_schema.get("type") == "boolean":
                expected_boolean_paths[path] = field_schema["const"]

    bridge_enum_by_path = {
        path: bridge._PUBLIC_STRING_ENUMS[kind]
        for path, kind in bridge._PUBLIC_STRING_VALUE_KINDS.items()
        if kind in bridge._PUBLIC_STRING_ENUMS
    }
    result_schema = schema_defs["result"]["properties"]

    assert set(bridge._PUBLIC_STRING_VALUE_KINDS) == expected_string_paths
    assert bridge._ALLOWED_NUMERIC_VALUE_PATHS == expected_numeric_paths
    assert bridge._ALLOWED_BOOLEAN_VALUE_PATHS == expected_boolean_paths
    assert bridge_enum_by_path == expected_enum_by_path
    assert bridge._ALLOWED_STATUSES == set(result_schema["status"]["enum"])
    assert bridge._ALLOWED_FAILURE_CLASSES == set(
        result_schema["failure_class"]["enum"]
    )
    assert bridge._ALLOWED_FAILURE_REASONS == set(
        result_schema["failure_reason"]["enum"]
    )
    assert bridge._ALLOWED_TERMINAL_GATES == set(
        result_schema["terminal_gate"]["enum"]
    )
    assert {
        reason
        for _, _, reason, _ in bridge._ALLOWED_RESULT_TUPLES
        if reason is not None
    } == bridge._ALLOWED_FAILURE_REASONS - {None}


def test_wrapper_artifacts_validate_and_persist_through_existing_guards(
    tmp_path: Path,
) -> None:
    artifact = _build_artifact()
    builder.validate_public_artifact(artifact)
    builder.assert_public_material_safe(artifact)

    output_path = tmp_path / "phase160" / "passed.json"
    persisted = builder.persist_no_order_artifact(
        artifact,
        output_path,
        allowed_root=tmp_path,
    )

    assert persisted == output_path
    assert json.loads(output_path.read_text(encoding="utf-8")) == artifact


def test_returned_projector_artifact_runs_schema_and_public_material_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    original_validate = bridge.validate_public_artifact
    original_public_guard = bridge.assert_public_material_safe

    def validate_probe(artifact: dict[str, Any]) -> None:
        events.append("schema_semantic")
        original_validate(artifact)

    def public_guard_probe(artifact: dict[str, Any]) -> None:
        events.append("public_material")
        original_public_guard(artifact)

    monkeypatch.setattr(bridge, "validate_public_artifact", validate_probe)
    monkeypatch.setattr(bridge, "assert_public_material_safe", public_guard_probe)

    builder.validate_public_artifact(_build_artifact())

    assert events == ["schema_semantic", "public_material"]


@pytest.mark.parametrize(
    ("block_name", "override", "expected_path"),
    (
        pytest.param(
            "market_proof",
            {"price_source_alias": "https://private.internal/secret-token"},
            "market_proof.price_source_alias",
            id="market_proof_url_secret_value_rejects",
        ),
        pytest.param(
            "order_intent",
            {"idempotency_key_hash": "raw-idempotency-key"},
            "order_intent.idempotency_key_hash",
            id="order_intent_raw_idempotency_value_rejects",
        ),
        pytest.param(
            "kill_switch_proof",
            {"global_gate_ref": "public-ref-generated-from-raw-scalar-250000"},
            "kill_switch_proof.global_gate_ref",
            id="kill_switch_raw_derivation_value_rejects",
        ),
        pytest.param(
            "idempotency_proof",
            {"duplicate_check_ref": "250000"},
            "idempotency_proof.duplicate_check_ref",
            id="idempotency_account_amount_shape_value_rejects",
        ),
        pytest.param(
            "idempotency_proof",
            {"duplicate_check_ref": 250000.0},
            "idempotency_proof.duplicate_check_ref",
            id="idempotency_numeric_account_amount_value_rejects",
        ),
        pytest.param(
            "market_proof",
            {"price_ref": None},
            "market_proof.price_ref",
            id="market_proof_unknown_leaf_type_rejects",
        ),
        pytest.param(
            "market_proof",
            {"price_source_alias": "raw-account-id-12345"},
            "market_proof.price_source_alias",
            id="market_proof_raw_account_id_value_rejects",
        ),
        pytest.param(
            "order_intent",
            {"price_bounds_ref": "raw-account-id-12345"},
            "order_intent.price_bounds_ref",
            id="order_intent_raw_account_id_value_rejects",
        ),
        pytest.param(
            "kill_switch_proof",
            {"global_gate_ref": "raw-account-id-12345"},
            "kill_switch_proof.global_gate_ref",
            id="kill_switch_raw_account_id_value_rejects",
        ),
        pytest.param(
            "idempotency_proof",
            {"duplicate_check_ref": "raw-account-id-12345"},
            "idempotency_proof.duplicate_check_ref",
            id="idempotency_raw_account_id_value_rejects",
        ),
        pytest.param(
            "market_proof",
            {"price_source_alias": "10.0.0.5"},
            "market_proof.price_source_alias",
            id="market_proof_private_ipv4_value_rejects",
        ),
        pytest.param(
            "kill_switch_proof",
            {"global_gate_ref": "fix1.corp.example.com"},
            "kill_switch_proof.global_gate_ref",
            id="kill_switch_internal_hostname_value_rejects",
        ),
        pytest.param(
            "market_proof",
            {"price_source_alias": False},
            "market_proof.price_source_alias",
            id="market_proof_boolean_string_field_rejects",
        ),
    ),
)
def test_unsafe_non_account_proof_blocks_fail_before_builder_construction(
    monkeypatch: pytest.MonkeyPatch,
    block_name: str,
    override: dict[str, Any],
    expected_path: str,
) -> None:
    def fail_if_builder_reached(*_args: object, **_kwargs: object) -> None:
        pytest.fail("builder construction must not be reached")

    monkeypatch.setattr(bridge, "build_no_order_artifact", fail_if_builder_reached)
    kwargs = {
        "projector_result": _projector_result(),
        "now": _now(),
        "outcome": _outcome(),
        "market_proof": _safe_market_proof(),
        "order_intent": _safe_order_intent(),
        "kill_switch_proof": _safe_kill_switch_proof(),
        "idempotency_proof": _safe_idempotency_proof(),
    }
    kwargs[block_name] = {**kwargs[block_name], **override}

    with pytest.raises(ProjectorViolation) as error:
        bridge.build_no_order_artifact_from_projector(**kwargs)

    _assert_projector_violation(
        error,
        path=expected_path,
        reason="unsafe_public_material",
    )


def test_deep_non_account_proof_material_fails_before_builder_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_builder_reached(*_args: object, **_kwargs: object) -> None:
        pytest.fail("builder construction must not be reached")

    monkeypatch.setattr(bridge, "build_no_order_artifact", fail_if_builder_reached)
    market_proof = _safe_market_proof()
    market_proof["price_ref"] = _deep_public_material(70)

    with pytest.raises(ProjectorViolation) as error:
        bridge.build_no_order_artifact_from_projector(
            _projector_result(),
            now=_now(),
            outcome=_outcome(),
            market_proof=market_proof,
            order_intent=_safe_order_intent(),
            kill_switch_proof=_safe_kill_switch_proof(),
            idempotency_proof=_safe_idempotency_proof(),
        )

    assert error.value.reason == "unsafe_public_material"
    assert error.value.path.endswith("<max_depth>")


def test_non_public_outcome_reason_fails_before_builder_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_builder_reached(*_args: object, **_kwargs: object) -> None:
        pytest.fail("builder construction must not be reached")

    monkeypatch.setattr(bridge, "build_no_order_artifact", fail_if_builder_reached)

    with pytest.raises(ProjectorViolation) as error:
        _build_artifact(
            outcome=_outcome(
                "risk_stopped",
                "risk_decision",
                "https://private.internal/secret-token",
                "risk_decision",
            )
        )

    _assert_projector_violation(
        error,
        path="outcome.failure_reason",
        reason="invalid_outcome",
    )


def test_incoherent_public_outcome_tuple_fails_before_builder_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_builder_reached(*_args: object, **_kwargs: object) -> None:
        pytest.fail("builder construction must not be reached")

    monkeypatch.setattr(bridge, "build_no_order_artifact", fail_if_builder_reached)

    with pytest.raises(ProjectorViolation) as error:
        _build_artifact(
            outcome=_outcome(
                "passed",
                "stale_proof",
                "account_proof_stale",
                "account_proof",
            )
        )

    _assert_projector_violation(
        error,
        path="outcome",
        reason="invalid_outcome",
    )


def test_input_only_projector_bridge_is_private() -> None:
    assert not hasattr(bridge, "build_no_order_input_from_projector")
    assert hasattr(bridge, "_build_no_order_input_from_projector")


@pytest.mark.parametrize(
    (
        "account_proof",
        "milliseconds_after_snapshot",
        "expected_path",
        "expected_reason",
    ),
    (
        pytest.param(
            _account_proof(snapshot_ts="2026-05-28T00:00:01Z"),
            0,
            "account_proof.snapshot_ts",
            "future_snapshot_ts",
            id="future_snapshot_ts_rejects_before_builder",
        ),
        pytest.param(
            _account_proof(snapshot_ts="2026-05-28 00:00:00"),
            0,
            "account_proof.snapshot_ts",
            "invalid_timestamp",
            id="malformed_snapshot_ts_rejects_before_builder",
        ),
        pytest.param(
            _account_proof(omit_snapshot_ts=True),
            0,
            "account_proof.snapshot_ts",
            "missing_timestamp",
            id="missing_snapshot_ts_rejects_before_builder",
        ),
        pytest.param(
            _account_proof(snapshot_max_age_ms=0),
            0,
            "account_proof.snapshot_max_age_ms",
            "invalid_snapshot_max_age_ms",
            id="non_positive_snapshot_max_age_rejects_before_builder",
        ),
    ),
)
def test_freshness_failures_stop_before_builder_construction(
    monkeypatch: pytest.MonkeyPatch,
    account_proof: dict[str, Any],
    milliseconds_after_snapshot: int,
    expected_path: str,
    expected_reason: str,
) -> None:
    def fail_if_builder_reached(*_args: object, **_kwargs: object) -> None:
        pytest.fail("builder construction must not be reached")

    monkeypatch.setattr(bridge, "build_no_order_artifact", fail_if_builder_reached)

    with pytest.raises(ProjectorViolation) as error:
        _build_artifact(
            _projector_result(account_proof=account_proof),
            now=_now(milliseconds_after_snapshot=milliseconds_after_snapshot),
        )

    _assert_projector_violation(
        error,
        path=expected_path,
        reason=expected_reason,
    )


def test_projector_failures_mutation_attempts_and_protected_roots_do_not_persist(
    tmp_path: Path,
) -> None:
    invalid_account_proof = _account_proof()
    invalid_account_proof["account_alias"] = "synthetic-secret-fragment"
    invalid_output = tmp_path / "invalid" / "artifact.json"

    with pytest.raises(ProjectorViolation):
        _build_artifact(_projector_result(account_proof=invalid_account_proof))
    assert not invalid_output.exists()
    assert not invalid_output.parent.exists()

    mutation_intent = _safe_order_intent()
    mutation_intent["order_mutation_attempted"] = True
    mutation_output = tmp_path / "mutation" / "artifact.json"
    with pytest.raises(ProjectorViolation):
        _build_artifact(order_intent=mutation_intent)
    assert not mutation_output.exists()
    assert not mutation_output.parent.exists()
    assert not mutation_output.with_suffix(".json.tmp").exists()

    protected_output = ROOT / ".planning/milestones/phase160-protected/generated.json"
    assert not protected_output.parent.exists()
    with pytest.raises(builder.GuardViolation):
        builder.persist_no_order_artifact(
            _build_artifact(),
            protected_output,
            allowed_root=ROOT,
        )
    assert not protected_output.parent.exists()
    assert not protected_output.exists()
    assert not protected_output.with_suffix(".json.tmp").exists()


def test_phase160_source_absence_guards() -> None:
    source = BRIDGE_HELPER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    forbidden_import_roots = {
        "requests",
        "urllib",
        "httpx",
        "socket",
        "subprocess",
        "os",
        "dotenv",
        "keyring",
        "google.cloud.secretmanager",
        "hashlib",
    }
    assert forbidden_import_roots.isdisjoint({name.split(".")[0] for name in imports})

    for token in (
        "map_v8_5_live_fixture_input",
        "_ref_digest",
        "hashlib",
        "sha256",
        "hexdigest",
        "side live",
        "broker_adapter",
        "broker_client",
        "place_order",
        "submit_order",
        "cancel_order",
        "fetch_account",
        "account_fetcher",
        "runtime_public_emission",
        "requests.",
        "httpx.",
        "socket.",
        "subprocess.",
        "os.environ",
        "os.getenv",
        "SecretManagerServiceClient",
        "Path.write_text",
        "Path.write_bytes",
        ".write_text(",
        ".write_bytes(",
        "open(",
        "json.dump(",
        "save_",
    ):
        assert token not in source
