"""Generate a Phase 169 fixture-only ProfitVisibilityReport.v1 payload."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts.profit_visibility_statistical_evaluator import (
        DEFAULT_PERMUTATION_B,
        DEFAULT_PERMUTATION_SEED,
        SUPPORTED_FIXTURES,
        assert_json_safe,
        evaluate_fixture_family_statistics,
        validate_statistical_evaluation_result,
    )
    from scripts.validate_profit_visibility_report import (
        choose_primary_stop_reason,
        classify_candidate_claim_boundary,
        derive_family_outcome,
        derive_overall_outcome,
        summarize_survivor_counts,
        validate_claim_wording,
        validate_fixture_anchor_boundary,
        validate_pvalue_provenance,
        validate_report_shape,
    )
except ModuleNotFoundError:  # pragma: no cover - script execution path fallback
    from profit_visibility_statistical_evaluator import (
        DEFAULT_PERMUTATION_B,
        DEFAULT_PERMUTATION_SEED,
        SUPPORTED_FIXTURES,
        assert_json_safe,
        evaluate_fixture_family_statistics,
        validate_statistical_evaluation_result,
    )
    from validate_profit_visibility_report import (
        choose_primary_stop_reason,
        classify_candidate_claim_boundary,
        derive_family_outcome,
        derive_overall_outcome,
        summarize_survivor_counts,
        validate_claim_wording,
        validate_fixture_anchor_boundary,
        validate_pvalue_provenance,
        validate_report_shape,
    )


REPORT_VERSION = "ProfitVisibilityReport.v1"
MILESTONE = "v9.1"
DEFAULT_FIXTURE = "all"
DEFAULT_OUTPUT = Path("reports/v9.1/profit_visibility_report_v1_fixture.json")
DEFAULT_DECISION_REPORT_OUTPUT = Path(
    "reports/v9.1/profit_visibility_decision_report.md"
)
DETERMINISTIC_GENERATED_AT = "2026-06-04T00:00:00+09:00"
FIXTURE_REPORT_REPLAY_COMMAND = (
    "uv run python scripts/profit_visibility_e2e_report.py --fixture all "
    "--B 999 --seed 168 --output "
    "reports/v9.1/profit_visibility_report_v1_fixture.json --pretty"
)
READINESS_FALSE_FIELDS = (
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
)
REPORT_ROW_METRIC_FIELDS = (
    "net_profit_factor",
    "net_expectancy",
    "max_drawdown",
    "turnover",
    "trade_count",
    "capacity_notional_bound",
    "slippage_sensitivity",
)


def _fail_closed(status: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "status": status,
        "profit_visible": False,
        "route_eligible": False,
        "reason": reason,
        **details,
    }


def _selected_fixtures(fixture: str) -> tuple[str, ...]:
    if fixture == "all":
        return tuple(SUPPORTED_FIXTURES)
    if fixture not in SUPPORTED_FIXTURES:
        raise ValueError(f"unsupported fixture: {fixture}")
    return (fixture,)


def build_fixture_registration_anchor(
    fixture_id: str, registration_anchor_ref: str | None = None
) -> dict[str, Any]:
    anchor_ref = registration_anchor_ref or f"phase168-fixture-protocol:{fixture_id}"
    anchor = {
        "anchor_kind": "fixture_unanchored",
        "anchor_scope": "fixture_only",
        "external_anchor_verified": False,
        "registration_anchor_ref": anchor_ref,
        "registered_protocol_ref": anchor_ref,
        "ots_proof_path": None,
        "ots_proof_unavailable_reason": "fixture_protocol_has_no_external_ots_anchor",
        "reason": "fixture_unanchored_not_external_proof",
    }
    assert_json_safe(anchor)
    return anchor


def build_gate_statuses(
    statistical_result: dict[str, Any], row: dict[str, Any] | None = None
) -> dict[str, Any]:
    report_row = row or statistical_result["adjusted_rows"][0]
    registration = build_fixture_registration_anchor(
        str(statistical_result["fixture_id"]),
        str(report_row["registration_anchor_ref"]),
    )
    holm = statistical_result["holm_fwer"]
    gates = {
        "registration_anchor": {
            "status": "blocked",
            "reason": "registration_anchor_invalid",
            "anchor_kind": registration["anchor_kind"],
            "external_anchor_verified": False,
            "anchor_scope": "fixture_only",
        },
        "external_anchor": {
            "status": "blocked",
            "reason": "external_anchor_or_ots_missing",
            "external_anchor_verified": False,
            "ots_proof_path": None,
        },
        "cost": {
            "status": "computed",
            "reason": "phase167_costed_metrics_evaluated",
            "cost_model_fingerprint": report_row["cost_model_fingerprint"],
            "net_expectancy": report_row["net_expectancy"],
            "net_profit_factor": report_row["net_profit_factor"],
        },
        "sample": {
            "status": "blocked",
            "reason": "sample_or_leakage_failed",
            "scope": "fixture_only",
        },
        "leakage": {
            "status": "blocked",
            "reason": "sample_or_leakage_failed",
            "scope": "fixture_only",
        },
        "oos_wfd_or_holdout": {
            "status": "blocked",
            "reason": "oos_wfd_or_holdout_failed",
            "scope": "fixture_only",
        },
        "mtc": {
            "status": "computed",
            "reason": report_row["mtc_reason"],
            "mtc_status": holm["status"],
            "mtc_passed": report_row["mtc_passed"],
            "p_holm_adjusted": report_row["p_holm_adjusted"],
            "sealed_denominator": report_row["sealed_denominator"],
            "mtc_method": report_row["mtc_method"],
            "error_rate_target": report_row["error_rate_target"],
            "alpha": report_row["alpha"],
        },
        "cost_sensitivity": {
            "status": "blocked",
            "reason": "cost_sensitivity_failed",
            "slippage_sensitivity": report_row["slippage_sensitivity"],
            "scope": "fixture_only",
        },
        "paper_forward_mapping": {
            "status": "blocked",
            "reason": "paper_forward_mapping_blocked",
            "paper_forward_mapping_status": "blocked",
            "scope": "synthetic_fixture_not_claimable",
        },
    }
    assert_json_safe(gates)
    return gates


def _all_failures_for_report_row(row: dict[str, Any]) -> list[str]:
    failures = [
        "registration_anchor_invalid",
        "external_anchor_or_ots_missing",
        "sample_or_leakage_failed",
        "oos_wfd_or_holdout_failed",
    ]
    if row.get("mtc_passed") is not True:
        failures.append("mtc_failed")
    failures.extend(
        [
            "cost_sensitivity_failed",
            "paper_forward_mapping_blocked",
            "synthetic_provenance_non_claimable",
        ]
    )
    return failures


def _metric_unavailable_reasons(row: dict[str, Any]) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for field in REPORT_ROW_METRIC_FIELDS:
        if row.get(field) is None:
            reasons[f"{field}_unavailable_reason"] = (
                f"{field} unavailable in Phase 168 computed fixture evidence"
            )
    return reasons


def _readiness_false_fields() -> dict[str, bool]:
    return {field: False for field in READINESS_FALSE_FIELDS}


def project_statistical_row_to_report_row(
    row: dict[str, Any], statistical_result: dict[str, Any]
) -> dict[str, Any]:
    fixture_id = str(statistical_result["fixture_id"])
    registration = build_fixture_registration_anchor(
        fixture_id, str(row["registration_anchor_ref"])
    )
    failures = _all_failures_for_report_row(row)
    primary_stop_reason = choose_primary_stop_reason(failures)
    report_row = {
        "family_id": row["family_id"],
        "hypothesis_id": row["hypothesis_id"],
        "sealed_hypothesis_index": row["sealed_hypothesis_index"],
        "sealed_hypothesis_count": statistical_result["sealed_hypothesis_count"],
        "signal_family": row["family_id"],
        "universe": "synthetic_fixture_not_market",
        "timeframe": "fixture_replay",
        "parameter_set": {
            "fixture_id": fixture_id,
            "source_costed_fixture_id": row["source_costed_fixture_id"],
        },
        "filter_set": {"status": "synthetic_fixture_predefined"},
        "split_protocol": "fixture_only_no_oos_wfd_or_holdout",
        "cost_model_fingerprint": row["cost_model_fingerprint"],
        "registration_anchor_ref": row["registration_anchor_ref"],
        "registration_anchor": registration,
        "anchor_kind": registration["anchor_kind"],
        "external_anchor_verified": False,
        "synthetic_fixture_id": fixture_id,
        "data_provenance": row["data_provenance"],
        "source_refs": row["source_refs"],
        "p_raw": row["p_raw"],
        "p_holm_adjusted": row["p_holm_adjusted"],
        "holm_rank": row["holm_rank"],
        "mtc_passed": row["mtc_passed"],
        "mtc_reason": row["mtc_reason"],
        "sealed_denominator": row["sealed_denominator"],
        "mtc_method": row["mtc_method"],
        "error_rate_target": row["error_rate_target"],
        "alpha": row["alpha"],
        "mtc_input_count": row["mtc_input_count"],
        "B": row["B"],
        "seed": row["seed"],
        "test_statistic": row["test_statistic"],
        "null_method": row["null_method"],
        "p_value_method": row["p_value_method"],
        "observed_stat": row["observed_stat"],
        "null_extreme_count": row["null_extreme_count"],
        "p_value_provenance": row["p_value_provenance"],
        "sealed_protocol_ref": row["sealed_protocol_ref"],
        "supported_evaluation_run_ref": row["supported_evaluation_run_ref"],
        "net_profit_factor": row["net_profit_factor"],
        "net_expectancy": row["net_expectancy"],
        "max_drawdown": row["max_drawdown"],
        "turnover": row["turnover"],
        "trade_count": row["trade_count"],
        "capacity_notional_bound": row["capacity_notional_bound"],
        "slippage_sensitivity": row["slippage_sensitivity"],
        "base_metrics": {
            field: row[field]
            for field in REPORT_ROW_METRIC_FIELDS
            if field in row
        },
        "adverse_metrics_unavailable_reason": (
            "Phase 168 adjusted row does not expose adverse metrics"
        ),
        "cost_sensitivity": {
            "status": "fixture_only_blocked",
            "slippage_sensitivity": row["slippage_sensitivity"],
        },
        "statistical_route_status": statistical_result["statistical_route_status"],
        "computed_route_label": statistical_result["statistical_route_status"],
        "known_fixture_route_reason": statistical_result["known_fixture_route_reason"],
        "claim_boundary_status": statistical_result["claim_boundary_status"],
        "profit_visible": False,
        "route_eligible": False,
        "paper_forward_mapping_status": "blocked",
        "all_failures": failures,
        "primary_stop_reason": primary_stop_reason,
        "replay_metadata": row["replay_metadata"],
        **_readiness_false_fields(),
    }
    report_row["gate_statuses"] = build_gate_statuses(statistical_result, row)
    report_row.update(_metric_unavailable_reasons(row))
    assert_json_safe(report_row)
    return report_row


def _family_summary_for_rows(
    statistical_result: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    derived = derive_family_outcome(rows)
    holm_summary = statistical_result["holm_fwer"]["family_summaries"][0]
    summary = {
        **derived,
        "derived_from_all_hypothesis_rows": True,
        "synthetic_fixture_id": statistical_result["fixture_id"],
        "computed_statistical_route_status": statistical_result[
            "statistical_route_status"
        ],
        "known_fixture_route_reason": statistical_result[
            "known_fixture_route_reason"
        ],
        "mtc_status": statistical_result["holm_fwer"]["status"],
        "mtc_passed_count": holm_summary["mtc_passed_count"],
        "mtc_failed_count": holm_summary["mtc_failed_count"],
        "sealed_denominator": holm_summary["sealed_denominator"],
        "mtc_method": holm_summary["method"],
        "error_rate_target": holm_summary["error_rate_target"],
        "alpha": holm_summary["alpha"],
        "route_eligible": False,
        **_readiness_false_fields(),
    }
    assert_json_safe(summary)
    return summary


def project_statistical_family_to_report_family(
    statistical_result: dict[str, Any]
) -> dict[str, Any]:
    validation = validate_statistical_evaluation_result(statistical_result)
    if validation["status"] != "statistical_evaluation_valid":
        return _fail_closed(
            "fixture_report_invalid",
            "statistical_evaluation_invalid",
            validation=validation,
        )
    rows = [
        project_statistical_row_to_report_row(row, statistical_result)
        for row in statistical_result["adjusted_rows"]
    ]
    projected = {
        "synthetic_fixture_id": statistical_result["fixture_id"],
        "family_id": statistical_result["family_id"],
        "candidate_rows": rows,
        "family_summary": _family_summary_for_rows(statistical_result, rows),
        "statistical_evidence": {
            "status": statistical_result["status"],
            "sealed_protocol_ref": statistical_result["sealed_protocol_ref"],
            "supported_evaluation_run_ref": statistical_result[
                "supported_evaluation_run_ref"
            ],
            "canonical_input_order": statistical_result["canonical_input_order"],
            "replay_metadata": statistical_result["replay_metadata"],
        },
    }
    assert_json_safe(projected)
    return projected


def build_fixture_report_payload(
    *,
    fixture: str = DEFAULT_FIXTURE,
    B: int = DEFAULT_PERMUTATION_B,
    seed: int = DEFAULT_PERMUTATION_SEED,
) -> dict[str, Any]:
    family_results = {
        fixture_id: evaluate_fixture_family_statistics(fixture_id, B=B, seed=seed)
        for fixture_id in _selected_fixtures(fixture)
    }
    projected_families = [
        project_statistical_family_to_report_family(result)
        for result in family_results.values()
    ]
    invalid = [family for family in projected_families if family.get("status")]
    if invalid:
        return _fail_closed(
            "fixture_report_invalid",
            "family_projection_invalid",
            invalid_families=invalid,
        )

    candidate_rows = [
        row
        for projected in projected_families
        for row in projected["candidate_rows"]
    ]
    family_summaries = [projected["family_summary"] for projected in projected_families]
    overall = derive_overall_outcome(family_summaries)
    survivor_counts = summarize_survivor_counts(candidate_rows)
    mtc_survivor_count = sum(1 for row in candidate_rows if row.get("mtc_passed") is True)
    survivor_count = sum(
        family_count["survivor_count"] for family_count in survivor_counts.values()
    )

    payload = {
        "report_version": REPORT_VERSION,
        "generated_at": DETERMINISTIC_GENERATED_AT,
        "milestone": MILESTONE,
        "fixture": fixture,
        "B": B,
        "seed": seed,
        "status": "fixture_report_generated",
        "evaluation_scope": "fixture_only_evaluation_instrument",
        "overall_outcome": overall["overall_outcome"],
        "profit_visible": False,
        "route_eligible": False,
        "survivor_count": survivor_count,
        "mtc_survivor_count": mtc_survivor_count,
        "primary_stop_reason": overall["primary_stop_reason"],
        "all_failures": overall["all_failures"],
        "claim_boundary_statuses": overall["claim_boundary_statuses"],
        "decision_report_ref": "reports/v9.1/profit_visibility_decision_report.md",
        "candidate_rows": candidate_rows,
        "family_summaries": family_summaries,
        "survivor_counts": survivor_counts,
        "statistical_evidence": {
            projected["synthetic_fixture_id"]: projected["statistical_evidence"]
            for projected in projected_families
        },
    }
    validation = validate_fixture_report_payload(payload)
    if validation["status"] != "fixture_report_valid":
        return _fail_closed(
            "fixture_report_invalid",
            "payload_validation_failed",
            validation=validation,
        )
    assert_json_safe(payload)
    return payload


def validate_fixture_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _fail_closed("fixture_report_invalid", "payload_not_object")
    if payload.get("report_version") != REPORT_VERSION:
        return _fail_closed("fixture_report_invalid", "report_version_mismatch")
    if payload.get("profit_visible") is True or payload.get("route_eligible") is True:
        return _fail_closed("fixture_report_invalid", "report_claimability_drift")

    shape = validate_report_shape(payload)
    if shape["status"] != "shape_valid":
        return _fail_closed("fixture_report_invalid", "report_shape_invalid", shape=shape)

    for row in payload["candidate_rows"]:
        for field in ("profit_visible", "route_eligible"):
            if row.get(field) is True:
                return _fail_closed(
                    "fixture_report_invalid",
                    "row_claimability_drift",
                    field=field,
                    value=True,
                )
        if row.get("external_anchor_verified") is not False:
            return _fail_closed(
                "fixture_report_invalid",
                "row_external_anchor_drift",
                field="external_anchor_verified",
                value=row.get("external_anchor_verified"),
            )
        pvalue = validate_pvalue_provenance(row)
        if pvalue["status"] != "pvalue_provenance_valid":
            return _fail_closed(
                "fixture_report_invalid",
                "pvalue_provenance_invalid",
                pvalue=pvalue,
            )
        anchor = validate_fixture_anchor_boundary(row.get("registration_anchor"))
        if anchor["status"] != "fixture_anchor_only":
            return _fail_closed(
                "fixture_report_invalid",
                "fixture_anchor_boundary_invalid",
                anchor=anchor,
            )
        boundary = classify_candidate_claim_boundary(row)
        if boundary.get("profit_visible") is True or boundary.get("claimable") is True:
            return _fail_closed(
                "fixture_report_invalid",
                "claim_boundary_drift",
                boundary=boundary,
            )
        for field in READINESS_FALSE_FIELDS:
            if row.get(field) is not False:
                return _fail_closed(
                    "fixture_report_invalid",
                    "readiness_field_drift",
                    field=field,
                    value=row.get(field),
                )
    assert_json_safe(payload)
    return {
        "status": "fixture_report_valid",
        "profit_visible": False,
        "route_eligible": False,
        "shape": shape,
    }


def write_fixture_report_payload(
    path: str | Path,
    *,
    fixture: str = DEFAULT_FIXTURE,
    B: int = DEFAULT_PERMUTATION_B,
    seed: int = DEFAULT_PERMUTATION_SEED,
    pretty: bool = True,
) -> dict[str, Any]:
    payload = build_fixture_report_payload(fixture=fixture, B=B, seed=seed)
    validation = validate_fixture_report_payload(payload)
    if validation["status"] != "fixture_report_valid":
        raise ValueError(validation["reason"])
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            payload,
            sort_keys=True,
            indent=2 if pretty else None,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return payload


def _rows_by_fixture(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["synthetic_fixture_id"]): row
        for row in payload.get("candidate_rows", [])
        if isinstance(row, dict) and "synthetic_fixture_id" in row
    }


def _families_by_fixture(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(summary["synthetic_fixture_id"]): summary
        for summary in payload.get("family_summaries", [])
        if isinstance(summary, dict) and "synthetic_fixture_id" in summary
    }


def _fixture_order(payload: dict[str, Any]) -> tuple[str, ...]:
    observed = tuple(_families_by_fixture(payload))
    preferred = ("known-noise", "known-edge")
    ordered = [fixture_id for fixture_id in preferred if fixture_id in observed]
    ordered.extend(sorted(fixture_id for fixture_id in observed if fixture_id not in ordered))
    return tuple(ordered)


def _code(value: Any) -> str:
    return f"`{value}`"


def _status(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def render_fixture_decision_report(
    payload: dict[str, Any],
    *,
    payload_ref: str = "reports/v9.1/profit_visibility_report_v1_fixture.json",
    generation_command: str = FIXTURE_REPORT_REPLAY_COMMAND,
) -> str:
    validation = validate_fixture_report_payload(payload)
    if validation["status"] != "fixture_report_valid":
        raise ValueError(validation["reason"])

    rows = _rows_by_fixture(payload)
    families = _families_by_fixture(payload)
    fixture_order = _fixture_order(payload)
    family_lines = [
        "| family | family_outcome | survivor_count | primary_stop_reason | "
        "computed_route | mtc_status | route_status |",
        "|---|---|---:|---|---|---|---|",
    ]
    for fixture_id in fixture_order:
        family = families[fixture_id]
        row = rows[fixture_id]
        family_lines.append(
            "| "
            f"{family['family_id']} | "
            f"{_code(family['family_outcome'])} | "
            f"{family['survivor_count']} | "
            f"{_code(family['primary_stop_reason'])} | "
            f"{_code(family['computed_statistical_route_status'])} | "
            f"{_code(family['mtc_status'])} | "
            f"{_code('route-ineligible' if not row['route_eligible'] else 'route-eligible')} |"
        )

    known_noise = families["known-noise"]
    known_noise_row = rows["known-noise"]
    known_edge = families["known-edge"]
    known_edge_row = rows["known-edge"]

    report = f"""# v9.1 Profit Visibility Decision Report

