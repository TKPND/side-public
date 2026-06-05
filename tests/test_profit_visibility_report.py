"""Report contract guards for the v9.0 profit visibility checkpoint."""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_CONTRACT_DOC = ROOT / "docs/contracts/profit_visibility_report_v1.md"
V9_REPORT_PAYLOAD = ROOT / "reports/v9.0/profit_visibility_report_v1.json"
V91_DECISION_REPORT = ROOT / "reports/v9.1/profit_visibility_decision_report.md"
VALIDATOR = ROOT / "scripts" / "validate_profit_visibility_report.py"
SEALED_PROTOCOL_REF = "phase163-registration:sha256:" + ("1" * 64)
SUPPORTED_EVALUATION_RUN_REF = "phase164-evaluation:run-001"
ALLOWED_PAPER_FORWARD_WORDING = "eligible for paper-forward prerequisite review"
CANONICAL_HYPOTHESIS_IDS = (
    "family.mean_reversion.h001",
    "family.mean_reversion.h002",
    "family.mean_reversion.h003",
)

REQUIRED_ROW_FIELDS = (
    "family_id",
    "hypothesis_id",
    "sealed_hypothesis_index",
    "signal_family",
    "universe",
    "timeframe",
    "parameter_set",
    "filter_set",
    "split_protocol",
    "cost_model_fingerprint",
    "net_profit_factor",
    "net_expectancy",
    "max_drawdown",
    "turnover",
    "trade_count",
    "capacity_notional_bound",
    "slippage_sensitivity",
    "gate_statuses",
    "primary_stop_reason",
    "all_failures",
    "paper_forward_mapping_status",
)
REQUIRED_GATE_FIELDS = (
    "registration_anchor",
    "cost",
    "sample",
    "leakage",
    "oos_wfd_or_holdout",
    "mtc",
    "cost_sensitivity",
    "paper_forward_mapping",
)
REQUIRED_METRIC_FIELDS = (
    "net_profit_factor",
    "net_expectancy",
    "max_drawdown",
    "turnover",
    "trade_count",
    "capacity_notional_bound",
    "slippage_sensitivity",
)
TYPED_NULL_REASON_FIELDS = tuple(
    f"{field}_unavailable_reason" for field in REQUIRED_METRIC_FIELDS
)
PAPER_FORWARD_PREREQUISITE_CATEGORIES = (
    "risk_gate",
    "sizing",
    "accounting",
    "paper_forward_rehearsal",
)
FORBIDDEN_PAPER_FORWARD_WORDING = (
    "paper_forward_ready",
    "paper-forward ready",
    "paper forward ready",
    "live-shadow candidate",
    "live ready",
    "live readiness",
)
EXPANDED_FORBIDDEN_CLAIM_WORDING = (
    *FORBIDDEN_PAPER_FORWARD_WORDING,
    "market evidence",
    "market proof",
    "profit ready",
    "profit readiness",
    "profit claimable",
    "paper-forward readiness",
    "live-shadow ready",
    "live shadow ready",
    "live-shadow readiness",
    "live shadow readiness",
    "account ready",
    "account readiness",
    "broker ready",
    "broker readiness",
    "network ready",
    "network readiness",
    "credential ready",
    "credential readiness",
    "runtime ready",
    "runtime readiness",
)


def read_report_contract_doc() -> str:
    return REPORT_CONTRACT_DOC.read_text(encoding="utf-8")


def load_validator_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "validate_profit_visibility_report", VALIDATOR
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_contains_all(text: str, fragments: tuple[str, ...]) -> None:
    missing = [fragment for fragment in fragments if fragment not in text]
    assert not missing, f"missing report contract fragments: {missing}"


