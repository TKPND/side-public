"""Phase 169 fixture report projection guards."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
E2E_HELPER = ROOT / "scripts" / "profit_visibility_e2e_report.py"
STAT_HELPER = ROOT / "scripts" / "profit_visibility_statistical_evaluator.py"
VALIDATOR = ROOT / "scripts" / "validate_profit_visibility_report.py"


def load_module(path: Path, name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_e2e_module() -> object:
    return load_module(E2E_HELPER, "profit_visibility_e2e_report")


def load_stat_module() -> object:
    return load_module(STAT_HELPER, "profit_visibility_statistical_evaluator")


def load_validator_module() -> object:
    return load_module(VALIDATOR, "validate_profit_visibility_report")


def rows_by_fixture(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["synthetic_fixture_id"]): row
        for row in payload["candidate_rows"]
    }


def families_by_fixture(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(summary["synthetic_fixture_id"]): summary
        for summary in payload["family_summaries"]
    }


def assert_row_preserves_phase168_evidence(
    report_row: dict[str, Any], expected_row: dict[str, Any], expected_family: dict[str, Any]
) -> None:
    copied_fields = (
        "family_id",
        "hypothesis_id",
        "sealed_hypothesis_index",
        "p_raw",
        "p_holm_adjusted",
        "holm_rank",
        "mtc_passed",
        "mtc_reason",
        "sealed_denominator",
        "mtc_method",
        "error_rate_target",
        "alpha",
        "mtc_input_count",
        "B",
        "seed",
        "test_statistic",
        "null_method",
        "p_value_method",
        "observed_stat",
        "null_extreme_count",
        "registration_anchor_ref",
        "sealed_protocol_ref",
        "supported_evaluation_run_ref",
        "cost_model_fingerprint",
        "net_expectancy",
        "net_profit_factor",
        "max_drawdown",
        "turnover",
        "trade_count",
        "capacity_notional_bound",
        "slippage_sensitivity",
        "data_provenance",
        "source_refs",
        "p_value_provenance",
    )
    for field in copied_fields:
        assert report_row[field] == expected_row[field], field

    assert report_row["sealed_hypothesis_count"] == expected_family["sealed_hypothesis_count"]
    assert report_row["computed_route_label"] == expected_family["statistical_route_status"]
    assert report_row["statistical_route_status"] == expected_family["statistical_route_status"]
    assert report_row["known_fixture_route_reason"] == expected_family[
        "known_fixture_route_reason"
    ]
    assert report_row["p_value_provenance"]["p_value_source"] == (
        "phase168_seeded_sign_flip_permutation"
    )
    assert report_row["p_value_provenance"]["hypothesis_id"] == report_row["hypothesis_id"]


def test_e2e_report_payload_is_generated_from_computed_fixture_evidence() -> None:
    e2e = load_e2e_module()
    evaluator = load_stat_module()
    validator = load_validator_module()

    payload = e2e.build_fixture_report_payload(fixture="all", B=999, seed=168)

    assert payload["report_version"] == "ProfitVisibilityReport.v1"
    assert payload["milestone"] == "v9.1"
    assert payload["fixture"] == "all"
    assert payload["B"] == 999
    assert payload["seed"] == 168
    assert payload["profit_visible"] is False
    assert payload["route_eligible"] is False
    assert validator.validate_report_shape(payload) == {
        "status": "shape_valid",
        "row_count": 2,
    }

    report_rows = rows_by_fixture(payload)
    assert set(report_rows) == {"known-noise", "known-edge"}
    for fixture_id, report_row in report_rows.items():
        expected_family = evaluator.evaluate_fixture_family_statistics(fixture_id)
        assert evaluator.validate_statistical_evaluation_result(expected_family) == {
            "status": "statistical_evaluation_valid",
            "profit_visible": False,
            "route_eligible": False,
        }
        expected_row = expected_family["adjusted_rows"][0]
        assert_row_preserves_phase168_evidence(
            report_row, expected_row, expected_family
        )


def test_e2e_report_payload_validates_and_cli_replay_is_deterministic(
    tmp_path: Path,
) -> None:
    e2e = load_e2e_module()
    validator = load_validator_module()
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"

    first = subprocess.run(
        [
            sys.executable,
            str(E2E_HELPER),
            "--fixture",
            "all",
            "--B",
            "999",
            "--seed",
            "168",
            "--output",
            str(first_output),
            "--pretty",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    second = subprocess.run(
        [
            sys.executable,
            str(E2E_HELPER),
            "--fixture",
            "all",
            "--B",
            "999",
            "--seed",
            "168",
            "--output",
            str(second_output),
            "--pretty",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first_output.read_text(encoding="utf-8") == second_output.read_text(
        encoding="utf-8"
    )

    payload = json.loads(first_output.read_text(encoding="utf-8"))
    assert payload == json.loads(second_output.read_text(encoding="utf-8"))
    assert "NaN" not in first_output.read_text(encoding="utf-8")
    assert "Infinity" not in first_output.read_text(encoding="utf-8")
    assert "-Infinity" not in first_output.read_text(encoding="utf-8")
    assert e2e.validate_fixture_report_payload(payload)["status"] == (
        "fixture_report_valid"
    )
    assert validator.validate_report_shape(payload)["status"] == "shape_valid"

    validator_run = subprocess.run(
        [sys.executable, str(VALIDATOR), str(first_output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert validator_run.returncode == 0, validator_run.stderr
    assert json.loads(validator_run.stdout)["status"] == "shape_valid"


def test_fixture_report_validation_rejects_row_claimability_drift() -> None:
    e2e = load_e2e_module()
    payload = e2e.build_fixture_report_payload(fixture="all", B=999, seed=168)

    for field in ("profit_visible", "route_eligible"):
        drifted = json.loads(json.dumps(payload))
        drifted["candidate_rows"][0][field] = True

        result = e2e.validate_fixture_report_payload(drifted)

        assert result["status"] == "fixture_report_invalid"
        assert result["reason"] == "row_claimability_drift"
        assert result["field"] == field


def test_fixture_report_validation_rejects_external_proof_shaped_fixture_anchor() -> None:
    e2e = load_e2e_module()
    validator = load_validator_module()
    payload = e2e.build_fixture_report_payload(fixture="all", B=999, seed=168)

    for proof_field in ("ots_proof_path", "proof_ref"):
        anchor_result = validator.validate_fixture_anchor_boundary(
            {
                "anchor_kind": "fixture_unanchored",
                "external_anchor_verified": True,
                proof_field: "proof.ots",
            }
        )
        assert anchor_result["status"] == "fixture_anchor_rejected"
        assert anchor_result["reason"] == "fixture_anchor_external_proof_drift"
    assert proof_field in anchor_result["proof_like_fields"]

    drifted = json.loads(json.dumps(payload))
    drifted["candidate_rows"][0]["registration_anchor"]["external_anchor_verified"] = True
    drifted["candidate_rows"][0]["registration_anchor"]["ots_proof_path"] = "proof.ots"

    result = e2e.validate_fixture_report_payload(drifted)

    assert result["status"] == "fixture_report_invalid"
    assert result["reason"] == "fixture_anchor_boundary_invalid"
    assert result["anchor"]["reason"] == "fixture_anchor_external_proof_drift"


def test_fixture_report_validation_rejects_row_external_anchor_drift() -> None:
    e2e = load_e2e_module()
    payload = e2e.build_fixture_report_payload(fixture="all", B=999, seed=168)
    drifted = json.loads(json.dumps(payload))
    drifted["candidate_rows"][0]["external_anchor_verified"] = True

    result = e2e.validate_fixture_report_payload(drifted)

    assert result["status"] == "fixture_report_invalid"
    assert result["reason"] == "row_external_anchor_drift"
    assert result["field"] == "external_anchor_verified"


def test_gate_statuses_and_fixture_anchor_labels_are_explicit() -> None:
    e2e = load_e2e_module()
    validator = load_validator_module()

    payload = e2e.build_fixture_report_payload(fixture="all", B=999, seed=168)

    assert payload["primary_stop_reason"] == "registration_anchor_invalid"
    for row in payload["candidate_rows"]:
        assert row["anchor_kind"] == "fixture_unanchored"
        assert row["external_anchor_verified"] is False
        assert row["registration_anchor"]["anchor_kind"] == "fixture_unanchored"
        assert row["registration_anchor"]["external_anchor_verified"] is False
        assert validator.validate_fixture_anchor_boundary(row["registration_anchor"]) == {
            "status": "fixture_anchor_only",
            "profit_visible": False,
            "reason": "fixture_unanchored_not_external_proof",
            "anchor_kind": "fixture_unanchored",
            "anchor_scope": "fixture_only",
            "external_anchor_verified": False,
            "market_evidence": False,
            "claimable": False,
        }

        gates = row["gate_statuses"]
        assert gates["cost"]["status"] == "computed"
        assert gates["cost"]["reason"] == "phase167_costed_metrics_evaluated"
        assert gates["mtc"]["status"] == "computed"
        assert gates["mtc"]["mtc_passed"] is row["mtc_passed"]
        assert gates["mtc"]["reason"] == row["mtc_reason"]

        blocked_expectations = {
            "registration_anchor": "registration_anchor_invalid",
            "external_anchor": "external_anchor_or_ots_missing",
            "sample": "sample_or_leakage_failed",
            "leakage": "sample_or_leakage_failed",
            "oos_wfd_or_holdout": "oos_wfd_or_holdout_failed",
            "cost_sensitivity": "cost_sensitivity_failed",
            "paper_forward_mapping": "paper_forward_mapping_blocked",
        }
        for gate_name, reason in blocked_expectations.items():
            assert gates[gate_name]["status"] == "blocked", gate_name
            assert gates[gate_name]["reason"] == reason, gate_name

        assert row["primary_stop_reason"] == "registration_anchor_invalid"
        assert "registration_anchor_invalid" in row["all_failures"]
        assert "external_anchor_or_ots_missing" in row["all_failures"]
        assert "paper_forward_mapping_blocked" in row["all_failures"]


def test_known_edge_survivor_shape_remains_synthetic_non_claimable_in_report() -> None:
    e2e = load_e2e_module()
    evaluator = load_stat_module()

    payload = e2e.build_fixture_report_payload(fixture="all", B=999, seed=168)
    edge_family = evaluator.evaluate_fixture_family_statistics("known-edge")
    edge_row = rows_by_fixture(payload)["known-edge"]
    edge_summary = families_by_fixture(payload)["known-edge"]

    assert edge_family["holm_fwer"]["status"] == "mtc_passed"
    assert edge_row["mtc_passed"] is True
    assert edge_row["p_raw"] == edge_family["adjusted_rows"][0]["p_raw"]
    assert edge_row["p_holm_adjusted"] == edge_family["adjusted_rows"][0][
        "p_holm_adjusted"
    ]
    assert edge_row["statistical_route_status"] == "synthetic_edge_non_claimable"
    assert edge_row["computed_route_label"] == "synthetic_edge_non_claimable"
    assert edge_row["claim_boundary_status"] == "synthetic_edge_non_claimable"
    assert edge_row["data_provenance"] == "synthetic_known_edge_not_market"
    assert edge_row["profit_visible"] is False
    assert edge_row["route_eligible"] is False
    assert edge_row["paper_forward_mapping_status"] == "blocked"
    assert edge_summary["computed_statistical_route_status"] == (
        "synthetic_edge_non_claimable"
    )
    assert edge_summary["mtc_passed_count"] == 1
    assert edge_summary["survivor_count"] == 0
    assert edge_summary["profit_visible"] is False
    assert payload["profit_visible"] is False
    assert payload["route_eligible"] is False

    for key in (
        "market_evidence",
        "claimable",
        "paper_forward_eligible",
        "live_shadow_ready",
        "live_ready",
        "account_ready",
        "broker_ready",
        "network_ready",
        "credential_ready",
        "runtime_ready",
    ):
        assert edge_row[key] is False, key