## Decision

Current closure decision:

| field | value |
|---|---|
| `overall_outcome` | `{payload["overall_outcome"]}` |
| `evaluation_scope` | `{payload["evaluation_scope"]}` |
| `profit_visible = true/false` | `profit_visible = {_status(payload["profit_visible"])}` |
| `route_eligible = true/false` | `route_eligible = {_status(payload["route_eligible"])}` |
| `survivor_count` | `{payload["survivor_count"]}` |
| `mtc_survivor_count` | `{payload["mtc_survivor_count"]}` |
| `primary_stop_reason` | `{payload["primary_stop_reason"]}` |
| input evidence contract | `{payload["report_version"]}` |
| input evidence payload | `{payload_ref}` |
| generation command | `{generation_command}` |

This decision report is derived from `{payload_ref}`. Its strongest scope is
fixture-only evaluation instrument evidence. It records Phase 168 synthetic
fixture replay behavior without converting that replay into market, deployment,
or operations evidence.

## Known-Noise Route Summary

Known-noise remains an honest null-ship fixture route:

| field | value |
|---|---|
| `family_id` | `{known_noise["family_id"]}` |
| `computed_statistical_route_status` | `{known_noise["computed_statistical_route_status"]}` |
| `known_fixture_route_reason` | `{known_noise["known_fixture_route_reason"]}` |
| `mtc_status` | `{known_noise["mtc_status"]}` |
| `p_holm_adjusted` | `{known_noise_row["p_holm_adjusted"]}` |
| `survivor_count` | `{known_noise["survivor_count"]}` |
| `primary_stop_reason` | `{known_noise["primary_stop_reason"]}` |

