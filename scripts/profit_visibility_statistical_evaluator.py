"""Evaluate Phase 168 fixture permutation and Holm/FWER statistics."""

from __future__ import annotations

import argparse
import json
import math
import random
from typing import Any

try:
    from scripts.profit_visibility_costed_metrics import (
        assert_json_safe,
        evaluate_builtin_fixture,
    )
    from scripts.validate_profit_visibility_report import apply_holm_fwer
except ModuleNotFoundError:  # pragma: no cover - script execution path fallback
    from profit_visibility_costed_metrics import assert_json_safe, evaluate_builtin_fixture
    from validate_profit_visibility_report import apply_holm_fwer


DEFAULT_PERMUTATION_B = 999
DEFAULT_PERMUTATION_SEED = 168
DEFAULT_TEST_STATISTIC = "net_expectancy"
DEFAULT_NULL_METHOD = "seed_controlled_sign_flip"
DEFAULT_P_VALUE_METHOD = "one_sided_positive_plus_one"
DEFAULT_HOLM_METHOD = "FWER/Holm"
DEFAULT_ERROR_RATE_TARGET = "FWER"
DEFAULT_ALPHA = 0.05
KNOWN_NOISE_ROUTE = "honest_null_ship"
KNOWN_EDGE_ROUTE = "synthetic_edge_non_claimable"
SUPPORTED_FIXTURES = ("known-noise", "known-edge")
SEALED_PROTOCOL_REF_PREFIX = "phase168-fixture-protocol"
SUPPORTED_EVALUATION_RUN_REF_PREFIX = "phase168-fixture-statistical-run"


def _fail_closed(status: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "status": status,
        "profit_visible": False,
        "route_eligible": False,
        "reason": reason,
        **details,
    }


def _round(value: float) -> float:
    rounded = round(value, 10)
    return 0.0 if rounded == -0.0 else rounded


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _is_nonblank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_b_seed_statistic(B: int, seed: int, test_statistic: str) -> dict[str, Any] | None:
    if isinstance(B, bool) or not isinstance(B, int) or B <= 0:
        return _fail_closed("statistical_input_invalid", "invalid_permutation_B")
    if isinstance(seed, bool) or not isinstance(seed, int):
        return _fail_closed("statistical_input_invalid", "invalid_permutation_seed")
    if test_statistic != DEFAULT_TEST_STATISTIC:
        return _fail_closed("statistical_input_invalid", "unsupported_test_statistic")
    return None


def _validate_net_trade_bps(net_trade_bps: Any) -> tuple[list[float] | None, dict[str, Any] | None]:
    if not isinstance(net_trade_bps, list):
        return None, _fail_closed("statistical_input_invalid", "net_trade_bps_not_list")
    if not net_trade_bps:
        return None, _fail_closed("statistical_input_invalid", "empty_net_trade_bps")
    values: list[float] = []
    for value in net_trade_bps:
        if not _is_finite_number(value):
            return None, _fail_closed("statistical_input_invalid", "net_trade_bps_non_finite")
        values.append(float(value))
    return values, None


def _mean(values: list[float]) -> float:
    return _round(sum(values) / len(values))


def _random_sign_draws(width: int, B: int, seed: int) -> list[list[int]]:
    rng = random.Random(seed)
    return [[1 if rng.random() < 0.5 else -1 for _ in range(width)] for _ in range(B)]


