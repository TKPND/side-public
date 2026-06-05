"""Tests for the Phase 153 local live-preflight result builder."""

from __future__ import annotations

import ast
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.helpers import live_preflight_result_builder as builder
from tests.helpers.live_preflight_result_builder import (
    GuardViolation,
    InputProvenance,
    NoOrderPreflightInput,
    OutcomeSpec,
)


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "tests/helpers/live_preflight_result_builder.py"
LIVE_FIXTURE_PATH = (
    ROOT / "risk/contracts/v2/fixtures/valid/live_order_notional_claimable_valid.json"
)
TOP_LEVEL_KEYS = (
    "schema_version",
    "artifact_kind",
    "execution_mode",
    "result",
    "risk_gate",
    "live_preflight",
    "emission",
)
RISK_GATE = {
    "schema_version": "risk_contract.v2",
    "contract_version": "v2",
    "validator_result_schema_version": "risk_contract_validator_result.v2",
    "schema_ref": "risk/contracts/v2/risk_contract_v2.schema.json",
    "validated_schema_ref": "risk/contracts/v2/risk_contract_v2.schema.json",
    "validator": "scripts/validate_risk_contract.py",
}


def _safe_input(kind: str = "synthetic") -> NoOrderPreflightInput:
    return NoOrderPreflightInput(
        input_provenance=InputProvenance(
            kind=kind,
            runtime_evidence_claim=False,
            source_ref=f"phase153-{kind}-input",
        ),
        account_proof={
            "account_alias": "acct-alias-primary",
            "broker_alias": "broker-alias-sim",
            "account_snapshot_ref": f"acct-snapshot-sha256-{kind}",
            "snapshot_ts": "2026-05-19T00:00:00Z",
            "snapshot_max_age_ms": 30000,
            "base_currency": "USD",
            "equity_ref": f"equity-ref-{kind}",
            "cash_available_ref": f"cash-ref-{kind}",
            "buying_power_ref": f"buying-power-ref-{kind}",
            "open_exposure_digest": f"exposure-sha256-{kind}",
            "open_orders_digest": f"orders-sha256-{kind}",
        },
        market_proof={
            "symbol": "EURUSD",
            "market_snapshot_ref": f"market-snapshot-sha256-{kind}",
            "market_ts": "2026-05-19T00:00:01Z",
            "market_max_age_ms": 5000,
            "price_ref": f"quote-ref-{kind}",
            "spread_bps": 1.85,
            "price_source_alias": f"{kind}_quote",
        },
        order_intent={
            "side": "buy",
            "order_type": "limit",
            "time_in_force": "IOC",
            "requested_notional": 10000.0,
            "allowed_notional": 7500.0,
            "notional_currency": "USD",
            "notional_source": "manual_preflight",
            "price_bounds_ref": f"price-bounds-sha256-{kind}",
            "max_slippage_bps": 5.0,
            "idempotency_key_hash": f"idempotency-sha256-{kind}",
            "order_mutation_allowed": False,
        },
        kill_switch_proof={
            "global_gate_status": "passed",
            "global_gate_ref": f"gate-ref-sha256-{kind}-global",
            "strategy_gate_status": "passed",
            "strategy_gate_ref": f"gate-ref-sha256-{kind}-strategy",
            "symbol_gate_status": "passed",
            "symbol_gate_ref": f"gate-ref-sha256-{kind}-symbol",
            "broker_account_gate_status": "passed",
            "broker_account_gate_ref": f"gate-ref-sha256-{kind}-broker-account",
            "proof_ts": "2026-05-19T00:00:02Z",
            "proof_max_age_ms": 10000,
        },
        idempotency_proof={
            "candidate_identity_digest": f"candidate-identity-sha256-{kind}",
            "idempotency_key_hash": f"idempotency-sha256-{kind}",
            "duplicate_check_status": "passed",
            "duplicate_check_ref": f"duplicate-check-sha256-{kind}",
            "proof_ts": "2026-05-19T00:00:03Z",
            "proof_max_age_ms": 10000,
        },
    )


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


def _artifact() -> dict[str, Any]:
    return builder.build_no_order_artifact(_safe_input(), _outcome())


def _artifact_with_nested_value(path_parts: tuple[str, ...], value: Any) -> dict[str, Any]:
    artifact = _artifact()
    target: dict[str, Any] = artifact
    for part in path_parts[:-1]:
        target = target[part]
    target[path_parts[-1]] = value
    return artifact


