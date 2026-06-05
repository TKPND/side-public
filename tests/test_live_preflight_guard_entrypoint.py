"""Phase 155 contract tests for the no-order guard entrypoint harness."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scripts.generate_risk_contract_v2_adoption_closure_audit import (
    live_runtime_surface_absent_check,
)
from tests.helpers import live_preflight_result_builder as builder
from tests.helpers.live_preflight_guard_entrypoint import (
    GuardEntrypointResult,
    GuardProviderState,
    run_no_order_guard,
)
from tests.helpers.live_preflight_result_builder import (
    GuardViolation,
    InputProvenance,
    NoOrderPreflightInput,
)


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "tests/helpers/live_preflight_guard_entrypoint.py"
EVIDENCE_LABEL = "non-runtime/live-broker evidence"


def _safe_input(kind: str = "synthetic") -> NoOrderPreflightInput:
    return NoOrderPreflightInput(
        input_provenance=InputProvenance(
            kind=kind,
            runtime_evidence_claim=False,
            source_ref=f"phase155-{kind}-input",
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


def _assert_guard(
    error: pytest.ExceptionInfo[GuardViolation],
    *,
    path: str,
    reason: str,
) -> None:
    assert error.value.path == path
    assert error.value.reason == reason


def _assert_result(
    result: GuardEntrypointResult,
    *,
    status: str,
    failure_class: str | None,
    failure_reason: str | None,
    terminal_gate: str | None,
) -> None:
    assert result.status == status
    assert result.failure_class == failure_class
    assert result.failure_reason == failure_reason
    assert result.terminal_gate == terminal_gate
    assert result.persisted_to_disk is False


def _fail_if_artifact_constructed(*_args: object, **_kwargs: object) -> None:
    pytest.fail("artifact construction must not be reached")


def test_guard_entrypoint_has_no_runtime_or_broker_surface() -> None:
    source = HELPER_PATH.read_text(encoding="utf-8")
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
        "scripts",
        "side_cli",
        "side_engine",
    }
    assert forbidden_import_roots.isdisjoint({name.split(".")[0] for name in imports})

    forbidden_text = {
        "argparse",
        "subprocess",
        "os.environ",
        "persist_no_order_artifact",
        "build_and_persist_no_order_artifact",
        "account_fetcher",
        "credential_loader",
        "credential_client",
        "broker_adapter",
        "broker_client",
        "side live",
        'if __name__ == "__main__"',
    }
    for text in forbidden_text:
        assert text not in source

    repo_surface = live_runtime_surface_absent_check()
    assert {
        "passed",
        "live_subcommand_present",
        "live_runtime_paths",
        "claim_block_reason",
    } <= repo_surface.keys()
    assert repo_surface["passed"] is True
    assert repo_surface["live_subcommand_present"] is False
    assert repo_surface["live_runtime_paths"] == []


def test_guard_accepts_only_safe_provenance() -> None:
    for kind in ("fixture", "synthetic", "sanitized"):
        result = run_no_order_guard(_safe_input(kind), GuardProviderState())

        assert result.evidence_label == EVIDENCE_LABEL
        assert result.input_provenance_kind == kind
        assert result.source_ref == f"phase155-{kind}-input"
        assert result.valid_public_artifact is True

    runtime_claim = replace(
        _safe_input(),
        input_provenance=InputProvenance(
            kind="synthetic",
            runtime_evidence_claim=True,
            source_ref="phase155-runtime-claim",
        ),
    )
    with pytest.raises(GuardViolation) as runtime_error:
        run_no_order_guard(runtime_claim, GuardProviderState())
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
            source_ref="phase155-runtime-kind",
        ),
    )
    with pytest.raises(GuardViolation) as kind_error:
        run_no_order_guard(unknown_kind, GuardProviderState())
    _assert_guard(
        kind_error,
        path="input_provenance",
        reason="invalid_input_provenance",
    )


def test_guard_rejects_invalid_provenance_before_no_artifact_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        builder,
        "build_no_order_artifact",
        _fail_if_artifact_constructed,
    )
    invalid_input = replace(
        _safe_input(),
        input_provenance=InputProvenance(
            kind="runtime",
            runtime_evidence_claim=True,
            source_ref="phase155-invalid-runtime",
        ),
    )
    matrix = (
        replace(
            invalid_input,
            order_intent={
                **invalid_input.order_intent,
                "order_mutation_attempted": True,
            },
        ),
        invalid_input,
        invalid_input,
    )
    providers = (
        GuardProviderState(),
        GuardProviderState(unsafe_material_detected=True),
        GuardProviderState(protected_output_root_denied=True),
    )

    for input_data, provider_state in zip(matrix, providers, strict=True):
        with pytest.raises(GuardViolation) as provenance_error:
            run_no_order_guard(input_data, provider_state)
        _assert_guard(
            provenance_error,
            path="input_provenance",
            reason="invalid_input_provenance",
        )


def test_guard_rejects_missing_or_empty_source_ref_before_artifact_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        builder,
        "build_no_order_artifact",
        _fail_if_artifact_constructed,
    )

    for source_ref in (None, ""):
        input_data = replace(
            _safe_input(),
            input_provenance=InputProvenance(
                kind="synthetic",
                runtime_evidence_claim=False,
                source_ref=source_ref,
            ),
        )
        with pytest.raises(GuardViolation) as source_ref_error:
            run_no_order_guard(input_data, GuardProviderState())
        _assert_guard(
            source_ref_error,
            path="input_provenance.source_ref",
            reason="missing_source_ref",
        )


def test_guard_outputs_keep_no_order_mutation_flags_false() -> None:
    for providers in (
        GuardProviderState(),
        GuardProviderState(kill_switch_active=True),
        GuardProviderState(account_proof_stale=True),
        GuardProviderState(duplicate_idempotency=True),
    ):
        result = run_no_order_guard(_safe_input(), providers)

        assert result.artifact is not None
        live_preflight = result.artifact["live_preflight"]
        assert live_preflight["order_mutation_allowed"] is False
        assert live_preflight["order_mutation_attempted"] is False
        assert live_preflight["broker_mutation_attempted"] is False


def test_guard_rejects_input_order_intent_mutation_attempts_before_artifact_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        builder,
        "build_no_order_artifact",
        _fail_if_artifact_constructed,
    )
    matrix = [
        ("order_mutation_attempted", "order_mutation_attempted"),
        ("broker_mutation_attempted", "broker_mutation_attempted"),
    ]

    for order_intent_key, expected_reason in matrix:
        input_data = replace(
            _safe_input(),
            order_intent={**_safe_input().order_intent, order_intent_key: True},
        )
        result = run_no_order_guard(input_data, GuardProviderState())

        _assert_result(
            result,
            status="failed",
            failure_class="mutation_attempt",
            failure_reason=expected_reason,
            terminal_gate="no_order_assertion",
        )
        assert result.artifact is None
        assert result.valid_public_artifact is False


def test_guard_rejects_provider_mutation_attempts_before_artifact_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        builder,
        "build_no_order_artifact",
        _fail_if_artifact_constructed,
    )
    matrix = [
        (
            GuardProviderState(order_mutation_attempted=True),
            "order_mutation_attempted",
        ),
        (
            GuardProviderState(broker_mutation_attempted=True),
            "broker_mutation_attempted",
        ),
    ]

    for providers, expected_reason in matrix:
        result = run_no_order_guard(_safe_input(), providers)

        _assert_result(
            result,
            status="failed",
            failure_class="mutation_attempt",
            failure_reason=expected_reason,
            terminal_gate="no_order_assertion",
        )
        assert result.artifact is None
        assert result.valid_public_artifact is False


def test_guard_rejects_dangerous_outcomes_before_artifact_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        builder,
        "build_no_order_artifact",
        _fail_if_artifact_constructed,
    )
    matrix = (
        (
            GuardProviderState(unsafe_material_detected=True),
            "unsafe_material",
            "raw_secret_or_private_identifier",
            "credential_hygiene",
        ),
        (
            GuardProviderState(protected_output_root_denied=True),
            "protected_output_root",
            "protected_output_root_denied",
            "protected_output_root",
        ),
    )

    for providers, failure_class, failure_reason, terminal_gate in matrix:
        result = run_no_order_guard(_safe_input(), providers)

        _assert_result(
            result,
            status="failed",
            failure_class=failure_class,
            failure_reason=failure_reason,
            terminal_gate=terminal_gate,
        )
        assert result.artifact is None
        assert result.valid_public_artifact is False


def test_guard_classifies_all_outcomes_distinctly() -> None:
    matrix = [
        ("passed", GuardProviderState(), "passed", None, None, None),
        (
            "risk_stopped",
            GuardProviderState(kill_switch_active=True),
            "risk_stopped",
            "risk_decision",
            "kill_switch_active",
            "risk_decision",
        ),
        (
            "stale_proof",
            GuardProviderState(account_proof_stale=True),
            "failed",
            "stale_proof",
            "account_proof_stale",
            "account_proof",
        ),
        (
            "duplicate_idempotency",
            GuardProviderState(duplicate_idempotency=True),
            "failed",
            "duplicate_idempotency",
            "idempotency_replay_detected",
            "idempotency",
        ),
        (
            "unsafe_material",
            GuardProviderState(unsafe_material_detected=True),
            "failed",
            "unsafe_material",
            "raw_secret_or_private_identifier",
            "credential_hygiene",
        ),
        (
            "mutation_attempt",
            GuardProviderState(order_mutation_attempted=True),
            "failed",
            "mutation_attempt",
            "order_mutation_attempted",
            "no_order_assertion",
        ),
        (
            "protected_output_root",
            GuardProviderState(protected_output_root_denied=True),
            "failed",
            "protected_output_root",
            "protected_output_root_denied",
            "protected_output_root",
        ),
        (
            "preflight_dependency_unavailable",
            GuardProviderState(dependency_available=False),
            "failed",
            "infrastructure",
            "preflight_dependency_unavailable",
            "preflight_dependency",
        ),
    ]

    seen = set()
    for name, providers, status, failure_class, failure_reason, terminal_gate in matrix:
        result = run_no_order_guard(_safe_input(), providers)

        _assert_result(
            result,
            status=status,
            failure_class=failure_class,
            failure_reason=failure_reason,
            terminal_gate=terminal_gate,
        )
        assert name not in seen
        seen.add(name)


def test_risk_stop_is_not_dependency_failure() -> None:
    for providers, expected_reason in (
        (GuardProviderState(kill_switch_active=True), "kill_switch_active"),
        (GuardProviderState(broker_risk_blocked=True), "broker_risk_block"),
        (GuardProviderState(risk_validator_rejected=True), "risk_validator_rejected"),
    ):
        result = run_no_order_guard(_safe_input(), providers)
        _assert_result(
            result,
            status="risk_stopped",
            failure_class="risk_decision",
            failure_reason=expected_reason,
            terminal_gate="risk_decision",
        )

    dependency_failure = run_no_order_guard(
        _safe_input(),
        GuardProviderState(dependency_available=False),
    )
    _assert_result(
        dependency_failure,
        status="failed",
        failure_class="infrastructure",
        failure_reason="preflight_dependency_unavailable",
        terminal_gate="preflight_dependency",
    )


def test_guard_entrypoint_never_persists_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        builder,
        "persist_no_order_artifact",
        lambda *_args, **_kwargs: pytest.fail("persistence must not be reached"),
    )
    monkeypatch.setattr(
        builder,
        "build_and_persist_no_order_artifact",
        lambda *_args, **_kwargs: pytest.fail("persistence must not be reached"),
    )

    for providers in (
        GuardProviderState(),
        GuardProviderState(kill_switch_active=True),
        GuardProviderState(dependency_available=False),
        GuardProviderState(unsafe_material_detected=True),
        GuardProviderState(protected_output_root_denied=True),
    ):
        result = run_no_order_guard(_safe_input(), providers)
        assert result.persisted_to_disk is False

    source = HELPER_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "write_text(",
        "write_bytes(",
        ".open(",
        "open(",
        "os.replace",
        "mkdir(",
    ):
        assert forbidden not in source

    assert list(tmp_path.iterdir()) == []