def permutation_p_raw(
    net_trade_bps: list[float],
    *,
    B: int = DEFAULT_PERMUTATION_B,
    seed: int = DEFAULT_PERMUTATION_SEED,
    test_statistic: str = DEFAULT_TEST_STATISTIC,
    sign_draws: list[list[int]] | None = None,
) -> dict[str, Any]:
    invalid = _validate_b_seed_statistic(B, seed, test_statistic)
    if invalid is not None:
        return invalid
    values, invalid_values = _validate_net_trade_bps(net_trade_bps)
    if invalid_values is not None:
        return invalid_values
    assert values is not None

    draws = sign_draws if sign_draws is not None else _random_sign_draws(len(values), B, seed)
    if len(draws) != B or any(len(draw) != len(values) for draw in draws):
        return _fail_closed("statistical_input_invalid", "sign_draw_shape_mismatch")

    observed = _mean(values)
    null_extreme_count = 0
    for draw in draws:
        if any(sign not in {-1, 1} for sign in draw):
            return _fail_closed("statistical_input_invalid", "sign_draw_value_invalid")
        null_stat = _mean([value * sign for value, sign in zip(values, draw, strict=True)])
        if null_stat >= observed:
            null_extreme_count += 1

    p_raw = (null_extreme_count + 1) / (B + 1)
    result = {
        "status": "permutation_evaluated",
        "profit_visible": False,
        "route_eligible": False,
        "B": B,
        "seed": seed,
        "test_statistic": test_statistic,
        "null_method": DEFAULT_NULL_METHOD,
        "p_value_method": DEFAULT_P_VALUE_METHOD,
        "observed_stat": observed,
        "null_extreme_count": null_extreme_count,
        "p_raw": p_raw,
        "trade_count": len(values),
    }
    assert_json_safe(result)
    return result


def _sealed_protocol_ref(fixture_id: str) -> str:
    return f"{SEALED_PROTOCOL_REF_PREFIX}:{fixture_id}"


def _supported_evaluation_run_ref(fixture_id: str, B: int, seed: int) -> str:
    return f"{SUPPORTED_EVALUATION_RUN_REF_PREFIX}:{fixture_id}:B{B}:seed{seed}"


def _hypothesis_id(fixture_id: str) -> str:
    return f"phase168.{fixture_id}.h001"


def _candidate_row_order(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("hypothesis_id")) for row in rows]


def evaluate_candidate_permutation(
    candidate: dict[str, Any],
    *,
    B: int = DEFAULT_PERMUTATION_B,
    seed: int = DEFAULT_PERMUTATION_SEED,
    sealed_hypothesis_index: int,
    sealed_hypothesis_count: int,
    supported_evaluation_run_ref: str,
    sealed_protocol_ref: str,
) -> dict[str, Any]:
    if candidate.get("status") != "costed_metrics_evaluated":
        return _fail_closed(
            "statistical_input_invalid",
            "costed_metrics_missing_or_invalid",
            source_costed_status=candidate.get("status"),
            fixture_id=candidate.get("fixture_id"),
        )
    base_metrics = candidate.get("base_metrics")
    if not isinstance(base_metrics, dict):
        return _fail_closed("statistical_input_invalid", "base_metrics_missing")
    permutation = permutation_p_raw(
        base_metrics.get("net_trade_bps"),
        B=B,
        seed=seed,
    )
    if permutation["status"] != "permutation_evaluated":
        return permutation

    fixture_id = str(candidate.get("fixture_id"))
    hypothesis_id = _hypothesis_id(fixture_id)
    row = {
        "family_id": f"phase168.{fixture_id}",
        "hypothesis_id": hypothesis_id,
        "sealed_hypothesis_index": sealed_hypothesis_index,
        "sealed_hypothesis_count": sealed_hypothesis_count,
        "fixture_id": fixture_id,
        "source_costed_fixture_id": fixture_id,
        "data_provenance": candidate.get("data_provenance"),
        "source_refs": candidate.get("source_refs"),
        "cost_model_fingerprint": candidate.get("cost_model_fingerprint"),
        "net_expectancy": base_metrics.get("net_expectancy"),
        "net_profit_factor": base_metrics.get("net_profit_factor"),
        "max_drawdown": base_metrics.get("max_drawdown"),
        "turnover": base_metrics.get("turnover"),
        "trade_count": base_metrics.get("trade_count"),
        "capacity_notional_bound": base_metrics.get("capacity_notional_bound"),
        "slippage_sensitivity": base_metrics.get("slippage_sensitivity"),
        "registration_anchor_ref": sealed_protocol_ref,
        "sealed_protocol_ref": sealed_protocol_ref,
        "supported_evaluation_run_ref": supported_evaluation_run_ref,
        "B": permutation["B"],
        "seed": permutation["seed"],
        "test_statistic": permutation["test_statistic"],
        "null_method": permutation["null_method"],
        "p_value_method": permutation["p_value_method"],
        "observed_stat": permutation["observed_stat"],
        "null_extreme_count": permutation["null_extreme_count"],
        "p_raw": permutation["p_raw"],
        "p_value_provenance": {
            "sealed_protocol_ref": sealed_protocol_ref,
            "hypothesis_id": hypothesis_id,
            "supported_evaluation_run_ref": supported_evaluation_run_ref,
            "p_value_source": "phase168_seeded_sign_flip_permutation",
        },
        "claim_boundary_status": candidate.get("claim_boundary_status"),
        "claim_boundary": candidate.get("claim_boundary"),
        "profit_visible": False,
        "route_eligible": False,
    }
    row["replay_metadata"] = _row_replay_metadata(row)
    assert_json_safe(row)
    return row


