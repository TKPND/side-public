"""Closure guards for the v9.0 profit visibility decision report."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DECISION_REPORT = ROOT / "reports/v9.0/profit_visibility_decision_report.md"
PROFIT_VISIBILITY_REPORT = ROOT / "reports/v9.0/profit_visibility_report_v1.json"
PHASE169_DECISION_REPORT = ROOT / "reports/v9.1/profit_visibility_decision_report.md"
PHASE169_PROFIT_VISIBILITY_REPORT = (
    ROOT / "reports/v9.1/profit_visibility_report_v1_fixture.json"
)
PHASE169_REVIEW_BUNDLE_MANIFEST = (
    ROOT / "reports/v9.1/profit_visibility_review_bundle_manifest.json"
)
PHASE169_E2E_HELPER = ROOT / "scripts" / "profit_visibility_e2e_report.py"
PHASE165_DIR = (
    ROOT
    / ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review"
)
PHASE165_ARCHIVE_DIR = (
    ROOT
    / ".planning/milestones/v9.0-phases/"
    "165-decision-report-paper-forward-handoff-and-closure-review"
)
if not PHASE165_DIR.exists() and PHASE165_ARCHIVE_DIR.exists():
    PHASE165_DIR = PHASE165_ARCHIVE_DIR
REVIEW_EVIDENCE = PHASE165_DIR / "165-REVIEW-EVIDENCE.md"
REVIEW_RAW_RELATIVE = (
    ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review/"
    "165-REVIEW-RAW.md"
)
REVIEW_RAW_ARCHIVE_RELATIVE = (
    ".planning/milestones/v9.0-phases/"
    "165-decision-report-paper-forward-handoff-and-closure-review/"
    "165-REVIEW-RAW.md"
)
REVIEW_RAW_DELIMITER = "--- RAW REVIEW OUTPUT ---"
REVIEW_RAW = ROOT / (
    REVIEW_RAW_ARCHIVE_RELATIVE
    if (ROOT / REVIEW_RAW_ARCHIVE_RELATIVE).exists()
    else REVIEW_RAW_RELATIVE
)
PHASE165_PHASE_START_BASE = "84ec8ff5f2488716355de0296ca9f4267c9d33dd"
PHASE165_REVIEW_EVIDENCE_COMMIT = "923d1fb3867195b9db235c80f045e8d927636146"

REQUIRED_REPORT_SECTIONS = (
    "# v9.0 Profit Visibility Decision Report",
    "## Decision",
    "## Outcome Taxonomy",
    "## Evidence References",
    "## Family Outcomes",
    "## Next-Stage Claim Boundary",
    "## Live-Trading Claim Exclusions",
    "## Closure Guard Evidence",
    "## Requirements Coverage",
    "## Review Gate",
    "## Milestone Handoff",
    "## Non-Goals",
)
REQUIRED_DECISION_FIELDS = (
    "overall_outcome",
    "profit_visible = true/false",
    "survivor_count",
    "primary_stop_reason",
    "ProfitVisibilityReport.v1",
)
REQUIRED_OUTCOME_LABELS = (
    "profit_visible",
    "honest_null_ship",
    "plumbing_only",
    "invalid_disqualified",
)
REQUIRED_PHASE165_REQUIREMENTS = (
    "PVC-SCOPE-04",
    "PVC-PAPER-03",
    "PVC-CLOSURE-01",
    "PVC-CLOSURE-02",
    "PVC-CLOSURE-03",
)
STOP_REASON_CLOSURE_LABEL_MAP = {
    "phase165_reviewed_plumbing_only": "cost_incomplete",
}
PHASE169_STOP_REASON_CLOSURE_LABEL_MAP: dict[str, str] = {}
ALLOWED_NEXT_STAGE_PHRASES = (
    "eligible for paper-forward prerequisite review",
    "honest null-ship",
    "plumbing-only route with no profit claim",
)
FORBIDDEN_NEXT_STAGE_PHRASES = (
    "paper_forward_ready",
    "paper-forward ready",
    "paper ready",
    "live-shadow candidate",
    "shadow ready",
    "live ready",
    "live trading enabled",
    "live trading ready",
    "cleared for live trading",
    "approved to trade real money",
    "fit to deploy live",
    "greenlit for go-live",
    "go-live approved",
    "go live approved",
    "production ready",
    "broker ready",
    "account ready",
    "network ready",
    "credential ready",
    "ready for live",
    "ready to go live",
    "tiny live trade approved",
)
NEGATED_FORBIDDEN_CLAIM_PATTERNS = (
    r"\b(?:not|never|no)\s+{phrase}",
    r"\bdoes\s+not\s+(?:grant|approve)(?:\s+[A-Za-z0-9_][\w-]*){{0,1}}\s+{phrase}",
    r"\bmust\s+not\s+treat(?:\s+[A-Za-z0-9_][\w-]*){{0,1}}\s+as\s+{phrase}",
)
LIVE_TRADING_EXCLUSIONS = (
    "live account fetching",
    "credential/network paths",
    "broker mutation",
    "public schema expansion",
    "runtime public live emission",
    "tiny live trade approval",
)
PROTECTED_CHANGED_PREFIXES = (
    ".planning/milestones/",
    "data/v4",
    "docs/contracts/",
    "docs/reports/v4",
    "reports/v5.7",
    "reports/v5.8",
    "reports/v6.",
    "reports/v7.",
    "reports/v8.",
    "risk/contracts/",
)
PROTECTED_EXACT_PATHS = (
    "docs/contracts/live_preflight_result_v1.schema.json",
    "pyproject.toml",
    "uv.lock",
)
PROTECTED_NAME_PATTERNS = (
    re.compile(r"(^|[/_.-])golden([/_.-]|$)", re.IGNORECASE),
    re.compile(r"(^|[/_.-])seal(?:ed)?([/_.-]|$)", re.IGNORECASE),
    re.compile(r"(^|[/_.-])parity([/_.-]|$)", re.IGNORECASE),
    re.compile(r"(^|[/_.-])sha(?:256)?([/_.-]|$)", re.IGNORECASE),
)
PHASE165_ALLOWED_CHANGED_PATHS = frozenset(
    {
        "reports/v9.0/profit_visibility_decision_report.md",
        "tests/test_profit_visibility_closure.py",
        ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review/165-REVIEW-EVIDENCE.md",
        ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review/165-REVIEW-RAW.md",
        ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review/165-01-SUMMARY.md",
        ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review/165-02-SUMMARY.md",
        ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review/165-01-PLAN.md",
        ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review/165-02-PLAN.md",
        ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review/165-RESEARCH.md",
        ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review/165-VALIDATION.md",
        ".planning/phases/165-decision-report-paper-forward-handoff-and-closure-review/165-PATTERNS.md",
        ".planning/STATE.md",
        ".planning/ROADMAP.md",
        ".planning/REQUIREMENTS.md",
        "scripts/validate_profit_visibility_report.py",
    }
)
PHASE166_ALLOWED_WORKTREE_PATHS = frozenset(
    {
        "scripts/validate_profit_visibility_report.py",
        "tests/test_profit_visibility_report.py",
        "tests/test_profit_visibility_null_ship.py",
        "tests/test_profit_visibility_contract.py",
        "tests/test_profit_visibility_closure.py",
        ".planning/phases/166-claim-boundary-and-provenance-firewall/166-03-SUMMARY.md",
    }
)
PHASE166_PHASE_START_BASE = "9a61ae2d"
PHASE166_SCOPE_GUARD_ENV = "PHASE166_SCOPE_GUARD"
PHASE166_TRACKING_ARTIFACT_PREFIX = (
    ".planning/phases/166-claim-boundary-and-provenance-firewall/",
)
PHASE166_TRACKING_ARTIFACT_FILES = (
    ".planning/PROJECT.md",
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
)
PHASE167_ALLOWED_WORKTREE_PATHS = frozenset(
    {
        "scripts/profit_visibility_costed_metrics.py",
        "tests/test_profit_visibility_costed_metrics.py",
        "tests/test_profit_visibility_contract.py",
        "tests/test_profit_visibility_closure.py",
        ".planning/REQUIREMENTS.md",
        ".planning/ROADMAP.md",
        ".planning/STATE.md",
    }
)
PHASE167_PHASE_START_BASE = "4fd1341c"
PHASE167_SCOPE_GUARD_ENV = "PHASE167_SCOPE_GUARD"
PHASE168_ALLOWED_WORKTREE_PATHS = frozenset(
    {
        "scripts/profit_visibility_costed_metrics.py",
        "scripts/profit_visibility_statistical_evaluator.py",
        "tests/test_profit_visibility_costed_metrics.py",
        "tests/test_profit_visibility_statistical_evaluator.py",
        "tests/test_profit_visibility_contract.py",
        "tests/test_profit_visibility_closure.py",
        ".planning/REQUIREMENTS.md",
        ".planning/ROADMAP.md",
        ".planning/STATE.md",
    }
)
PHASE168_TRACKING_ARTIFACT_PREFIX = (
    ".planning/phases/168-permutation-and-holm-evaluation/",
)
PHASE168_TRACKING_ARTIFACT_FILES = (
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
)
PHASE168_PHASE_START_BASE = "c4702341"
PHASE168_SCOPE_GUARD_ENV = "PHASE168_SCOPE_GUARD"
PHASE167_TRACKING_ARTIFACT_PREFIX = (
    ".planning/phases/167-costed-economic-metric-engine/",
)
PHASE167_TRACKING_ARTIFACT_FILES = (
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
)
PHASE169_ALLOWED_WORKTREE_PATHS = frozenset(
    {
        "scripts/profit_visibility_e2e_report.py",
        "scripts/validate_profit_visibility_report.py",
        "tests/test_profit_visibility_e2e_report.py",
        "tests/test_profit_visibility_report.py",
        "tests/test_profit_visibility_contract.py",
        "tests/test_profit_visibility_closure.py",
        "reports/v9.1/profit_visibility_report_v1_fixture.json",
        "reports/v9.1/profit_visibility_decision_report.md",
        "reports/v9.1/profit_visibility_review_bundle_manifest.json",
    }
)
PHASE169_TRACKING_ARTIFACT_PREFIX = (
    ".planning/phases/169-e2e-report-routing-and-closure-proof/",
)
PHASE169_TRACKING_ARTIFACT_FILES = (
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
)
PHASE169_USER_OWNED_UNTRACKED_FILES = frozenset({".lean-ctx.toml"})
PHASE169_PROTECTED_CHANGED_PREFIXES = (
    "reports/v9.0/",
)
PHASE165_REQUIRED_CHANGED_PATHS = frozenset(
    {
        "reports/v9.0/profit_visibility_decision_report.md",
        "tests/test_profit_visibility_closure.py",
    }
)
REVIEW_VERDICT_VALUES = frozenset({"approve", "needs-attention"})
REVIEW_VERDICT_NORMALIZATION = (
    "review_tool_verdict_normalization: REVIEW-VERDICT: approve -> approve; "
    "REVIEW-VERDICT: needs-attention -> needs-attention"
)
REQUIRED_REVIEW_NOTE_SECTIONS = (
    "Local Verification Before Review",
    "Review Command",
    "Review Scope",
    "Review Focus",
    "Review Verdict",
    "Needs-Attention Handling",
    "Fixes And Re-Review",
    "Closure Decision",
    "Remaining Limits",
)
REQUIRED_REVIEW_NOTE_FRAGMENTS = (
    "$cc:adversarial-review",
    "decision evidence diff",
    "--scope branch",
    "--base",
    "review_result_reference",
    "review_command_provenance",
    "review_transcript_provenance",
    "verbatim_review_verdict_line",
    "review_tool_verdict_normalization",
    "closure_status",
    "anchor predates supported evaluation output",
    "claim-boundary wording",
    "absence of live/account/credential/network/broker/public-schema/"
    "protected-artifact drift",
)
EVIDENCE_REFERENCE_EXPECTATIONS = (
    (
        Path("docs/contracts/profit_visibility_contract_v1.md"),
        ("no-live scope", "OpenTimestamps", "FWER/Holm"),
    ),
    (
        Path("docs/contracts/profit_visibility_registration_protocol_v1.md"),
        (
            "OpenTimestamps",
            "pre_anchor_result_bearing_runs",
            "FWER/Holm",
            "supported evaluation run",
        ),
    ),
    (
        Path("docs/contracts/profit_visibility_cost_model_v1.md"),
        ("fees", "spread", "slippage", "not paper-forward readiness"),
    ),
    (
        Path("docs/contracts/profit_visibility_report_v1.md"),
        ("Registration Anchor Gate", "FWER/Holm", "Paper-Forward Prerequisite Mapping"),
    ),
    (
        Path(
            ".planning/phases/164-profit-visibility-evidence-generator-and-null-ship-gate/164-01-SUMMARY.md"
        ),
        ("ProfitVisibilityReport.v1", "canonical hypothesis rows", "typed-null"),
    ),
    (
        Path(
            ".planning/phases/164-profit-visibility-evidence-generator-and-null-ship-gate/164-02-SUMMARY.md"
        ),
        ("registration anchor", "exact-denominator FWER/Holm", "p-value provenance"),
    ),
    (
        Path(
            ".planning/phases/164-profit-visibility-evidence-generator-and-null-ship-gate/164-03-SUMMARY.md"
        ),
        ("Invalid registration or anchor evidence", "honest_null_ship", "family/overall outcome"),
    ),
    (
        Path(
            ".planning/phases/164-profit-visibility-evidence-generator-and-null-ship-gate/164-04-SUMMARY.md"
        ),
        ("paper-forward prerequisite mapping", "divergence", "Claim wording"),
    ),
    (
        Path("tests/test_profit_visibility_report.py"),
        ("validate_registration_anchor", "apply_holm_fwer", "paper-forward"),
    ),
    (
        Path("tests/test_profit_visibility_null_ship.py"),
        ("derive_family_outcome", "derive_overall_outcome", "honest_null_ship"),
    ),
    (
        Path("tests/test_profit_visibility_registration.py"),
        ("OpenTimestamps", "pre_anchor_result_bearing_runs", "FWER/Holm"),
    ),
    (
        Path("tests/test_profit_visibility_cost_model.py"),
        ("fee", "spread", "slippage"),
    ),
    (
        Path("tests/test_profit_visibility_contract.py"),
        ("profit_visible", "honest_null_ship", "plumbing_only"),
    ),
)
PHASE169_REVIEW_BUNDLE_SCOPE = (
    "fixture-only profit visibility report routing and closure proof"
)
PHASE169_REVIEW_BUNDLE_REQUIRED_PATHS = frozenset(
    {
        "reports/v9.1/profit_visibility_report_v1_fixture.json",
        "reports/v9.1/profit_visibility_decision_report.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-CONTEXT.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-RESEARCH.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-VALIDATION.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-PATTERNS.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-01-PLAN.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-02-PLAN.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-03-PLAN.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-04-PLAN.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-01-SUMMARY.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-02-SUMMARY.md",
        ".planning/phases/168-permutation-and-holm-evaluation/168-VERIFICATION.md",
        ".planning/reviews/v9.1/next-milestone-decision-gpt-pro-review.md",
        "docs/contracts/profit_visibility_contract_v1.md",
        "docs/contracts/profit_visibility_registration_protocol_v1.md",
        "docs/contracts/profit_visibility_cost_model_v1.md",
        "docs/contracts/profit_visibility_report_v1.md",
        "scripts/profit_visibility_e2e_report.py",
        "scripts/profit_visibility_statistical_evaluator.py",
        "scripts/validate_profit_visibility_report.py",
        "tests/test_profit_visibility_e2e_report.py",
        "tests/test_profit_visibility_report.py",
        "tests/test_profit_visibility_contract.py",
        "tests/test_profit_visibility_closure.py",
        "tests/test_risk_contract_v2_public_proof_invariance.py",
        "tests/test_live_preflight_result_contract.py",
        "AGENTS.md",
        ".agents/skills/side-cc-workflow/SKILL.md",
        ".agents/skills/side-review-verify/SKILL.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-REVIEW-EVIDENCE.md",
    }
)
PHASE169_DEFERRED_REVIEW_EVIDENCE_PATH = (
    ".planning/phases/169-e2e-report-routing-and-closure-proof/"
    "169-REVIEW-EVIDENCE.md"
)
PHASE169_REVIEW_EVIDENCE = ROOT / PHASE169_DEFERRED_REVIEW_EVIDENCE_PATH
PHASE169_REVIEW_VERDICT_VALUES = frozenset(
    {"approve", "needs-attention", "not-run-tooling-blocked"}
)
PHASE169_REVIEW_EVIDENCE_REQUIRED_SECTIONS = (
    "Review Command",
    "Review Scope",
    "Review Focus",
    "Review Verdict",
    "Material Blockers",
    "Needs-Attention Handling",
    "Fixes Or Override",
    "Local Verification Basis",
    "Final Verification Gates",
    "Remaining Limits",
)
PHASE169_REVIEW_EVIDENCE_REQUIRED_FRAGMENTS = (
    "$cc:adversarial-review --effort high --scope working-tree",
    "synthetic/real firewall",
    "fake anchor or fingerprint normalization",
    "no-go re-entry",
    "report/payload drift",
    "review-bundle reproducibility",
    "claim creep",
    "review_result_reference",
    "review_command_provenance",
    "canonical_verdict",
    "closure_status",
)
PHASE169_FINAL_VERIFICATION_REQUIRED_FRAGMENTS = (
    "uv run pytest -q tests/test_profit_visibility_e2e_report.py",
    "uv run python scripts/profit_visibility_e2e_report.py --fixture all --output reports/v9.1/profit_visibility_report_v1_fixture.json --pretty",
    "uv run python scripts/validate_profit_visibility_report.py reports/v9.1/profit_visibility_report_v1_fixture.json",
    "uv run pytest -q tests/test_profit_visibility_closure.py::test_phase169_decision_report_stop_reason_matches_generated_payload tests/test_profit_visibility_closure.py::test_phase169_unmapped_stop_reason_labels_fail_closure_guard tests/test_profit_visibility_report.py::test_phase169_fixture_report_claim_wording_rejects_readiness_claims",
    "uv run pytest -q tests/test_profit_visibility_closure.py::test_phase169_review_bundle_manifest_lists_existing_required_files tests/test_profit_visibility_closure.py::test_phase169_review_bundle_manifest_missing_files_fail",
    "uv run pytest -q tests/test_profit_visibility_contract.py::test_phase169_forbidden_surfaces_and_source_ingest_absent tests/test_profit_visibility_closure.py::test_phase169_closure_rejects_public_schema_and_protected_drift",
    "PHASE169_SCOPE_GUARD=1 uv run pytest -q tests/test_profit_visibility_contract.py::test_phase169_scope_guard_allows_only_planned_changed_paths",
    "uv run pytest -q tests/test_risk_contract_v2_public_proof_invariance.py tests/test_live_preflight_result_contract.py",
    'node "$HOME/.codex/gsd-core/bin/gsd-tools.cjs" query audit-open',
    "git diff --check",
    "git status --short",
    "clean-worktree evidence is collected after committing intended Plan 04 files",
    ".lean-ctx.toml is user-owned untracked and excluded from the intended-worktree cleanliness claim",
)


def read_decision_report() -> str:
    return DECISION_REPORT.read_text(encoding="utf-8")


def read_phase169_decision_report() -> str:
    return PHASE169_DECISION_REPORT.read_text(encoding="utf-8")


def read_phase169_review_evidence() -> str:
    return PHASE169_REVIEW_EVIDENCE.read_text(encoding="utf-8")


def load_phase169_e2e_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "profit_visibility_e2e_report", PHASE169_E2E_HELPER
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_contains_all(text: str, fragments: tuple[str, ...]) -> None:
    missing = [fragment for fragment in fragments if fragment not in text]
    assert not missing, f"missing fragments: {missing}"


def resolve_review_bundle_path(path_value: str, *, root: Path = ROOT) -> Path:
    relative_path = Path(path_value)
    assert not relative_path.is_absolute(), (
        f"review bundle path must be relative: {path_value}"
    )
    assert ".." not in relative_path.parts, (
        f"review bundle path must not contain '..': {path_value}"
    )
    resolved_root = root.resolve()
    resolved_path = (resolved_root / relative_path).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise AssertionError(
            f"review bundle path escapes repository: {path_value}"
        ) from exc
    return resolved_path


def assert_phase169_review_bundle_manifest(
    manifest: dict[str, object],
    *,
    root: Path = ROOT,
) -> None:
    assert manifest["version"] == 1
    assert manifest["phase"] == 169
    assert manifest["bundle_scope"] == PHASE169_REVIEW_BUNDLE_SCOPE

    required_files = manifest.get("required_files")
    assert isinstance(required_files, list) and required_files
    for entry in required_files:
        assert isinstance(entry, dict), f"invalid manifest entry: {entry!r}"
        path_value = entry.get("path")
        assert isinstance(path_value, str) and path_value
        path = resolve_review_bundle_path(path_value, root=root)
        assert path.is_file(), f"missing review bundle file: {path_value}"

        required_markers = entry.get("required_markers", ())
        assert isinstance(required_markers, list)
        assert required_markers, f"missing marker list for {path_value}"
        assert all(
            isinstance(marker, str) and marker for marker in required_markers
        ), f"invalid markers for {path_value}: {required_markers}"
        text = path.read_text(encoding="utf-8")
        missing = [marker for marker in required_markers if marker not in text]
        assert not missing, f"missing markers for {path_value}: {missing}"

    deferred_files = manifest.get("deferred_files", [])
    assert isinstance(deferred_files, list)
    for entry in deferred_files:
        assert isinstance(entry, dict), f"invalid deferred manifest entry: {entry!r}"
        path_value = entry.get("path")
        assert isinstance(path_value, str) and path_value
        resolve_review_bundle_path(path_value, root=root)
        assert isinstance(entry.get("deferred_until_plan"), str)


def evidence_reference_path(relative_path: Path) -> Path:
    path = ROOT / relative_path
    if path.is_file():
        return path
    phase_prefix = Path(".planning/phases")
    if relative_path.parts[:2] == phase_prefix.parts:
        archived = (
            ROOT
            / ".planning/milestones/v9.0-phases"
            / Path(*relative_path.parts[2:])
        )
        if archived.is_file():
            return archived
    return path


def markdown_section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$",
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, f"missing markdown section: {heading}"
    tail = text[match.end() :]
    next_heading = re.search(r"^##\s+", tail, flags=re.MULTILINE)
    if next_heading is not None:
        tail = tail[: next_heading.start()]
    return tail


def decision_stop_reason_from_report(text: str) -> str:
    decision = markdown_section(text, "Decision")
    matches = re.findall(
        r"^\|\s*`primary_stop_reason`\s*\|\s*`([^`]+)`\s*\|",
        decision,
        flags=re.MULTILINE,
    )
    assert len(matches) == 1, f"expected exactly one decision stop reason: {matches}"
    return matches[0]


def family_stop_reasons_from_report(text: str) -> tuple[str, ...]:
    family_section = markdown_section(text, "Family Outcomes")
    reasons: list[str] = []
    for line in family_section.splitlines():
        if not line.startswith("|") or "---" in line or "primary_stop_reason" in line:
            continue
        columns = [column.strip().strip("`") for column in line.strip("|").split("|")]
        if len(columns) >= 4 and columns[3]:
            reasons.append(columns[3])
    assert reasons, "family stop reasons must be present"
    return tuple(reasons)


def payload_stop_reasons(payload: dict[str, object]) -> set[str]:
    reasons: set[str] = set()

    def add_reason(value: object) -> None:
        if isinstance(value, str) and value:
            reasons.add(value)

    add_reason(payload.get("primary_stop_reason"))
    for row in payload.get("candidate_rows", []):
        assert isinstance(row, dict)
        add_reason(row.get("primary_stop_reason"))
    for family in payload.get("family_summaries", []):
        assert isinstance(family, dict)
        add_reason(family.get("primary_stop_reason"))

    assert reasons, "payload must carry stop reasons"
    return reasons


def assert_stop_reasons_match_payload_or_mapped_label(
    report_text: str,
    payload: dict[str, object],
    *,
    label_map: dict[str, str] = STOP_REASON_CLOSURE_LABEL_MAP,
) -> None:
    payload_reasons = payload_stop_reasons(payload)
    report_reasons = (
        decision_stop_reason_from_report(report_text),
        *family_stop_reasons_from_report(report_text),
    )
    unmapped = []
    for reason in report_reasons:
        mapped_reason = label_map.get(reason, reason)
        if mapped_reason not in payload_reasons:
            unmapped.append(reason)
    assert unmapped == []


def review_note_section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$",
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, f"missing review note section: {heading}"
    tail = text[match.end() :]
    next_heading = re.search(r"^##\s+", tail, flags=re.MULTILINE)
    if next_heading is not None:
        tail = tail[: next_heading.start()]
    return tail.strip()


def _optional_review_note_section(text: str, heading: str) -> str:
    try:
        return review_note_section(text, heading)
    except AssertionError:
        return ""


def _field_values(section: str, key: str) -> list[str]:
    return [
        value.strip()
        for value in re.findall(
            rf"^{re.escape(key)}:\s*(.+?)\s*$",
            section,
            flags=re.MULTILINE,
        )
    ]


def _single_field(section: str, key: str) -> str:
    values = _field_values(section, key)
    assert len(values) == 1, f"expected exactly one {key}, got {values}"
    return values[0]


def review_verdict_from_note(text: str) -> str:
    verdict_section = review_note_section(text, "Review Verdict")
    values = _field_values(verdict_section, "verdict")
    assert len(values) == 1, f"expected exactly one verdict token, got {values}"
    verdict = values[0]
    assert verdict in REVIEW_VERDICT_VALUES, f"unknown review verdict: {verdict}"
    return verdict


def phase169_canonical_verdict_from_note(text: str) -> str:
    verdict_section = review_note_section(text, "Review Verdict")
    values = _field_values(verdict_section, "canonical_verdict")
    assert len(values) == 1, f"expected exactly one canonical verdict, got {values}"
    verdict = values[0]
    assert verdict in PHASE169_REVIEW_VERDICT_VALUES, (
        f"unknown Phase 169 review verdict: {verdict}"
    )
    return verdict


def phase169_closure_status_from_note(text: str) -> str:
    verdict_section = review_note_section(text, "Review Verdict")
    return _single_field(verdict_section, "closure_status")


def _has_phase169_structured_fix_or_override(text: str) -> bool:
    fixes = review_note_section(text, "Fixes Or Override")
    has_fixes = all(
        token in fixes
        for token in (
            "fixes_applied:",
            "supporting_evidence_refs:",
            "post_fix_verification:",
        )
    )
    has_override = all(
        token in fixes
        for token in (
            "override_reason:",
            "supporting_evidence_refs:",
            "approved_by:",
        )
    )
    return has_fixes or has_override


def phase169_review_note_has_required_verdict_handling(text: str) -> bool:
    verdict = phase169_canonical_verdict_from_note(text)
    closure_status = phase169_closure_status_from_note(text)
    if verdict == "needs-attention":
        return (
            _has_phase169_structured_fix_or_override(text)
            and closure_status == "closed-after-fixes-local-verification"
        )
    if verdict == "not-run-tooling-blocked":
        blocker = review_note_section(text, "Needs-Attention Handling")
        local_basis = review_note_section(text, "Local Verification Basis")
        return (
            "tooling_blocker:" in blocker
            and "local_verification_basis:" in local_basis
            and closure_status == "external-review-blocked-local-verification-only"
        )
    return closure_status == "closed"


def review_result_reference_path(text: str, *, root: Path = ROOT) -> Path:
    verdict_section = review_note_section(text, "Review Verdict")
    reference = _single_field(verdict_section, "review_result_reference")
    assert reference in {REVIEW_RAW_RELATIVE, REVIEW_RAW_ARCHIVE_RELATIVE}
    path = root / reference
    if not path.is_file() and reference == REVIEW_RAW_RELATIVE:
        path = root / REVIEW_RAW_ARCHIVE_RELATIVE
    assert path.is_file(), f"missing review result reference: {reference}"
    return path


def _split_raw_review(text: str) -> tuple[str, str]:
    assert REVIEW_RAW_DELIMITER in text, "missing raw review delimiter"
    prelude, body = text.split(REVIEW_RAW_DELIMITER, 1)
    return prelude, body


def review_artifact_verdict_from_reference(text: str, *, root: Path = ROOT) -> str:
    raw = review_result_reference_path(text, root=root).read_text(encoding="utf-8")
    prelude, _ = _split_raw_review(raw)
    prelude_lines = [line.strip() for line in prelude.splitlines() if line.strip()]
    assert prelude_lines, "raw review prelude is empty"
    verdict_lines = [
        line for line in prelude_lines if line.startswith("REVIEW-VERDICT:")
    ]
    assert len(verdict_lines) == 1, f"invalid raw verdict prelude: {verdict_lines}"
    assert prelude_lines[0] == verdict_lines[0]
    match = re.fullmatch(
        r"REVIEW-VERDICT: (approve|needs-attention)",
        verdict_lines[0],
    )
    assert match is not None, f"unknown raw review verdict: {verdict_lines[0]}"
    return match.group(1)


def transcript_top_level_verdict(text: str) -> str:
    body = _split_raw_review(text)[1] if REVIEW_RAW_DELIMITER in text else text
    stripped = body.lstrip()
    non_empty_lines = [line.strip() for line in body.splitlines() if line.strip()]
    assert non_empty_lines, "raw review transcript body is empty"

    verdicts: list[str] = []
    if stripped.startswith("{"):
        try:
            parsed, _ = json.JSONDecoder().raw_decode(stripped)
        except json.JSONDecodeError as exc:
            raise AssertionError("invalid top-level JSON review transcript") from exc
        assert isinstance(parsed, dict), "top-level review JSON must be an object"
        verdict = parsed.get("verdict")
        assert isinstance(verdict, str), "top-level review JSON lacks verdict"
        assert verdict in REVIEW_VERDICT_VALUES, f"unknown JSON verdict: {verdict}"
        verdicts.append(verdict)

    if non_empty_lines[0].startswith("Verdict:"):
        if len(non_empty_lines) > 1 and non_empty_lines[1].startswith("Verdict:"):
            raise AssertionError(
                f"multiple transcript verdicts: {non_empty_lines[:2]}"
            )
        match = re.fullmatch(r"Verdict: (approve|needs-attention)", non_empty_lines[0])
        assert match is not None, f"unknown transcript verdict: {non_empty_lines[0]}"
        verdicts.append(match.group(1))
    else:
        later_verdict_lines = [
            line for line in non_empty_lines[1:] if line.startswith("Verdict:")
        ]
        assert not later_verdict_lines, (
            "Verdict line must be the first non-empty transcript line"
        )

    assert len(verdicts) == 1, f"expected exactly one transcript verdict: {verdicts}"
    return verdicts[0]


def _has_structured_fix_or_override(text: str) -> bool:
    fixes = _optional_review_note_section(text, "Fixes And Re-Review")
    closure = _optional_review_note_section(text, "Closure Decision")
    needs_attention = _optional_review_note_section(text, "Needs-Attention Handling")
    joined = "\n".join((fixes, closure, needs_attention))
    has_fix_and_rereview = all(
        token in fixes
        for token in (
            "fix_commit:",
            "re_review_result_reference:",
            "re_review_verdict: approve",
        )
    )
    has_override = all(
        token in joined
        for token in (
            "override_reason:",
            "supporting_evidence_refs:",
            "approved_by:",
        )
    )
    return has_fix_and_rereview or has_override


def review_note_blocks_closure(text: str, *, root: Path = ROOT) -> bool:
    try:
        note_verdict = review_verdict_from_note(text)
        raw_path = review_result_reference_path(text, root=root)
        raw_text = raw_path.read_text(encoding="utf-8")
        raw_verdict = review_artifact_verdict_from_reference(text, root=root)
        transcript_verdict = transcript_top_level_verdict(raw_text)
        verdict_section = review_note_section(text, "Review Verdict")
        verbatim_line = _single_field(
            verdict_section,
            "verbatim_review_verdict_line",
        )
    except AssertionError:
        return True

    if verbatim_line != f"REVIEW-VERDICT: {raw_verdict}":
        return True
    if REVIEW_VERDICT_NORMALIZATION not in verdict_section:
        return True
    if {note_verdict, raw_verdict, transcript_verdict} != {note_verdict}:
        return True
    if transcript_verdict == "needs-attention":
        return not _has_structured_fix_or_override(text)
    return False


def report_claim_sections(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines())


def _phrase_pattern(phrase: str) -> str:
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    return rf"(?<![\w-]){escaped}(?![\w-])"


def _is_exempted_occurrence(line: str, phrase: str, start: int, end: int) -> bool:
    phrase_pattern = _phrase_pattern(phrase)
    for template in NEGATED_FORBIDDEN_CLAIM_PATTERNS:
        assert ".*" not in template
        pattern = template.format(phrase=phrase_pattern)
        for match in re.finditer(pattern, line, flags=re.IGNORECASE):
            if match.start() <= start and match.end() >= end:
                return True
    return False


def forbidden_claim_matches(text: str) -> list[str]:
    matches: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for phrase in FORBIDDEN_NEXT_STAGE_PHRASES:
            pattern = _phrase_pattern(phrase)
            for match in re.finditer(pattern, line, flags=re.IGNORECASE):
                if not _is_exempted_occurrence(
                    line,
                    phrase,
                    match.start(),
                    match.end(),
                ):
                    matches.append(f"line {line_number}: {match.group(0)}")
    return matches


def _git_lines(args: list[str], *, check: bool = True) -> tuple[str, ...]:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return ()
    return tuple(line for line in proc.stdout.splitlines() if line)


def resolve_phase165_phase_start_base() -> str:
    env_base = os.environ.get("PHASE165_PHASE_START_BASE")
    if env_base is None:
        base = PHASE165_PHASE_START_BASE
    else:
        base = env_base
        assert base == PHASE165_PHASE_START_BASE
    assert os.environ.get("PHASE165_DIFF_BASE") in {None, ""}

    head = _git_lines(["rev-parse", "HEAD"])[0]
    assert base != head
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, "HEAD"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    committed_diff = _git_lines(["diff", "--name-only", f"{base}..HEAD"])
    if not committed_diff:
        assert (
            os.environ.get("PHASE165_ALLOW_EMPTY_DIFF_FOR_RED") == "1"
            and not DECISION_REPORT.exists()
        )
    return base


def resolve_phase165_diff_base() -> str:
    return resolve_phase165_phase_start_base()


def resolve_phase165_diff_end() -> str:
    end = PHASE165_REVIEW_EVIDENCE_COMMIT
    head = _git_lines(["rev-parse", "HEAD"])[0]
    assert end != head
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", end, "HEAD"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return end


def changed_paths_from_git(base: str, end: str | None = None) -> tuple[str, ...]:
    diff_end = "HEAD" if end is None else end
    return tuple(sorted(_git_lines(["diff", "--name-only", f"{base}..{diff_end}"])))


def working_tree_changed_paths() -> tuple[str, ...]:
    paths = set(_git_lines(["diff", "--name-only"]))
    paths.update(_git_lines(["diff", "--cached", "--name-only"]))
    paths.update(_git_lines(["ls-files", "--others", "--exclude-standard"]))
    return tuple(sorted(paths))


def protected_path_violations(paths: tuple[str, ...] | list[str]) -> list[str]:
    violations = []
    for path in paths:
        if path in PROTECTED_EXACT_PATHS:
            violations.append(path)
            continue
        if any(path.startswith(prefix) for prefix in PROTECTED_CHANGED_PREFIXES):
            violations.append(path)
            continue
        if any(pattern.search(path) for pattern in PROTECTED_NAME_PATTERNS):
            violations.append(path)
    return sorted(violations)


def unexpected_phase165_changed_paths(paths: tuple[str, ...] | list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path not in PHASE165_ALLOWED_CHANGED_PATHS
    )


def missing_phase165_required_changed_paths(
    paths: tuple[str, ...] | list[str],
) -> list[str]:
    return sorted(PHASE165_REQUIRED_CHANGED_PATHS - set(paths))


def is_phase166_tracking_artifact(path: str) -> bool:
    return path in PHASE166_TRACKING_ARTIFACT_FILES or path.startswith(
        PHASE166_TRACKING_ARTIFACT_PREFIX
    )


def is_phase167_tracking_artifact(path: str) -> bool:
    return path in PHASE167_TRACKING_ARTIFACT_FILES or path.startswith(
        PHASE167_TRACKING_ARTIFACT_PREFIX
    )


def is_phase168_tracking_artifact(path: str) -> bool:
    return path in PHASE168_TRACKING_ARTIFACT_FILES or path.startswith(
        PHASE168_TRACKING_ARTIFACT_PREFIX
    )


def is_phase169_tracking_artifact(path: str) -> bool:
    return path in PHASE169_TRACKING_ARTIFACT_FILES or path.startswith(
        PHASE169_TRACKING_ARTIFACT_PREFIX
    )


def resolve_phase166_diff_base() -> str:
    diff_base = os.environ.get("PHASE166_DIFF_BASE") or PHASE166_PHASE_START_BASE
    resolved = _git_lines(["rev-parse", "--verify", f"{diff_base}^{{commit}}"])[0]
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", resolved, "HEAD"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert changed_paths_from_git(resolved), (
        "PHASE166_DIFF_BASE must produce a non-empty phase diff"
    )
    return resolved


def phase166_changed_paths_for_scope_guard() -> tuple[str, ...]:
    base = resolve_phase166_diff_base()
    paths = set(changed_paths_from_git(base))
    paths.update(working_tree_changed_paths())
    return tuple(sorted(paths))


def resolve_phase167_diff_base() -> str:
    diff_base = os.environ.get("PHASE167_DIFF_BASE") or PHASE167_PHASE_START_BASE
    resolved = _git_lines(["rev-parse", "--verify", f"{diff_base}^{{commit}}"])[0]
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", resolved, "HEAD"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert changed_paths_from_git(resolved), (
        "PHASE167_DIFF_BASE must produce a non-empty phase diff"
    )
    return resolved


def phase167_changed_paths_for_scope_guard() -> tuple[str, ...]:
    base = resolve_phase167_diff_base()
    paths = set(changed_paths_from_git(base))
    paths.update(working_tree_changed_paths())
    return tuple(sorted(paths))


def resolve_phase168_diff_base() -> str:
    diff_base = os.environ.get("PHASE168_DIFF_BASE") or PHASE168_PHASE_START_BASE
    resolved = _git_lines(["rev-parse", "--verify", f"{diff_base}^{{commit}}"])[0]
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", resolved, "HEAD"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert changed_paths_from_git(resolved), (
        "PHASE168_DIFF_BASE must produce a non-empty phase diff"
    )
    return resolved


def phase168_changed_paths_for_scope_guard() -> tuple[str, ...]:
    base = resolve_phase168_diff_base()
    paths = set(changed_paths_from_git(base))
    paths.update(working_tree_changed_paths())
    return tuple(sorted(paths))


def unexpected_phase166_changed_paths(paths: tuple[str, ...] | list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path not in PHASE166_ALLOWED_WORKTREE_PATHS
        and not is_phase166_tracking_artifact(path)
    )


def unexpected_phase167_changed_paths(paths: tuple[str, ...] | list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path not in PHASE167_ALLOWED_WORKTREE_PATHS
        and not is_phase167_tracking_artifact(path)
    )


def unexpected_phase168_changed_paths(paths: tuple[str, ...] | list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path not in PHASE168_ALLOWED_WORKTREE_PATHS
        and not is_phase168_tracking_artifact(path)
    )


def unexpected_phase169_changed_paths(paths: tuple[str, ...] | list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path not in PHASE169_ALLOWED_WORKTREE_PATHS
        and not is_phase169_tracking_artifact(path)
        and path not in PHASE169_USER_OWNED_UNTRACKED_FILES
    )


def assert_phase166_no_public_schema_or_protected_drift() -> None:
    protected_examples = (
        ".planning/milestones/v9.0-phases/164/archive.md",
        "data/v4/archive/report.json",
        "docs/contracts/profit_visibility_public_v2.schema.json",
        "docs/contracts/profit_visibility_public_v2.schema.json.pyc.md",
        "docs/reports/v4/audit.md",
        "reports/v5.7/risk_gate_closure_evidence.md",
        "reports/v5.8/backtest_risk_gate_closure_evidence.md",
        "reports/v6.9/v6_closure_audit.md",
        "reports/v7.4/runtime_accounting_series_closure_audit.md",
        "reports/v8.0/grid_subset_invariance_semantic_cleanup_closure_audit.md",
        "risk/contracts/v2/risk_contract_v2.schema.json",
        "docs/contracts/live_preflight_result_v1.schema.json",
        "tests/fixtures/golden/result.json",
        "reports/v9.0/seal-fixture.json",
        "reports/v9.0/parity_fixture.json",
        "reports/v9.0/sha256_fixture.json",
    )
    assert protected_path_violations(protected_examples) == sorted(protected_examples)
    assert protected_path_violations(tuple(sorted(PHASE166_ALLOWED_WORKTREE_PATHS))) == []

    if os.environ.get(PHASE166_SCOPE_GUARD_ENV) != "1":
        return

    paths = phase166_changed_paths_for_scope_guard()
    assert protected_path_violations(paths) == []
    assert unexpected_phase166_changed_paths(paths) == []


def assert_phase167_no_public_schema_or_protected_drift() -> None:
    protected_examples = (
        ".planning/milestones/v9.0-phases/167/archive.md",
        "data/v4/archive/report.json",
        "docs/contracts/profit_visibility_public_v2.schema.json",
        "docs/contracts/profit_visibility_costed_metrics_v1.schema.json",
        "docs/reports/v4/audit.md",
        "reports/v5.7/risk_gate_closure_evidence.md",
        "reports/v5.8/backtest_risk_gate_closure_evidence.md",
        "reports/v6.9/v6_closure_audit.md",
        "reports/v7.4/runtime_accounting_series_closure_audit.md",
        "reports/v8.0/grid_subset_invariance_semantic_cleanup_closure_audit.md",
        "risk/contracts/v2/risk_contract_v2.schema.json",
        "docs/contracts/live_preflight_result_v1.schema.json",
        "tests/fixtures/golden/result.json",
        "reports/v9.0/seal-fixture.json",
        "reports/v9.0/parity_fixture.json",
        "reports/v9.0/sha256_fixture.json",
    )
    assert protected_path_violations(protected_examples) == sorted(protected_examples)
    assert protected_path_violations(tuple(sorted(PHASE167_ALLOWED_WORKTREE_PATHS))) == []
    assert unexpected_phase167_changed_paths(
        tuple(sorted(PHASE167_ALLOWED_WORKTREE_PATHS))
    ) == []

    if os.environ.get(PHASE167_SCOPE_GUARD_ENV) != "1":
        return

    paths = phase167_changed_paths_for_scope_guard()
    assert protected_path_violations(paths) == []
    assert unexpected_phase167_changed_paths(paths) == []


def assert_phase168_no_public_schema_or_protected_drift() -> None:
    protected_examples = (
        ".planning/milestones/v9.1-phases/168/archive.md",
        "data/v4/archive/report.json",
        "docs/contracts/profit_visibility_public_v2.schema.json",
        "docs/contracts/profit_visibility_statistical_evaluator_v1.schema.json",
        "docs/reports/v4/audit.md",
        "reports/v5.7/risk_gate_closure_evidence.md",
        "reports/v5.8/backtest_risk_gate_closure_evidence.md",
        "reports/v6.9/v6_closure_audit.md",
        "reports/v7.4/runtime_accounting_series_closure_audit.md",
        "reports/v8.0/grid_subset_invariance_semantic_cleanup_closure_audit.md",
        "risk/contracts/v2/risk_contract_v2.schema.json",
        "docs/contracts/live_preflight_result_v1.schema.json",
        "tests/fixtures/golden/result.json",
        "reports/v9.0/seal-fixture.json",
        "reports/v9.0/parity_fixture.json",
        "reports/v9.0/sha256_fixture.json",
    )
    assert protected_path_violations(protected_examples) == sorted(protected_examples)
    assert protected_path_violations(tuple(sorted(PHASE168_ALLOWED_WORKTREE_PATHS))) == []
    assert unexpected_phase168_changed_paths(
        tuple(sorted(PHASE168_ALLOWED_WORKTREE_PATHS))
    ) == []

    if os.environ.get(PHASE168_SCOPE_GUARD_ENV) != "1":
        return

    paths = phase168_changed_paths_for_scope_guard()
    assert protected_path_violations(paths) == []
    assert unexpected_phase168_changed_paths(paths) == []


def phase169_protected_path_violations(paths: tuple[str, ...] | list[str]) -> list[str]:
    violations = set(protected_path_violations(paths))
    violations.update(
        path
        for path in paths
        if any(path.startswith(prefix) for prefix in PHASE169_PROTECTED_CHANGED_PREFIXES)
    )
    return sorted(violations)


def assert_phase169_no_public_schema_or_protected_drift() -> None:
    protected_examples = (
        ".planning/milestones/v9.1-phases/169/archive.md",
        "data/v4/archive/report.json",
        "docs/contracts/profit_visibility_public_v2.schema.json",
        "docs/contracts/profit_visibility_e2e_report_v1.schema.json",
        "docs/reports/v4/audit.md",
        "reports/v5.7/risk_gate_closure_evidence.md",
        "reports/v5.8/backtest_risk_gate_closure_evidence.md",
        "reports/v6.9/v6_closure_audit.md",
        "reports/v7.4/runtime_accounting_series_closure_audit.md",
        "reports/v8.0/grid_subset_invariance_semantic_cleanup_closure_audit.md",
        "reports/v9.0/profit_visibility_decision_report.md",
        "reports/v9.0/profit_visibility_report_v1.json",
        "risk/contracts/v2/risk_contract_v2.schema.json",
        "docs/contracts/live_preflight_result_v1.schema.json",
        "tests/fixtures/golden/result.json",
        "reports/v9.1/seal-fixture.json",
        "reports/v9.1/parity_fixture.json",
        "reports/v9.1/sha256_fixture.json",
    )
    allowed_examples = tuple(sorted(PHASE169_ALLOWED_WORKTREE_PATHS)) + (
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-REVIEW-EVIDENCE.md",
        ".planning/phases/169-e2e-report-routing-and-closure-proof/169-04-SUMMARY.md",
        ".lean-ctx.toml",
    )

    assert phase169_protected_path_violations(protected_examples) == sorted(
        protected_examples
    )
    assert phase169_protected_path_violations(allowed_examples) == []
    assert unexpected_phase169_changed_paths(allowed_examples) == []
    assert unexpected_phase169_changed_paths(("README.md", "pyproject.toml")) == [
        "README.md",
        "pyproject.toml",
    ]


def test_decision_report_pins_required_fields_and_evidence_refs() -> None:
    text = read_decision_report()

    assert_contains_all(text, REQUIRED_REPORT_SECTIONS)
    assert_contains_all(text, REQUIRED_DECISION_FIELDS)
    assert "reports/v9.0/profit_visibility_report_v1.json" in text
    payload = json.loads(PROFIT_VISIBILITY_REPORT.read_text(encoding="utf-8"))
    assert payload["report_version"] == "ProfitVisibilityReport.v1"
    assert payload["overall_outcome"] == "plumbing_only"
    assert payload["profit_visible"] is False
    assert payload["survivor_count"] == 0
    assert (
        "Row-level detail is\ncarried by `reports/v9.0/profit_visibility_report_v1.json`"
        in text
    )
    assert "Phase 162" in text
    assert "Phase 163" in text
    assert "Phase 164" in text

    for relative_path, expected_markers in EVIDENCE_REFERENCE_EXPECTATIONS:
        path = evidence_reference_path(relative_path)
        assert path.is_file(), f"missing evidence reference: {relative_path}"
        assert_contains_all(path.read_text(encoding="utf-8"), expected_markers)


def test_decision_report_stop_reason_matches_payload_or_mapped_label() -> None:
    text = read_decision_report()
    payload = json.loads(PROFIT_VISIBILITY_REPORT.read_text(encoding="utf-8"))

    assert decision_stop_reason_from_report(text) == "phase165_reviewed_plumbing_only"
    assert STOP_REASON_CLOSURE_LABEL_MAP == {
        "phase165_reviewed_plumbing_only": "cost_incomplete",
    }
    assert_stop_reasons_match_payload_or_mapped_label(text, payload)


def test_unmapped_stop_reason_labels_fail_closure_guard() -> None:
    text = read_decision_report()
    payload = json.loads(PROFIT_VISIBILITY_REPORT.read_text(encoding="utf-8"))

    unmapped_report = text.replace(
        "`phase165_reviewed_plumbing_only`",
        "`phase165_new_unmapped_label`",
        1,
    )
    with pytest.raises(AssertionError):
        assert_stop_reasons_match_payload_or_mapped_label(unmapped_report, payload)

    duplicate_decision_register = text.replace(
        "| `primary_stop_reason` | `phase165_reviewed_plumbing_only` |",
        "| `primary_stop_reason` | `phase165_reviewed_plumbing_only` |\n"
        "| `primary_stop_reason` | `cost_incomplete` |",
        1,
    )
    with pytest.raises(AssertionError):
        assert_stop_reasons_match_payload_or_mapped_label(
            duplicate_decision_register,
            payload,
        )

    mismatched_map = {"phase165_reviewed_plumbing_only": "paper_forward_mapping_blocked"}
    with pytest.raises(AssertionError):
        assert_stop_reasons_match_payload_or_mapped_label(
            text,
            payload,
            label_map=mismatched_map,
        )


def test_phase169_decision_report_stop_reason_matches_generated_payload() -> None:
    text = read_phase169_decision_report()
    payload = json.loads(PHASE169_PROFIT_VISIBILITY_REPORT.read_text(encoding="utf-8"))
    e2e = load_phase169_e2e_module()

    assert decision_stop_reason_from_report(text) == payload["primary_stop_reason"]
    assert decision_stop_reason_from_report(text) == "registration_anchor_invalid"
    assert PHASE169_STOP_REASON_CLOSURE_LABEL_MAP == {}
    assert_stop_reasons_match_payload_or_mapped_label(
        text,
        payload,
        label_map=PHASE169_STOP_REASON_CLOSURE_LABEL_MAP,
    )
    assert text == e2e.render_fixture_decision_report(payload)


def test_phase169_unmapped_stop_reason_labels_fail_closure_guard() -> None:
    payload = {
        "primary_stop_reason": "registration_anchor_invalid",
        "candidate_rows": [{"primary_stop_reason": "registration_anchor_invalid"}],
        "family_summaries": [{"primary_stop_reason": "registration_anchor_invalid"}],
    }
    report_text = """# Synthetic Decision Report

