"""Contract tests for paper/live runtime sizing guards evidence generation."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import generate_paper_live_runtime_sizing_guards_evidence as guards


EVIDENCE_SCHEMA_VERSION = "paper_live_runtime_sizing_guards_evidence.v1"
EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version",
    "boundary",
    "summary",
    "source_evidence",
    "surfaces",
    "checks",
    "protected_surface_guard",
}
EXPECTED_SOURCE_EVIDENCE_PATHS = {
    "docs/plans/2026-05-14-paper-live-runtime-sizing-guards.md",
    "reports/v7.4/runtime_accounting_series_closure_audit.md",
    "reports/v8.1/backtest_runtime_accounting_parity_evidence.md",
    "rust/side-cli/src/main.rs",
    "rust/side-cli/src/cmd/paper.rs",
    "rust/side-engine/src/paper/risk.rs",
    "rust/side-cli/tests/paper_cli_test.rs",
    "rust/side-engine/tests/paper_risk_test.rs",
}

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_paper_live_runtime_sizing_guards_evidence.py"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_builds_top_level_guard_contract(tmp_path: Path) -> None:
    report = guards.build_paper_live_runtime_sizing_guards_evidence(
        report_dir=tmp_path,
        diff_base="HEAD",
    )

    assert guards.SCHEMA_VERSION == EVIDENCE_SCHEMA_VERSION
    assert guards.DEFAULT_REPORT_DIR == Path("reports/v8.2")
    assert set(report) == EXPECTED_TOP_LEVEL_KEYS
    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["boundary"] == "paper_live_runtime_sizing_guards"
    assert report["summary"]["overall_status"] == "PASS"
    assert report["summary"]["checks_failed"] == 0


def test_surfaces_record_paper_implemented_and_live_not_wired(tmp_path: Path) -> None:
    report = guards.build_paper_live_runtime_sizing_guards_evidence(
        report_dir=tmp_path,
        diff_base="origin/master",
    )

    surfaces = {row["surface"]: row for row in report["surfaces"]}
    assert set(surfaces) == {"paper", "live"}

    paper = surfaces["paper"]
    assert paper["surface_status"] == "implemented"
    assert paper["runtime_sizing_applied"] == "guarded_for_cap_apply"
    assert paper["runtime_accounting_mode"] == "legacy_gross_default_estimated_net_opt_in"
    assert paper["runtime_accounting_default_preserved"] is True
    assert paper["live_runtime_claim_allowed"] is False
    assert paper["claim_block_reason"] == "paper_surface_not_live"

    live = surfaces["live"]
    assert live["surface_status"] == "not_wired"
    assert live["runtime_sizing_applied"] == "not_applicable"
    assert live["runtime_accounting_mode"] == "not_implemented"
    assert live["runtime_accounting_default_preserved"] is True
    assert live["live_runtime_claim_allowed"] is False
    assert live["claim_block_reason"] == "live_runtime_not_implemented"


def test_source_evidence_is_read_only_and_disjoint_from_outputs(tmp_path: Path) -> None:
    report = guards.build_paper_live_runtime_sizing_guards_evidence(
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

    outputs = set(report["protected_surface_guard"]["planned_output_paths"])
    assert set(sources).isdisjoint(outputs)
    assert outputs == {
        f"{tmp_path.as_posix()}/paper_live_runtime_sizing_guards_evidence.json",
        f"{tmp_path.as_posix()}/paper_live_runtime_sizing_guards_evidence.md",
    }


def test_protected_output_directories_are_rejected(tmp_path: Path) -> None:
    protected_dirs = [
        Path("reports/v5.8"),
        Path("reports/v5.7"),
        Path("reports/v7.4"),
        Path("reports/v8.0"),
        Path("reports/v8.1"),
        Path(".planning/milestones/v4"),
        Path("docs/reports/v4.6-verdict-resolution"),
        Path("data/v4"),
    ]

    for report_dir in protected_dirs:
        with pytest.raises(ValueError, match="protected output"):
            guards.build_paper_live_runtime_sizing_guards_evidence(
                report_dir=report_dir,
                diff_base="HEAD",
            )

    allowed_report = guards.build_paper_live_runtime_sizing_guards_evidence(
        report_dir=tmp_path,
        diff_base="HEAD",
    )
    assert allowed_report["protected_surface_guard"]["passed"] is True


def test_checks_cover_runtime_guard_and_live_absence(tmp_path: Path) -> None:
    report = guards.build_paper_live_runtime_sizing_guards_evidence(
        report_dir=tmp_path,
        diff_base="origin/master",
    )
    checks = report["checks"]

    assert checks["source_evidence_loaded"]["passed"] is True
    assert checks["paper_runtime_guard_contract_defined"]["passed"] is True
    assert checks["paper_runtime_guard_contract_defined"]["expansion_guard"] is True
    assert checks["paper_runtime_guard_contract_defined"]["finite_non_negative_guard"] is True
    assert checks["paper_cli_apply_uses_runtime_guard"]["passed"] is True
    assert checks["paper_cli_apply_uses_runtime_guard"]["guard_called_before_override"] is True
    assert checks["live_cli_not_wired"]["passed"] is True
    assert checks["live_cli_not_wired"]["live_subcommand_present"] is False
    assert checks["runtime_accounting_default_preserved"]["passed"] is True
    assert checks["runtime_accounting_default_preserved"]["default_mode"] == "legacy_gross"


def test_render_markdown_has_surface_and_check_tables(tmp_path: Path) -> None:
    report = guards.build_paper_live_runtime_sizing_guards_evidence(
        report_dir=tmp_path,
        diff_base="origin/master",
    )

    markdown = guards.render_markdown(report)

    assert "# Paper/live Runtime Sizing Guards Evidence" in markdown
    assert "## Surface Contract" in markdown
    assert "paper_runtime_guard_contract_defined" in markdown
    assert "live_cli_not_wired" in markdown
    assert "live_runtime_not_implemented" in markdown
    assert "reports/v8.1/backtest_runtime_accounting_parity_evidence.md" in markdown
    assert "{'" not in markdown
    assert "['" not in markdown


def test_cli_writes_fresh_v82_json_and_markdown(tmp_path: Path) -> None:
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
    json_path = tmp_path / "paper_live_runtime_sizing_guards_evidence.json"
    markdown_path = tmp_path / "paper_live_runtime_sizing_guards_evidence.md"
    assert json_path.exists()
    assert markdown_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert payload["summary"]["overall_status"] == "PASS"
    assert payload["checks"]["protected_surface_guard"]["passed"] is True
    assert "## Check Results" in markdown_path.read_text(encoding="utf-8")
