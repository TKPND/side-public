"""Phase 159 account-proof projector rejection matrix tests."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.live_preflight_account_proof_projector import (
    ALLOWED_ACCOUNT_PROOF_FIELDS,
    ProjectorViolation,
    project_account_proof,
)


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "tests/helpers/live_preflight_account_proof_projector.py"
MATRIX_TEST_PATH = ROOT / "tests/test_live_preflight_account_proof_projector.py"
FRESHNESS_TEST_PATH = (
    ROOT / "tests/test_live_preflight_account_proof_projector_freshness.py"
)
PHASE159_GUARD_TARGETS = (
    HELPER_PATH,
    MATRIX_TEST_PATH,
    FRESHNESS_TEST_PATH,
)

EXPECTED_PHASE159_GUARD_TARGETS = {
    "tests/helpers/live_preflight_account_proof_projector.py",
    "tests/test_live_preflight_account_proof_projector.py",
    "tests/test_live_preflight_account_proof_projector_freshness.py",
}
FORBIDDEN_IMPORT_ROOTS = {
    "side",
    "scripts",
    "tests.helpers.live_preflight_result_builder",
    "tests.helpers.live_preflight_guard_entrypoint",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "http",
    "socket",
    "subprocess",
    "os",
    "shutil",
    "dotenv",
    "keyring",
    "google.cloud.secretmanager",
    "hash" + "lib",
}
FORBIDDEN_DYNAMIC_IMPORT_ROOTS = {"import" + "lib"}
FORBIDDEN_CALL_NAMES = {
    "build_no_order_artifact",
    "build_and_persist_no_order_artifact",
    "map_v8_5_live_fixture_input",
    "place_order",
    "submit_order",
    "cancel_order",
    "fetch_account",
    "__" + "import__",
    "import_" + "module",
    "eval",
    "exec",
}
FORBIDDEN_SOURCE_TOKENS = {
    "_ref_" + "digest",
    "hash" + "lib",
    "sha" + "256",
    "hex" + "digest",
    'if __name__ == "__main__"',
    "live_preflight_result_builder",
    "build_no_order_artifact",
    "map_v8_5_live_fixture_input",
    "broker_adapter",
    "broker_client",
    "place_order",
    "submit_order",
    "cancel_order",
    "fetch_account",
    "account_fetcher",
    "runtime_public_emission",
    "side live",
    "requests.",
    "httpx.",
    "aiohttp.",
    "urllib.",
    "http.client",
    "socket.",
    "subprocess.",
    "os.environ",
    "os.getenv",
    "getenv(",
    "load_dotenv",
    "SecretManagerServiceClient",
    "Path.write_text",
    "Path.write_bytes",
    ".write_text(",
    ".write_bytes(",
    "open(",
    "json.dump(",
    "pickle.dump(",
    "save_",
}
UNSAFE_SENTINELS = (
    "synthetic-unsafe-secret-alpha",
    "synthetic-private-endpoint-alpha",
    "synthetic-raw-scalar-alpha",
    "synthetic-token-fragment-alpha",
    "synthetic-cookie-fragment-alpha",
    "synthetic-broker-secret-fragment-alpha",
    "synthetic-raw-idempotency-alpha",
)


def _deep_public_material(depth: int) -> dict[str, Any]:
    value: object = "public-redacted"
    for _ in range(depth):
        value = {"child": value}
    assert isinstance(value, dict)
    return value


class _SyntheticPrivateObject:
    pass


def _valid_account_proof() -> dict[str, Any]:
    return {
        "account_alias": "public-account-alias",
        "broker_alias": "public-broker-alias",
        "account_snapshot_ref": "public-account-snapshot-ref-phase159matrix001",
        "snapshot_ts": "2026-05-28T00:00:00Z",
        "snapshot_max_age_ms": 30000,
        "base_currency": "USD",
        "equity_ref": "public-equity-ref-phase159matrix001",
        "cash_available_ref": "public-cash-available-ref-phase159matrix001",
        "buying_power_ref": "public-buying-power-ref-phase159matrix001",
        "open_exposure_digest": "public-open-exposure-digest-phase159matrix001",
        "open_orders_digest": "public-open-orders-digest-phase159matrix001",
        "staleness_ref": "public-staleness-ref-phase159matrix001",
    }


def _safe_input(
    *,
    account_proof: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "provenance_kind": "synthetic_public",
        "source_ref": "phase159-synthetic-public-input",
        "runtime_evidence_claim": False,
        "broker_evidence_claim": False,
        "account_fetch_evidence_claim": False,
        "account_proof": account_proof or _valid_account_proof(),
    }
    if extra:
        payload.update(extra)
    return payload


def _with_account_proof_field(field: str, value: object) -> dict[str, Any]:
    account_proof = _valid_account_proof()
    account_proof[field] = value
    return _safe_input(account_proof=account_proof)


def _assert_projector_violation(
    error: pytest.ExceptionInfo[ProjectorViolation],
    *,
    path: str,
    reason: str,
    category: str,
) -> None:
    assert error.value.path == path
    assert error.value.reason == reason
    assert error.value.category == category


def _expect_violation(
    payload: object,
    *,
    path: str,
    reason: str,
    category: str = "projector_contract",
) -> ProjectorViolation:
    with pytest.raises(ProjectorViolation) as error:
        project_account_proof(payload)
    _assert_projector_violation(
        error,
        path=path,
        reason=reason,
        category=category,
    )
    return error.value


REJECTION_MATRIX = (
    pytest.param(
        "SAP-INPUT-02",
        _safe_input(
            extra={
                "synthetic_fixture_internals": {
                    "account_number": "synthetic-redacted-account-number"
                }
            }
        ),
        "synthetic_fixture_internals.<unsafe_key>",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-INPUT-02__raw_fixture_internal_key_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-01",
        _with_account_proof_field("unexpected_public_field", "public-redacted-ref"),
        "account_proof.unexpected_public_field",
        "unsupported_account_proof_field",
        "projector_contract",
        id="SAP-PROJECT-01__unsupported_account_proof_key_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"raw_account_id": "synthetic-redacted-id"}}),
        "metadata.<unsafe_key>",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__raw_private_identifier_key_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"raw-account-id": "public-redacted-ref"}}),
        "metadata.<unsafe_key>",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__hyphenated_raw_private_identifier_key_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"account-number": "public-redacted-ref"}}),
        "metadata.<unsafe_key>",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__hyphenated_account_number_key_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"idempotency-key": "public-redacted-ref"}}),
        "metadata.<unsafe_key>",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__hyphenated_idempotency_key_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"raw-idempotency-key": "public-redacted-ref"}}),
        "metadata.<unsafe_key>",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__hyphenated_raw_idempotency_key_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"raw-idempotency": "public-redacted-ref"}}),
        "metadata.<unsafe_key>",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__hyphenated_raw_idempotency_key_material_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"raw_idempotency": "public-redacted-ref"}}),
        "metadata.<unsafe_key>",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__raw_idempotency_key_material_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"notional_hint": "00000.00"}}),
        "metadata.notional_hint",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__raw_account_scalar_shape_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"note": UNSAFE_SENTINELS[2]}}),
        "metadata.note",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__raw_scalar_fragment_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"note": UNSAFE_SENTINELS[6]}}),
        "metadata.note",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__raw_idempotency_shaped_material_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"note": UNSAFE_SENTINELS[3]}}),
        "metadata.note",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__credential_like_fragment_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"note": "synthetic://unsafe-fragment"}}),
        "metadata.note",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__url_like_fragment_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-02",
        _safe_input(extra={"metadata": {"note": UNSAFE_SENTINELS[5]}}),
        "metadata.note",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-02__broker_secret_like_fragment_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-03",
        _with_account_proof_field("equity_ref", "00000.00"),
        "account_proof.equity_ref",
        "invalid_public_opaque_ref",
        "projector_contract",
        id="SAP-PROJECT-03__raw_scalar_equity_ref_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-03",
        _with_account_proof_field(
            "equity_ref",
            "public-equity-ref-generated-from-raw-scalar001",
        ),
        "account_proof.equity_ref",
        "invalid_public_opaque_ref",
        "projector_contract",
        id="SAP-PROJECT-03__generated_ref_shaped_value_rejects",
    ),
    pytest.param(
        "SAP-PROJECT-05",
        _safe_input(extra={"metadata": {"note": UNSAFE_SENTINELS[0]}}),
        "metadata.note",
        "unsafe_public_material",
        "projector_contract",
        id="SAP-PROJECT-05__unsafe_value_surface_rejects",
    ),
)


@pytest.mark.parametrize(
    (
        "requirement_id",
        "payload",
        "expected_path",
        "expected_reason",
        "expected_category",
    ),
    REJECTION_MATRIX,
)
def test_requirement_owned_rejection_matrix(
    requirement_id: str,
    payload: dict[str, Any],
    expected_path: str,
    expected_reason: str,
    expected_category: str,
) -> None:
    assert requirement_id in {
        "SAP-INPUT-02",
        "SAP-PROJECT-01",
        "SAP-PROJECT-02",
        "SAP-PROJECT-03",
        "SAP-PROJECT-04",
        "SAP-PROJECT-05",
    }
    _expect_violation(
        payload,
        path=expected_path,
        reason=expected_reason,
        category=expected_category,
    )


def test_projector_allows_only_public_account_proof_fields() -> None:
    result = project_account_proof(_safe_input())

    assert frozenset(result.account_proof) == ALLOWED_ACCOUNT_PROOF_FIELDS
    assert result.account_proof == _valid_account_proof()

    _expect_violation(
        _with_account_proof_field("unexpected_public_field", "public-redacted-ref"),
        path="account_proof.unexpected_public_field",
        reason="unsupported_account_proof_field",
    )


def test_projector_rejects_mixed_type_account_proof_keys_without_type_error() -> None:
    account_proof: dict[Any, Any] = dict(_valid_account_proof())
    account_proof[1] = "public-redacted-ref"
    account_proof["unexpected_public_field"] = "public-redacted-ref"

    _expect_violation(
        _safe_input(account_proof=account_proof),
        path="account_proof.<unsupported_key>",
        reason="unsupported_account_proof_field",
    )


def test_projector_accepts_hyphenated_public_ref_payloads() -> None:
    account_proof = _valid_account_proof()
    account_proof["equity_ref"] = "public-equity-ref-phase-159-passthrough001"
    account_proof["cash_available_ref"] = (
        "public-cash-available-ref-phase-159-passthrough001"
    )
    account_proof["buying_power_ref"] = (
        "public-buying-power-ref-phase-159-passthrough001"
    )
    account_proof["open_exposure_digest"] = (
        "public-open-exposure-digest-phase-159-passthrough001"
    )
    account_proof["open_orders_digest"] = (
        "public-open-orders-digest-phase-159-passthrough001"
    )
    account_proof["staleness_ref"] = "public-staleness-ref-phase-159-passthrough001"

    result = project_account_proof(_safe_input(account_proof=account_proof))

    assert result.account_proof["equity_ref"] == account_proof["equity_ref"]
    assert result.account_proof["cash_available_ref"] == account_proof[
        "cash_available_ref"
    ]
    assert result.account_proof["buying_power_ref"] == account_proof[
        "buying_power_ref"
    ]
    assert result.account_proof["open_exposure_digest"] == account_proof[
        "open_exposure_digest"
    ]
    assert result.account_proof["open_orders_digest"] == account_proof[
        "open_orders_digest"
    ]
    assert result.account_proof["staleness_ref"] == account_proof["staleness_ref"]


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    (
        pytest.param(
            "equity_ref",
            "public-equity-ref-raw-scalar001",
            "invalid_public_opaque_ref",
            id="opaque_ref_rejects_raw_scalar_digit_suffix",
        ),
        pytest.param(
            "cash_available_ref",
            "public-cash-available-ref-generated-from-raw001",
            "invalid_public_opaque_ref",
            id="opaque_ref_rejects_generated_from_raw_digit_suffix",
        ),
        pytest.param(
            "buying_power_ref",
            "public-buying-power-ref-rawscalar001",
            "invalid_public_opaque_ref",
            id="opaque_ref_rejects_rawscalar_digit_suffix",
        ),
        pytest.param(
            "open_exposure_digest",
            "public-open-exposure-digest-generatedfromraw001",
            "invalid_public_digest_ref",
            id="digest_ref_rejects_generatedfromraw_digit_suffix",
        ),
        pytest.param(
            "open_orders_digest",
            "public-open-orders-digest-raw-scalar001",
            "invalid_public_digest_ref",
            id="digest_ref_rejects_raw_scalar_digit_suffix",
        ),
    ),
)
def test_public_refs_reject_raw_derived_suffix_fragments(
    field: str,
    value: str,
    expected_reason: str,
) -> None:
    _expect_violation(
        _with_account_proof_field(field, value),
        path=f"account_proof.{field}",
        reason=expected_reason,
    )


def test_value_refs_are_pass_through_only_for_public_safe_handles() -> None:
    account_proof = _valid_account_proof()
    account_proof["equity_ref"] = "public-equity-ref-phase159passthrough001"
    account_proof["cash_available_ref"] = (
        "public-cash-available-ref-phase159passthrough001"
    )
    account_proof["buying_power_ref"] = (
        "public-buying-power-ref-phase159passthrough001"
    )

    result = project_account_proof(_safe_input(account_proof=account_proof))

    assert result.account_proof["equity_ref"] == account_proof["equity_ref"]
    assert result.account_proof["cash_available_ref"] == account_proof[
        "cash_available_ref"
    ]
    assert result.account_proof["buying_power_ref"] == account_proof[
        "buying_power_ref"
    ]

    for field in ("equity_ref", "cash_available_ref", "buying_power_ref"):
        _expect_violation(
            _with_account_proof_field(field, "00000.00"),
            path=f"account_proof.{field}",
            reason="invalid_public_opaque_ref",
        )
        _expect_violation(
            _with_account_proof_field(
                field,
                f"public-{field.replace('_', '-')}-generated-from-raw-scalar001",
            ),
            path=f"account_proof.{field}",
            reason="invalid_public_opaque_ref",
        )
        _expect_violation(
            _with_account_proof_field(
                field,
                f"public-{field.replace('_', '-')}-ref-raw_scalar_value_001",
            ),
            path=f"account_proof.{field}",
            reason="invalid_public_opaque_ref",
        )

    helper_source = HELPER_PATH.read_text(encoding="utf-8")
    assert '"raw-scalar"' not in helper_source
    assert '"generated-from-raw"' not in helper_source


SHAPE_VALIDATION_CASES = (
    pytest.param(
        "SAP-PROJECT-04",
        "account_alias",
        123,
        "invalid_public_alias",
        id="SAP-PROJECT-04__account_alias_rejects_type",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "account_alias",
        "public account alias",
        "invalid_public_alias",
        id="SAP-PROJECT-04__account_alias_rejects_whitespace",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "account_alias",
        "a" * 81,
        "invalid_public_alias",
        id="SAP-PROJECT-04__account_alias_rejects_length",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "account_alias",
        "synthetic://unsafe-fragment",
        "invalid_public_alias",
        id="SAP-PROJECT-04__account_alias_rejects_url_like",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "broker_alias",
        UNSAFE_SENTINELS[0],
        "invalid_public_alias",
        id="SAP-PROJECT-04__broker_alias_rejects_secretish",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "account_snapshot_ref",
        None,
        "invalid_public_opaque_ref",
        id="SAP-PROJECT-04__account_snapshot_ref_rejects_type",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "account_snapshot_ref",
        "short",
        "invalid_public_opaque_ref",
        id="SAP-PROJECT-04__account_snapshot_ref_rejects_pattern",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "equity_ref",
        " public-equity-ref-phase159matrix001",
        "invalid_public_opaque_ref",
        id="SAP-PROJECT-04__equity_ref_rejects_whitespace",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "cash_available_ref",
        "public-cash-available-ref-" + ("a" * 200),
        "invalid_public_opaque_ref",
        id="SAP-PROJECT-04__cash_available_ref_rejects_length",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "buying_power_ref",
        "synthetic://unsafe-fragment",
        "invalid_public_opaque_ref",
        id="SAP-PROJECT-04__buying_power_ref_rejects_url_like",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "staleness_ref",
        UNSAFE_SENTINELS[0],
        "invalid_public_opaque_ref",
        id="SAP-PROJECT-04__staleness_ref_rejects_secretish",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "open_exposure_digest",
        123,
        "invalid_public_digest_ref",
        id="SAP-PROJECT-04__open_exposure_digest_rejects_type",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "open_exposure_digest",
        "open-exposure-digest-phase159matrix001",
        "invalid_public_digest_ref",
        id="SAP-PROJECT-04__open_exposure_digest_rejects_pattern",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "open_orders_digest",
        "public-open-orders-digest-" + ("a" * 200),
        "invalid_public_digest_ref",
        id="SAP-PROJECT-04__open_orders_digest_rejects_length",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "open_orders_digest",
        "public-open-orders-digest-has space",
        "invalid_public_digest_ref",
        id="SAP-PROJECT-04__open_orders_digest_rejects_whitespace",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "snapshot_ts",
        "2026-05-28 00:00:00",
        "invalid_timestamp",
        id="SAP-PROJECT-04__snapshot_ts_rejects_pattern",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "snapshot_ts",
        "2026-05-28T00:00:00Z ",
        "invalid_timestamp",
        id="SAP-PROJECT-04__snapshot_ts_rejects_whitespace",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "snapshot_ts",
        "2026-05-28T00:00:00.001Z",
        "invalid_timestamp",
        id="SAP-PROJECT-04__snapshot_ts_rejects_fractional_seconds",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "snapshot_ts",
        "2026-05-28T00:00:00+00:00",
        "invalid_timestamp",
        id="SAP-PROJECT-04__snapshot_ts_rejects_offset",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "snapshot_ts",
        "synthetic://unsafe-fragment",
        "invalid_timestamp",
        id="SAP-PROJECT-04__snapshot_ts_rejects_url_like",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "snapshot_max_age_ms",
        True,
        "invalid_snapshot_max_age_ms",
        id="SAP-PROJECT-04__snapshot_max_age_ms_rejects_bool",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "snapshot_max_age_ms",
        0,
        "invalid_snapshot_max_age_ms",
        id="SAP-PROJECT-04__snapshot_max_age_ms_rejects_non_positive",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "snapshot_max_age_ms",
        "30000",
        "invalid_snapshot_max_age_ms",
        id="SAP-PROJECT-04__snapshot_max_age_ms_rejects_string",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "base_currency",
        "usd",
        "invalid_base_currency",
        id="SAP-PROJECT-04__base_currency_rejects_lowercase",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "base_currency",
        "USD ",
        "invalid_base_currency",
        id="SAP-PROJECT-04__base_currency_rejects_whitespace",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "base_currency",
        "USDD",
        "invalid_base_currency",
        id="SAP-PROJECT-04__base_currency_rejects_length",
    ),
    pytest.param(
        "SAP-PROJECT-04",
        "base_currency",
        "synthetic://unsafe-fragment",
        "invalid_base_currency",
        id="SAP-PROJECT-04__base_currency_rejects_url_like",
    ),
)


@pytest.mark.parametrize(
    ("requirement_id", "field", "value", "expected_reason"),
    SHAPE_VALIDATION_CASES,
)
def test_allowed_account_proof_fields_have_path_specific_shape_validation(
    requirement_id: str,
    field: str,
    value: object,
    expected_reason: str,
) -> None:
    assert requirement_id == "SAP-PROJECT-04"
    _expect_violation(
        _with_account_proof_field(field, value),
        path=f"account_proof.{field}",
        reason=expected_reason,
    )


def test_projector_violation_surfaces_never_echo_unsafe_values() -> None:
    payload = _safe_input(extra={"metadata": {"note": UNSAFE_SENTINELS[0]}})
    violation = _expect_violation(
        payload,
        path="metadata.note",
        reason="unsafe_public_material",
    )

    assert set(violation.__dict__) == {"path", "reason", "category"}
    surfaces = (
        violation.path,
        violation.reason,
        violation.category,
        str(violation),
        repr(violation),
    )
    for surface in surfaces:
        for sentinel in UNSAFE_SENTINELS:
            assert sentinel not in surface

    unsafe_key = f"token_{UNSAFE_SENTINELS[0]}"
    key_violation = _expect_violation(
        _safe_input(extra={unsafe_key: "public-redacted-ref"}),
        path="<unsafe_key>",
        reason="unsafe_public_material",
    )
    for surface in (
        key_violation.path,
        key_violation.reason,
        key_violation.category,
        str(key_violation),
        repr(key_violation),
    ):
        assert unsafe_key not in surface
        assert UNSAFE_SENTINELS[0] not in surface


def test_projector_rejects_excessively_deep_public_material() -> None:
    expected_path = "metadata." + ".".join(["child"] * 64) + ".<max_depth>"

    violation = _expect_violation(
        _safe_input(extra={"metadata": _deep_public_material(1200)}),
        path=expected_path,
        reason="unsafe_public_material",
    )

    assert violation.category == "projector_contract"


@pytest.mark.parametrize(
    ("value", "expected_path"),
    (
        pytest.param(b"synthetic-bytes", "metadata.note", id="bytes"),
        pytest.param({"synthetic-set"}, "metadata.note", id="set"),
        pytest.param(frozenset({"synthetic-frozen"}), "metadata.note", id="frozenset"),
        pytest.param(1 + 2j, "metadata.note", id="complex"),
        pytest.param(_SyntheticPrivateObject(), "metadata.note", id="custom_object"),
    ),
)
def test_projector_rejects_unsupported_public_material_types(
    value: object,
    expected_path: str,
) -> None:
    _expect_violation(
        _safe_input(extra={"metadata": {"note": value}}),
        path=expected_path,
        reason="unsafe_public_material",
    )


@pytest.mark.parametrize(
    ("payload", "expected_path"),
    (
        pytest.param(
            _safe_input(extra={"accountId": "public-redacted-ref"}),
            "<unsafe_key>",
            id="top_level_account_id_camel_case_key",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"AccountNumber": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_account_number_pascal_case_key",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"rawAccountId": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_raw_account_id_camel_case_key",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"BuyingPower": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_buying_power_pascal_case_key",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"cashAvailable": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_cash_available_camel_case_key",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"equityValue": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_equity_value_camel_case_key",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"idempotencyKey": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_idempotency_key_camel_case_key",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"IdempotencyKey": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_idempotency_key_pascal_case_key",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"rawIdempotency": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_raw_idempotency_camel_case_key",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"idempotency_key_hash": "public-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_idempotency_key_hash_suffix",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"myIdempotencyKey": "public-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_compound_idempotency_key_camel_case",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"APIKey": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_api_key_acronym_key",
        ),
        pytest.param(
            _safe_input(extra={"metadata": {"apikey": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
            id="nested_api_key_compact_key",
        ),
    ),
)
def test_projector_rejects_camel_case_raw_account_and_idempotency_keys(
    payload: dict[str, Any],
    expected_path: str,
) -> None:
    _expect_violation(
        payload,
        path=expected_path,
        reason="unsafe_public_material",
    )


@pytest.mark.parametrize(
    "value",
    (
        pytest.param("rawAccountId", id="raw_account_id_camel_case_value"),
        pytest.param("idempotencyKey12345", id="idempotency_key_camel_case_value"),
        pytest.param("myIdempotencyKey", id="compound_idempotency_key_value"),
        pytest.param("apiKeySynthetic", id="api_key_camel_case_value"),
        pytest.param("APIKeySynthetic", id="api_key_acronym_value"),
        pytest.param("APIKey", id="api_key_acronym_only_value"),
        pytest.param("apikey", id="api_key_compact_value"),
    ),
)
def test_projector_rejects_camel_case_unsafe_value_fragments(value: str) -> None:
    _expect_violation(
        _safe_input(extra={"metadata": {"note": value}}),
        path="metadata.note",
        reason="unsafe_public_material",
    )


def _imports_from(tree: ast.AST) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def _root_import_violations(imports: list[str], forbidden: set[str]) -> list[str]:
    violations: list[str] = []
    for imported in imports:
        for root in forbidden:
            if imported == root or imported.startswith(f"{root}."):
                violations.append(imported)
    return sorted(set(violations))


def _call_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _has_main_guard(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        left_is_name = isinstance(test.left, ast.Name) and test.left.id == "__name__"
        compares_to_main = any(
            isinstance(comparator, ast.Constant) and comparator.value == "__main__"
            for comparator in test.comparators
        )
        if left_is_name and compares_to_main:
            return True
    return False


def _guard_relative_path(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _source_without_guard_constants(source: str, path: Path) -> str:
    if path != MATRIX_TEST_PATH:
        return source
    start = source.index("EXPECTED_PHASE159_GUARD_TARGETS = {")
    end = source.index("\n}\nUNSAFE_SENTINELS", start) + len("\n}\n")
    return source[:start] + source[end:]


def test_phase159_source_absence_guards() -> None:
    assert {_guard_relative_path(path) for path in PHASE159_GUARD_TARGETS} == (
        EXPECTED_PHASE159_GUARD_TARGETS
    )

    existing_targets = [path for path in PHASE159_GUARD_TARGETS if path.exists()]
    assert {_guard_relative_path(path) for path in existing_targets} == (
        EXPECTED_PHASE159_GUARD_TARGETS
    )

    for path in existing_targets:
        source = path.read_text(encoding="utf-8")
        guarded_source = _source_without_guard_constants(source, path)
        tree = ast.parse(source)
        assert _root_import_violations(
            _imports_from(tree),
            FORBIDDEN_IMPORT_ROOTS | FORBIDDEN_DYNAMIC_IMPORT_ROOTS,
        ) == []
        assert not (FORBIDDEN_CALL_NAMES & _call_names(tree))
        assert not _has_main_guard(tree)
        for token in FORBIDDEN_SOURCE_TOKENS:
            assert token not in guarded_source