## Decision

| field | value |
|---|---|
| `primary_stop_reason` | `registration_anchor_invalid` |

## Family Outcomes

| family | family_outcome | survivor_count | primary_stop_reason |
|---|---|---:|---|
| synthetic | `invalid_disqualified` | 0 | `phase169_new_unmapped_label` |
"""

    with pytest.raises(AssertionError):
        assert_stop_reasons_match_payload_or_mapped_label(
            report_text,
            payload,
            label_map=PHASE169_STOP_REASON_CLOSURE_LABEL_MAP,
        )

    mapped_label = {"phase169_reviewed_registration_anchor": "registration_anchor_invalid"}
    mapped_report = report_text.replace(
        "`phase169_new_unmapped_label`",
        "`phase169_reviewed_registration_anchor`",
    )
    assert_stop_reasons_match_payload_or_mapped_label(
        mapped_report,
        payload,
        label_map=mapped_label,
    )


def test_phase169_review_bundle_manifest_lists_existing_required_files() -> None:
    manifest = json.loads(PHASE169_REVIEW_BUNDLE_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["generated_at"] == "2026-06-04"
    assert_phase169_review_bundle_manifest(manifest)

    required_paths = {
        entry["path"]
        for entry in manifest["required_files"]
        if isinstance(entry, dict)
    }
    assert PHASE169_REVIEW_BUNDLE_REQUIRED_PATHS <= required_paths
    assert PHASE169_DEFERRED_REVIEW_EVIDENCE_PATH in required_paths

    deferred_paths = {
        entry["path"]
        for entry in manifest.get("deferred_files", [])
        if isinstance(entry, dict)
    }
    assert PHASE169_DEFERRED_REVIEW_EVIDENCE_PATH not in deferred_paths

    review_entry = next(
        entry
        for entry in manifest["required_files"]
        if entry["path"] == PHASE169_DEFERRED_REVIEW_EVIDENCE_PATH
    )
    assert "canonical_verdict" in review_entry["required_markers"]
    assert "review_result_reference" in review_entry["required_markers"]
    assert "closure_status" in review_entry["required_markers"]

    closure_entry = next(
        entry
        for entry in manifest["required_files"]
        if entry["path"] == "tests/test_profit_visibility_closure.py"
    )
    assert "review_result_reference_path" in closure_entry["required_markers"]
    assert "review-bundle reproducibility" in manifest["review_focus"]
    assert "claim creep" in manifest["review_focus"]
    limitations = "\n".join(manifest["manifest_limitations"])
    assert "$side-cc-workflow" in limitations
    assert "$side-review-verify" in limitations


def test_phase169_final_verification_mentions_review_replay_audit_and_clean_worktree_gates() -> None:
    text = read_phase169_review_evidence()

    for section in PHASE169_REVIEW_EVIDENCE_REQUIRED_SECTIONS:
        assert review_note_section(text, section)
    assert_contains_all(text, PHASE169_REVIEW_EVIDENCE_REQUIRED_FRAGMENTS)
    assert_contains_all(text, PHASE169_FINAL_VERIFICATION_REQUIRED_FRAGMENTS)
    assert phase169_review_note_has_required_verdict_handling(text)


def test_phase169_review_bundle_manifest_missing_files_fail(
    tmp_path: Path,
) -> None:
    present = tmp_path / "present.txt"
    present.write_text("required marker\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "phase": 169,
        "bundle_scope": PHASE169_REVIEW_BUNDLE_SCOPE,
        "required_files": [
            {
                "path": "present.txt",
                "required_markers": ["required marker"],
            }
        ],
        "deferred_files": [],
    }
    assert_phase169_review_bundle_manifest(manifest, root=tmp_path)

    missing_file_manifest = json.loads(json.dumps(manifest))
    missing_file_manifest["required_files"][0]["path"] = "missing.txt"
    with pytest.raises(AssertionError, match="missing review bundle file"):
        assert_phase169_review_bundle_manifest(missing_file_manifest, root=tmp_path)

    absolute_path_manifest = json.loads(json.dumps(manifest))
    absolute_path_manifest["required_files"][0]["path"] = str(present)
    with pytest.raises(AssertionError, match="must be relative"):
        assert_phase169_review_bundle_manifest(absolute_path_manifest, root=tmp_path)

    escaping_path_manifest = json.loads(json.dumps(manifest))
    escaping_path_manifest["required_files"][0]["path"] = "../present.txt"
    with pytest.raises(AssertionError, match="must not contain"):
        assert_phase169_review_bundle_manifest(escaping_path_manifest, root=tmp_path)

    missing_marker_manifest = json.loads(json.dumps(manifest))
    missing_marker_manifest["required_files"][0]["required_markers"] = [
        "missing marker"
    ]
    with pytest.raises(AssertionError, match="missing markers"):
        assert_phase169_review_bundle_manifest(missing_marker_manifest, root=tmp_path)


def test_outcome_taxonomy_and_next_stage_claim_boundary_are_exact() -> None:
    assert forbidden_claim_matches("this phase does not grant live ready status") == []
    assert forbidden_claim_matches("never paper-forward ready") == []
    assert forbidden_claim_matches("must not treat this as live ready") == []

    rejected = (
        "there is no doubt the system is live ready",
        "No caveats remain: the system is broker ready",
        "Another milestone - live ready",
        "This is production ready",
        "go-live approved for the next stage",
        "live trading enabled after closure",
        "cleared for live trading",
        "approved to trade real money",
        "fit to deploy live",
        "greenlit for go-live",
        "shadow ready after review",
        "We must not treat plumbing-only routes as profit-bearing; the strategy counts as broker ready",
        "must not treat this - as live ready",
        "does not approve yet truly fully live ready",
        "not live ready, but live ready soon",
        "not only live ready",
        "no less than live ready",
        "## Live-Trading Claim Exclusions\npositive live ready",
    )
    for line in rejected:
        assert forbidden_claim_matches(line), line

    text = read_decision_report()
    assert_contains_all(text, REQUIRED_OUTCOME_LABELS)
    assert_contains_all(text, ALLOWED_NEXT_STAGE_PHRASES)
    assert forbidden_claim_matches(report_claim_sections(text)) == []


def test_live_trading_claim_exclusions_are_explicit() -> None:
    text = read_decision_report()

    assert_contains_all(text, LIVE_TRADING_EXCLUSIONS)


def test_requirements_coverage_covers_all_phase_165_requirements() -> None:
    text = read_decision_report()

    assert_contains_all(text, REQUIRED_PHASE165_REQUIREMENTS)


def test_closure_guard_rejects_public_schema_and_protected_drift() -> None:
    protected_examples = (
        ".planning/milestones/v9.0-ROADMAP.md",
        "data/v4/archive/report.json",
        "docs/contracts/profit_visibility_public_v2.schema.json",
        "docs/contracts/profit_visibility_public_v2.schema.json.pyc.md",
        "docs/reports/v4/audit.md",
        "reports/v5.7/risk_gate_closure_evidence.md",
        "reports/v5.8/backtest_risk_gate_closure_evidence.md",
        "reports/v6.9/v6_closure_audit.md",
        "reports/v7.4/runtime_accounting_series_closure_audit.md",
        "reports/v8.0/grid_subset_invariance_semantic_cleanup_closure_audit.md",
        "risk/contracts/v2/risk_contract_v2.schema.json",
        "docs/contracts/live_preflight_result_v1.schema.json",
        "pyproject.toml",
        "uv.lock",
        "tests/fixtures/golden/result.json",
        "reports/v9.0/seal-fixture.json",
        "reports/v9.0/parity_fixture.json",
        "reports/v9.0/sha256_fixture.json",
    )
    assert protected_path_violations(protected_examples) == sorted(protected_examples)

    allowed_examples = tuple(sorted(PHASE165_ALLOWED_CHANGED_PATHS))
    assert protected_path_violations(allowed_examples) == []
    assert unexpected_phase165_changed_paths(allowed_examples) == []
    assert unexpected_phase165_changed_paths(("reports/v9.0/extra.md",)) == [
        "reports/v9.0/extra.md"
    ]


def test_generated_like_paths_are_not_exempt_from_committed_diff() -> None:
    generated_like_examples = (
        "pkg/__pycache__/module.cpython-312.pyc",
        ".pytest_cache/v/cache/nodeids",
        ".mypy_cache/3.12/module.meta.json",
        ".ruff_cache/0.8.0/file",
        "tests/unit.pyc",
    )
    for path in generated_like_examples:
        assert unexpected_phase165_changed_paths((path,)) == [path]

    bypass_attempt = "docs/contracts/profit_visibility_public_v2.schema.json.pyc.md"
    assert protected_path_violations((bypass_attempt,)) == [bypass_attempt]
    assert unexpected_phase165_changed_paths((bypass_attempt,)) == [bypass_attempt]


def test_phase165_real_diff_has_no_public_schema_or_protected_drift() -> None:
    paths = changed_paths_from_git(
        resolve_phase165_diff_base(),
        resolve_phase165_diff_end(),
    )

    assert protected_path_violations(paths) == []
    assert unexpected_phase165_changed_paths(paths) == []
    assert missing_phase165_required_changed_paths(paths) == []


def test_phase165_worktree_is_clean_before_closure() -> None:
    paths = working_tree_changed_paths()

    assert protected_path_violations(paths) == []
    if paths:
        assert set(paths) <= PHASE166_ALLOWED_WORKTREE_PATHS
        return
    assert unexpected_phase165_changed_paths(paths) == []
    assert paths == ()


def test_existing_public_proof_and_live_preflight_invariance_tests_are_in_suite() -> None:
    public_proof = (ROOT / "tests/test_risk_contract_v2_public_proof_invariance.py")
    live_preflight = ROOT / "tests/test_live_preflight_result_contract.py"

    assert public_proof.is_file()
    assert live_preflight.is_file()
    assert "test_public_proof_invariance_guard_reads_current_sources_not_snapshots" in (
        public_proof.read_text(encoding="utf-8")
    )
    assert "test_live_preflight_result_schema_is_present_only_at_docs_contract_path" in (
        live_preflight.read_text(encoding="utf-8")
    )


def test_audit_open_and_diff_hygiene_commands_are_listed() -> None:
    text = read_decision_report()

    assert "rtk " not in text
    assert_contains_all(
        text,
        (
            "uv run pytest -q tests/test_profit_visibility_closure.py",
            "uv run pytest -q tests/test_risk_contract_v2_public_proof_invariance.py tests/test_live_preflight_result_contract.py",
            "node $HOME/.codex/get-shit-done/bin/gsd-tools.cjs query audit-open",
            "test -z \"$(git status --porcelain --untracked-files=all)\"",
            "git diff --check",
        ),
    )


def test_phase166_closure_rejects_public_schema_and_protected_drift() -> None:
    assert_phase166_no_public_schema_or_protected_drift()


def test_phase167_closure_rejects_public_schema_and_protected_drift() -> None:
    assert_phase167_no_public_schema_or_protected_drift()


def test_phase168_closure_rejects_public_schema_and_protected_drift() -> None:
    assert_phase168_no_public_schema_or_protected_drift()


def test_phase169_closure_rejects_public_schema_and_protected_drift() -> None:
    assert_phase169_no_public_schema_or_protected_drift()


def _write_raw_review(root: Path, raw_text: str) -> None:
    path = root / REVIEW_RAW_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw_text, encoding="utf-8")


def _review_note_fixture(
    verdict: str = "approve",
    *,
    verdict_token: str | None = None,
    reference: str = REVIEW_RAW_RELATIVE,
    verbatim_line: str | None = None,
    fixes: str = "None required.",
    closure_status: str | None = None,
) -> str:
    verdict_token = verdict if verdict_token is None else verdict_token
    verbatim_line = (
        f"REVIEW-VERDICT: {verdict}" if verbatim_line is None else verbatim_line
    )
    if closure_status is None:
        closure_status = "blocked" if verdict == "needs-attention" else "closed"
    return f"""# Phase 165 Review Evidence

