"""Internal account-proof projector contract helper for Phase 158 tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping


VALID_PROVENANCE_KINDS = frozenset(
    {"synthetic_public", "fixture_sanitized_public", "already_sanitized_public"}
)
SOURCE_REF_MIN_LENGTH = 1
SOURCE_REF_MAX_LENGTH = 128
OPAQUE_HANDLE_MIN_LENGTH = 8
OPAQUE_HANDLE_MAX_LENGTH = 160
PUBLIC_MATERIAL_MAX_DEPTH = 65
PUBLIC_SOURCE_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
PUBLIC_OPAQUE_REF_PATTERN = (
    r"^public-[a-z][a-z0-9-]{2,31}-ref-[A-Za-z0-9][A-Za-z0-9._-]{7,127}$"
)
PUBLIC_DIGEST_REF_PATTERN = (
    r"^public-[a-z][a-z0-9-]{2,31}-digest-[A-Za-z0-9][A-Za-z0-9._-]{7,127}$"
)
_PUBLIC_REF_PAYLOAD_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}"
ALLOWED_ACCOUNT_PROOF_FIELDS = frozenset(
    {
        "account_alias",
        "broker_alias",
        "account_snapshot_ref",
        "snapshot_ts",
        "snapshot_max_age_ms",
        "base_currency",
        "equity_ref",
        "cash_available_ref",
        "buying_power_ref",
        "open_exposure_digest",
        "open_orders_digest",
        "staleness_ref",
    }
)
EVIDENCE_LABEL = "non-runtime/non-broker/non-account-fetch evidence"
_CLAIM_FIELDS = (
    "runtime_evidence_claim",
    "broker_evidence_claim",
    "account_fetch_evidence_claim",
)
_OPAQUE_REF_FIELDS = frozenset(
    {
        "account_snapshot_ref",
        "equity_ref",
        "cash_available_ref",
        "buying_power_ref",
        "staleness_ref",
    }
)
_DIGEST_REF_FIELDS = frozenset({"open_exposure_digest", "open_orders_digest"})
_ALIAS_FIELDS = frozenset({"account_alias", "broker_alias"})
_RAW_ACCOUNT_KEYS = frozenset(
    {
        "raw_account_id",
        "account_id",
        "account_number",
        "equity",
        "cash_available",
        "buying_power",
    }
)
_UNSAFE_KEY_FRAGMENTS = (
    "credential",
    "idempotency",
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
_UNSAFE_VALUE_FRAGMENTS = (
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
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "raw-account-id",
    "raw_account_id",
    "raw-idempotency",
    "raw_idempotency",
    "idempotency-key",
    "idempotency_key",
)
_RAW_DERIVATION_FRAGMENT_PATTERN = (
    r"(?:^|[._-])(?:raw[._-]?scalar|generated[._-]?from[._-]?raw)"
)


@dataclass(frozen=True)
class ProjectorViolation(Exception):
    path: str
    reason: str
    category: str = "projector_contract"

    def __str__(self) -> str:
        return f"{self.category}:{self.reason} at {self.path}"

    def __repr__(self) -> str:
        return (
            "ProjectorViolation("
            f"path={self.path!r}, reason={self.reason!r}, category={self.category!r})"
        )


@dataclass(frozen=True)
class AccountProofProjectorInput:
    provenance_kind: str
    source_ref: str
    runtime_evidence_claim: bool
    broker_evidence_claim: bool
    account_fetch_evidence_claim: bool
    account_proof: Mapping[str, Any]


@dataclass(frozen=True)
class AccountProofProjectorResult:
    account_proof: dict[str, Any]
    evidence_label: str
    input_provenance_kind: str
    source_ref: str
    runtime_evidence_claim: bool
    broker_evidence_claim: bool
    account_fetch_evidence_claim: bool


@dataclass(frozen=True)
class AccountProofFreshnessResult:
    status: str
    age_ms: int
    max_age_ms: int
    reason: str | None = None


def project_account_proof(
    input_data: AccountProofProjectorInput | Mapping[str, Any],
) -> AccountProofProjectorResult:
    normalized = _normalize_input(input_data)
    _assert_public_material_safe(_guard_payload(input_data, normalized), [])
    projected = _validate_account_proof(normalized.account_proof)

    return AccountProofProjectorResult(
        account_proof=projected,
        evidence_label=EVIDENCE_LABEL,
        input_provenance_kind=normalized.provenance_kind,
        source_ref=normalized.source_ref,
        runtime_evidence_claim=False,
        broker_evidence_claim=False,
        account_fetch_evidence_claim=False,
    )


def classify_account_proof_freshness(
    account_proof: Mapping[str, Any],
    *,
    now: datetime,
) -> AccountProofFreshnessResult:
    if not _is_utc_datetime(now):
        raise ProjectorViolation(path="now", reason="invalid_timestamp")
    if not isinstance(account_proof, Mapping):
        raise ProjectorViolation(path="account_proof", reason="invalid_account_proof")
    _assert_public_material_safe({"account_proof": account_proof}, [])
    projected = _validate_account_proof(account_proof)
    if "snapshot_ts" not in projected:
        raise ProjectorViolation(
            path="account_proof.snapshot_ts",
            reason="missing_timestamp",
        )
    if "snapshot_max_age_ms" not in projected:
        raise ProjectorViolation(
            path="account_proof.snapshot_max_age_ms",
            reason="invalid_snapshot_max_age_ms",
        )

    snapshot_ts = _parse_timestamp(
        projected["snapshot_ts"],
        "account_proof.snapshot_ts",
    )
    max_age_ms = projected["snapshot_max_age_ms"]
    _assert_snapshot_max_age_ms(
        max_age_ms,
        "account_proof.snapshot_max_age_ms",
    )

    age_delta = now - snapshot_ts
    if age_delta < timedelta(0):
        raise ProjectorViolation(
            path="account_proof.snapshot_ts",
            reason="future_snapshot_ts",
        )
    age_ms = age_delta // timedelta(milliseconds=1)
    if age_ms <= max_age_ms:
        return AccountProofFreshnessResult(
            status="current",
            age_ms=age_ms,
            max_age_ms=max_age_ms,
        )
    return AccountProofFreshnessResult(
        status="stale",
        age_ms=age_ms,
        max_age_ms=max_age_ms,
        reason="age_exceeds_snapshot_max_age_ms",
    )


def _normalize_input(
    input_data: AccountProofProjectorInput | Mapping[str, Any],
) -> AccountProofProjectorInput:
    if isinstance(input_data, AccountProofProjectorInput):
        candidate = {
            "provenance_kind": input_data.provenance_kind,
            "source_ref": input_data.source_ref,
            "runtime_evidence_claim": input_data.runtime_evidence_claim,
            "broker_evidence_claim": input_data.broker_evidence_claim,
            "account_fetch_evidence_claim": input_data.account_fetch_evidence_claim,
            "account_proof": input_data.account_proof,
        }
    elif isinstance(input_data, Mapping):
        candidate = input_data
    else:
        raise ProjectorViolation(path="$", reason="invalid_projector_input")

    if "provenance_kind" not in candidate:
        raise ProjectorViolation(
            path="provenance_kind",
            reason="missing_provenance_kind",
        )
    provenance_kind = candidate["provenance_kind"]
    if (
        not isinstance(provenance_kind, str)
        or provenance_kind not in VALID_PROVENANCE_KINDS
    ):
        raise ProjectorViolation(
            path="provenance_kind",
            reason="invalid_provenance_kind",
        )

    source_ref = _validate_source_ref(candidate.get("source_ref"))
    for field in _CLAIM_FIELDS:
        if field not in candidate:
            raise ProjectorViolation(path=field, reason="missing_evidence_claim")
        if candidate[field] is not False:
            raise ProjectorViolation(path=field, reason="invalid_evidence_claim")

    account_proof = candidate.get("account_proof")
    if not isinstance(account_proof, Mapping):
        raise ProjectorViolation(path="account_proof", reason="invalid_account_proof")

    return AccountProofProjectorInput(
        provenance_kind=provenance_kind,
        source_ref=source_ref,
        runtime_evidence_claim=False,
        broker_evidence_claim=False,
        account_fetch_evidence_claim=False,
        account_proof=account_proof,
    )


def _validate_source_ref(value: Any) -> str:
    if value is None or value == "":
        raise ProjectorViolation(path="source_ref", reason="missing_source_ref")
    if not isinstance(value, str) or value.strip() == "":
        raise ProjectorViolation(path="source_ref", reason="invalid_source_ref")
    if not _is_public_source_ref(value):
        raise ProjectorViolation(path="source_ref", reason="invalid_source_ref")
    return value


def _guard_payload(
    input_data: AccountProofProjectorInput | Mapping[str, Any],
    normalized: AccountProofProjectorInput,
) -> dict[str, Any]:
    if isinstance(input_data, Mapping):
        return dict(input_data)
    return {
        "provenance_kind": normalized.provenance_kind,
        "source_ref": normalized.source_ref,
        "runtime_evidence_claim": normalized.runtime_evidence_claim,
        "broker_evidence_claim": normalized.broker_evidence_claim,
        "account_fetch_evidence_claim": normalized.account_fetch_evidence_claim,
        "account_proof": normalized.account_proof,
    }


def _validate_account_proof(account_proof: Mapping[str, Any]) -> dict[str, Any]:
    for key in account_proof:
        if not isinstance(key, str):
            raise ProjectorViolation(
                path="account_proof.<unsupported_key>",
                reason="unsupported_account_proof_field",
            )

    extra_fields = sorted(set(account_proof) - ALLOWED_ACCOUNT_PROOF_FIELDS)
    if extra_fields:
        raise ProjectorViolation(
            path=f"account_proof.{extra_fields[0]}",
            reason="unsupported_account_proof_field",
        )

    projected: dict[str, Any] = {}
    for field, value in account_proof.items():
        path = f"account_proof.{field}"
        if field in _ALIAS_FIELDS:
            _assert_public_alias(value, path)
        elif field in _OPAQUE_REF_FIELDS:
            _assert_public_opaque_ref(value, path)
        elif field in _DIGEST_REF_FIELDS:
            _assert_public_digest_ref(value, path)
        elif field == "snapshot_ts":
            _assert_timestamp(value, path)
        elif field == "snapshot_max_age_ms":
            _assert_snapshot_max_age_ms(value, path)
        elif field == "base_currency":
            _assert_base_currency(value, path)
        projected[field] = value
    return projected


def _assert_public_material_safe(value: Any, parts: list[str]) -> None:
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
                    path=_unsafe_key_path(parts),
                    reason="unsafe_public_material",
                )
            _assert_public_material_safe(nested, key_path)
        return
    if isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            _assert_public_material_safe(nested, [*parts, f"[{index}]"])
        return
    if _is_allowed_account_proof_value_path(parts):
        return
    if isinstance(value, str):
        if _unsafe_string_reason(value) is not None or _is_account_amount_shape(value):
            raise ProjectorViolation(
                path=_format_path(parts),
                reason="unsafe_public_material",
            )
    elif isinstance(value, int | float) and not isinstance(value, bool):
        raise ProjectorViolation(
            path=_format_path(parts),
            reason="unsafe_public_material",
        )
    elif not _is_allowed_false_evidence_claim(parts, value):
        raise ProjectorViolation(
            path=_format_path(parts),
            reason="unsafe_public_material",
        )


def _unsafe_key_reason(key: str) -> str | None:
    lowered = key.lower()
    normalized = _normalize_public_material_identifier(key)
    if normalized in _RAW_ACCOUNT_KEYS:
        return "unsafe_public_material"
    if normalized in {
        "idempotency",
        "idempotency_key",
        "raw_idempotency",
        "raw_idempotency_key",
    }:
        return "unsafe_public_material"
    if normalized not in ALLOWED_ACCOUNT_PROOF_FIELDS and _has_unsafe_key_prefix(
        normalized
    ):
        return "unsafe_public_material"
    if _contains_unsafe_fragment(key, _UNSAFE_KEY_FRAGMENTS):
        return "unsafe_public_material"
    return None


def _unsafe_string_reason(value: str) -> str | None:
    lowered = value.lower()
    normalized = _normalize_public_material_identifier(value)
    if _is_url_like(lowered):
        return "unsafe_public_material"
    if (
        re.search(_RAW_DERIVATION_FRAGMENT_PATTERN, lowered) is not None
        or re.search(_RAW_DERIVATION_FRAGMENT_PATTERN, normalized) is not None
    ):
        return "unsafe_public_material"
    if _contains_unsafe_fragment(value, _UNSAFE_VALUE_FRAGMENTS):
        return "unsafe_public_material"
    return None


def _contains_unsafe_fragment(value: str, fragments: tuple[str, ...]) -> bool:
    lowered = value.lower()
    normalized = _normalize_public_material_identifier(value)
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    return any(
        fragment in lowered
        or fragment.replace("-", "_") in normalized
        or fragment.replace("_", "").replace("-", "") in compact
        for fragment in fragments
    )


def _normalize_public_material_identifier(value: str) -> str:
    acronym_folded = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", value)
    camel_folded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", acronym_folded)
    return camel_folded.replace("-", "_").lower()


def _has_unsafe_key_prefix(normalized: str) -> bool:
    unsafe_prefixes = (
        "account_id",
        "account_number",
        "raw_account_id",
        "equity",
        "cash_available",
        "buying_power",
        "idempotency",
        "raw_idempotency",
    )
    return any(normalized.startswith(f"{prefix}_") for prefix in unsafe_prefixes)


def _assert_public_alias(value: Any, path: str) -> None:
    if (
        not isinstance(value, str)
        or not _is_public_text(value, max_length=80)
        or _is_account_amount_shape(value)
    ):
        raise ProjectorViolation(path=path, reason="invalid_public_alias")


def _assert_public_opaque_ref(value: Any, path: str) -> None:
    if not isinstance(value, str) or not _is_public_opaque_ref(value):
        raise ProjectorViolation(path=path, reason="invalid_public_opaque_ref")


def _assert_public_digest_ref(value: Any, path: str) -> None:
    if not isinstance(value, str) or not _is_public_digest_ref(value):
        raise ProjectorViolation(path=path, reason="invalid_public_digest_ref")


def _assert_timestamp(value: Any, path: str) -> None:
    _parse_timestamp(value, path)


def _parse_timestamp(value: Any, path: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
        value,
    ):
        raise ProjectorViolation(path=path, reason="invalid_timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ProjectorViolation(path=path, reason="invalid_timestamp") from exc


def _assert_snapshot_max_age_ms(value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProjectorViolation(path=path, reason="invalid_snapshot_max_age_ms")


def _assert_base_currency(value: Any, path: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z]{3}", value) is None:
        raise ProjectorViolation(path=path, reason="invalid_base_currency")


def _is_utc_datetime(value: Any) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def _is_public_source_ref(value: str) -> bool:
    return (
        SOURCE_REF_MIN_LENGTH <= len(value) <= SOURCE_REF_MAX_LENGTH
        and value.strip() == value
        and re.fullmatch(PUBLIC_SOURCE_REF_PATTERN, value) is not None
        and _unsafe_string_reason(value) is None
    )


def _is_public_text(value: str, *, max_length: int) -> bool:
    return (
        1 <= len(value) <= max_length
        and value.strip() == value
        and " " not in value
        and re.fullmatch(PUBLIC_SOURCE_REF_PATTERN, value) is not None
        and _unsafe_string_reason(value) is None
    )


def _is_public_opaque_ref(value: str) -> bool:
    return (
        OPAQUE_HANDLE_MIN_LENGTH <= len(value) <= OPAQUE_HANDLE_MAX_LENGTH
        and value.strip() == value
        and re.fullmatch(PUBLIC_OPAQUE_REF_PATTERN, value) is not None
        and _has_structural_public_ref_payload(value, "-ref-")
        and _unsafe_string_reason(value) is None
    )


def _is_public_digest_ref(value: str) -> bool:
    return (
        OPAQUE_HANDLE_MIN_LENGTH <= len(value) <= OPAQUE_HANDLE_MAX_LENGTH
        and value.strip() == value
        and re.fullmatch(PUBLIC_DIGEST_REF_PATTERN, value) is not None
        and _has_structural_public_ref_payload(value, "-digest-")
        and _unsafe_string_reason(value) is None
    )


def _has_structural_public_ref_payload(value: str, marker: str) -> bool:
    if marker not in value:
        return False
    payload = value.rsplit(marker, 1)[1]
    return re.fullmatch(_PUBLIC_REF_PAYLOAD_PATTERN, payload) is not None


def _is_allowed_account_proof_value_path(parts: list[str]) -> bool:
    return len(parts) == 2 and parts[0] == "account_proof" and parts[1] in (
        ALLOWED_ACCOUNT_PROOF_FIELDS
    )


def _is_allowed_false_evidence_claim(parts: list[str], value: Any) -> bool:
    return len(parts) == 1 and parts[0] in _CLAIM_FIELDS and value is False


def _is_url_like(lowered: str) -> bool:
    return (
        "://" in lowered
        or lowered.startswith("www.")
        or ".internal" in lowered
        or "localhost" in lowered
    )


def _is_account_amount_shape(value: str) -> bool:
    return (
        re.fullmatch(
            r"(?:[A-Z]{3}\s*)?[$]?[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)"
            r"(?:\.\d+)?(?:\s*[A-Z]{3})?",
            value.strip(),
            flags=re.IGNORECASE,
        )
        is not None
    )


def _unsafe_key_path(parts: list[str]) -> str:
    return _format_path([*parts, "<unsafe_key>"])


def _depth_limit_path(parts: list[str]) -> str:
    return _format_path([*parts[:PUBLIC_MATERIAL_MAX_DEPTH], "<max_depth>"])


def _format_path(parts: list[str]) -> str:
    return ".".join(parts) if parts else "$"