def _assert_guard(
    error: pytest.ExceptionInfo[GuardViolation],
    *,
    path: str,
    reason: str,
) -> None:
    assert error.value.path == path
    assert error.value.reason == reason


def test_builds_in_memory_artifacts_for_allowed_provenance(tmp_path: Path) -> None:
    for kind in ("fixture", "synthetic", "sanitized"):
        artifact = builder.build_no_order_artifact(_safe_input(kind), _outcome())

        assert isinstance(artifact, dict)
        assert tuple(artifact) == TOP_LEVEL_KEYS
        assert artifact["schema_version"] == "side.live_preflight.result.v1"
        assert artifact["artifact_kind"] == "live_preflight_result"
        assert artifact["execution_mode"] == "no_order_preflight"
        assert artifact["risk_gate"] == RISK_GATE
        assert artifact["live_preflight"]["order_mutation_allowed"] is False
        assert artifact["live_preflight"]["order_mutation_attempted"] is False
        assert artifact["live_preflight"]["broker_mutation_attempted"] is False
        assert artifact["emission"] == {
            "persisted": True,
            "protected_output_root": False,
        }

    assert list(tmp_path.iterdir()) == []


def test_rejects_runtime_evidence_claim_and_unknown_provenance() -> None:
    runtime_claim = replace(
        _safe_input(),
        input_provenance=InputProvenance(
            kind="synthetic",
            runtime_evidence_claim=True,
            source_ref="phase153-runtime-claim",
        ),
    )

    with pytest.raises(GuardViolation) as runtime_error:
        builder.build_no_order_artifact(runtime_claim, _outcome())
    _assert_guard(
        runtime_error,
        path="input_provenance",
        reason="invalid_input_provenance",
    )

    unknown_kind = replace(
        _safe_input(),
        input_provenance=InputProvenance(
            kind="runtime",
            runtime_evidence_claim=False,
            source_ref="phase153-runtime-kind",
        ),
    )

    with pytest.raises(GuardViolation) as kind_error:
        builder.build_no_order_artifact(unknown_kind, _outcome())
    _assert_guard(
        kind_error,
        path="input_provenance",
        reason="invalid_input_provenance",
    )


def test_schema_semantic_and_public_material_guards_run_before_persistence() -> None:
    builder.validate_public_artifact(_artifact())
    builder.assert_public_material_safe(_artifact())

    mutation_attempt = _artifact()
    mutation_attempt["live_preflight"]["order_mutation_attempted"] = True
    with pytest.raises(GuardViolation) as mutation_error:
        builder.validate_public_artifact(mutation_attempt)
    _assert_guard(
        mutation_error,
        path="live_preflight.order_mutation_attempted",
        reason="schema_validation",
    )

    dangerous_persisted = builder.build_no_order_artifact(
        _safe_input(),
        _outcome(
            "failed",
            "unsafe_material",
            "raw_secret_or_private_identifier",
            "credential_hygiene",
            persisted=True,
        ),
    )
    with pytest.raises(GuardViolation) as semantic_error:
        builder.validate_public_artifact(dangerous_persisted)
    _assert_guard(
        semantic_error,
        path="result.failure_class",
        reason="semantic_violation",
    )

    direct_account_value = _artifact()
    direct_account_value["live_preflight"]["account_proof"]["equity"] = 250000.0
    with pytest.raises(GuardViolation) as value_error:
        builder.assert_public_material_safe(direct_account_value)
    _assert_guard(
        value_error,
        path="live_preflight.account_proof.equity",
        reason="raw_account_value",
    )

    raw_idempotency = _artifact()
    raw_idempotency["live_preflight"]["order_intent"][
        "idempotency_key"
    ] = "raw-idempotency-key"
    with pytest.raises(GuardViolation) as idempotency_error:
        builder.assert_public_material_safe(raw_idempotency)
    _assert_guard(
        idempotency_error,
        path="live_preflight.order_intent.idempotency_key",
        reason="unsafe_public_material",
    )


