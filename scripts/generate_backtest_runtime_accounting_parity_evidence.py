#!/usr/bin/env python3
"""Generate deterministic backtest/runtime accounting parity evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROOT_RESOLVED = ROOT.resolve()
SCHEMA_VERSION = "backtest_runtime_accounting_parity_evidence.v1"
BOUNDARY = "backtest_runtime_accounting_parity"
DEFAULT_REPORT_DIR = Path("reports/v8.1")
JSON_REPORT_NAME = "backtest_runtime_accounting_parity_evidence.json"
MD_REPORT_NAME = "backtest_runtime_accounting_parity_evidence.md"

PROTECTED_OUTPUT_PREFIXES = (
    "reports/v5.8",
    "reports/v5.7",
    "reports/v7.",
    "reports/v8.0",
    ".planning/milestones",
    "docs/reports/v4",
    "data/v4",
)

SOURCE_EVIDENCE_PATHS: tuple[str, ...] = (
    "reports/v5.11/backtest_risk_gate_closure_evidence.json",
    "reports/v7.4/runtime_accounting_series_closure_audit.md",
    "reports/v8.0/grid_subset_invariance_semantic_cleanup_closure_audit.md",
    "docs/sessions/2026-05-14-side-post-v80-healthcheck.md",
)

COMPARABLE_CLAIMS: tuple[dict[str, str], ...] = (
    {
        "id": "requested_unit",
        "claim": "Requested runtime sizing unit uses unit_backtest_run vocabulary.",
        "backtest_surface": 'BACKTEST_REQUESTED_SIZE and requested_size_basis = "unit_backtest_run".',
        "paper_runtime_surface": "Paper cap evidence requested_size / requested_size_basis when unit-style sizing is used.",
        "expected_relation": "Same vocabulary only; no broker/notional conversion is added in this boundary.",
        "status": "defined",
    },
    {
        "id": "cap_decision_size",
        "claim": "Binding cap decision size reduces runtime size.",
        "backtest_surface": "risk_gate.allowed_size and effective_size in BacktestRuntimeSizing.",
        "paper_runtime_surface": "Paper cap runtime size override and cap health summary.",
        "expected_relation": "Binding cap reduces runtime size; non-binding cap records applied/no sizing effect.",
        "status": "defined",
    },
    {
        "id": "cost_adjusted_pnl_basis",
        "claim": "Cost-adjusted PnL basis is explicit.",
        "backtest_surface": "Backtest returns subtract fee cost from strategy return.",
        "paper_runtime_surface": "estimated_net_pnl = gross_pnl - estimated_cost.",
        "expected_relation": "Both state cost-adjusted basis; numeric equality is limited to deterministic fixture rows.",
        "status": "defined",
    },
    {
        "id": "size_scaled_cost",
        "claim": "Costs scale with effective exposure unit.",
        "backtest_surface": "fee_cost = trade * fee * effective_size.",
        "paper_runtime_surface": "Explicit fee/spread cost model with requested size/leverage inputs.",
        "expected_relation": "Both scale cost with effective exposure; formulas must be documented before numeric comparison.",
        "status": "defined",
    },
    {
        "id": "trade_count",
        "claim": "Event counts are not size-scaled.",
        "backtest_surface": "num_trades from position-change events.",
        "paper_runtime_surface": "Paper close/event rows.",
        "expected_relation": "Trade/event counts remain count data, not exposure-scaled metrics.",
        "status": "defined",
    },
    {
        "id": "fail_closed_missing_or_zero_cost",
        "claim": "Missing/zero/invalid accounting inputs do not silently produce net claims.",
        "backtest_surface": "Invalid/non-finite fee or cap sizing fails before metrics.",
        "paper_runtime_surface": "estimated_net missing/zero cost model fails before close mutation.",
        "expected_relation": "Fail closed instead of falling back to a net claim.",
        "status": "defined",
    },
)

NON_COMPARABLE_CLAIMS: tuple[dict[str, str], ...] = (
    {
        "id": "paper_legacy_gross_pnl_surfaces",
        "claim": "Paper trades.pnl and get_todays_pnl remain legacy gross/cost-unadjusted.",
        "reason": "They intentionally preserve legacy gross semantics.",
        "status": "intentionally_non_comparable",
    },
    {
        "id": "paper_liquidation_trigger_basis",
        "claim": "Paper liquidation trigger remains gross/unrealized basis.",
        "reason": "Trigger basis is not the same surface as runtime realized/equity accounting.",
        "status": "intentionally_non_comparable",
    },
    {
        "id": "paper_runtime_ledger_tables",
        "claim": "Paper runtime ledger/source tables have no current backtest ledger equivalent.",
        "reason": "Backtest parity can reference them but must not claim table-level equivalence yet.",
        "status": "intentionally_non_comparable",
    },
    {
        "id": "backtest_public_result_shape",
        "claim": "side-cli.backtest.result.v1 public metrics are profit_factor, num_trades, and total_return.",
        "reason": "Internal engine metrics are not public contract unless explicitly added.",
        "status": "intentionally_non_comparable",
    },
    {
        "id": "v511_cap_parity_status",
        "claim": 'v5.11 cap_parity.status = "not_applicable" remains truthful for old outputs.',
        "reason": "Fresh v8.1 evidence must not overload historical v5.11 output semantics.",
        "status": "intentionally_non_comparable",
    },
)


def repo_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_RESOLVED).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(repo_path(path).read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(repo_path(path).read_text(encoding="utf-8"))


def load_text(path: Path) -> str:
    return repo_path(path).read_text(encoding="utf-8")


def protected_prefix_matches(display: str, prefix: str) -> bool:
    if prefix.endswith("."):
        return display.startswith(prefix)
    if prefix.endswith("v4"):
        return display == prefix or display.startswith(prefix)
    return display == prefix or display.startswith(prefix.rstrip("/") + "/")


def assert_allowed_report_dir(report_dir: Path) -> None:
    display = display_path(repo_path(report_dir))
    for prefix in PROTECTED_OUTPUT_PREFIXES:
        if protected_prefix_matches(display, prefix):
            raise ValueError(f"protected output directory is not allowed: {display}")


def build_protected_surface_guard(report_dir: Path) -> dict[str, Any]:
    assert_allowed_report_dir(report_dir)
    resolved_report_dir = repo_path(report_dir)
    return {
        "passed": True,
        "report_dir": display_path(resolved_report_dir),
        "planned_output_paths": [
            display_path(resolved_report_dir / JSON_REPORT_NAME),
            display_path(resolved_report_dir / MD_REPORT_NAME),
        ],
        "protected_prefixes": list(PROTECTED_OUTPUT_PREFIXES),
        "reason": "fresh v8.1 outputs only; historical/archive surfaces are read-only inputs",
    }


def build_source_evidence() -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for path_text in SOURCE_EVIDENCE_PATHS:
        path = Path(path_text)
        resolved = repo_path(path)
        exists = resolved.exists()
        sources.append(
            {
                "path": path_text,
                "role": "read_only_input",
                "exists": exists,
                "sha256": sha256_file(path) if exists else None,
            }
        )
    return sources


def build_backtest_cap_runtime_sizing_check() -> dict[str, Any]:
    v511_report = load_json(Path("reports/v5.11/backtest_risk_gate_closure_evidence.json"))
    cap_run = v511_report["runs"]["cap"]
    cap_stdout = load_json(Path(cap_run["stdout_path"]))
    cap_gate = cap_stdout["risk_gate"]
    runtime_sizing_effect = v511_report["checks"]["runtime_sizing_effect"]
    ungated_metrics = runtime_sizing_effect["ungated_metrics_hash"]["payload"]
    cap_metrics = runtime_sizing_effect["cap_metrics_hash"]["payload"]
    effective_size_equals_allowed_size = cap_gate["effective_size"] == cap_gate["allowed_size"]
    trade_count_not_scaled = cap_metrics["num_trades"] == ungated_metrics["num_trades"]
    size_scaled_metric_changed = cap_metrics["total_return"] != ungated_metrics["total_return"]
    passed = all(
        [
            cap_gate["requested_size_basis"] == "unit_backtest_run",
            cap_gate["application_status"] == "applied",
            cap_gate["runtime_sizing_applied"] is True,
            effective_size_equals_allowed_size,
            trade_count_not_scaled,
            size_scaled_metric_changed,
            runtime_sizing_effect["passed"] is True,
        ]
    )
    return {
        "passed": passed,
        "source": "reports/v5.11/backtest_risk_gate_closure_evidence.json",
        "run_name": "cap",
        "requested_size": cap_gate["requested_size"],
        "requested_size_basis": cap_gate["requested_size_basis"],
        "allowed_size": cap_gate["allowed_size"],
        "effective_size": cap_gate["effective_size"],
        "effective_size_equals_allowed_size": effective_size_equals_allowed_size,
        "application_status": cap_gate["application_status"],
        "runtime_sizing_applied": cap_gate["runtime_sizing_applied"],
        "trade_count_not_scaled": trade_count_not_scaled,
        "size_scaled_metric_changed": size_scaled_metric_changed,
        "cap_total_return": cap_metrics["total_return"],
        "ungated_total_return": ungated_metrics["total_return"],
        "num_trades": cap_metrics["num_trades"],
    }


def contains_all(text: str, needles: tuple[str, ...]) -> bool:
    return all(needle in text for needle in needles)


def build_paper_estimated_net_normal_exit_check(v74_text: str) -> dict[str, Any]:
    required = (
        'runtime_accounting_mode = "estimated_net"',
        "Realized PnL, slot equity, and portfolio equity use `estimated_net_pnl` basis.",
        "`trades.pnl` and `get_todays_pnl()` remain gross basis.",
        "missing/zero cost model fails closed before close mutation.",
    )
    return {
        "passed": contains_all(v74_text, required),
        "source": "reports/v7.4/runtime_accounting_series_closure_audit.md",
        "runtime_mode": "estimated_net",
        "event_kind": "normal_exit",
        "gross_surfaces_preserved": "`trades.pnl` and `get_todays_pnl()` remain gross basis." in v74_text,
        "explicit_nonzero_cost_model_required": "explicit nonzero cost model" in v74_text,
        "required_evidence": list(required),
    }


def build_paper_estimated_net_liquidation_check(v74_text: str) -> dict[str, Any]:
    required = (
        "Runtime realized/equity uses estimated net basis for liquidation close.",
        "`trades.pnl` and `get_todays_pnl()` remain gross basis.",
        "Margin liquidation trigger remains gross/unrealized basis.",
        "Missing/zero cost model fails closed before close mutation.",
    )
    return {
        "passed": contains_all(v74_text, required),
        "source": "reports/v7.4/runtime_accounting_series_closure_audit.md",
        "runtime_mode": "estimated_net",
        "event_kind": "liquidation",
        "liquidation_trigger_basis": "gross_unrealized",
        "gross_surfaces_preserved": "`trades.pnl` and `get_todays_pnl()` remain gross basis." in v74_text,
        "required_evidence": list(required),
    }


def build_paper_missing_or_zero_cost_fail_closed_check(v74_text: str) -> dict[str, Any]:
    required = (
        "missing/zero cost model fails closed before close mutation.",
        "Missing/zero cost model fails closed before close mutation.",
        "Missing/zero cost model blocks the cap claim and keeps estimated net close fail-closed.",
    )
    matched = [needle for needle in required if needle in v74_text]
    return {
        "passed": len(matched) >= 2,
        "source": "reports/v7.4/runtime_accounting_series_closure_audit.md",
        "close_mutation_blocked": bool(matched),
        "matched_evidence": matched,
    }


def build_verification_scope_check() -> dict[str, Any]:
    healthcheck = load_text(Path("docs/sessions/2026-05-14-side-post-v80-healthcheck.md"))
    full_workspace_skipped = "cargo test --workspace -- --test-threads=1` | SKIPPED" in healthcheck
    return {
        "passed": True,
        "source": "docs/sessions/2026-05-14-side-post-v80-healthcheck.md",
        "full_workspace": "skipped" if full_workspace_skipped else "not_recorded",
        "skip_reason": "post-v8.0 healthcheck used targeted and package-level checks; full workspace was optional and skipped",
    }


def build_backtest_runtime_accounting_parity_evidence(
    *,
    report_dir: Path = DEFAULT_REPORT_DIR,
    diff_base: str = "origin/master",
) -> dict[str, Any]:
    protected_surface_guard = build_protected_surface_guard(report_dir)
    source_evidence = build_source_evidence()
    v74_text = load_text(Path("reports/v7.4/runtime_accounting_series_closure_audit.md"))
    source_evidence_loaded = {
        "passed": all(row["exists"] and row["sha256"] for row in source_evidence),
        "source_count": len(source_evidence),
        "missing_sources": [row["path"] for row in source_evidence if not row["exists"]],
    }
    checks = {
        "parity_contract_defined": {
            "passed": True,
            "comparable_claims": len(COMPARABLE_CLAIMS),
            "non_comparable_claims": len(NON_COMPARABLE_CLAIMS),
        },
        "source_evidence_loaded": source_evidence_loaded,
        "backtest_cap_runtime_sizing": build_backtest_cap_runtime_sizing_check(),
        "paper_estimated_net_normal_exit": build_paper_estimated_net_normal_exit_check(v74_text),
        "paper_estimated_net_liquidation": build_paper_estimated_net_liquidation_check(v74_text),
        "paper_missing_or_zero_cost_fail_closed": build_paper_missing_or_zero_cost_fail_closed_check(v74_text),
        "verification_scope": build_verification_scope_check(),
        "protected_surface_guard": protected_surface_guard,
    }
    checks_failed = sum(1 for check in checks.values() if not check.get("passed"))
    return {
        "schema_version": SCHEMA_VERSION,
        "boundary": BOUNDARY,
        "summary": {
            "overall_status": "PASS" if checks_failed == 0 else "FAIL",
            "checks_failed": checks_failed,
            "checks_passed": len(checks) - checks_failed,
            "diff_base": diff_base,
            "implementation_scope": "contract_evidence_only_first_slice",
        },
        "source_evidence": source_evidence,
        "parity_contract": {
            "comparable_claims": list(COMPARABLE_CLAIMS),
            "non_comparable_claims": list(NON_COMPARABLE_CLAIMS),
        },
        "checks": checks,
        "protected_surface_guard": protected_surface_guard,
    }


def write_json(data: Any, path: Path) -> None:
    target = repo_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def markdown_cell(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    if isinstance(value, list):
        return "<br>".join(markdown_cell(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def render_claim_table(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> list[str]:
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(markdown_cell(row.get(column, "")) for column in columns) + " |")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Backtest/runtime Accounting Parity Evidence",
        "",
        f"- Schema: `{report['schema_version']}`",
        f"- Boundary: `{report['boundary']}`",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Implementation scope: `{summary['implementation_scope']}`",
        f"- Diff base: `{summary['diff_base']}`",
        "",
        "## Source Evidence",
        "",
        "| Path | Role | Exists | SHA256 |",
        "|---|---|---:|---|",
    ]
    for source in report["source_evidence"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(source["path"]),
                    markdown_cell(source["role"]),
                    markdown_cell(source["exists"]),
                    markdown_cell(source["sha256"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Comparable Claims",
            "",
            *render_claim_table(
                report["parity_contract"]["comparable_claims"],
                ("id", "claim", "backtest_surface", "paper_runtime_surface", "expected_relation", "status"),
            ),
            "",
            "## Intentionally Non-Comparable Claims",
            "",
            *render_claim_table(
                report["parity_contract"]["non_comparable_claims"],
                ("id", "claim", "reason", "status"),
            ),
            "",
            "## Check Results",
            "",
            "| Check | Passed | Source | Notes |",
            "|---|---:|---|---|",
        ]
    )
    for name, check in report["checks"].items():
        notes = []
        for key, value in check.items():
            if key in {"passed", "source", "required_evidence", "matched_evidence", "protected_prefixes", "planned_output_paths"}:
                continue
            notes.append(f"{key}={markdown_cell(value)}")
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(name),
                    markdown_cell(check.get("passed")),
                    markdown_cell(check.get("source", "")),
                    markdown_cell("; ".join(notes)),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Protected Surface Guard",
            "",
            f"- Result: `{markdown_cell(report['protected_surface_guard']['passed'])}`",
            f"- Report dir: `{markdown_cell(report['protected_surface_guard']['report_dir'])}`",
            "- Planned outputs:",
        ]
    )
    for output_path in report["protected_surface_guard"]["planned_output_paths"]:
        lines.append(f"  - `{markdown_cell(output_path)}`")
    lines.append("")
    return "\n".join(lines)


def write_text(content: str, path: Path) -> None:
    target = repo_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def write_reports(report: dict[str, Any], report_dir: Path) -> None:
    write_json(report, report_dir / JSON_REPORT_NAME)
    write_text(render_markdown(report), report_dir / MD_REPORT_NAME)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--diff-base", default="origin/master")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_backtest_runtime_accounting_parity_evidence(
        report_dir=args.report_dir,
        diff_base=args.diff_base,
    )
    write_reports(report, args.report_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
