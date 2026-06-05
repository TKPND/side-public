"""Phase 158 account-proof projector contract tests."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.live_preflight_account_proof_projector import (
    ALLOWED_ACCOUNT_PROOF_FIELDS,
    EVIDENCE_LABEL,
    OPAQUE_HANDLE_MAX_LENGTH,
    OPAQUE_HANDLE_MIN_LENGTH,
    PUBLIC_DIGEST_REF_PATTERN,
    PUBLIC_OPAQUE_REF_PATTERN,
    PUBLIC_SOURCE_REF_PATTERN,
    SOURCE_REF_MAX_LENGTH,
    SOURCE_REF_MIN_LENGTH,
    VALID_PROVENANCE_KINDS,
    AccountProofProjectorInput,
    AccountProofProjectorResult,
    ProjectorViolation,
    project_account_proof,
)


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "tests/helpers/live_preflight_account_proof_projector.py"
CONTRACT_TEST_PATH = ROOT / "tests/test_live_preflight_account_proof_projector_contract.py"
SCHEMA_PATH = ROOT / "docs/contracts/live_preflight_result_v1.schema.json"
BUILDER_PATH = ROOT / "tests/helpers/live_preflight_result_builder.py"
PHASE_158_ACTIVE_DIR = (
    ROOT / ".planning/phases/158-account-proof-projector-contract-and-threat-model"
)
PHASE_158_ARCHIVED_DIR = (
    ROOT
    / ".planning/milestones/v8.9-phases/"
    "158-account-proof-projector-contract-and-threat-model"
)

FORBIDDEN_HELPER_IMPORT_ROOTS = {
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
    "pathlib",
    "shutil",
    "dotenv",
    "keyring",
    "google.cloud.secretmanager",
    "hashlib",
}
FORBIDDEN_HELPER_SOURCE_TOKENS = {
    "NoOrderPreflightInput",
    "_ref_digest",
    "hashlib",
    "sha256",
    "hexdigest",
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
FORBIDDEN_TEST_IMPORT_ROOTS = {
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
}
FORBIDDEN_TEST_CALL_NAMES = {
    "_ref_" + "digest",
    "build_no_order_artifact",
    "build_and_persist_no_order_artifact",
    "map_v8_5_live_fixture_input",
    "place_order",
    "submit_order",
    "cancel_order",
    "fetch_account",
}


def resolve_phase_158_artifact(filename: str) -> Path:
    active_path = PHASE_158_ACTIVE_DIR / filename
    if active_path.exists():
        return active_path
    archived_path = PHASE_158_ARCHIVED_DIR / filename
    if archived_path.exists():
        return archived_path
    return active_path


def read_phase_158_security() -> str:
    path = resolve_phase_158_artifact("158-SECURITY.md")
    assert path.exists(), f"missing Phase 158 security artifact: {path}"
    return path.read_text(encoding="utf-8")
FORBIDDEN_DYNAMIC_IMPORT_ROOTS = {
    "importlib",
}
FORBIDDEN_DYNAMIC_CALL_NAMES = {
    "__import__",
    "import_module",
    "eval",
    "exec",
}
LEGACY_DIGEST_TOKEN_ALLOWLIST = {
    "_ref_" + "digest",
    "hash" + "lib",
    "sha" + "256",
    "hex" + "digest",
}
UNSAFE_SENTINELS = (
    "synthetic-unsafe-secret-alpha",
    "synthetic-private-endpoint-alpha",
    "synthetic-raw-account-scalar-alpha",
)
REQUIRED_SECURITY_CATEGORIES = {
    "information disclosure",
    "spoofing",
    "tampering",
    "boundary creep",
}
BOUNDARY_CREEP_OUT_OF_SCOPE = {
    "real account fetcher",
    "broker execution",
    "credential/network path",
    "side live wiring",
    "runtime public emission",
    "public schema expansion",
    "Scan/WFD sizing",
    "warning cleanup",
    "frozen proof-field drift",
}


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


def _schema() -> dict[str, Any]:
    payload = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _schema_account_proof_fields() -> frozenset[str]:
    account_proof = _schema()["$defs"]["account_proof"]
    return frozenset(account_proof["properties"])


def _valid_account_proof() -> dict[str, Any]:
    return {
        "account_alias": "public-account-alias",
        "broker_alias": "public-broker-alias",
        "account_snapshot_ref": "public-account-snapshot-ref-phase158fixture001",
        "snapshot_ts": "2026-05-28T00:00:00Z",
        "snapshot_max_age_ms": 30000,
        "base_currency": "USD",
        "equity_ref": "public-equity-ref-phase158fixture001",
        "cash_available_ref": "public-cash-available-ref-phase158fixture001",
        "buying_power_ref": "public-buying-power-ref-phase158fixture001",
        "open_exposure_digest": "public-open-exposure-digest-phase158fixture001",
        "open_orders_digest": "public-open-orders-digest-phase158fixture001",
        "staleness_ref": "public-staleness-ref-phase158fixture001",
    }


def _safe_input(
    *,
    provenance_kind: str = "synthetic_public",
    source_ref: str = "phase158-synthetic-public-input",
    runtime_evidence_claim: object = False,
    broker_evidence_claim: object = False,
    account_fetch_evidence_claim: object = False,
    account_proof: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "provenance_kind": provenance_kind,
        "source_ref": source_ref,
        "runtime_evidence_claim": runtime_evidence_claim,
        "broker_evidence_claim": broker_evidence_claim,
        "account_fetch_evidence_claim": account_fetch_evidence_claim,
        "account_proof": account_proof or _valid_account_proof(),
    }
    if extra:
        payload.update(extra)
    return payload


def _assert_projector_violation(
    error: pytest.ExceptionInfo[ProjectorViolation],
    *,
    path: str,
    reason: str,
    category: str = "projector_contract",
) -> None:
    assert error.value.path == path
    assert error.value.reason == reason
    assert error.value.category == category


def _expect_violation(
    payload: object,
    *,
    path: str,
    reason: str,
) -> ProjectorViolation:
    with pytest.raises(ProjectorViolation) as error:
        project_account_proof(payload)
    _assert_projector_violation(error, path=path, reason=reason)
    return error.value


def _without_key(payload: dict[str, Any], key: str) -> dict[str, Any]:
    copy = dict(payload)
    copy.pop(key)
    return copy


def _with_account_proof_field(field: str, value: object) -> dict[str, Any]:
    account_proof = _valid_account_proof()
    account_proof[field] = value
    return _safe_input(account_proof=account_proof)


def test_projector_helper_has_no_runtime_or_broker_surface() -> None:
    assert FORBIDDEN_HELPER_IMPORT_ROOTS == {
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
        "pathlib",
        "shutil",
        "dotenv",
        "keyring",
        "google.cloud.secretmanager",
        "hashlib",
    }
    assert FORBIDDEN_HELPER_SOURCE_TOKENS == {
        "NoOrderPreflightInput",
        "_ref_digest",
        "hashlib",
        "sha256",
        "hexdigest",
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

    helper_source = HELPER_PATH.read_text(encoding="utf-8")
    helper_tree = ast.parse(helper_source)
    assert _root_import_violations(
        _imports_from(helper_tree),
        FORBIDDEN_HELPER_IMPORT_ROOTS | FORBIDDEN_DYNAMIC_IMPORT_ROOTS,
    ) == []
    assert not (FORBIDDEN_DYNAMIC_CALL_NAMES & _call_names(helper_tree))
    for token in FORBIDDEN_HELPER_SOURCE_TOKENS:
        assert token not in helper_source
    assert not _has_main_guard(helper_tree)

    test_source = CONTRACT_TEST_PATH.read_text(encoding="utf-8")
    test_tree = ast.parse(test_source)
    assert _root_import_violations(
        _imports_from(test_tree),
        FORBIDDEN_TEST_IMPORT_ROOTS | FORBIDDEN_DYNAMIC_IMPORT_ROOTS,
    ) == []
    assert not (FORBIDDEN_TEST_CALL_NAMES & _call_names(test_tree))
    assert not (FORBIDDEN_DYNAMIC_CALL_NAMES & _call_names(test_tree))
    assert not _has_main_guard(test_tree)
    assert LEGACY_DIGEST_TOKEN_ALLOWLIST == {
        "_ref_digest",
        "hashlib",
        "sha256",
        "hexdigest",
    }


def test_projector_accepts_only_public_safe_provenance_kinds() -> None:
    examples = {
        "synthetic_public": "phase158-synthetic-public-input",
        "fixture_sanitized_public": "fixture_sanitized_public.v8_9",
        "already_sanitized_public": "already-public-ref-001",
    }
    assert VALID_PROVENANCE_KINDS == frozenset(examples)

    for provenance_kind, source_ref in examples.items():
        result = project_account_proof(
            _safe_input(provenance_kind=provenance_kind, source_ref=source_ref)
        )
        assert isinstance(result, AccountProofProjectorResult)
        assert result.input_provenance_kind == provenance_kind
        assert result.source_ref == source_ref
        assert result.account_proof == _valid_account_proof()

    invalid_cases = (
        ("runtime", "invalid_provenance_kind"),
        ("broker_live", "invalid_provenance_kind"),
        ("account_fetch", "invalid_provenance_kind"),
        ("fixture", "invalid_provenance_kind"),
        ("", "invalid_provenance_kind"),
    )
    for provenance_kind, reason in invalid_cases:
        _expect_violation(
            _safe_input(provenance_kind=provenance_kind),
            path="provenance_kind",
            reason=reason,
        )

    _expect_violation(
        _without_key(_safe_input(), "provenance_kind"),
        path="provenance_kind",
        reason="missing_provenance_kind",
    )


def test_projector_output_labels_non_runtime_non_broker_non_account_fetch_evidence() -> None:
    result = project_account_proof(_safe_input())

    assert result.evidence_label == EVIDENCE_LABEL
    assert result.runtime_evidence_claim is False
    assert result.broker_evidence_claim is False
    assert result.account_fetch_evidence_claim is False
    assert "non-runtime" in result.evidence_label
    assert "non-broker" in result.evidence_label
    assert "non-account-fetch" in result.evidence_label


def test_projector_rejects_invalid_input_before_projection() -> None:
    bad_source_refs = (
        (None, "missing_source_ref"),
        ("", "missing_source_ref"),
        ("   ", "invalid_source_ref"),
        ("https://broker.example/account", "invalid_source_ref"),
        ("secret token source", "invalid_source_ref"),
        ("a" * (SOURCE_REF_MAX_LENGTH + 1), "invalid_source_ref"),
    )
    for source_ref, reason in bad_source_refs:
        _expect_violation(
            _safe_input(source_ref=source_ref),  # type: ignore[arg-type]
            path="source_ref",
            reason=reason,
        )

    for claim in (
        "runtime_evidence_claim",
        "broker_evidence_claim",
        "account_fetch_evidence_claim",
    ):
        _expect_violation(
            _without_key(_safe_input(), claim),
            path=claim,
            reason="missing_evidence_claim",
        )
        for value in (True, None, 0, "false"):
            _expect_violation(
                _safe_input(**{claim: value}),
                path=claim,
                reason="invalid_evidence_claim",
            )

    private_material_cases = (
        (
            _safe_input(extra={"credential_hint": "public-redacted-ref"}),
            "<unsafe_key>",
        ),
        (
            _safe_input(extra={"metadata": {"endpoint_ref": "public-redacted-ref"}}),
            "metadata.<unsafe_key>",
        ),
        (
            _safe_input(extra={"metadata": ["ok", {"token_hint": "public-redacted"}]}),
            "metadata.[1].<unsafe_key>",
        ),
        (
            _safe_input(extra={"metadata": {"note": UNSAFE_SENTINELS[0]}}),
            "metadata.note",
        ),
        (
            _safe_input(extra={"metadata": {"note": "synthetic-raw-idempotency-alpha"}}),
            "metadata.note",
        ),
        (
            _safe_input(extra={"metadata": {"note": "synthetic-raw-account-id-alpha"}}),
            "metadata.note",
        ),
        (
            _safe_input(extra={"metadata": {"note": "USD 123.45"}}),
            "metadata.note",
        ),
        (
            _safe_input(extra={"metadata": {"note": "12,345.67 USD"}}),
            "metadata.note",
        ),
        (
            _safe_input(extra={"metadata": {"notional_hint": "12345.67"}}),
            "metadata.notional_hint",
        ),
        (
            _safe_input(
                account_proof={**_valid_account_proof(), "equity": "public-redacted"}
            ),
            "account_proof.<unsafe_key>",
        ),
    )
    for payload, path in private_material_cases:
        _expect_violation(payload, path=path, reason="unsafe_public_material")


def test_projector_rejects_raw_scalar_to_public_ref_conversion() -> None:
    legacy_source = BUILDER_PATH.read_text(encoding="utf-8")
    for token in LEGACY_DIGEST_TOKEN_ALLOWLIST:
        assert token in legacy_source

    helper_source = HELPER_PATH.read_text(encoding="utf-8")
    for token in LEGACY_DIGEST_TOKEN_ALLOWLIST:
        assert token not in helper_source

    raw_scalar_cases = (
        _with_account_proof_field("equity_ref", "250000"),
        _with_account_proof_field("cash_available_ref", "99999.50"),
        _with_account_proof_field("buying_power_ref", "equity-ref-sha256-deadbeef"),
        _with_account_proof_field("open_exposure_digest", "open-exposure-sha256-deadbeef"),
        _safe_input(extra={"metadata": {"cash_available": "public-redacted"}}),
    )
    for payload in raw_scalar_cases:
        with pytest.raises(ProjectorViolation) as error:
            project_account_proof(payload)
        assert error.value.category == "projector_contract"
        for sentinel in UNSAFE_SENTINELS:
            assert sentinel not in str(error.value)
            assert sentinel not in repr(error.value)


def test_projector_enforces_internal_allowed_account_proof_contract() -> None:
    assert ALLOWED_ACCOUNT_PROOF_FIELDS == _schema_account_proof_fields()
    assert SOURCE_REF_MIN_LENGTH == 1
    assert SOURCE_REF_MAX_LENGTH == 128
    assert OPAQUE_HANDLE_MIN_LENGTH == 8
    assert OPAQUE_HANDLE_MAX_LENGTH == 160
    assert (
        PUBLIC_SOURCE_REF_PATTERN
        == r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    )
    assert (
        PUBLIC_OPAQUE_REF_PATTERN
        == r"^public-[a-z][a-z0-9-]{2,31}-ref-[A-Za-z0-9][A-Za-z0-9._-]{7,127}$"
    )
    assert (
        PUBLIC_DIGEST_REF_PATTERN
        == r"^public-[a-z][a-z0-9-]{2,31}-digest-[A-Za-z0-9][A-Za-z0-9._-]{7,127}$"
    )

    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    for internal_name in (
        "SOURCE_REF_MAX_LENGTH",
        "PUBLIC_SOURCE_REF_PATTERN",
        "PUBLIC_OPAQUE_REF_PATTERN",
        "PUBLIC_DIGEST_REF_PATTERN",
        "AccountProofProjectorInput",
        "AccountProofProjectorResult",
    ):
        assert internal_name not in schema_text

    payload = _safe_input(
        account_proof={**_valid_account_proof(), "unexpected_public_field": "value"}
    )
    _expect_violation(
        payload,
        path="account_proof.unexpected_public_field",
        reason="unsupported_account_proof_field",
    )


def test_projector_validates_path_specific_account_proof_fields() -> None:
    invalid_cases = (
        ("account_alias", "", "invalid_public_alias"),
        ("account_alias", "123456789", "invalid_public_alias"),
        ("broker_alias", "broker secret alias", "invalid_public_alias"),
        ("broker_alias", "987654321", "invalid_public_alias"),
        ("account_snapshot_ref", "acct-snapshot-sha256-legacy", "invalid_public_opaque_ref"),
        ("snapshot_ts", "2026-05-28 00:00:00", "invalid_timestamp"),
        ("snapshot_max_age_ms", 0, "invalid_snapshot_max_age_ms"),
        ("base_currency", "usd", "invalid_base_currency"),
        ("equity_ref", "250000", "invalid_public_opaque_ref"),
        ("cash_available_ref", "http://example.test/ref", "invalid_public_opaque_ref"),
        ("buying_power_ref", "public-equity-ref-has space", "invalid_public_opaque_ref"),
        ("open_exposure_digest", "open-exposure-sha256-deadbeef", "invalid_public_digest_ref"),
        ("open_orders_digest", "abcdef0123456789abcdef0123456789", "invalid_public_digest_ref"),
        ("staleness_ref", "public-staleness-ref-has space", "invalid_public_opaque_ref"),
    )
    for field, value, reason in invalid_cases:
        _expect_violation(
            _with_account_proof_field(field, value),
            path=f"account_proof.{field}",
            reason=reason,
        )

    dataclass_input = AccountProofProjectorInput(
        provenance_kind="synthetic_public",
        source_ref="phase158-synthetic-public-input",
        runtime_evidence_claim=False,
        broker_evidence_claim=False,
        account_fetch_evidence_claim=False,
        account_proof=_valid_account_proof(),
    )
    replaced = replace(dataclass_input, source_ref="already-public-ref-001")
    result = project_account_proof(replaced)
    assert result.source_ref == "already-public-ref-001"


def test_projector_contract_remains_internal_not_public_schema_ref() -> None:
    schema = _schema()
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")

    for forbidden in (
        "live_preflight_account_proof_projector.py",
        "AccountProofProjectorInput",
        "AccountProofProjectorResult",
        "ProjectorViolation",
    ):
        assert forbidden not in schema_text

    refs = [
        node["$ref"]
        for node in _walk_json(schema)
        if isinstance(node, dict) and isinstance(node.get("$ref"), str)
    ]
    assert "risk/contracts/v2/risk_contract_v2.schema.json" not in refs
    assert "risk_contract_v2.schema.json" not in refs
    assert {"candidate", "evidence", "decision", "application", "trace"}.isdisjoint(
        schema["properties"]
    )
    assert {"candidate", "evidence", "decision", "application", "trace"}.isdisjoint(
        schema["$defs"]
    )


def _walk_json(value: object) -> list[object]:
    if isinstance(value, dict):
        nested: list[object] = [value]
        for child in value.values():
            nested.extend(_walk_json(child))
        return nested
    if isinstance(value, list):
        nested = [value]
        for child in value:
            nested.extend(_walk_json(child))
        return nested
    return [value]


def test_projector_violation_surfaces_are_path_reason_only() -> None:
    payload = _safe_input(extra={"metadata": {"note": UNSAFE_SENTINELS[0]}})
    violation = _expect_violation(
        payload,
        path="metadata.note",
        reason="unsafe_public_material",
    )

    assert set(violation.__dict__) == {"path", "reason", "category"}
    surface = f"{violation!s}\n{violation!r}"
    assert violation.path in surface
    assert violation.reason in surface
    assert violation.category in surface
    security_text = read_phase_158_security()
    for sentinel in UNSAFE_SENTINELS:
        assert sentinel not in surface
        assert sentinel not in security_text

    unsafe_key = f"token_{UNSAFE_SENTINELS[0]}"
    key_violation = _expect_violation(
        _safe_input(extra={unsafe_key: "public-redacted-ref"}),
        path="<unsafe_key>",
        reason="unsafe_public_material",
    )
    key_surface = f"{key_violation.path}\n{key_violation!s}\n{key_violation!r}"
    assert unsafe_key not in key_surface
    assert UNSAFE_SENTINELS[0] not in key_surface


def test_phase_158_security_register_records_required_threats_and_evidence() -> None:
    text = read_phase_158_security()
    rows = _security_threat_rows(text)

    assert {row["category"] for row in rows} == REQUIRED_SECURITY_CATEGORIES
    assert len(rows) == len(REQUIRED_SECURITY_CATEGORIES)

    for row in rows:
        assert row["status"]
        assert row["mitigation"]
        assert row["verification command"].startswith("rtk uv run pytest -q ")
        assert row["test name"].startswith("test_")
        assert row["test name"] in CONTRACT_TEST_PATH.read_text(encoding="utf-8")

    boundary_row = next(row for row in rows if row["category"] == "boundary creep")
    for item in BOUNDARY_CREEP_OUT_OF_SCOPE:
        assert item in boundary_row["mitigation"]

    for sentinel in UNSAFE_SENTINELS:
        assert sentinel not in text


def _security_threat_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "Threat ID" in stripped or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 7 or not cells[0].startswith("T-158-"):
            continue
        rows.append(
            {
                "threat id": cells[0],
                "category": cells[1],
                "status": cells[2],
                "mitigation": cells[3],
                "verification command": cells[4],
                "test name": cells[5],
                "notes": cells[6],
            }
        )
    return rows