def build_report_row_fixture(
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "family_id": "family.mean_reversion",
        "hypothesis_id": "family.mean_reversion.h001",
        "sealed_hypothesis_index": 1,
        "signal_family": "mean_reversion",
        "universe": "top_100_us_equities",
        "timeframe": "1d",
        "parameter_set": {"lookback": 20},
        "filter_set": {"min_volume": "pre_registered"},
        "split_protocol": "purged-oos-holdout",
        "cost_model_fingerprint": "sha256:" + ("a" * 64),
        "registration_anchor_ref": SEALED_PROTOCOL_REF,
        "net_profit_factor": 1.25,
        "net_expectancy": 0.02,
        "max_drawdown": -0.08,
        "turnover": 1.1,
        "trade_count": 260,
        "capacity_notional_bound": "max 10,000 USD equivalent per candidate",
        "slippage_sensitivity": "base and adverse ladder passed",
        "gate_statuses": {
            gate: {"status": "passed", "reason": "passed"}
            for gate in REQUIRED_GATE_FIELDS
        },
        "primary_stop_reason": None,
        "all_failures": [],
        "paper_forward_mapping_status": "not_evaluated",
        "p_raw": 0.01,
        "p_value_provenance": {
            "sealed_protocol_ref": SEALED_PROTOCOL_REF,
            "hypothesis_id": "family.mean_reversion.h001",
            "supported_evaluation_run_ref": SUPPORTED_EVALUATION_RUN_REF,
        },
    }
    if overrides:
        row.update(overrides)
    return row