## Local Verification Before Review

- `uv run pytest -q tests/test_profit_visibility_closure.py` passed.

## Review Command

`$cc:adversarial-review --wait --scope branch --base {PHASE165_PHASE_START_BASE} "Phase 165 decision evidence diff"`

## Review Scope

Reviewed branch `decision evidence diff` with `--scope branch` and `--base`.

## Review Focus

- anchor predates supported evaluation output
- claim-boundary wording
- absence of live/account/credential/network/broker/public-schema/protected-artifact drift

## Review Verdict

review_result_reference: {reference}
review_command_provenance: companion adversarial-review command captured in this note
review_transcript_provenance: raw transcript captured in 165-REVIEW-RAW.md
verbatim_review_verdict_line: {verbatim_line}
{REVIEW_VERDICT_NORMALIZATION}
verdict: {verdict_token}
closure_status: {closure_status}

## Needs-Attention Handling

The word needs-attention may appear here as process documentation without
changing the canonical review verdict.

## Fixes And Re-Review

{fixes}

## Closure Decision

Plan-only review is not downstream readiness evidence.

## Remaining Limits

No live-shadow, broker, account, network, credential, or tiny-live readiness is
approved by this review note.
"""


def test_review_evidence_note_records_required_scope_and_verdict() -> None:
    if not REVIEW_EVIDENCE.exists():
        text = read_decision_report()
        assert "external review pending" in text
        assert (
            "PVC-CLOSURE-02 not complete until 165-REVIEW-EVIDENCE.md exists"
            in text
        )
        assert "does not independently prove anchor timestamp ordering" in text
        assert "committed Phase 165 diff only" in text
        assert "defense-in-depth literal checks" in text
        assert "evidence-note/raw consistency only" in text
        return

    text = REVIEW_EVIDENCE.read_text(encoding="utf-8")
    for section in REQUIRED_REVIEW_NOTE_SECTIONS:
        assert review_note_section(text, section)
    assert_contains_all(text, REQUIRED_REVIEW_NOTE_FRAGMENTS)

    note_verdict = review_verdict_from_note(text)
    raw_verdict = review_artifact_verdict_from_reference(text)
    raw_text = REVIEW_RAW.read_text(encoding="utf-8")
    assert note_verdict == raw_verdict == transcript_top_level_verdict(raw_text)
    assert not review_note_blocks_closure(text)


def test_review_gate_blocks_needs_attention_without_fix_or_override() -> None:
    if not REVIEW_EVIDENCE.exists():
        assert "external review pending" in read_decision_report()
        return

    text = REVIEW_EVIDENCE.read_text(encoding="utf-8")
    assert not review_note_blocks_closure(text)


def test_review_verdict_parser_ignores_needs_attention_handling_section(
    tmp_path: Path,
) -> None:
    _write_raw_review(
        tmp_path,
        "REVIEW-VERDICT: approve\n"
        f"{REVIEW_RAW_DELIMITER}\n"
        "Verdict: approve\n"
        "Findings:\n"
        "The transcript can mention REVIEW-VERDICT: needs-attention later.\n",
    )
    note = _review_note_fixture("approve")

    assert review_verdict_from_note(note) == "approve"
    assert review_artifact_verdict_from_reference(note, root=tmp_path) == "approve"
    assert not review_note_blocks_closure(note, root=tmp_path)


def test_review_gate_blocks_needs_attention_and_contradictory_transcript(
    tmp_path: Path,
) -> None:
    _write_raw_review(
        tmp_path,
        "REVIEW-VERDICT: needs-attention\n"
        f"{REVIEW_RAW_DELIMITER}\n"
        "Verdict: needs-attention\n"
        "Findings:\n"
        "Later prose says approve, but the top-level verdict controls.\n",
    )
    needs_attention_note = _review_note_fixture("needs-attention")
    assert review_note_blocks_closure(needs_attention_note, root=tmp_path)

    _write_raw_review(
        tmp_path,
        "REVIEW-VERDICT: approve\n"
        f"{REVIEW_RAW_DELIMITER}\n"
        '{"verdict": "needs-attention"}\n',
    )
    contradictory_note = _review_note_fixture("approve")
    assert review_note_blocks_closure(contradictory_note, root=tmp_path)


def test_review_raw_prelude_and_transcript_verdicts_are_strict(
    tmp_path: Path,
) -> None:
    multiple_prelude = (
        "REVIEW-VERDICT: approve\n"
        "REVIEW-VERDICT: needs-attention\n"
        f"{REVIEW_RAW_DELIMITER}\n"
        "Verdict: approve\n"
    )
    _write_raw_review(tmp_path, multiple_prelude)
    with pytest.raises(AssertionError):
        review_artifact_verdict_from_reference(_review_note_fixture(), root=tmp_path)

    with pytest.raises(AssertionError):
        transcript_top_level_verdict("Findings:\nVerdict: approve\n")

    with pytest.raises(AssertionError):
        transcript_top_level_verdict("Verdict: approve\nVerdict: needs-attention\n")

    tolerated_body_marker = (
        "REVIEW-VERDICT: approve\n"
        f"{REVIEW_RAW_DELIMITER}\n"
        "Verdict: approve\n"
        "Findings:\n"
        "Nested REVIEW-VERDICT: needs-attention text is body prose.\n"
    )
    assert transcript_top_level_verdict(tolerated_body_marker) == "approve"


def test_review_note_requires_reference_verbatim_line_and_canonical_verdict(
    tmp_path: Path,
) -> None:
    _write_raw_review(
        tmp_path,
        "REVIEW-VERDICT: approve\n"
        f"{REVIEW_RAW_DELIMITER}\n"
        "Verdict: approve\n",
    )

    missing_reference_note = _review_note_fixture().replace(
        f"review_result_reference: {REVIEW_RAW_RELATIVE}\n",
        "",
    )
    assert review_note_blocks_closure(missing_reference_note, root=tmp_path)

    wrong_verbatim_note = _review_note_fixture(
        "approve",
        verbatim_line="REVIEW-VERDICT: needs-attention",
    )
    assert review_note_blocks_closure(wrong_verbatim_note, root=tmp_path)

    for invalid_token in ("blocked", "override"):
        with pytest.raises(AssertionError):
            review_verdict_from_note(
                _review_note_fixture("approve", verdict_token=invalid_token)
            )


@pytest.mark.parametrize(
    ("env_value", "expected_error"),
    (
        ("84ec8ff5f2488716355de0296ca9f4267c9d33dd", None),
        ("HEAD", AssertionError),
        ("", AssertionError),
    ),
)
def test_phase_start_base_is_fixed(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected_error: type[BaseException] | None,
) -> None:
    monkeypatch.delenv("PHASE165_DIFF_BASE", raising=False)
    monkeypatch.setenv("PHASE165_PHASE_START_BASE", env_value)
    if expected_error is None:
        assert resolve_phase165_phase_start_base() == PHASE165_PHASE_START_BASE
    else:
        with pytest.raises(expected_error):
            resolve_phase165_phase_start_base()


def test_phase_start_base_defaults_to_pinned_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PHASE165_PHASE_START_BASE", raising=False)
    monkeypatch.delenv("PHASE165_DIFF_BASE", raising=False)

    assert resolve_phase165_phase_start_base() == PHASE165_PHASE_START_BASE
