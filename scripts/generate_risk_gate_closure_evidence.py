#!/usr/bin/env python3
"""Generate deterministic v5.7 risk gate closure evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import generate_risk_contract_drift_gate_evidence as drift_evidence
from scripts import validate_risk_contract as validator


SCHEMA_VERSION = "risk_gate_closure_evidence.v1"
RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
PHASE = 137
REQUIREMENTS_ADDRESSED = ["CLOSE-01", "CLOSE-02", "CLOSE-03", "CLOSE-04"]
DEFAULT_REPORT_DIR = Path("reports/v5.7")
JSON_REPORT_NAME = "risk_gate_closure_evidence.json"
MD_REPORT_NAME = "risk_gate_closure_evidence.md"
PHASE137_DIR = ".planning/phases/137-closure-evidence"
VALIDATOR_PATH = Path("scripts/validate_risk_contract.py")
SCHEMA_PATH = Path("risk/contracts/v1/risk_contract_v1.schema.json")
V56_CLOSURE_REPORT = Path("reports/v5.6/risk_engine_closure_evidence.json")
POLICY_VERSION = "risk-policy.v1.phase137.closure-evidence"
DECISION_CLASSES = ("size", "cap", "reject", "kill", "block")
STOPPED_DECISIONS = ("block", "kill", "reject")
CONTINUED_DECISIONS = ("size", "cap")
FIXTURE_PARQUET = Path("rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet")
EDGES_FIXTURE = Path("rust/side-engine/tests/fixtures/edges_sample.json")
V4_ARCHIVE_PREFIXES = (".planning/milestones/v4", "data/v4", "docs/reports/v4")
FORBIDDEN_RUNTIME_PREFIXES = (
    "rust/side-cli/",
    "rust/side-engine/",
    "rust/side-mirror/",
    "backtest/",
    "paper/",
    "paper_trading/",
    "live/",
    "side-cli/",
    "side-engine/",
    "side-mirror/",
)
FORBIDDEN_RUNTIME_PATHS = {"risk/engine.py"}
ALLOWED_PHASE137_PREFIXES = (PHASE137_DIR + "/", "reports/v5.7/")
ALLOWED_PHASE137_PATHS = {
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
    ".planning/v5.7-MILESTONE-AUDIT.md",
    "scripts/generate_risk_gate_closure_evidence.py",
    "tests/test_generate_risk_gate_closure_evidence.py",
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


def write_policy(path: Path, decision_class: str) -> None:
    fail_close_reason = {
        "block": "malformed_policy",
        "kill": "stale_evidence",
    }.get(decision_class, "insufficient_validation_power")
    rule: dict[str, Any] = {
        "id": f"phase137.{decision_class}",
        "decision_class": decision_class,
        "when": {
            "path": "candidate.requested_size",
            "op": "exists",
            "value": True,
        },
        "fail_close_reason": fail_close_reason,
    }
    if decision_class == "cap":
        rule["allowed_size"] = 0.25

    policy = {
        "version": POLICY_VERSION,
        "owner": "side-v5.7-risk-gate",
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
        "rules": [rule],
    }
    target = repo_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def scan_command(
    decision_class: str,
    report_dir: Path,
    policy_path: Path,
    artifact_root: Path,
    output_path: Path,
) -> list[str]:
    _ = decision_class
    _ = report_dir
    return [
        "cargo",
        "run",
        "-p",
        "side-cli",
        "--",
        "scan",
        "--asset",
        "USDJPY",
        "--timeframe",
        "1h",
        "--fixture-parquet",
        FIXTURE_PARQUET.as_posix(),
        "--edges",
        EDGES_FIXTURE.as_posix(),
        "--spread-bps-rt",
        "1.5",
        "--commission-bps-rt",
        "0.5",
        "--risk-gate-policy",
        str(policy_path),
        "--risk-gate-artifact-root",
        str(artifact_root),
        "--output",
        str(output_path),
    ]


def ungated_scan_command(output_path: Path) -> list[str]:
    return [
        "cargo",
        "run",
        "-p",
        "side-cli",
        "--",
        "scan",
        "--asset",
        "USDJPY",
        "--timeframe",
        "1h",
        "--fixture-parquet",
        FIXTURE_PARQUET.as_posix(),
        "--edges",
        EDGES_FIXTURE.as_posix(),
        "--spread-bps-rt",
        "1.5",
        "--commission-bps-rt",
        "0.5",
        "--output",
        str(output_path),
    ]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def evidence_path(path: str | Path | None) -> str | None:
    if path is None:
        return None
    return display_path(repo_path(Path(path)))


def selected_slot(
    slots: Any,
    decision_class: str,
) -> dict[str, Any] | None:
    if not isinstance(slots, list):
        return None
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        risk_gate = slot.get("risk_gate")
        if isinstance(risk_gate, dict) and risk_gate.get("decision_class") == decision_class:
            return slot
    return None


def slot_artifact_rows(slots: Any, decision_class: str) -> list[dict[str, Any]]:
    if not isinstance(slots, list):
        return []
    rows: list[dict[str, Any]] = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        risk_gate = slot.get("risk_gate")
        if not isinstance(risk_gate, dict):
            continue
        if risk_gate.get("decision_class") != decision_class:
            continue
        observed_artifact_path = risk_gate.get("artifact_path")
        rows.append(
            {
                "decision_class": decision_class,
                "slot_identity": {
                    "source_edge_index": slot.get("source_edge_index"),
                    "entry_minute": slot.get("entry_minute"),
                    "direction": slot.get("direction"),
                    "hold_h": slot.get("hold_h"),
                },
                "artifact_path": evidence_path(observed_artifact_path),
                "observed_artifact_path": observed_artifact_path,
            }
        )
    return rows


def run_scan_replay(decision_class: str, report_dir: Path) -> dict[str, Any]:
    policy_path = report_dir / "risk_gate_policies" / f"{decision_class}.policy.json"
    artifact_root = report_dir / "risk_gate" / decision_class
    output_path = report_dir / "risk_gate_scan_outputs" / f"{decision_class}.scan.json"
    repo_path(policy_path).parent.mkdir(parents=True, exist_ok=True)
    repo_path(artifact_root).mkdir(parents=True, exist_ok=True)
    repo_path(output_path).parent.mkdir(parents=True, exist_ok=True)
    write_policy(policy_path, decision_class)

    command = scan_command(decision_class, report_dir, policy_path, artifact_root, output_path)
    env = os.environ.copy()
    env["SIDE_RISK_GATE_NO_WFD_SENTINEL"] = "panic"
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    slots: Any = []
    parse_error = ""
    if result.returncode == 0:
        try:
            slots = load_json(repo_path(output_path))
        except (OSError, json.JSONDecodeError) as exc:
            parse_error = str(exc)

    slot = selected_slot(slots, decision_class)
    emitted_artifacts = slot_artifact_rows(slots, decision_class)
    risk_gate = slot.get("risk_gate", {}) if isinstance(slot, dict) else {}
    observed_artifact_path = (
        risk_gate.get("artifact_path") if isinstance(risk_gate, dict) else None
    )
    artifact_path = evidence_path(observed_artifact_path)
    artifact_exists = bool(
        artifact_path and repo_path(Path(artifact_path)).exists()
    )
    emitted_artifacts_exist = all(
        row["artifact_path"] is not None
        and repo_path(Path(row["artifact_path"])).exists()
        for row in emitted_artifacts
    )
    validator_payload = (
        validator_cli_payload(Path(artifact_path))
        if artifact_exists and artifact_path is not None
        else {
            "schema_version": RESULT_SCHEMA_VERSION,
            "checked_path": artifact_path or "",
            "valid": False,
            "errors": [{"code": "missing_artifact", "path": "", "message": "artifact missing"}],
        }
    )
    passed = (
        result.returncode == 0
        and parse_error == ""
        and isinstance(slot, dict)
        and isinstance(risk_gate, dict)
        and risk_gate.get("decision_class") == decision_class
        and artifact_exists
        and len(emitted_artifacts) == (len(slots) if isinstance(slots, list) else 0)
        and emitted_artifacts_exist
        and validator_payload.get("schema_version") == RESULT_SCHEMA_VERSION
        and validator_payload.get("valid") is True
    )
    return {
        "decision_class": decision_class,
        "replay_command_vector": command,
        "replay_command": command_string(command),
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "scan_output_path": display_path(output_path),
        "policy_path": display_path(policy_path),
        "artifact_root": display_path(artifact_root),
        "emitted_slot_count": len(slots) if isinstance(slots, list) else 0,
        "emitted_artifacts": emitted_artifacts,
        "selected_slot": slot,
        "risk_gate": risk_gate,
        "artifact_path": artifact_path,
        "observed_artifact_path": observed_artifact_path,
        "artifact_exists": artifact_exists,
        "validator_payload": validator_payload,
        "parse_error": parse_error,
        "passed": passed,
    }


def run_ungated_scan(report_dir: Path) -> dict[str, Any]:
    output_path = report_dir / "risk_gate_scan_outputs" / "ungated.scan.json"
    repo_path(output_path).parent.mkdir(parents=True, exist_ok=True)
    command = ungated_scan_command(output_path)
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    slots: Any = []
    parse_error = ""
    if result.returncode == 0:
        try:
            slots = load_json(repo_path(output_path))
        except (OSError, json.JSONDecodeError) as exc:
            parse_error = str(exc)
    return {
        "replay_command_vector": command,
        "replay_command": command_string(command),
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "scan_output_path": display_path(output_path),
        "slot_count": len(slots) if isinstance(slots, list) else 0,
        "slots": slots,
        "parse_error": parse_error,
        "passed": result.returncode == 0 and parse_error == "" and isinstance(slots, list),
    }


def decision_replay_check(report_dir: Path) -> dict[str, Any]:
    decisions = [run_scan_replay(decision_class, report_dir) for decision_class in DECISION_CLASSES]
    passed = all(row["passed"] for row in decisions)
    return {
        "name": "decision_replay",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "decision_count": len(decisions),
        "decisions": decisions,
    }


def slot_value(slot: dict[str, Any], key: str) -> Any:
    return slot.get(key)


def normal_verdict_fields_absent_or_null(slot: dict[str, Any]) -> bool:
    return all(slot.get(key) is None for key in ("verdict", "relaxed_pass", "verdicts_per_fee"))


def stopped_execution_check(decision_replay: dict[str, Any]) -> dict[str, Any]:
    replay_by_decision = {
        row["decision_class"]: row for row in decision_replay.get("decisions", [])
    }
    rows: list[dict[str, Any]] = []
    for decision_class in STOPPED_DECISIONS:
        replay = replay_by_decision.get(decision_class, {})
        slot = replay.get("selected_slot") if isinstance(replay, dict) else None
        risk_gate = replay.get("risk_gate") if isinstance(replay, dict) else None
        slot = slot if isinstance(slot, dict) else {}
        risk_gate = risk_gate if isinstance(risk_gate, dict) else {}
        row = {
            "decision_class": decision_class,
            "execution_state": risk_gate.get("execution_state"),
            "sentinel_tripped": "SIDE_RISK_GATE_NO_WFD_SENTINEL" in replay.get("stderr", ""),
            "fee_curve": slot_value(slot, "fee_curve"),
            "pf_gross": slot_value(slot, "pf_gross"),
            "pf_net_2bps_rt": slot_value(slot, "pf_net@2bps_rt"),
            "alpha_cliff": slot_value(slot, "alpha_cliff"),
            "normal_verdict_fields_absent_or_null": normal_verdict_fields_absent_or_null(slot),
        }
        row["passed"] = (
            row["execution_state"] == "stopped"
            and row["sentinel_tripped"] is False
            and row["fee_curve"] == []
            and row["pf_gross"] is None
            and row["pf_net_2bps_rt"] is None
            and row["alpha_cliff"] is None
            and row["normal_verdict_fields_absent_or_null"] is True
        )
        rows.append(row)
    passed = all(row["passed"] for row in rows)
    return {
        "name": "stopped_execution",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "decisions": rows,
    }


def slot_identity(slot: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (
        slot.get("source_edge_index"),
        slot.get("entry_minute"),
        slot.get("direction"),
        slot.get("hold_h"),
    )


def metric_values(slot: dict[str, Any]) -> dict[str, Any]:
    return {
        "fee_curve": slot.get("fee_curve"),
        "pf_gross": slot.get("pf_gross"),
        "pf_net_2bps_rt": slot.get("pf_net@2bps_rt"),
        "alpha_cliff": slot.get("alpha_cliff"),
    }


def continued_runtime_check(
    decision_replay: dict[str, Any],
    ungated: dict[str, Any],
) -> dict[str, Any]:
    replay_by_decision = {
        row["decision_class"]: row for row in decision_replay.get("decisions", [])
    }
    ungated_slots = ungated.get("slots") if isinstance(ungated.get("slots"), list) else []
    ungated_by_identity = {
        slot_identity(slot): slot for slot in ungated_slots if isinstance(slot, dict)
    }
    rows: list[dict[str, Any]] = []
    for decision_class in CONTINUED_DECISIONS:
        replay = replay_by_decision.get(decision_class, {})
        slot = replay.get("selected_slot") if isinstance(replay, dict) else None
        risk_gate = replay.get("risk_gate") if isinstance(replay, dict) else None
        slot = slot if isinstance(slot, dict) else {}
        risk_gate = risk_gate if isinstance(risk_gate, dict) else {}
        row = {
            "decision_class": decision_class,
            "execution_state": risk_gate.get("execution_state"),
            "application_status": risk_gate.get("application_status"),
            "application_status_present": "application_status" in risk_gate,
        }
        if decision_class == "cap":
            ungated_slot = ungated_by_identity.get(slot_identity(slot), {})
            row["metrics_match_ungated"] = metric_values(slot) == metric_values(ungated_slot)
            row["ungated_slot_identity"] = list(slot_identity(ungated_slot)) if ungated_slot else None
            row["gated_metrics"] = metric_values(slot)
            row["ungated_metrics"] = metric_values(ungated_slot)
            row["passed"] = (
                row["execution_state"] == "continued"
                and row["application_status"] == "deferred"
                and row["metrics_match_ungated"] is True
            )
        else:
            row["passed"] = (
                row["execution_state"] == "continued"
                and row["application_status_present"] is False
            )
        rows.append(row)
    passed = ungated.get("passed") is True and all(row["passed"] for row in rows)
    return {
        "name": "continued_runtime",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "ungated_scan": {
            key: value for key, value in ungated.items() if key != "slots"
        },
        "decisions": rows,
    }


def artifact_validation_check(decision_replay: dict[str, Any]) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for replay in decision_replay.get("decisions", []):
        for artifact in replay.get("emitted_artifacts", []):
            artifact_path = artifact.get("artifact_path")
            payload = (
                validator_cli_payload(Path(artifact_path))
                if isinstance(artifact_path, str)
                and repo_path(Path(artifact_path)).exists()
                else {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "checked_path": artifact_path or "",
                    "valid": False,
                    "errors": [
                        {
                            "code": "missing_artifact",
                            "path": "",
                            "message": "artifact missing",
                        }
                    ],
                }
            )
            row = {
                "decision_class": artifact.get("decision_class"),
                "slot_identity": artifact.get("slot_identity"),
                "artifact_path": artifact_path,
                "observed_artifact_path": artifact.get("observed_artifact_path"),
                "validator_payload": payload,
            }
            row["passed"] = (
                payload.get("schema_version") == RESULT_SCHEMA_VERSION
                and payload.get("valid") is True
                and payload.get("errors") == []
            )
            artifacts.append(row)
    expected_count = sum(
        row.get("emitted_slot_count", 0)
        for row in decision_replay.get("decisions", [])
        if isinstance(row.get("emitted_slot_count", 0), int)
    )
    passed = (
        all(row["passed"] for row in artifacts)
        and len(artifacts) == expected_count
        and expected_count > 0
    )
    return {
        "name": "artifact_validation",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "artifact_count": len(artifacts),
        "expected_artifact_count": expected_count,
        "artifacts": artifacts,
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


def v56_alignment_check() -> dict[str, Any]:
    report = load_json(repo_path(V56_CLOSURE_REPORT))
    required_checks = {
        "adapter_proof",
        "decision_replay",
        "validator_alignment",
        "schema_drift_alignment",
        "scope_guard",
    }
    checks_present = sorted(report.get("checks", {}).keys())
    passed = (
        report.get("schema_version") == "risk_engine_closure_evidence.v1"
        and required_checks.issubset(set(checks_present))
        and report.get("summary", {}).get("overall_status") == "PASS"
        and report.get("summary", {}).get("close_readiness")
        == "ready_for_milestone_completion"
    )
    return {
        "name": "v56_alignment",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "schema_version": report.get("schema_version"),
        "checks_present": checks_present,
        "required_checks": sorted(required_checks),
        "overall_status": report.get("summary", {}).get("overall_status"),
        "close_readiness": report.get("summary", {}).get("close_readiness"),
        "source_report": display_path(repo_path(V56_CLOSURE_REPORT)),
    }


def discover_phase137_diff_base() -> str | None:
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%H", "--", PHASE137_DIR],
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


def is_allowed_phase137_path(path: str) -> bool:
    return path in ALLOWED_PHASE137_PATHS or path.startswith(ALLOWED_PHASE137_PREFIXES)


def is_forbidden_runtime_path(path: str) -> bool:
    return path in FORBIDDEN_RUNTIME_PATHS or path.startswith(FORBIDDEN_RUNTIME_PREFIXES)


def is_v4_archive_path(path: str) -> bool:
    return path.startswith(V4_ARCHIVE_PREFIXES)


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
            "missing Phase 137 diff base",
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
    allowed_phase137_paths: list[str] = []
    forbidden_runtime_paths: list[str] = []
    forbidden_v4_archive_paths: list[str] = []
    unexpected_paths: list[str] = []

    for path in changed_path_union:
        if is_v4_archive_path(path):
            forbidden_v4_archive_paths.append(path)
        elif is_forbidden_runtime_path(path):
            forbidden_runtime_paths.append(path)
        elif is_allowed_phase137_path(path):
            allowed_phase137_paths.append(path)
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
        "allowed_phase137_paths": allowed_phase137_paths,
        "forbidden_runtime_paths": forbidden_runtime_paths,
        "forbidden_v4_archive_paths": forbidden_v4_archive_paths,
        "unexpected_paths": unexpected_paths,
        "commands_passed": commands_passed,
    }


def build_risk_gate_closure_evidence(
    report_dir: Path = DEFAULT_REPORT_DIR,
    diff_base: str | None = None,
    committed_changed_paths: Iterable[str] | None = None,
    changed_paths: Iterable[str] | None = None,
    staged_changed_paths: Iterable[str] | None = None,
    untracked_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    resolved_diff_base = diff_base if diff_base is not None else discover_phase137_diff_base()
    decision_replay = decision_replay_check(report_dir)
    ungated = run_ungated_scan(report_dir)
    checks = {
        "decision_replay": decision_replay,
        "stopped_execution": stopped_execution_check(decision_replay),
        "continued_runtime": continued_runtime_check(decision_replay, ungated),
        "artifact_validation": artifact_validation_check(decision_replay),
        "schema_drift_alignment": schema_drift_alignment_check(),
        "v56_alignment": v56_alignment_check(),
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
            "v56_closure_report": display_path(repo_path(V56_CLOSURE_REPORT)),
            "diff_base": resolved_diff_base,
            "report_dir": display_path(repo_path(report_dir)),
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
    name = check["name"]
    if name == "decision_replay":
        passed_count = sum(1 for row in check["decisions"] if row["passed"])
        return f"{passed_count}/{check['decision_count']} real scan decision replays passed."
    if name == "stopped_execution":
        passed_count = sum(1 for row in check["decisions"] if row["passed"])
        return f"{passed_count}/{len(STOPPED_DECISIONS)} stopped decisions prove no WFD metrics."
    if name == "continued_runtime":
        passed_count = sum(1 for row in check["decisions"] if row["passed"])
        return f"{passed_count}/{len(CONTINUED_DECISIONS)} continued decisions preserved expected runtime behavior."
    if name == "artifact_validation":
        passed_count = sum(1 for row in check["artifacts"] if row["passed"])
        return f"{passed_count}/{len(check['artifacts'])} persisted artifacts validated."
    if name == "schema_drift_alignment":
        return "v5.5 schema facts and synthetic mutations remain aligned."
    if name == "v56_alignment":
        return f"v5.6 closure report status: {check['overall_status']}."
    if name == "scope_guard":
        blocked = (
            len(check["forbidden_runtime_paths"])
            + len(check["forbidden_v4_archive_paths"])
            + len(check["unexpected_paths"])
        )
        return f"{blocked} forbidden or unexpected Phase 137 path(s)."
    return ""


def render_markdown(evidence: dict[str, Any]) -> str:
    checks = evidence["checks"]
    lines = [
        "# v5.7 Risk Gate Closure Evidence",
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
        "decision_replay",
        "stopped_execution",
        "continued_runtime",
        "artifact_validation",
        "schema_drift_alignment",
        "v56_alignment",
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
            "## Decision Replay",
            "",
            "| Decision | Execution state | Application status | Artifact | Validator valid | Passed |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in checks["decision_replay"]["decisions"]:
        risk_gate = row["risk_gate"]
        lines.append(
            f"| `{row['decision_class']}` | {risk_gate.get('execution_state')} | "
            f"{risk_gate.get('application_status', '')} | `{row['artifact_path']}` | "
            f"{row['validator_payload'].get('valid')} | {row['passed']} |"
        )

    lines.extend(
        [
            "",
            "## Stopped Execution Proof",
            "",
            "| Decision | Sentinel tripped | Fee curve | PF gross | PF net 2bps RT | Alpha cliff | Normal verdict fields absent/null | Passed |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in checks["stopped_execution"]["decisions"]:
        lines.append(
            f"| `{row['decision_class']}` | {row['sentinel_tripped']} | "
            f"{row['fee_curve']} | {row['pf_gross']} | {row['pf_net_2bps_rt']} | "
            f"{row['alpha_cliff']} | {row['normal_verdict_fields_absent_or_null']} | "
            f"{row['passed']} |"
        )

    lines.extend(
        [
            "",
            "## Continued Runtime Proof",
            "",
            "| Decision | Execution state | Application status | Application status present | Cap metrics match ungated | Passed |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in checks["continued_runtime"]["decisions"]:
        lines.append(
            f"| `{row['decision_class']}` | {row['execution_state']} | "
            f"{row.get('application_status', '')} | {row['application_status_present']} | "
            f"{row.get('metrics_match_ungated', '')} | {row['passed']} |"
        )

    lines.extend(
        [
            "",
            "## Artifact Validation",
            "",
            "| Decision | Artifact path | Schema version | Valid | Errors | Passed |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in checks["artifact_validation"]["artifacts"]:
        payload = row["validator_payload"]
        lines.append(
            f"| `{row['decision_class']}` | `{row['artifact_path']}` | "
            f"`{payload.get('schema_version')}` | {payload.get('valid')} | "
            f"{payload.get('errors')} | {row['passed']} |"
        )

    schema = checks["schema_drift_alignment"]
    v56 = checks["v56_alignment"]
    scope = checks["scope_guard"]
    lines.extend(
        [
            "",
            "## Schema Drift Alignment",
            "",
            f"- Schema fact snapshot: {schema['schema_fact_snapshot']['status']}",
            f"- Synthetic mutations: {schema['synthetic_mutations']['status']}",
            "",
            "## v5.6 Alignment",
            "",
            f"- Source report: `{v56['source_report']}`",
            f"- Schema version: `{v56['schema_version']}`",
            f"- Overall status: {v56['overall_status']}",
            f"- Close readiness: {v56['close_readiness']}",
            f"- Checks present: {v56['checks_present']}",
            "",
            "## Scope Guard",
            "",
            "| Category | Paths |",
            "| --- | --- |",
            f"| Allowed Phase 137 paths | {scope['allowed_phase137_paths']} |",
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
            "",
            "## Replay Commands",
            "",
        ]
    )
    for row in checks["decision_replay"]["decisions"]:
        lines.append(f"- `{row['decision_class']}`: `{row['replay_command']}`")

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
        help="Git base commit for Phase 137 committed diff proof.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        evidence = build_risk_gate_closure_evidence(
            report_dir=args.report_dir,
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
        print(f"generate_risk_gate_closure_evidence.py: {exc}", file=sys.stderr)
        return 1

    return (
        0
        if evidence["summary"]["close_readiness"] == "ready_for_milestone_completion"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
