#!/usr/bin/env python3
"""Generate deterministic v5.11 backtest risk gate runtime-sizing evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SCHEMA_VERSION = "backtest_risk_gate_closure_evidence.v1"
RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
PHASE = 148
REQUIREMENTS_ADDRESSED = [
    "CAPEVID-01",
    "CAPEVID-02",
    "CAPEVID-03",
]
DEFAULT_REPORT_DIR = Path("reports/v5.11")
JSON_REPORT_NAME = "backtest_risk_gate_closure_evidence.json"
MD_REPORT_NAME = "backtest_risk_gate_closure_evidence.md"
RUN_NAMES = ("ungated", "size", "cap", "reject", "kill", "block")
GATED_RUN_NAMES = ("size", "cap", "reject", "kill", "block")
STOPPED_RUN_NAMES = ("block", "kill", "reject")
CONTINUED_RUN_NAMES = ("size", "cap")
PARITY_METRIC_KEYS = ("num_trades", "profit_factor", "total_return")
VALIDATOR_PATH = Path("scripts/validate_risk_contract.py")
SCHEMA_PATH = Path("risk/contracts/v1/risk_contract_v1.schema.json")
FIXTURE_PARQUET = Path("rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet")
POLICY_VERSION = "risk-policy.v1.phase148.backtest-runtime-sizing-evidence"

V57_EXPECTED_SHA256 = {
    "reports/v5.7/risk_gate_closure_evidence.json": "a4d6b0f526e04db7001b95fd37495e1c63600fe6ea03a4471602c6c1f820cb0d",
    "reports/v5.7/risk_gate_closure_evidence.md": "365598b429211036c5db91a78bfedf9fe49e55001375852c81ae895d8c786084",
}

RUNTIME_SCOPE_COMMANDS = {
    "committed": ["git", "diff", "--name-only"],
    "unstaged": ["git", "diff", "--name-only"],
    "staged": ["git", "diff", "--cached", "--name-only"],
    "untracked": ["git", "ls-files", "--others", "--exclude-standard"],
}
PHASE140_DIR = ".planning/phases/148-replay-and-evidence-hardening"
ALLOWED_PHASE148_PATHS = {
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
    "reports/v5.11/backtest_risk_gate_closure_evidence.json",
    "reports/v5.11/backtest_risk_gate_closure_evidence.md",
    "scripts/generate_backtest_risk_gate_closure_evidence.py",
    "tests/test_generate_backtest_risk_gate_closure_evidence.py",
}
ALLOWED_PHASE148_PREFIXES = (
    PHASE140_DIR + "/",
    "reports/v5.11/backtest_risk_gate_closure/",
)
FORBIDDEN_RUNTIME_PREFIXES = (
    "rust/side-engine/",
    "paper/",
    "paper_trading/",
    "live/",
    "backtest/",
    "legacy/optuna/",
    "optuna/",
)
FORBIDDEN_RUNTIME_PATHS = {"risk/engine.py"}
FORBIDDEN_V4_PREFIXES = (
    "docs/reports/v4",
    "data/v4",
    ".planning/milestones/v4",
)


def repo_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def command_string(command: list[str]) -> str:
    return " ".join(command)


def markdown_command_arg(arg: str) -> str:
    path = Path(arg)
    if not path.is_absolute():
        return arg
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return arg


def markdown_command_string(command: list[str]) -> str:
    return " ".join(markdown_command_arg(arg) for arg in command)


def format_scope_guard_path_summary(paths: list[str], preview_limit: int = 3) -> str:
    count = len(paths)
    noun = "path" if count == 1 else "paths"
    if count == 0:
        return "0 paths"
    preview = paths[:preview_limit]
    rendered_preview = ", ".join(f"`{path}`" for path in preview)
    if count > preview_limit:
        return f"{count} {noun} (first {preview_limit}): {rendered_preview}"
    return f"{count} {noun}: {rendered_preview}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(repo_path(path).read_bytes())


def load_json(path: Path) -> Any:
    return json.loads(repo_path(path).read_text(encoding="utf-8"))


def write_json(data: Any, path: Path) -> None:
    target = repo_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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


def injected_path_command(command: list[str], paths: Iterable[str] | None) -> dict[str, Any]:
    return {
        "command": command_string(command),
        "command_vector": command,
        "exit_code": 0,
        "stderr": "",
        "paths": sorted(path for path in (paths or []) if path),
    }


def write_policy(path: Path, decision_class: str) -> None:
    fail_close_reason = {
        "block": "malformed_policy",
        "kill": "stale_evidence",
    }.get(decision_class, "insufficient_validation_power")
    rule: dict[str, Any] = {
        "id": f"phase148.{decision_class}",
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
        "owner": "side-v5.11-runtime-cap-sizing",
        "effective_from": "2026-05-13",
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
    write_json(policy, path)


def validator_cli_payload(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(ROOT / VALIDATOR_PATH), str(repo_path(path))],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "checked_path": display_path(repo_path(path)),
            "valid": False,
            "errors": [
                {
                    "code": "validator_infra_error",
                    "path": "",
                    "message": result.stderr,
                }
            ],
        }
    return json.loads(result.stdout)


def backtest_command(
    run_name: str,
    policy_path: Path | None = None,
    artifact_root: Path | None = None,
) -> list[str]:
    _ = run_name
    command = [
        "cargo",
        "run",
        "-p",
        "side-cli",
        "--",
        "backtest",
        "--asset",
        "USDJPY",
        "--timeframe",
        "1h",
        "--data",
        FIXTURE_PARQUET.as_posix(),
        "--strategy",
        "tod_edge",
        "--params",
        '{"entry_minute":0,"direction":"long","hold_h":3}',
        "--fee-bps",
        "1.0",
    ]
    if policy_path is not None and artifact_root is not None:
        command.extend(
            [
                "--risk-gate-policy",
                str(policy_path),
                "--risk-gate-artifact-root",
                artifact_root.as_posix(),
            ]
        )
    return command


def artifact_root_for(report_dir: Path, run_name: str) -> Path:
    resolved_report_dir = repo_path(report_dir)
    try:
        relative_report_dir = resolved_report_dir.relative_to(ROOT)
    except ValueError:
        digest = hashlib.sha256(str(resolved_report_dir).encode("utf-8")).hexdigest()[:12]
        return Path("target") / "phase148-backtest-risk-gate" / digest / run_name / "risk_artifacts"
    return relative_report_dir / "backtest_risk_gate_closure" / run_name / "risk_artifacts"


def parse_stdout_json(stdout: str) -> tuple[dict[str, Any] | None, str]:
    if not stdout.strip():
        return None, "stdout is empty"
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "stdout JSON must be an object"
    return value, ""


def missing_validator_payload(path: str | None) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "checked_path": path or "",
        "valid": False,
        "errors": [
            {
                "code": "missing_artifact",
                "path": "",
                "message": "artifact missing",
            }
        ],
    }


def semantic_assertions_for(
    run_name: str,
    return_code: int,
    stdout: dict[str, Any] | None,
    validator_payload: dict[str, Any] | None,
) -> dict[str, bool]:
    stdout = stdout or {}
    metrics = stdout.get("metrics")
    risk_gate = stdout.get("risk_gate") if isinstance(stdout.get("risk_gate"), dict) else {}
    cap_parity = (
        stdout.get("cap_parity") if isinstance(stdout.get("cap_parity"), dict) else {}
    )
    execution = (
        stdout.get("backtest_execution")
        if isinstance(stdout.get("backtest_execution"), dict)
        else {}
    )
    assertions = {
        "exit_zero": return_code == 0,
    }
    if run_name == "ungated":
        assertions.update(
            {
                "risk_gate_disabled": stdout.get("risk_gate_enabled") is False,
                "run_completed": stdout.get("run_status") == "completed",
                "metrics_present": isinstance(metrics, dict),
                "metrics_exact_keys": set(metrics or {}) == set(PARITY_METRIC_KEYS),
                "cap_not_applicable": cap_parity.get("status") == "not_applicable",
                "execution_run": execution.get("status") == "run",
                "invocation_count_one": execution.get("backtest_invocation_count") == 1,
            }
        )
    elif run_name in STOPPED_RUN_NAMES:
        assertions.update(
            {
                "risk_gate_enabled": stdout.get("risk_gate_enabled") is True,
                "run_stopped": stdout.get("run_status") == "stopped",
                "metrics_absent": metrics is None,
                "decision_class": risk_gate.get("decision_class") == run_name,
                "execution_state_stopped": risk_gate.get("execution_state") == "stopped",
                "cap_not_applicable": cap_parity.get("status") == "not_applicable",
                "execution_not_run": execution.get("status") == "not_run",
                "risk_gate_stop_reason": execution.get("reason") == "risk_gate_stop",
                "invocation_count_zero": execution.get("backtest_invocation_count") == 0,
                "validator_valid": validator_payload is not None
                and validator_payload.get("schema_version") == RESULT_SCHEMA_VERSION
                and validator_payload.get("valid") is True,
            }
        )
    else:
        assertions.update(
            {
                "risk_gate_enabled": stdout.get("risk_gate_enabled") is True,
                "run_completed": stdout.get("run_status") == "completed",
                "metrics_present": isinstance(metrics, dict),
                "metrics_exact_keys": set(metrics or {}) == set(PARITY_METRIC_KEYS),
                "decision_class": risk_gate.get("decision_class") == run_name,
                "execution_state_continued": risk_gate.get("execution_state") == "continued",
                "execution_run": execution.get("status") == "run",
                "invocation_count_one": execution.get("backtest_invocation_count") == 1,
                "validator_valid": validator_payload is not None
                and validator_payload.get("schema_version") == RESULT_SCHEMA_VERSION
                and validator_payload.get("valid") is True,
            }
        )
        if run_name == "size":
            assertions["cap_not_applicable"] = cap_parity.get("status") == "not_applicable"
        if run_name == "cap":
            assertions.update(
                {
                    "application_applied": risk_gate.get("application_status") == "applied",
                    "runtime_sizing_applied": risk_gate.get("runtime_sizing_applied") is True,
                    "sizing_effect_reduced": risk_gate.get("sizing_effect") == "reduced",
                    "requested_size_unit": risk_gate.get("requested_size") == 1.0,
                    "requested_size_basis": risk_gate.get("requested_size_basis") == "unit_backtest_run",
                    "effective_size_matches_allowed": risk_gate.get("effective_size") == risk_gate.get("allowed_size"),
                    "cap_not_applicable": cap_parity.get("status") == "not_applicable",
                }
            )
    return assertions


def run_backtest_replay(run_name: str, report_dir: Path) -> dict[str, Any]:
    run_dir = repo_path(report_dir) / "backtest_risk_gate_closure" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_stdout_path = run_dir / "stdout.raw.json"
    stdout_path = run_dir / "stdout.json"
    policy_path: Path | None = None
    artifact_root: Path | None = None
    if run_name in GATED_RUN_NAMES:
        policy_path = run_dir / "policy.json"
        write_policy(policy_path, run_name)
        artifact_root = artifact_root_for(report_dir, run_name)
        repo_path(artifact_root).mkdir(parents=True, exist_ok=True)

    command = backtest_command(run_name, policy_path=policy_path, artifact_root=artifact_root)
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    raw_stdout_path.write_text(result.stdout, encoding="utf-8")
    stdout_json, parse_error = parse_stdout_json(result.stdout)
    if stdout_json is not None:
        write_json(stdout_json, stdout_path)
    else:
        stdout_path.write_text(result.stdout, encoding="utf-8")

    risk_gate = (
        stdout_json.get("risk_gate")
        if isinstance(stdout_json, dict) and isinstance(stdout_json.get("risk_gate"), dict)
        else {}
    )
    artifact_path = risk_gate.get("artifact_path") if isinstance(risk_gate, dict) else None
    artifact_exists = isinstance(artifact_path, str) and Path(artifact_path).exists()
    validator_payload = (
        validator_cli_payload(Path(artifact_path))
        if artifact_exists and isinstance(artifact_path, str)
        else (
            None
            if run_name == "ungated"
            else missing_validator_payload(artifact_path if isinstance(artifact_path, str) else None)
        )
    )
    semantic_assertions = semantic_assertions_for(
        run_name,
        result.returncode,
        stdout_json,
        validator_payload,
    )
    passed = (
        parse_error == ""
        and stdout_json is not None
        and all(semantic_assertions.values())
        and (run_name == "ungated" or artifact_exists)
    )
    final_stdout_sha = (
        sha256_file(stdout_path)
        if stdout_path.exists() and stdout_path.read_text(encoding="utf-8")
        else ""
    )
    return {
        "run_name": run_name,
        "decision_class": None if run_name == "ungated" else run_name,
        "command_vector": command,
        "command": command_string(command),
        "return_code": result.returncode,
        "stdout_path": display_path(stdout_path),
        "raw_stdout_path": display_path(raw_stdout_path),
        "stdout_sha256": final_stdout_sha,
        "stderr": result.stderr,
        "stdout": stdout_json,
        "parse_error": parse_error,
        "policy_path": display_path(policy_path) if policy_path is not None else None,
        "artifact_root": display_path(repo_path(artifact_root)) if artifact_root else None,
        "semantic_assertions": semantic_assertions,
        "candidate_id": risk_gate.get("candidate_id") if isinstance(risk_gate, dict) else None,
        "artifact_path": display_path(Path(artifact_path)) if isinstance(artifact_path, str) else None,
        "artifact_sha256": sha256_file(Path(artifact_path)) if artifact_exists else None,
        "validator_payload": validator_payload,
        "passed": passed,
    }


def candidate_artifact_for(row: dict[str, Any]) -> tuple[str | None, str | None]:
    artifact_root = row.get("artifact_root")
    candidate_id = row.get("candidate_id")
    if not isinstance(artifact_root, str) or not isinstance(candidate_id, str):
        return None, None
    candidate_path = Path(artifact_root) / "candidates" / f"{candidate_id}.json"
    resolved = repo_path(candidate_path)
    if not resolved.exists():
        return display_path(resolved), None
    return display_path(resolved), sha256_file(resolved)


def build_run_manifest(replay_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for row in replay_rows:
        stdout = row.get("stdout") if isinstance(row.get("stdout"), dict) else {}
        risk_gate = (
            stdout.get("risk_gate") if isinstance(stdout.get("risk_gate"), dict) else {}
        )
        execution = (
            stdout.get("backtest_execution")
            if isinstance(stdout.get("backtest_execution"), dict)
            else {}
        )
        validator_payload = row.get("validator_payload")
        candidate_artifact_path, candidate_artifact_sha256 = candidate_artifact_for(row)
        manifest.append(
            {
                "run_name": row["run_name"],
                "risk_gate_enabled": stdout.get("risk_gate_enabled"),
                "decision_class": row.get("decision_class"),
                "execution_state": risk_gate.get("execution_state"),
                "run_status": stdout.get("run_status"),
                "backtest_invocation_count": execution.get(
                    "backtest_invocation_count"
                ),
                "candidate_id": row.get("candidate_id"),
                "policy_path": row.get("policy_path"),
                "artifact_root": row.get("artifact_root"),
                "candidate_artifact_path": candidate_artifact_path,
                "candidate_artifact_sha256": candidate_artifact_sha256,
                "decision_artifact_path": row.get("artifact_path"),
                "decision_artifact_sha256": row.get("artifact_sha256"),
                "stdout_path": row.get("stdout_path"),
                "stdout_sha256": row.get("stdout_sha256"),
                "validator_valid": (
                    validator_payload.get("valid")
                    if isinstance(validator_payload, dict)
                    else None
                ),
                "passed": row.get("passed"),
            }
        )
    return manifest


def canonical_metric_hash(stdout: dict[str, Any]) -> dict[str, Any]:
    metrics = stdout["metrics"]
    payload = {key: metrics[key] for key in sorted(PARITY_METRIC_KEYS)}
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return {
        "payload": payload,
        "canonical_json": canonical,
        "sha256": sha256_bytes(canonical.encode("utf-8")),
    }


def metric_hash_or_error(
    stdout: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(stdout, dict):
        return None, "stdout missing or not an object"
    metrics = stdout.get("metrics")
    if not isinstance(metrics, dict):
        return None, "metrics missing or not an object"
    missing = [key for key in PARITY_METRIC_KEYS if key not in metrics]
    if missing:
        return None, f"metrics missing keys: {missing}"
    return canonical_metric_hash(stdout), None


def runtime_sizing_effect_check(
    replay_rows: list[dict[str, Any]],
    stdout_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows_by_name = {row["run_name"]: row for row in replay_rows}
    ungated_stdout = stdout_overrides.get("ungated") if stdout_overrides else None
    cap_stdout = stdout_overrides.get("cap") if stdout_overrides else None
    ungated_stdout = ungated_stdout or rows_by_name["ungated"]["stdout"]
    cap_stdout = cap_stdout or rows_by_name["cap"]["stdout"]
    ungated_hash, ungated_error = metric_hash_or_error(ungated_stdout)
    cap_hash, cap_error = metric_hash_or_error(cap_stdout)
    errors = {
        key: value
        for key, value in {
            "ungated": ungated_error,
            "cap": cap_error,
        }.items()
        if value is not None
    }
    passed = not errors and ungated_hash["sha256"] != cap_hash["sha256"]
    if errors:
        return {
            "name": "runtime_sizing_effect",
            "status": "FAIL",
            "passed": False,
            "ungated_metrics_hash": ungated_hash,
            "cap_metrics_hash": cap_hash,
            "errors": errors,
        }
    enriched = {
        "ungated_metrics_hash": ungated_hash,
        "cap_metrics_hash": cap_hash,
    }
    for run_name in ("ungated", "cap"):
        row = rows_by_name[run_name]
        stdout = stdout_overrides.get(run_name) if stdout_overrides else None
        stdout = json.loads(json.dumps(stdout or row["stdout"]))
        stdout.setdefault("cap_parity", {}).update(enriched)
        row["stdout"] = stdout
        write_json(stdout, Path(row["stdout_path"]))
        row["stdout_sha256"] = sha256_file(Path(row["stdout_path"]))
    return {
        "name": "runtime_sizing_effect",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "ungated_metrics_hash": ungated_hash,
        "cap_metrics_hash": cap_hash,
        "errors": {},
    }


def backtest_replay_check(replay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    passed = all(row["passed"] for row in replay_rows)
    return {
        "name": "backtest_replay",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "run_count": len(replay_rows),
        "runs": replay_rows,
    }


def artifact_validation_check(replay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    for row in replay_rows:
        if row["run_name"] not in GATED_RUN_NAMES:
            continue
        artifact_path = row.get("artifact_path")
        payload = (
            validator_cli_payload(Path(artifact_path))
            if isinstance(artifact_path, str) and repo_path(Path(artifact_path)).exists()
            else missing_validator_payload(artifact_path if isinstance(artifact_path, str) else None)
        )
        artifacts.append(
            {
                "run_name": row["run_name"],
                "decision_class": row["decision_class"],
                "candidate_id": row["candidate_id"],
                "artifact_path": artifact_path,
                "artifact_sha256": row.get("artifact_sha256"),
                "validator_payload": payload,
                "passed": payload.get("schema_version") == RESULT_SCHEMA_VERSION
                and payload.get("valid") is True
                and payload.get("errors") == [],
            }
        )
    passed = len(artifacts) == len(GATED_RUN_NAMES) and all(
        row["passed"] for row in artifacts
    )
    return {
        "name": "artifact_validation",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }


def infra_command_case(
    name: str,
    policy_path: Path | None,
    artifact_root: Path | None,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    command = backtest_command("infra", policy_path=policy_path, artifact_root=artifact_root)
    if extra_args:
        command.extend(extra_args)
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    passed = (
        result.returncode != 0
        and result.stdout == ""
        and result.stderr != ""
        and '"run_status":"stopped"' not in result.stderr
    )
    return {
        "name": name,
        "command_vector": command,
        "command": command_string(command),
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "passed": passed,
    }


def infra_failure_check(report_dir: Path) -> dict[str, Any]:
    infra_dir = repo_path(report_dir) / "backtest_risk_gate_closure" / "infra_failures"
    infra_dir.mkdir(parents=True, exist_ok=True)
    invalid_policy = infra_dir / "invalid-policy.json"
    non_object_policy = infra_dir / "non-object-policy.json"
    invalid_policy.write_text("{", encoding="utf-8")
    non_object_policy.write_text("[]\n", encoding="utf-8")
    safe_root = Path("target") / "phase148-backtest-risk-gate" / "infra"
    safe_root.mkdir(parents=True, exist_ok=True)
    cases = [
        infra_command_case("invalid policy JSON", invalid_policy, safe_root / "invalid-json"),
        infra_command_case(
            "non-object policy JSON",
            non_object_policy,
            safe_root / "non-object-policy",
        ),
        infra_command_case(
            "unsafe artifact root",
            non_object_policy,
            Path("reports/../risk_gate"),
        ),
        infra_command_case(
            "malformed candidate or validator failure",
            None,
            None,
            extra_args=["--risk-gate-policy", str(invalid_policy)],
        ),
    ]
    passed = all(case["passed"] for case in cases)
    return {
        "name": "infra_failures",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "cases": cases,
    }


def check_v57_integrity(
    expected_sha256: dict[str, str] | None = None,
) -> dict[str, Any]:
    expected = expected_sha256 or V57_EXPECTED_SHA256
    top_level_reports: list[dict[str, Any]] = []
    for path, expected_sha in expected.items():
        exists = repo_path(Path(path)).exists()
        actual = sha256_file(Path(path)) if exists else None
        top_level_reports.append(
            {
                "path": path,
                "exists": exists,
                "sha256": actual,
                "expected_sha256": expected_sha,
                "passed": exists and actual == expected_sha,
            }
        )
    tracked = subprocess.run(
        ["git", "ls-files", "reports/v5.7"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    tracked_paths = split_paths(tracked.stdout) if tracked.returncode == 0 else []
    risk_artifacts = sorted(
        path.as_posix()
        for path in (ROOT / "reports/v5.7/risk_gate").glob("**/*.json")
        if path.is_file()
    )
    passed = (
        all(row["passed"] for row in top_level_reports)
        and tracked.returncode == 0
        and len(tracked_paths) == 28
        and len(risk_artifacts) == 15
    )
    return {
        "name": "v57_integrity",
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "top_level_reports": top_level_reports,
        "tracked_report_file_count": len(tracked_paths),
        "tracked_report_files": tracked_paths,
        "risk_artifact_count": len(risk_artifacts),
        "risk_artifacts": [
            display_path(Path(path)) for path in risk_artifacts
        ],
    }


def is_allowed_phase148_path(path: str) -> bool:
    return path in ALLOWED_PHASE148_PATHS or path.startswith(ALLOWED_PHASE148_PREFIXES)


def is_forbidden_runtime_path(path: str) -> bool:
    return path in FORBIDDEN_RUNTIME_PATHS or path.startswith(FORBIDDEN_RUNTIME_PREFIXES)


def is_forbidden_v4_archive_path(path: str) -> bool:
    return path.startswith(FORBIDDEN_V4_PREFIXES)


def collect_scope_guard(
    *,
    diff_base: str | None,
    changed_paths: list[str] | None = None,
    staged_changed_paths: list[str] | None = None,
    untracked_paths: list[str] | None = None,
    committed_changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    committed_command_vector = (
        RUNTIME_SCOPE_COMMANDS["committed"] + [f"{diff_base}..HEAD"]
        if diff_base is not None
        else RUNTIME_SCOPE_COMMANDS["committed"] + ["<missing-diff-base>..HEAD"]
    )
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
    allowed_phase148_paths: list[str] = []
    forbidden_runtime_paths: list[str] = []
    forbidden_v4_archive_paths: list[str] = []
    forbidden_v57_paths: list[str] = []
    unexpected_paths: list[str] = []
    for path in changed_path_union:
        if path.startswith("reports/v5.7/"):
            forbidden_v57_paths.append(path)
        elif is_forbidden_v4_archive_path(path):
            forbidden_v4_archive_paths.append(path)
        elif is_forbidden_runtime_path(path):
            forbidden_runtime_paths.append(path)
        elif is_allowed_phase148_path(path):
            allowed_phase148_paths.append(path)
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
        and forbidden_v57_paths == []
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
        "allowed_phase148_paths": allowed_phase148_paths,
        "forbidden_runtime_paths": forbidden_runtime_paths,
        "forbidden_v4_archive_paths": forbidden_v4_archive_paths,
        "forbidden_v57_paths": forbidden_v57_paths,
        "unexpected_paths": unexpected_paths,
        "commands_passed": commands_passed,
    }


def build_backtest_risk_gate_closure_evidence(
    *,
    report_dir: Path,
    diff_base: str | None,
    committed_changed_paths: list[str] | None = None,
    changed_paths: list[str] | None = None,
    staged_changed_paths: list[str] | None = None,
    untracked_paths: list[str] | None = None,
    stdout_overrides: dict[str, dict[str, Any]] | None = None,
    write_reports: bool = False,
) -> dict[str, Any]:
    replay_rows = [run_backtest_replay(run_name, report_dir) for run_name in RUN_NAMES]
    runtime_sizing_effect = runtime_sizing_effect_check(
        replay_rows, stdout_overrides=stdout_overrides
    )
    checks = {
        "backtest_replay": backtest_replay_check(replay_rows),
        "runtime_sizing_effect": runtime_sizing_effect,
        "artifact_validation": artifact_validation_check(replay_rows),
        "infra_failures": infra_failure_check(report_dir),
        "v57_integrity": check_v57_integrity(),
        "scope_guard": collect_scope_guard(
            diff_base=diff_base,
            committed_changed_paths=committed_changed_paths,
            changed_paths=changed_paths,
            staged_changed_paths=staged_changed_paths,
            untracked_paths=untracked_paths,
        ),
    }
    checks_passed = sum(1 for check in checks.values() if check["passed"])
    checks_failed = len(checks) - checks_passed
    close_ready = checks_failed == 0
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "requirements_addressed": REQUIREMENTS_ADDRESSED,
        "source_inputs": {
            "schema": display_path(repo_path(SCHEMA_PATH)),
            "validator": display_path(repo_path(VALIDATOR_PATH)),
            "diff_base": diff_base,
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
        "run_manifest": build_run_manifest(replay_rows),
        "runs": {
            row["run_name"]: {
                key: value
                for key, value in row.items()
                if key
                in {
                    "run_name",
                    "decision_class",
                    "command_vector",
                    "command",
                    "return_code",
                    "stdout_path",
                    "raw_stdout_path",
                    "stdout_sha256",
                    "stderr",
                    "policy_path",
                    "artifact_root",
                    "candidate_id",
                    "artifact_path",
                    "artifact_sha256",
                    "semantic_assertions",
                    "passed",
                }
            }
            for row in replay_rows
        },
        "checks": checks,
    }
    if write_reports and close_ready:
        output_dir = repo_path(report_dir)
        write_json(evidence, output_dir / JSON_REPORT_NAME)
        (output_dir / MD_REPORT_NAME).write_text(
            render_markdown(evidence),
            encoding="utf-8",
        )
    return evidence


def check_detail(check: dict[str, Any]) -> str:
    name = check["name"]
    if name == "backtest_replay":
        passed_count = sum(1 for row in check["runs"] if row["passed"])
        return f"{passed_count}/{check['run_count']} backtest replays passed."
    if name == "runtime_sizing_effect":
        return "ungated and capped canonical metric hashes differ, proving runtime sizing applied."
    if name == "artifact_validation":
        passed_count = sum(1 for row in check["artifacts"] if row["passed"])
        return f"{passed_count}/{len(check['artifacts'])} emitted artifacts validated."
    if name == "infra_failures":
        passed_count = sum(1 for row in check["cases"] if row["passed"])
        return f"{passed_count}/{len(check['cases'])} infra failure cases stayed stderr-only."
    if name == "v57_integrity":
        return f"{check['tracked_report_file_count']} v5.7 files and {check['risk_artifact_count']} risk artifacts checked."
    if name == "scope_guard":
        blocked = (
            len(check["forbidden_runtime_paths"])
            + len(check["forbidden_v4_archive_paths"])
            + len(check["forbidden_v57_paths"])
            + len(check["unexpected_paths"])
        )
        return f"{blocked} forbidden or unexpected Phase 148 path(s)."
    return ""


def render_markdown(evidence: dict[str, Any]) -> str:
    checks = evidence["checks"]
    lines = [
        "# v5.11 Backtest Risk Gate Closure Evidence",
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
        "backtest_replay",
        "runtime_sizing_effect",
        "artifact_validation",
        "infra_failures",
        "v57_integrity",
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
            "## Run Manifest",
            "",
            "| Run | Gate | Decision | Execution | Invocations | Candidate | Candidate artifact | Decision artifact | Stdout | Validator valid | Passed |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in evidence["run_manifest"]:
        lines.append(
            f"| `{row['run_name']}` | {row['risk_gate_enabled']} | "
            f"`{row['decision_class']}` | `{row['execution_state']}` | "
            f"{row['backtest_invocation_count']} | `{row['candidate_id']}` | "
            f"`{row['candidate_artifact_path']}` | "
            f"`{row['decision_artifact_path']}` | `{row['stdout_path']}` | "
            f"{row['validator_valid']} | {row['passed']} |"
        )
    lines.extend(
        [
            "",
            "## Backtest Replay",
            "",
            "| Run | Status | Candidate | Artifact | Validator valid | Passed |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for run_name in RUN_NAMES:
        run = evidence["runs"][run_name]
        validator_payload = next(
            (
                row["validator_payload"]
                for row in checks["artifact_validation"]["artifacts"]
                if row["run_name"] == run_name
            ),
            {},
        )
        lines.append(
            f"| `{run_name}` | {run['return_code']} | `{run.get('candidate_id')}` | "
            f"`{run.get('artifact_path')}` | {validator_payload.get('valid', '')} | "
            f"{run['passed']} |"
        )

    cap = checks["runtime_sizing_effect"]
    lines.extend(
        [
            "",
            "## Runtime Sizing Effect",
            "",
            "| Metric Hash | SHA-256 | Canonical JSON |",
            "| --- | --- | --- |",
            f"| Ungated | `{cap['ungated_metrics_hash']['sha256']}` | `{cap['ungated_metrics_hash']['canonical_json']}` |",
            f"| Cap | `{cap['cap_metrics_hash']['sha256']}` | `{cap['cap_metrics_hash']['canonical_json']}` |",
            "",
            "## Artifact Validation",
            "",
            "| Run | Artifact path | Schema version | Valid | Errors | Passed |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in checks["artifact_validation"]["artifacts"]:
        payload = row["validator_payload"]
        lines.append(
            f"| `{row['run_name']}` | `{row['artifact_path']}` | "
            f"`{payload.get('schema_version')}` | {payload.get('valid')} | "
            f"{payload.get('errors')} | {row['passed']} |"
        )

    scope = checks["scope_guard"]
    lines.extend(
        [
            "",
            "## v5.7 Integrity",
            "",
            f"- Tracked report files: {checks['v57_integrity']['tracked_report_file_count']}",
            f"- Risk artifacts: {checks['v57_integrity']['risk_artifact_count']}",
            "",
            "## Scope Guard",
            "",
            "| Category | Summary |",
            "| --- | --- |",
            f"| Allowed Phase 148 paths | {format_scope_guard_path_summary(scope['allowed_phase148_paths'])} |",
            f"| Forbidden runtime paths | {format_scope_guard_path_summary(scope['forbidden_runtime_paths'])} |",
            f"| Forbidden v4 archive paths | {format_scope_guard_path_summary(scope['forbidden_v4_archive_paths'])} |",
            f"| Forbidden v5.7 paths | {format_scope_guard_path_summary(scope['forbidden_v57_paths'])} |",
            f"| Unexpected paths | {format_scope_guard_path_summary(scope['unexpected_paths'])} |",
            "",
            "## Replay Commands",
            "",
        ]
    )
    for run_name in RUN_NAMES:
        run = evidence["runs"][run_name]
        command_vector = run.get("command_vector")
        command = (
            markdown_command_string(command_vector)
            if isinstance(command_vector, list)
            else run["command"]
        )
        lines.append(f"- `{run_name}`: `{command}`")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--diff-base")
    return parser


def is_committed_report_dir(report_dir: Path) -> bool:
    return repo_path(report_dir).resolve() == repo_path(DEFAULT_REPORT_DIR).resolve()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.diff_base is None and is_committed_report_dir(args.report_dir):
            parser.error("--diff-base is required when writing committed closure evidence")
        evidence = build_backtest_risk_gate_closure_evidence(
            report_dir=args.report_dir,
            diff_base=args.diff_base or "HEAD",
            write_reports=True,
        )
    except SystemExit as exc:
        return int(exc.code)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(
            f"generate_backtest_risk_gate_closure_evidence.py: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0 if evidence["summary"]["close_readiness"] == "ready_for_milestone_completion" else 1


if __name__ == "__main__":
    raise SystemExit(main())
