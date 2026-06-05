"""Contract tests for backtest/runtime accounting parity evidence generation."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import generate_backtest_runtime_accounting_parity_evidence as parity


EVIDENCE_SCHEMA_VERSION = "backtest_runtime_accounting_parity_evidence.v1"
EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version",
    "boundary",
    "summary",
    "source_evidence",
    "parity_contract",
    "checks",
    "protected_surface_guard",
}
EXPECTED_COMPARABLE_CLAIMS = {
    "requested_unit",
    "cap_decision_size",
    "cost_adjusted_pnl_basis",
    "size_scaled_cost",
    "trade_count",
    "fail_closed_missing_or_zero_cost",
}
EXPECTED_NON_COMPARABLE_CLAIMS = {
    "paper_legacy_gross_pnl_surfaces",
    "paper_liquidation_trigger_basis",
    "paper_runtime_ledger_tables",
    "backtest_public_result_shape",
    "v511_cap_parity_status",
}
EXPECTED_SOURCE_EVIDENCE_PATHS = {
    "reports/v5.11/backtest_risk_gate_closure_evidence.json",
    "reports/v7.4/runtime_accounting_series_closure_audit.md",
    "reports/v8.0/grid_subset_invariance_semantic_cleanup_closure_audit.md",
    "docs/sessions/2026-05-14-side-post-v80-healthcheck.md",
}

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_backtest_runtime_accounting_parity_evidence.py"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_builds_top_level_parity_contract(tmp_path: Path) -> None:
    report = parity.build_backtest_runtime_accounting_parity_evidence(
        report_dir=tmp_path,
        diff_base="HEAD",
    )

    assert parity.SCHEMA_VERSION == EVIDENCE_SCHEMA_VERSION
    assert parity.DEFAULT_REPORT_DIR == Path("reports/v8.1")
    assert set(report) == EXPECTED_TOP_LEVEL_KEYS
    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["boundary"] == "backtest_runtime_accounting_parity"
    assert report["summary"]["overall_status"] == "PASS"
    assert report["summary"]["checks_failed"] == 0

    contract = report["parity_contract"]
    assert {row["id"] for row in contract["comparable_claims"]} == EXPECTED_COMPARABLE_CLAIMS
    assert {row["id"] for row in contract["non_comparable_claims"]} == EXPECTED_NON_COMPARABLE_CLAIMS
    assert all(row["status"] == "defined" for row in contract["comparable_claims"])
    assert all(row["status"] == "intentionally_non_comparable" for row in contract["non_comparable_claims"])


def test_protected_output_directories_are_rejected(tmp_path: Path) -> None:
    protected_dirs = [
        Path("reports/v5.8"),
        Path("reports/v5.7"),
        Path("reports/v7.4"),
        Path("reports/v8.0"),
        Path(".planning/milestones/v4"),
        Path("docs/reports/v4.6-verdict-resolution"),
        Path("data/v4"),
    ]

    for report_dir in protected_dirs:
        with pytest.raises(ValueError, match="protected output"):
            parity.build_backtest_runtime_accounting_parity_evidence(
                report_dir=report_dir,
                diff_base="HEAD",
            )

    allowed_report = parity.build_backtest_runtime_accounting_parity_evidence(
        report_dir=tmp_path,
        diff_base="HEAD",
    )
    assert allowed_report["protected_surface_guard"]["passed"] is True


def test_source_evidence_is_loaded_as_read_only_inputs(tmp_path: Path) -> None:
    report = parity.build_backtest_runtime_accounting_parity_evidence(
        report_dir=tmp_path,
        diff_base="origin/master",
    )

    sources = {row["path"]: row for row in report["source_evidence"]}
    assert set(sources) == EXPECTED_SOURCE_EVIDENCE_PATHS

    for path in EXPECTED_SOURCE_EVIDENCE_PATHS:
        source = sources[path]
        assert source["role"] == "read_only_input"
        assert source["exists"] is True
        assert source["sha256"] == sha256_file(ROOT / path)
        assert len(source["sha256"]) == 64

    assert report["checks"]["source_evidence_loaded"]["passed"] is True
    assert report["checks"]["source_evidence_loaded"]["source_count"] == len(
        EXPECTED_SOURCE_EVIDENCE_PATHS
    )


def test_source_evidence_paths_do_not_become_output_paths(tmp_path: Path) -> None:
    report = parity.build_backtest_runtime_accounting_parity_evidence(
        report_dir=tmp_path,
        diff_base="origin/master",
    )

    source_paths = {row["path"] for row in report["source_evidence"]}
    output_paths = set(report["protected_surface_guard"]["planned_output_paths"])

    assert source_paths.isdisjoint(output_paths)
    assert output_paths == {
        f"{tmp_path.as_posix()}/backtest_runtime_accounting_parity_evidence.json",
        f"{tmp_path.as_posix()}/backtest_runtime_accounting_parity_evidence.md",
    }


def test_parity_checks_cover_backtest_and_paper_evidence(tmp_path: Path) -> None:
    report = parity.build_backtest_runtime_accounting_parity_evidence(
        report_dir=tmp_path,
        diff_base="origin/master",
    )

    checks = report["checks"]
    assert checks["backtest_cap_runtime_sizing"]["passed"] is True
    assert checks["backtest_cap_runtime_sizing"]["requested_size_basis"] == "unit_backtest_run"
    assert checks["backtest_cap_runtime_sizing"]["effective_size_equals_allowed_size"] is True
    assert checks["backtest_cap_runtime_sizing"]["runtime_sizing_applied"] is True

    assert checks["paper_estimated_net_normal_exit"]["passed"] is True
    assert checks["paper_estimated_net_normal_exit"]["runtime_mode"] == "estimated_net"
    assert checks["paper_estimated_net_normal_exit"]["gross_surfaces_preserved"] is True

    assert checks["paper_estimated_net_liquidation"]["passed"] is True
    assert checks["paper_estimated_net_liquidation"]["liquidation_trigger_basis"] == "gross_unrealized"
    assert checks["paper_missing_or_zero_cost_fail_closed"]["passed"] is True
    assert checks["paper_missing_or_zero_cost_fail_closed"]["close_mutation_blocked"] is True

    assert checks["verification_scope"]["full_workspace"] == "skipped"
    assert checks["verification_scope"]["skip_reason"]


def test_render_markdown_has_claim_tables_and_no_raw_python_repr(tmp_path: Path) -> None:
    report = parity.build_backtest_runtime_accounting_parity_evidence(
        report_dir=tmp_path,
        diff_base="origin/master",
    )

    markdown = parity.render_markdown(report)

    assert "# Backtest/runtime Accounting Parity Evidence" in markdown
    assert "## Comparable Claims" in markdown
    assert "## Intentionally Non-Comparable Claims" in markdown
    assert "## Check Results" in markdown
    assert "backtest_cap_runtime_sizing" in markdown
    assert "paper_estimated_net_normal_exit" in markdown
    assert "paper_estimated_net_liquidation" in markdown
    assert "paper_missing_or_zero_cost_fail_closed" in markdown
    assert "protected_surface_guard" in markdown
    assert "reports/v5.11/backtest_risk_gate_closure_evidence.json" in markdown
    assert "{'" not in markdown
    assert "['" not in markdown


def test_cli_writes_fresh_v81_json_and_markdown(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--report-dir",
            str(tmp_path),
            "--diff-base",
            "origin/master",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    json_path = tmp_path / "backtest_runtime_accounting_parity_evidence.json"
    markdown_path = tmp_path / "backtest_runtime_accounting_parity_evidence.md"
    assert json_path.exists()
    assert markdown_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert payload["summary"]["overall_status"] == "PASS"
    assert payload["checks"]["protected_surface_guard"]["passed"] is True
    assert "## Check Results" in markdown_path.read_text(encoding="utf-8")
