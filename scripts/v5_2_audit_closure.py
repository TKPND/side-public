"""Build the v5.2 audit-closure verdict."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "v5.2.audit-closure.1"
DEFAULT_REPORT_DIR = Path("reports/v5.2/audit_closure")
DEFAULT_REQUIREMENTS = Path(".planning/milestones/v5.2-REQUIREMENTS.md")
DEFAULT_ROADMAP = Path(".planning/milestones/v5.2-ROADMAP.md")
DEFAULT_STATE = Path(".planning/STATE.md")
DEFAULT_CLAIM_READINESS = Path("reports/v5.2/claim_readiness/verdict.json")
DEFAULT_V5_1_VERDICT = Path("reports/v5.1/phase116/final_verdict.json")
DEFAULT_V5_1_TICK_CONTRACT = Path("reports/v5.1/tick_data_contract_report.json")

REQUIREMENTS_ADDRESSED = [
    "AUDIT52-V52-01",
    "AUDIT52-V52-02",
    "AUDIT52-V52-03",
]

PROTECTED_V5_1_PATHS = {
    "reports/v5.1/phase116/final_verdict.json",
    "reports/v5.1/phase116/final_verdict.md",
    "reports/v5.1/tick_data_contract_report.json",
    "reports/v5.1/tick_data_contract_report.md",
}

EVIDENCE_REFS = [
    "reports/v5.2/source_selection/source_verdict.json",
    "reports/v5.2/ingestion_smoke/manifest.json",
    "reports/v5.2/microstructure_audit/audit.json",
    DEFAULT_CLAIM_READINESS.as_posix(),
    DEFAULT_V5_1_VERDICT.as_posix(),
    DEFAULT_V5_1_TICK_CONTRACT.as_posix(),
]


def expected_v5_2_traceability() -> dict[str, int]:
    """Return the exact v5.2 requirement-to-phase mapping."""
    return {
        **{f"SOURCE-V52-{index:02d}": 118 for index in range(1, 4)},
        **{f"INGEST-V52-{index:02d}": 119 for index in range(1, 4)},
        **{f"MICRO-V52-{index:02d}": 120 for index in range(1, 4)},
        **{f"CLAIM52-V52-{index:02d}": 121 for index in range(1, 4)},
        **{f"AUDIT52-V52-{index:02d}": 122 for index in range(1, 4)},
    }


def protected_path_matches(changed_paths: Iterable[str]) -> dict[str, list[str]]:
    """Classify changed paths that would violate preservation boundaries."""
    matches = {"v5_1": [], "v4_archive": []}
    for raw_path in changed_paths:
        path = raw_path.strip()
        if not path:
            continue
        if path in PROTECTED_V5_1_PATHS:
            matches["v5_1"].append(path)
        if (
            path.startswith(".planning/milestones/v4")
            or path == ".planning/milestones/RETROSPECTIVE.md"
            or path.startswith("data/v4.13/")
            or path == "data/v4.13"
            or path.startswith("reports/v4")
            or path.startswith("docs/reports/v4")
            or path.startswith("docs/v4")
        ):
            matches["v4_archive"].append(path)
    return matches


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _traceability_from_markdown(path: Path) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    pattern = re.compile(
        r"^\|\s*([A-Z0-9]+-V52-\d{2})\s*\|\s*Phase\s+(\d+)\s*\|\s*([^|]+?)\s*\|"
    )
    for line in path.read_text().splitlines():
        match = pattern.match(line)
        if match is None:
            continue
        requirement, phase, status = match.groups()
        rows.setdefault(requirement, []).append(
            {"phase": int(phase), "status": status.strip()}
        )
    return rows


def _traceability_exactly_once(requirements_path: Path) -> tuple[bool, dict[str, Any]]:
    expected = expected_v5_2_traceability()
    actual = _traceability_from_markdown(requirements_path)
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    duplicates = sorted(req for req, rows in actual.items() if len(rows) != 1)
    phase_mismatches = {
        req: {"expected": phase, "actual": actual.get(req, [{}])[0].get("phase")}
        for req, phase in expected.items()
        if req in actual and actual[req][0].get("phase") != phase
    }
    incomplete = sorted(
        req
        for req, rows in actual.items()
        if req in expected and rows[0].get("status") != "Complete"
    )
    passed = not (missing or unexpected or duplicates or phase_mismatches or incomplete)
    detail = {
        "expected": expected,
        "actual": {
            req: rows[0] if len(rows) == 1 else rows
            for req, rows in sorted(actual.items())
        },
        "missing": missing,
        "unexpected": unexpected,
        "duplicates": duplicates,
        "phase_mismatches": phase_mismatches,
        "incomplete": incomplete,
    }
    return passed, detail


def _v5_1_evidence(
    v5_1_verdict_path: Path,
    v5_1_tick_contract_path: Path,
) -> dict[str, Any]:
    verdict = _read_json(v5_1_verdict_path)
    tick_contract = _read_json(v5_1_tick_contract_path)
    return {
        "ref": v5_1_verdict_path.as_posix(),
        "tick_contract_ref": v5_1_tick_contract_path.as_posix(),
        "verdict": verdict.get("verdict"),
        "ship_verdict": verdict.get("ship_verdict"),
        "candidate_count": verdict.get("candidate_count"),
        "null_ship_reasons": verdict.get("null_ship_reasons", []),
        "tick_contract_claims": tick_contract.get("claims", {}),
    }


def _v5_1_closed_input_preserved(
    evidence: dict[str, Any],
    protected_matches: list[str],
) -> bool:
    claims = evidence.get("tick_contract_claims", {})
    return (
        not protected_matches
        and evidence.get("verdict") == "null_ship"
        and evidence.get("ship_verdict") is False
        and evidence.get("candidate_count") == 0
        and "empty_phase116_candidate_set" in evidence.get("null_ship_reasons", [])
        and claims.get("aggressor_flow_claim") is False
        and claims.get("l2_depth_claim") is False
        and claims.get("market_depth_claim") is False
    )


def _claim_readiness_closed(report: dict[str, Any]) -> bool:
    candidate = report.get("blocked_contract_candidate", {})
    return (
        report.get("verdict") == "null_ship"
        and report.get("ship_verdict") is False
        and report.get("downstream_claim_ready") is False
        and report.get("sealed_for_downstream_claim") is False
        and candidate.get("downstream_consumable") is False
    )


def build_audit_closure(
    requirements_path: Path,
    roadmap_path: Path,
    state_path: Path,
    claim_readiness_path: Path,
    v5_1_verdict_path: Path,
    v5_1_tick_contract_path: Path,
    changed_paths: Iterable[str],
) -> dict[str, Any]:
    """Build the Phase 122 closure audit from local artifacts only."""
    traceability_exactly_once, traceability = _traceability_exactly_once(
        requirements_path
    )
    protected_matches = protected_path_matches(changed_paths)
    claim_readiness = _read_json(claim_readiness_path)
    v5_1_evidence = _v5_1_evidence(v5_1_verdict_path, v5_1_tick_contract_path)
    v5_1_preserved = _v5_1_closed_input_preserved(
        v5_1_evidence,
        protected_matches["v5_1"],
    )
    v4_archive_untouched = not protected_matches["v4_archive"]
    claim_readiness_closed = _claim_readiness_closed(claim_readiness)

    all_guards_pass = (
        traceability_exactly_once
        and v5_1_preserved
        and v4_archive_untouched
        and claim_readiness_closed
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": 122,
        "requirements_addressed": REQUIREMENTS_ADDRESSED,
        "traceability_exactly_once": traceability_exactly_once,
        "v5_1_preserved": v5_1_preserved,
        "v4_archive_untouched": v4_archive_untouched,
        "milestone_verdict": "source_level_null_ship",
        "ship_verdict": False,
        "downstream_claim_ready": False,
        "sealed_for_downstream_claim": False,
        "close_readiness": (
            "ready_for_milestone_completion" if all_guards_pass else "blocked"
        ),
        "claim_readiness_closed": claim_readiness_closed,
        "traceability": traceability,
        "v5_1_evidence": v5_1_evidence,
        "protected_path_matches": protected_matches,
        "source_inputs": {
            "requirements": requirements_path.as_posix(),
            "roadmap": roadmap_path.as_posix(),
            "state": state_path.as_posix(),
            "claim_readiness": claim_readiness_path.as_posix(),
            "v5_1_verdict": v5_1_verdict_path.as_posix(),
            "v5_1_tick_contract": v5_1_tick_contract_path.as_posix(),
        },
        "evidence_refs": EVIDENCE_REFS,
        "notes": [
            "Phase 122 does not rerun provider access, signal logic, backtests, or v5.1 thresholds.",
            "v5.2 closes as a source-level null_ship unless a future source contract phase creates live/raw evidence.",
        ],
    }


def _bool_text(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _list_text(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def render_audit_markdown(report: dict[str, Any]) -> str:
    """Render the audit closure report as compact Markdown."""
    lines = [
        "# v5.2 Audit Closure",
        "",
        f"- schema_version: {report['schema_version']}",
        f"- phase: {report['phase']}",
        f"- milestone_verdict: {report['milestone_verdict']}",
        f"- ship_verdict: {_bool_text(report['ship_verdict'])}",
        f"- downstream_claim_ready: {_bool_text(report['downstream_claim_ready'])}",
        f"- sealed_for_downstream_claim: {_bool_text(report['sealed_for_downstream_claim'])}",
        f"- traceability_exactly_once: {_bool_text(report['traceability_exactly_once'])}",
        f"- v5_1_preserved: {_bool_text(report['v5_1_preserved'])}",
        f"- v4_archive_untouched: {_bool_text(report['v4_archive_untouched'])}",
        f"- close_readiness: {report['close_readiness']}",
        "",
        "## Requirements Addressed",
        "",
    ]
    lines.extend(f"- {requirement}" for requirement in report["requirements_addressed"])
    lines.extend(
        [
            "",
            "## v5.1 Preservation",
            "",
            f"- verdict: {report['v5_1_evidence']['verdict']}",
            f"- ship_verdict: {_bool_text(report['v5_1_evidence']['ship_verdict'])}",
            f"- candidate_count: {report['v5_1_evidence']['candidate_count']}",
            "- null_ship_reasons: "
            + ", ".join(report["v5_1_evidence"]["null_ship_reasons"]),
            "",
            "## Protected Path Matches",
            "",
            "- v5_1: " + _list_text(report["protected_path_matches"]["v5_1"]),
            "- v4_archive: "
            + _list_text(report["protected_path_matches"]["v4_archive"]),
            "",
            "## Evidence References",
            "",
        ]
    )
    lines.extend(f"- {ref}" for ref in report["evidence_refs"])
    lines.append("")
    return "\n".join(lines)


def write_json(report: dict[str, Any], path: Path) -> None:
    """Write canonical JSON with deterministic key ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS)
    parser.add_argument("--roadmap", type=Path, default=DEFAULT_ROADMAP)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--claim-readiness", type=Path, default=DEFAULT_CLAIM_READINESS)
    parser.add_argument("--v5-1-verdict", type=Path, default=DEFAULT_V5_1_VERDICT)
    parser.add_argument(
        "--v5-1-tick-contract",
        type=Path,
        default=DEFAULT_V5_1_TICK_CONTRACT,
    )
    parser.add_argument("--changed-path", action="append", default=[])
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_audit_closure(
        args.requirements,
        args.roadmap,
        args.state,
        args.claim_readiness,
        args.v5_1_verdict,
        args.v5_1_tick_contract,
        args.changed_path,
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report, args.report_dir / "audit.json")
    (args.report_dir / "audit.md").write_text(render_audit_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
