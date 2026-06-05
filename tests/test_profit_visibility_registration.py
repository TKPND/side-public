"""Registration protocol guards for the v9.0 profit visibility checkpoint."""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION_CONTRACT_DOC = (
    ROOT / "docs/contracts/profit_visibility_registration_protocol_v1.md"
)

REGISTRATION_REQUIRED_FIELDS = (
    "split/OOS/WFD or holdout protocol",
    "leakage checks",
    "minimum eligible sample",
    "multiple-testing-control method",
    "equivalent-control choice",
    "error_rate_target = FWER",
    "alpha = 0.05",
    "candidate-family boundary",
    "finest-granularity hypothesis count",
    "pre_anchor_result_bearing_runs",
)
PROTOCOL_CRITICAL_FIELDS = (
    "thresholds",
    "features",
    "filters",
    "acceptance criteria",
    "multiple-testing-control method",
    "equivalent-control choice and criteria",
    "named error-rate target",
    "alpha level",
    "candidate-family boundary",
    "finest-granularity hypothesis count",
)
FINEST_GRANULARITY_COUNT_COMPONENTS = (
    "signal family",
    "universe",
    "timeframe",
    "feature/filter variant",
    "parameter grid/candidate",
    "split/protocol variant",
    "other evaluated minimal candidate unit",
)
SUPPORTED_RESULT_BEARING_RUN_TYPES = (
    "repo-supported scripts",
    "tests",
    "report generators",
    "documented workflows",
    "candidate performance",
    "survivor status",
    "multiple-testing inputs",
    "paper-forward readiness",
)
DISQUALIFIED_ANCHORS = (
    "git commit dates",
    "git tag dates",
    "force-pushable refs",
    "local file mtimes",
    "handwritten timestamps",
    "unverified CI log timestamps",
)
EQUIVALENT_CONTROL_DOSSIER_FIELDS = (
    "method name",
    "controls FWER equivalently or better",
    "application scope",
    "required inputs",
    "approval authority",
    "validity criteria",
    "fallback/null-ship conditions",
)
REJECTED_PLACEHOLDERS = ("blank", "unknown", "TBD", "placeholder")
REJECTED_REGISTRATION_STRINGS = {"", "blank", "unknown", "tbd", "placeholder"}
REQUIRED_REGISTRATION_KEYS = (
    "split/OOS/WFD or holdout protocol",
    "leakage checks",
    "minimum eligible sample",
    "multiple_testing_control_method",
    "equivalent_control_choice",
    "error_rate_target",
    "alpha",
    "candidate-family boundary",
    "finest-granularity hypothesis count",
    "pre_anchor_result_bearing_runs",
)


def read_registration_contract_doc() -> str:
    return REGISTRATION_CONTRACT_DOC.read_text(encoding="utf-8")


def assert_contains_all(text: str, fragments: tuple[str, ...]) -> None:
    missing = [fragment for fragment in fragments if fragment not in text]
    assert not missing, f"missing registration contract fragments: {missing}"


def sha256_bytes(bytes_value: bytes) -> str:
    return hashlib.sha256(bytes_value).hexdigest()


def sealed_artifact_fixture(
    payload_bytes: bytes,
    proof_path: str = "registrations/candidate-family.ots",
    anchor_kind: str = "OpenTimestamps",
) -> dict[str, object]:
    return {
        "registered_bytes": payload_bytes,
        "current_bytes": payload_bytes,
        "sha256": sha256_bytes(payload_bytes),
        "proof_path": proof_path,
        "anchor_kind": anchor_kind,
        "anchor_verified": True,
    }


def mutate_one_byte(bytes_value: bytes) -> bytes:
    assert bytes_value, "test fixture must have bytes to mutate"
    first = bytes_value[0] ^ 0x01
    return bytes([first]) + bytes_value[1:]


def protocol_mutations(
    original: dict[str, object], candidate: dict[str, object]
) -> tuple[str, ...]:
    return tuple(
        field
        for field in PROTOCOL_CRITICAL_FIELDS
        if original.get(field) != candidate.get(field)
    )


