#!/usr/bin/env python3
"""Generate replayable risk_contract.v2 scan runtime adoption evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROOT_RESOLVED = ROOT.resolve()
SCHEMA_VERSION = "risk_contract_v2_scan_runtime_adoption_evidence.v1"
BOUNDARY = "risk_contract_v2_scan_runtime_adoption_evidence"
DEFAULT_REPORT_DIR = Path("reports/risk-contract-v2/scan-runtime-adoption")
JSON_REPORT_NAME = "scan_v2_runtime_adoption_evidence.json"
MD_REPORT_NAME = "scan_v2_runtime_adoption_evidence.md"

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
    "docs/plans/2026-05-18-risk-contract-v2-scan-runtime-statistical-split-design.md",
    "docs/superpowers/plans/2026-05-18-scan-v2-runtime-adoption-tdd.md",
    "docs/superpowers/plans/2026-05-18-scan-v2-evidence-replay-hardening-tdd.md",
    "reports/v8.3/scan_wfd_runtime_cap_application_evidence.md",
    "risk/contracts/v2/risk_contract_v2.schema.json",
    "risk/contracts/v2/risk_contract_validator_result_v2.schema.json",
    "scripts/validate_risk_contract.py",
    "scripts/evaluate_risk_gate.py",
    "scripts/generate_scan_v2_runtime_adoption_evidence.py",
    "rust/side-cli/src/cmd/scan.rs",
    "rust/side-cli/tests/risk_gate_test.rs",
    "tests/test_generate_scan_v2_runtime_adoption_evidence.py",
)

REPLAY_CONTRACT: tuple[dict[str, str], ...] = (
    {
        "id": "v2_public_version_proof",
        "claim": "Every replayed scan risk_gate block exposes risk_contract.v2 and validator-result v2 proof.",
        "evidence": "slot risk_gate schema_version, contract_version, validator_result_schema_version, and schema_ref.",
        "status": "required",
    },
    {
        "id": "v2_validator_replay",
        "claim": "Every selected emitted v2 decision artifact revalidates through scripts/validate_risk_contract.py.",
        "evidence": "validator replay payload has risk_contract_validator_result.v2 and valid=true.",
        "status": "required",
    },
    {
        "id": "scan_slot_runtime_application",
        "claim": "The cap decision applies scan-slot runtime sizing only.",
        "evidence": "cap slot and artifact show unit_scan_slot, runtime_sizing_applied=true, and metrics_rescaled=false.",
        "status": "required",
    },
    {
        "id": "statistical_sidecar_split",
        "claim": "WFD/statistical output remains full-size / ungated-basis evidence and is not embedded as applied runtime sizing.",
        "evidence": "sidecar records metrics_rescaled=false and compares cap/size statistical fields.",
        "status": "required",
    },
    {
        "id": "stop_before_metrics",
        "claim": "The reject decision remains stopped before fee/WFD statistical work.",
        "evidence": "reject slot has empty fee_curve and null metric fields.",
        "status": "required",
    },
    {
        "id": "fresh_namespaced_output",
        "claim": "v2 replay output is isolated from protected historical/report/contract roots.",
        "evidence": "report_dir and artifact roots are under reports/risk-contract-v2 by default.",
        "status": "required",
    },
)

STATISTICAL_FIELDS = (
    "fee_curve",
    "pf_gross",
    "pf_net@2bps_rt",
    "alpha_cliff",
    "verdict",
    "verdicts_per_fee",
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
        "reason": "fresh risk_contract.v2 scan replay outputs only; protected historical/report/contract roots are not write targets",
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
        "id": f"phase-v2-scan-evidence.{decision_class}",
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
        "version": "risk-policy.v1.v2-scan-evidence-test",
        "owner": "side-risk-contract-v2-scan-runtime-adoption",
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


def single_edge_fixture() -> list[dict[str, Any]]:
    return [
        {
            "entry_minute": 0,
            "direction": "long",
            "hold_h_candidates": [3],
            "t_stat": 4.52,
            "bh_q": 0.018,
            "dsr_p": None,
            "source_query": "scan_v2_runtime_adoption_evidence.py",
            "asset": "USDJPY",
            "timeframe": "1h",
        }
    ]


def side_cli_scan_command(policy_path: Path, artifact_root: Path, edges_path: Path, output_path: Path) -> list[str]:
    return [
        "cargo",
        "run",
        "-p",
        "side-cli",
        "--bin",
        "side",
        "--",
        "scan",
        "--asset",
        "USDJPY",
        "--timeframe",
        "1h",
        "--fixture-parquet",
        "rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet",
        "--edges",
        display_path(edges_path),
        "--spread-bps-rt",
        "1.5",
        "--commission-bps-rt",
        "0.5",
        "--risk-gate-policy",
        display_path(policy_path),
        "--risk-gate-artifact-root",
        display_path(artifact_root),
        "--risk-gate-contract-version",
        "v2",
        "--output",
        display_path(output_path),
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


def first_slot(slots: list[Any]) -> dict[str, Any]:
    if not slots:
        raise ValueError("scan replay output must contain at least one slot")
    slot = slots[0]
    if not isinstance(slot, dict):
        raise ValueError("scan replay slot must be an object")
    return slot


def run_scan_v2_replay(run_name: str, decision_class: str, report_dir: Path) -> dict[str, Any]:
    assert_allowed_report_dir(report_dir)
    report_root = repo_path(report_dir)
    run_dir = report_root / "runs" / run_name
    prepare_run_dir(run_dir)

    policy_path = run_dir / "policy.json"
    edges_path = run_dir / "edges.json"
    artifact_root = run_dir / "risk_artifacts"
    output_path = run_dir / "scan.json"
    raw_stdout_path = run_dir / "stdout.txt"
    write_json(policy_for(decision_class), policy_path)
    write_json(single_edge_fixture(), edges_path)

    command = side_cli_scan_command(policy_path, artifact_root, edges_path, output_path)
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
    raw_stdout_path.write_text(result.stdout, encoding="utf-8")

    slots: list[Any] = []
    candidate: dict[str, Any] = {}
    artifact: dict[str, Any] = {}
    validator_payload: dict[str, Any] = {}
    candidate_id: str | None = None
    candidate_path: Path | None = None
    artifact_path: Path | None = None

    if result.returncode == 0:
        loaded_slots = load_json(output_path)
        if not isinstance(loaded_slots, list):
            raise ValueError(f"{run_name} scan output top-level JSON must be an array")
        slots = loaded_slots
        slot = first_slot(slots)
        risk_gate = slot["risk_gate"]
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
        "output_path": display_path(output_path),
        "raw_stdout_path": display_path(raw_stdout_path),
        "stderr": result.stderr,
        "policy_path": display_path(policy_path),
        "edges_path": display_path(edges_path),
        "artifact_root": display_path(artifact_root),
        "candidate_id": candidate_id,
        "candidate_path": display_path(candidate_path) if candidate_path else None,
        "artifact_path": display_path(artifact_path) if artifact_path else None,
        "slots": slots,
        "candidate": candidate,
        "artifact": artifact,
        "validator_payload": validator_payload,
    }
    row["passed"] = replay_row_passed(row)
    return row


def run_scan_v2_replays(report_dir: Path) -> list[dict[str, Any]]:
    assert_allowed_report_dir(report_dir)
    return [
        run_scan_v2_replay(spec["run_name"], spec["decision_class"], report_dir)
        for spec in RUN_SPECS
    ]


def replay_row_passed(row: dict[str, Any]) -> bool:
    slot = first_slot(row.get("slots", [])) if row.get("slots") else {}
    risk_gate = slot.get("risk_gate", {})
    return (
        row.get("return_code") == 0
        and risk_gate.get("contract_version") == "v2"
        and row.get("candidate", {}).get("candidate_schema_version") == "risk_contract.v2.candidate.v1"
        and row.get("candidate", {}).get("surface", {}).get("runtime_surface") == "scan"
        and row.get("candidate", {}).get("sizing", {}).get("requested_size_basis") == "unit_scan_slot"
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
        risk_gate = first_slot(row["slots"])["risk_gate"]
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
    slot = first_slot(cap["slots"])
    risk_gate = slot["risk_gate"]
    application = cap["artifact"]["application"]
    candidate = cap["candidate"]
    effective_size_equals_allowed_size = (
        risk_gate.get("effective_size") == risk_gate.get("allowed_size") == application.get("effective_size")
    )
    fee_curve_present = bool(slot.get("fee_curve"))
    passed = all(
        [
            cap.get("passed") is True,
            risk_gate.get("application_status") == "applied",
            risk_gate.get("runtime_sizing_applied") is True,
            risk_gate.get("requested_size_basis") == "unit_scan_slot",
            candidate.get("sizing", {}).get("requested_size_basis") == "unit_scan_slot",
            application.get("application_status") == "applied",
            application.get("runtime_sizing_applied") is True,
            application.get("metrics_rescaled") is False,
            effective_size_equals_allowed_size,
            fee_curve_present,
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
        "fee_curve_preserved_as_statistical_output": fee_curve_present,
    }


def size_continue_replay_check(replay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    size = rows_by_name(replay_rows)["size"]
    slot = first_slot(size["slots"])
    risk_gate = slot["risk_gate"]
    application = size["artifact"]["application"]
    runtime_sizing_applied = application.get("runtime_sizing_applied")
    fee_curve_present = bool(slot.get("fee_curve"))
    passed = all(
        [
            size.get("passed") is True,
            risk_gate.get("decision_class") == "size",
            risk_gate.get("execution_state") == "continued",
            "runtime_sizing_applied" not in risk_gate,
            application.get("application_status") == "not_applicable",
            runtime_sizing_applied is False,
            fee_curve_present,
        ]
    )
    return {
        "passed": passed,
        "source": size.get("artifact_path"),
        "execution_state": risk_gate.get("execution_state"),
        "application_status": application.get("application_status"),
        "runtime_sizing_applied": runtime_sizing_applied,
        "fee_curve_present": fee_curve_present,
    }


def reject_stop_replay_check(replay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    reject = rows_by_name(replay_rows)["reject"]
    slot = first_slot(reject["slots"])
    risk_gate = slot["risk_gate"]
    application = reject["artifact"]["application"]
    fee_curve_empty = slot.get("fee_curve") == []
    metrics_are_null = all(slot.get(key) is None for key in ["pf_gross", "pf_net@2bps_rt", "alpha_cliff", "verdict", "verdicts_per_fee"])
    passed = all(
        [
            reject.get("passed") is True,
            risk_gate.get("decision_class") == "reject",
            risk_gate.get("execution_state") == "stopped",
            application.get("execution_state") == "stopped",
            fee_curve_empty,
            metrics_are_null,
        ]
    )
    return {
        "passed": passed,
        "source": reject.get("artifact_path"),
        "execution_state": risk_gate.get("execution_state"),
        "fee_curve_empty": fee_curve_empty,
        "metrics_are_null": metrics_are_null,
        "application_status": application.get("application_status"),
    }


def build_statistical_sidecar(replay_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = rows_by_name(replay_rows)
    cap = rows["cap"]
    size = rows["size"]
    reject = rows["reject"]
    cap_slot = first_slot(cap["slots"])
    size_slot = first_slot(size["slots"])
    reject_slot = first_slot(reject["slots"])
    statistical_fields_equal = {
        field: cap_slot.get(field) == size_slot.get(field)
        for field in STATISTICAL_FIELDS
    }
    all_metrics_rescaled_false = all(
        row.get("artifact", {}).get("application", {}).get("metrics_rescaled") is False
        for row in replay_rows
    )
    reject_stopped_before_statistics = reject_slot.get("fee_curve") == [] and all(
        reject_slot.get(key) is None
        for key in ["pf_gross", "pf_net@2bps_rt", "alpha_cliff", "verdict", "verdicts_per_fee"]
    )
    passed = (
        all(statistical_fields_equal.values())
        and all_metrics_rescaled_false
        and reject_stopped_before_statistics
    )
    return {
        "passed": passed,
        "runtime_contract_scope": "scan_slot_runtime_only",
        "statistical_basis": "full_size_ungated_basis",
        "metrics_rescaled": False,
        "wfd_statistical_metrics_embedded_in_runtime_contract": False,
        "cap_vs_size_statistical_fields_equal": statistical_fields_equal,
        "reject_stopped_before_statistics": reject_stopped_before_statistics,
        "runtime_artifacts": [
            {
                "run_name": row["run_name"],
                "candidate_id": row.get("candidate_id"),
                "artifact_path": row.get("artifact_path"),
                "validator_valid": row.get("validator_payload", {}).get("valid"),
            }
            for row in replay_rows
        ],
        "source_note": "cap and size use the same one-slot scan fixture; cap applies only downstream scan-slot runtime size while statistical fields match the full-size size run",
    }


def statistical_sidecar_check(sidecar: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": sidecar["passed"],
        "source": "statistical_sidecar",
        "metrics_rescaled": sidecar["metrics_rescaled"],
        "statistical_basis": sidecar["statistical_basis"],
        "runtime_contract_scope": sidecar["runtime_contract_scope"],
        "reject_stopped_before_statistics": sidecar["reject_stopped_before_statistics"],
    }


def compact_run(row: dict[str, Any]) -> dict[str, Any]:
    slot = first_slot(row.get("slots", [])) if row.get("slots") else {}
    risk_gate = slot.get("risk_gate", {})
    return {
        "run_name": row.get("run_name"),
        "decision_class": row.get("decision_class"),
        "command_vector": row.get("command_vector"),
        "return_code": row.get("return_code"),
        "output_path": row.get("output_path"),
        "raw_stdout_path": row.get("raw_stdout_path"),
        "stderr": row.get("stderr"),
        "policy_path": row.get("policy_path"),
        "edges_path": row.get("edges_path"),
        "artifact_root": row.get("artifact_root"),
        "candidate_id": row.get("candidate_id"),
        "candidate_path": row.get("candidate_path"),
        "artifact_path": row.get("artifact_path"),
        "execution_state": risk_gate.get("execution_state"),
        "contract_version": risk_gate.get("contract_version"),
        "validator_result_schema_version": risk_gate.get("validator_result_schema_version"),
        "slot_count": len(row.get("slots", [])),
        "validator_valid": row.get("validator_payload", {}).get("valid"),
        "passed": row.get("passed"),
    }


def build_scan_v2_runtime_adoption_evidence(
    *,
    report_dir: Path = DEFAULT_REPORT_DIR,
    diff_base: str = "origin/master",
    replay_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    protected_surface_guard = build_protected_surface_guard(report_dir)
    source_evidence = build_source_evidence()
    rows = replay_rows if replay_rows is not None else run_scan_v2_replays(report_dir)
    statistical_sidecar = build_statistical_sidecar(rows)
    checks = {
        "source_evidence_loaded": source_evidence_loaded_check(source_evidence),
        "v2_version_proof": v2_version_proof_check(rows),
        "validator_replay": validator_replay_check(rows),
        "cap_runtime_application": cap_runtime_application_check(rows),
        "size_continue_replay": size_continue_replay_check(rows),
        "reject_stop_replay": reject_stop_replay_check(rows),
        "statistical_sidecar": statistical_sidecar_check(statistical_sidecar),
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
            "implementation_scope": "scan_v2_evidence_replay_only",
        },
        "source_evidence": source_evidence,
        "replay_contract": list(REPLAY_CONTRACT),
        "runs": {row["run_name"]: compact_run(row) for row in rows},
        "checks": checks,
        "statistical_sidecar": statistical_sidecar,
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
        "# risk_contract.v2 Scan Runtime Adoption Evidence",
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
            "## Statistical Sidecar",
            "",
            f"- Runtime contract scope: `{markdown_cell(report['statistical_sidecar']['runtime_contract_scope'])}`",
            f"- Statistical basis: `{markdown_cell(report['statistical_sidecar']['statistical_basis'])}`",
            f"- Metrics rescaled: `{markdown_cell(report['statistical_sidecar']['metrics_rescaled'])}`",
            f"- Sidecar passed: `{markdown_cell(report['statistical_sidecar']['passed'])}`",
            "",
            "| Run | Candidate | Artifact | Validator valid |",
            "|---|---|---|---:|",
        ]
    )
    for row in report["statistical_sidecar"]["runtime_artifacts"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(row["run_name"]),
                    markdown_cell(row["candidate_id"]),
                    markdown_cell(row["artifact_path"]),
                    markdown_cell(row["validator_valid"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Run Manifest",
            "",
            "| Run | Decision | Return | Execution | Contract | Validator | Slot count | Artifact | Passed |",
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
                    markdown_cell(run["slot_count"]),
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
        report = build_scan_v2_runtime_adoption_evidence(
            report_dir=args.report_dir,
            diff_base=args.diff_base,
        )
        write_reports(report, args.report_dir)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(
            f"generate_scan_v2_runtime_adoption_evidence.py: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0 if report["summary"]["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