def test_public_material_guard_reports_path_and_reason_without_value() -> None:
    unsafe_samples = [
        (
            ("live_preflight", "account_proof", "raw_account_id"),
            "example-raw-account-id",
            "live_preflight.account_proof.raw_account_id",
            "unsafe_public_material",
        ),
        (
            ("live_preflight", "market_proof", "private_endpoint"),
            "https://private.internal/SECRET-TOKEN",
            "live_preflight.market_proof.private_endpoint",
            "unsafe_public_material",
        ),
        (
            ("live_preflight", "account_proof", "equity"),
            250000.0,
            "live_preflight.account_proof.equity",
            "raw_account_value",
        ),
    ]

    for path_parts, unsafe_value, expected_path, expected_reason in unsafe_samples:
        artifact = _artifact()
        target: dict[str, Any] = artifact
        for part in path_parts[:-1]:
            target = target[part]
        target[path_parts[-1]] = unsafe_value

        with pytest.raises(GuardViolation) as error:
            builder.assert_public_material_safe(artifact)

        _assert_guard(error, path=expected_path, reason=expected_reason)
        for forbidden in (
            "example-raw-account-id",
            "SECRET",
            "TOKEN",
            "250000",
        ):
            assert forbidden not in str(error.value)
            assert forbidden not in repr(error.value)


def test_public_material_guard_rejects_lprg_proof_01_unsafe_material() -> None:
    unsafe_samples = (
        (
            ("live_preflight", "market_proof", "raw_account_id"),
            "acct-alias-leak",
            "live_preflight.market_proof.raw_account_id",
            "unsafe_public_material",
            ("acct-alias-leak",),
        ),
        (
            ("live_preflight", "idempotency_proof", "account_id"),
            "acct-alias-leak",
            "live_preflight.idempotency_proof.account_id",
            "unsafe_public_material",
            ("acct-alias-leak",),
        ),
        (
            ("live_preflight", "account_proof", "equity"),
            250000.0,
            "live_preflight.account_proof.equity",
            "raw_account_value",
            ("250000",),
        ),
        (
            ("live_preflight", "account_proof", "cash_available"),
            100000.0,
            "live_preflight.account_proof.cash_available",
            "raw_account_value",
            ("100000",),
        ),
        (
            ("live_preflight", "account_proof", "buying_power"),
            150000.0,
            "live_preflight.account_proof.buying_power",
            "raw_account_value",
            ("150000",),
        ),
        (
            ("live_preflight", "market_proof", "access_token"),
            "public-ref-only",
            "live_preflight.market_proof.access_token",
            "unsafe_public_material",
            ("public-ref-only",),
        ),
        (
            ("live_preflight", "market_proof", "session_cookie"),
            "public-ref-only",
            "live_preflight.market_proof.session_cookie",
            "unsafe_public_material",
            ("public-ref-only",),
        ),
        (
            ("live_preflight", "market_proof", "private_key"),
            "public-ref-only",
            "live_preflight.market_proof.private_key",
            "unsafe_public_material",
            ("public-ref-only",),
        ),
        (
            ("live_preflight", "market_proof", "api_key"),
            "public-ref-only",
            "live_preflight.market_proof.api_key",
            "unsafe_public_material",
            ("public-ref-only",),
        ),
        (
            ("live_preflight", "market_proof", "password"),
            "public-ref-only",
            "live_preflight.market_proof.password",
            "unsafe_public_material",
            ("public-ref-only",),
        ),
        (
            ("live_preflight", "market_proof", "credential_ref"),
            "public-ref-only",
            "live_preflight.market_proof.credential_ref",
            "unsafe_public_material",
            ("public-ref-only",),
        ),
        (
            ("live_preflight", "market_proof", "secret_ref"),
            "public-ref-only",
            "live_preflight.market_proof.secret_ref",
            "unsafe_public_material",
            ("public-ref-only",),
        ),
        (
            ("live_preflight", "market_proof", "broker_secret_ref"),
            "public-ref-only",
            "live_preflight.market_proof.broker_secret_ref",
            "unsafe_public_material",
            ("public-ref-only",),
        ),
        (
            ("live_preflight", "market_proof", "endpoint"),
            "public-ref-only",
            "live_preflight.market_proof.endpoint",
            "unsafe_public_material",
            ("public-ref-only",),
        ),
        (
            ("live_preflight", "market_proof", "private_endpoint"),
            "public-ref-only",
            "live_preflight.market_proof.private_endpoint",
            "unsafe_public_material",
            ("public-ref-only",),
        ),
        (
            ("live_preflight", "market_proof", "price_source_alias"),
            "https://private.internal/secret-token",
            "live_preflight.market_proof.price_source_alias",
            "unsafe_public_material",
            ("private.internal", "secret-token"),
        ),
        (
            ("live_preflight", "order_intent", "idempotency_key"),
            "phase156-idempotency-ref",
            "live_preflight.order_intent.idempotency_key",
            "unsafe_public_material",
            ("phase156-idempotency-ref",),
        ),
        (
            ("live_preflight", "idempotency_proof", "raw_idempotency_key"),
            "phase156-idempotency-ref",
            "live_preflight.idempotency_proof.raw_idempotency_key",
            "unsafe_public_material",
            ("phase156-idempotency-ref",),
        ),
    )

    for path_parts, value, expected_path, expected_reason, forbidden_values in unsafe_samples:
        with pytest.raises(GuardViolation) as error:
            builder.assert_public_material_safe(
                _artifact_with_nested_value(path_parts, value),
            )

        _assert_guard(error, path=expected_path, reason=expected_reason)
        for forbidden in forbidden_values:
            assert forbidden not in str(error.value)
            assert forbidden not in repr(error.value)

    allowed = _artifact()
    allowed["live_preflight"]["order_intent"][
        "idempotency_key_hash"
    ] = "idempotency-sha256-safe"
    allowed["live_preflight"]["idempotency_proof"][
        "duplicate_check_ref"
    ] = "duplicate-check-sha256-safe"
    builder.assert_public_material_safe(allowed)


