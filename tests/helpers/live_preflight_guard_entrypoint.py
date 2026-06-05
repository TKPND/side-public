"""Test-owned no-order live preflight guard entrypoint harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tests.helpers import live_preflight_result_builder as builder


EVIDENCE_LABEL = "non-runtime/live-broker evidence"


@dataclass(frozen=True)
class GuardProviderState:
    dependency_available: bool = True
    kill_switch_active: bool = False
    risk_validator_rejected: bool = False
    broker_risk_blocked: bool = False
    account_proof_stale: bool = False
    market_proof_stale: bool = False
    duplicate_idempotency: bool = False
    unsafe_material_detected: bool = False
    order_mutation_attempted: bool = False
    broker_mutation_attempted: bool = False
    protected_output_root_denied: bool = False


@dataclass(frozen=True)
class GuardEntrypointResult:
    status: str
    failure_class: str | None
    failure_reason: str | None
    terminal_gate: str | None
    artifact: dict[str, Any] | None
    valid_public_artifact: bool
    persisted_to_disk: bool
    evidence_label: str
    input_provenance_kind: str
    source_ref: str | None


def run_no_order_guard(
    input_data: builder.NoOrderPreflightInput,
    providers: GuardProviderState | None = None,
) -> GuardEntrypointResult:
    providers = providers or GuardProviderState()
    source_ref = _assert_safe_entrypoint_provenance(input_data)

    input_mutation = _input_mutation_reason(input_data)
    if input_mutation is not None:
        return _without_artifact(input_data, "mutation_attempt", input_mutation)

    provider_mutation = _provider_mutation_reason(providers)
    if provider_mutation is not None:
        return _without_artifact(input_data, "mutation_attempt", provider_mutation)

    dangerous_outcome = _dangerous_provider_outcome(providers)
    if dangerous_outcome is not None:
        failure_class, failure_reason, terminal_gate = dangerous_outcome
        return _result(
            input_data,
            status="failed",
            failure_class=failure_class,
            failure_reason=failure_reason,
            terminal_gate=terminal_gate,
            artifact=None,
            valid_public_artifact=False,
        )

    outcome = _classify_provider_state(providers)
    artifact = builder.build_no_order_artifact(input_data, outcome)
    builder.validate_public_artifact(artifact)
    builder.assert_public_material_safe(artifact)

    return GuardEntrypointResult(
        status=outcome.status,
        failure_class=outcome.failure_class,
        failure_reason=outcome.failure_reason,
        terminal_gate=outcome.terminal_gate,
        artifact=artifact,
        valid_public_artifact=True,
        persisted_to_disk=False,
        evidence_label=EVIDENCE_LABEL,
        input_provenance_kind=input_data.input_provenance.kind,
        source_ref=source_ref,
    )


def _result(
    input_data: builder.NoOrderPreflightInput,
    *,
    status: str,
    failure_class: str | None,
    failure_reason: str | None,
    terminal_gate: str | None,
    artifact: dict[str, Any] | None,
    valid_public_artifact: bool,
) -> GuardEntrypointResult:
    return GuardEntrypointResult(
        status=status,
        failure_class=failure_class,
        failure_reason=failure_reason,
        terminal_gate=terminal_gate,
        artifact=artifact,
        valid_public_artifact=valid_public_artifact,
        persisted_to_disk=False,
        evidence_label=EVIDENCE_LABEL,
        input_provenance_kind=input_data.input_provenance.kind,
        source_ref=input_data.input_provenance.source_ref,
    )


def _assert_safe_entrypoint_provenance(
    input_data: builder.NoOrderPreflightInput,
) -> str:
    provenance = input_data.input_provenance
    if (
        provenance.kind not in builder.ALLOWED_PROVENANCE_KINDS
        or provenance.runtime_evidence_claim is not False
    ):
        raise builder.GuardViolation(
            path="input_provenance",
            reason="invalid_input_provenance",
        )

    source_ref = provenance.source_ref
    if not isinstance(source_ref, str) or source_ref.strip() == "":
        raise builder.GuardViolation(
            path="input_provenance.source_ref",
            reason="missing_source_ref",
        )
    return source_ref


def _without_artifact(
    input_data: builder.NoOrderPreflightInput,
    failure_class: str,
    failure_reason: str,
) -> GuardEntrypointResult:
    return _result(
        input_data,
        status="failed",
        failure_class=failure_class,
        failure_reason=failure_reason,
        terminal_gate="no_order_assertion",
        artifact=None,
        valid_public_artifact=False,
    )


def _input_mutation_reason(
    input_data: builder.NoOrderPreflightInput,
) -> str | None:
    if input_data.order_intent.get("order_mutation_attempted") is True:
        return "order_mutation_attempted"
    if input_data.order_intent.get("broker_mutation_attempted") is True:
        return "broker_mutation_attempted"
    return None


def _provider_mutation_reason(providers: GuardProviderState) -> str | None:
    if providers.order_mutation_attempted:
        return "order_mutation_attempted"
    if providers.broker_mutation_attempted:
        return "broker_mutation_attempted"
    return None


def _dangerous_provider_outcome(
    providers: GuardProviderState,
) -> tuple[str, str, str] | None:
    if providers.unsafe_material_detected:
        return (
            "unsafe_material",
            "raw_secret_or_private_identifier",
            "credential_hygiene",
        )
    if providers.protected_output_root_denied:
        return (
            "protected_output_root",
            "protected_output_root_denied",
            "protected_output_root",
        )
    return None


def _classify_provider_state(providers: GuardProviderState) -> builder.OutcomeSpec:
    if not providers.dependency_available:
        return builder.OutcomeSpec(
            status="failed",
            failure_class="infrastructure",
            failure_reason="preflight_dependency_unavailable",
            terminal_gate="preflight_dependency",
            persisted=False,
        )
    if providers.account_proof_stale:
        return builder.OutcomeSpec(
            status="failed",
            failure_class="stale_proof",
            failure_reason="account_proof_stale",
            terminal_gate="account_proof",
            persisted=False,
        )
    if providers.market_proof_stale:
        return builder.OutcomeSpec(
            status="failed",
            failure_class="stale_proof",
            failure_reason="market_proof_stale",
            terminal_gate="market_proof",
            persisted=False,
        )
    if providers.duplicate_idempotency:
        return builder.OutcomeSpec(
            status="failed",
            failure_class="duplicate_idempotency",
            failure_reason="idempotency_replay_detected",
            terminal_gate="idempotency",
            persisted=False,
        )
    if providers.kill_switch_active:
        return _risk_decision("kill_switch_active")
    if providers.broker_risk_blocked:
        return _risk_decision("broker_risk_block")
    if providers.risk_validator_rejected:
        return _risk_decision("risk_validator_rejected")
    return builder.OutcomeSpec(
        status="passed",
        failure_class=None,
        failure_reason=None,
        terminal_gate=None,
        persisted=False,
    )


def _risk_decision(failure_reason: str) -> builder.OutcomeSpec:
    return builder.OutcomeSpec(
        status="risk_stopped",
        failure_class="risk_decision",
        failure_reason=failure_reason,
        terminal_gate="risk_decision",
        persisted=False,
    )