def registration_anchor_fixture(
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    registration = {
        "registered_bytes_sha256": "sha256:" + ("b" * 64),
        "current_bytes_sha256": "sha256:" + ("b" * 64),
        "ots_proof_path": "registrations/phase163-candidates.ots",
        "anchor_kind": "OpenTimestamps",
        "external_anchor_verified": True,
        "anchor_stale": False,
        "anchor_author_controlled": False,
        "anchor_force_pushable": False,
    }
    if overrides:
        registration.update(overrides)
    return registration


def test_data_provenance_missing_blank_and_unsupported_fail_closed() -> None:
    validator = load_validator_module()
    invalid_cases = {
        "missing": {},
        "blank": {"data_provenance": "   "},
        "non_string": {"data_provenance": {"kind": "synthetic_fixture_not_market"}},
        "unsupported": {"data_provenance": "market_data_claimed"},
    }

    for case_name, overrides in invalid_cases.items():
        result = validator.validate_data_provenance(
            build_report_row_fixture(overrides)
        )
        assert result["status"] == "invalid_disqualified", case_name
        assert result["profit_visible"] is False
        assert result["reason"].startswith("data_provenance_"), case_name


def test_synthetic_fixture_provenance_is_non_market_and_non_claimable() -> None:
    validator = load_validator_module()

    for provenance in (
        "synthetic_fixture_not_market",
        "synthetic_known_noise_not_market",
    ):
        result = validator.validate_data_provenance(
            build_report_row_fixture({"data_provenance": provenance})
        )
        assert result["status"] == "data_provenance_valid"
        assert result["data_provenance"] == provenance
        assert result["provenance_class"] == "synthetic"
        assert result["market_evidence"] is False
        assert result["claimable"] is False
        assert result["profit_visible"] is False


def test_placeholder_proof_material_is_classified_before_routing() -> None:
    validator = load_validator_module()

    identical_fingerprint = validator.classify_proof_material(
        build_report_row_fixture(
            {
                "data_provenance": "synthetic_fixture_not_market",
                "cost_model_fingerprint": "sha256:" + ("f" * 64),
                "source_refs": ["fixtures/profit_visibility/synthetic.json"],
            }
        )
    )
    assert identical_fingerprint["status"] == "proof_material_rejected"
    assert identical_fingerprint["reason"] == "placeholder_sha256_fingerprint"
    assert identical_fingerprint["profit_visible"] is False

    empty_refs = validator.classify_proof_material(
        build_report_row_fixture(
            {
                "data_provenance": "synthetic_fixture_not_market",
                "cost_model_fingerprint": "sha256:" + ("1234567890abcdef" * 4),
                "source_refs": ["", "   "],
            }
        )
    )
    assert empty_refs["status"] == "proof_material_rejected"
    assert empty_refs["reason"] == "empty_source_refs"
    assert empty_refs["profit_visible"] is False

    contract_only = validator.validate_fixture_anchor_boundary(
        registration_anchor_fixture(
            {
                "anchor_kind": "phase163-registration-contract-only",
                "external_anchor_verified": True,
            }
        )
    )
    assert contract_only["status"] == "fixture_anchor_rejected"
    assert contract_only["reason"] == "contract_only_anchor_not_external_proof"
    assert contract_only["profit_visible"] is False


def test_fixture_unanchored_anchor_is_fixture_only_not_external_proof() -> None:
    validator = load_validator_module()

    result = validator.validate_fixture_anchor_boundary(
        registration_anchor_fixture(
            {
                "anchor_kind": "fixture_unanchored",
                "external_anchor_verified": False,
                "ots_proof_path": "",
            }
        )
    )
    assert result["status"] == "fixture_anchor_only"
    assert result["anchor_kind"] == "fixture_unanchored"
    assert result["anchor_scope"] == "fixture_only"
    assert result["external_anchor_verified"] is False
    assert result["claimable"] is False
    assert result["market_evidence"] is False
    assert result["profit_visible"] is False


def test_synthetic_survivor_shaped_row_is_non_claimable() -> None:
    validator = load_validator_module()
    row = build_report_row_fixture(
        {
            "data_provenance": "synthetic_fixture_not_market",
            "source_refs": ["fixtures/profit_visibility/synthetic-survivor.json"],
            "paper_forward_mapping_status": "eligible_for_mapping",
            "mtc_passed": True,
        }
    )

    boundary = validator.classify_candidate_claim_boundary(row)
    counts = validator.summarize_survivor_counts([row])

    assert boundary["status"] == "synthetic_fixture_non_claimable"
    assert boundary["profit_visible"] is False
    assert boundary["market_evidence"] is False
    assert boundary["claimable"] is False
    assert boundary["paper_forward_eligible"] is False
    assert boundary["live_shadow_ready"] is False
    assert boundary["live_ready"] is False
    assert boundary["account_ready"] is False
    assert boundary["broker_ready"] is False
    assert boundary["network_ready"] is False
    assert boundary["credential_ready"] is False
    assert boundary["runtime_ready"] is False
    assert counts["family.mean_reversion"]["survivor_count"] == 0


def test_synthetic_known_edge_returns_non_claimable_status() -> None:
    validator = load_validator_module()
    row = build_report_row_fixture(
        {
            "data_provenance": "synthetic_known_edge_not_market",
            "source_refs": ["fixtures/profit_visibility/synthetic-known-edge.json"],
            "paper_forward_mapping_status": "eligible_for_mapping",
            "mtc_passed": True,
        }
    )

    boundary = validator.classify_candidate_claim_boundary(row)

    assert boundary["status"] == "synthetic_edge_non_claimable"
    assert boundary["status"] != "profit_visible"
    assert boundary["status"] != "honest_null_ship"
    assert boundary["status"] != "eligible_for_mapping"
    assert boundary["profit_visible"] is False
    assert boundary["market_evidence"] is False
    assert boundary["claimable"] is False
    assert boundary["paper_forward_eligible"] is False


def build_mtc_rows(
    p_values: tuple[float, ...] = (0.01, 0.02, 0.2),
) -> list[dict[str, object]]:
    rows = []
    for index, (hypothesis_id, p_raw) in enumerate(
        zip(CANONICAL_HYPOTHESIS_IDS, p_values, strict=True), start=1
    ):
        rows.append(
            build_report_row_fixture(
                {
                    "hypothesis_id": hypothesis_id,
                    "sealed_hypothesis_index": index,
                    "p_raw": p_raw,
                    "p_value_provenance": {
                        "sealed_protocol_ref": SEALED_PROTOCOL_REF,
                        "hypothesis_id": hypothesis_id,
                        "supported_evaluation_run_ref": SUPPORTED_EVALUATION_RUN_REF,
                    },
                }
            )
        )
    return rows


def validate_report_shape_stub(report: dict[str, object]) -> str:
    rows = report.get("candidate_rows")
    if not isinstance(rows, list) or not rows:
        return "shape_invalid"
    for row in rows:
        if not isinstance(row, dict):
            return "shape_invalid"
        if any(field not in row for field in REQUIRED_ROW_FIELDS):
            return "shape_invalid"
        gates = row.get("gate_statuses")
        if not isinstance(gates, dict):
            return "shape_invalid"
        if any(gate not in gates for gate in REQUIRED_GATE_FIELDS):
            return "shape_invalid"
        for metric, reason_field in zip(
            REQUIRED_METRIC_FIELDS, TYPED_NULL_REASON_FIELDS, strict=True
        ):
            value = row.get(metric)
            reason = row.get(reason_field)
            if value is None and not (isinstance(reason, str) and reason.strip()):
                return "shape_invalid"
            if value in ("", "TBD", "unknown"):
                return "shape_invalid"
    return "shape_valid"


def summarize_survivor_counts_stub(
    rows: list[dict[str, object]],
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        family_id = str(row["family_id"])
        counts.setdefault(family_id, {"candidate_count": 0, "survivor_count": 0})
        counts[family_id]["candidate_count"] += 1
        if row.get("paper_forward_mapping_status") == "eligible_for_mapping":
            counts[family_id]["survivor_count"] += 1
    return counts


def test_report_contract_pins_canonical_row_shape() -> None:
    text = read_report_contract_doc()

    assert "# Profit Visibility Report v1" in text
    assert "canonical hypothesis rows" in text
    assert "one row for every sealed finest-granularity hypothesis" in text
    assert_contains_all(text, REQUIRED_ROW_FIELDS)
    assert_contains_all(text, REQUIRED_GATE_FIELDS)
    assert_contains_all(text, REQUIRED_METRIC_FIELDS)

    report = {"candidate_rows": [build_report_row_fixture()]}
    assert validate_report_shape_stub(report) == "shape_valid"


def test_failed_metrics_use_typed_null_with_reasons() -> None:
    failed_metrics = {
        field: None for field in REQUIRED_METRIC_FIELDS
    } | {
        reason: "unavailable because registration_anchor failed"
        for reason in TYPED_NULL_REASON_FIELDS
    }
    report = {
        "candidate_rows": [
            build_report_row_fixture(
                {
                    **failed_metrics,
                    "gate_statuses": {
                        gate: {
                            "status": "failed" if gate == "registration_anchor" else "not_evaluated",
                            "reason": "registration_anchor_invalid",
                        }
                        for gate in REQUIRED_GATE_FIELDS
                    },
                    "primary_stop_reason": "registration_anchor_invalid",
                    "all_failures": ["registration_anchor_invalid"],
                }
            )
        ]
    }

    assert validate_report_shape_stub(report) == "shape_valid"

    missing_reason = dict(report["candidate_rows"][0])
    missing_reason.pop("net_profit_factor_unavailable_reason")
    assert validate_report_shape_stub({"candidate_rows": [missing_reason]}) == (
        "shape_invalid"
    )

    zero_placeholder = dict(report["candidate_rows"][0])
    zero_placeholder["net_profit_factor"] = ""
    assert validate_report_shape_stub({"candidate_rows": [zero_placeholder]}) == (
        "shape_invalid"
    )


def test_survivor_counts_include_zero_survivor_output() -> None:
    rows = [
        build_report_row_fixture(
            {
                "family_id": "family.zero_survivor",
                "hypothesis_id": "family.zero_survivor.h001",
                "paper_forward_mapping_status": "blocked",
            }
        )
    ]

    counts = summarize_survivor_counts_stub(rows)

    assert counts["family.zero_survivor"]["candidate_count"] == 1
    assert counts["family.zero_survivor"]["survivor_count"] == 0


def test_family_summary_is_derived_only() -> None:
    report = {
        "candidate_rows": [build_report_row_fixture()],
        "family_summaries": [
            {
                "family_id": "family.mean_reversion",
                "candidate_count": 1,
                "survivor_count": 0,
                "derived_from_all_hypothesis_rows": True,
            }
        ],
    }

    assert report["family_summaries"][0]["derived_from_all_hypothesis_rows"] is True


def test_v9_report_payload_is_concrete_plumbing_only_evidence() -> None:
    validator = load_validator_module()
    report = json.loads(V9_REPORT_PAYLOAD.read_text(encoding="utf-8"))

    assert report["report_version"] == "ProfitVisibilityReport.v1"
    assert report["overall_outcome"] == "plumbing_only"
    assert report["profit_visible"] is False
    assert report["survivor_count"] == 0
    assert report["decision_report_ref"] == (
        "reports/v9.0/profit_visibility_decision_report.md"
    )

    shape = validator.validate_report_shape(report)
    assert shape == {"status": "shape_valid", "row_count": 1}

    family_summary = report["family_summaries"][0]
    assert family_summary["derived_from_all_hypothesis_rows"] is True
    assert family_summary["family_outcome"] == "plumbing_only"
    assert family_summary["candidate_count"] == 1
    assert family_summary["survivor_count"] == 0


def test_validator_help_exposes_supported_report_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "ProfitVisibilityReport.v1" in result.stdout


def test_report_contract_pins_registration_anchor_and_mtc_sections() -> None:
    text = read_report_contract_doc()

    assert_contains_all(
        text,
        (
            "## Registration Anchor Gate",
            "## FWER/Holm Multiple-Testing Control",
            "## P-Value Provenance",
            "## Exact Denominator Fail-Closed Rules",
            "p=1 padding is forbidden",
            "sealed_denominator",
            "p_value_provenance",
            "p_holm_adjusted",
            "holm_rank",
            "mtc_passed",
        ),
    )


def test_registration_anchor_mismatch_fails_closed() -> None:
    validator = load_validator_module()

    valid = validator.validate_registration_anchor(registration_anchor_fixture())
    assert valid["status"] == "registration_anchor_valid"

    invalid_cases = {
        "missing_ots": {"ots_proof_path": "registrations/phase163-candidates.json"},
        "byte_mismatch": {"current_bytes_sha256": "sha256:" + ("c" * 64)},
        "missing_anchor_verification": {"external_anchor_verified": False},
        "string_false_anchor_verification": {"external_anchor_verified": "false"},
        "commit_timestamp_anchor_kind": {"anchor_kind": "commit timestamp"},
        "stale_anchor": {"anchor_stale": True},
        "author_controlled_anchor": {"anchor_author_controlled": True},
        "force_pushable_anchor": {"anchor_force_pushable": True},
    }
    for case_name, overrides in invalid_cases.items():
        result = validator.validate_registration_anchor(
            registration_anchor_fixture(overrides)
        )
        assert result["status"] == "invalid_disqualified", case_name
        assert result["profit_visible"] is False


def test_holm_requires_exact_sealed_denominator() -> None:
    validator = load_validator_module()
    rows = build_mtc_rows()

    assert (
        validator.apply_holm_fwer(rows, 3, "FWER/Holm", "FWER", 0.05)["status"]
        == "mtc_passed"
    )

    invalid_cases = {
        "missing_row": (rows[:-1], 3),
        "extra_row": (
            rows
            + [
                build_report_row_fixture(
                    {
                        "hypothesis_id": "family.mean_reversion.h004",
                        "sealed_hypothesis_index": 4,
                        "p_raw": 0.03,
                        "p_value_provenance": {
                            "sealed_protocol_ref": SEALED_PROTOCOL_REF,
                            "hypothesis_id": "family.mean_reversion.h004",
                            "supported_evaluation_run_ref": (
                                SUPPORTED_EVALUATION_RUN_REF
                            ),
                        },
                    }
                )
            ],
            3,
        ),
        "survivor_only_input": (rows[:1], 3),
        "denominator_shrinkage": (rows, 2),
    }
    for case_name, (candidate_rows, sealed_count) in invalid_cases.items():
        result = validator.apply_holm_fwer(
            candidate_rows, sealed_count, "FWER/Holm", "FWER", 0.05
        )
        assert result["status"] == "profit_visible_false", case_name
        assert result["profit_visible"] is False

    invalid_parameters = (
        ("DSR", "FWER", 0.05),
        ("FWER/Holm", "FDR", 0.05),
        ("FWER/Holm", "FWER", 0.1),
    )
    for method, target, alpha in invalid_parameters:
        result = validator.apply_holm_fwer(rows, 3, method, target, alpha)
        assert result["status"] == "profit_visible_false"
        assert result["profit_visible"] is False

    padded_rows = build_mtc_rows()
    padded_rows[2] = dict(padded_rows[2])
    padded_rows[2]["p_raw"] = 1.0
    padded_rows[2]["p_value_provenance"] = {
        "sealed_protocol_ref": SEALED_PROTOCOL_REF,
        "hypothesis_id": padded_rows[2]["hypothesis_id"],
        "supported_evaluation_run_ref": SUPPORTED_EVALUATION_RUN_REF,
        "p_value_source": "p_value_padding",
    }
    result = validator.apply_holm_fwer(
        padded_rows, 3, "FWER/Holm", "FWER", 0.05
    )
    assert result["status"] == "profit_visible_false"
    assert result["profit_visible"] is False


def test_holm_rejects_pvalue_without_typed_provenance() -> None:
    validator = load_validator_module()

    valid = validator.validate_pvalue_provenance(build_mtc_rows()[0])
    assert valid["status"] == "pvalue_provenance_valid"

    rows_missing_provenance = build_mtc_rows()
    rows_missing_provenance[0] = dict(rows_missing_provenance[0])
    rows_missing_provenance[0].pop("p_value_provenance")
    result = validator.apply_holm_fwer(
        rows_missing_provenance, 3, "FWER/Holm", "FWER", 0.05
    )
    assert result["status"] == "invalid_disqualified"

    for p_raw in (math.nan, math.inf, -0.01, 1.01):
        rows = build_mtc_rows()
        rows[0] = dict(rows[0])
        rows[0]["p_raw"] = p_raw
        result = validator.apply_holm_fwer(rows, 3, "FWER/Holm", "FWER", 0.05)
        assert result["status"] == "invalid_disqualified", p_raw

    unregistered_rows = build_mtc_rows()
    unregistered_rows[1] = dict(unregistered_rows[1])
    unregistered_rows[1]["p_value_provenance"] = {
        "sealed_protocol_ref": "phase163-registration:sha256:" + ("9" * 64),
        "hypothesis_id": unregistered_rows[1]["hypothesis_id"],
        "supported_evaluation_run_ref": SUPPORTED_EVALUATION_RUN_REF,
    }
    result = validator.apply_holm_fwer(
        unregistered_rows, 3, "FWER/Holm", "FWER", 0.05
    )
    assert result["status"] == "invalid_disqualified"


def test_holm_outputs_per_row_and_family_summary_fields() -> None:
    validator = load_validator_module()

    failed = validator.apply_holm_fwer(
        build_mtc_rows((0.2, 0.3, 0.4)), 3, "FWER/Holm", "FWER", 0.05
    )
    assert failed["status"] == "mtc_failed"
    assert failed["profit_visible"] is False

    result = validator.apply_holm_fwer(
        build_mtc_rows(), 3, "FWER/Holm", "FWER", 0.05
    )

    assert result["status"] == "mtc_passed"
    assert result["profit_visible"] is True
    assert result["sealed_denominator"] == 3
    assert result["method"] == "FWER/Holm"
    assert result["error_rate_target"] == "FWER"
    assert result["alpha"] == 0.05
    assert result["input_count"] == 3

    adjusted_rows = result["rows"]
    assert len(adjusted_rows) == 3
    assert adjusted_rows[0]["p_raw"] == 0.01
    assert adjusted_rows[0]["p_holm_adjusted"] == 0.03
    assert adjusted_rows[0]["holm_rank"] == 1
    assert adjusted_rows[0]["mtc_passed"] is True
    assert adjusted_rows[0]["mtc_reason"] == "mtc_passed"
    assert adjusted_rows[2]["p_holm_adjusted"] == 0.2
    assert adjusted_rows[2]["mtc_passed"] is False
    assert adjusted_rows[2]["mtc_reason"] == "mtc_failed"
    for row in adjusted_rows:
        assert row["sealed_denominator"] == 3
        assert row["mtc_method"] == "FWER/Holm"
        assert row["error_rate_target"] == "FWER"
        assert row["alpha"] == 0.05
        assert row["mtc_input_count"] == 3

    summary = result["family_summaries"][0]
    assert summary["family_id"] == "family.mean_reversion"
    assert summary["candidate_count"] == 3
    assert summary["mtc_passed_count"] == 2
    assert summary["mtc_failed_count"] == 1
    assert summary["sealed_denominator"] == 3
    assert summary["method"] == "FWER/Holm"
    assert summary["error_rate_target"] == "FWER"
    assert summary["alpha"] == 0.05
    assert summary["input_count"] == 3


def test_report_contract_pins_paper_forward_mapping_sections() -> None:
    text = read_report_contract_doc()

    assert_contains_all(
        text,
        (
            "## Paper-Forward Prerequisite Mapping",
            "## Economics Divergence Stop",
            "## Claim Wording Guard",
            "## Absence And Protected Surface Guard",
            ALLOWED_PAPER_FORWARD_WORDING,
            "no dedicated paper-forward handoff artifact",
            "no workflow trigger",
            "no runtime behavior",
            "no new public schema",
            "live-preflight schema",
            *FORBIDDEN_PAPER_FORWARD_WORDING,
        ),
    )


def test_survivor_mapping_is_prerequisite_review_only() -> None:
    validator = load_validator_module()
    survivor = build_report_row_fixture(
        {
            "paper_forward_mapping_status": "eligible_for_mapping",
            "mtc_passed": True,
        }
    )

    mapping = validator.map_paper_forward_prerequisites(survivor)

    assert mapping["status"] == "eligible_for_paper_forward_prerequisite_review"
    assert mapping["paper_forward_mapping_status"] == "eligible_for_mapping"
    assert mapping["claim_wording"] == ALLOWED_PAPER_FORWARD_WORDING
    assert mapping["workflow_trigger"] is None
    assert mapping["handoff_artifact_path"] is None
    assert mapping["runtime_actions"] == []


def test_synthetic_rows_block_paper_forward_mapping() -> None:
    validator = load_validator_module()
    survivor = build_report_row_fixture(
        {
            "data_provenance": "synthetic_fixture_not_market",
            "source_refs": ["fixtures/profit_visibility/synthetic-survivor.json"],
            "paper_forward_mapping_status": "eligible_for_mapping",
            "mtc_passed": True,
        }
    )

    mapping = validator.map_paper_forward_prerequisites(survivor)

    assert mapping["status"] == "paper_forward_mapping_blocked"
    assert mapping["paper_forward_mapping_status"] == "blocked"
    assert mapping["reason"] == "synthetic_provenance_non_claimable"
    assert mapping["claim_boundary_status"] == "synthetic_fixture_non_claimable"
    assert mapping["profit_visible"] is False
    assert mapping["market_evidence"] is False
    assert mapping["paper_forward_eligible"] is False
    assert mapping["runtime_actions"] == []


def test_paper_forward_mapping_uses_existing_references_only() -> None:
    validator = load_validator_module()
    survivor = build_report_row_fixture(
        {
            "paper_forward_mapping_status": "eligible_for_mapping",
            "mtc_passed": True,
        }
    )

    mapping = validator.map_paper_forward_prerequisites(survivor)
    refs = mapping["prerequisite_refs"]

    assert {ref["category"] for ref in refs} == set(
        PAPER_FORWARD_PREREQUISITE_CATEGORIES
    )
    for ref in refs:
        source_path = ROOT / ref["path"]
        assert source_path.exists(), ref
        assert "live_preflight" not in ref["path"]
        assert not ref["path"].endswith(".schema.json")


def test_forbidden_paper_forward_and_live_shadow_wording_is_rejected() -> None:
    validator = load_validator_module()

    allowed = validator.validate_claim_wording(ALLOWED_PAPER_FORWARD_WORDING)
    assert allowed["status"] == "claim_wording_valid"

    for wording in FORBIDDEN_PAPER_FORWARD_WORDING:
        result = validator.validate_claim_wording(wording)
        assert result["status"] == "claim_wording_rejected", wording
        assert result["allowed_wording"] == ALLOWED_PAPER_FORWARD_WORDING


def test_claim_wording_rejects_market_profit_paper_and_live_readiness() -> None:
    validator = load_validator_module()

    allowed = validator.validate_claim_wording(ALLOWED_PAPER_FORWARD_WORDING)
    assert allowed["status"] == "claim_wording_valid"

    positive_claims = (
        "synthetic fixture produced market evidence",
        "candidate has market proof",
        "candidate is profit ready",
        "candidate reached profit readiness",
        "synthetic row is profit claimable",
        "candidate is paper forward ready",
        "paper-forward readiness is granted",
        "candidate is live-shadow ready",
        "candidate is live shadow ready",
        "candidate is live ready",
        "candidate reached live readiness",
        "account ready after closure",
        "account readiness is established",
        "broker ready after review",
        "broker readiness is established",
        "network ready after review",
        "network readiness is established",
        "credential ready after review",
        "credential readiness is established",
        "runtime ready after review",
        "runtime readiness is established",
    )
    for wording in positive_claims:
        result = validator.validate_claim_wording(wording)
        assert result["status"] == "claim_wording_rejected", wording
        assert result["forbidden_matches"], wording

    for wording in EXPANDED_FORBIDDEN_CLAIM_WORDING:
        result = validator.validate_claim_wording(wording)
        assert result["status"] == "claim_wording_rejected", wording

    negated_exclusions = (
        "this phase does not grant live readiness",
        "no broker readiness is approved",
        "never credential ready",
    )
    for wording in negated_exclusions:
        result = validator.validate_claim_wording(wording)
        assert result["status"] == "claim_wording_valid", wording


def test_phase169_fixture_report_claim_wording_rejects_readiness_claims() -> None:
    validator = load_validator_module()
    positive_claims = (
        "synthetic fixture is market evidence",
        "fixture row establishes profit readiness",
        "fixture row is paper forward ready",
        "paper-forward readiness is granted",
        "live-shadow readiness is established",
        "live readiness is established",
        "account readiness is established",
        "broker readiness is established",
        "network readiness is established",
        "credential readiness is established",
        "runtime readiness is established",
    )
    for wording in positive_claims:
        result = validator.validate_claim_wording(wording)
        assert result["status"] == "claim_wording_rejected", wording
        assert result["forbidden_matches"], wording

    explicit_exclusions = (
        "no market evidence",
        "no profit readiness",
        "no paper-forward readiness",
        "no live-shadow readiness",
        "no live readiness",
        "no account readiness",
        "no broker readiness",
        "no network readiness",
        "no credential readiness",
        "no runtime readiness",
    )
    for wording in explicit_exclusions:
        assert validator.validate_claim_wording(wording)["status"] == (
            "claim_wording_valid"
        )

    text = V91_DECISION_REPORT.read_text(encoding="utf-8")
    assert_contains_all(
        text,
        (
            "fixture-only evaluation instrument",
            "synthetic non-claimable",
            "route-ineligible",
            *explicit_exclusions,
        ),
    )
    result = validator.validate_claim_wording(text)
    assert result["status"] == "claim_wording_valid", result["forbidden_matches"]
