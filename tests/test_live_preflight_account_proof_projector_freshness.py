"""Phase 159 account-proof freshness classifier boundary tests."""

from __future__ import annotations

import ast
from dataclasses import is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.live_preflight_account_proof_projector import ProjectorViolation

try:
    from tests.helpers.live_preflight_account_proof_projector import (
        AccountProofFreshnessResult,
        classify_account_proof_freshness,
    )
except ImportError as exc:  # pragma: no cover - RED phase evidence only.
    AccountProofFreshnessResult = None  # type: ignore[assignment]
    classify_account_proof_freshness = None  # type: ignore[assignment]
    FRESHNESS_IMPORT_ERROR: ImportError | None = exc
else:
    FRESHNESS_IMPORT_ERROR = None


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "tests/helpers/live_preflight_account_proof_projector.py"


def _projected_account_proof(
    *,
    snapshot_ts: object = "2026-05-28T00:00:00Z",
    snapshot_max_age_ms: object = 30000,
    omit_snapshot_ts: bool = False,
    omit_snapshot_max_age_ms: bool = False,
) -> dict[str, Any]:
    account_proof: dict[str, Any] = {
        "account_alias": "public-account-alias",
        "broker_alias": "public-broker-alias",
        "account_snapshot_ref": "public-account-snapshot-ref-phase159fresh001",
        "snapshot_ts": snapshot_ts,
        "snapshot_max_age_ms": snapshot_max_age_ms,
        "base_currency": "USD",
        "equity_ref": "public-equity-ref-phase159fresh001",
        "cash_available_ref": "public-cash-available-ref-phase159fresh001",
        "buying_power_ref": "public-buying-power-ref-phase159fresh001",
        "open_exposure_digest": "public-open-exposure-digest-phase159fresh001",
        "open_orders_digest": "public-open-orders-digest-phase159fresh001",
        "staleness_ref": "public-staleness-ref-phase159fresh001",
    }
    if omit_snapshot_ts:
        account_proof.pop("snapshot_ts")
    if omit_snapshot_max_age_ms:
        account_proof.pop("snapshot_max_age_ms")
    return account_proof


def _utc_now(*, milliseconds_after_snapshot: int) -> datetime:
    return datetime(2026, 5, 28, tzinfo=timezone.utc) + timedelta(
        milliseconds=milliseconds_after_snapshot
    )


def _assert_freshness_imported() -> None:
    assert FRESHNESS_IMPORT_ERROR is None, FRESHNESS_IMPORT_ERROR
    assert AccountProofFreshnessResult is not None
    assert classify_account_proof_freshness is not None


def _classify(account_proof: dict[str, Any], *, now: object) -> Any:
    _assert_freshness_imported()
    assert classify_account_proof_freshness is not None
    return classify_account_proof_freshness(account_proof, now=now)


def _expect_violation(
    account_proof: dict[str, Any],
    *,
    now: object,
    path: str,
    reason: str,
    category: str = "projector_contract",
) -> ProjectorViolation:
    with pytest.raises(ProjectorViolation) as error:
        _classify(account_proof, now=now)
    assert error.value.path == path
    assert error.value.reason == reason
    assert error.value.category == category
    return error.value


def test_account_proof_freshness_uses_deterministic_now() -> None:
    _assert_freshness_imported()
    assert AccountProofFreshnessResult is not None
    assert is_dataclass(AccountProofFreshnessResult)

    account_proof = _projected_account_proof(snapshot_max_age_ms=30000)
    original = dict(account_proof)

    result = _classify(account_proof, now=_utc_now(milliseconds_after_snapshot=10000))

    assert isinstance(result, AccountProofFreshnessResult)
    assert result.status == "current"
    assert result.age_ms == 10000
    assert result.max_age_ms == 30000
    assert result.reason is None
    assert account_proof == original


@pytest.mark.parametrize(
    ("milliseconds_after_snapshot", "max_age_ms", "expected_status", "expected_reason"),
    (
        pytest.param(
            30000,
            30000,
            "current",
            None,
            id="current_exactly_at_snapshot_max_age_ms",
        ),
        pytest.param(
            30001,
            30000,
            "stale",
            "age_exceeds_snapshot_max_age_ms",
            id="stale_one_millisecond_over_snapshot_max_age_ms",
        ),
    ),
)
def test_account_proof_freshness_boundary_matrix(
    milliseconds_after_snapshot: int,
    max_age_ms: int,
    expected_status: str,
    expected_reason: str | None,
) -> None:
    result = _classify(
        _projected_account_proof(snapshot_max_age_ms=max_age_ms),
        now=_utc_now(milliseconds_after_snapshot=milliseconds_after_snapshot),
    )

    assert result.status == expected_status
    assert result.status in {"current", "stale"}
    assert result.age_ms == milliseconds_after_snapshot
    assert result.max_age_ms == max_age_ms
    assert result.reason == expected_reason


