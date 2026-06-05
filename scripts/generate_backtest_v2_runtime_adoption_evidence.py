#!/usr/bin/env python3
"""Generate replayable risk_contract.v2 backtest runtime adoption evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROOT_RESOLVED = ROOT.resolve()
SCHEMA_VERSION = "risk_contract_v2_backtest_runtime_adoption_evidence.v1"
BOUNDARY = "risk_contract_v2_backtest_runtime_adoption_evidence"
DEFAULT_REPORT_DIR = Path("reports/risk-contract-v2/backtest-runtime-adoption")
JSON_REPORT_NAME = "backtest_v2_runtime_adoption_evidence.json"
MD_REPORT_NAME = "backtest_v2_runtime_adoption_evidence.md"

RUN_SPECS: tuple[dict[str, str], ...] = (
    {"run_name": "cap", "decision_class": "cap"},
    {"run_name": "size", "decision_class": "size"},
    {"run_name": "reject", "decision_class": "reject"},
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

SOURCE_EVIDENCE_PATHS: tuple[str, ...] = (
    "docs/plans/2026-05-18-risk-contract-v2-runtime-adoption-design.md",
    "docs/plans/2026-05-18-risk-contract-v2-backtest-runtime-adoption-tdd.md",
    "docs/plans/2026-05-18-risk-contract-v2-backtest-evidence-replay-hardening-tdd.md",
    "risk/contracts/v2/risk_contract_v2.schema.json",
    "risk/contracts/v2/risk_contract_validator_result_v2.schema.json",
    "scripts/validate_risk_contract.py",
    "scripts/evaluate_risk_gate.py",
    "scripts/generate_backtest_v2_runtime_adoption_evidence.py",
    "rust/side-cli/src/cmd/backtest.rs",
    "rust/side-cli/tests/backtest_cli_test.rs",
    "tests/test_generate_backtest_v2_runtime_adoption_evidence.py",
)

REPLAY_CONTRACT: tuple[dict[str, str], ...] = (
    {
        "id": "v2_public_version_proof",
        "claim": "Every replayed backtest risk_gate block exposes risk_contract.v2 and validator-result v2 proof.",
        "evidence": "stdout risk_gate schema_version, contract_version, validator_result_schema_version, and schema_ref.",
        "status": "required",
    },
    {
        "id": "v2_validator_replay",
        "claim": "Every emitted v2 decision artifact revalidates through scripts/validate_risk_contract.py.",
        "evidence": "validator replay payload has risk_contract_validator_result.v2 and valid=true.",
        "status": "required",
    },
    {
        "id": "cap_runtime_application",
        "claim": "The cap decision applies runtime sizing for the supported backtest surface.",
        "evidence": "cap stdout and artifact application both show applied runtime sizing without metric rescaling.",
        "status": "required",
    },
    {
        "id": "stop_before_metrics",
        "claim": "The reject decision remains stopped before metric-producing backtest execution.",
        "evidence": "reject stdout has metrics=null and backtest_invocation_count=0.",
        "status": "required",
    },
    {
        "id": "fresh_namespaced_output",
        "claim": "v2 replay output is isolated from protected historical/report/contract roots.",
        "evidence": "report_dir and artifact roots are under reports/risk-contract-v2 by default.",
        "status": "required",
    },
)


def repo_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_RESOLVED).as_posix()
    except ValueError:
        return path.as_posix()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(repo_path(path).read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(repo_path(path).read_text(encoding="utf-8"))


def write_json(data: Any, path: Path) -> None:
    target = repo_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(content: str, path: Path) -> None:
    target = repo_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def protected_prefix_matches(display: str, prefix: str) -> bool:
    if prefix.endswith("."):
        return display.startswith(prefix)
    return display == prefix or display.startswith(prefix.rstrip("/") + "/")


def assert_allowed_report_dir(report_dir: Path) -> None:
    display = display_path(repo_path(report_dir))
    for prefix in PROTECTED_OUTPUT_PREFIXES:
        if protected_prefix_matches(display, prefix):
            raise ValueError(f"protected output directory is not allowed: {display}")


def build_protected_surface_guard(report_dir: Path) -> dict[str, Any]:
    assert_allowed_report_dir(report_dir)
    resolved = repo_path(report_dir)
    return {
        "passed": True,
        "report_dir": display_path(resolved),
        "planned_output_paths": [
            display_path(resolved / JSON_REPORT_NAME),
            display_path(resolved / MD_REPORT_NAME),
            display_path(resolved / "runs"),
        ],
        "protected_prefixes": list(PROTECTED_OUTPUT_PREFIXES),
        "reason": "fresh risk_contract.v2 backtest replay outputs only; protected historical/report/contract roots are not write targets",
    }


def build_source_evidence() -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for path_text in SOURCE_EVIDENCE_PATHS:
        path = Path(path_text)
        resolved = repo_path(path)
        exists = resolved.exists()
        sources.append(
            {
                "path": path_text,
                "role": "read_only_input",
                "exists": exists,
                "sha256": sha256_file(path) if exists else None,
            }
        )
    return sources


def policy_for(decision_class: str) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "id": f"phase-v2-evidence.{decision_class}",
        "decision_class": decision_class,
        "when": {
            "path": "candidate.requested_size",
            "op": "exists",
            "value": True,
        },
        "fail_close_reason": "insufficient_validation_power",
    }
    if decision_class == "cap":
        rule["allowed_size"] = 0.25
    return {
        "version": "risk-policy.v1.v2-backtest-evidence-test",
        "owner": "side-risk-contract-v2-backtest-runtime-adoption",
        "effective_from": "2026-05-18",
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


def side_cli_backtest_command(policy_path: Path, artifact_root: Path) -> list[str]:
    return [
        "cargo",
        "run",
        "-p",
        "side-cli",
        "--bin",
        "side",
        "--",
        "backtest",
        "--asset",
        "USDJPY",
        "--timeframe",
        "1h",
        "--data",
        "rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet",
        "--strategy",
        "tod_edge",
        "--params",
        '{"entry_minute":0,"direction":"long","hold_h":3}',
        "--fee-bps",
        "1.0",
        "--risk-gate-policy",
        display_path(policy_path),
        "--risk-gate-artifact-root",
        display_path(artifact_root),
        "--risk-gate-contract-version",
        "v2",
    ]


def validator_command(artifact_path: Path) -> list[str]:
    return [
        sys.executable,
        "scripts/validate_risk_contract.py",
        display_path(artifact_path),
    ]


def run_validator_replay(artifact_path: Path) -> dict[str, Any]:
    command = validator_command(artifact_path)
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    payload: dict[str, Any] = {}
    if result.stdout.strip():
        loaded = json.loads(result.stdout)
        if isinstance(loaded, dict):
            payload = loaded
    payload["_command_vector"] = command
    payload["_return_code"] = result.returncode
    payload["_stderr"] = result.stderr
    return payload


def prepare_run_dir(run_dir: Path) -> None:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)


def run_backtest_v2_replay(run_name: str, decision_class: str, report_dir: Path) -> dict[str, Any]:
    assert_allowed_report_dir(report_dir)
    report_root = repo_path(report_dir)
    run_dir = report_root / "runs" / run_name
    prepare_run_dir(run_dir)

    policy_path = run_dir / "policy.json"
    artifact_root = run_dir / "risk_artifacts"
    raw_stdout_path = run_dir / "stdout.raw.json"
    stdout_path = run_dir / "stdout.json"
    write_json(policy_for(decision_class), policy_path)

    command = side_cli_backtest_command(policy_path, artifact_root)
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    raw_stdout_path.write_text(result.stdout, encoding="utf-8")

    stdout: dict[str, Any] = {}
    candidate: dict[str, Any] = {}
    artifact: dict[str, Any] = {}
    validator_payload: dict[str, Any] = {}
    candidate_id: str | None = None
    candidate_path: Path | None = None
    artifact_path: Path | None = None

    if result.returncode == 0:
        loaded_stdout = json.loads(result.stdout)
        if not isinstance(loaded_stdout, dict):
            raise ValueError(f"{run_name} stdout top-level JSON must be an object")
        stdout = loaded_stdout
        write_json(stdout, stdout_path)
        risk_gate = stdout["risk_gate"]
        candidate_id = risk_gate["candidate_id"]
        candidate_path = artifact_root / "candidates" / f"{candidate_id}.json"
        artifact_path = repo_path(Path(risk_gate["artifact_path"]))
        candidate = load_json(candidate_path)
        artifact = load_json(artifact_path)
        validator_payload = run_validator_replay(artifact_path)

    row = {
        "run_name": run_name,
        "decision_class": decision_class,
        "command_vector": command,
        "return_code": result.returncode,
        "stdout_path": display_path(stdout_path),
        "raw_stdout_path": display_path(raw_stdout_path),
        "stderr": result.stderr,
        "policy_path": display_path(policy_path),
        "artifact_root": display_path(artifact_root),
        "candidate_id": candidate_id,
        "candidate_path": display_path(candidate_path) if candidate_path else None,
        "artifact_path": display_path(artifact_path) if artifact_path else None,
        "stdout": stdout,
        "candidate": candidate,
        "artifact": artifact,
        "validator_payload": validator_payload,
    }
    row["passed"] = replay_row_passed(row)
    return row


def run_backtest_v2_replays(report_dir: Path) -> list[dict[str, Any]]:
    assert_allowed_report_dir(report_dir)
    return [
        run_backtest_v2_replay(spec["run_name"], spec["decision_class"], report_dir)
        for spec in RUN_SPECS
    ]


def replay_row_passed(row: dict[str, Any]) -> bool:
    return (
        row.get("return_code") == 0
        and row.get("stdout", {}).get("risk_gate", {}).get("contract_version") == "v2"
        and row.get("candidate", {}).get("candidate_schema_version") == "risk_contract.v2.candidate.v1"
        and row.get("artifact", {}).get("schema_version") == "risk_contract.v2"
        and row.get("artifact", {}).get("contract_version") == "v2"
        and row.get("validator_payload", {}).get("schema_version") == "risk_contract_validator_result.v2"
        and row.get("validator_payload", {}).get("valid") is True
    )


def rows_by_name(replay_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["run_name"]: row for row in replay_rows}


def source_evidence_loaded_check(source_evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "passed": all(row["exists"] and row["sha256"] for row in source_evidence),
        "source_count": len(source_evidence),
        "missing_sources": [row["path"] for row in source_evidence if not row["exists"]],
    }


def v2_version_proof_check(replay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in replay_rows if row.get("return_code") == 0]
    version_proofs = []
    for row in rows:
        risk_gate = row["stdout"]["risk_gate"]
        version_proofs.append(
            {
                "run_name": row["run_name"],
                "schema_version": risk_gate.get("schema_version"),
                "contract_version": risk_gate.get("contract_version"),
                "validator_result_schema_version": risk_gate.get("validator_result_schema_version"),
                "schema_ref": risk_gate.get("schema_ref"),
                "validated_schema_ref": risk_gate.get("validated_schema_ref"),
            }
        )
    passed = len(version_proofs) == len(RUN_SPECS) and all(
        proof["schema_version"] == "risk_contract.v2"
        and proof["contract_version"] == "v2"
        and proof["validator_result_schema_version"] == "risk_contract_validator_result.v2"
        and proof["schema_ref"] == "risk/contracts/v2/risk_contract_v2.schema.json"
        and proof["validated_schema_ref"] == "risk/contracts/v2/risk_contract_v2.schema.json"
        for proof in version_proofs
    )
    return {
        "passed": passed,
        "contract_version": "v2",
        "validator_result_schema_version": "risk_contract_validator_result.v2",
        "version_proofs": version_proofs,
    }


def validator_replay_check(replay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    artifacts = []
    for row in replay_rows:
        payload = row.get("validator_payload", {})
        artifacts.append(
            {
                "run_name": row["run_name"],
                "artifact_path": row.get("artifact_path"),
                "schema_version": payload.get("schema_version"),
                "valid": payload.get("valid"),
                "contract_identity": payload.get("contract_identity"),
                "validated_schema": payload.get("validated_schema"),
                "dispatch": payload.get("dispatch"),
                "return_code": payload.get("_return_code", 0),
                "errors": payload.get("errors"),
            }
        )
    valid_artifacts = [
        row
        for row in artifacts
        if row["schema_version"] == "risk_contract_validator_result.v2"
        and row["valid"] is True
        and row.get("contract_identity", {}).get("schema_version") == "risk_contract.v2"
        and row.get("contract_identity", {}).get("contract_version") == "v2"
        and row.get("validated_schema", {}).get("path") == "risk/contracts/v2/risk_contract_v2.schema.json"
    ]
    return {
        "passed": len(valid_artifacts) == len(RUN_SPECS),
        "validated_artifacts": len(valid_artifacts),
        "artifacts": artifacts,
    }


def cap_runtime_application_check(replay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    cap = rows_by_name(replay_rows)["cap"]
    risk_gate = cap["stdout"]["risk_gate"]
    application = cap["artifact"]["application"]
    execution = cap["stdout"]["backtest_execution"]
    effective_size_equals_allowed_size = (
        risk_gate.get("effective_size") == risk_gate.get("allowed_size") == application.get("effective_size")
    )
    passed = all(
        [
            cap.get("passed") is True,
            risk_gate.get("application_status") == "applied",
            risk_gate.get("runtime_sizing_applied") is True,
            risk_gate.get("requested_size_basis") == "unit_backtest_run",
            application.get("application_status") == "applied",
            application.get("runtime_sizing_applied") is True,
            application.get("metrics_rescaled") is False,
            effective_size_equals_allowed_size,
            execution.get("backtest_invocation_count") == 1,
            cap["stdout"].get("metrics") is not None,
        ]
    )
    return {
        "passed": passed,
        "source": cap.get("artifact_path"),
        "application_status": risk_gate.get("application_status"),
        "runtime_sizing_applied": risk_gate.get("runtime_sizing_applied"),
        "requested_size_basis": risk_gate.get("requested_size_basis"),
        "allowed_size": risk_gate.get("allowed_size"),
        "effective_size": risk_gate.get("effective_size"),
        "effective_size_equals_allowed_size": effective_size_equals_allowed_size,
        "metrics_rescaled": application.get("metrics_rescaled"),
        "sizing_effect": risk_gate.get("sizing_effect"),
        "backtest_invocation_count": execution.get("backtest_invocation_count"),
    }


def size_continue_replay_check(replay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    size = rows_by_name(replay_rows)["size"]
    risk_gate = size["stdout"]["risk_gate"]
    application = size["artifact"]["application"]
    execution = size["stdout"]["backtest_execution"]
    passed = all(
        [
            size.get("passed") is True,
            risk_gate.get("decision_class") == "size",
            risk_gate.get("execution_state") == "continued",
            application.get("application_status") == "not_applicable",
            application.get("runtime_sizing_applied") is False,
            execution.get("backtest_invocation_count") == 1,
            size["stdout"].get("metrics") is not None,
        ]
    )
    return {
        "passed": passed,
        "source": size.get("artifact_path"),
        "execution_state": risk_gate.get("execution_state"),
        "application_status": application.get("application_status"),
        "runtime_sizing_applied": application.get("runtime_sizing_applied"),
        "backtest_invocation_count": execution.get("backtest_invocation_count"),
    }


def reject_stop_replay_check(replay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    reject = rows_by_name(replay_rows)["reject"]
    risk_gate = reject["stdout"]["risk_gate"]
    application = reject["artifact"]["application"]
    execution = reject["stdout"]["backtest_execution"]
    metrics_is_null = reject["stdout"].get("metrics") is None
    passed = all(
        [
            reject.get("passed") is True,
            risk_gate.get("decision_class") == "reject",
            risk_gate.get("execution_state") == "stopped",
            application.get("execution_state") == "stopped",
            metrics_is_null,
            execution.get("backtest_invocation_count") == 0,
        ]
    )
    return {
        "passed": passed,
        "source": reject.get("artifact_path"),
        "execution_state": risk_gate.get("execution_state"),
        "metrics_is_null": metrics_is_null,
        "backtest_invocation_count": execution.get("backtest_invocation_count"),
        "application_status": application.get("application_status"),
    }


def compact_run(row: dict[str, Any]) -> dict[str, Any]:
    risk_gate = row.get("stdout", {}).get("risk_gate", {})
    execution = row.get("stdout", {}).get("backtest_execution", {})
    return {
        "run_name": row.get("run_name"),
        "decision_class": row.get("decision_class"),
        "command_vector": row.get("command_vector"),
        "return_code": row.get("return_code"),
        "stdout_path": row.get("stdout_path"),
        "raw_stdout_path": row.get("raw_stdout_path"),
        "stderr": row.get("stderr"),
        "policy_path": row.get("policy_path"),
        "artifact_root": row.get("artifact_root"),
        "candidate_id": row.get("candidate_id"),
        "candidate_path": row.get("candidate_path"),
        "artifact_path": row.get("artifact_path"),
        "execution_state": risk_gate.get("execution_state"),
        "contract_version": risk_gate.get("contract_version"),
        "validator_result_schema_version": risk_gate.get("validator_result_schema_version"),
        "backtest_invocation_count": execution.get("backtest_invocation_count"),
        "validator_valid": row.get("validator_payload", {}).get("valid"),
        "passed": row.get("passed"),
    }


def build_backtest_v2_runtime_adoption_evidence(
    *,
    report_dir: Path = DEFAULT_REPORT_DIR,
    diff_base: str = "origin/master",
    replay_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    protected_surface_guard = build_protected_surface_guard(report_dir)
    source_evidence = build_source_evidence()
    rows = replay_rows if replay_rows is not None else run_backtest_v2_replays(report_dir)
    checks = {
        "source_evidence_loaded": source_evidence_loaded_check(source_evidence),
        "v2_version_proof": v2_version_proof_check(rows),
        "validator_replay": validator_replay_check(rows),
        "cap_runtime_application": cap_runtime_application_check(rows),
        "size_continue_replay": size_continue_replay_check(rows),
        "reject_stop_replay": reject_stop_replay_check(rows),
        "protected_surface_guard": protected_surface_guard,
    }
    checks_failed = sum(1 for check in checks.values() if not check.get("passed"))
    return {
        "schema_version": SCHEMA_VERSION,
        "boundary": BOUNDARY,
        "summary": {
            "overall_status": "PASS" if checks_failed == 0 else "FAIL",
            "checks_passed": len(checks) - checks_failed,
            "checks_failed": checks_failed,
            "diff_base": diff_base,
            "implementation_scope": "backtest_v2_evidence_replay_only",
        },
        "source_evidence": source_evidence,
        "replay_contract": list(REPLAY_CONTRACT),
        "runs": {row["run_name"]: compact_run(row) for row in rows},
        "checks": checks,
        "protected_surface_guard": protected_surface_guard,
    }


def markdown_cell(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def markdown_command_string(command_vector: Any) -> str:
    if not isinstance(command_vector, list):
        return markdown_cell(command_vector)
    return shlex.join(str(part) for part in command_vector)


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# risk_contract.v2 Backtest Runtime Adoption Evidence",
        "",
        f"- Schema: `{report['schema_version']}`",
        f"- Boundary: `{report['boundary']}`",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Implementation scope: `{summary['implementation_scope']}`",
        f"- Diff base: `{summary['diff_base']}`",
        "",
        "## Source Evidence",
        "",
        "| Path | Role | Exists | SHA256 |",
        "|---|---|---:|---|",
    ]
    for source in report["source_evidence"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(source["path"]),
                    markdown_cell(source["role"]),
                    markdown_cell(source["exists"]),
                    markdown_cell(source["sha256"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Replay Contract",
            "",
            "| Claim | Status | Evidence |",
            "|---|---|---|",
        ]
    )
    for row in report["replay_contract"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(row["id"]),
                    markdown_cell(row["status"]),
                    markdown_cell(row["evidence"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Run Manifest",
            "",
            "| Run | Decision | Return | Execution | Contract | Validator | Invocations | Artifact | Passed |",
            "|---|---|---:|---|---|---|---:|---|---:|",
        ]
    )
    for run_name in sorted(report["runs"]):
        run = report["runs"][run_name]
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(run_name),
                    markdown_cell(run["decision_class"]),
                    markdown_cell(run["return_code"]),
                    markdown_cell(run["execution_state"]),
                    markdown_cell(run["contract_version"]),
                    markdown_cell(run["validator_result_schema_version"]),
                    markdown_cell(run["backtest_invocation_count"]),
                    markdown_cell(run["artifact_path"]),
                    markdown_cell(run["passed"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Check Results",
            "",
            "| Check | Passed | Source | Notes |",
            "|---|---:|---|---|",
        ]
    )
    for name, check in report["checks"].items():
        notes = []
        for key, value in check.items():
            if key in {"passed", "source", "artifacts", "version_proofs", "protected_prefixes", "planned_output_paths"}:
                continue
            notes.append(f"{key}={markdown_cell(value)}")
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(name),
                    markdown_cell(check.get("passed")),
                    markdown_cell(check.get("source", "")),
                    markdown_cell("; ".join(notes)),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Protected Surface Guard",
            "",
            f"- Result: `{markdown_cell(report['protected_surface_guard']['passed'])}`",
            f"- Report dir: `{markdown_cell(report['protected_surface_guard']['report_dir'])}`",
            "- Planned outputs:",
        ]
    )
    for output_path in report["protected_surface_guard"]["planned_output_paths"]:
        lines.append(f"  - `{markdown_cell(output_path)}`")
    lines.extend(["", "## Replay Commands", ""])
    for run_name in sorted(report["runs"]):
        run = report["runs"][run_name]
        lines.append(f"- `{run_name}`: `{markdown_command_string(run['command_vector'])}`")
    return "\n".join(lines).rstrip() + "\n"


def write_reports(report: dict[str, Any], report_dir: Path) -> None:
    write_json(report, report_dir / JSON_REPORT_NAME)
    write_text(render_markdown(report), report_dir / MD_REPORT_NAME)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--diff-base", default="origin/master")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_backtest_v2_runtime_adoption_evidence(
            report_dir=args.report_dir,
            diff_base=args.diff_base,
        )
        write_reports(report, args.report_dir)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(
            f"generate_backtest_v2_runtime_adoption_evidence.py: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0 if report["summary"]["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
