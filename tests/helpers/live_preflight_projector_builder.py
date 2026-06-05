"""Projector-to-builder bridge for test-owned live preflight artifacts."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping

from tests.helpers.live_preflight_account_proof_projector import (
    AccountProofProjectorResult,
    AccountProofFreshnessResult,
    PUBLIC_MATERIAL_MAX_DEPTH,
    ProjectorViolation,
    _RAW_DERIVATION_FRAGMENT_PATTERN,
    _UNSAFE_KEY_FRAGMENTS,
    _UNSAFE_VALUE_FRAGMENTS,
    _depth_limit_path,
    _format_path,
    _is_account_amount_shape,
    _is_public_digest_ref,
    _is_public_opaque_ref,
    _unsafe_key_reason as _projector_unsafe_key_reason,
    _unsafe_string_reason,
    classify_account_proof_freshness,
    project_account_proof,
)
from tests.helpers.live_preflight_result_builder import (
    InputProvenance,
    NoOrderPreflightInput,
    OutcomeSpec,
    assert_public_material_safe,
    build_no_order_artifact,
    validate_public_artifact,
)

_PROJECTOR_TO_BUILDER_PROVENANCE = {
    "synthetic_public": "synthetic",
    "fixture_sanitized_public": "sanitized",
    "already_sanitized_public": "sanitized",
}
_ALLOWED_IDEMPOTENCY_PUBLIC_KEYS = {
    "candidate_identity_digest",
    "idempotency_key_hash",
    "duplicate_check_status",
    "duplicate_check_ref",
}
_ALLOWED_NUMERIC_VALUE_PATHS = {
    ("market_proof", "market_max_age_ms"),
    ("market_proof", "spread_bps"),
    ("order_intent", "requested_notional"),
    ("order_intent", "allowed_notional"),
    ("order_intent", "max_slippage_bps"),
    ("kill_switch_proof", "proof_max_age_ms"),
    ("idempotency_proof", "proof_max_age_ms"),
}
_ALLOWED_BOOLEAN_VALUE_PATHS = {
    ("order_intent", "order_mutation_allowed"): False,
}
_ALLOWED_STATUSES = {"passed", "risk_stopped", "failed"}
_ALLOWED_FAILURE_CLASSES = {
    None,
    "risk_decision",
    "stale_proof",
    "duplicate_idempotency",
    "unsafe_material",
    "mutation_attempt",
    "protected_output_root",
    "infrastructure",
}
_ALLOWED_FAILURE_REASONS = {
    None,
    "broker_risk_block",
    "kill_switch_active",
    "risk_validator_rejected",
    "account_proof_stale",
    "market_proof_stale",
    "idempotency_replay_detected",
    "raw_secret_or_private_identifier",
    "order_mutation_attempted",
    "broker_mutation_attempted",
    "protected_output_root_denied",
    "preflight_dependency_unavailable",
}
_ALLOWED_TERMINAL_GATES = {
    None,
    "protected_output_root",
    "credential_hygiene",
    "account_proof",
    "market_proof",
    "idempotency",
    "risk_validator",
    "risk_decision",
    "no_order_assertion",
    "preflight_dependency",
}
_ALLOWED_RESULT_TUPLES = {
    ("passed", None, None, None),
    ("risk_stopped", "risk_decision", "broker_risk_block", "risk_decision"),
    ("risk_stopped", "risk_decision", "kill_switch_active", "risk_decision"),
    ("risk_stopped", "risk_decision", "risk_validator_rejected", "risk_decision"),
    ("failed", "stale_proof", "account_proof_stale", "account_proof"),
    ("failed", "stale_proof", "market_proof_stale", "market_proof"),
    (
        "failed",
        "duplicate_idempotency",
        "idempotency_replay_detected",
        "idempotency",
    ),
    (
        "failed",
        "unsafe_material",
        "raw_secret_or_private_identifier",
        "credential_hygiene",
    ),
    (
        "failed",
        "mutation_attempt",
        "order_mutation_attempted",
        "no_order_assertion",
    ),
    (
        "failed",
        "mutation_attempt",
        "broker_mutation_attempted",
        "no_order_assertion",
    ),
    (
        "failed",
        "protected_output_root",
        "protected_output_root_denied",
        "protected_output_root",
    ),
    (
        "failed",
        "infrastructure",
        "preflight_dependency_unavailable",
        "preflight_dependency",
    ),
}
_PUBLIC_STRING_VALUE_KINDS = {
    ("market_proof", "symbol"): "symbol",
    ("market_proof", "market_snapshot_ref"): "ref",
    ("market_proof", "market_ts"): "timestamp",
    ("market_proof", "price_ref"): "ref",
    ("market_proof", "price_source_alias"): "token",
    ("order_intent", "side"): "order_side",
    ("order_intent", "order_type"): "order_type",
    ("order_intent", "time_in_force"): "time_in_force",
    ("order_intent", "notional_currency"): "currency",
    ("order_intent", "notional_source"): "token",
    ("order_intent", "price_bounds_ref"): "ref",
    ("order_intent", "idempotency_key_hash"): "digest",
    ("kill_switch_proof", "global_gate_status"): "gate_status",
    ("kill_switch_proof", "global_gate_ref"): "ref",
    ("kill_switch_proof", "strategy_gate_status"): "gate_status",
    ("kill_switch_proof", "strategy_gate_ref"): "ref",
    ("kill_switch_proof", "symbol_gate_status"): "gate_status",
    ("kill_switch_proof", "symbol_gate_ref"): "ref",
    ("kill_switch_proof", "broker_account_gate_status"): "gate_status",
    ("kill_switch_proof", "broker_account_gate_ref"): "ref",
    ("kill_switch_proof", "proof_ts"): "timestamp",
    ("idempotency_proof", "candidate_identity_digest"): "digest",
    ("idempotency_proof", "idempotency_key_hash"): "digest",
    ("idempotency_proof", "duplicate_check_status"): "duplicate_check_status",
    ("idempotency_proof", "duplicate_check_ref"): "ref",
    ("idempotency_proof", "proof_ts"): "timestamp",
}
_PUBLIC_STRING_ENUMS = {
    "order_side": {"buy", "sell"},
    "order_type": {"limit", "market"},
    "time_in_force": {"IOC", "FOK", "DAY", "GTC"},
    "gate_status": {"passed", "stopped"},
    "duplicate_check_status": {"passed", "duplicate_detected"},
}
_PUBLIC_TOKEN_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}"
_PUBLIC_SYMBOL_PATTERN = r"[A-Z0-9][A-Z0-9_-]{0,23}"
_PUBLIC_CURRENCY_PATTERN = r"[A-Z]{3}"
_PUBLIC_TIMESTAMP_PATTERN = (
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"
)
_IPV4_LITERAL_PATTERN = r"(?:\d{1,3}\.){3}\d{1,3}"
_DOTTED_HOST_PATTERN = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"


def _build_no_order_input_from_projector(
    projector_result: AccountProofProjectorResult,
    *,
    now: datetime,
    outcome: OutcomeSpec,
    market_proof: Mapping[str, Any],
    order_intent: Mapping[str, Any],
    kill_switch_proof: Mapping[str, Any],
    idempotency_proof: Mapping[str, Any],
) -> tuple[NoOrderPreflightInput, OutcomeSpec]:
    validated = _validated_projector_result(projector_result)
    freshness = classify_account_proof_freshness(
        validated.account_proof,
        now=now,
    )
    safe_market_proof = _validated_public_block("market_proof", market_proof)
    safe_order_intent = _validated_public_block("order_intent", order_intent)
    safe_kill_switch_proof = _validated_public_block(
        "kill_switch_proof",
        kill_switch_proof,
    )
    safe_idempotency_proof = _validated_public_block(
        "idempotency_proof",
        idempotency_proof,
    )
    effective_outcome = _outcome_for_freshness(freshness, outcome)
    _assert_public_outcome(effective_outcome)
    builder_input = NoOrderPreflightInput(
        input_provenance=InputProvenance(
            kind=_builder_provenance_from_projector(
                validated.input_provenance_kind
            ),
            runtime_evidence_claim=False,
            source_ref=validated.source_ref,
        ),
        account_proof=dict(validated.account_proof),
        market_proof=safe_market_proof,
        order_intent=safe_order_intent,
        kill_switch_proof=safe_kill_switch_proof,
        idempotency_proof=safe_idempotency_proof,
    )
    return builder_input, effective_outcome


def build_no_order_artifact_from_projector(
    projector_result: AccountProofProjectorResult,
    *,
    now: datetime,
    outcome: OutcomeSpec,
    market_proof: Mapping[str, Any],
    order_intent: Mapping[str, Any],
    kill_switch_proof: Mapping[str, Any],
    idempotency_proof: Mapping[str, Any],
) -> dict[str, Any]:
    builder_input, effective_outcome = _build_no_order_input_from_projector(
        projector_result,
        now=now,
        outcome=outcome,
        market_proof=market_proof,
        order_intent=order_intent,
        kill_switch_proof=kill_switch_proof,
        idempotency_proof=idempotency_proof,
    )
    artifact = build_no_order_artifact(builder_input, effective_outcome)
    validate_public_artifact(artifact)
    assert_public_material_safe(artifact)
    return artifact


def _validated_projector_result(
    projector_result: AccountProofProjectorResult,
) -> AccountProofProjectorResult:
    if not isinstance(projector_result, AccountProofProjectorResult):
        raise ProjectorViolation(
            path="projector_result",
            reason="invalid_projector_result",
        )
    return project_account_proof(
        {
            "provenance_kind": projector_result.input_provenance_kind,
            "source_ref": projector_result.source_ref,
            "runtime_evidence_claim": projector_result.runtime_evidence_claim,
            "broker_evidence_claim": projector_result.broker_evidence_claim,
            "account_fetch_evidence_claim": (
                projector_result.account_fetch_evidence_claim
            ),
            "account_proof": projector_result.account_proof,
        }
    )


def _builder_provenance_from_projector(projector_provenance_kind: str) -> str:
    try:
        return _PROJECTOR_TO_BUILDER_PROVENANCE[projector_provenance_kind]
    except KeyError as exc:
        raise ProjectorViolation(
            path="provenance_kind",
            reason="invalid_provenance_kind",
        ) from exc


def _outcome_for_freshness(
    freshness: AccountProofFreshnessResult,
    outcome: OutcomeSpec,
) -> OutcomeSpec:
    if freshness.status == "current":
        return outcome
    if freshness.status == "stale":
        return OutcomeSpec(
            status="failed",
            failure_class="stale_proof",
            failure_reason="account_proof_stale",
            terminal_gate="account_proof",
            persisted=outcome.persisted,
            protected_output_root=outcome.protected_output_root,
        )
    raise ProjectorViolation(
        path="account_proof.freshness",
        reason="invalid_freshness_status",
    )


def _validated_public_block(
    block_name: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectorViolation(path=block_name, reason="unsafe_public_material")
    copied = dict(value)
    _assert_bridge_public_material_safe(copied, [block_name])
    return copied


def _assert_bridge_public_material_safe(value: Any, parts: list[str]) -> None:
    if len(parts) > PUBLIC_MATERIAL_MAX_DEPTH:
        raise ProjectorViolation(
            path=_depth_limit_path(parts),
            reason="unsafe_public_material",
        )
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_path = [*parts, str(key)]
            if _unsafe_key_reason(str(key)) is not None:
                raise ProjectorViolation(
                    path=_format_path(key_path),
                    reason="unsafe_public_material",
                )
            _assert_bridge_public_material_safe(nested, key_path)
        return
    if isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            _assert_bridge_public_material_safe(nested, [*parts, f"[{index}]"])
        return
    if isinstance(value, str):
        if not _is_allowed_public_string_value(parts, value):
            raise ProjectorViolation(
                path=_format_path(parts),
                reason="unsafe_public_material",
            )
        return
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
    ):
        if _is_allowed_numeric_value_path(parts, value):
            return
        raise ProjectorViolation(
            path=_format_path(parts),
            reason="unsafe_public_material",
        )
    if isinstance(value, bool):
        if _is_allowed_boolean_value_path(parts, value):
            return
        raise ProjectorViolation(
            path=_format_path(parts),
            reason="unsafe_public_material",
        )
    raise ProjectorViolation(
        path=_format_path(parts),
        reason="unsafe_public_material",
    )


def _assert_public_outcome(outcome: OutcomeSpec) -> None:
    if outcome.status not in _ALLOWED_STATUSES:
        raise ProjectorViolation(path="outcome.status", reason="invalid_outcome")
    if outcome.failure_class not in _ALLOWED_FAILURE_CLASSES:
        raise ProjectorViolation(
            path="outcome.failure_class",
            reason="invalid_outcome",
        )
    if outcome.failure_reason not in _ALLOWED_FAILURE_REASONS:
        raise ProjectorViolation(
            path="outcome.failure_reason",
            reason="invalid_outcome",
        )
    if outcome.terminal_gate not in _ALLOWED_TERMINAL_GATES:
        raise ProjectorViolation(
            path="outcome.terminal_gate",
            reason="invalid_outcome",
        )
    result_tuple = (
        outcome.status,
        outcome.failure_class,
        outcome.failure_reason,
        outcome.terminal_gate,
    )
    if result_tuple not in _ALLOWED_RESULT_TUPLES:
        raise ProjectorViolation(path="outcome", reason="invalid_outcome")


def _unsafe_key_reason(key: str) -> str | None:
    if key in _ALLOWED_IDEMPOTENCY_PUBLIC_KEYS:
        return None
    return _projector_unsafe_key_reason(key)


def _is_allowed_numeric_value_path(parts: list[str], value: int | float) -> bool:
    return tuple(parts) in _ALLOWED_NUMERIC_VALUE_PATHS and value >= 0


def _is_allowed_boolean_value_path(parts: list[str], value: bool) -> bool:
    return _ALLOWED_BOOLEAN_VALUE_PATHS.get(tuple(parts)) is value


def _is_allowed_public_string_value(parts: list[str], value: str) -> bool:
    if (
        _unsafe_string_reason(value) is not None
        or _is_account_amount_shape(value)
        or _is_endpoint_or_host_literal(value)
    ):
        return False

    kind = _PUBLIC_STRING_VALUE_KINDS.get(tuple(parts))
    if kind is None:
        return False
    if kind in _PUBLIC_STRING_ENUMS:
        return value in _PUBLIC_STRING_ENUMS[kind]
    if kind == "ref":
        return _is_public_opaque_ref(value)
    if kind == "digest":
        return _is_public_digest_ref(value)
    if kind == "token":
        return re.fullmatch(_PUBLIC_TOKEN_PATTERN, value) is not None
    if kind == "symbol":
        return re.fullmatch(_PUBLIC_SYMBOL_PATTERN, value) is not None
    if kind == "currency":
        return re.fullmatch(_PUBLIC_CURRENCY_PATTERN, value) is not None
    if kind == "timestamp":
        return re.fullmatch(_PUBLIC_TIMESTAMP_PATTERN, value) is not None
    return False


def _is_endpoint_or_host_literal(value: str) -> bool:
    lowered = value.strip().lower()
    if _is_ipv4_literal(lowered):
        return True
    return re.fullmatch(_DOTTED_HOST_PATTERN, lowered) is not None


def _is_ipv4_literal(value: str) -> bool:
    if re.fullmatch(_IPV4_LITERAL_PATTERN, value) is None:
        return False
    return all(0 <= int(part) <= 255 for part in value.split("."))
