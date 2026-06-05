"""Evaluate Phase 167 synthetic costed economic metric fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from typing import Any

try:
    from scripts.validate_profit_visibility_report import classify_candidate_claim_boundary
except ModuleNotFoundError:  # pragma: no cover - script execution path fallback
    from validate_profit_visibility_report import classify_candidate_claim_boundary


CORE_COST_FIELDS = ("fees_bps", "spread_bps", "slippage_bps", "turnover_multiplier")
OPTIONAL_COST_FIELDS = (
    "financing_bps",
    "borrow_bps",
    "conversion_bps",
    "market_access_bps",
)
POST_HOC_COST_RELAXATION_KEYS = (
    "relaxed_from",
    "post_hoc",
    "after_result",
    "tuned_after_result",
)
POST_HOC_RELAXATION_VALUES = (
    "relaxed_from",
    "post_hoc",
    "after_result",
    "tuned_after_result",
)
BUILTIN_FIXTURE_NAMES = ("known-noise", "known-edge", "invalid-cost")
SUPPORTED_SYNTHETIC_DATA_PROVENANCE = {
    "synthetic_fixture_not_market",
    "synthetic_known_noise_not_market",
    "synthetic_known_edge_not_market",
}
SYNTHETIC_FIXTURE_NON_CLAIMABLE = "synthetic_fixture_non_claimable"
SYNTHETIC_EDGE_NON_CLAIMABLE = "synthetic_edge_non_claimable"
REPLAYABLE_FIXTURE_STATUSES = frozenset(
    {"costed_metrics_evaluated", "cost_model_invalid", "input_invalid"}
)
_INVALID_COST_STRINGS = {"", "0", "0.0", "blank", "missing", "unknown", "tbd", "tba"}
_ROUND_DIGITS = 10


def _fail_closed(status: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "status": status,
        "profit_visible": False,
        "route_eligible": False,
        "reason": reason,
        **details,
    }


def _round(value: float) -> float:
    rounded = round(value, _ROUND_DIGITS)
    return 0.0 if rounded == -0.0 else rounded


def _is_positive_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and float(value) > 0.0


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _nonblank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalized_string(value: str) -> str:
    return value.strip().lower()


def _normalized_marker(value: str) -> str:
    return _normalized_string(value).replace("-", "_")


def _post_hoc_violation(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _normalized_marker(str(key)) in POST_HOC_COST_RELAXATION_KEYS:
                return f"post_hoc_key:{key}"
            reason = _post_hoc_violation(nested)
            if reason is not None:
                return reason
    elif isinstance(value, list):
        for item in value:
            reason = _post_hoc_violation(item)
            if reason is not None:
                return reason
    elif isinstance(value, str) and _normalized_marker(value) in POST_HOC_RELAXATION_VALUES:
        return f"post_hoc_value:{value.strip()}"
    return None


def _validate_optional_cost(value: Any, path: str) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(value, dict):
        return None, f"{path}_malformed"

    status = value.get("status")
    if status == "applicable":
        bps = value.get("bps")
        if not _is_positive_finite_number(bps):
            return None, f"{path}_applicable_bps_invalid"
        return {"status": "applicable", "bps": float(bps)}, None

    if status == "not_applicable":
        reason = value.get("reason")
        if not _nonblank_string(reason):
            return None, f"{path}_reason_missing"
        if value.get("covered_by_effective_cost") is not False:
            return None, f"{path}_covered_by_effective_cost_not_false"
        return {
            "status": "not_applicable",
            "reason": str(reason).strip(),
            "covered_by_effective_cost": False,
        }, None

    return None, f"{path}_status_invalid"


def _canonicalize_cost_scenario(
    scenario: Any, scenario_name: str
) -> tuple[dict[str, Any] | None, list[str]]:
    invalid_fields: list[str] = []
    if not isinstance(scenario, dict):
        return None, [scenario_name]

    canonical: dict[str, Any] = {}
    for field in CORE_COST_FIELDS:
        value = scenario.get(field)
        if isinstance(value, str) and _normalized_string(value) in _INVALID_COST_STRINGS:
            invalid_fields.append(f"{scenario_name}.{field}")
            continue
        if not _is_positive_finite_number(value):
            invalid_fields.append(f"{scenario_name}.{field}")
            continue
        canonical[field] = float(value)

    for field in OPTIONAL_COST_FIELDS:
        optional_value = scenario.get(field)
        optional, reason = _validate_optional_cost(
            optional_value, f"{scenario_name}.{field}"
        )
        if reason is not None:
            invalid_fields.append(reason)
            continue
        canonical[field] = optional

    if invalid_fields:
        return None, invalid_fields
    return canonical, []


def _canonical_cost_payload_or_invalid(
    cost_model: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(cost_model, dict) or not cost_model:
        return None, _fail_closed(
            "cost_model_invalid",
            "cost_model_missing_or_not_object",
            invalid_fields=["cost_model"],
        )

    post_hoc_reason = _post_hoc_violation(cost_model)
    if post_hoc_reason is not None:
        return None, _fail_closed(
            "cost_model_invalid",
            "post_hoc_cost_relaxation_rejected",
            invalid_fields=[post_hoc_reason],
        )

    invalid_fields: list[str] = []
    if cost_model.get("declared_before_evaluation") is not True:
        invalid_fields.append("declared_before_evaluation")

    effective_cost_rationale = cost_model.get("effective_cost_rationale")
    if not isinstance(effective_cost_rationale, (dict, str)):
        invalid_fields.append("effective_cost_rationale")
    elif isinstance(effective_cost_rationale, str) and not effective_cost_rationale.strip():
        invalid_fields.append("effective_cost_rationale")
    elif isinstance(effective_cost_rationale, dict) and not effective_cost_rationale:
        invalid_fields.append("effective_cost_rationale")

    capacity = cost_model.get("capacity")
    if not isinstance(capacity, dict):
        invalid_fields.append("capacity")
    else:
        bound = capacity.get("capacity_notional_bound")
        if not _is_positive_finite_number(bound):
            invalid_fields.append("capacity.capacity_notional_bound")
        measurement = capacity.get("measurement")
        if not _nonblank_string(measurement):
            invalid_fields.append("capacity.measurement")

    base, base_invalid = _canonicalize_cost_scenario(cost_model.get("base"), "base")
    adverse, adverse_invalid = _canonicalize_cost_scenario(
        cost_model.get("adverse"), "adverse"
    )
    invalid_fields.extend(base_invalid)
    invalid_fields.extend(adverse_invalid)
    if base is not None and adverse is not None:
        for field in CORE_COST_FIELDS:
            if float(adverse[field]) < float(base[field]):
                invalid_fields.append(f"adverse.{field}_less_than_base")
        if compute_total_round_trip_cost_bps(
            adverse
        ) <= compute_total_round_trip_cost_bps(base):
            invalid_fields.append("adverse.total_round_trip_cost_not_worse_than_base")

    if invalid_fields:
        return None, _fail_closed(
            "cost_model_invalid",
            "cost_model_fields_invalid",
            invalid_fields=invalid_fields,
        )

    assert base is not None
    assert adverse is not None
    assert isinstance(capacity, dict)
    canonical = {
        "declared_before_evaluation": True,
        "effective_cost_rationale": effective_cost_rationale,
        "capacity": {
            "capacity_notional_bound": float(capacity["capacity_notional_bound"]),
            "measurement": str(capacity["measurement"]).strip(),
        },
        "base": base,
        "adverse": adverse,
    }
    assert_json_safe(canonical)
    return canonical, None


def validate_cost_model(cost_model: dict[str, Any]) -> dict[str, Any]:
    canonical, invalid = _canonical_cost_payload_or_invalid(cost_model)
    if invalid is not None:
        return invalid
    return {
        "status": "cost_model_valid",
        "profit_visible": False,
        "route_eligible": False,
        "canonical_cost_payload": canonical,
    }


def canonicalize_cost_model(cost_model: dict[str, Any]) -> dict[str, Any]:
    canonical, invalid = _canonical_cost_payload_or_invalid(cost_model)
    if invalid is not None:
        raise ValueError(invalid["reason"])
    assert canonical is not None
    return canonical


def derive_cost_model_fingerprint(cost_model: dict[str, Any]) -> str:
    canonical = canonicalize_cost_model(cost_model)
    serialized = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_total_round_trip_cost_bps(cost_scenario: dict[str, Any]) -> float:
    applicable_optional_cost_bps = 0.0
    for field in OPTIONAL_COST_FIELDS:
        optional = cost_scenario[field]
        if optional["status"] == "applicable":
            applicable_optional_cost_bps += float(optional["bps"])
    total = (
        float(cost_scenario["fees_bps"])
        + float(cost_scenario["spread_bps"])
        + float(cost_scenario["slippage_bps"])
        + applicable_optional_cost_bps
    ) * float(cost_scenario["turnover_multiplier"])
    return _round(total)


def compute_net_trade_bps(
    gross_trade_bps: list[float], total_round_trip_cost_bps: float
) -> list[float]:
    return [_round(float(gross) - total_round_trip_cost_bps) for gross in gross_trade_bps]


def compute_max_drawdown(net_trade_bps: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade_bps in net_trade_bps:
        cumulative = _round(cumulative + trade_bps)
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, _round(cumulative - peak))
    return _round(max_drawdown)


def compute_net_profit_factor(net_trade_bps: list[float]) -> tuple[float | None, str | None]:
    positive_sum = sum(value for value in net_trade_bps if value > 0.0)
    negative_sum = sum(value for value in net_trade_bps if value < 0.0)
    if negative_sum == 0.0:
        return None, "no_net_losses_profit_factor_degenerate"
    return positive_sum / abs(negative_sum), None


def _validate_gross_trade_bps(gross_trade_bps: Any) -> tuple[list[float] | None, str]:
    if gross_trade_bps is None:
        return None, "missing_gross_trade_bps"
    if not isinstance(gross_trade_bps, list):
        return None, "gross_trade_bps_not_list"
    if not gross_trade_bps:
        return None, "empty_return_series"

    returns: list[float] = []
    for value in gross_trade_bps:
        if not _is_finite_number(value):
            return None, "return_series_non_finite"
        returns.append(float(value))
    return returns, ""


def _metrics_for_scenario(
    gross_trade_bps: list[float],
    cost_scenario: dict[str, Any],
    capacity_notional_bound: float,
    slippage_sensitivity: float,
) -> tuple[dict[str, Any], dict[str, str]]:
    total_cost = compute_total_round_trip_cost_bps(cost_scenario)
    net_trade_bps = compute_net_trade_bps(gross_trade_bps, total_cost)
    profit_factor, unavailable_reason = compute_net_profit_factor(net_trade_bps)
    metric = {
        "net_profit_factor": profit_factor,
        "net_expectancy": _round(sum(net_trade_bps) / len(net_trade_bps)),
        "max_drawdown": compute_max_drawdown(net_trade_bps),
        "turnover": _round(float(cost_scenario["turnover_multiplier"]) * len(net_trade_bps)),
        "trade_count": len(net_trade_bps),
        "capacity_notional_bound": capacity_notional_bound,
        "slippage_sensitivity": slippage_sensitivity,
        "total_round_trip_cost_bps": total_cost,
        "net_trade_bps": net_trade_bps,
    }
    reasons: dict[str, str] = {}
    if unavailable_reason is not None:
        metric["net_profit_factor_unavailable_reason"] = unavailable_reason
        reasons["net_profit_factor"] = unavailable_reason
    assert_json_safe(metric)
    return metric, reasons


def _claim_boundary_for_fixture(
    *,
    data_provenance: str,
    source_refs: list[str],
    cost_model_fingerprint: str,
) -> dict[str, Any]:
    boundary = classify_candidate_claim_boundary(
        {
            "data_provenance": data_provenance,
            "source_refs": source_refs,
            "cost_model_fingerprint": cost_model_fingerprint,
        }
    )
    assert_json_safe(boundary)
    return boundary


def evaluate_costed_metrics(
    gross_trade_bps: list[float], cost_model: Any
) -> dict[str, Any]:
    returns, return_reason = _validate_gross_trade_bps(gross_trade_bps)
    if returns is None:
        return _fail_closed("input_invalid", return_reason)

    canonical, invalid_cost = _canonical_cost_payload_or_invalid(cost_model)
    if invalid_cost is not None:
        return invalid_cost
    assert canonical is not None

    fingerprint = derive_cost_model_fingerprint(cost_model)
    base_total = compute_total_round_trip_cost_bps(canonical["base"])
    adverse_total = compute_total_round_trip_cost_bps(canonical["adverse"])
    incremental_cost = _round(adverse_total - base_total)
    capacity_bound = float(canonical["capacity"]["capacity_notional_bound"])
    base_metrics, base_reasons = _metrics_for_scenario(
        returns, canonical["base"], capacity_bound, incremental_cost
    )
    adverse_metrics, adverse_reasons = _metrics_for_scenario(
        returns, canonical["adverse"], capacity_bound, incremental_cost
    )
    degenerate_metric_reasons = {
        **{
            f"base_metrics.{key}": value
            for key, value in base_reasons.items()
        },
        **{
            f"adverse_metrics.{key}": value
            for key, value in adverse_reasons.items()
        },
    }
    result = {
        "status": "costed_metrics_evaluated",
        "profit_visible": False,
        "route_eligible": False,
        "reason": "synthetic_fixture_costed_metrics_non_claimable",
        "cost_model_fingerprint": fingerprint,
        "canonical_cost_payload": canonical,
        "base_metrics": base_metrics,
        "adverse_metrics": adverse_metrics,
        "cost_sensitivity": {
            "base_total_round_trip_cost_bps": base_total,
            "adverse_total_round_trip_cost_bps": adverse_total,
            "adverse_incremental_cost_bps": incremental_cost,
        },
        "capacity": canonical["capacity"],
        "degenerate_metric_reasons": degenerate_metric_reasons,
    }
    assert_json_safe(result)
    return result


def assert_json_safe(value: Any) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non_finite_json_value")
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("non_string_json_key")
            assert_json_safe(nested)
        return
    if isinstance(value, list):
        for nested in value:
            assert_json_safe(nested)
        return
    raise ValueError(f"unsupported_json_value:{type(value).__name__}")


def _base_cost_model() -> dict[str, Any]:
    return {
        "declared_before_evaluation": True,
        "effective_cost_rationale": {
            "status": "explicit_nonzero_synthetic_costs",
            "notes": "fixture-only costs declared before evaluation",
        },
        "capacity": {
            "capacity_notional_bound": 250000.0,
            "measurement": "synthetic_fixture_not_market",
        },
        "base": {
            "fees_bps": 1.0,
            "spread_bps": 0.5,
            "slippage_bps": 1.5,
            "turnover_multiplier": 1.2,
            "financing_bps": {"status": "applicable", "bps": 0.25},
            "borrow_bps": {
                "status": "not_applicable",
                "reason": "unborrowed_synthetic_fixture",
                "covered_by_effective_cost": False,
            },
            "conversion_bps": {
                "status": "not_applicable",
                "reason": "single_currency_fixture",
                "covered_by_effective_cost": False,
            },
            "market_access_bps": {"status": "applicable", "bps": 0.10},
        },
        "adverse": {
            "fees_bps": 1.5,
            "spread_bps": 1.0,
            "slippage_bps": 3.0,
            "turnover_multiplier": 1.2,
            "financing_bps": {"status": "applicable", "bps": 0.25},
            "borrow_bps": {
                "status": "not_applicable",
                "reason": "unborrowed_synthetic_fixture",
                "covered_by_effective_cost": False,
            },
            "conversion_bps": {
                "status": "not_applicable",
                "reason": "single_currency_fixture",
                "covered_by_effective_cost": False,
            },
            "market_access_bps": {"status": "applicable", "bps": 0.10},
        },
    }


def builtin_fixture_payload(name: str) -> dict[str, Any]:
    if name == "known-noise":
        return {
            "fixture_id": name,
            "data_provenance": "synthetic_known_noise_not_market",
            "source_refs": ["fixtures/profit_visibility/synthetic-known-noise.json"],
            "gross_trade_bps": [14.02, -3.98, 8.02, -9.98, 5.02],
            "cost_model": _base_cost_model(),
        }
    if name == "known-edge":
        return {
            "fixture_id": name,
            "data_provenance": "synthetic_known_edge_not_market",
            "source_refs": ["fixtures/profit_visibility/synthetic-known-edge.json"],
            "gross_trade_bps": [
                34.02,
                35.02,
                33.02,
                36.02,
                32.02,
                34.02,
                35.02,
                33.02,
                3.02,
                37.02,
            ],
            "cost_model": _base_cost_model(),
        }
    if name == "invalid-cost":
        payload = builtin_fixture_payload("known-noise")
        payload["fixture_id"] = name
        payload["data_provenance"] = "synthetic_fixture_not_market"
        payload["source_refs"] = ["fixtures/profit_visibility/synthetic-invalid-cost.json"]
        cost_model = _base_cost_model()
        cost_model["base"] = dict(cost_model["base"])
        cost_model["base"]["fees_bps"] = 0
        payload["cost_model"] = cost_model
        return payload
    raise ValueError(f"unknown fixture: {name}")


def _fixture_id_from_payload(payload: dict[str, Any]) -> Any:
    return payload.get("fixture_id", payload.get("fixture"))


def _validate_fixture_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    fixture_id = _fixture_id_from_payload(payload)
    returns, return_reason = _validate_gross_trade_bps(payload.get("gross_trade_bps"))
    if returns is None:
        return _fail_closed(
            "input_invalid",
            return_reason,
            invalid_fields=["gross_trade_bps"],
            fixture_id=fixture_id,
        )

    provenance = payload.get("data_provenance")
    if provenance is None:
        return _fail_closed(
            "input_invalid",
            "missing_data_provenance",
            invalid_fields=["data_provenance"],
            fixture_id=fixture_id,
        )
    if provenance not in SUPPORTED_SYNTHETIC_DATA_PROVENANCE:
        return _fail_closed(
            "input_invalid",
            "unsupported_data_provenance",
            invalid_fields=["data_provenance"],
            fixture_id=fixture_id,
        )
    source_refs = payload.get("source_refs")
    if not isinstance(source_refs, list) or not any(_nonblank_string(ref) for ref in source_refs):
        return _fail_closed(
            "input_invalid",
            "source_refs_missing",
            invalid_fields=["source_refs"],
            fixture_id=fixture_id,
        )
    return None


def evaluate_fixture_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _fail_closed("input_invalid", "payload_not_object")

    fixture_id = _fixture_id_from_payload(payload)
    invalid_fixture = _validate_fixture_payload(payload)
    if invalid_fixture is not None:
        return invalid_fixture

    data_provenance = payload["data_provenance"]
    source_refs = [
        str(ref).strip()
        for ref in payload["source_refs"]
        if _nonblank_string(ref)
    ]
    result = evaluate_costed_metrics(payload["gross_trade_bps"], payload.get("cost_model"))
    result["fixture_id"] = fixture_id
    result["data_provenance"] = data_provenance
    result["source_refs"] = source_refs
    result["profit_visible"] = False
    result["route_eligible"] = False

    if result["status"] != "costed_metrics_evaluated":
        assert_json_safe(result)
        return result

    boundary = _claim_boundary_for_fixture(
        data_provenance=data_provenance,
        source_refs=source_refs,
        cost_model_fingerprint=result["cost_model_fingerprint"],
    )
    result["claim_boundary"] = boundary
    for key, value in boundary.items():
        if key in {"status", "reason", "profit_visible"}:
            continue
        result[key] = value
    result["profit_visible"] = False
    result["route_eligible"] = False
    assert_json_safe(result)
    return result


def evaluate_builtin_fixture(name: str) -> dict[str, Any]:
    payload = builtin_fixture_payload(name)
    return evaluate_fixture_payload(payload)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Phase 167 synthetic costed metric fixtures."
    )
    parser.add_argument(
        "--fixture",
        choices=(*BUILTIN_FIXTURE_NAMES, "all"),
        default="known-noise",
        help="Fixture to evaluate.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print deterministic JSON output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.fixture == "all":
        results = {
            name: evaluate_builtin_fixture(name) for name in BUILTIN_FIXTURE_NAMES
        }
        output: dict[str, Any] = {
            "status": "fixture_evaluation_complete",
            "fixtures": results,
        }
    else:
        output = evaluate_builtin_fixture(args.fixture)

    assert_json_safe(output)
    print(
        json.dumps(
            output,
            sort_keys=True,
            indent=2 if args.pretty else None,
            allow_nan=False,
        )
    )
    if args.fixture == "all":
        return (
            0
            if all(item["status"] in REPLAYABLE_FIXTURE_STATUSES for item in results.values())
            else 1
        )
    return 0 if output["status"] in REPLAYABLE_FIXTURE_STATUSES else 1


if __name__ == "__main__":
    raise SystemExit(main())
