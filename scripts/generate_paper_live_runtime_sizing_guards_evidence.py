#!/usr/bin/env python3
"""Generate deterministic paper/live runtime sizing guards evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROOT_RESOLVED = ROOT.resolve()
SCHEMA_VERSION = "paper_live_runtime_sizing_guards_evidence.v1"
BOUNDARY = "paper_live_runtime_sizing_guards"
DEFAULT_REPORT_DIR = Path("reports/v8.2")
JSON_REPORT_NAME = "paper_live_runtime_sizing_guards_evidence.json"
MD_REPORT_NAME = "paper_live_runtime_sizing_guards_evidence.md"

PROTECTED_OUTPUT_PREFIXES = (
    "reports/v5.8",
    "reports/v5.7",
    "reports/v7.",
    "reports/v8.0",
    "reports/v8.1",
    ".planning/milestones",
    "docs/reports/v4",
    "data/v4",
)

SOURCE_EVIDENCE_PATHS: tuple[str, ...] = (
    "docs/plans/2026-05-14-paper-live-runtime-sizing-guards.md",
    "reports/v7.4/runtime_accounting_series_closure_audit.md",
    "reports/v8.1/backtest_runtime_accounting_parity_evidence.md",
    "rust/side-cli/src/main.rs",
    "rust/side-cli/src/cmd/paper.rs",
    "rust/side-engine/src/paper/risk.rs",
    "rust/side-cli/tests/paper_cli_test.rs",
    "rust/side-engine/tests/paper_risk_test.rs",
)

SURFACES: tuple[dict[str, Any], ...] = (
    {
        "surface": "paper",
        "surface_status": "implemented",
        "runtime_sizing_applied": "guarded_for_cap_apply",
        "runtime_accounting_mode": "legacy_gross_default_estimated_net_opt_in",
        "runtime_accounting_default_preserved": True,
        "live_runtime_claim_allowed": False,
        "claim_block_reason": "paper_surface_not_live",
    },
    {
        "surface": "live",
        "surface_status": "not_wired",
        "runtime_sizing_applied": "not_applicable",
        "runtime_accounting_mode": "not_implemented",
        "runtime_accounting_default_preserved": True,
        "live_runtime_claim_allowed": False,
        "claim_block_reason": "live_runtime_not_implemented",
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
        "reason": "fresh v8.2 outputs only; historical v7/v8.1 evidence remains read-only input",
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


def build_paper_runtime_guard_contract_defined_check() -> dict[str, Any]:
    risk_text = load_text(Path("rust/side-engine/src/paper/risk.rs"))
    expansion_guard = (
        "validate_paper_runtime_sizing_guard" in risk_text
        and "allowed_size > requested_size" in risk_text
        and "paper cap allowed_size must be <= requested_size" in risk_text
    )
    finite_non_negative_guard = (
        "paper {decision_class} allowed_size must be finite and non-negative" in risk_text
        and "!allowed_size.is_finite() || allowed_size < 0.0" in risk_text
    )
    requested_size_guard = (
        "paper requested_size must be finite and non-negative" in risk_text
        and "!requested_size.is_finite() || requested_size < 0.0" in risk_text
    )
    return {
        "passed": expansion_guard and finite_non_negative_guard and requested_size_guard,
        "source": "rust/side-engine/src/paper/risk.rs",
        "expansion_guard": expansion_guard,
        "finite_non_negative_guard": finite_non_negative_guard,
        "requested_size_guard": requested_size_guard,
    }


def build_paper_cli_apply_uses_runtime_guard_check() -> dict[str, Any]:
    paper_text = load_text(Path("rust/side-cli/src/cmd/paper.rs"))
    guard_index = paper_text.find("validate_paper_runtime_sizing_guard(")
    override_index = paper_text.find("runtime_size_overrides.push")
    import_present = "validate_paper_runtime_sizing_guard" in paper_text
    guard_called_before_override = (
        guard_index != -1 and override_index != -1 and guard_index < override_index
    )
    return {
        "passed": import_present and guard_called_before_override,
        "source": "rust/side-cli/src/cmd/paper.rs",
        "import_present": import_present,
        "guard_called_before_override": guard_called_before_override,
    }


def build_live_cli_not_wired_check() -> dict[str, Any]:
    main_text = load_text(Path("rust/side-cli/src/main.rs"))
    live_subcommand_present = "Live(" in main_text or "Commands::Live" in main_text
    live_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "rust").rglob("*live*.rs")
        if "target" not in path.parts
    )
    return {
        "passed": not live_subcommand_present and not live_files,
        "source": "rust/side-cli/src/main.rs",
        "live_subcommand_present": live_subcommand_present,
        "live_rust_files": live_files,
        "claim_block_reason": "live_runtime_not_implemented",
    }


def build_runtime_accounting_default_preserved_check() -> dict[str, Any]:
    paper_text = load_text(Path("rust/side-cli/src/cmd/paper.rs"))
    risk_text = load_text(Path("rust/side-engine/src/paper/risk.rs"))
    legacy_default = 'default_value = "legacy_gross"' in paper_text
    estimated_net_opt_in = "estimated_net" in paper_text and "estimated_net" in risk_text
    gross_surfaces_preserved = "trades.pnl" in load_text(
        Path("reports/v7.4/runtime_accounting_series_closure_audit.md")
    )
    return {
        "passed": legacy_default and estimated_net_opt_in and gross_surfaces_preserved,
        "source": "rust/side-cli/src/cmd/paper.rs",
        "default_mode": "legacy_gross" if legacy_default else "unknown",
        "estimated_net_opt_in": estimated_net_opt_in,
        "gross_surfaces_preserved": gross_surfaces_preserved,
    }


def build_paper_live_runtime_sizing_guards_evidence(
    *,
    report_dir: Path = DEFAULT_REPORT_DIR,
    diff_base: str = "origin/master",
) -> dict[str, Any]:
    protected_surface_guard = build_protected_surface_guard(report_dir)
    source_evidence = build_source_evidence()
    source_evidence_loaded = {
        "passed": all(row["exists"] and row["sha256"] for row in source_evidence),
        "source_count": len(source_evidence),
        "missing_sources": [row["path"] for row in source_evidence if not row["exists"]],
    }
    checks = {
        "source_evidence_loaded": source_evidence_loaded,
        "paper_runtime_guard_contract_defined": build_paper_runtime_guard_contract_defined_check(),
        "paper_cli_apply_uses_runtime_guard": build_paper_cli_apply_uses_runtime_guard_check(),
        "live_cli_not_wired": build_live_cli_not_wired_check(),
        "runtime_accounting_default_preserved": build_runtime_accounting_default_preserved_check(),
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
            "implementation_scope": "paper_guard_hardening_and_live_not_wired_evidence",
        },
        "source_evidence": source_evidence,
        "surfaces": list(SURFACES),
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


def render_table(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> list[str]:
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
        "# Paper/live Runtime Sizing Guards Evidence",
        "",
        f"- Schema: `{report['schema_version']}`",
        f"- Boundary: `{report['boundary']}`",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Implementation scope: `{summary['implementation_scope']}`",
        f"- Diff base: `{summary['diff_base']}`",
        "",
        "## Source Evidence",
        "",
        *render_table(report["source_evidence"], ("path", "role", "exists", "sha256")),
        "",
        "## Surface Contract",
        "",
        *render_table(
            report["surfaces"],
            (
                "surface",
                "surface_status",
                "runtime_sizing_applied",
                "runtime_accounting_mode",
                "runtime_accounting_default_preserved",
                "live_runtime_claim_allowed",
                "claim_block_reason",
            ),
        ),
        "",
        "## Check Results",
        "",
        "| Check | Passed | Source | Notes |",
        "|---|---:|---|---|",
    ]
    for name, check in report["checks"].items():
        notes = []
        for key, value in check.items():
            if key in {"passed", "source", "protected_prefixes", "planned_output_paths"}:
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
    report = build_paper_live_runtime_sizing_guards_evidence(
        report_dir=args.report_dir,
        diff_base=args.diff_base,
    )
    write_reports(report, args.report_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