def test_persist_no_order_artifact_writes_only_after_all_guards_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "phase153" / "passed.json"
    events: list[str] = []
    original_validate = builder.validate_public_artifact
    original_public_guard = builder.assert_public_material_safe
    original_replace = os.replace

    def validate_probe(artifact: dict[str, Any]) -> None:
        events.append("schema_semantic")
        original_validate(artifact)

    def public_guard_probe(artifact: dict[str, Any]) -> None:
        events.append("public_material")
        original_public_guard(artifact)

    def replace_probe(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        events.append("replace")
        original_replace(src, dst)

    monkeypatch.setattr(builder, "validate_public_artifact", validate_probe)
    monkeypatch.setattr(builder, "assert_public_material_safe", public_guard_probe)
    monkeypatch.setattr(builder.os, "replace", replace_probe)

    result = builder.persist_no_order_artifact(
        _artifact(),
        output_path,
        allowed_root=tmp_path,
    )

    assert result == output_path
    assert events == ["schema_semantic", "public_material", "replace"]
    assert json.loads(output_path.read_text(encoding="utf-8")) == _artifact()
    assert not list(output_path.parent.glob("*.tmp"))


def test_protected_output_root_fails_before_filesystem_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected_output = (
        ROOT / ".planning/milestones/phase153-protected/generated.json"
    )
    assert not protected_output.parent.exists()

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("path guard should fail before validation or writes")

    monkeypatch.setattr(builder, "validate_public_artifact", fail_if_called)
    monkeypatch.setattr(builder, "assert_public_material_safe", fail_if_called)
    monkeypatch.setattr(builder.os, "replace", fail_if_called)

    with pytest.raises(GuardViolation) as error:
        builder.persist_no_order_artifact(
            _artifact(),
            protected_output,
            allowed_root=ROOT,
        )

    _assert_guard(
        error,
        path=protected_output.as_posix(),
        reason="protected_output_root",
    )
    assert not protected_output.parent.exists()
    assert not protected_output.exists()
    assert not protected_output.with_suffix(".json.tmp").exists()


def test_guard_failures_do_not_persist_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        builder.os,
        "replace",
        lambda *_args, **_kwargs: pytest.fail("replace must not be reached"),
    )

    schema_failure = _artifact()
    schema_failure["unexpected"] = "extra"

    semantic_failure = builder.build_no_order_artifact(
        _safe_input(),
        _outcome(
            "failed",
            "mutation_attempt",
            "order_mutation_attempted",
            "no_order_assertion",
            persisted=True,
        ),
    )

    public_material_failure = _artifact()
    public_material_failure["live_preflight"]["account_proof"][
        "account_alias"
    ] = "acct-SECRET"

    for name, artifact in (
        ("schema", schema_failure),
        ("semantic", semantic_failure),
        ("public", public_material_failure),
    ):
        output_path = tmp_path / name / "artifact.json"
        with pytest.raises(GuardViolation):
            builder.persist_no_order_artifact(
                artifact,
                output_path,
                allowed_root=tmp_path,
            )
        assert not output_path.parent.exists()
        assert not output_path.exists()
        assert not list(tmp_path.glob(f"{name}/*.tmp"))


