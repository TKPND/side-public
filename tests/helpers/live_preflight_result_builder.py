"""Pure local builder for side.live_preflight.result.v1 test artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs/contracts/live_preflight_result_v1.schema.json"
ALLOWED_PROVENANCE_KINDS = {"fixture", "synthetic", "sanitized"}
SCHEMA_VERSION = "side.live_preflight.result.v1"
ARTIFACT_KIND = "live_preflight_result"
EXECUTION_MODE = "no_order_preflight"
RISK_GATE = {
    "schema_version": "risk_contract.v2",
    "contract_version": "v2",
    "validator_result_schema_version": "risk_contract_validator_result.v2",
    "schema_ref": "risk/contracts/v2/risk_contract_v2.schema.json",
    "validated_schema_ref": "risk/contracts/v2/risk_contract_v2.schema.json",
    "validator": "scripts/validate_risk_contract.py",
}
DANGEROUS_FAILURE_CLASSES = {
    "unsafe_material",
    "mutation_attempt",
    "protected_output_root",
}
RAW_ACCOUNT_VALUE_KEYS = {"equity", "cash_available", "buying_power"}
RAW_ACCOUNT_ID_KEYS = {"raw_account_id", "account_id"}
ALLOWED_IDEMPOTENCY_KEYS = {"idempotency_key_hash", "duplicate_check_ref"}
UNSAFE_KEY_FRAGMENTS = (
    "credential",
    "token",
    "cookie",
    "private_key",
    "broker_secret",
    "password",
    "api_key",
    "secret",
    "private_endpoint",
    "endpoint",
)
UNSAFE_VALUE_FRAGMENTS = (
    "example-raw-account-id",
    "raw-idempotency-key",
    "credential",
    "token",
    "cookie",
    "private_key",
    "broker_secret",
    "password",
    "api_key",
    "secret",
    "private.internal",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
)
PROTECTED_REPO_ROOTS = (
    Path(".planning/milestones"),
    Path("risk/contracts"),
    Path("docs/contracts"),
    Path("docs/examples/live_preflight/result_v1"),
)


@dataclass(frozen=True)
class GuardViolation(Exception):
    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.reason} at {self.path}"

    def __repr__(self) -> str:
        return f"GuardViolation(path={self.path!r}, reason={self.reason!r})"


@dataclass(frozen=True)
class InputProvenance:
    kind: str
    runtime_evidence_claim: bool = False
    source_ref: str | None = None


@dataclass(frozen=True)
class NoOrderPreflightInput:
    input_provenance: InputProvenance
    account_proof: dict[str, Any] = field(default_factory=dict)
    market_proof: dict[str, Any] = field(default_factory=dict)
    order_intent: dict[str, Any] = field(default_factory=dict)
    kill_switch_proof: dict[str, Any] = field(default_factory=dict)
    idempotency_proof: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutcomeSpec:
    status: str
    failure_class: str | None
    failure_reason: str | None
    terminal_gate: str | None
    persisted: bool = True
    protected_output_root: bool = False


def _assert_safe_provenance(provenance: InputProvenance) -> None:
    if (
        provenance.kind not in ALLOWED_PROVENANCE_KINDS
        or provenance.runtime_evidence_claim is not False
    ):
        raise GuardViolation(
            path="input_provenance",
            reason="invalid_input_provenance",
        )


def build_no_order_artifact(
    input_data: NoOrderPreflightInput,
    outcome: OutcomeSpec,
) -> dict[str, Any]:
    _assert_safe_provenance(input_data.input_provenance)

    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "execution_mode": EXECUTION_MODE,
        "result": {
            "status": outcome.status,
            "failure_class": outcome.failure_class,
            "failure_reason": outcome.failure_reason,
            "terminal_gate": outcome.terminal_gate,
        },
        "risk_gate": dict(RISK_GATE),
        "live_preflight": {
            "order_mutation_allowed": False,
            "order_mutation_attempted": False,
            "broker_mutation_attempted": False,
            "account_proof": dict(input_data.account_proof),
            "market_proof": dict(input_data.market_proof),
            "order_intent": dict(input_data.order_intent),
            "kill_switch_proof": dict(input_data.kill_switch_proof),
            "idempotency_proof": dict(input_data.idempotency_proof),
        },
        "emission": {
            "persisted": outcome.persisted,
            "protected_output_root": outcome.protected_output_root,
        },
    }


def _load_schema() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise GuardViolation(path=SCHEMA_PATH.as_posix(), reason="schema_validation")
    return schema


def _format_path(parts: list[str]) -> str:
    return ".".join(parts) if parts else "$"


def _schema_error_path(error: ValidationError) -> str:
    parts = [str(part) for part in error.path]
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        allowed = set(error.schema.get("properties", {}))
        extras = sorted(set(error.instance) - allowed)
        if extras:
            parts.append(str(extras[0]))
    return _format_path(parts)


def validate_public_artifact(artifact: dict[str, Any]) -> None:
    schema = _load_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(artifact),
        key=lambda error: (tuple(str(part) for part in error.path), error.message),
    )
    if errors:
        raise GuardViolation(
            path=_schema_error_path(errors[0]),
            reason="schema_validation",
        )

    preflight = artifact["live_preflight"]
    for field in (
        "order_mutation_allowed",
        "order_mutation_attempted",
        "broker_mutation_attempted",
    ):
        if preflight[field] is not False:
            raise GuardViolation(
                path=f"live_preflight.{field}",
                reason="semantic_violation",
            )

    emission = artifact["emission"]
    if emission["protected_output_root"] is True and emission["persisted"] is True:
        raise GuardViolation(
            path="emission.protected_output_root",
            reason="semantic_violation",
        )

    result = artifact["result"]
    if result["failure_class"] in DANGEROUS_FAILURE_CLASSES and emission["persisted"]:
        raise GuardViolation(
            path="result.failure_class",
            reason="semantic_violation",
        )


def _is_account_proof_path(parts: list[str]) -> bool:
    return parts[:2] == ["live_preflight", "account_proof"]


def _unsafe_key_reason(parts: list[str], key: str) -> str | None:
    lowered = key.lower()
    if lowered in RAW_ACCOUNT_ID_KEYS:
        return "unsafe_public_material"
    if _is_account_proof_path(parts) and lowered in RAW_ACCOUNT_VALUE_KEYS:
        return "raw_account_value"
    if lowered == "idempotency_key" or lowered == "raw_idempotency_key":
        return "unsafe_public_material"
    if lowered in ALLOWED_IDEMPOTENCY_KEYS:
        return None
    if any(fragment in lowered for fragment in UNSAFE_KEY_FRAGMENTS):
        return "unsafe_public_material"
    return None


def _unsafe_string_reason(value: str) -> str | None:
    lowered = value.lower()
    if any(fragment in lowered for fragment in UNSAFE_VALUE_FRAGMENTS):
        return "unsafe_public_material"
    return None


def _assert_public_material_safe(value: Any, parts: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_path = [*parts, str(key)]
            reason = _unsafe_key_reason(parts, str(key))
            if reason is not None:
                raise GuardViolation(path=_format_path(key_path), reason=reason)
            _assert_public_material_safe(nested, key_path)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_public_material_safe(nested, [*parts, f"[{index}]"])
    elif isinstance(value, str):
        reason = _unsafe_string_reason(value)
        if reason is not None:
            raise GuardViolation(path=_format_path(parts), reason=reason)


def assert_public_material_safe(value: Any) -> None:
    _assert_public_material_safe(value, [])


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _repo_relative(path: Path) -> Path | None:
    root = ROOT.resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(root)
    except ValueError:
        return None


def _is_protected_repo_path(path: Path) -> bool:
    rel_path = _repo_relative(path)
    if rel_path is None:
        return False

    for protected in PROTECTED_REPO_ROOTS:
        if rel_path == protected or _is_relative_to(rel_path, protected):
            return True

    parts = rel_path.parts
    if len(parts) >= 2 and parts[0] == "reports" and parts[1].startswith("v"):
        return True
    if (
        len(parts) >= 3
        and parts[0] == "docs"
        and parts[1] == "reports"
        and parts[2].startswith("v4")
    ):
        return True
    if len(parts) >= 2 and parts[0] == "data" and parts[1].startswith("v"):
        return True

    frozen_fragments = ("golden", "seal", "parity")
    frozen_exact = {"sha", "sha256", "hash", "hashes"}
    for part in parts:
        lowered = part.lower()
        if any(fragment in lowered for fragment in frozen_fragments):
            return True
        if lowered in frozen_exact:
            return True
    return False


def _assert_persist_path_allowed(output_path: Path, allowed_root: Path) -> None:
    output = output_path.resolve(strict=False)
    root = allowed_root.resolve(strict=False)
    if (
        not _is_relative_to(output, root)
        or _is_protected_repo_path(output)
        or _is_protected_repo_path(root)
    ):
        raise GuardViolation(
            path=output_path.as_posix(),
            reason="protected_output_root",
        )


def persist_no_order_artifact(
    artifact: dict[str, Any],
    output_path: str | os.PathLike[str],
    *,
    allowed_root: str | os.PathLike[str],
) -> Path:
    final_path = Path(output_path)
    _assert_persist_path_allowed(final_path, Path(allowed_root))
    validate_public_artifact(artifact)
    assert_public_material_safe(artifact)

    final_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, final_path)
    return final_path


def build_and_persist_no_order_artifact(
    input_data: NoOrderPreflightInput,
    outcome: OutcomeSpec,
    output_path: str | os.PathLike[str],
    *,
    allowed_root: str | os.PathLike[str],
) -> Path:
    artifact = build_no_order_artifact(input_data, outcome)
    return persist_no_order_artifact(
        artifact,
        output_path,
        allowed_root=allowed_root,
    )


def _ref_digest(label: str, value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{label}-sha256-{digest}"


def map_v8_5_live_fixture_input(fixture: dict[str, Any]) -> NoOrderPreflightInput:
    payload = fixture["candidate"]["surface_payload"]
    account = payload["account_proof"]
    market = payload["market_proof"]
    order = payload["order_intent"]
    kill_switch = payload["kill_switch_proof"]
    idempotency = payload["idempotency_proof"]

    return NoOrderPreflightInput(
        input_provenance=InputProvenance(
            kind="fixture",
            runtime_evidence_claim=False,
            source_ref=fixture.get("trace", {}).get("emitted_artifact_path"),
        ),
        account_proof={
            "account_alias": account["account_alias"],
            "broker_alias": account["broker_alias"],
            "account_snapshot_ref": account["account_snapshot_id"],
            "snapshot_ts": account["snapshot_ts"],
            "snapshot_max_age_ms": account["snapshot_max_age_ms"],
            "base_currency": account["base_currency"],
            "equity_ref": _ref_digest("equity-ref", account["equity"]),
            "cash_available_ref": _ref_digest(
                "cash-available-ref",
                account["cash_available"],
            ),
            "buying_power_ref": _ref_digest(
                "buying-power-ref",
                account["buying_power"],
            ),
            "open_exposure_digest": account["open_exposure_digest"],
            "open_orders_digest": account["open_orders_digest"],
        },
        market_proof={
            "symbol": market["symbol"],
            "market_snapshot_ref": market["market_snapshot_id"],
            "market_ts": market["market_ts"],
            "market_max_age_ms": market["market_max_age_ms"],
            "price_ref": _ref_digest(
                "price-ref",
                {"bid": market["bid"], "ask": market["ask"]},
            ),
            "spread_bps": market["spread_bps"],
            "price_source_alias": market["price_source"],
        },
        order_intent={
            "side": order["side"],
            "order_type": order["order_type"],
            "time_in_force": order["time_in_force"],
            "requested_notional": order["requested_notional"],
            "allowed_notional": order["allowed_notional"],
            "notional_currency": order["notional_currency"],
            "notional_source": order["notional_source"],
            "price_bounds_ref": _ref_digest(
                "price-bounds-ref",
                order["price_bounds"],
            ),
            "max_slippage_bps": order["max_slippage_bps"],
            "idempotency_key_hash": order["idempotency_key_hash"],
            "order_mutation_allowed": False,
        },
        kill_switch_proof={
            "global_gate_status": kill_switch["global_gate_status"],
            "global_gate_ref": kill_switch["global_gate_ref"],
            "strategy_gate_status": kill_switch["strategy_gate_status"],
            "strategy_gate_ref": kill_switch["strategy_gate_ref"],
            "symbol_gate_status": kill_switch["symbol_gate_status"],
            "symbol_gate_ref": kill_switch["symbol_gate_ref"],
            "broker_account_gate_status": kill_switch["broker_account_gate_status"],
            "broker_account_gate_ref": kill_switch["broker_account_gate_ref"],
            "proof_ts": kill_switch["proof_ts"],
            "proof_max_age_ms": kill_switch["proof_max_age_ms"],
        },
        idempotency_proof={
            "candidate_identity_digest": idempotency["candidate_identity_digest"],
            "idempotency_key_hash": idempotency["idempotency_key_hash"],
            "duplicate_check_status": idempotency["duplicate_check_status"],
            "duplicate_check_ref": idempotency["duplicate_check_ref"],
            "proof_ts": idempotency["proof_ts"],
            "proof_max_age_ms": idempotency["proof_max_age_ms"],
        },
    )
