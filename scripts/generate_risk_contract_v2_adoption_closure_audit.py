#!/usr/bin/env python3
"""Generate a closure audit for the risk_contract.v2 adoption evidence set."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "risk_contract_v2_adoption_closure_audit.v1"
BOUNDARY = "risk_contract_v2_adoption_closure_audit"
DEFAULT_REPORT_DIR = Path("reports/risk-contract-v2/adoption-closure")
JSON_REPORT_NAME = "risk_contract_v2_adoption_closure_audit.json"
MD_REPORT_NAME = "risk_contract_v2_adoption_closure_audit.md"
EXPECTED_SURFACES = ("backtest", "paper", "scan")
EXPECTED_DECISIONS = ("cap", "reject", "size")
LIVE_FIXTURE_PATH = Path("risk/contracts/v2/fixtures/valid/live_not_claimable_valid.json")

SURFACE_REPORTS: dict[str, Path] = {
    "backtest": Path(
        "reports/risk-contract-v2/backtest-runtime-adoption/backtest_v2_runtime_adoption_evidence.json"
    ),
    "paper": Path(
        "reports/risk-contract-v2/paper-runtime-adoption/paper_v2_runtime_adoption_evidence.json"
    ),
    "scan": Path(
        "reports/risk-contract-v2/scan-runtime-adoption/scan_v2_runtime_adoption_evidence.json"
    ),
}

SOURCE_EVIDENCE_PATHS = (
    "docs/plans/2026-05-18-risk-contract-v2-runtime-adoption-design.md",
    "docs/plans/2026-05-18-risk-contract-v2-backtest-runtime-adoption-tdd.md",
    "docs/plans/2026-05-18-risk-contract-v2-backtest-evidence-replay-hardening-tdd.md",
    "docs/plans/2026-05-18-risk-contract-v2-scan-runtime-statistical-split-design.md",
    "docs/superpowers/plans/2026-05-18-scan-v2-runtime-adoption-tdd.md",
    "docs/superpowers/plans/2026-05-18-scan-v2-evidence-replay-hardening-tdd.md",
    "docs/plans/2026-05-18-risk-contract-v2-sizing-unit-expansion-design.md",
    "docs/plans/2026-05-18-risk-contract-v2-sizing-unit-schema-validator-tdd.md",
    "docs/plans/2026-05-18-risk-contract-v2-paper-runtime-adoption-design.md",
    "docs/plans/2026-05-18-risk-contract-v2-paper-runtime-adoption-tdd.md",
    "docs/plans/2026-05-18-risk-contract-v2-paper-evidence-replay-hardening-tdd.md",
    "docs/plans/2026-05-18-risk-contract-v2-live-not-claimable-fixtures-tdd.md",
    "docs/plans/2026-05-18-risk-contract-v2-adoption-closure-audit-tdd.md",
    "risk/contracts/v2/risk_contract_v2.schema.json",
    "risk/contracts/v2/risk_contract_validator_result_v2.schema.json",
    "risk/contracts/v2/fixture_matrix.json",
    "risk/contracts/v2/fixtures/valid/live_not_claimable_valid.json",
    "rust/side-cli/src/main.rs",
    "scripts/validate_risk_contract.py",
    "scripts/generate_backtest_v2_runtime_adoption_evidence.py",
    "scripts/generate_scan_v2_runtime_adoption_evidence.py",
    "scripts/generate_paper_v2_runtime_adoption_evidence.py",
    "scripts/generate_risk_contract_v2_adoption_closure_audit.py",
    "tests/test_generate_backtest_v2_runtime_adoption_evidence.py",
    "tests/test_generate_scan_v2_runtime_adoption_evidence.py",
    "tests/test_generate_paper_v2_runtime_adoption_evidence.py",
    "tests/test_generate_risk_contract_v2_adoption_closure_audit.py",
)

PROTECTED_OUTPUT_PREFIXES = (
    "reports/v5.7",
    "reports/v5.8",
    "reports/v8.",
    ".planning",
    "docs/reports/v4",
    "data/v4",
    "risk/contracts",
)

PROTECTED_CHANGED_PREFIXES = (
    "reports/v5.7",
    "reports/v5.8",
    "reports/v8.",
    ".planning/milestones",
    "docs/reports/v4",
    "data/v4",
    "risk/contracts",
)

RUNTIME_EXPANSION_PREFIXES = (
    "rust/side-cli/src/cmd/live",
    "rust/side-cli/src/cmd/broker",
    "rust/side-engine/src/live",
    "rust/side-engine/src/broker",
)

LIVE_RUNTIME_SURFACE_SOURCE = Path("rust/side-cli/src/main.rs")
LIVE_RUNTIME_IMPLEMENTATION_PATHS = (
    Path("rust/side-cli/src/cmd/live.rs"),
    Path("rust/side-cli/src/cmd/live"),
    Path("rust/side-cli/src/cmd/broker.rs"),
    Path("rust/side-cli/src/cmd/broker"),
    Path("rust/side-engine/src/live.rs"),
    Path("rust/side-engine/src/live"),
    Path("rust/side-engine/src/broker.rs"),
    Path("rust/side-engine/src/broker"),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def canonical_relative(path: str | Path) -> str:
    path_obj = Path(path)
    if path_obj.is_absolute():
        try:
            return path_obj.resolve().relative_to(repo_root()).as_posix()
        except ValueError:
            return path_obj.as_posix()
    rel = path_obj.as_posix()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def is_under_prefix(path: str | Path, prefixes: tuple[str, ...]) -> bool:
    rel = canonical_relative(path)
    for prefix in prefixes:
        if prefix.endswith(".") and rel.startswith(prefix):
            return True
        if rel == prefix or rel.startswith(f"{prefix}/"):
            return True
    return False


def ensure_safe_output_dir(report_dir: Path) -> None:
    if is_under_prefix(report_dir, PROTECTED_OUTPUT_PREFIXES):
        raise ValueError(f"protected output directory: {report_dir.as_posix()}")


def sha256_file(path: Path) -> str | None:
    full_path = repo_root() / path
    if not full_path.exists() or not full_path.is_file():
        return None
    return hashlib.sha256(full_path.read_bytes()).hexdigest()


def source_evidence_rows() -> list[dict[str, Any]]:
    rows = []
    for path_text in SOURCE_EVIDENCE_PATHS:
        path = Path(path_text)
        digest = sha256_file(path)
        rows.append(
            {
                "path": path_text,
                "role": "read_only_input" if not path_text.startswith(("scripts/", "tests/")) else "audit_code",
                "exists": digest is not None,
                "sha256": digest,
            }
        )
    return rows


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.as_posix()} top-level JSON must be an object")
    return data


def load_surface_reports() -> dict[str, dict[str, Any]]:
    root = repo_root()
    return {surface: load_json(root / path) for surface, path in SURFACE_REPORTS.items()}


def load_live_fixture() -> dict[str, Any]:
    return load_json(repo_root() / LIVE_FIXTURE_PATH)


def changed_paths_since(diff_base: str | None) -> list[str]:
    root = repo_root()
    paths: set[str] = set()
    if diff_base:
        proc = subprocess.run(
            ["git", "diff", "--name-only", diff_base, "--"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git diff --name-only {diff_base} failed: {proc.stderr.strip()}"
            )
        paths.update(line.strip() for line in proc.stdout.splitlines() if line.strip())
    proc = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files --others failed: {proc.stderr.strip()}")
    paths.update(line.strip() for line in proc.stdout.splitlines() if line.strip())
    return sorted(paths)


def run_validator(path: str | Path) -> dict[str, Any]:
    rel_path = canonical_relative(path)
    proc = subprocess.run(
        [sys.executable, "scripts/validate_risk_contract.py", rel_path],
        cwd=repo_root(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if not proc.stdout.strip():
        return {
            "schema_version": None,
            "valid": False,
            "checked_path": rel_path,
            "errors": [
                {
                    "code": "validator_no_stdout",
                    "path": "$",
                    "message": proc.stderr.strip() or "validator produced no stdout",
                }
            ],
        }
    payload = json.loads(proc.stdout)
    payload["return_code"] = proc.returncode
    if proc.stderr.strip():
        payload["stderr"] = proc.stderr.strip()
    return payload


def collect_validator_payloads(
    surface_reports: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    payloads: dict[tuple[str, str], dict[str, Any]] = {}
    for surface, report in surface_reports.items():
        for decision in EXPECTED_DECISIONS:
            run = report.get("runs", {}).get(decision)
            if not isinstance(run, dict):
                continue
            artifact_path = run.get("artifact_path")
            if isinstance(artifact_path, str):
                payloads[(surface, decision)] = run_validator(artifact_path)
    return payloads


def surface_reports_pass_check(
    surface_reports: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    missing = [surface for surface in EXPECTED_SURFACES if surface not in surface_reports]
    failing = []
    for surface in EXPECTED_SURFACES:
        report = surface_reports.get(surface)
        if not isinstance(report, dict):
            continue
        summary = report.get("summary", {})
        if summary.get("overall_status") != "PASS" or summary.get("checks_failed") != 0:
            failing.append(surface)
    return {
        "passed": not missing and not failing,
        "expected_surfaces": list(EXPECTED_SURFACES),
        "loaded_surfaces": sorted(surface_reports),
        "missing_surfaces": missing,
        "failing_surfaces": failing,
    }


def decision_class_matrix_check(
    surface_reports: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    missing: dict[str, list[str]] = {}
    invalid: list[dict[str, Any]] = []
    for surface in EXPECTED_SURFACES:
        report = surface_reports.get(surface, {})
        runs = report.get("runs", {})
        if not isinstance(runs, dict):
            missing[surface] = list(EXPECTED_DECISIONS)
            continue
        missing_decisions = [decision for decision in EXPECTED_DECISIONS if decision not in runs]
        if missing_decisions:
            missing[surface] = missing_decisions
        for decision in EXPECTED_DECISIONS:
            run = runs.get(decision)
            if not isinstance(run, dict):
                continue
            expected_execution = "stopped" if decision == "reject" else "continued"
            facts = {
                "decision_class": run.get("decision_class"),
                "execution_state": run.get("execution_state"),
                "contract_version": run.get("contract_version"),
                "validator_result_schema_version": run.get("validator_result_schema_version"),
                "validator_valid": run.get("validator_valid"),
                "passed": run.get("passed"),
            }
            if facts != {
                "decision_class": decision,
                "execution_state": expected_execution,
                "contract_version": "v2",
                "validator_result_schema_version": "risk_contract_validator_result.v2",
                "validator_valid": True,
                "passed": True,
            }:
                invalid.append({"surface": surface, "run": decision, "facts": facts})
    return {
        "passed": not missing and not invalid,
        "expected_decisions": list(EXPECTED_DECISIONS),
        "missing": missing,
        "invalid": invalid,
    }


def validator_replay_check(
    validator_payloads: dict[tuple[str, str], dict[str, Any]],
    live_validator_payload: dict[str, Any],
) -> dict[str, Any]:
    invalid: list[dict[str, Any]] = []
    validated = 0
    for surface in EXPECTED_SURFACES:
        for decision in EXPECTED_DECISIONS:
            payload = validator_payloads.get((surface, decision))
            if not isinstance(payload, dict):
                invalid.append(
                    {
                        "surface": surface,
                        "decision": decision,
                        "reason": "missing_validator_payload",
                    }
                )
                continue
            ok = (
                payload.get("schema_version") == "risk_contract_validator_result.v2"
                and payload.get("valid") is True
                and payload.get("contract_identity", {}).get("schema_version")
                == "risk_contract.v2"
                and payload.get("contract_identity", {}).get("contract_version") == "v2"
                and payload.get("validated_schema", {}).get("path")
                == "risk/contracts/v2/risk_contract_v2.schema.json"
            )
            if ok:
                validated += 1
            else:
                invalid.append(
                    {
                        "surface": surface,
                        "decision": decision,
                        "payload": payload,
                    }
                )
    live_ok = (
        live_validator_payload.get("schema_version") == "risk_contract_validator_result.v2"
        and live_validator_payload.get("valid") is True
        and live_validator_payload.get("contract_identity", {}).get("schema_version")
        == "risk_contract.v2"
        and live_validator_payload.get("contract_identity", {}).get("contract_version") == "v2"
    )
    return {
        "passed": not invalid and live_ok,
        "validated_artifacts": validated,
        "expected_artifacts": len(EXPECTED_SURFACES) * len(EXPECTED_DECISIONS),
        "live_fixture_validated": live_ok,
        "invalid": invalid,
    }


def live_not_claimable_check(live_fixture: dict[str, Any]) -> dict[str, Any]:
    surface = live_fixture.get("candidate", {}).get("surface", {})
    application = live_fixture.get("application", {})
    facts = {
        "schema_version": live_fixture.get("schema_version"),
        "contract_version": live_fixture.get("contract_version"),
        "runtime_surface": surface.get("runtime_surface"),
        "surface_status": surface.get("surface_status"),
        "analysis_scope": surface.get("analysis_scope"),
        "application_status": application.get("application_status"),
        "runtime_sizing_applied": application.get("runtime_sizing_applied"),
        "metrics_rescaled": application.get("metrics_rescaled"),
    }
    passed = facts == {
        "schema_version": "risk_contract.v2",
        "contract_version": "v2",
        "runtime_surface": "live",
        "surface_status": "not_wired",
        "analysis_scope": "none",
        "application_status": "not_claimable",
        "runtime_sizing_applied": False,
        "metrics_rescaled": False,
    }
    return {"passed": passed, **facts}


def protected_surface_guard_check(
    report_dir: Path, changed_paths: list[str]
) -> dict[str, Any]:
    violations = [
        canonical_relative(path)
        for path in changed_paths
        if is_under_prefix(path, PROTECTED_CHANGED_PREFIXES)
    ]
    return {
        "passed": not violations,
        "report_dir": canonical_relative(report_dir),
        "allowed_output_prefix": DEFAULT_REPORT_DIR.as_posix(),
        "violations": sorted(violations),
        "protected_changed_prefixes": list(PROTECTED_CHANGED_PREFIXES),
    }


def no_runtime_expansion_check(changed_paths: list[str]) -> dict[str, Any]:
    violations = [
        canonical_relative(path)
        for path in changed_paths
        if is_under_prefix(path, RUNTIME_EXPANSION_PREFIXES)
    ]
    return {
        "passed": not violations,
        "live_runtime_adoption": "not_approved",
        "live_broker_sizing_units": "not_approved",
        "scan_wfd_metric_rescaling": "not_approved",
        "runtime_code_violations": sorted(violations),
    }


def live_runtime_surface_absent_check() -> dict[str, Any]:
    root = repo_root()
    main_path = root / LIVE_RUNTIME_SURFACE_SOURCE
    main_text = main_path.read_text(encoding="utf-8") if main_path.exists() else ""
    live_subcommand_present = "Live(" in main_text or "Commands::Live" in main_text

    live_runtime_paths: list[str] = []
    for relative_path in LIVE_RUNTIME_IMPLEMENTATION_PATHS:
        candidate = root / relative_path
        if candidate.is_file():
            live_runtime_paths.append(canonical_relative(candidate))
        elif candidate.is_dir():
            live_runtime_paths.extend(
                canonical_relative(path)
                for path in sorted(candidate.rglob("*"))
                if path.is_file()
            )

    return {
        "passed": not live_subcommand_present and not live_runtime_paths,
        "live_runtime_adoption": "not_approved",
        "source": LIVE_RUNTIME_SURFACE_SOURCE.as_posix(),
        "source_exists": main_path.exists(),
        "live_subcommand_present": live_subcommand_present,
        "live_runtime_paths": sorted(live_runtime_paths),
        "claim_block_reason": "live_runtime_not_implemented",
    }


def build_adoption_closure_audit(
    *,
    report_dir: Path,
    diff_base: str,
    surface_reports: dict[str, dict[str, Any]] | None = None,
    live_fixture: dict[str, Any] | None = None,
    validator_payloads: dict[tuple[str, str], dict[str, Any]] | None = None,
    live_validator_payload: dict[str, Any] | None = None,
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    ensure_safe_output_dir(report_dir)
    reports = surface_reports if surface_reports is not None else load_surface_reports()
    live = live_fixture if live_fixture is not None else load_live_fixture()
    replay_payloads = (
        validator_payloads if validator_payloads is not None else collect_validator_payloads(reports)
    )
    live_payload = (
        live_validator_payload
        if live_validator_payload is not None
        else run_validator(LIVE_FIXTURE_PATH)
    )
    changed = changed_paths if changed_paths is not None else changed_paths_since(diff_base)

    checks = {
        "surface_reports_pass": surface_reports_pass_check(reports),
        "decision_class_matrix": decision_class_matrix_check(reports),
        "validator_replay": validator_replay_check(replay_payloads, live_payload),
        "live_not_claimable": live_not_claimable_check(live),
        "live_runtime_surface_absent": live_runtime_surface_absent_check(),
        "protected_surface_guard": protected_surface_guard_check(report_dir, changed),
        "no_runtime_expansion": no_runtime_expansion_check(changed),
    }
    checks_failed = sum(1 for check in checks.values() if not check["passed"])
    checks_passed = len(checks) - checks_failed
    status = "PASS" if checks_failed == 0 else "FAIL"

    surface_summaries = []
    for surface in EXPECTED_SURFACES:
        report = reports.get(surface, {})
        summary = report.get("summary", {})
        runs = report.get("runs", {})
        surface_summaries.append(
            {
                "surface": surface,
                "report_status": summary.get("overall_status"),
                "implementation_scope": summary.get("implementation_scope"),
                "decisions": sorted(runs.keys()) if isinstance(runs, dict) else [],
                "report_path": SURFACE_REPORTS.get(surface, Path("")).as_posix(),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "boundary": BOUNDARY,
        "summary": {
            "overall_status": status,
            "checks_passed": checks_passed,
            "checks_failed": checks_failed,
            "diff_base": diff_base,
            "implementation_scope": "v2_adoption_closure_audit_only",
            "surfaces_closed": list(EXPECTED_SURFACES),
            "live_runtime_claim": "not_claimable",
            "live_runtime_adoption": "not_approved",
            "new_runtime_behavior": "none",
        },
        "source_evidence": source_evidence_rows(),
        "surface_reports": surface_summaries,
        "checks": checks,
        "changed_paths": sorted(canonical_relative(path) for path in changed),
        "closure_statement": {
            "claim": (
                "risk_contract.v2 explicit adoption is replay-evidenced for backtest, scan, and paper; "
                "live runtime adoption remains not approved and not claimable."
            ),
            "does_not_claim": [
                "live runtime adoption",
                "live/broker sizing units",
                "true scan/WFD metric rescaling",
                "full workspace verification",
            ],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# risk_contract.v2 Adoption Closure Audit",
        "",
        f"- Schema: `{report['schema_version']}`",
        f"- Boundary: `{report['boundary']}`",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Implementation scope: `{summary['implementation_scope']}`",
        f"- Live claim: `{summary['live_runtime_claim']}`; live runtime adoption remains not approved",
        "",
        "## Surface Reports",
        "",
        "| Surface | Status | Decisions | Scope | Report |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["surface_reports"]:
        decisions = ", ".join(row["decisions"])
        lines.append(
            f"| {row['surface']} | {row['report_status']} | {decisions} | "
            f"{row['implementation_scope']} | `{row['report_path']}` |"
        )

    lines.extend(
        [
            "",
            "## Checks",
            "",
            "| Check | Passed | Detail |",
            "| --- | --- | --- |",
        ]
    )
    for check_id, check in report["checks"].items():
        detail_parts = []
        if check_id == "validator_replay":
            detail_parts.append(
                f"validated_artifacts={check['validated_artifacts']}/{check['expected_artifacts']}"
            )
            detail_parts.append(f"live_fixture_validated={check['live_fixture_validated']}")
        elif check_id == "live_not_claimable":
            detail_parts.append(f"surface_status={check['surface_status']}")
            detail_parts.append(f"application_status={check['application_status']}")
            detail_parts.append(f"runtime_sizing_applied={check['runtime_sizing_applied']}")
        elif check_id == "live_runtime_surface_absent":
            detail_parts.append(f"live_subcommand_present={check['live_subcommand_present']}")
            detail_parts.append(f"live_runtime_paths={len(check['live_runtime_paths'])}")
            detail_parts.append(f"claim_block_reason={check['claim_block_reason']}")
        elif check_id == "protected_surface_guard":
            detail_parts.append(f"violations={len(check['violations'])}")
            detail_parts.append(f"report_dir={check['report_dir']}")
        elif check_id == "no_runtime_expansion":
            detail_parts.append(f"live_runtime_adoption={check['live_runtime_adoption']}")
            detail_parts.append(f"runtime_code_violations={len(check['runtime_code_violations'])}")
        elif check_id == "decision_class_matrix":
            detail_parts.append("expected_decisions=" + ", ".join(check["expected_decisions"]))
            detail_parts.append(f"invalid={len(check['invalid'])}")
        elif check_id == "surface_reports_pass":
            detail_parts.append("loaded_surfaces=" + ", ".join(check["loaded_surfaces"]))
            detail_parts.append(f"failing_surfaces={len(check['failing_surfaces'])}")
        lines.append(
            f"| {check_id} | {str(check['passed']).lower()} | {'; '.join(detail_parts)} |"
        )

    lines.extend(
        [
            "",
            "## Closure Statement",
            "",
            report["closure_statement"]["claim"],
            "",
            "This audit does not claim:",
        ]
    )
    for item in report["closure_statement"]["does_not_claim"]:
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Changed Paths Considered",
            "",
        ]
    )
    if report["changed_paths"]:
        for path in report["changed_paths"]:
            lines.append(f"- `{path}`")
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], report_dir: Path) -> dict[str, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / JSON_REPORT_NAME
    markdown_path = report_dir / MD_REPORT_NAME
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--diff-base", default="HEAD")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    planned_outputs = [
        (args.report_dir / JSON_REPORT_NAME).as_posix(),
        (args.report_dir / MD_REPORT_NAME).as_posix(),
    ]
    changed_paths = sorted(set(changed_paths_since(args.diff_base) + planned_outputs))
    try:
        report = build_adoption_closure_audit(
            report_dir=args.report_dir,
            diff_base=args.diff_base,
            changed_paths=changed_paths,
        )
    except Exception as exc:
        print(f"generate_risk_contract_v2_adoption_closure_audit.py: {exc}", file=sys.stderr)
        return 2
    paths = write_report(report, args.report_dir)
    print(
        json.dumps(
            {
                "schema_version": report["schema_version"],
                "overall_status": report["summary"]["overall_status"],
                "checks_passed": report["summary"]["checks_passed"],
                "checks_failed": report["summary"]["checks_failed"],
                "json_report": paths["json"].as_posix(),
                "markdown_report": paths["markdown"].as_posix(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["summary"]["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
