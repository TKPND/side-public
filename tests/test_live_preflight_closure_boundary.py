"""Closure-boundary guards for Phase 154 live preflight stop gates."""

from __future__ import annotations

from pathlib import Path

from scripts.generate_risk_contract_v2_adoption_closure_audit import (
    LIVE_RUNTIME_IMPLEMENTATION_PATHS,
    LIVE_RUNTIME_SURFACE_SOURCE,
    live_runtime_surface_absent_check,
)


ROOT = Path(__file__).resolve().parents[1]
V8_7_PHASE_ARCHIVE_ROOT = Path(".planning/milestones/v8.7-phases")
V8_7_REQUIREMENTS_ARCHIVE = Path(".planning/milestones/v8.7-REQUIREMENTS.md")


def resolve_active_or_archived_path(relative_path: str | Path) -> Path:
    relative_path = Path(relative_path)
    if relative_path == Path(".planning/REQUIREMENTS.md"):
        archived_requirements = ROOT / V8_7_REQUIREMENTS_ARCHIVE
        if archived_requirements.is_file():
            return archived_requirements

    active_path = ROOT / relative_path
    if active_path.is_file():
        return active_path

    parts = relative_path.parts
    if parts[:2] == (".planning", "phases"):
        archived_phase_path = ROOT / V8_7_PHASE_ARCHIVE_ROOT / Path(*parts[2:])
        if archived_phase_path.is_file():
            return archived_phase_path
    return active_path


def read_text(relative_path: str | Path) -> str:
    return resolve_active_or_archived_path(relative_path).read_text(encoding="utf-8")


def test_archive_resolution_keeps_active_phase_artifacts_authoritative(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    active_phase_artifact = (
        tmp_path
        / ".planning/phases/154-closure-and-boundary-audit/154-CONTEXT.md"
    )
    archived_phase_artifact = (
        tmp_path
        / V8_7_PHASE_ARCHIVE_ROOT
        / "154-closure-and-boundary-audit/154-CONTEXT.md"
    )
    active_requirements = tmp_path / ".planning/REQUIREMENTS.md"
    archived_requirements = tmp_path / V8_7_REQUIREMENTS_ARCHIVE
    for path, text in (
        (active_phase_artifact, "active phase artifact"),
        (archived_phase_artifact, "archived phase artifact"),
        (active_requirements, "active requirements"),
        (archived_requirements, "archived v8.7 requirements"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    assert (
        resolve_active_or_archived_path(
            ".planning/phases/154-closure-and-boundary-audit/154-CONTEXT.md"
        )
        == active_phase_artifact
    )
    assert (
        resolve_active_or_archived_path(".planning/REQUIREMENTS.md")
        == archived_requirements
    )


def assert_contains(relative_path: str | Path, *needles: str) -> None:
    path = resolve_active_or_archived_path(relative_path)
    assert path.is_file(), f"missing required closure artifact: {relative_path}"
    text = path.read_text(encoding="utf-8")
    missing = [needle for needle in needles if needle not in text]
    assert not missing, f"{relative_path} missing expected evidence: {missing}"


def test_phase_152_and_153_gate_artifacts_are_verified() -> None:
    assert_contains(
        ".planning/phases/152-live-preflight-schema-hardening/152-VERIFICATION.md",
        "status: passed",
        "score: 22/22 must-haves verified",
    )
    assert_contains(
        ".planning/phases/152-live-preflight-schema-hardening/152-VALIDATION.md",
        "status: verified",
        "nyquist_compliant: true",
    )
    assert_contains(
        ".planning/phases/153-no-order-artifact-builder-and-guard/153-VERIFICATION.md",
        "status: passed",
        "score: 11/11 requirements verified",
    )
    assert_contains(
        ".planning/phases/153-no-order-artifact-builder-and-guard/153-VALIDATION.md",
        "status: verified",
        "nyquist_compliant: true",
    )
    assert_contains(
        ".planning/phases/153-no-order-artifact-builder-and-guard/153-SECURITY.md",
        "status: verified",
        "threats_open: 0",
    )


def test_live_runtime_cli_and_broker_paths_remain_absent_for_closure() -> None:
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


def test_v8_4_public_proof_baseline_is_canonical() -> None:
    audit = read_text("reports/v8.4/risk_contract_v2_consumer_contract_audit.md")
    invariance_test = read_text("tests/test_risk_contract_v2_public_proof_invariance.py")

    backtest_scan_fields = (
        "schema_version",
        "contract_version",
        "validator_result_schema_version",
        "schema_ref",
        "validated_schema_ref",
        "validator",
    )
    paper_fields = (
        "risk_contract_schema_version",
        "risk_contract_version",
        "validator_result_schema_version",
        "validated_schema_ref",
        "validator",
    )
    frozen_values = (
        "risk_contract.v2",
        "v2",
        "risk_contract_validator_result.v2",
        "risk/contracts/v2/risk_contract_v2.schema.json",
        "scripts/validate_risk_contract.py",
    )

    assert "PASS_FREEZE_CURRENT_PUBLIC_PROOF_CONTRACT" in audit
    assert "Do not normalize paper to the scan/backtest names in-place" in audit
    for literal in backtest_scan_fields + paper_fields + frozen_values:
        assert literal in audit

    assert (
        "test_backtest_and_scan_public_proof_fields_are_source_search_guarded"
        in invariance_test
    )
    assert "test_paper_public_proof_fields_are_source_search_guarded" in invariance_test
    assert (
        "test_public_proof_invariance_guard_reads_current_sources_not_snapshots"
        in invariance_test
    )


def test_optional_sanitized_account_projection_remains_future_only() -> None:
    requirements = read_text(".planning/REQUIREMENTS.md")
    roadmap = read_text(".planning/milestones/v8.7-ROADMAP.md")
    context = read_text(
        ".planning/phases/154-closure-and-boundary-audit/154-CONTEXT.md"
    )
    normalized_context = " ".join(context.split())

    assert "## Future Requirements" in requirements
    for req_id in ("FUT-ACCT-01", "FUT-ACCT-02", "FUT-ACCT-03"):
        assert req_id in requirements
    assert (
        "Optional sanitized account-proof projection beyond ref-only public handles "
        "is deferred to Future Requirements"
    ) in roadmap
    assert (
        "a separate later phase decision is required before any such projection is promoted"
        in normalized_context
    )
