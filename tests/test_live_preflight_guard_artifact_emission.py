"""Phase 156 tests for guarded live-preflight artifact emission."""

from __future__ import annotations

import ast
import json
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest

from tests.helpers import live_preflight_result_builder as builder
from tests.helpers.live_preflight_guard_artifact_emission import (
    GuardArtifactEmissionResult,
    emit_guard_artifact,
)
from tests.helpers.live_preflight_guard_entrypoint import (
    GuardEntrypointResult,
    GuardProviderState,
    run_no_order_guard,
)
from tests.helpers.live_preflight_result_builder import (
    InputProvenance,
    NoOrderPreflightInput,
)


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "tests/helpers/live_preflight_guard_artifact_emission.py"
RAW_SECRET = "acct-SECRET-token-private.internal"


def _safe_input(kind: str = "synthetic") -> NoOrderPreflightInput:
    return NoOrderPreflightInput(
        input_provenance=InputProvenance(
            kind=kind,
            runtime_evidence_claim=False,
            source_ref=f"phase156-{kind}-input",
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


def _guard_result(providers: GuardProviderState | None = None) -> GuardEntrypointResult:
    return run_no_order_guard(_safe_input(), providers or GuardProviderState())


def _assert_not_persisted(
    emission: GuardArtifactEmissionResult,
    output_path: Path,
) -> None:
    assert emission.persisted is False
    assert emission.path is None
    assert not output_path.exists()
    assert not output_path.parent.exists()
    assert not output_path.with_suffix(output_path.suffix + ".tmp").exists()


def test_emission_requires_explicit_output_path_and_allowed_root(tmp_path: Path) -> None:
    result = _guard_result()

    with pytest.raises(TypeError):
        emit_guard_artifact(result)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        emit_guard_artifact(result, tmp_path / "missing-root.json")  # type: ignore[call-arg]


def test_emission_persists_schema_valid_public_safe_guard_results(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "passed.json"
    result = _guard_result()

    emission = emit_guard_artifact(result, output_path, allowed_root=tmp_path)

    assert emission == GuardArtifactEmissionResult(
        persisted=True,
        path=output_path,
        status="passed",
        failure_class=None,
        failure_reason=None,
        reason="persisted",
    )
    artifact = json.loads(output_path.read_text(encoding="utf-8"))
    builder.validate_public_artifact(artifact)
    builder.assert_public_material_safe(artifact)
    assert artifact["emission"] == {
        "persisted": True,
        "protected_output_root": False,
    }
    assert result.persisted_to_disk is False


def test_emission_maps_persistable_result_matrix(tmp_path: Path) -> None:
    matrix = (
        ("passed", GuardProviderState()),
        ("risk_stopped", GuardProviderState(kill_switch_active=True)),
        ("stale_proof", GuardProviderState(account_proof_stale=True)),
        ("duplicate_idempotency", GuardProviderState(duplicate_idempotency=True)),
        ("infrastructure", GuardProviderState(dependency_available=False)),
    )

    for name, providers in matrix:
        output_path = tmp_path / f"{name}.json"
        result = _guard_result(providers)

        emission = emit_guard_artifact(result, output_path, allowed_root=tmp_path)

        assert emission.persisted is True
        assert emission.path == output_path
        assert emission.status == result.status
        assert emission.failure_class == result.failure_class
        assert emission.failure_reason == result.failure_reason
        artifact = json.loads(output_path.read_text(encoding="utf-8"))
        assert artifact["result"] == {
            "status": result.status,
            "failure_class": result.failure_class,
            "failure_reason": result.failure_reason,
            "terminal_gate": result.terminal_gate,
        }
        assert artifact["emission"]["persisted"] is True


def test_emission_fails_closed_for_missing_or_invalid_artifact(tmp_path: Path) -> None:
    valid_result = _guard_result()
    invalid_artifact = dict(valid_result.artifact or {})
    invalid_artifact["unexpected"] = "extra"
    matrix = (
        (
            replace(valid_result, artifact=None, valid_public_artifact=True),
            "missing_artifact",
        ),
        (
            replace(
                valid_result,
                artifact=invalid_artifact,
                valid_public_artifact=False,
            ),
            "invalid_public_artifact",
        ),
    )

    for index, (result, reason) in enumerate(matrix):
        output_path = tmp_path / f"invalid-{index}" / "artifact.json"

        emission = emit_guard_artifact(result, output_path, allowed_root=tmp_path)

        assert emission.reason == reason
        _assert_not_persisted(emission, output_path)


def test_emission_rejects_dangerous_outcomes_without_persistence(
    tmp_path: Path,
) -> None:
    mutation_input = replace(
        _safe_input(),
        order_intent={
            **_safe_input().order_intent,
            "order_mutation_attempted": True,
        },
    )
    matrix = (
        run_no_order_guard(
            _safe_input(),
            GuardProviderState(unsafe_material_detected=True),
        ),
        run_no_order_guard(mutation_input, GuardProviderState()),
        run_no_order_guard(
            _safe_input(),
            GuardProviderState(protected_output_root_denied=True),
        ),
    )

    for result in matrix:
        output_path = tmp_path / f"{result.failure_class}" / "artifact.json"

        emission = emit_guard_artifact(result, output_path, allowed_root=tmp_path)

        assert emission.status == "failed"
        assert emission.failure_class in {
            "unsafe_material",
            "mutation_attempt",
            "protected_output_root",
        }
        assert emission.reason == "non_persistable_result"
        _assert_not_persisted(emission, output_path)


def test_emission_rejects_protected_roots_before_validation_or_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected_output = ROOT / ".planning/milestones/phase156/protected.json"
    result = _guard_result()
    assert not protected_output.parent.exists()

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        pytest.fail("protected path guard must fail first")

    monkeypatch.setattr(builder, "validate_public_artifact", fail_if_called)
    monkeypatch.setattr(builder, "assert_public_material_safe", fail_if_called)
    monkeypatch.setattr(builder.os, "replace", fail_if_called)

    emission = emit_guard_artifact(
        result,
        protected_output,
        allowed_root=ROOT,
    )

    assert emission.persisted is False
    assert emission.path is None
    assert emission.reason == "protected_output_root"
    assert not protected_output.parent.exists()
    assert not protected_output.exists()
    assert not protected_output.with_suffix(".json.tmp").exists()


def test_emission_runs_schema_and_public_material_guards_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "ordered.json"
    result = _guard_result()
    events: list[str] = []
    original_validate = builder.validate_public_artifact
    original_public_guard = builder.assert_public_material_safe
    original_replace = builder.os.replace

    def validate_probe(artifact: dict[str, Any]) -> None:
        events.append("schema_semantic")
        original_validate(artifact)

    def public_guard_probe(artifact: dict[str, Any]) -> None:
        events.append("public_material")
        original_public_guard(artifact)

    def replace_probe(src: object, dst: object) -> None:
        events.append("replace")
        original_replace(src, dst)

    monkeypatch.setattr(builder, "validate_public_artifact", validate_probe)
    monkeypatch.setattr(builder, "assert_public_material_safe", public_guard_probe)
    monkeypatch.setattr(builder.os, "replace", replace_probe)

    emission = emit_guard_artifact(result, output_path, allowed_root=tmp_path)

    assert emission.persisted is True
    assert events == ["schema_semantic", "public_material", "replace"]


def test_emission_result_does_not_expose_nested_guard_result(tmp_path: Path) -> None:
    emission = emit_guard_artifact(
        _guard_result(),
        tmp_path / "result.json",
        allowed_root=tmp_path,
    )

    assert isinstance(emission, GuardArtifactEmissionResult)
    assert {field.name for field in fields(emission)} == {
        "persisted",
        "path",
        "status",
        "failure_class",
        "failure_reason",
        "reason",
    }
    assert not hasattr(emission, "guard_result")
    assert not hasattr(emission, "artifact")


def test_emission_error_output_never_contains_raw_material(tmp_path: Path) -> None:
    valid_result = _guard_result()
    unsafe_artifact = dict(valid_result.artifact or {})
    unsafe_live_preflight = dict(unsafe_artifact["live_preflight"])
    unsafe_live_preflight["account_proof"] = {
        **unsafe_live_preflight["account_proof"],
        "account_alias": RAW_SECRET,
    }
    unsafe_artifact["live_preflight"] = unsafe_live_preflight
    unsafe_result = replace(
        valid_result,
        artifact=unsafe_artifact,
        valid_public_artifact=True,
    )
    output_path = tmp_path / "unsafe" / "artifact.json"

    emission = emit_guard_artifact(unsafe_result, output_path, allowed_root=tmp_path)

    _assert_not_persisted(emission, output_path)
    assert emission.reason == "unsafe_public_material"
    for unsafe_text in ("SECRET", "token", "private.internal", RAW_SECRET):
        assert unsafe_text not in str(emission)
        assert unsafe_text not in repr(emission)
        assert unsafe_text not in emission.reason


def test_emission_helper_has_no_runtime_or_broker_surface() -> None:
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