def complete_registration_fixture(
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    registration: dict[str, object] = {
        "split/OOS/WFD or holdout protocol": "purged OOS holdout with embargo",
        "leakage checks": "lookahead, overlap, and embargo checks pass",
        "minimum eligible sample": "at least 250 eligible trades",
        "multiple_testing_control_method": "FWER/Holm",
        "equivalent_control_choice": "none",
        "error_rate_target": "FWER",
        "alpha": 0.05,
        "candidate-family boundary": "phase163 family/universe/timeframe/grid",
        "finest-granularity hypothesis count": 42,
        "pre_anchor_result_bearing_runs": (),
    }
    if overrides:
        registration.update(overrides)
    return registration


def _is_rejected_registration_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in REJECTED_REGISTRATION_STRINGS
    return False


def _missing_required_registration_fields(
    registration: dict[str, object],
) -> tuple[str, ...]:
    missing = []
    for key in REQUIRED_REGISTRATION_KEYS:
        if key not in registration or _is_rejected_registration_value(
            registration[key]
        ):
            missing.append(key)
    runs = registration.get("pre_anchor_result_bearing_runs")
    if "pre_anchor_result_bearing_runs" not in missing and not isinstance(
        runs, (list, tuple)
    ):
        missing.append("pre_anchor_result_bearing_runs")
    return tuple(missing)


def registration_stub_status(
    registration: dict[str, object],
    discovered_runs: tuple[dict[str, object], ...],
    sealed_metadata: dict[str, object],
) -> str:
    if registration.get("protocol_critical_mutations"):
        return "profit_visible_false"
    if _missing_required_registration_fields(registration):
        return "invalid_disqualified"

    registered_bytes = sealed_metadata.get("registered_bytes")
    current_bytes = sealed_metadata.get("current_bytes", registered_bytes)
    expected_sha = sealed_metadata.get("sha256")
    proof_path = str(sealed_metadata.get("proof_path") or "")
    anchor_kind = str(sealed_metadata.get("anchor_kind") or "")
    disqualified_anchor = anchor_kind in DISQUALIFIED_ANCHORS
    if (
        not isinstance(registered_bytes, bytes)
        or not isinstance(current_bytes, bytes)
        or sha256_bytes(current_bytes) != expected_sha
        or current_bytes != registered_bytes
        or not proof_path.endswith(".ots")
        or not sealed_metadata.get("anchor_verified")
        or disqualified_anchor
    ):
        return "invalid_disqualified"

    disclosed_runs = tuple(registration.get("pre_anchor_result_bearing_runs", ()))
    if disclosed_runs:
        return "profit_visible_false"
    if any(run.get("pre_anchor") for run in discovered_runs):
        return "invalid_disqualified"

    if registration.get("denominator_shrinkage") or registration.get(
        "survivor_only_correction"
    ):
        return "profit_visible_false"
    if registration.get("multiple_testing_control_method") != "FWER/Holm":
        return "profit_visible_false"
    if registration.get("error_rate_target") != "FWER":
        return "profit_visible_false"
    if registration.get("alpha") != 0.05:
        return "profit_visible_false"
    if registration.get("equivalent_control_choice") != "none" and not registration.get(
        "equivalent_control_dossier"
    ):
        return "profit_visible_false"

    return "eligible_for_evaluation"


def test_registration_protocol_pins_required_fields_and_denominator() -> None:
    text = read_registration_contract_doc()

    assert "# Profit Visibility Registration Protocol v1" in text
    assert "## Required Pre-Registration Fields" in text
    assert "## Candidate Count And Multiple-Testing Control" in text
    assert_contains_all(text, REGISTRATION_REQUIRED_FIELDS)
    assert_contains_all(text, FINEST_GRANULARITY_COUNT_COMPONENTS)
    assert_contains_all(text, REJECTED_PLACEHOLDERS)
    assert "recounting only reported candidates or survivors is forbidden" in text


def test_incomplete_required_registration_artifacts_fail_closed() -> None:
    sealed_metadata = sealed_artifact_fixture(b'{"family":"complete"}')
    complete = complete_registration_fixture()

    assert registration_stub_status(complete, (), sealed_metadata) == (
        "eligible_for_evaluation"
    )
    for missing_key in REQUIRED_REGISTRATION_KEYS:
        incomplete = dict(complete)
        incomplete.pop(missing_key)
        assert (
            registration_stub_status(incomplete, (), sealed_metadata)
            == "invalid_disqualified"
        ), missing_key

    for placeholder in ("", "unknown", "TBD", "placeholder"):
        registration = complete_registration_fixture({"leakage checks": placeholder})
        assert (
            registration_stub_status(registration, (), sealed_metadata)
            == "invalid_disqualified"
        ), placeholder


def test_supported_evaluation_runs_are_broadly_defined() -> None:
    text = read_registration_contract_doc()

    assert "## Supported Evaluation Runs" in text
    assert_contains_all(text, SUPPORTED_RESULT_BEARING_RUN_TYPES)
    assert (
        "computes candidate performance, survivor status, multiple-testing inputs, "
        "or paper-forward readiness"
        in text
    )


def test_protocol_critical_mutation_after_results_fails_closed() -> None:
    text = read_registration_contract_doc()
    original = {field: f"sealed-{field}" for field in PROTOCOL_CRITICAL_FIELDS}
    candidate = dict(original)
    candidate["thresholds"] = "relaxed-after-results"
    mutations = protocol_mutations(original, candidate)
    registration = complete_registration_fixture(
        {"protocol_critical_mutations": mutations}
    )

    assert "## Protocol-Critical Immutability" in text
    assert_contains_all(text, PROTOCOL_CRITICAL_FIELDS)
    assert mutations == ("thresholds",)
    assert (
        registration_stub_status(
            registration,
            (),
            sealed_artifact_fixture(b'{"family":"sealed"}'),
        )
        == "profit_visible_false"
    )


def test_strict_byte_seal_and_anchor_rules_are_pinned() -> None:
    text = read_registration_contract_doc()
    payload_bytes = b'{"candidate_family":"phase163","alpha":0.05}'
    sealed_metadata = sealed_artifact_fixture(payload_bytes)
    registration = complete_registration_fixture()
    mutated_metadata = dict(sealed_metadata)
    mutated_metadata["current_bytes"] = mutate_one_byte(payload_bytes)

    assert "## Strict Byte Seal And OpenTimestamps Anchor" in text
    assert_contains_all(text, ("OpenTimestamps", ".ots", "sha256", "strict byte seal"))
    assert_contains_all(text, DISQUALIFIED_ANCHORS)
    assert sealed_metadata["sha256"] == hashlib.sha256(payload_bytes).hexdigest()
    assert registration_stub_status(registration, (), sealed_metadata) == (
        "eligible_for_evaluation"
    )
    assert registration_stub_status(registration, (), mutated_metadata) in (
        "profit_visible_false",
        "invalid_disqualified",
    )
    assert registration_stub_status(
        registration,
        (),
        sealed_artifact_fixture(payload_bytes, proof_path="registration.json"),
    ) == "invalid_disqualified"
    assert registration_stub_status(
        registration,
        (),
        sealed_artifact_fixture(payload_bytes, anchor_kind="git commit dates"),
    ) == "invalid_disqualified"


def test_pre_anchor_result_bearing_runs_downgrade_or_invalidate() -> None:
    text = read_registration_contract_doc()
    base_registration = complete_registration_fixture()
    disclosed = {
        **base_registration,
        "pre_anchor_result_bearing_runs": ({"id": "run-1", "disclosed": True},),
    }
    malformed_disclosed = {
        **base_registration,
        "pre_anchor_result_bearing_runs": ("run-1",),
    }
    undisclosed = {**base_registration, "pre_anchor_result_bearing_runs": ()}
    discovered = ({"id": "run-2", "pre_anchor": True},)

    assert "## Pre-Anchor Result-Bearing Run Disclosure" in text
    assert "pre_anchor_result_bearing_runs" in text
    assert "profit_visible = false" in text
    assert "invalid/disqualified" in text
    assert (
        registration_stub_status(
            disclosed,
            (),
            sealed_artifact_fixture(b'{"family":"disclosed"}'),
        )
        == "profit_visible_false"
    )
    assert (
        registration_stub_status(
            malformed_disclosed,
            (),
            sealed_artifact_fixture(b'{"family":"malformed-disclosed"}'),
        )
        == "profit_visible_false"
    )
    assert (
        registration_stub_status(
            undisclosed,
            discovered,
            sealed_artifact_fixture(b'{"family":"undisclosed"}'),
        )
        == "invalid_disqualified"
    )


def test_holm_fwer_equivalent_control_rules_fail_closed() -> None:
    text = read_registration_contract_doc()
    base_registration = complete_registration_fixture()

    assert "## Equivalent-Control Dossier" in text
    assert_contains_all(
        text,
        (
            "FWER",
            "Holm",
            "alpha = 0.05",
            "finest-granularity hypothesis count",
            "equivalent-control dossier",
        ),
    )
    assert_contains_all(text, EQUIVALENT_CONTROL_DOSSIER_FIELDS)
    for override in (
        {"denominator_shrinkage": True},
        {"survivor_only_correction": True},
        {"multiple_testing_control_method": "BH/FDR"},
        {"error_rate_target": "FDR"},
        {"alpha": 0.10},
        {"equivalent_control_choice": "permutation", "equivalent_control_dossier": None},
    ):
        registration = {**base_registration, **override}
        assert (
            registration_stub_status(
                registration,
                (),
                sealed_artifact_fixture(b'{"family":"mtc"}'),
            )
            == "profit_visible_false"
        )