The route is still route-ineligible because the fixture anchor, external anchor,
sample/leakage, OOS/WFD or holdout, cost sensitivity, and paper-forward mapping
gates are blocked in the payload.

## Known-Edge Route Summary

Known-edge preserves the synthetic MTC survivor-shaped edge without a claimable
route:

| field | value |
|---|---|
| `family_id` | `{known_edge["family_id"]}` |
| `computed_statistical_route_status` | `{known_edge["computed_statistical_route_status"]}` |
| `known_fixture_route_reason` | `{known_edge["known_fixture_route_reason"]}` |
| `mtc_status` | `{known_edge["mtc_status"]}` |
| `mtc_passed_count` | `{known_edge["mtc_passed_count"]}` |
| `p_holm_adjusted` | `{known_edge_row["p_holm_adjusted"]}` |
| `survivor_count` | `{known_edge["survivor_count"]}` |
| `primary_stop_reason` | `{known_edge["primary_stop_reason"]}` |

The row is synthetic non-claimable and route-ineligible even though its
MTC-shaped fields pass inside the fixture. It is not promoted into a claimable
route.

## Family Outcomes

{chr(10).join(family_lines)}

## Fixture Anchor And OTS Blockers

All report rows use `anchor_kind = fixture_unanchored`, set
`external_anchor_verified = false`, and carry no OTS proof path. The first
blocking stop is therefore `{payload["primary_stop_reason"]}`, with
`external_anchor_or_ots_missing` also retained in `all_failures`.

