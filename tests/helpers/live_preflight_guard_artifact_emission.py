"""Guarded public artifact emission for test-owned live preflight results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.helpers import live_preflight_result_builder as builder
from tests.helpers.live_preflight_guard_entrypoint import GuardEntrypointResult


_NON_PERSISTABLE_FAILURE_CLASSES = {
    "unsafe_material",
    "mutation_attempt",
    "protected_output_root",
}


@dataclass(frozen=True)
class GuardArtifactEmissionResult:
    persisted: bool
    path: Path | None
    status: str
    failure_class: str | None
    failure_reason: str | None
    reason: str


def emit_guard_artifact(
    result: GuardEntrypointResult,
    output_path: str | Path,
    *,
    allowed_root: str | Path,
) -> GuardArtifactEmissionResult:
    if result.failure_class in _NON_PERSISTABLE_FAILURE_CLASSES:
        return _emission_result(result, reason="non_persistable_result")
    if result.artifact is None:
        return _emission_result(result, reason="missing_artifact")
    if result.valid_public_artifact is not True:
        return _emission_result(result, reason="invalid_public_artifact")

    artifact = _persistable_artifact_copy(result.artifact)
    try:
        path = builder.persist_no_order_artifact(
            artifact,
            output_path,
            allowed_root=allowed_root,
        )
    except builder.GuardViolation as error:
        return _emission_result(result, reason=_safe_reason(error.reason))

    return _emission_result(result, persisted=True, path=path, reason="persisted")


def _persistable_artifact_copy(artifact: dict[str, object]) -> dict[str, object]:
    copied = dict(artifact)
    emission = dict(copied.get("emission", {}))
    emission["persisted"] = True
    emission["protected_output_root"] = False
    copied["emission"] = emission
    return copied


def _safe_reason(reason: str) -> str:
    if reason in {"schema_validation", "semantic_violation"}:
        return "invalid_public_artifact"
    return reason


def _emission_result(
    result: GuardEntrypointResult,
    *,
    persisted: bool = False,
    path: Path | None = None,
    reason: str,
) -> GuardArtifactEmissionResult:
    return GuardArtifactEmissionResult(
        persisted=persisted,
        path=path,
        status=result.status,
        failure_class=result.failure_class,
        failure_reason=result.failure_reason,
        reason=reason,
    )