def _row_replay_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "B": row.get("B"),
        "seed": row.get("seed"),
        "test_statistic": row.get("test_statistic"),
        "null_method": row.get("null_method"),
        "p_value_method": row.get("p_value_method"),
        "observed_stat": row.get("observed_stat"),
        "null_extreme_count": row.get("null_extreme_count"),
        "p_raw": row.get("p_raw"),
        "sealed_denominator": row.get("sealed_hypothesis_count"),
        "sealed_hypothesis_index": row.get("sealed_hypothesis_index"),
        "hypothesis_id": row.get("hypothesis_id"),
        "sealed_protocol_ref": row.get("sealed_protocol_ref"),
        "supported_evaluation_run_ref": row.get("supported_evaluation_run_ref"),
        "p_value_provenance": row.get("p_value_provenance"),
        "fixture_id": row.get("fixture_id"),
        "data_provenance": row.get("data_provenance"),
        "source_refs": row.get("source_refs"),
        "cost_model_fingerprint": row.get("cost_model_fingerprint"),
    }
    assert_json_safe(metadata)
    return metadata


def builtin_statistical_fixture_family(name: str) -> dict[str, Any]:
    if name not in SUPPORTED_FIXTURES:
        raise ValueError(f"unknown statistical fixture: {name}")
    fixture = evaluate_builtin_fixture(name)
    return {
        "family_id": f"phase168.{name}",
        "fixture_id": name,
        "sealed_hypothesis_count": 1,
        "candidates": [fixture],
    }


def evaluate_fixture_family_permutations(
    fixture: str,
    *,
    B: int = DEFAULT_PERMUTATION_B,
    seed: int = DEFAULT_PERMUTATION_SEED,
) -> dict[str, Any]:
    family = builtin_statistical_fixture_family(fixture)
    sealed_count = family["sealed_hypothesis_count"]
    sealed_protocol_ref = _sealed_protocol_ref(fixture)
    supported_run_ref = _supported_evaluation_run_ref(fixture, B, seed)
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(family["candidates"], start=1):
        row = evaluate_candidate_permutation(
            candidate,
            B=B,
            seed=seed,
            sealed_hypothesis_index=index,
            sealed_hypothesis_count=sealed_count,
            supported_evaluation_run_ref=supported_run_ref,
            sealed_protocol_ref=sealed_protocol_ref,
        )
        if row.get("status") not in {None, "permutation_evaluated"} and "p_raw" not in row:
            return row
        rows.append(row)
    result = {
        "status": "permutation_family_evaluated",
        "fixture_id": fixture,
        "family_id": family["family_id"],
        "B": B,
        "seed": seed,
        "test_statistic": DEFAULT_TEST_STATISTIC,
        "null_method": DEFAULT_NULL_METHOD,
        "p_value_method": DEFAULT_P_VALUE_METHOD,
        "sealed_hypothesis_count": sealed_count,
        "sealed_protocol_ref": sealed_protocol_ref,
        "supported_evaluation_run_ref": supported_run_ref,
        "canonical_input_order": _candidate_row_order(rows),
        "rows": rows,
        "profit_visible": False,
        "route_eligible": False,
    }
    result["replay_metadata"] = replay_metadata_for_family(rows)
    assert_json_safe(result)
    return result


