#!/usr/bin/env python3
"""Generate deterministic v5.5 risk contract drift-gate evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import generate_risk_contract_evidence as v54_evidence
from scripts import validate_risk_contract as validator


SCHEMA_VERSION = "risk_contract_drift_gate_evidence.v1"
RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
PHASE = 130
REQUIREMENTS_ADDRESSED = ["GATE-01", "GATE-02", "EVID-01"]
DEFAULT_MATRIX = Path("risk/contracts/v1/fixture_matrix.json")
DEFAULT_VALIDATOR = Path("scripts/validate_risk_contract.py")
DEFAULT_REPORT_DIR = Path("reports/v5.5")
JSON_REPORT_NAME = "risk_contract_drift_gate_evidence.json"
MD_REPORT_NAME = "risk_contract_drift_gate_evidence.md"
PHASE130_DIR = ".planning/phases/130-drift-gate-and-evidence-closure"

SCHEMA_PATH = Path("risk/contracts/v1/risk_contract_v1.schema.json")
V4_ARCHIVE_PATHS = (
    ".planning/milestones/v4*",
    "data/v4*",
    "docs/reports/v4*",
)
V4_ARCHIVE_PREFIXES = (
    ".planning/milestones/v4",
    "data/v4",
    "docs/reports/v4",
)
ALLOWED_PHASE130_PREFIXES = (
    ".planning/phases/130-drift-gate-and-evidence-closure/",
    "reports/v5.5/",
)
ALLOWED_PHASE130_PATHS = {
    ".planning/REQUIREMENTS.md",
    ".planning/STATE.md",
    ".planning/ROADMAP.md",
    "scripts/generate_risk_contract_drift_gate_evidence.py",
    "scripts/validate_risk_contract.py",
    "tests/test_validate_risk_contract.py",
    "tests/test_generate_risk_contract_evidence.py",
}
FORBIDDEN_RUNTIME_PREFIXES = (
    "backtest/",
    "paper_trading/",
    "side-cli/",
    "side-engine/",
    "side-mirror/",
)
RUNTIME_SCOPE_COMMANDS = {
    "committed": ["git", "diff", "--name-only"],
    "unstaged": ["git", "diff", "--name-only"],
    "staged": ["git", "diff", "--cached", "--name-only"],
    "untracked": ["git", "ls-files", "--others", "--exclude-standard"],
}
EXPECTED_SCHEMA_FACTS = {
    "decision_classes": ("block", "cap", "kill", "reject", "size"),
    "fail_close_rule_decision_classes": ("block", "kill"),
    "fail_close_reasons": (
        "absent_source_proof",
        "candidate_validation_failure",
        "evidence_acquisition_failure",
        "insufficient_validation_power",
        "malformed_policy",
        "missing_required_policy_field",
        "policy_evidence_contradiction",
        "stale_evidence",
    ),
    "required_top_level": (
        "schema_version",
        "contract_version",
        "policy",
        "candidate",
        "evidence",
        "context",
        "decision",
        "trace",
    ),
    "required_nested_fields": {
        "policy": (
            "version",
            "owner",
            "effective_from",
            "required_fields",
            "fail_close_rules",
        ),
        "candidate": (
            "strategy_id",
            "symbol_or_universe",
            "timeframe",
            "validation_refs",
        ),
        "evidence": ("refs",),
        "decision": (
            "decision_class",
            "allowed_size",
            "binding_rule",
            "supporting_rules",
            "fail_close_reason",
            "evidence_refs",
            "policy_version",
        ),
        "trace": (
            "policy_version",
            "candidate_id",
            "input_evidence_refs",
            "binding_rule",
            "decision_class",
            "emitted_artifact_path",
        ),
    },
    "required_fail_close_rule_fields": (
        "condition",
        "decision_class",
        "fail_close_reason",
    ),
}


def repo_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def command_string(command: list[str]) -> str:
    return " ".join(command)


def split_paths(stdout: str) -> list[str]:
    return sorted(line.strip() for line in stdout.splitlines() if line.strip())


def missing_path_command(command: list[str], message: str) -> dict[str, Any]:
    return {
        "command": command_string(command),
        "command_vector": command,
        "exit_code": 1,
        "stderr": message,
        "paths": [],
    }


def run_path_command(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": command_string(command),
        "command_vector": command,
        "exit_code": result.returncode,
        "stderr": result.stderr,
        "paths": split_paths(result.stdout),
    }


def injected_path_command(
    command: list[str],
    paths: Iterable[str],
    path_filter: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    return {
        "command": command_string(command),
        "command_vector": command,
        "exit_code": 0,
        "stderr": "",
        "paths": sorted(
            path for path in paths if path and (path_filter is None or path_filter(path))
        ),
    }


def is_v4_archive_path(path: str) -> bool:
    return path.startswith(V4_ARCHIVE_PREFIXES)


def is_allowed_phase130_path(path: str) -> bool:
    return path in ALLOWED_PHASE130_PATHS or path.startswith(ALLOWED_PHASE130_PREFIXES)


def is_forbidden_runtime_path(path: str) -> bool:
    return path.startswith(FORBIDDEN_RUNTIME_PREFIXES)


def normalized_schema_facts(schema: dict[str, Any]) -> dict[str, Any]:
    facts = validator.schema_facts(schema)
    return {
        "decision_classes": tuple(sorted(facts["decision_classes"])),
        "fail_close_rule_decision_classes": tuple(
            sorted(facts["fail_close_rule_decision_classes"])
        ),
        "fail_close_reasons": tuple(sorted(facts["fail_close_reasons"])),
        "required_top_level": tuple(facts["required_top_level"]),
        "required_nested_fields": {
            key: tuple(value)
            for key, value in facts["required_nested_fields"].items()
        },
        "required_fail_close_rule_fields": tuple(
            facts["required_fail_close_rule_fields"]
        ),
    }


def changed_schema_fact_keys(observed: dict[str, Any]) -> list[str]:
    return sorted(
        key
        for key, expected in EXPECTED_SCHEMA_FACTS.items()
        if observed.get(key) != expected
    )


def schema_fact_snapshot_check(
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observed = normalized_schema_facts(schema or validator.load_contract_schema())
    changed_keys = changed_schema_fact_keys(observed)
    passed = changed_keys == []
    return {
        "name": "schema_fact_snapshot",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "expected": EXPECTED_SCHEMA_FACTS,
        "observed": observed,
        "changed_keys": changed_keys,
    }


def add_decision_class(schema: dict[str, Any]) -> None:
    schema["properties"]["decision"]["properties"]["decision_class"]["enum"].append(
        "halt"
    )
    schema["properties"]["trace"]["properties"]["decision_class"]["enum"].append("halt")


def add_fail_close_reason(schema: dict[str, Any]) -> None:
    schema["$defs"]["fail_close_reason"]["enum"].append("provider_timeout")


def add_top_level_required_field(schema: dict[str, Any]) -> None:
    schema["required"].append("governance")


def add_policy_required_field(schema: dict[str, Any]) -> None:
    schema["properties"]["policy"]["required"].append("risk_budget")


def add_fail_close_rule_required_field(schema: dict[str, Any]) -> None:
    schema["$defs"]["fail_close_rule"]["required"].append("severity")


def synthetic_mutation_checks(
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_schema = schema or validator.load_contract_schema()
    cases: list[dict[str, Any]] = []
    mutations: tuple[tuple[str, str, Callable[[dict[str, Any]], None]], ...] = (
        ("decision_class_vocab_added", "decision_classes", add_decision_class),
        ("fail_close_reason_added", "fail_close_reasons", add_fail_close_reason),
        ("top_level_required_added", "required_top_level", add_top_level_required_field),
        ("policy_required_added", "required_nested_fields", add_policy_required_field),
        (
            "fail_close_rule_required_added",
            "required_fail_close_rule_fields",
            add_fail_close_rule_required_field,
        ),
    )

    for case_id, expected_changed_key, mutate in mutations:
        mutated_schema = deepcopy(base_schema)
        mutate(mutated_schema)
        observed = normalized_schema_facts(mutated_schema)
        changed_keys = changed_schema_fact_keys(observed)
        drift_detected = expected_changed_key in changed_keys
        cases.append(
            {
                "id": case_id,
                "expected_changed_key": expected_changed_key,
                "changed_keys": changed_keys,
                "drift_detected": drift_detected,
                "passed": drift_detected is True,
            }
        )

    passed = all(case["passed"] for case in cases)
    return {
        "name": "synthetic_mutations",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "cases": cases,
    }


def fixture_replay_check(
    matrix_path: Path = DEFAULT_MATRIX,
    validator_path: Path = DEFAULT_VALIDATOR,
) -> dict[str, Any]:
    fixtures = [
        v54_evidence.replay_fixture(fixture, validator_path)
        for fixture in v54_evidence.load_matrix(matrix_path)
    ]
    passed_count = sum(1 for fixture in fixtures if fixture["passed"])
    failed_count = len(fixtures) - passed_count
    passed = failed_count == 0
    return {
        "name": "fixture_replay",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "fixture_count": len(fixtures),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "fixtures": fixtures,
    }


def discover_phase130_diff_base() -> str | None:
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%H", "--", PHASE130_DIR],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    first_commit = next(
        (line.strip() for line in result.stdout.splitlines() if line.strip()),
        None,
    )
    if first_commit is None:
        return None
    parent = subprocess.run(
        ["git", "rev-parse", f"{first_commit}^"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if parent.returncode != 0:
        return None
    return parent.stdout.strip()


def collect_v4_archive_scope(
    diff_base: str | None = None,
    committed_changed_paths: Iterable[str] | None = None,
    changed_paths: Iterable[str] | None = None,
    staged_changed_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    working_command_vector = ["git", "diff", "--name-only", "--", *V4_ARCHIVE_PATHS]
    working_command = (
        injected_path_command(working_command_vector, changed_paths, is_v4_archive_path)
        if changed_paths is not None
        else run_path_command(working_command_vector)
    )

    committed_command_vector = [
        "git",
        "diff",
        "--name-only",
        f"{diff_base or '<missing-diff-base>'}..HEAD",
        "--",
        *V4_ARCHIVE_PATHS,
    ]
    if diff_base is None and committed_changed_paths is None:
        committed_command = missing_path_command(
            committed_command_vector,
            "missing Phase 130 diff base",
        )
    else:
        committed_command = (
            injected_path_command(
                committed_command_vector,
                committed_changed_paths,
                is_v4_archive_path,
            )
            if committed_changed_paths is not None
            else run_path_command(committed_command_vector)
        )

    staged_command_vector = [
        "git",
        "diff",
        "--cached",
        "--name-only",
        "--",
        *V4_ARCHIVE_PATHS,
    ]
    staged_command = (
        injected_path_command(
            staged_command_vector,
            staged_changed_paths,
            is_v4_archive_path,
        )
        if staged_changed_paths is not None
        else run_path_command(staged_command_vector)
    )

    modified_archive_paths = sorted(
        set(working_command["paths"])
        | set(committed_command["paths"])
        | set(staged_command["paths"])
    )
    commands_passed = (
        working_command["exit_code"] == 0
        and committed_command["exit_code"] == 0
        and staged_command["exit_code"] == 0
    )
    passed = commands_passed and modified_archive_paths == []
    return {
        "name": "v4_archive_scope",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "diff_base": diff_base,
        "working_command": working_command,
        "committed_command": committed_command,
        "staged_command": staged_command,
        "modified_archive_paths": modified_archive_paths,
        "commands_passed": commands_passed,
    }


def collect_runtime_scope(
    diff_base: str | None = None,
    committed_changed_paths: Iterable[str] | None = None,
    changed_paths: Iterable[str] | None = None,
    staged_changed_paths: Iterable[str] | None = None,
    untracked_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    committed_command_vector = (
        RUNTIME_SCOPE_COMMANDS["committed"] + [f"{diff_base}..HEAD"]
        if diff_base is not None
        else RUNTIME_SCOPE_COMMANDS["committed"] + ["<missing-diff-base>..HEAD"]
    )
    if diff_base is None and committed_changed_paths is None:
        committed_command = missing_path_command(
            committed_command_vector,
            "missing Phase 130 diff base",
        )
    else:
        committed_command = (
            injected_path_command(committed_command_vector, committed_changed_paths)
            if committed_changed_paths is not None
            else run_path_command(committed_command_vector)
        )

    unstaged_command = (
        injected_path_command(RUNTIME_SCOPE_COMMANDS["unstaged"], changed_paths)
        if changed_paths is not None
        else run_path_command(RUNTIME_SCOPE_COMMANDS["unstaged"])
    )
    staged_command = (
        injected_path_command(RUNTIME_SCOPE_COMMANDS["staged"], staged_changed_paths)
        if staged_changed_paths is not None
        else run_path_command(RUNTIME_SCOPE_COMMANDS["staged"])
    )
    untracked_command = (
        injected_path_command(RUNTIME_SCOPE_COMMANDS["untracked"], untracked_paths)
        if untracked_paths is not None
        else run_path_command(RUNTIME_SCOPE_COMMANDS["untracked"])
    )

    changed_path_union = sorted(
        set(committed_command["paths"])
        | set(unstaged_command["paths"])
        | set(staged_command["paths"])
        | set(untracked_command["paths"])
    )
    allowed_phase130_paths: list[str] = []
    forbidden_runtime_paths: list[str] = []
    forbidden_v4_archive_paths: list[str] = []
    unexpected_paths: list[str] = []

    for path in changed_path_union:
        if is_v4_archive_path(path):
            forbidden_v4_archive_paths.append(path)
        elif is_forbidden_runtime_path(path):
            forbidden_runtime_paths.append(path)
        elif is_allowed_phase130_path(path):
            allowed_phase130_paths.append(path)
        else:
            unexpected_paths.append(path)

    commands_passed = (
        committed_command["exit_code"] == 0
        and unstaged_command["exit_code"] == 0
        and staged_command["exit_code"] == 0
        and untracked_command["exit_code"] == 0
    )
    passed = (
        commands_passed
        and forbidden_runtime_paths == []
        and forbidden_v4_archive_paths == []
        and unexpected_paths == []
    )
    return {
        "name": "runtime_scope",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "diff_base": diff_base,
        "committed_command": committed_command,
        "unstaged_command": unstaged_command,
        "staged_command": staged_command,
        "untracked_command": untracked_command,
        "committed_changed_paths": committed_command["paths"],
        "unstaged_changed_paths": unstaged_command["paths"],
        "staged_changed_paths": staged_command["paths"],
        "untracked_paths": untracked_command["paths"],
        "changed_path_union": changed_path_union,
        "allowed_phase130_paths": allowed_phase130_paths,
        "forbidden_runtime_paths": forbidden_runtime_paths,
        "forbidden_v4_archive_paths": forbidden_v4_archive_paths,
        "unexpected_paths": unexpected_paths,
        "commands_passed": commands_passed,
    }


def build_drift_gate_evidence(
    matrix_path: Path = DEFAULT_MATRIX,
    validator_path: Path = DEFAULT_VALIDATOR,
    diff_base: str | None = None,
    committed_changed_paths: Iterable[str] | None = None,
    changed_paths: Iterable[str] | None = None,
    staged_changed_paths: Iterable[str] | None = None,
    untracked_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    resolved_diff_base = diff_base if diff_base is not None else discover_phase130_diff_base()
    schema = validator.load_contract_schema()
    checks = {
        "schema_fact_snapshot": schema_fact_snapshot_check(schema),
        "synthetic_mutations": synthetic_mutation_checks(schema),
        "fixture_replay": fixture_replay_check(matrix_path, validator_path),
        "runtime_scope": collect_runtime_scope(
            diff_base=resolved_diff_base,
            committed_changed_paths=committed_changed_paths,
            changed_paths=changed_paths,
            staged_changed_paths=staged_changed_paths,
            untracked_paths=untracked_paths,
        ),
        "v4_archive_scope": collect_v4_archive_scope(
            diff_base=resolved_diff_base,
            committed_changed_paths=committed_changed_paths,
            changed_paths=changed_paths,
            staged_changed_paths=staged_changed_paths,
        ),
    }
    checks_passed = sum(1 for check in checks.values() if check["passed"])
    checks_failed = len(checks) - checks_passed
    close_ready = checks_failed == 0
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "requirements_addressed": REQUIREMENTS_ADDRESSED,
        "source_inputs": {
            "schema": display_path(repo_path(SCHEMA_PATH)),
            "fixture_matrix": display_path(repo_path(matrix_path)),
            "validator": display_path(repo_path(validator_path)),
            "diff_base": resolved_diff_base,
        },
        "summary": {
            "overall_status": "PASS" if close_ready else "FAIL",
            "checks_passed": checks_passed,
            "checks_failed": checks_failed,
            "close_readiness": (
                "ready_for_milestone_completion" if close_ready else "blocked"
            ),
        },
        "checks": checks,
    }


def check_detail(check: dict[str, Any]) -> str:
    if check["name"] == "schema_fact_snapshot":
        if check["passed"]:
            return "No schema fact drift detected."
        return "Changed keys: " + ", ".join(check["changed_keys"])
    if check["name"] == "synthetic_mutations":
        passed_cases = sum(1 for case in check["cases"] if case["passed"])
        return f"{passed_cases}/{len(check['cases'])} representative mutations detected."
    if check["name"] == "fixture_replay":
        return (
            f"{check['passed_count']}/{check['fixture_count']} fixture replays passed."
        )
    if check["name"] == "runtime_scope":
        blocked = (
            len(check["forbidden_runtime_paths"])
            + len(check["forbidden_v4_archive_paths"])
            + len(check["unexpected_paths"])
        )
        return f"{blocked} forbidden or unexpected runtime path(s)."
    if check["name"] == "v4_archive_scope":
        return f"{len(check['modified_archive_paths'])} v4 archive path(s) modified."
    return ""


def render_markdown(evidence: dict[str, Any]) -> str:
    checks = evidence["checks"]
    lines = [
        "# v5.5 Risk Contract Drift Gate Evidence",
        "",
        f"Schema version: `{evidence['schema_version']}`",
        f"Phase: {evidence['phase']}",
        f"Requirements: {', '.join(evidence['requirements_addressed'])}",
        "",
        "## Audit Summary",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for key in (
        "schema_fact_snapshot",
        "synthetic_mutations",
        "fixture_replay",
        "runtime_scope",
        "v4_archive_scope",
    ):
        check = checks[key]
        lines.append(f"| {key} | {check['status']} | {check_detail(check)} |")

    summary = evidence["summary"]
    lines.extend(
        [
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Overall status | {summary['overall_status']} |",
            f"| Checks passed | {summary['checks_passed']} |",
            f"| Checks failed | {summary['checks_failed']} |",
            f"| Close readiness | {summary['close_readiness']} |",
            f"| Diff base | `{evidence['source_inputs']['diff_base']}` |",
            "",
            "## Schema Fact Snapshot",
            "",
            f"- Status: {checks['schema_fact_snapshot']['status']}",
            f"- Changed keys: {checks['schema_fact_snapshot']['changed_keys']}",
            "",
            "## Synthetic Mutation Proof",
            "",
            "| Case | Expected changed key | Drift detected | Changed keys |",
            "| --- | --- | --- | --- |",
        ]
    )
    for case in checks["synthetic_mutations"]["cases"]:
        lines.append(
            f"| {case['id']} | {case['expected_changed_key']} | "
            f"{case['drift_detected']} | {case['changed_keys']} |"
        )

    lines.extend(
        [
            "",
            "## Fixture Replay",
            "",
            "| Fixture | Expected valid | Exit code | Actual error | Passed |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for fixture in checks["fixture_replay"]["fixtures"]:
        lines.append(
            f"| `{fixture['id']}` | {fixture['expected_valid']} | "
            f"{fixture['exit_code']} | `{fixture['actual_error']}` | "
            f"{fixture['passed']} |"
        )

    lines.extend(["", "## Fixture Details", ""])
    for fixture in checks["fixture_replay"]["fixtures"]:
        lines.extend(
            [
                f"### {fixture['id']}",
                "",
                f"- Path: `{fixture['path']}`",
                f"- Description: {fixture['description']}",
                f"- Command: `{command_string(fixture['command'])}`",
                f"- Exit code: {fixture['exit_code']}",
                f"- Expected valid: {fixture['expected_valid']}",
                f"- Expected error: `{fixture['expected_error']}`",
                f"- Actual error: `{fixture['actual_error']}`",
                f"- Passed: {fixture['passed']}",
                "- Validator payload:",
                "",
                "```json",
                json.dumps(
                    fixture["validator_payload"],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "```",
                "",
            ]
        )

    runtime = checks["runtime_scope"]
    archive = checks["v4_archive_scope"]
    lines.extend(
        [
            "## Runtime Scope",
            "",
            "| Category | Paths |",
            "| --- | --- |",
            f"| Allowed Phase 130 paths | {runtime['allowed_phase130_paths']} |",
            f"| Forbidden runtime paths | {runtime['forbidden_runtime_paths']} |",
            f"| Forbidden v4 archive paths | {runtime['forbidden_v4_archive_paths']} |",
            f"| Unexpected paths | {runtime['unexpected_paths']} |",
            "",
            "### Runtime Scope Commands",
            "",
            f"- Committed: `{runtime['committed_command']['command']}`",
            f"- Unstaged: `{runtime['unstaged_command']['command']}`",
            f"- Staged: `{runtime['staged_command']['command']}`",
            f"- Untracked: `{runtime['untracked_command']['command']}`",
            "",
            "## v4 Archive Scope",
            "",
            f"- Modified archive paths: {archive['modified_archive_paths']}",
            f"- Committed command: `{archive['committed_command']['command']}`",
            f"- Working command: `{archive['working_command']['command']}`",
            f"- Staged command: `{archive['staged_command']['command']}`",
        ]
    )

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def write_json(evidence: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--validator", type=Path, default=DEFAULT_VALIDATOR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--diff-base",
        default=None,
        help="Git base commit for Phase 130 committed diff proof.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        evidence = build_drift_gate_evidence(
            matrix_path=args.matrix,
            validator_path=args.validator,
            diff_base=args.diff_base,
        )
        report_dir = repo_path(args.report_dir)
        write_json(evidence, report_dir / JSON_REPORT_NAME)
        (report_dir / MD_REPORT_NAME).write_text(
            render_markdown(evidence),
            encoding="utf-8",
        )
    except SystemExit as exc:
        return int(exc.code)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"generate_risk_contract_drift_gate_evidence.py: {exc}", file=sys.stderr)
        return 1

    return (
        0
        if evidence["summary"]["close_readiness"] == "ready_for_milestone_completion"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
