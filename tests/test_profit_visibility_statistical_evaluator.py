"""Permutation and Holm/FWER guards for Phase 168 synthetic fixtures."""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STAT_HELPER = ROOT / "scripts" / "profit_visibility_statistical_evaluator.py"


def load_stat_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "profit_visibility_statistical_evaluator", STAT_HELPER
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_permutation_p_raw_uses_sign_flip_null_and_plus_one_correction() -> None:
    evaluator = load_stat_module()
    signs = [
        [1, 1, 1],
        [1, -1, 1],
        [-1, 1, -1],
        [1, 1, 1],
    ]

    result = evaluator.permutation_p_raw(
        [1.0, 2.0, -1.0],
        B=4,
        seed=168,
        sign_draws=signs,
    )

    assert result["status"] == "permutation_evaluated"
    assert result["test_statistic"] == "net_expectancy"
    assert result["null_method"] == "seed_controlled_sign_flip"
    assert result["p_value_method"] == "one_sided_positive_plus_one"
    assert result["observed_stat"] == round(2.0 / 3.0, 10)
    assert result["null_extreme_count"] == 3
    assert result["p_raw"] == 4.0 / 5.0
    assert result["p_raw"] > 0.0


def test_known_noise_permutation_replay_is_deterministic_with_default_b_and_seed() -> None:
    evaluator = load_stat_module()

    first = evaluator.evaluate_fixture_family_permutations("known-noise")
    second = evaluator.evaluate_fixture_family_permutations("known-noise")

    assert first == second
    assert first["B"] == 999
    assert first["seed"] == 168
    assert first["test_statistic"] == "net_expectancy"
    assert first["null_method"] == "seed_controlled_sign_flip"
    assert first["p_value_method"] == "one_sided_positive_plus_one"
    assert first["canonical_input_order"] == ["phase168.known-noise.h001"]
    assert first["rows"][0]["observed_stat"] == -1.4
    evaluator.assert_json_safe(first)
    json.dumps(first, allow_nan=False)


def test_known_edge_permutation_replay_records_full_metadata() -> None:
    evaluator = load_stat_module()

    result = evaluator.evaluate_fixture_family_permutations("known-edge")
    row = result["rows"][0]
    metadata = row["replay_metadata"]

    assert result["B"] == 999
    assert result["seed"] == 168
    assert row["observed_stat"] > 0
    assert row["p_raw"] <= 0.05
    assert metadata["B"] == 999
    assert metadata["seed"] == 168
    assert metadata["test_statistic"] == "net_expectancy"
    assert metadata["null_method"] == "seed_controlled_sign_flip"
    assert metadata["p_value_method"] == "one_sided_positive_plus_one"
    assert metadata["observed_stat"] == row["observed_stat"]
    assert metadata["null_extreme_count"] == row["null_extreme_count"]
    assert metadata["p_raw"] == row["p_raw"]
    assert metadata["hypothesis_id"] == "phase168.known-edge.h001"
    assert metadata["sealed_hypothesis_index"] == 1
    assert metadata["sealed_denominator"] == 1
    assert metadata["p_value_provenance"] == row["p_value_provenance"]


def test_replay_validation_rejects_seed_b_statistic_method_and_input_order_drift() -> None:
    evaluator = load_stat_module()
    result = evaluator.evaluate_fixture_family_permutations("known-edge")
    expected = copy.deepcopy(result["replay_metadata"])

    assert evaluator.validate_replay_metadata(result, expected)["status"] == (
        "replay_metadata_valid"
    )

    mutations = [
        ("rows", 0, "B", 998),
        ("rows", 0, "seed", 169),
        ("rows", 0, "test_statistic", "net_profit_factor"),
        ("rows", 0, "null_method", "trade_order_shuffle"),
        ("rows", 0, "p_value_method", "one_sided_without_plus_one"),
        ("rows", 0, "p_raw", 0.5),
        (
            "rows",
            0,
            "p_value_provenance",
            {
                **expected["rows"][0]["p_value_provenance"],
                "supported_evaluation_run_ref": "changed",
            },
        ),
    ]
    for path in mutations:
        drifted = copy.deepcopy(expected)
        drifted[path[0]][path[1]][path[2]] = path[3]
        validation = evaluator.validate_replay_metadata(result, drifted)
        assert validation["status"] == "replay_metadata_invalid"
        assert validation["profit_visible"] is False
        assert validation["route_eligible"] is False

    drifted = copy.deepcopy(expected)
    drifted["canonical_input_order"] = ["changed"]
    assert evaluator.validate_replay_metadata(result, drifted)["status"] == (
        "replay_metadata_invalid"
    )