def test_result_classification_matrix_covers_representative_outcomes(
    tmp_path: Path,
) -> None:
    matrix = [
        (
            "passed",
            _safe_input("fixture"),
            _outcome("passed", None, None, None, persisted=True),
        ),
        (
            "risk_stopped",
            _safe_input("fixture"),
            _outcome(
                "risk_stopped",
                "risk_decision",
                "kill_switch_active",
                "risk_decision",
                persisted=True,
            ),
        ),
        (
            "failed_account_proof_stale",
            _safe_input("sanitized"),
            _outcome(
                "failed",
                "stale_proof",
                "account_proof_stale",
                "account_proof",
                persisted=True,
            ),
        ),
        (
            "failed_duplicate_idempotency",
            _safe_input("synthetic"),
            _outcome(
                "failed",
                "duplicate_idempotency",
                "idempotency_replay_detected",
                "idempotency",
                persisted=True,
            ),
        ),
    ]

    for name, input_data, outcome in matrix:
        output_path = tmp_path / f"{name}.json"
        builder.build_and_persist_no_order_artifact(
            input_data,
            outcome,
            output_path,
            allowed_root=tmp_path,
        )

        artifact = json.loads(output_path.read_text(encoding="utf-8"))
        builder.validate_public_artifact(artifact)
        builder.assert_public_material_safe(artifact)
        assert artifact["result"] == {
            "status": outcome.status,
            "failure_class": outcome.failure_class,
            "failure_reason": outcome.failure_reason,
            "terminal_gate": outcome.terminal_gate,
        }
        assert artifact["emission"]["persisted"] is True
        assert artifact["live_preflight"]["order_mutation_allowed"] is False
        assert artifact["live_preflight"]["order_mutation_attempted"] is False
        assert artifact["live_preflight"]["broker_mutation_attempted"] is False


def test_v8_5_fixture_mapping_never_copies_raw_account_values() -> None:
    fixture = json.loads(LIVE_FIXTURE_PATH.read_text(encoding="utf-8"))
    mapped_input = builder.map_v8_5_live_fixture_input(fixture)

    assert mapped_input.input_provenance.kind == "fixture"
    assert mapped_input.input_provenance.runtime_evidence_claim is False

    artifact = builder.build_no_order_artifact(mapped_input, _outcome())
    builder.validate_public_artifact(artifact)
    builder.assert_public_material_safe(artifact)

    account_proof = artifact["live_preflight"]["account_proof"]
    assert "equity" not in account_proof
    assert "cash_available" not in account_proof
    assert "buying_power" not in account_proof
    assert "account_id" not in account_proof
    assert "raw_account_id" not in account_proof
    assert {
        "equity_ref",
        "cash_available_ref",
        "buying_power_ref",
    } <= account_proof.keys()

    serialized_public_proof = json.dumps(
        artifact["live_preflight"],
        sort_keys=True,
    )
    for raw_value in ("250000", "100000", "150000"):
        assert raw_value not in serialized_public_proof


def test_builder_has_no_runtime_or_broker_surface() -> None:
    source = HELPER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    forbidden_import_modules = {
        "scripts.generate_risk_contract_v2_adoption_closure_audit",
    }
    forbidden_import_roots = {
        "requests",
        "urllib",
        "httpx",
        "socket",
        "scripts",
        "side_cli",
        "side_engine",
        "broker",
        "account_fetcher",
    }
    assert forbidden_import_modules.isdisjoint(imports)
    assert forbidden_import_roots.isdisjoint({name.split(".")[0] for name in imports})
    assert 'if __name__ == "__main__"' not in source
    assert "argparse" not in source

    changed_surface = {
        "tests/helpers/__init__.py",
        "tests/helpers/live_preflight_result_builder.py",
        "tests/test_live_preflight_result_builder.py",
    }
    assert HELPER_PATH.relative_to(ROOT).as_posix() in changed_surface
