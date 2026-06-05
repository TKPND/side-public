"""Contract tests for Phase 148 backtest runtime sizing evidence generation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import generate_backtest_risk_gate_closure_evidence as closure


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_backtest_risk_gate_closure_evidence.py"
REPORT_JSON = ROOT / "reports" / "v5.11" / "backtest_risk_gate_closure_evidence.json"
REPORT_MD = ROOT / "reports" / "v5.11" / "backtest_risk_gate_closure_evidence.md"
EVIDENCE_SCHEMA_VERSION = "backtest_risk_gate_closure_evidence.v1"
RESULT_SCHEMA_VERSION = "risk_contract_validator_result.v1"
EXPECTED_RUNS = {"ungated", "size", "cap", "reject", "kill", "block"}
EXPECTED_CHECKS = {
    "backtest_replay",
    "runtime_sizing_effect",
    "artifact_validation",
    "infra_failures",
    "v57_integrity",
    "scope_guard",
}


def build_clean_report(tmp_path: Path) -> dict:
    return closure.build_backtest_risk_gate_closure_evidence(
        report_dir=tmp_path,
        diff_base="HEAD",
        committed_changed_paths=[],
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
    )


def report_path(path: str) -> Path:
    resolved = Path(path)
    return resolved if resolved.is_absolute() else ROOT / resolved


def load_report_path(path: str) -> dict:
    return json.loads(report_path(path).read_text(encoding="utf-8"))


def run_stdout(report: dict, run_name: str) -> dict:
    return load_report_path(report["runs"][run_name]["stdout_path"])


def markdown_section(markdown: str, heading: str) -> str:
    start = markdown.index(f"## {heading}")
    next_heading = markdown.find("\n## ", start + 1)
    if next_heading == -1:
        return markdown[start:]
    return markdown[start:next_heading]


def load_committed_report() -> dict:
    return json.loads(REPORT_JSON.read_text(encoding="utf-8"))


def hash_report_path(path: str) -> str:
    return closure.sha256_file(report_path(path))


def test_phase148_builds_top_level_runtime_sizing_contract(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)

    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["phase"] == 148
    assert report["requirements_addressed"] == [
        "CAPEVID-01",
        "CAPEVID-02",
        "CAPEVID-03",
    ]
    assert set(report["checks"]) == EXPECTED_CHECKS
    assert report["summary"]["overall_status"] == "PASS"
    assert report["summary"]["checks_failed"] == 0
    assert set(report["runs"]) == EXPECTED_RUNS
    for run_name, run in report["runs"].items():
        assert run["run_name"] == run_name
        assert report_path(run["stdout_path"]).exists()
        assert run["stdout_sha256"]


def test_run_manifest_lists_every_run_with_audit_fields(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)
    manifest = report["run_manifest"]

    assert [row["run_name"] for row in manifest] == list(closure.RUN_NAMES)
    assert len(manifest) == len(closure.RUN_NAMES)

    expected_fields = {
        "run_name",
        "risk_gate_enabled",
        "decision_class",
        "execution_state",
        "run_status",
        "backtest_invocation_count",
        "candidate_id",
        "policy_path",
        "artifact_root",
        "candidate_artifact_path",
        "candidate_artifact_sha256",
        "decision_artifact_path",
        "decision_artifact_sha256",
        "stdout_path",
        "stdout_sha256",
        "validator_valid",
        "passed",
    }
    for row in manifest:
        assert set(row) == expected_fields

    rows = {row["run_name"]: row for row in manifest}
    ungated = rows["ungated"]
    assert ungated["risk_gate_enabled"] is False
    assert ungated["decision_class"] is None
    assert ungated["execution_state"] is None
    assert ungated["run_status"] == "completed"
    assert ungated["backtest_invocation_count"] == 1
    assert ungated["candidate_id"] is None
    assert ungated["policy_path"] is None
    assert ungated["artifact_root"] is None
    assert ungated["candidate_artifact_path"] is None
    assert ungated["candidate_artifact_sha256"] is None
    assert ungated["decision_artifact_path"] is None
    assert ungated["decision_artifact_sha256"] is None
    assert ungated["stdout_path"] == report["runs"]["ungated"]["stdout_path"]
    assert ungated["stdout_sha256"] == report["runs"]["ungated"]["stdout_sha256"]
    assert ungated["validator_valid"] is None
    assert ungated["passed"] is True

    for run_name in closure.GATED_RUN_NAMES:
        row = rows[run_name]
        stdout = run_stdout(report, run_name)
        assert row["risk_gate_enabled"] is True
        assert row["decision_class"] == run_name
        assert row["execution_state"] == stdout["risk_gate"]["execution_state"]
        assert row["run_status"] == stdout["run_status"]
        assert row["backtest_invocation_count"] == stdout["backtest_execution"][
            "backtest_invocation_count"
        ]
        assert row["candidate_id"] == report["runs"][run_name]["candidate_id"]
        assert row["policy_path"] == report["runs"][run_name]["policy_path"]
        assert row["artifact_root"] == report["runs"][run_name]["artifact_root"]
        assert row["decision_artifact_path"] == report["runs"][run_name]["artifact_path"]
        assert row["decision_artifact_sha256"] == report["runs"][run_name][
            "artifact_sha256"
        ]
        assert row["stdout_path"] == report["runs"][run_name]["stdout_path"]
        assert row["stdout_sha256"] == report["runs"][run_name]["stdout_sha256"]
        assert row["validator_valid"] is True
        assert row["passed"] is True


def test_run_manifest_gated_artifacts_match_existing_outputs(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)
    rows = {row["run_name"]: row for row in report["run_manifest"]}
    validation_by_run = {
        row["run_name"]: row
        for row in report["checks"]["artifact_validation"]["artifacts"]
    }

    for run_name in closure.GATED_RUN_NAMES:
        row = rows[run_name]
        run = report["runs"][run_name]
        stdout = run_stdout(report, run_name)
        candidate = load_report_path(row["candidate_artifact_path"])
        decision = load_report_path(row["decision_artifact_path"])
        validation = validation_by_run[run_name]

        assert report_path(row["candidate_artifact_path"]).exists()
        assert hash_report_path(row["candidate_artifact_path"]) == row[
            "candidate_artifact_sha256"
        ]
        assert row["decision_artifact_path"] == run["artifact_path"]
        assert row["decision_artifact_sha256"] == run["artifact_sha256"]
        assert hash_report_path(row["decision_artifact_path"]) == row[
            "decision_artifact_sha256"
        ]
        assert hash_report_path(row["stdout_path"]) == row["stdout_sha256"]
        assert row["candidate_id"] == stdout["risk_gate"]["candidate_id"]
        assert row["candidate_id"] == candidate["candidate_id"]
        assert row["candidate_id"] == decision["candidate"]["candidate_id"]
        assert row["validator_valid"] == validation["validator_payload"]["valid"]


def test_backtest_replay_semantics_cover_all_runs(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)
    replay = report["checks"]["backtest_replay"]

    assert replay["passed"] is True
    assert {row["run_name"] for row in replay["runs"]} == EXPECTED_RUNS

    ungated = run_stdout(report, "ungated")
    assert ungated["risk_gate_enabled"] is False
    assert ungated["run_status"] == "completed"
    assert set(ungated["metrics"]) == {"profit_factor", "num_trades", "total_return"}
    assert ungated["cap_parity"]["status"] == "not_applicable"
    assert ungated["backtest_execution"]["status"] == "run"
    assert ungated["backtest_execution"]["backtest_invocation_count"] == 1

    for run_name in ("block", "kill", "reject"):
        stdout = run_stdout(report, run_name)
        assert stdout["risk_gate_enabled"] is True
        assert stdout["run_status"] == "stopped"
        assert stdout["metrics"] is None
        assert stdout["risk_gate"]["decision_class"] == run_name
        assert stdout["risk_gate"]["execution_state"] == "stopped"
        assert stdout["backtest_execution"]["status"] == "not_run"
        assert stdout["backtest_execution"]["reason"] == "risk_gate_stop"
        assert stdout["backtest_execution"]["backtest_invocation_count"] == 0

    for run_name in ("size", "cap"):
        stdout = run_stdout(report, run_name)
        assert stdout["risk_gate_enabled"] is True
        assert stdout["run_status"] == "completed"
        assert set(stdout["metrics"]) == {"profit_factor", "num_trades", "total_return"}
        assert stdout["risk_gate"]["decision_class"] == run_name
        assert stdout["risk_gate"]["execution_state"] == "continued"
        assert stdout["backtest_execution"]["status"] == "run"
        assert stdout["backtest_execution"]["backtest_invocation_count"] == 1

    cap = run_stdout(report, "cap")
    assert cap["risk_gate"]["application_status"] == "applied"
    assert cap["risk_gate"]["runtime_sizing_applied"] is True
    assert cap["risk_gate"]["sizing_effect"] == "reduced"
    assert cap["risk_gate"]["requested_size"] == 1.0
    assert cap["risk_gate"]["requested_size_basis"] == "unit_backtest_run"
    assert cap["risk_gate"]["effective_size"] == cap["risk_gate"]["allowed_size"]
    assert cap["cap_parity"]["status"] == "not_applicable"


def test_gated_replay_semantics_require_risk_gate_enabled(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)
    cap_stdout = run_stdout(report, "cap")
    cap_stdout["risk_gate_enabled"] = False
    cap_validator = next(
        row["validator_payload"]
        for row in report["checks"]["artifact_validation"]["artifacts"]
        if row["run_name"] == "cap"
    )

    assertions = closure.semantic_assertions_for(
        "cap",
        0,
        cap_stdout,
        cap_validator,
    )

    assert assertions["risk_gate_enabled"] is False
    assert not all(assertions.values())


def test_runtime_sizing_effect_hash_differs_from_ungated_and_fails_on_no_effect(
    tmp_path: Path,
) -> None:
    report = build_clean_report(tmp_path)
    ungated_stdout = run_stdout(report, "ungated")
    cap_stdout = run_stdout(report, "cap")

    ungated_hash = closure.canonical_metric_hash(ungated_stdout)
    cap_hash = closure.canonical_metric_hash(cap_stdout)

    assert ungated_hash["payload"] == {
        key: ungated_stdout["metrics"][key]
        for key in sorted(closure.PARITY_METRIC_KEYS)
    }
    assert ungated_hash["canonical_json"] == json.dumps(
        ungated_hash["payload"],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert cap_hash["sha256"] != ungated_hash["sha256"]
    assert report["checks"]["runtime_sizing_effect"]["ungated_metrics_hash"] == ungated_hash
    assert report["checks"]["runtime_sizing_effect"]["cap_metrics_hash"] == cap_hash
    assert cap_stdout["cap_parity"]["ungated_metrics_hash"] == ungated_hash
    assert cap_stdout["cap_parity"]["cap_metrics_hash"] == cap_hash

    no_effect_cap = json.loads(json.dumps(cap_stdout))
    no_effect_cap["metrics"] = json.loads(json.dumps(ungated_stdout["metrics"]))
    no_effect = closure.build_backtest_risk_gate_closure_evidence(
        report_dir=tmp_path / "no-effect",
        diff_base="HEAD",
        committed_changed_paths=[],
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
        stdout_overrides={"cap": no_effect_cap},
        write_reports=False,
    )
    assert no_effect["summary"]["overall_status"] == "FAIL"
    assert no_effect["checks"]["runtime_sizing_effect"]["passed"] is False
    assert not (tmp_path / "no-effect" / closure.JSON_REPORT_NAME).exists()
    assert not (tmp_path / "no-effect" / closure.MD_REPORT_NAME).exists()

    malformed_cap = json.loads(json.dumps(cap_stdout))
    del malformed_cap["metrics"]["profit_factor"]
    malformed = closure.build_backtest_risk_gate_closure_evidence(
        report_dir=tmp_path / "malformed",
        diff_base="HEAD",
        committed_changed_paths=[],
        changed_paths=[],
        staged_changed_paths=[],
        untracked_paths=[],
        stdout_overrides={"cap": malformed_cap},
        write_reports=False,
    )
    assert malformed["summary"]["overall_status"] == "FAIL"
    assert malformed["checks"]["runtime_sizing_effect"]["passed"] is False
    assert "cap" in malformed["checks"]["runtime_sizing_effect"]["errors"]
    assert "profit_factor" in malformed["checks"]["runtime_sizing_effect"]["errors"]["cap"]


def test_artifact_validation_lists_every_backtest_run_artifact(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)
    artifacts = report["checks"]["artifact_validation"]["artifacts"]

    assert report["checks"]["artifact_validation"]["passed"] is True
    assert {row["run_name"] for row in artifacts} == set(closure.GATED_RUN_NAMES)
    for row in artifacts:
        assert row["decision_class"] == row["run_name"]
        assert report_path(row["artifact_path"]).exists()
        assert row["validator_payload"]["schema_version"] == RESULT_SCHEMA_VERSION
        assert row["validator_payload"]["valid"] is True
        assert row["validator_payload"]["errors"] == []


def test_candidate_identity_aligns_stdout_artifact_and_manifest(
    tmp_path: Path,
) -> None:
    report = build_clean_report(tmp_path)

    for run_name in closure.GATED_RUN_NAMES:
        run = report["runs"][run_name]
        stdout = run_stdout(report, run_name)
        artifact = load_report_path(run["artifact_path"])
        assert run["candidate_id"] == stdout["risk_gate"]["candidate_id"]
        assert run["candidate_id"] == artifact["candidate"]["candidate_id"]


def test_v57_integrity_pins_top_level_reports(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)
    integrity = report["checks"]["v57_integrity"]

    assert integrity["passed"] is True
    assert closure.V57_EXPECTED_SHA256 == {
        "reports/v5.7/risk_gate_closure_evidence.json": "a4d6b0f526e04db7001b95fd37495e1c63600fe6ea03a4471602c6c1f820cb0d",
        "reports/v5.7/risk_gate_closure_evidence.md": "365598b429211036c5db91a78bfedf9fe49e55001375852c81ae895d8c786084",
    }
    assert {
        row["path"]: row["sha256"]
        for row in integrity["top_level_reports"]
    } == closure.V57_EXPECTED_SHA256
    assert integrity["tracked_report_file_count"] == 28
    assert integrity["risk_artifact_count"] == 15


def test_scope_guard_blocks_forbidden_phase148_paths(tmp_path: Path) -> None:
    report = closure.build_backtest_risk_gate_closure_evidence(
        report_dir=tmp_path,
        diff_base="phase-base",
        committed_changed_paths=[
            ".planning/REQUIREMENTS.md",
            ".planning/ROADMAP.md",
            ".planning/phases/140-cap-parity-and-closure-evidence/140-03-SUMMARY.md",
            "scripts/generate_backtest_risk_gate_closure_evidence.py",
            "tests/test_generate_backtest_risk_gate_closure_evidence.py",
            "reports/v5.11/backtest_risk_gate_closure_evidence.json",
        ],
        changed_paths=[
            "rust/side-engine/src/backtest.rs",
            "risk/engine.py",
            "paper_trading/new_guard.py",
        ],
        staged_changed_paths=[
            "docs/reports/v4.13/archive.md",
            "reports/v5.7/risk_gate_closure_evidence.json",
        ],
        untracked_paths=["unexpected.txt"],
    )

    scope = report["checks"]["scope_guard"]

    assert scope["passed"] is False
    assert "rust/side-engine/src/backtest.rs" in scope["forbidden_runtime_paths"]
    assert "risk/engine.py" in scope["forbidden_runtime_paths"]
    assert "paper_trading/new_guard.py" in scope["forbidden_runtime_paths"]
    assert "docs/reports/v4.13/archive.md" in scope["forbidden_v4_archive_paths"]
    assert "reports/v5.7/risk_gate_closure_evidence.json" in scope[
        "forbidden_v57_paths"
    ]
    assert "unexpected.txt" in scope["unexpected_paths"]

    unexpected_report = closure.collect_scope_guard(
        diff_base="phase-base",
        committed_changed_paths=[],
        changed_paths=["reports/v5.11/unrelated_alpha_discovery.json"],
        staged_changed_paths=[],
        untracked_paths=[],
    )
    assert unexpected_report["passed"] is False
    assert "reports/v5.11/unrelated_alpha_discovery.json" in unexpected_report[
        "unexpected_paths"
    ]


def test_scope_guard_path_summary_formats_empty_short_and_long_lists() -> None:
    assert closure.format_scope_guard_path_summary([]) == "0 paths"
    assert closure.format_scope_guard_path_summary(["risk/engine.py"]) == (
        "1 path: `risk/engine.py`"
    )
    assert closure.format_scope_guard_path_summary(
        [
            ".planning/REQUIREMENTS.md",
            ".planning/ROADMAP.md",
            ".planning/STATE.md",
            "scripts/generate_backtest_risk_gate_closure_evidence.py",
        ]
    ) == (
        "4 paths (first 3): `.planning/REQUIREMENTS.md`, "
        "`.planning/ROADMAP.md`, `.planning/STATE.md`"
    )


def test_markdown_summarizes_scope_guard_path_lists(tmp_path: Path) -> None:
    report = closure.build_backtest_risk_gate_closure_evidence(
        report_dir=tmp_path,
        diff_base="phase-base",
        committed_changed_paths=[
            ".planning/REQUIREMENTS.md",
            ".planning/ROADMAP.md",
            ".planning/STATE.md",
            "scripts/generate_backtest_risk_gate_closure_evidence.py",
        ],
        changed_paths=["risk/engine.py"],
        staged_changed_paths=[
            "docs/reports/v4.13/archive.md",
            "reports/v5.7/risk_gate_closure_evidence.json",
        ],
        untracked_paths=["unexpected.txt"],
    )
    markdown = closure.render_markdown(report)
    scope = markdown_section(markdown, "Scope Guard")

    assert "| Category | Summary |" in scope
    assert (
        "| Allowed Phase 148 paths | 4 paths (first 3): "
        "`.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/STATE.md` |"
    ) in scope
    assert "| Forbidden runtime paths | 1 path: `risk/engine.py` |" in scope
    assert (
        "| Forbidden v4 archive paths | 1 path: `docs/reports/v4.13/archive.md` |"
    ) in scope
    assert (
        "| Forbidden v5.7 paths | "
        "1 path: `reports/v5.7/risk_gate_closure_evidence.json` |"
    ) in scope
    assert "| Unexpected paths | 1 path: `unexpected.txt` |" in scope
    assert "scripts/generate_backtest_risk_gate_closure_evidence.py" not in scope
    assert "['.planning/REQUIREMENTS.md'" not in scope


def test_markdown_renders_backtest_sections_in_run_order_after_json_roundtrip(
    tmp_path: Path,
) -> None:
    report = build_clean_report(tmp_path)
    roundtripped = json.loads(json.dumps(report, sort_keys=True))
    markdown = closure.render_markdown(roundtripped)

    replay = markdown_section(markdown, "Backtest Replay")
    replay_rows = [line.split("`")[1] for line in replay.splitlines() if line.startswith("| `")]
    assert replay_rows == list(closure.RUN_NAMES)

    commands = markdown_section(markdown, "Replay Commands")
    command_rows = [
        line.split("`")[1] for line in commands.splitlines() if line.startswith("- `")
    ]
    assert command_rows == list(closure.RUN_NAMES)


def test_markdown_command_string_relativizes_repo_absolute_args() -> None:
    repo_policy = ROOT / "reports/v5.11/backtest_risk_gate_closure/cap/policy.json"
    outside_path = Path("/tmp/outside-policy.json")

    assert closure.markdown_command_string(
        [
            "side",
            "--risk-gate-policy",
            str(repo_policy),
            "--risk-gate-artifact-root",
            "reports/v5.11/backtest_risk_gate_closure/cap/risk_artifacts",
            "--external",
            str(outside_path),
        ]
    ) == (
        "side --risk-gate-policy "
        "reports/v5.11/backtest_risk_gate_closure/cap/policy.json "
        "--risk-gate-artifact-root "
        "reports/v5.11/backtest_risk_gate_closure/cap/risk_artifacts "
        "--external /tmp/outside-policy.json"
    )


def test_markdown_replay_commands_use_repo_relative_policy_paths(
    tmp_path: Path,
) -> None:
    report = build_clean_report(tmp_path)
    cap = report["runs"]["cap"]
    policy_arg_index = cap["command_vector"].index("--risk-gate-policy") + 1
    cap["command_vector"][policy_arg_index] = str(
        ROOT / "reports/v5.11/backtest_risk_gate_closure/cap/policy.json"
    )

    markdown = closure.render_markdown(report)
    replay_commands = markdown_section(markdown, "Replay Commands")

    assert str(ROOT) not in replay_commands
    assert (
        "--risk-gate-policy "
        "reports/v5.11/backtest_risk_gate_closure/cap/policy.json"
    ) in replay_commands


def test_infra_failures_are_nonzero_and_stderr_only(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)
    infra = report["checks"]["infra_failures"]

    assert infra["passed"] is True
    assert {case["name"] for case in infra["cases"]} >= {
        "invalid policy JSON",
        "non-object policy JSON",
        "unsafe artifact root",
        "malformed candidate or validator failure",
    }
    for case in infra["cases"]:
        assert case["exit_code"] != 0
        assert case["stdout"] == ""
        assert '"run_status":"stopped"' not in case["stderr"]


def test_markdown_renders_run_manifest_section(tmp_path: Path) -> None:
    report = build_clean_report(tmp_path)
    markdown = closure.render_markdown(report)

    assert "## Run Manifest" in markdown
    assert "| Run | Gate | Decision | Execution | Invocations | Candidate | Candidate artifact | Decision artifact | Stdout | Validator valid | Passed |" in markdown
    for run_name in closure.RUN_NAMES:
        assert f"| `{run_name}` |" in markdown

    cap_row = next(row for row in report["run_manifest"] if row["run_name"] == "cap")
    assert cap_row["candidate_artifact_path"] in markdown
    assert cap_row["decision_artifact_path"] in markdown
    assert cap_row["stdout_path"] in markdown

    ungated_row_prefix = "| `ungated` | False | `None` | `None` | 1 | `None` | `None` | `None` |"
    assert ungated_row_prefix in markdown


def test_cli_refuses_committed_report_without_explicit_diff_base(
    monkeypatch, capsys
) -> None:
    called = False

    def fail_if_called(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("generator should fail before building committed evidence")

    monkeypatch.setattr(
        closure,
        "build_backtest_risk_gate_closure_evidence",
        fail_if_called,
    )

    return_code = closure.main([])
    captured = capsys.readouterr()

    assert return_code == 2
    assert called is False
    assert "--diff-base is required when writing committed closure evidence" in captured.err


def test_cli_writes_v511_json_markdown_and_artifacts(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--report-dir",
            str(tmp_path),
            "--diff-base",
            "HEAD",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    json_report = tmp_path / "backtest_risk_gate_closure_evidence.json"
    md_report = tmp_path / "backtest_risk_gate_closure_evidence.md"
    assert json_report.exists()
    assert md_report.exists()

    report = json.loads(json_report.read_text(encoding="utf-8"))
    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert "## Audit Summary" in md_report.read_text(encoding="utf-8")
    for run_name, run in report["runs"].items():
        assert run_name in EXPECTED_RUNS
        assert report_path(run["stdout_path"]).exists()
    for row in report["checks"]["artifact_validation"]["artifacts"]:
        assert report_path(row["artifact_path"]).exists()
        assert row["validator_payload"]["valid"] is True


def test_committed_v511_reports_and_artifacts_are_replayable() -> None:
    assert REPORT_JSON.exists(), "committed Phase 148 JSON report is required"
    assert REPORT_MD.exists(), "committed Phase 148 Markdown report is required"

    report = load_committed_report()
    markdown = REPORT_MD.read_text(encoding="utf-8")

    assert report["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert report["phase"] == 148
    assert set(report["runs"]) == EXPECTED_RUNS
    assert set(report["checks"]) == EXPECTED_CHECKS
    assert report["summary"]["overall_status"] == "PASS"
    assert [row["run_name"] for row in report["run_manifest"]] == list(
        closure.RUN_NAMES
    )
    assert "## Audit Summary" in markdown
    assert "## Run Manifest" in markdown
    committed_scope = markdown_section(markdown, "Scope Guard")
    assert "| Category | Summary |" in committed_scope
    assert "Allowed Phase 148 paths | " in committed_scope
    assert "['.planning/REQUIREMENTS.md'" not in committed_scope
    replay_commands = markdown_section(markdown, "Replay Commands")
    assert str(ROOT) not in replay_commands
    assert (
        "--risk-gate-policy "
        "reports/v5.11/backtest_risk_gate_closure/cap/policy.json"
    ) in replay_commands
    for run_name in EXPECTED_RUNS:
        assert f"| `{run_name}` |" in markdown
    runtime_sizing_effect = report["checks"]["runtime_sizing_effect"]
    for run_name, run in report["runs"].items():
        manifest_row = next(
            row for row in report["run_manifest"] if row["run_name"] == run_name
        )
        assert manifest_row["stdout_path"] == run["stdout_path"]
        assert manifest_row["stdout_sha256"] == run["stdout_sha256"]
        assert manifest_row["decision_artifact_path"] == run["artifact_path"]
        assert manifest_row["decision_artifact_sha256"] == run["artifact_sha256"]
        if manifest_row["candidate_artifact_path"] is not None:
            assert report_path(manifest_row["candidate_artifact_path"]).exists()
            assert hash_report_path(manifest_row["candidate_artifact_path"]) == manifest_row[
                "candidate_artifact_sha256"
            ]
        assert report_path(run["stdout_path"]).exists()
        assert hash_report_path(run["stdout_path"]) == run["stdout_sha256"]
        stdout = load_report_path(run["stdout_path"])
        if run_name in {"ungated", "cap"}:
            assert stdout["cap_parity"]["ungated_metrics_hash"] == runtime_sizing_effect[
                "ungated_metrics_hash"
            ]
            assert stdout["cap_parity"]["cap_metrics_hash"] == runtime_sizing_effect[
                "cap_metrics_hash"
            ]
        if run["artifact_path"] is not None:
            assert hash_report_path(run["artifact_path"]) == run["artifact_sha256"]
    for row in report["checks"]["artifact_validation"]["artifacts"]:
        assert report_path(row["artifact_path"]).exists()
        assert hash_report_path(row["artifact_path"]) == row["artifact_sha256"]
        assert row["validator_payload"]["schema_version"] == RESULT_SCHEMA_VERSION
        assert row["validator_payload"]["valid"] is True
        assert row["validator_payload"]["errors"] == []
