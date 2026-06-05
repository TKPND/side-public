#!/usr/bin/env python3
"""Generate deterministic v5.4 risk contract validation evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "risk_contract_validation_evidence.v1"
RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
PHASE = 128
REQUIREMENTS_ADDRESSED = ["RISKVAL-08", "RISKVAL-09", "RISKVAL-10"]
DEFAULT_MATRIX = Path("risk/contracts/v1/fixture_matrix.json")
DEFAULT_VALIDATOR = Path("scripts/validate_risk_contract.py")
DEFAULT_REPORT_DIR = Path("reports/v5.4")
JSON_REPORT_NAME = "risk_contract_validation_evidence.json"
MD_REPORT_NAME = "risk_contract_validation_evidence.md"

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIFF_COMMAND = [
    "git",
    "diff",
    "--name-only",
    "--",
    ".planning/milestones/v4*",
    "data/v4*",
    "docs/reports/v4*",
]
ARCHIVE_DIFF_COMMAND_STRING = (
    "git diff --name-only -- .planning/milestones/v4* data/v4* docs/reports/v4*"
)
RUNTIME_SCOPE_COMMANDS = {
    "committed": ["git", "diff", "--name-only"],
    "unstaged": ["git", "diff", "--name-only"],
    "staged": ["git", "diff", "--cached", "--name-only"],
    "untracked": ["git", "ls-files", "--others", "--exclude-standard"],
}
KNOWN_PRE_EXISTING_UNRELATED_PATHS = {"AGENTS.md", ".planning/PROJECT.md"}
DEFERRED_SCOPE_NOTES = [
    "common risk module remains deferred outside v5.4 closure evidence.",
    "paper guard remains deferred outside v5.4 closure evidence.",
    "strategy integration remains deferred outside v5.4 closure evidence.",
    "Rust CLI parity remains deferred outside v5.4 closure evidence.",
    "runtime behavior changes remain deferred outside v5.4 closure evidence.",
]
ALLOWED_PHASE128_PREFIXES = (
    ".planning/phases/126-contract-artifacts-and-fixture-matrix/",
    ".planning/phases/127-validator-cli-and-semantic-checks/",
    ".planning/phases/128-evidence-report-and-closure-gates/",
    "reports/v5.4/",
)
ALLOWED_PHASE128_PATHS = {
    ".planning/MILESTONES.md",
    ".planning/REQUIREMENTS.md",
    ".planning/RETROSPECTIVE.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
    ".planning/milestones/v5.4-MILESTONE-AUDIT.md",
    ".planning/milestones/v5.4-REQUIREMENTS.md",
    ".planning/milestones/v5.4-ROADMAP.md",
    ".planning/v5.4-MILESTONE-AUDIT.md",
    "scripts/generate_risk_contract_evidence.py",
    "tests/test_generate_risk_contract_evidence.py",
}
FORBIDDEN_RUNTIME_PREFIXES = (
    "backtest/",
    "paper/",
    "live/",
    "strategy/",
    "strategies/",
    "src/",
    "crates/",
)
FORBIDDEN_RUNTIME_FILES = {"Cargo.toml", "Cargo.lock", "pyproject.toml"}


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


def completed_to_path_command(
    command: list[str],
    result: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    return {
        "command": command_string(command),
        "command_vector": command,
        "exit_code": result.returncode,
        "stderr": result.stderr,
        "paths": split_paths(result.stdout),
    }


def discover_phase_diff_base() -> str | None:
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%H", f"--grep={PHASE}-0"],
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


def load_matrix(matrix_path: Path = DEFAULT_MATRIX) -> list[dict[str, Any]]:
    matrix = json.loads(repo_path(matrix_path).read_text(encoding="utf-8"))
    fixtures = matrix.get("fixtures")
    if not isinstance(fixtures, list):
        raise ValueError("fixture_matrix.json must contain a fixtures array")
    return fixtures


def actual_error_code(payload: dict[str, Any]) -> str | None:
    errors = payload.get("errors", [])
    if not errors:
        return None
    first = errors[0]
    if not isinstance(first, dict):
        return None
    code = first.get("code")
    return code if isinstance(code, str) else None


def replay_fixture(
    fixture: dict[str, Any],
    validator_path: Path = DEFAULT_VALIDATOR,
) -> dict[str, Any]:
    validator = repo_path(validator_path)
    execution_command = [sys.executable, str(validator), fixture["path"]]
    result = subprocess.run(
        execution_command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"validator stdout for {fixture['id']} is not JSON: {exc}") from exc

    if payload.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ValueError(
            f"validator stdout for {fixture['id']} has unexpected schema_version: "
            f"{payload.get('schema_version')!r}"
        )

    expected_valid = fixture["valid"]
    expected_error = fixture["expected_error"]
    actual_error = actual_error_code(payload)
    expected_exit_code = 0 if expected_valid else 1
    exit_code_matches = result.returncode == expected_exit_code
    validity_matches = payload.get("valid") is expected_valid
    error_code_matches = actual_error == expected_error

    return {
        "id": fixture["id"],
        "path": fixture["path"],
        "description": fixture["description"],
        "command": ["python", display_path(validator), fixture["path"]],
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "validator_payload": payload,
        "stderr": result.stderr,
        "expected_valid": expected_valid,
        "expected_error": expected_error,
        "actual_error": actual_error,
        "exit_code_matches": exit_code_matches,
        "validity_matches": validity_matches,
        "error_code_matches": error_code_matches,
        "passed": exit_code_matches and validity_matches and error_code_matches,
    }


def collect_archive_proof(
    archive_diff_result: subprocess.CompletedProcess[str] | None = None,
    committed_archive_diff_result: subprocess.CompletedProcess[str] | None = None,
    staged_archive_diff_result: subprocess.CompletedProcess[str] | None = None,
    diff_base: str | None = None,
) -> dict[str, Any]:
    working_result = archive_diff_result
    if working_result is None:
        working_result = subprocess.run(
            ARCHIVE_DIFF_COMMAND,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    working_command = completed_to_path_command(ARCHIVE_DIFF_COMMAND, working_result)

    command = [
        "git",
        "diff",
        "--name-only",
        f"{diff_base or '<missing-diff-base>'}..HEAD",
        "--",
        ".planning/milestones/v4*",
        "data/v4*",
        "docs/reports/v4*",
    ]
    if diff_base is None and committed_archive_diff_result is None:
        committed_command = missing_path_command(command, "missing Phase 128 diff base")
    else:
        committed_command = (
            completed_to_path_command(command, committed_archive_diff_result)
            if committed_archive_diff_result is not None
            else run_path_command(command)
        )

    staged_command_vector = [
        "git",
        "diff",
        "--cached",
        "--name-only",
        "--",
        ".planning/milestones/v4*",
        "data/v4*",
        "docs/reports/v4*",
    ]
    staged_command = (
        completed_to_path_command(staged_command_vector, staged_archive_diff_result)
        if staged_archive_diff_result is not None
        else run_path_command(staged_command_vector)
    )

    path_sets = [set(working_command["paths"]), set(staged_command["paths"])]
    path_sets.append(set(committed_command["paths"]))
    modified_archive_paths = sorted(set().union(*path_sets))
    commands_passed = working_command["exit_code"] == 0 and staged_command["exit_code"] == 0
    commands_passed = commands_passed and committed_command["exit_code"] == 0
    return {
        "command": ARCHIVE_DIFF_COMMAND_STRING,
        "command_vector": ARCHIVE_DIFF_COMMAND,
        "diff_base": diff_base,
        "working_command": working_command,
        "staged_command": staged_command,
        "committed_command": committed_command,
        "exit_code": working_command["exit_code"],
        "stderr": working_command["stderr"],
        "modified_archive_paths": modified_archive_paths,
        "passed": commands_passed and modified_archive_paths == [],
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


def injected_path_command(command: list[str], paths: Iterable[str]) -> dict[str, Any]:
    return {
        "command": command_string(command),
        "command_vector": command,
        "exit_code": 0,
        "stderr": "",
        "paths": sorted(path for path in paths if path),
    }


def is_v4_archive_path(path: str) -> bool:
    return (
        path.startswith(".planning/milestones/v4")
        or path.startswith("data/v4")
        or path.startswith("docs/reports/v4")
    )


def is_allowed_phase128_path(path: str) -> bool:
    return path in ALLOWED_PHASE128_PATHS or path.startswith(ALLOWED_PHASE128_PREFIXES)


def is_forbidden_runtime_path(path: str) -> bool:
    return path in FORBIDDEN_RUNTIME_FILES or path.startswith(FORBIDDEN_RUNTIME_PREFIXES)


def collect_runtime_scope_proof(
    diff_base: str | None = None,
    committed_changed_paths: Iterable[str] | None = None,
    changed_paths: Iterable[str] | None = None,
    staged_changed_paths: Iterable[str] | None = None,
    untracked_paths: Iterable[str] | None = None,
    committed_result: subprocess.CompletedProcess[str] | None = None,
    unstaged_result: subprocess.CompletedProcess[str] | None = None,
    staged_result: subprocess.CompletedProcess[str] | None = None,
    untracked_result: subprocess.CompletedProcess[str] | None = None,
) -> dict[str, Any]:
    command = (
        RUNTIME_SCOPE_COMMANDS["committed"] + [f"{diff_base}..HEAD"]
        if diff_base is not None
        else RUNTIME_SCOPE_COMMANDS["committed"] + ["<missing-diff-base>..HEAD"]
    )
    if diff_base is None and committed_changed_paths is None and committed_result is None:
        committed_command = missing_path_command(command, "missing Phase 128 diff base")
    else:
        committed_command = (
            injected_path_command(command, committed_changed_paths)
            if committed_changed_paths is not None
            else (
                completed_to_path_command(command, committed_result)
                if committed_result is not None
                else run_path_command(command)
            )
        )
    unstaged_command = (
        injected_path_command(RUNTIME_SCOPE_COMMANDS["unstaged"], changed_paths)
        if changed_paths is not None
        else (
            completed_to_path_command(RUNTIME_SCOPE_COMMANDS["unstaged"], unstaged_result)
            if unstaged_result is not None
            else run_path_command(RUNTIME_SCOPE_COMMANDS["unstaged"])
        )
    )
    staged_command = (
        injected_path_command(RUNTIME_SCOPE_COMMANDS["staged"], staged_changed_paths)
        if staged_changed_paths is not None
        else (
            completed_to_path_command(RUNTIME_SCOPE_COMMANDS["staged"], staged_result)
            if staged_result is not None
            else run_path_command(RUNTIME_SCOPE_COMMANDS["staged"])
        )
    )
    untracked_command = (
        injected_path_command(RUNTIME_SCOPE_COMMANDS["untracked"], untracked_paths)
        if untracked_paths is not None
        else (
            completed_to_path_command(RUNTIME_SCOPE_COMMANDS["untracked"], untracked_result)
            if untracked_result is not None
            else run_path_command(RUNTIME_SCOPE_COMMANDS["untracked"])
        )
    )

    path_sets = [
        set(unstaged_command["paths"]),
        set(staged_command["paths"]),
        set(untracked_command["paths"]),
    ]
    path_sets.append(set(committed_command["paths"]))
    union = sorted(set().union(*path_sets))
    allowed_phase128_paths: list[str] = []
    pre_existing_unrelated_paths: list[str] = []
    forbidden_runtime_paths: list[str] = []
    forbidden_v4_archive_paths: list[str] = []

    for path in union:
        if is_v4_archive_path(path):
            forbidden_v4_archive_paths.append(path)
        elif is_forbidden_runtime_path(path):
            forbidden_runtime_paths.append(path)
        elif path in KNOWN_PRE_EXISTING_UNRELATED_PATHS:
            pre_existing_unrelated_paths.append(path)
        elif is_allowed_phase128_path(path):
            allowed_phase128_paths.append(path)
        else:
            forbidden_runtime_paths.append(path)

    commands_passed = (
        unstaged_command["exit_code"] == 0
        and staged_command["exit_code"] == 0
        and untracked_command["exit_code"] == 0
        and committed_command["exit_code"] == 0
    )

    return {
        "diff_base": diff_base,
        "committed_command": committed_command,
        "unstaged_command": unstaged_command,
        "staged_command": staged_command,
        "untracked_command": untracked_command,
        "committed_changed_paths": committed_command["paths"],
        "unstaged_changed_paths": unstaged_command["paths"],
        "staged_changed_paths": staged_command["paths"],
        "untracked_paths": untracked_command["paths"],
        "changed_path_union": union,
        "allowed_phase128_paths": allowed_phase128_paths,
        "pre_existing_unrelated_paths": pre_existing_unrelated_paths,
        "forbidden_runtime_paths": forbidden_runtime_paths,
        "forbidden_v4_archive_paths": forbidden_v4_archive_paths,
        "commands_passed": commands_passed,
        "passed": (
            commands_passed
            and forbidden_runtime_paths == []
            and forbidden_v4_archive_paths == []
        ),
    }


def build_risk_contract_evidence(
    matrix_path: Path = DEFAULT_MATRIX,
    validator_path: Path = DEFAULT_VALIDATOR,
    diff_base: str | None = None,
    committed_changed_paths: Iterable[str] | None = None,
    changed_paths: Iterable[str] | None = None,
    staged_changed_paths: Iterable[str] | None = None,
    untracked_paths: Iterable[str] | None = None,
    archive_diff_result: subprocess.CompletedProcess[str] | None = None,
    committed_archive_diff_result: subprocess.CompletedProcess[str] | None = None,
    staged_archive_diff_result: subprocess.CompletedProcess[str] | None = None,
    committed_result: subprocess.CompletedProcess[str] | None = None,
    unstaged_result: subprocess.CompletedProcess[str] | None = None,
    staged_result: subprocess.CompletedProcess[str] | None = None,
    untracked_result: subprocess.CompletedProcess[str] | None = None,
) -> dict[str, Any]:
    resolved_diff_base = diff_base if diff_base is not None else discover_phase_diff_base()
    fixtures = [
        replay_fixture(fixture, validator_path) for fixture in load_matrix(matrix_path)
    ]
    archive_proof = collect_archive_proof(
        archive_diff_result,
        committed_archive_diff_result=committed_archive_diff_result,
        staged_archive_diff_result=staged_archive_diff_result,
        diff_base=resolved_diff_base,
    )
    runtime_scope_proof = collect_runtime_scope_proof(
        diff_base=resolved_diff_base,
        committed_changed_paths=committed_changed_paths,
        changed_paths=changed_paths,
        staged_changed_paths=staged_changed_paths,
        untracked_paths=untracked_paths,
        committed_result=committed_result,
        unstaged_result=unstaged_result,
        staged_result=staged_result,
        untracked_result=untracked_result,
    )

    passed_count = sum(1 for fixture in fixtures if fixture["passed"])
    failed_count = len(fixtures) - passed_count
    all_fixture_replays_passed = failed_count == 0
    close_ready = (
        all_fixture_replays_passed
        and archive_proof["passed"]
        and runtime_scope_proof["passed"]
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "requirements_addressed": REQUIREMENTS_ADDRESSED,
        "source_inputs": {
            "fixture_matrix": display_path(repo_path(matrix_path)),
            "validator": display_path(repo_path(validator_path)),
            "diff_base": resolved_diff_base,
        },
        "summary": {
            "fixture_count": len(fixtures),
            "passed_count": passed_count,
            "failed_count": failed_count,
            "all_fixture_replays_passed": all_fixture_replays_passed,
            "v4_archive_untouched": archive_proof["passed"],
            "runtime_scope_within_allowlist": runtime_scope_proof["passed"],
            "close_readiness": (
                "ready_for_milestone_completion" if close_ready else "blocked"
            ),
        },
        "fixtures": fixtures,
        "archive_proof": archive_proof,
        "runtime_scope_proof": runtime_scope_proof,
        "closure_notes": DEFERRED_SCOPE_NOTES,
    }


def passed_label(value: bool) -> str:
    return "PASS" if value else "FAIL"


def render_markdown(evidence: dict[str, Any]) -> str:
    summary = evidence["summary"]
    archive = evidence["archive_proof"]
    scope = evidence["runtime_scope_proof"]
    lines = [
        "# v5.4 Risk Contract Validation Evidence",
        "",
        f"Schema version: `{evidence['schema_version']}`",
        f"Phase: {evidence['phase']}",
        f"Requirements: {', '.join(evidence['requirements_addressed'])}",
        f"Fixture matrix: `{evidence['source_inputs']['fixture_matrix']}`",
        f"Validator: `{evidence['source_inputs']['validator']}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Fixture count | {summary['fixture_count']} |",
        f"| Passed fixtures | {summary['passed_count']} |",
        f"| Failed fixtures | {summary['failed_count']} |",
        f"| All fixture replays passed | {summary['all_fixture_replays_passed']} |",
        f"| v4 archive untouched | {summary['v4_archive_untouched']} |",
        f"| Runtime scope within allowlist | {summary['runtime_scope_within_allowlist']} |",
        f"| Close readiness | {summary['close_readiness']} |",
        "",
        "## Fixture Replay Summary",
        "",
        "| Fixture | Expected valid | Exit code | Actual error | Passed |",
        "| --- | --- | --- | --- | --- |",
    ]
    for fixture in evidence["fixtures"]:
        lines.append(
            f"| `{fixture['id']}` | {fixture['expected_valid']} | "
            f"{fixture['exit_code']} | `{fixture['actual_error']}` | "
            f"{passed_label(fixture['passed'])} |"
        )

    lines.extend(["", "## Fixture Details", ""])
    for fixture in evidence["fixtures"]:
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
                f"- Exit code matches: {fixture['exit_code_matches']}",
                f"- Validity matches: {fixture['validity_matches']}",
                f"- Error code matches: {fixture['error_code_matches']}",
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

    lines.extend(
        [
            "## v4 Archive Proof",
            "",
            f"- Command: `{archive['command']}`",
            f"- Exit code: {archive['exit_code']}",
            f"- Modified archive paths: {archive['modified_archive_paths']}",
            f"- Passed: {archive['passed']}",
            "",
            "## Runtime Scope Proof",
            "",
            "| Category | Paths |",
            "| --- | --- |",
            f"| Unstaged changed paths | {scope['unstaged_changed_paths']} |",
            f"| Staged changed paths | {scope['staged_changed_paths']} |",
            f"| Untracked paths | {scope['untracked_paths']} |",
            f"| Committed changed paths | {scope['committed_changed_paths']} |",
            f"| Allowed Phase 128 paths | {scope['allowed_phase128_paths']} |",
            f"| Pre-existing unrelated paths | {scope['pre_existing_unrelated_paths']} |",
            f"| Forbidden runtime paths | {scope['forbidden_runtime_paths']} |",
            f"| Forbidden v4 archive paths | {scope['forbidden_v4_archive_paths']} |",
            "",
            "### Runtime Scope Commands",
            "",
            f"- Committed: `{scope['committed_command']['command']}`",
            f"- Unstaged: `{scope['unstaged_command']['command']}`",
            f"- Staged: `{scope['staged_command']['command']}`",
            f"- Untracked: `{scope['untracked_command']['command']}`",
            "",
            "## Closure Notes",
            "",
        ]
    )
    for note in evidence["closure_notes"]:
        lines.append(f"- {note}")

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
        help="Git base commit for Phase 128 committed diff proof.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        evidence = build_risk_contract_evidence(
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
        print(f"generate_risk_contract_evidence.py: {exc}", file=sys.stderr)
        return 1

    return 0 if evidence["summary"]["close_readiness"] == "ready_for_milestone_completion" else 1


if __name__ == "__main__":
    raise SystemExit(main())
