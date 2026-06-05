"""Phase 161 closure guards for the v8.9 projector-only boundary."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from scripts.generate_risk_contract_v2_adoption_closure_audit import (
    LIVE_RUNTIME_IMPLEMENTATION_PATHS,
    LIVE_RUNTIME_SURFACE_SOURCE,
    live_runtime_surface_absent_check,
)


ROOT = Path(__file__).resolve().parents[1]
V8_9_PHASE_ARCHIVE_ROOT = Path(".planning/milestones/v8.9-phases")

PHASE_158_DIR = Path(
    ".planning/phases/158-account-proof-projector-contract-and-threat-model"
)
PHASE_159_DIR = Path(
    ".planning/phases/159-test-owned-projector-helper-and-rejection-matrix"
)
PHASE_160_DIR = Path(
    ".planning/phases/160-builder-integration-and-public-artifact-gates"
)
PHASE_161_DIR = Path(
    ".planning/phases/161-closure-absence-invariance-and-security-audit"
)

V8_4_PUBLIC_PROOF_AUDIT = Path(
    "reports/v8.4/risk_contract_v2_consumer_contract_audit.md"
)
PUBLIC_PROOF_INVARIANCE_TEST = Path(
    "tests/test_risk_contract_v2_public_proof_invariance.py"
)

PROJECTOR_ONLY_HELPERS = (
    Path("tests/helpers/live_preflight_account_proof_projector.py"),
    Path("tests/helpers/live_preflight_projector_builder.py"),
)

SAFE_PROJECTOR_HELPER_IMPORT_ROOTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "re",
    "tests",
    "typing",
}

RUNTIME_EXPANSION_PATHS = (
    "rust/side-cli/src/cmd/live",
    "rust/side-cli/src/cmd/broker",
    "rust/side-engine/src/live",
    "rust/side-engine/src/broker",
)

PROTECTED_CHANGED_PREFIXES = (
    "docs/examples/live_preflight/result_v1",
    "risk/contracts",
    "tests/v4_13/fixtures",
)

PROTECTED_VERSION_PREFIXES = (
    "data/v4.",
    "docs/reports/v4.",
    "reports/v4.",
    "reports/v5.7",
    "reports/v5.8",
    "reports/v8.",
)

PROTECTED_CHANGED_EXACT_PATHS = (
    "docs/contracts/live_preflight_result_v1.md",
    "docs/contracts/live_preflight_result_v1.schema.json",
    "Cargo.lock",
    "Cargo.toml",
    "pyproject.toml",
    "uv.lock",
)

def resolve_active_or_archived_path(relative_path: str | Path) -> Path:
    relative_path = Path(relative_path)
    active_path = ROOT / relative_path
    if active_path.is_file():
        return active_path

    parts = relative_path.parts
    if parts[:2] == (".planning", "phases"):
        archived_phase_path = ROOT / V8_9_PHASE_ARCHIVE_ROOT / Path(*parts[2:])
        if archived_phase_path.is_file():
            return archived_phase_path
    return active_path


def read_text(relative_path: str | Path) -> str:
    return resolve_active_or_archived_path(relative_path).read_text(encoding="utf-8")


def assert_contains(relative_path: str | Path, *needles: str) -> None:
    path = resolve_active_or_archived_path(relative_path)
    assert path.is_file(), f"missing required Phase 161 artifact: {relative_path}"
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


def assert_no_runtime_imports_or_calls(relative_path: Path) -> None:
    source = read_text(relative_path)
    imports = imported_modules(source)
    import_roots = {name.split(".")[0] for name in imports}
    unexpected_roots = import_roots - SAFE_PROJECTOR_HELPER_IMPORT_ROOTS
    assert not unexpected_roots, (
        f"{relative_path} imports unexpected roots: {unexpected_roots}"
    )

    forbidden_source_tokens = (
        "argparse",
        "asyncio",
        "broker_adapter",
        "broker_client",
        "broker_order",
        "credential_loader",
        "credential_client",
        "os.environ",
        "os.getenv",
        "side live",
        "submit_order",
        "cancel_order",
        "subprocess",
        'if __name__ == "__main__"',
    )
    found = [token for token in forbidden_source_tokens if token in source]
    assert not found, f"{relative_path} contains runtime source tokens: {found}"


def is_protected_change(path: str) -> bool:
    if path in PROTECTED_CHANGED_EXACT_PATHS:
        return True
    if path == ".planning/milestones" or path.startswith(".planning/milestones/"):
        return True
    if any(
        path == prefix or path.startswith(prefix)
        for prefix in PROTECTED_VERSION_PREFIXES
    ):
        return True
    for prefix in PROTECTED_CHANGED_PREFIXES:
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


def working_tree_changed_paths() -> list[str]:
    paths: set[str] = set()
    for command in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        proc = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        paths.update(line.strip() for line in proc.stdout.splitlines() if line.strip())
    return sorted(paths)


def test_archive_resolution_keeps_phase_161_artifacts_authoritative(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    active_artifact = (
        tmp_path
        / ".planning/phases/161-closure-absence-invariance-and-security-audit/161-CLOSURE-REPORT.md"
    )
    archived_artifact = (
        tmp_path
        / V8_9_PHASE_ARCHIVE_ROOT
        / "161-closure-absence-invariance-and-security-audit/161-CLOSURE-REPORT.md"
    )
    active_artifact.parent.mkdir(parents=True, exist_ok=True)
    archived_artifact.parent.mkdir(parents=True, exist_ok=True)
    active_artifact.write_text("active closure report", encoding="utf-8")
    archived_artifact.write_text("archived closure report", encoding="utf-8")

    relative_artifact = (
        ".planning/phases/161-closure-absence-invariance-and-security-audit/"
        "161-CLOSURE-REPORT.md"
    )
    assert resolve_active_or_archived_path(relative_artifact) == active_artifact

    active_artifact.unlink()
    assert resolve_active_or_archived_path(relative_artifact) == archived_artifact


def test_phase_161_closure_artifacts_are_present_and_closed() -> None:
    assert_contains(
        PHASE_161_DIR / "161-VALIDATION.md",
        "status: verified",
        "nyquist_compliant: true",
    )
    assert_contains(
        PHASE_161_DIR / "161-SECURITY.md",
        "status: secure",
        "threats_open: 0",
        "information disclosure",
        "spoofing",
        "tampering",
        "boundary creep",
    )
    assert_contains(
        PHASE_161_DIR / "161-VERIFICATION.md",
        "status: passed",
        "score: 6/6 requirements verified",
    )
    assert_contains(
        PHASE_161_DIR / "161-CLOSURE-REPORT.md",
        "SAP-SCOPE-03",
        "SAP-INVARIANCE-01",
        "SAP-PROTECTED-01",
        "SAP-CLOSURE-01",
        "SAP-CLOSURE-02",
        "SAP-CLOSURE-03",
    )


def test_phase_161_absence_matrix_binds_projector_only_scope() -> None:
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

    main_text = read_text(LIVE_RUNTIME_SURFACE_SOURCE)
    assert "Live(" not in main_text
    assert "Commands::Live" not in main_text

    result = live_runtime_surface_absent_check()
    assert result["passed"] is True
    assert result["source_exists"] is True
    assert result["live_subcommand_present"] is False
    assert result["live_runtime_paths"] == []

    for helper_path in PROJECTOR_ONLY_HELPERS:
        assert_no_runtime_imports_or_calls(helper_path)

    assert_contains(
        PHASE_158_DIR / "158-SECURITY.md",
        "real account fetcher",
        "broker execution",
        "credential/network path",
        "runtime public emission",
    )
    assert_contains(
        PHASE_159_DIR / "159-SECURITY.md",
        "Forbidden public/runtime/protected paths",
        "Runtime/network/credential/persistence/hash/public-mapping tokens",
    )
    assert_contains(
        PHASE_160_DIR / "160-01-SUMMARY.md",
        "No public schema/docs/examples",
        "live CLI/runtime",
        "broker/account-fetch/network/credential paths",
    )

    runtime_changes = [
        path
        for path in working_tree_changed_paths()
        if any(
            path == prefix or path.startswith(f"{prefix}/")
            for prefix in RUNTIME_EXPANSION_PATHS
        )
    ]
    assert runtime_changes == []


def test_phase_161_public_proof_invariance_gate_is_bound_to_current_sources() -> None:
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


def test_phase_161_working_tree_changes_exclude_protected_surfaces() -> None:
    protected_changes = [
        path for path in working_tree_changed_paths() if is_protected_change(path)
    ]

    assert protected_changes == []


def test_phase_161_protected_path_classifier_covers_canonical_roots() -> None:
    assert (
        is_protected_change(
            "reports/v8.4/risk_contract_v2_consumer_contract_audit.md"
        )
        is True
    )
    assert is_protected_change("reports/v4.13/diagnosis_v413_sources.json") is True
    assert is_protected_change("reports/v5.7-extra/closure.md") is True
    assert is_protected_change("reports/v5.8/closure.md") is True
    assert is_protected_change("docs/reports/v4.2-audusd/report.json") is True
    assert is_protected_change("data/v4.13/diagnosis_v413_sources.json") is True
    assert is_protected_change(".planning/milestones/v8.8-ROADMAP.md") is True
    assert is_protected_change("docs/contracts/live_preflight_result_v1.schema.json") is True
    assert is_protected_change(".planning/milestones/v8.9-REQUIREMENTS.md") is True
    assert is_protected_change(".planning/milestones/v8.9-ROADMAP.md") is True
    assert (
        is_protected_change(".planning/milestones/v8.9-MILESTONE-AUDIT.md")
        is True
    )
    assert (
        is_protected_change(
            ".planning/milestones/v8.9-phases/"
            "161-closure-absence-invariance-and-security-audit/"
            "161-VERIFICATION.md"
        )
        is True
    )
    assert is_protected_change(
        ".planning/phases/161-closure-absence-invariance-and-security-audit/"
        "161-VERIFICATION.md"
    ) is False


def test_phase_161_closure_report_records_future_boundaries_without_readiness_claims() -> None:
    report = read_text(PHASE_161_DIR / "161-CLOSURE-REPORT.md")
    normalized_report = " ".join(report.split())

    for expected_text in (
        "account fetching",
        "broker execution",
        "runtime public emission",
        "live operational readiness",
        "public schema expansion",
        "true Scan/WFD sizing",
        "warning/tooling cleanup",
        "remain future boundaries",
    ):
        assert expected_text in normalized_report

    for forbidden_text in (
        "safe for live operations",
        "production ready",
        "ready for live trading",
        "account fetcher implemented",
        "broker execution implemented",
        "profit evidence",
    ):
        assert forbidden_text not in normalized_report


def test_phase_161_closure_artifacts_bind_prior_phase_evidence_and_final_suite() -> None:
    assert_contains(
        PHASE_158_DIR / "158-SECURITY.md",
        "threats_open: 0",
        "information disclosure",
        "spoofing",
        "tampering",
        "boundary creep",
    )
    assert_contains(PHASE_159_DIR / "159-SECURITY.md", "threats_open: 0")
    assert_contains(
        PHASE_160_DIR / "160-01-SUMMARY.md",
        "Final verdict: **approve**",
        "no live/broker/account-fetch scope creep",
    )

    summary = read_text(PHASE_161_DIR / "161-01-SUMMARY.md")
    verification = read_text(PHASE_161_DIR / "161-VERIFICATION.md")
    for command in (
        "rtk uv run pytest -q tests/test_live_preflight_projector_closure_boundary.py",
        (
            "rtk uv run pytest -q tests/test_live_preflight_runtime_guard_closure.py "
            "tests/test_live_preflight_closure_boundary.py"
        ),
        "rtk uv run pytest -q tests/test_risk_contract_v2_public_proof_invariance.py",
        "rtk git diff --check",
    ):
        assert command in summary
        assert command in verification
