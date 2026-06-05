"""Costed economic metric guards for Phase 167 synthetic fixtures."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import copy
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
METRIC_HELPER = ROOT / "scripts" / "profit_visibility_costed_metrics.py"


def load_metric_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "profit_visibility_costed_metrics", METRIC_HELPER
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_gross_trade_bps() -> list[float]:
    return [14.02, -3.98, 8.02, -9.98, 5.02]


def build_zero_loss_gross_trade_bps() -> list[float]:
    return [9.02, 10.02, 11.02]


def optional_applicable(bps: float) -> dict[str, object]:
    return {"status": "applicable", "bps": bps}


def optional_not_applicable(reason: str) -> dict[str, object]:
    return {
        "status": "not_applicable",
        "reason": reason,
        "covered_by_effective_cost": False,
    }


def build_cost_scenario(overrides: dict[str, object] | None = None) -> dict[str, object]:
    scenario: dict[str, object] = {
        "fees_bps": 1.0,
        "spread_bps": 0.5,
        "slippage_bps": 1.5,
        "turnover_multiplier": 1.2,
        "financing_bps": optional_applicable(0.25),
        "borrow_bps": optional_not_applicable("unborrowed_synthetic_fixture"),
        "conversion_bps": optional_not_applicable("single_currency_fixture"),
        "market_access_bps": optional_applicable(0.10),
    }
    if overrides:
        scenario.update(overrides)
    return scenario


def build_cost_model(overrides: dict[str, object] | None = None) -> dict[str, object]:
    model: dict[str, object] = {
        "declared_before_evaluation": True,
        "effective_cost_rationale": {
            "status": "explicit_nonzero_synthetic_costs",
            "notes": "fixture-only costs declared before evaluation",
        },
        "capacity": {
            "capacity_notional_bound": 250000.0,
            "measurement": "synthetic_fixture_not_market",
        },
        "base": build_cost_scenario(),
        "adverse": build_cost_scenario(
            {
                "fees_bps": 1.5,
                "spread_bps": 1.0,
                "slippage_bps": 3.0,
            }
        ),
    }
    if overrides:
        model.update(overrides)
    return model


def assert_no_metric_output(result: dict[str, Any]) -> None:
    forbidden = {
        "base_metrics",
        "adverse_metrics",
        "metrics",
        "net_profit_factor",
        "net_expectancy",
        "max_drawdown",
    }
    assert forbidden.isdisjoint(result)


def assert_no_fixture_metric_or_boundary_output(result: dict[str, Any]) -> None:
    assert_no_metric_output(result)
    assert "claim_boundary_status" not in result
    assert "claim_boundary" not in result


def assert_invalid_without_metric_output(result: dict[str, Any]) -> None:
    assert result["status"] in {"input_invalid", "cost_model_invalid"}
    assert result["profit_visible"] is False
    assert result["route_eligible"] is False
    assert isinstance(result["reason"], str) and result["reason"]
    assert_no_metric_output(result)


def test_costed_metric_arithmetic_from_gross_bps_returns() -> None:
    metrics = load_metric_module()

    result = metrics.evaluate_costed_metrics(build_gross_trade_bps(), build_cost_model())

    assert result["status"] == "costed_metrics_evaluated"
    assert result["profit_visible"] is False
    assert result["route_eligible"] is False
    assert result["cost_model_fingerprint"].startswith("sha256:")
    assert len(result["cost_model_fingerprint"]) == 71
    base = result["base_metrics"]
    assert base["total_round_trip_cost_bps"] == 4.02
    assert base["net_trade_bps"] == [10.0, -8.0, 4.0, -14.0, 1.0]
    assert base["net_expectancy"] == -1.4
    assert base["max_drawdown"] == -18.0
    assert base["turnover"] == 6.0
    assert base["trade_count"] == 5
    assert base["capacity_notional_bound"] == 250000.0
    assert base["slippage_sensitivity"] == 3.0
    assert base["net_profit_factor"] == 15.0 / 22.0
    metrics.assert_json_safe(result)
    json.dumps(result, allow_nan=False)


def test_profit_factor_zero_losses_is_typed_null_not_infinity() -> None:
    metrics = load_metric_module()

    result = metrics.evaluate_costed_metrics(
        build_zero_loss_gross_trade_bps(), build_cost_model()
    )

    base = result["base_metrics"]
    assert base["net_trade_bps"] == [5.0, 6.0, 7.0]
    assert base["net_profit_factor"] is None
    assert (
        base["net_profit_factor_unavailable_reason"]
        == "no_net_losses_profit_factor_degenerate"
    )
    assert result["degenerate_metric_reasons"] == {
        "base_metrics.net_profit_factor": "no_net_losses_profit_factor_degenerate",
        "adverse_metrics.net_profit_factor": "no_net_losses_profit_factor_degenerate",
    }
    assert result["route_eligible"] is False
    metrics.assert_json_safe(result)
    assert "Infinity" not in json.dumps(result, allow_nan=False)


def test_cost_fingerprint_is_sha256_of_canonical_cost_payload() -> None:
    metrics = load_metric_module()
    cost_model = build_cost_model()

    canonical = metrics.canonicalize_cost_model(cost_model)
    serialized = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    expected = "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    assert metrics.derive_cost_model_fingerprint(cost_model) == expected
    assert metrics.derive_cost_model_fingerprint(cost_model) == expected
    assert expected.removeprefix("sha256:").islower()


def test_cost_fingerprint_excludes_return_fixtures() -> None:
    metrics = load_metric_module()
    cost_model = build_cost_model(
        {
            "gross_trade_bps": [100.0, -100.0],
            "fixture_id": "known-noise-v1",
            "source_refs": ["fixtures/profit_visibility/synthetic.json"],
            "data_provenance": "synthetic_known_noise_not_market",
        }
    )

    first = metrics.derive_cost_model_fingerprint(cost_model)
    cost_model["gross_trade_bps"] = [-1.0, 2.0, 3.0]
    cost_model["fixture_id"] = "known-edge-v2"
    cost_model["source_refs"] = ["changed"]
    cost_model["data_provenance"] = "synthetic_known_edge_not_market"
    second = metrics.derive_cost_model_fingerprint(cost_model)

    assert first == second
    cost_model["base"] = build_cost_scenario({"fees_bps": 1.25})
    assert metrics.derive_cost_model_fingerprint(cost_model) != first


def test_adverse_ladder_uses_same_returns_and_monotonically_worsens_metrics() -> None:
    metrics = load_metric_module()

    result = metrics.evaluate_costed_metrics(build_gross_trade_bps(), build_cost_model())

    base = result["base_metrics"]
    adverse = result["adverse_metrics"]
    assert adverse["trade_count"] == base["trade_count"]
    assert adverse["turnover"] == base["turnover"]
    assert adverse["total_round_trip_cost_bps"] == 7.02
    assert adverse["net_trade_bps"] == [7.0, -11.0, 1.0, -17.0, -2.0]
    assert adverse["net_expectancy"] < base["net_expectancy"]
    assert adverse["net_profit_factor"] < base["net_profit_factor"]
    assert adverse["max_drawdown"] < base["max_drawdown"]
    assert result["cost_sensitivity"] == {
        "base_total_round_trip_cost_bps": 4.02,
        "adverse_total_round_trip_cost_bps": 7.02,
        "adverse_incremental_cost_bps": 3.0,
    }


def test_adverse_ladder_rejects_non_worsening_or_cheaper_costs() -> None:
    metrics = load_metric_module()
    invalid_models = [
        build_cost_model({"adverse": build_cost_scenario()}),
        build_cost_model(
            {
                "adverse": build_cost_scenario(
                    {"fees_bps": 0.75, "spread_bps": 1.0, "slippage_bps": 3.0}
                )
            }
        ),
        build_cost_model(
            {
                "adverse": build_cost_scenario(
                    {
                        "fees_bps": 1.5,
                        "spread_bps": 1.0,
                        "slippage_bps": 3.0,
                        "turnover_multiplier": 1.0,
                    }
                )
            }
        ),
    ]

    for cost_model in invalid_models:
        result = metrics.evaluate_costed_metrics(build_gross_trade_bps(), cost_model)

        assert result["status"] == "cost_model_invalid"
        assert_invalid_without_metric_output(result)


def test_invalid_cost_payloads_fail_before_metrics() -> None:
    metrics = load_metric_module()
    invalid_models = [
        {},
        build_cost_model({"declared_before_evaluation": False}),
        build_cost_model({"relaxed_from": "base"}),
        build_cost_model({"effective_cost_rationale": "post_hoc"}),
        build_cost_model({"base": build_cost_scenario({"fees_bps": 0})}),
        build_cost_model({"base": build_cost_scenario({"spread_bps": -0.1})}),
        build_cost_model({"base": build_cost_scenario({"slippage_bps": ""})}),
        build_cost_model({"base": build_cost_scenario({"turnover_multiplier": "unknown"})}),
        build_cost_model({"base": build_cost_scenario({"fees_bps": math.inf})}),
        build_cost_model({"base": build_cost_scenario({"fees_bps": math.nan})}),
        build_cost_model({"base": build_cost_scenario({"financing_bps": {"status": "unknown"}})}),
        build_cost_model({"base": build_cost_scenario({"borrow_bps": "TBD"})}),
        build_cost_model(
            {
                "base": build_cost_scenario(
                    {
                        "conversion_bps": {
                            "status": "not_applicable",
                            "reason": "",
                            "covered_by_effective_cost": False,
                        }
                    }
                )
            }
        ),
        build_cost_model(
            {
                "base": build_cost_scenario(
                    {
                        "market_access_bps": {
                            "status": "not_applicable",
                            "reason": "covered elsewhere",
                            "covered_by_effective_cost": True,
                        }
                    }
                )
            }
        ),
    ]

    for cost_model in invalid_models:
        result = metrics.evaluate_costed_metrics(build_gross_trade_bps(), cost_model)
        assert_invalid_without_metric_output(result)


def test_hyphenated_and_nested_post_hoc_markers_fail_before_metrics() -> None:
    metrics = load_metric_module()
    invalid_models = [
        build_cost_model({"post-hoc": "base"}),
        build_cost_model({"effective_cost_rationale": "Post-Hoc"}),
        build_cost_model(
            {
                "effective_cost_rationale": {
                    "status": "explicit_nonzero_synthetic_costs",
                    "audit": [{"method": "tuned-after-result"}],
                }
            }
        ),
    ]

    for cost_model in invalid_models:
        result = metrics.evaluate_costed_metrics(build_gross_trade_bps(), cost_model)

        assert result["status"] == "cost_model_invalid"
        assert result["reason"] == "post_hoc_cost_relaxation_rejected"
        assert_invalid_without_metric_output(result)


def test_missing_return_series_fails_before_metrics() -> None:
    metrics = load_metric_module()
    invalid_returns = [
        None,
        [],
        ["bad"],
        [1.0, math.nan],
        [1.0, math.inf],
        [1.0, -math.inf],
    ]

    for gross_trade_bps in invalid_returns:
        result = metrics.evaluate_costed_metrics(gross_trade_bps, build_cost_model())
        assert result["status"] == "input_invalid"
        assert_invalid_without_metric_output(result)


def test_cli_fixture_replay_outputs_deterministic_json() -> None:
    first = subprocess.run(
        [sys.executable, str(METRIC_HELPER), "--fixture", "all"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, str(METRIC_HELPER), "--fixture", "all"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert first.returncode == 0
    assert second.returncode == 0
    assert first.stdout == second.stdout
    assert "NaN" not in first.stdout
    assert "Infinity" not in first.stdout
    assert "-Infinity" not in first.stdout
    payload = json.loads(first.stdout)
    assert payload["status"] == "fixture_evaluation_complete"
    assert set(payload["fixtures"]) == {"known-noise", "known-edge", "invalid-cost"}
    for fixture_id, result in payload["fixtures"].items():
        assert result["fixture_id"] == fixture_id


def test_known_edge_metrics_remain_synthetic_non_claimable() -> None:
    metrics = load_metric_module()

    result = metrics.evaluate_builtin_fixture("known-edge")

    assert result["status"] == "costed_metrics_evaluated"
    assert result["fixture_id"] == "known-edge"
    assert result["data_provenance"] == "synthetic_known_edge_not_market"
    assert result["base_metrics"]["net_expectancy"] > 0
    assert result["base_metrics"]["net_profit_factor"] is not None
    assert result["base_metrics"]["net_profit_factor"] > 0
    assert result["claim_boundary_status"] == "synthetic_edge_non_claimable"
    assert result["claim_boundary"]["claim_boundary_status"] == (
        "synthetic_edge_non_claimable"
    )
    assert result["profit_visible"] is False
    assert result["route_eligible"] is False
    assert result["market_evidence"] is False
    assert result["claimable"] is False


def test_invalid_cost_fixture_outputs_cost_model_invalid_without_metric_rows() -> None:
    metrics = load_metric_module()

    result = metrics.evaluate_builtin_fixture("invalid-cost")

    assert result["status"] == "cost_model_invalid"
    assert result["fixture_id"] == "invalid-cost"
    assert_invalid_without_metric_output(result)


def test_fixture_input_provenance_failures_emit_input_invalid_without_metrics() -> None:
    metrics = load_metric_module()
    base_payload = metrics.builtin_fixture_payload("known-noise")
    cases = [
        ("missing_gross_trade_bps", {"gross_trade_bps": None}),
        ("missing_data_provenance", {"data_provenance": None}),
        (
            "unsupported_data_provenance",
            {"data_provenance": "market_data_claimed"},
        ),
    ]

    for expected_reason, mutation in cases:
        payload = copy.deepcopy(base_payload)
        for field, value in mutation.items():
            if value is None:
                payload.pop(field, None)
            else:
                payload[field] = value

        result = metrics.evaluate_fixture_payload(payload)

        assert result["status"] == "input_invalid"
        assert result["reason"] == expected_reason
        assert result["profit_visible"] is False
        assert result["route_eligible"] is False
        assert_no_fixture_metric_or_boundary_output(result)


def test_fixture_missing_cost_model_fails_closed_without_metrics() -> None:
    metrics = load_metric_module()
    payload = metrics.builtin_fixture_payload("known-noise")
    payload.pop("cost_model")

    result = metrics.evaluate_fixture_payload(payload)

    assert result["status"] == "cost_model_invalid"
    assert result["reason"] == "cost_model_missing_or_not_object"
    assert result["fixture_id"] == "known-noise"
    assert result["profit_visible"] is False
    assert result["route_eligible"] is False
    assert_no_fixture_metric_or_boundary_output(result)