def replay_metadata_for_family(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = {
        "row_count": len(rows),
        "canonical_input_order": _candidate_row_order(rows),
        "rows": [_row_replay_metadata(row) for row in rows],
    }
    assert_json_safe(metadata)
    return metadata


def validate_replay_metadata(
    result: dict[str, Any], expected: dict[str, Any]
) -> dict[str, Any]:
    actual = result.get("replay_metadata")
    if actual != expected:
        return _fail_closed("replay_metadata_invalid", "replay_metadata_drift")
    return {
        "status": "replay_metadata_valid",
        "profit_visible": False,
        "route_eligible": False,
    }


def _precheck_holm_rows(rows: Any, sealed_hypothesis_count: int) -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return _fail_closed("invalid_disqualified", "rows_not_list")
    if len(rows) != sealed_hypothesis_count:
        return _fail_closed(
            "profit_visible_false",
            "sealed_denominator_mismatch",
            sealed_denominator=sealed_hypothesis_count,
            input_count=len(rows),
        )
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            return _fail_closed("invalid_disqualified", "row_not_object")
        hypothesis_id = row.get("hypothesis_id")
        if not _is_nonblank_string(hypothesis_id):
            return _fail_closed("invalid_disqualified", "missing_hypothesis_id")
        if str(hypothesis_id) in seen:
            return _fail_closed("profit_visible_false", "duplicate_hypothesis_id")
        seen.add(str(hypothesis_id))
        p_raw = row.get("p_raw")
        if not _is_finite_number(p_raw) or float(p_raw) < 0.0 or float(p_raw) > 1.0:
            return _fail_closed("invalid_disqualified", "invalid_p_raw")
        provenance = row.get("p_value_provenance")
        if not isinstance(provenance, dict):
            return _fail_closed("invalid_disqualified", "missing_p_value_provenance")
        source = str(provenance.get("p_value_source") or provenance.get("source") or "")
        if source.strip().lower().replace("-", "_") == "p_value_padding":
            return _fail_closed("profit_visible_false", "p_value_padding_forbidden")
    indexes = [row.get("sealed_hypothesis_index") for row in rows]
    if sorted(indexes) != list(range(1, sealed_hypothesis_count + 1)):
        return _fail_closed("profit_visible_false", "sealed_index_mismatch")
    return None


def apply_fixture_family_holm_fwer(
    rows: list[dict[str, Any]],
    *,
    sealed_hypothesis_count: int,
    method: str = DEFAULT_HOLM_METHOD,
    error_rate_target: str = DEFAULT_ERROR_RATE_TARGET,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Any]:
    invalid = _precheck_holm_rows(rows, sealed_hypothesis_count)
    if invalid is not None:
        return invalid
    return apply_holm_fwer(
        rows,
        sealed_hypothesis_count=sealed_hypothesis_count,
        method=method,
        error_rate_target=error_rate_target,
        alpha=alpha,
    )


def derive_synthetic_statistical_route(
    fixture_id: str,
    permutation_result: dict[str, Any],
    holm_result: dict[str, Any],
) -> dict[str, Any]:
    rows = permutation_result.get("rows")
    if not isinstance(rows, list):
        return _fail_closed("statistical_route_invalid", "permutation_rows_missing")
    replay_result = validate_replay_metadata(
        permutation_result, replay_metadata_for_family(rows)
    )
    if replay_result["status"] != "replay_metadata_valid":
        return replay_result
    if permutation_result.get("status") != "permutation_family_evaluated":
        return _fail_closed("statistical_route_invalid", "permutation_missing")
    if holm_result.get("status") not in {"mtc_passed", "mtc_failed"}:
        return _fail_closed("statistical_route_invalid", "holm_fwer_missing_or_invalid")

    if fixture_id == "known-noise":
        if holm_result["status"] == "mtc_failed":
            return {
                "status": KNOWN_NOISE_ROUTE,
                "statistical_route_status": KNOWN_NOISE_ROUTE,
                "profit_visible": False,
                "route_eligible": False,
                "known_fixture_route_reason": "cost_permutation_holm_support_honest_null_ship",
            }
        return _fail_closed("statistical_route_invalid", "known_noise_holm_survivor")

    if fixture_id == "known-edge":
        return {
            "status": KNOWN_EDGE_ROUTE,
            "statistical_route_status": KNOWN_EDGE_ROUTE,
            "claim_boundary_status": KNOWN_EDGE_ROUTE,
            "profit_visible": False,
            "route_eligible": False,
            "market_evidence": False,
            "claimable": False,
            "paper_forward_eligible": False,
            "live_shadow_ready": False,
            "live_ready": False,
            "account_ready": False,
            "broker_ready": False,
            "network_ready": False,
            "credential_ready": False,
            "runtime_ready": False,
            "known_fixture_route_reason": "synthetic_edge_survivor_shape_non_claimable",
        }
    return _fail_closed("statistical_route_invalid", "unknown_fixture_route")


def validate_statistical_evaluation_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return _fail_closed("invalid_disqualified", "result_not_object")
    if result.get("profit_visible") is True or result.get("route_eligible") is True:
        return _fail_closed("profit_visible_false", "synthetic_route_claimability_drift")
    return {"status": "statistical_evaluation_valid", "profit_visible": False, "route_eligible": False}


def evaluate_fixture_family_statistics(
    fixture: str,
    *,
    B: int = DEFAULT_PERMUTATION_B,
    seed: int = DEFAULT_PERMUTATION_SEED,
) -> dict[str, Any]:
    permutation_result = evaluate_fixture_family_permutations(fixture, B=B, seed=seed)
    if permutation_result.get("status") != "permutation_family_evaluated":
        return permutation_result
    holm_result = apply_fixture_family_holm_fwer(
        permutation_result["rows"],
        sealed_hypothesis_count=permutation_result["sealed_hypothesis_count"],
    )
    route = derive_synthetic_statistical_route(fixture, permutation_result, holm_result)
    adjusted_rows = holm_result.get("rows", [])
    result = {
        **permutation_result,
        "status": "fixture_statistical_evaluation_complete",
        "holm_fwer": holm_result,
        "adjusted_rows": adjusted_rows,
        "sealed_denominator": holm_result.get("sealed_denominator"),
        "method": holm_result.get("method"),
        "error_rate_target": holm_result.get("error_rate_target"),
        "alpha": holm_result.get("alpha"),
        "route": route,
        "statistical_route_status": route.get("statistical_route_status", route.get("status")),
        "claim_boundary_status": route.get(
            "claim_boundary_status",
            permutation_result["rows"][0].get("claim_boundary_status"),
        ),
        "profit_visible": False,
        "route_eligible": False,
    }
    result.update(
        {
            key: value
            for key, value in route.items()
            if key
            in {
                "known_fixture_route_reason",
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
            }
        }
    )
    validation = validate_statistical_evaluation_result(result)
    if validation["status"] != "statistical_evaluation_valid":
        return validation
    assert_json_safe(result)
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Phase 168 synthetic fixture permutation and Holm/FWER statistics."
    )
    parser.add_argument("--fixture", choices=(*SUPPORTED_FIXTURES, "all"), default="known-noise")
    parser.add_argument("--B", type=int, default=DEFAULT_PERMUTATION_B)
    parser.add_argument("--seed", type=int, default=DEFAULT_PERMUTATION_SEED)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.fixture == "all":
        evaluations = {
            fixture: evaluate_fixture_family_statistics(fixture, B=args.B, seed=args.seed)
            for fixture in SUPPORTED_FIXTURES
        }
        output: dict[str, Any] = {
            "status": "fixture_statistical_evaluation_complete",
            "fixtures": evaluations,
            "B": args.B,
            "seed": args.seed,
        }
    else:
        output = evaluate_fixture_family_statistics(args.fixture, B=args.B, seed=args.seed)
    assert_json_safe(output)
    print(json.dumps(output, sort_keys=True, indent=2 if args.pretty else None, allow_nan=False))
    if args.fixture == "all":
        return 0 if all(item["status"] == "fixture_statistical_evaluation_complete" for item in evaluations.values()) else 1
    return 0 if output["status"] == "fixture_statistical_evaluation_complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
