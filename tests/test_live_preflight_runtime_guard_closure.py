"""Phase 157 closure guards for live preflight runtime boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

from scripts.generate_risk_contract_v2_adoption_closure_audit import (
    LIVE_RUNTIME_IMPLEMENTATION_PATHS,
    LIVE_RUNTIME_SURFACE_SOURCE,
    live_runtime_surface_absent_check,
)


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PHASES_ROOT = Path(".planning/phases")
ARCHIVED_PHASES_ROOT = Path(".planning/milestones/v8.8-phases")


def phase_dir(slug: str) -> Path:
    active_path = ACTIVE_PHASES_ROOT / slug
    if (ROOT / active_path).is_dir():
        return active_path
    return ARCHIVED_PHASES_ROOT / slug


PHASE_157_DIR = phase_dir("157-runtime-guard-closure-and-boundary-audit")
PHASE_155_DIR = phase_dir("155-live-no-order-guard-entrypoint-contract")
PHASE_156_DIR = phase_dir("156-guard-artifact-emission-and-public-proof-policy")
V8_4_PUBLIC_PROOF_AUDIT = Path(
    "reports/v8.4/risk_contract_v2_consumer_contract_audit.md"
)
PUBLIC_PROOF_INVARIANCE_TEST = Path(
    "tests/test_risk_contract_v2_public_proof_invariance.py"
)


def read_text(relative_path: str | Path) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_contains(relative_path: str | Path, *needles: str) -> None:
    path = ROOT / relative_path
    assert path.is_file(), f"missing required closure artifact: {relative_path}"
    text = path.read_text(encoding="utf-8")
    missing = [needle for needle in needles if needle not in text]
    assert not missing, f"{relative_path} missing expected evidence: {missing}"


def imported_modules(source: str) -> list[str]:
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def assert_helper_has_no_runtime_surface(relative_path: str | Path) -> None:
    source = read_text(relative_path)
    imports = imported_modules(source)
    forbidden_import_roots = {
        "requests",
        "urllib",
        "httpx",
        "socket",
        "scripts",
        "side_cli",
        "side_engine",
    }
    import_roots = {name.split(".")[0] for name in imports}
    overlap = forbidden_import_roots & import_roots
    assert not overlap, f"{relative_path} imports forbidden runtime roots: {overlap}"

    forbidden_text = {
        "argparse",
        "subprocess",
        "os.environ",
        "account_fetcher",
        "credential_loader",
        "credential_client",
        "broker_adapter",
        "broker_client",
        "side live",
        'if __name__ == "__main__"',
    }
    found = [text for text in sorted(forbidden_text) if text in source]
    assert not found, f"{relative_path} contains forbidden runtime text: {found}"


def test_phase_155_and_156_prerequisite_artifacts_are_verified() -> None:
    assert_contains(
        PHASE_155_DIR / "155-VERIFICATION.md",
        "status: passed",
        "score: 7/7 must-haves verified",
    )
    assert_contains(
        PHASE_155_DIR / "155-VALIDATION.md",
        "status: draft",
        "nyquist_compliant: true",
    )
    assert_contains(
        PHASE_156_DIR / "156-VERIFICATION.md",
        "status: passed",
        "score: 5/5 requirements verified",
    )
    assert_contains(
        PHASE_156_DIR / "156-VALIDATION.md",
        "status: verified",
        "nyquist_compliant: true",
    )
    assert_contains(
        PHASE_156_DIR / "156-SECURITY.md",
        "status: verified",
        "threats_open: 0",
    )


def test_phase_157_absence_matrix_remains_closed() -> None:
    expected_paths = (
        Path("rust/side-cli/src/cmd/live.rs"),
        Path("rust/side-cli/src/cmd/live"),
        Path("rust/side-cli/src/cmd/broker.rs"),
        Path("rust/side-cli/src/cmd/broker"),
        Path("rust/side-engine/src/live.rs"),
        Path("rust/side-engine/src/live"),
        Path("rust/side-engine/src/broker.rs"),
        Path("rust/side-engine/src/broker"),
    )

    assert LIVE_RUNTIME_SURFACE_SOURCE == Path("rust/side-cli/src/main.rs")
    assert tuple(LIVE_RUNTIME_IMPLEMENTATION_PATHS) == expected_paths

    main_source = ROOT / LIVE_RUNTIME_SURFACE_SOURCE
    assert main_source.is_file()
    main_text = main_source.read_text(encoding="utf-8")
    assert "Live(" not in main_text
    assert "Commands::Live" not in main_text

    result = live_runtime_surface_absent_check()
    assert result["passed"] is True
    assert result["source_exists"] is True
    assert result["live_subcommand_present"] is False
    assert result["live_runtime_paths"] == []

    for helper_path in (
        "tests/helpers/live_preflight_guard_entrypoint.py",
        "tests/helpers/live_preflight_guard_artifact_emission.py",
        "tests/helpers/live_preflight_proof_classification.py",
    ):
        assert_helper_has_no_runtime_surface(helper_path)

    closure_test_source = Path(__file__).read_text(encoding="utf-8")
    closure_imports = imported_modules(closure_test_source)
    allowed_scripts_import = "scripts.generate_risk_contract_v2_adoption_closure_audit"
    forbidden_scripts_imports = [
        name
        for name in closure_imports
        if name.startswith("scripts.") and name != allowed_scripts_import
    ]
    assert not forbidden_scripts_imports


def test_phase_157_binds_public_proof_invariance_gate() -> None:
    audit = read_text(V8_4_PUBLIC_PROOF_AUDIT)
    invariance_test = read_text(PUBLIC_PROOF_INVARIANCE_TEST)
    closure_test_source = Path(__file__).read_text(encoding="utf-8")

    assert "PASS_FREEZE_CURRENT_PUBLIC_PROOF_CONTRACT" in audit
    assert "Do not normalize paper to the scan/backtest names in-place" in audit
    for test_name in (
        "test_consumer_audit_freezes_current_public_proof_field_matrix",
        "test_backtest_and_scan_public_proof_fields_are_source_search_guarded",
        "test_paper_public_proof_fields_are_source_search_guarded",
        "test_public_proof_invariance_guard_reads_current_sources_not_snapshots",
    ):
        assert test_name in invariance_test

    assert V8_4_PUBLIC_PROOF_AUDIT.as_posix() in closure_test_source
    assert PUBLIC_PROOF_INVARIANCE_TEST.as_posix() in closure_test_source
    assert "BACKTEST" + "_SCAN_FIELDS =" not in closure_test_source
    assert "PAPER" + "_FIELDS =" not in closure_test_source


def test_phase_157_future_boundaries_remain_separate_in_context() -> None:
    context = read_text(PHASE_157_DIR / "157-CONTEXT.md")

    for expected_text in (
        "account snapshot fetching",
        "broker execution",
        "richer account-proof projection",
        "true Scan/WFD sizing",
        "warning/tooling cleanup",
        "remain unimplemented and separately gated",
    ):
        assert expected_text in context

    for forbidden_text in (
        "live operational readiness",
        "safe for live operations",
        "production ready",
    ):
        assert forbidden_text not in context


def test_phase_157_closure_report_records_stop_gates_and_handoff() -> None:
    report_path = ROOT / PHASE_157_DIR / "157-CLOSURE-REPORT.md"
    assert report_path.is_file()
    report = report_path.read_text(encoding="utf-8")

    for gate_label in (
        "Focused absence matrix gate",
        "Public proof invariance gate",
        "Phase 155 prerequisite gate",
        "Phase 156 prerequisite gate",
        "Focused closure suite gate",
        "Diff hygiene gate",
        "Future-boundary handoff gate",
    ):
        assert gate_label in report

    for requirement_id in (
        "LPRG-ABSENCE-01",
        "LPRG-INVARIANCE-01",
        "LPRG-CLOSURE-01",
    ):
        assert requirement_id in report

    assert "reports/v8.4/risk_contract_v2_consumer_contract_audit.md" in report
    assert "tests/test_risk_contract_v2_public_proof_invariance.py" in report
    assert "rtk uv run pytest -q tests/test_live_preflight_runtime_guard_closure.py" in report
    assert "rtk git diff --check" in report

    for expected_text in (
        "account snapshot fetching",
        "broker execution",
        "richer account-proof projection",
        "true Scan/WFD sizing",
        "warning/tooling cleanup",
        "remain unimplemented and separately gated",
    ):
        assert expected_text in report


def test_phase_157_closure_report_avoids_operational_readiness_claims() -> None:
    report = read_text(PHASE_157_DIR / "157-CLOSURE-REPORT.md")

    for forbidden_text in (
        "TBD",
        "TODO",
        "pending",
        "not run",
        "live operational readiness",
        "safe for live operations",
        "production ready",
        "account fetcher implemented",
        "broker execution implemented",
    ):
        assert forbidden_text not in report