## Claim Boundary And Blocked Readiness

This report is a fixture-only evaluation instrument.
It records synthetic non-claimable fixture evidence only.
It records route-ineligible outcomes only.
It grants no market evidence.
It grants no profit readiness.
It grants no paper-forward readiness.
It grants no live-shadow readiness.
It grants no live readiness.
It grants no account readiness.
It grants no broker readiness.
It grants no network readiness.
It grants no credential readiness.
It grants no runtime readiness.

Blocked readiness fields remain false in every candidate row:
`market_evidence`, `claimable`, `paper_forward_eligible`,
`live_shadow_ready`, `live_ready`, `account_ready`, `broker_ready`,
`network_ready`, `credential_ready`, and `runtime_ready`.

## Verification And Review Evidence

Plan 02 local verification targets:

```bash
uv run pytest -q tests/test_profit_visibility_closure.py::test_phase169_decision_report_stop_reason_matches_generated_payload tests/test_profit_visibility_closure.py::test_phase169_unmapped_stop_reason_labels_fail_closure_guard tests/test_profit_visibility_report.py::test_phase169_fixture_report_claim_wording_rejects_readiness_claims
uv run python scripts/validate_profit_visibility_report.py reports/v9.1/profit_visibility_report_v1_fixture.json
git diff --check
```