@pytest.mark.parametrize(
    ("account_proof", "now", "expected_path", "expected_reason"),
    (
        pytest.param(
            _projected_account_proof(snapshot_ts="2026-05-28T00:00:01Z"),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_ts",
            "future_snapshot_ts",
            id="future_snapshot_ts_rejects",
        ),
        pytest.param(
            _projected_account_proof(omit_snapshot_ts=True),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_ts",
            "missing_timestamp",
            id="missing_timestamp_rejects",
        ),
        pytest.param(
            _projected_account_proof(snapshot_ts="2026-05-28 00:00:00"),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_ts",
            "invalid_timestamp",
            id="malformed_timestamp_rejects",
        ),
        pytest.param(
            _projected_account_proof(snapshot_ts="2026-05-28T00:00:00.001Z"),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_ts",
            "invalid_timestamp",
            id="fractional_second_timestamp_rejects",
        ),
        pytest.param(
            _projected_account_proof(snapshot_ts="2026-05-28T00:00:00+00:00"),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_ts",
            "invalid_timestamp",
            id="offset_timestamp_rejects",
        ),
        pytest.param(
            _projected_account_proof(snapshot_ts="2026-02-30T00:00:00Z"),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_ts",
            "invalid_timestamp",
            id="impossible_timestamp_rejects",
        ),
        pytest.param(
            _projected_account_proof(snapshot_ts=123),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_ts",
            "invalid_timestamp",
            id="non_string_timestamp_rejects",
        ),
        pytest.param(
            _projected_account_proof(omit_snapshot_max_age_ms=True),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_max_age_ms",
            "invalid_snapshot_max_age_ms",
            id="missing_snapshot_max_age_ms_rejects",
        ),
        pytest.param(
            _projected_account_proof(snapshot_max_age_ms=0),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_max_age_ms",
            "invalid_snapshot_max_age_ms",
            id="zero_snapshot_max_age_ms_rejects",
        ),
        pytest.param(
            _projected_account_proof(snapshot_max_age_ms=-1),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_max_age_ms",
            "invalid_snapshot_max_age_ms",
            id="negative_snapshot_max_age_ms_rejects",
        ),
        pytest.param(
            _projected_account_proof(snapshot_max_age_ms=True),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_max_age_ms",
            "invalid_snapshot_max_age_ms",
            id="boolean_snapshot_max_age_ms_rejects",
        ),
        pytest.param(
            _projected_account_proof(snapshot_max_age_ms="30000"),
            _utc_now(milliseconds_after_snapshot=0),
            "account_proof.snapshot_max_age_ms",
            "invalid_snapshot_max_age_ms",
            id="non_integer_snapshot_max_age_ms_rejects",
        ),
    ),
)
def test_account_proof_freshness_invalid_input_matrix(
    account_proof: dict[str, Any],
    now: datetime,
    expected_path: str,
    expected_reason: str,
) -> None:
    _expect_violation(
        account_proof,
        now=now,
        path=expected_path,
        reason=expected_reason,
    )


@pytest.mark.parametrize(
    "now",
    (
        pytest.param(None, id="none_now"),
        pytest.param("2026-05-28T00:00:00Z", id="non_datetime_now"),
        pytest.param(datetime(2026, 5, 28), id="naive_now"),
        pytest.param(
            datetime(2026, 5, 28, tzinfo=timezone(timedelta(hours=9))),
            id="non_utc_now",
        ),
    ),
)
def test_account_proof_freshness_invalid_now_raises_projector_violation(
    now: object,
) -> None:
    _expect_violation(
        _projected_account_proof(),
        now=now,
        path="now",
        reason="invalid_timestamp",
    )


def test_account_proof_freshness_accepts_utc_equivalent_now() -> None:
    zero_offset_now = datetime(
        2026,
        5,
        28,
        0,
        0,
        10,
        tzinfo=timezone(timedelta(0), "synthetic-zero"),
    )

    result = _classify(
        _projected_account_proof(snapshot_max_age_ms=30000),
        now=zero_offset_now,
    )

    assert result.status == "current"
    assert result.age_ms == 10000
    assert result.max_age_ms == 30000


@pytest.mark.parametrize(
    ("account_proof", "expected_path", "expected_reason"),
    (
        pytest.param(
            {
                **_projected_account_proof(),
                "equity_ref": "00000.00",
            },
            "account_proof.equity_ref",
            "invalid_public_opaque_ref",
            id="raw_scalar_ref_shape_rejects_before_freshness",
        ),
        pytest.param(
            {
                **_projected_account_proof(),
                "equity": "00000.00",
            },
            "account_proof.<unsafe_key>",
            "unsafe_public_material",
            id="unsupported_raw_scalar_field_rejects_before_freshness",
        ),
        pytest.param(
            {
                **_projected_account_proof(),
                "raw_account_id": "synthetic-redacted-id",
            },
            "account_proof.<unsafe_key>",
            "unsafe_public_material",
            id="unsupported_raw_account_id_rejects_before_freshness",
        ),
    ),
)
def test_account_proof_freshness_requires_projected_account_proof_shape(
    account_proof: dict[str, Any],
    expected_path: str,
    expected_reason: str,
) -> None:
    _expect_violation(
        account_proof,
        now=_utc_now(milliseconds_after_snapshot=0),
        path=expected_path,
        reason=expected_reason,
    )


def test_freshness_helper_source_keeps_projection_and_public_mapping_separate() -> None:
    helper_source = HELPER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(helper_source)

    assert "datetime.now" not in helper_source
    assert "datetime.utcnow" not in helper_source
    assert "time.time" not in helper_source
    assert "time.monotonic" not in helper_source
    assert r"\d{4}" not in helper_source
    assert r"[0-9]{4}" in helper_source

    calls_by_function: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        call_names: set[str] = set()
        for nested in ast.walk(node):
            if isinstance(nested, ast.Call):
                func = nested.func
                if isinstance(func, ast.Name):
                    call_names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    call_names.add(func.attr)
        calls_by_function[node.name] = call_names

    assert "classify_account_proof_freshness" not in calls_by_function.get(
        "project_account_proof",
        set(),
    )
    assert "project_account_proof" not in calls_by_function.get(
        "classify_account_proof_freshness",
        set(),
    )