def test_cli_replay_emits_deterministic_finite_json_for_known_noise_and_known_edge() -> None:
    for fixture in ("known-noise", "known-edge"):
        first = subprocess.run(
            [
                sys.executable,
                str(STAT_HELPER),
                "--fixture",
                fixture,
                "--B",
                "999",
                "--seed",
                "168",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        second = subprocess.run(
            [
                sys.executable,
                str(STAT_HELPER),
                "--fixture",
                fixture,
                "--B",
                "999",
                "--seed",
                "168",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert first.returncode == 0
        assert second.returncode == 0
        assert json.loads(first.stdout) == json.loads(second.stdout)
        assert "NaN" not in first.stdout
        assert "Infinity" not in first.stdout
        assert "-Infinity" not in first.stdout


def test_evaluator_delegates_holm_to_existing_report_helper(monkeypatch: Any) -> None:
    evaluator = load_stat_module()
    permutation = evaluator.evaluate_fixture_family_permutations("known-noise")
    calls: list[dict[str, Any]] = []

    def spy_apply_holm_fwer(
        rows: list[dict[str, Any]],
        sealed_hypothesis_count: int,
        method: str,
        error_rate_target: str,
        alpha: float,
    ) -> dict[str, Any]:
        calls.append(
            {
                "rows": rows,
                "sealed_hypothesis_count": sealed_hypothesis_count,
                "method": method,
                "error_rate_target": error_rate_target,
                "alpha": alpha,
            }
        )
        return {
            "status": "mtc_failed",
            "profit_visible": False,
            "sealed_denominator": sealed_hypothesis_count,
            "method": method,
            "error_rate_target": error_rate_target,
            "alpha": alpha,
            "input_count": len(rows),
            "rows": rows,
            "family_summaries": [],
        }

    monkeypatch.setattr(evaluator, "apply_holm_fwer", spy_apply_holm_fwer)

    result = evaluator.apply_fixture_family_holm_fwer(
        permutation["rows"],
        sealed_hypothesis_count=permutation["sealed_hypothesis_count"],
    )

    assert result["status"] == "mtc_failed"
    assert len(calls) == 1
    assert calls[0]["rows"] == permutation["rows"]
    assert calls[0]["sealed_hypothesis_count"] == 1
    assert calls[0]["method"] == "FWER/Holm"
    assert calls[0]["error_rate_target"] == "FWER"
    assert calls[0]["alpha"] == 0.05


def test_holm_fwer_rows_include_sealed_denominator_adjusted_pvalues_and_provenance() -> None:
    evaluator = load_stat_module()

    result = evaluator.evaluate_fixture_family_statistics("known-edge")
    row = result["adjusted_rows"][0]

    assert result["holm_fwer"]["status"] == "mtc_passed"
    assert result["sealed_denominator"] == 1
    assert result["method"] == "FWER/Holm"
    assert result["error_rate_target"] == "FWER"
    assert result["alpha"] == 0.05
    assert row["sealed_denominator"] == 1
    assert row["p_holm_adjusted"] == row["p_raw"]
    assert row["holm_rank"] == 1
    assert row["mtc_passed"] is True
    assert row["p_value_provenance"]["hypothesis_id"] == row["hypothesis_id"]
    assert row["p_value_provenance"]["sealed_protocol_ref"] == row["sealed_protocol_ref"]
    assert row["p_value_provenance"]["supported_evaluation_run_ref"] == (
        row["supported_evaluation_run_ref"]
    )


def test_known_noise_routes_to_computed_honest_null_ship_only_after_permutation_and_holm() -> None:
    evaluator = load_stat_module()

    result = evaluator.evaluate_fixture_family_statistics("known-noise")

    assert result["statistical_route_status"] == "honest_null_ship"
    assert result["known_fixture_route_reason"] == (
        "cost_permutation_holm_support_honest_null_ship"
    )
    assert result["rows"][0]["cost_model_fingerprint"].startswith("sha256:")
    assert result["rows"][0]["p_raw"] > 0.05
    assert result["holm_fwer"]["status"] == "mtc_failed"
    assert result["route"]["profit_visible"] is False
    assert result["route"]["route_eligible"] is False
    assert evaluator.validate_replay_metadata(
        result, result["replay_metadata"]
    )["status"] == "replay_metadata_valid"

    invalid_holm = copy.deepcopy(result["holm_fwer"])
    invalid_holm["status"] = "mtc_passed"
    route = evaluator.derive_synthetic_statistical_route("known-noise", result, invalid_holm)
    assert route["status"] == "statistical_route_invalid"
    assert route["profit_visible"] is False

    drifted = copy.deepcopy(result)
    drifted["replay_metadata"]["rows"][0]["seed"] = 169
    route = evaluator.derive_synthetic_statistical_route(
        "known-noise", drifted, result["holm_fwer"]
    )
    assert route["status"] == "replay_metadata_invalid"
    assert route["profit_visible"] is False
    assert route["route_eligible"] is False


def test_known_edge_holm_survivor_shape_remains_synthetic_non_claimable() -> None:
    evaluator = load_stat_module()

    result = evaluator.evaluate_fixture_family_statistics("known-edge")

    assert result["holm_fwer"]["status"] == "mtc_passed"
    assert result["adjusted_rows"][0]["mtc_passed"] is True
    assert result["statistical_route_status"] == "synthetic_edge_non_claimable"
    assert result["claim_boundary_status"] == "synthetic_edge_non_claimable"
    assert result["profit_visible"] is False
    assert result["route_eligible"] is False
    assert result["market_evidence"] is False
    assert result["claimable"] is False
    for key in (
        "paper_forward_eligible",
        "live_shadow_ready",
        "live_ready",
        "account_ready",
        "broker_ready",
        "network_ready",
        "credential_ready",
        "runtime_ready",
    ):
        assert result[key] is False


def test_holm_failure_modes_reject_denominator_index_alpha_method_target_invalid_p_and_padding() -> None:
    evaluator = load_stat_module()
    permutation = evaluator.evaluate_fixture_family_permutations("known-edge")
    rows = permutation["rows"]

    cases: list[tuple[str, list[dict[str, Any]], int, dict[str, Any], str]] = []
    cases.append(("denominator", rows, 2, {}, "sealed_denominator_mismatch"))
    mutated = copy.deepcopy(rows)
    mutated[0]["sealed_hypothesis_index"] = 2
    cases.append(("index", mutated, 1, {}, "sealed_index_mismatch"))
    mutated = copy.deepcopy(rows)
    mutated[0]["hypothesis_id"] = ""
    cases.append(("missing_id", mutated, 1, {}, "missing_hypothesis_id"))
    mutated = copy.deepcopy(rows)
    mutated.append(copy.deepcopy(mutated[0]))
    mutated[1]["sealed_hypothesis_index"] = 2
    cases.append(("duplicate_id", mutated, 2, {}, "duplicate_hypothesis_id"))
    mutated = copy.deepcopy(rows)
    mutated[0]["p_raw"] = math.nan
    cases.append(("nan", mutated, 1, {}, "invalid_p_raw"))
    mutated = copy.deepcopy(rows)
    mutated[0]["p_raw"] = 1.5
    cases.append(("range", mutated, 1, {}, "invalid_p_raw"))
    mutated = copy.deepcopy(rows)
    mutated[0]["p_value_provenance"]["p_value_source"] = "p_value_padding"
    cases.append(("padding", mutated, 1, {}, "p_value_padding_forbidden"))
    cases.append(("alpha", rows, 1, {"alpha": 0.10}, "alpha_mismatch"))
    cases.append(("method", rows, 1, {"method": "BH"}, "mtc_method_mismatch"))
    cases.append(
        ("target", rows, 1, {"error_rate_target": "FDR"}, "error_rate_target_mismatch")
    )

    for case_name, case_rows, denominator, overrides, expected_reason in cases:
        result = evaluator.apply_fixture_family_holm_fwer(
            case_rows,
            sealed_hypothesis_count=denominator,
            **overrides,
        )
        assert result["profit_visible"] is False, case_name
        assert result["reason"] == expected_reason, case_name