Plans 03-04 review evidence placeholder:

- Plan 03 must record external review evidence before closure evidence is
  treated as reviewed.
- Plan 04 must bind final closure proof and any remaining route decisions.
"""
    wording = validate_claim_wording(report)
    if wording["status"] != "claim_wording_valid":
        raise ValueError(f"decision_report_claim_wording_rejected: {wording}")
    return report


def write_fixture_decision_report(
    path: str | Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    report = render_fixture_decision_report(payload)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    return {
        "status": "fixture_decision_report_written",
        "output": str(output_path),
        "primary_stop_reason": payload.get("primary_stop_reason"),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a fixture-only ProfitVisibilityReport.v1 payload."
    )
    parser.add_argument(
        "--fixture",
        choices=(*SUPPORTED_FIXTURES, "all"),
        default=DEFAULT_FIXTURE,
    )
    parser.add_argument("--B", type=int, default=DEFAULT_PERMUTATION_B)
    parser.add_argument("--seed", type=int, default=DEFAULT_PERMUTATION_SEED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        payload = write_fixture_report_payload(
            args.output,
            fixture=args.fixture,
            B=args.B,
            seed=args.seed,
            pretty=args.pretty,
        )
    except ValueError as exc:
        print(json.dumps({"status": "fixture_report_invalid", "reason": str(exc)}))
        return 1

    result = validate_fixture_report_payload(payload)
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(args.output),
                "row_count": result["shape"]["row_count"],
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "fixture_report_valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
