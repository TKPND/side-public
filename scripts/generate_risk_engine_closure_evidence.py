#!/usr/bin/env python3
"""Generate deterministic v5.6 risk engine closure evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk import evaluate_risk, write_risk_artifact
from scripts import generate_risk_contract_drift_gate_evidence as drift_evidence
from scripts import validate_risk_contract as validator


SCHEMA_VERSION = "risk_engine_closure_evidence.v1"
RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
PHASE = 133
REQUIREMENTS_ADDRESSED = [
    "BTADAPT-01",
    "BTADAPT-02",
    "BTADAPT-03",
    "EVID-01",
    "EVID-02",
]
DEFAULT_REPORT_DIR = Path("reports/v5.6")
JSON_REPORT_NAME = "risk_engine_closure_evidence.json"
MD_REPORT_NAME = "risk_engine_closure_evidence.md"
PHASE133_DIR = ".planning/phases/133-backtest-adapter-proof-and-evidence-closure"
PRIOR_ACTIVE_REF = "risk/contracts/v1/fixtures/valid/base_valid.json"
VALIDATOR_PATH = Path("scripts/validate_risk_contract.py")
SCHEMA_PATH = Path("risk/contracts/v1/risk_contract_v1.schema.json")
POLICY_VERSION = "risk-policy.v1.phase133.closure-evidence"
V4_ARCHIVE_PREFIXES = (".planning/milestones/v4", "data/v4", "docs/reports/v4")
FORBIDDEN_RUNTIME_PREFIXES = (
    "backtest/",
    "paper/",
    "paper_trading/",
    "live/",
    "rust/side-cli/",
    "rust/side-engine/",
    "rust/side-mirror/",
    "side-cli/",
    "side-engine/",
    "side-mirror/",
)
FORBIDDEN_RUNTIME_PATHS = {"risk/engine.py"}
ALLOWED_PHASE133_PREFIXES = (PHASE133_DIR + "/", "reports/v5.6/")
ALLOWED_PHASE133_PATHS = {
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
    "scripts/generate_risk_engine_closure_evidence.py",
    "tests/test_risk_engine_backtest_adapter.py",
    "tests/test_generate_risk_engine_closure_evidence.py",
}
RUNTIME_SCOPE_COMMANDS = {
    "committed": ["git", "diff", "--name-only"],
    "unstaged": ["git", "diff", "--name-only"],
    "staged": ["git", "diff", "--cached", "--name-only"],
    "untracked": ["git", "ls-files", "--others", "--exclude-standard"],
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


def missing_path_command(command: list[str], message: str) -> dict[str, Any]:
    return {
        "command": command_string(command),
        "command_vector": command,
        "exit_code": 1,
        "stderr": message,
        "paths": [],
    }


def injected_path_command(command: list[str], paths: Iterable[str]) -> dict[str, Any]:
    return {
        "command": command_string(command),
        "command_vector": command,
        "exit_code": 0,
        "stderr": "",
        "paths": sorted(path for path in paths if path),
    }


def base_policy(rules: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": POLICY_VERSION,
        "owner": "side-v5.6-risk-engine",
        "effective_from": "2026-05-07",
        "required_fields": [
            "policy.version",
            "candidate.strategy_id",
            "candidate.requested_size",
            "evidence.refs",
            "trace.emitted_artifact_path",
        ],
        "fail_close_rules": [
            {
                "condition": "malformed policy rules",
                "decision_class": "block",
                "fail_close_reason": "malformed_policy",
            },
            {
                "condition": "candidate requested size invalid",
                "decision_class": "block",
                "fail_close_reason": "candidate_validation_failure",
            },
        ],
        "rules": rules,
    }


def base_candidate(
    scenario_id: str,
    requested_size: int | float = 10,
) -> dict[str, Any]:
    return {
        "strategy_id": f"phase133.{scenario_id}.candidate",
        "symbol_or_universe": "phase133-synthetic-universe",
        "timeframe": "candidate-defined",
        "validation_refs": [f"phase133.validation.{scenario_id}"],
        "requested_size": requested_size,
    }


def evidence_for(scenario_id: str) -> dict[str, Any]:
    return {"refs": [f"{PRIOR_ACTIVE_REF}#{scenario_id}"]}


def context_for(scenario_id: str, artifact_path: Path) -> dict[str, Any]:
    return {
        "phase": "133-backtest-adapter-proof-and-evidence-closure",
        "scenario": scenario_id,
        "emitted_artifact_path": str(artifact_path),
    }


def rule(
    rule_id: str,
    decision_class: str,
    *,
    path: str = "candidate.strategy_id",
    op: str = "eq",
    value: object | None = None,
    allowed_size: int | float | None = None,
    fail_close_reason: str = "insufficient_validation_power",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": rule_id,
        "decision_class": decision_class,
        "when": {
            "path": path,
            "op": op,
            "value": value if value is not None else f"phase133.{rule_id}.candidate",
        },
        "fail_close_reason": fail_close_reason,
    }
    if allowed_size is not None:
        payload["allowed_size"] = allowed_size
    return payload


def scenario_definitions() -> list[dict[str, Any]]:
    return [
        {
            "id": "scenario.size",
            "rules": [
                rule(
                    "scenario.size",
                    "size",
                    path="candidate.requested_size",
                    op="exists",
                    value=True,
                )
            ],
            "expected_decision_class": "size",
            "expected_allowed_size": 10,
            "expected_fail_close_reason": "insufficient_validation_power",
        },
        {
            "id": "scenario.cap",
            "rules": [
                rule(
                    "scenario.cap",
                    "cap",
                    path="candidate.requested_size",
                    op="exists",
                    value=True,
                    allowed_size=3,
                )
            ],
            "expected_decision_class": "cap",
            "expected_allowed_size": 3,
            "expected_fail_close_reason": "insufficient_validation_power",
        },
        {
            "id": "scenario.reject",
            "rules": [
                rule(
                    "scenario.reject",
                    "reject",
                    path="candidate.requested_size",
                    op="exists",
                    value=True,
                )
            ],
            "expected_decision_class": "reject",
            "expected_allowed_size": 0,
            "expected_fail_close_reason": "insufficient_validation_power",
        },
        {
            "id": "scenario.kill",
            "rules": [
                rule(
                    "scenario.kill",
                    "kill",
                    path="candidate.requested_size",
                    op="exists",
                    value=True,
                    fail_close_reason="stale_evidence",
                )
            ],
            "expected_decision_class": "kill",
            "expected_allowed_size": 0,
            "expected_fail_close_reason": "stale_evidence",
        },
        {
            "id": "scenario.block",
            "rules": [
                rule(
                    "scenario.block",
                    "block",
                    path="candidate.requested_size",
                    op="exists",
                    value=True,
                    fail_close_reason="malformed_policy",
                )
            ],
            "expected_decision_class": "block",
            "expected_allowed_size": 0,
            "expected_fail_close_reason": "malformed_policy",
        },
    ]


def build_scenarios() -> list[dict[str, Any]]:
    return scenario_definitions()


def validator_cli_payload(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(ROOT / VALIDATOR_PATH), str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise ValueError(result.stderr or "validator CLI failed")
    return json.loads(result.stdout)


def replay_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    scenario_id = scenario["id"]
    with tempfile.TemporaryDirectory(prefix="phase133-risk-engine-") as temp_dir:
        artifact_path = Path(temp_dir) / f"{scenario_id}.json"
        evaluation = evaluate_risk(
            base_policy(scenario["rules"]),
            base_candidate(scenario_id),
            evidence_for(scenario_id),
            context_for(scenario_id, artifact_path),
        )
        written = write_risk_artifact(evaluation, artifact_path)
        validator_errors = validator.validate_contract(evaluation.artifact)
        validator_payload = validator_cli_payload(written)

    decision = evaluation.decision
    trace = evaluation.trace
    decision_trace_aligned = (
        decision["decision_class"] == trace["decision_class"]
        and decision["binding_rule"] == trace["binding_rule"]
    )
    passed = (
        decision["decision_class"] == scenario["expected_decision_class"]
        and decision["allowed_size"] == scenario["expected_allowed_size"]
        and decision["fail_close_reason"] == scenario["expected_fail_close_reason"]
        and validator_errors == []
        and validator_payload["schema_version"] == RESULT_SCHEMA_VERSION
        and validator_payload["valid"] is True
        and validator_payload["errors"] == []
        and decision_trace_aligned
    )
    return {
        "id": scenario_id,
        "decision_class": decision["decision_class"],
        "expected_allowed_size": scenario["expected_allowed_size"],
        "actual_allowed_size": decision["allowed_size"],
        "expected_fail_close_reason": scenario["expected_fail_close_reason"],
        "actual_fail_close_reason": decision["fail_close_reason"],
        "scenario_ref": evidence_for(scenario_id)["refs"][0],
        "artifact_path": str(artifact_path),
        "artifact": evaluation.artifact,
        "validator_errors": validator_errors,
        "validator_payload": validator_payload,
        "decision_trace_aligned": decision_trace_aligned,
        "passed": passed,
    }


def decision_replay_check() -> dict[str, Any]:
    scenarios = [replay_scenario(scenario) for scenario in build_scenarios()]
    passed = all(scenario["passed"] for scenario in scenarios)
    return {
        "name": "decision_replay",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
    }


def adapter_proof_check() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="phase133-adapter-proof-") as temp_dir:
        artifact_path = Path(temp_dir) / "phase133-fake-backtest-risk-artifact.json"
        candidate = {
            "strategy_id": "phase133.fake-backtest-candidate",
            "symbol_or_universe": "phase133-fake-universe",
            "timeframe": "candidate-defined",
            "validation_refs": ["phase133.fake-backtest.validation"],
            "requested_size": 4,
        }
        evaluation = evaluate_risk(
            base_policy(
                [
                    rule(
                        "phase133.adapter.size",
                        "size",
                        path="candidate.requested_size",
                        op="exists",
                        value=True,
                    )
                ]
            ),
            candidate,
            evidence_for("scenario.adapter-proof"),
            {
                "phase": "133-backtest-adapter-proof-and-evidence-closure",
                "adapter": "pytest-only-fake-backtest-boundary",
                "emitted_artifact_path": str(artifact_path),
            },
        )
        written = write_risk_artifact(evaluation, artifact_path)
        validator_payload = validator_cli_payload(written)

    passed = (
        evaluation.decision["decision_class"] == "size"
        and evaluation.decision["allowed_size"] == 4
        and validator_payload["schema_version"] == RESULT_SCHEMA_VERSION
        and validator_payload["valid"] is True
        and validator_payload["errors"] == []
    )
    return {
        "name": "adapter_proof",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "artifact_path": str(written),
        "artifact": evaluation.artifact,
        "validator_payload": validator_payload,
    }


def validator_alignment_check(decision_replay: dict[str, Any]) -> dict[str, Any]:
    scenarios = [
        {
            "id": scenario["id"],
            "schema_version": scenario["validator_payload"].get("schema_version"),
            "valid": scenario["validator_payload"].get("valid"),
            "errors": scenario["validator_payload"].get("errors"),
            "passed": (
                scenario["validator_payload"].get("schema_version")
                == RESULT_SCHEMA_VERSION
                and scenario["validator_payload"].get("valid") is True
                and scenario["validator_payload"].get("errors") == []
            ),
        }
        for scenario in decision_replay["scenarios"]
    ]
    passed = all(scenario["passed"] for scenario in scenarios)
    return {
        "name": "validator_alignment",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "scenarios": scenarios,
    }


def is_v4_archive_path(path: str) -> bool:
    return path.startswith(V4_ARCHIVE_PREFIXES)


def is_forbidden_runtime_path(path: str) -> bool:
    return path in FORBIDDEN_RUNTIME_PATHS or path.startswith(FORBIDDEN_RUNTIME_PREFIXES)


def is_allowed_phase133_path(path: str) -> bool:
    return path in ALLOWED_PHASE133_PATHS or path.startswith(ALLOWED_PHASE133_PREFIXES)


def discover_phase133_diff_base() -> str | None:
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%H", "--", PHASE133_DIR],
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


def collect_scope_guard(
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
            "missing Phase 133 diff base",
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
    allowed_phase133_paths: list[str] = []
    forbidden_runtime_paths: list[str] = []
    forbidden_v4_archive_paths: list[str] = []
    unexpected_paths: list[str] = []

    for path in changed_path_union:
        if is_v4_archive_path(path):
            forbidden_v4_archive_paths.append(path)
        elif is_forbidden_runtime_path(path):
            forbidden_runtime_paths.append(path)
        elif is_allowed_phase133_path(path):
            allowed_phase133_paths.append(path)
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
        "name": "scope_guard",
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
        "allowed_phase133_paths": allowed_phase133_paths,
        "forbidden_runtime_paths": forbidden_runtime_paths,
        "forbidden_v4_archive_paths": forbidden_v4_archive_paths,
        "unexpected_paths": unexpected_paths,
        "commands_passed": commands_passed,
    }


def schema_drift_alignment_check() -> dict[str, Any]:
    schema = validator.load_contract_schema()
    schema_fact_snapshot = drift_evidence.schema_fact_snapshot_check(schema)
    synthetic_mutations = drift_evidence.synthetic_mutation_checks(schema)
    passed = schema_fact_snapshot["passed"] and synthetic_mutations["passed"]
    return {
        "name": "schema_drift_alignment",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "schema_fact_snapshot": schema_fact_snapshot,
        "synthetic_mutations": synthetic_mutations,
    }


def build_risk_engine_closure_evidence(
    diff_base: str | None = None,
    committed_changed_paths: Iterable[str] | None = None,
    changed_paths: Iterable[str] | None = None,
    staged_changed_paths: Iterable[str] | None = None,
    untracked_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    resolved_diff_base = diff_base if diff_base is not None else discover_phase133_diff_base()
    decision_replay = decision_replay_check()
    checks = {
        "adapter_proof": adapter_proof_check(),
        "decision_replay": decision_replay,
        "validator_alignment": validator_alignment_check(decision_replay),
        "schema_drift_alignment": schema_drift_alignment_check(),
        "scope_guard": collect_scope_guard(
            diff_base=resolved_diff_base,
            committed_changed_paths=committed_changed_paths,
            changed_paths=changed_paths,
            staged_changed_paths=staged_changed_paths,
            untracked_paths=untracked_paths,
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
            "validator": display_path(repo_path(VALIDATOR_PATH)),
            "prior_active_ref": PRIOR_ACTIVE_REF,
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
    if check["name"] == "adapter_proof":
        return f"Adapter artifact validator valid: {check['validator_payload']['valid']}."
    if check["name"] == "decision_replay":
        passed_count = sum(1 for scenario in check["scenarios"] if scenario["passed"])
        return f"{passed_count}/{check['scenario_count']} decision scenarios passed."
    if check["name"] == "validator_alignment":
        passed_count = sum(1 for scenario in check["scenarios"] if scenario["passed"])
        return f"{passed_count}/{len(check['scenarios'])} validator payloads valid."
    if check["name"] == "schema_drift_alignment":
        return "Schema facts and synthetic drift gates align with v5.5 expectations."
    if check["name"] == "scope_guard":
        blocked = (
            len(check["forbidden_runtime_paths"])
            + len(check["forbidden_v4_archive_paths"])
            + len(check["unexpected_paths"])
        )
        return f"{blocked} forbidden or unexpected Phase 133 path(s)."
    return ""


def render_markdown(evidence: dict[str, Any]) -> str:
    checks = evidence["checks"]
    lines = [
        "# v5.6 Risk Engine Closure Evidence",
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
        "adapter_proof",
        "decision_replay",
        "validator_alignment",
        "schema_drift_alignment",
        "scope_guard",
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
            "## Adapter Proof",
            "",
            f"- Status: {checks['adapter_proof']['status']}",
            f"- Artifact path: `{checks['adapter_proof']['artifact_path']}`",
            f"- Decision class: `{checks['adapter_proof']['artifact']['decision']['decision_class']}`",
            f"- Validator valid: {checks['adapter_proof']['validator_payload']['valid']}",
            "",
            "## Decision Replay",
            "",
            "| Scenario | Decision class | Allowed size | Fail-close reason | Validator valid | Trace aligned | Passed |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for scenario in checks["decision_replay"]["scenarios"]:
        lines.append(
            f"| `{scenario['id']}` | {scenario['decision_class']} | "
            f"{scenario['actual_allowed_size']} | "
            f"{scenario['actual_fail_close_reason']} | "
            f"{scenario['validator_payload']['valid']} | "
            f"{scenario['decision_trace_aligned']} | {scenario['passed']} |"
        )

    schema = checks["schema_drift_alignment"]
    scope = checks["scope_guard"]
    lines.extend(
        [
            "",
            "## Validator Alignment",
            "",
            "| Scenario | Schema version | Valid | Errors | Passed |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for scenario in checks["validator_alignment"]["scenarios"]:
        lines.append(
            f"| `{scenario['id']}` | `{scenario['schema_version']}` | "
            f"{scenario['valid']} | {scenario['errors']} | {scenario['passed']} |"
        )

    lines.extend(
        [
            "",
            "## Schema Drift Alignment",
            "",
            f"- Schema fact snapshot: {schema['schema_fact_snapshot']['status']}",
            f"- Synthetic mutations: {schema['synthetic_mutations']['status']}",
            "",
            "## Scope Guard",
            "",
            "| Category | Paths |",
            "| --- | --- |",
            f"| Allowed Phase 133 paths | {scope['allowed_phase133_paths']} |",
            f"| Forbidden runtime paths | {scope['forbidden_runtime_paths']} |",
            f"| Forbidden v4 archive paths | {scope['forbidden_v4_archive_paths']} |",
            f"| Unexpected paths | {scope['unexpected_paths']} |",
            "",
            "### Scope Commands",
            "",
            f"- Committed: `{scope['committed_command']['command']}`",
            f"- Unstaged: `{scope['unstaged_command']['command']}`",
            f"- Staged: `{scope['staged_command']['command']}`",
            f"- Untracked: `{scope['untracked_command']['command']}`",
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
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--diff-base",
        default=None,
        help="Git base commit for Phase 133 committed diff proof.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        evidence = build_risk_engine_closure_evidence(diff_base=args.diff_base)
        report_dir = repo_path(args.report_dir)
        write_json(evidence, report_dir / JSON_REPORT_NAME)
        (report_dir / MD_REPORT_NAME).write_text(
            render_markdown(evidence),
            encoding="utf-8",
        )
    except SystemExit as exc:
        return int(exc.code)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"generate_risk_engine_closure_evidence.py: {exc}", file=sys.stderr)
        return 1

    return (
        0
        if evidence["summary"]["close_readiness"] == "ready_for_milestone_completion"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
