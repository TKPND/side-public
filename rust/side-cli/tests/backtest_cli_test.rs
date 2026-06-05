use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::Command;

use tempfile::TempDir;

fn side_cli_binary() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_side"))
}

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("side-engine")
        .join("tests")
        .join("fixtures")
}

fn base_backtest_command() -> Command {
    let mut command = Command::new(side_cli_binary());
    command.args([
        "backtest",
        "--asset",
        "USDJPY",
        "--timeframe",
        "1h",
        "--data",
        fixtures_dir()
            .join("usdjpy_1h_sample.parquet")
            .to_str()
            .unwrap(),
        "--strategy",
        "tod_edge",
        "--params",
        r#"{"entry_minute":0,"direction":"long","hold_h":3}"#,
        "--fee-bps",
        "1.0",
    ]);
    command
}

fn write_backtest_policy(path: &Path, decision_class: &str) {
    let fail_close_reason = match decision_class {
        "block" => "malformed_policy",
        "kill" => "stale_evidence",
        _ => "insufficient_validation_power",
    };
    let allowed_size = if decision_class == "cap" {
        r#","allowed_size":0.25"#
    } else {
        ""
    };
    let policy = format!(
        r#"{{
          "version": "risk-policy.v1.phase139.backtest-test",
          "owner": "side-v5.8-backtest-risk-gate",
          "effective_from": "2026-05-08",
          "required_fields": [
            "policy.version",
            "candidate.strategy_id",
            "candidate.requested_size",
            "evidence.refs",
            "trace.emitted_artifact_path"
          ],
          "fail_close_rules": [
            {{
              "condition": "malformed policy rules",
              "decision_class": "block",
              "fail_close_reason": "malformed_policy"
            }},
            {{
              "condition": "candidate requested size invalid",
              "decision_class": "block",
              "fail_close_reason": "candidate_validation_failure"
            }}
          ],
          "rules": [
            {{
              "id": "phase139.{decision_class}",
              "decision_class": "{decision_class}",
              "when": {{
                "path": "candidate.requested_size",
                "op": "exists",
                "value": true
              }},
              "fail_close_reason": "{fail_close_reason}"{allowed_size}
            }}
          ]
        }}"#
    );
    std::fs::write(path, policy).unwrap();
}

fn gated_backtest_command_with_paths(policy: &Path, artifact_root: &Path) -> Command {
    gated_backtest_command_with_paths_and_contract_version(policy, artifact_root, None)
}

fn gated_backtest_command_with_paths_and_contract_version(
    policy: &Path,
    artifact_root: &Path,
    contract_version: Option<&str>,
) -> Command {
    let cwd = Path::new(env!("CARGO_MANIFEST_DIR"));
    let relative_policy = policy.strip_prefix(cwd).unwrap();
    let relative_artifact_root = artifact_root.strip_prefix(cwd).unwrap();
    let mut command = base_backtest_command();
    command.current_dir(cwd).args([
        "--risk-gate-policy",
        relative_policy.to_str().unwrap(),
        "--risk-gate-artifact-root",
        relative_artifact_root.to_str().unwrap(),
    ]);
    if let Some(contract_version) = contract_version {
        command.args(["--risk-gate-contract-version", contract_version]);
    }
    command
}

fn gated_backtest_output(decision_class: &str) -> (std::process::Output, TempDir, PathBuf) {
    gated_backtest_output_with_contract_version(decision_class, None)
}

fn gated_backtest_output_with_contract_version(
    decision_class: &str,
    contract_version: Option<&str>,
) -> (std::process::Output, TempDir, PathBuf) {
    let tmp = TempDir::new_in(env!("CARGO_MANIFEST_DIR")).unwrap();
    let policy = tmp.path().join(format!("{decision_class}.policy.json"));
    let artifact_root = tmp.path().join(match contract_version {
        Some("v2") => "risk-artifacts-v2",
        _ => "risk-artifacts",
    });
    write_backtest_policy(&policy, decision_class);

    let output = gated_backtest_command_with_paths_and_contract_version(
        &policy,
        &artifact_root,
        contract_version,
    )
    .output()
    .unwrap_or_else(|err| panic!("failed to spawn gated backtest {decision_class}: {err}"));

    (output, tmp, artifact_root)
}

fn parse_stdout_json(output: &std::process::Output) -> serde_json::Value {
    let stdout = String::from_utf8(output.stdout.clone()).unwrap();
    assert!(stdout.ends_with('\n'), "stdout should end with newline");
    serde_json::from_str(stdout.trim_end()).unwrap()
}

#[test]
fn backtest_success_backtest_stdout_shape() {
    let output = base_backtest_command()
        .output()
        .expect("failed to spawn side-cli backtest");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(output.status.success(), "stderr was: {stderr}");
    assert!(
        stderr.is_empty() || !stderr.to_lowercase().contains("error"),
        "unexpected stderr: {stderr}"
    );

    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.ends_with('\n'), "stdout should end with newline");
    let value: serde_json::Value = serde_json::from_str(stdout.trim_end()).unwrap();
    let object = value.as_object().expect("stdout must be one JSON object");
    let keys = object.keys().map(String::as_str).collect::<HashSet<_>>();
    assert_eq!(
        keys,
        HashSet::from([
            "schema_version",
            "risk_gate_enabled",
            "run_status",
            "asset",
            "strategy",
            "timeframe",
            "params",
            "data_ref",
            "data_fingerprint",
            "fee_bps",
            "metrics",
            "risk_gate",
            "cap_parity",
            "backtest_execution",
        ])
    );
    assert_eq!(value["schema_version"], "side-cli.backtest.result.v1");
    assert_eq!(value["risk_gate_enabled"], false);
    assert_eq!(value["run_status"], "completed");
    assert_eq!(value["asset"], "USDJPY");
    assert_eq!(value["strategy"], "tod_edge");
    assert_eq!(value["timeframe"], "1h");
    assert_eq!(value["params"]["entry_minute"], 0);
    assert_eq!(value["params"]["direction"], "long");
    assert_eq!(value["params"]["hold_h"], 3);
    assert!(value["data_ref"]
        .as_str()
        .unwrap()
        .contains("usdjpy_1h_sample.parquet"));
    assert_eq!(
        value["data_fingerprint"],
        "sha256:14def03e6037df2108b4c0faba9da0a71306bc8ff5259541bfeff9b8f24dd0b0"
    );
    assert_eq!(value["fee_bps"], 1.0);
    let metrics = value["metrics"].as_object().unwrap();
    assert_eq!(
        metrics.keys().map(String::as_str).collect::<HashSet<_>>(),
        HashSet::from(["profit_factor", "num_trades", "total_return"])
    );
    for absent in [
        "sharpe_ratio",
        "max_drawdown",
        "win_rate",
        "gross_profit",
        "gross_loss",
        "equity_curve",
        "timestamps",
    ] {
        assert!(metrics.get(absent).is_none(), "{absent} leaked");
    }
    assert_eq!(value["risk_gate"], serde_json::Value::Null);
    assert_eq!(value["cap_parity"]["status"], "not_applicable");
    assert_eq!(value["backtest_execution"]["status"], "run");
}

#[test]
fn backtest_help_lists_risk_gate_flags() {
    let output = Command::new(side_cli_binary())
        .args(["backtest", "--help"])
        .output()
        .expect("failed to spawn side-cli backtest --help");
    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("risk-gate-policy"));
    assert!(stdout.contains("risk-gate-artifact-root"));
    assert!(stdout.contains("risk-gate-contract-version"));
}

#[test]
fn backtest_risk_gate_flags_reject_exactly_one_flag() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    std::fs::write(&policy, "{}").unwrap();

    for (name, key, value) in [
        (
            "policy only",
            "--risk-gate-policy",
            policy.to_str().unwrap(),
        ),
        (
            "artifact root only",
            "--risk-gate-artifact-root",
            artifact_root.to_str().unwrap(),
        ),
    ] {
        let output = base_backtest_command()
            .args([key, value])
            .output()
            .unwrap_or_else(|err| panic!("failed to spawn side-cli backtest {name}: {err}"));
        assert!(!output.status.success(), "{name} should fail");
        assert!(
            output.stdout.is_empty(),
            "{name} stdout should be empty, got {}",
            String::from_utf8_lossy(&output.stdout)
        );
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            stderr.contains(
                "--risk-gate-policy and --risk-gate-artifact-root must be supplied together"
            ),
            "{name} stderr was: {stderr}"
        );
    }
}

#[test]
fn backtest_risk_gate_infra_errors_are_nonzero() {
    let cwd = Path::new(env!("CARGO_MANIFEST_DIR"));
    let tmp = TempDir::new_in(cwd).unwrap();
    let invalid_policy = tmp.path().join("invalid-policy.json");
    let non_object_policy = tmp.path().join("non-object-policy.json");
    std::fs::write(&invalid_policy, "{").unwrap();
    std::fs::write(&non_object_policy, "[]").unwrap();
    let invalid_policy = invalid_policy
        .strip_prefix(cwd)
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    let non_object_policy = non_object_policy
        .strip_prefix(cwd)
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    let invalid_json_root = tmp
        .path()
        .join("risk-artifacts-invalid-json")
        .strip_prefix(cwd)
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    let non_object_root = tmp
        .path()
        .join("risk-artifacts-non-object-policy")
        .strip_prefix(cwd)
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();

    for case in [
        GateInfraCase {
            name: "invalid policy JSON",
            policy: invalid_policy.clone(),
            artifact_root: invalid_json_root,
            expected_stderr: "risk gate",
        },
        GateInfraCase {
            name: "non-object policy JSON",
            policy: non_object_policy.clone(),
            artifact_root: non_object_root,
            expected_stderr: "risk gate",
        },
        GateInfraCase {
            name: "unsafe artifact root",
            policy: non_object_policy,
            artifact_root: "reports/../risk_gate".to_string(),
            expected_stderr: "unsafe artifact_root",
        },
    ] {
        let output = base_backtest_command()
            .current_dir(cwd)
            .args([
                "--risk-gate-policy",
                case.policy.as_str(),
                "--risk-gate-artifact-root",
                case.artifact_root.as_str(),
            ])
            .output()
            .unwrap_or_else(|err| panic!("failed to spawn side-cli backtest {}: {err}", case.name));
        assert!(!output.status.success(), "{} should fail", case.name);
        assert!(
            output.stdout.is_empty(),
            "{} stdout should be empty, got {}",
            case.name,
            String::from_utf8_lossy(&output.stdout)
        );
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            stderr.contains(case.expected_stderr),
            "{} stderr should contain {:?}, got {stderr}",
            case.name,
            case.expected_stderr
        );
        assert!(
            !stderr.contains(r#""run_status":"stopped""#),
            "{} must not masquerade as stopped JSON",
            case.name
        );
    }
}

#[test]
fn backtest_risk_gate_stop() {
    for decision_class in ["block", "kill", "reject"] {
        assert_backtest_stop_decision(decision_class);
    }
}

fn assert_backtest_stop_decision(decision_class: &str) {
    let (output, _tmp, artifact_root) = gated_backtest_output(decision_class);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        output.status.success(),
        "{decision_class} stderr was: {stderr}"
    );

    let value = parse_stdout_json(&output);
    assert_eq!(value["schema_version"], "side-cli.backtest.result.v1");
    assert_eq!(value["risk_gate_enabled"], true);
    assert_eq!(value["run_status"], "stopped");
    assert_eq!(value["metrics"], serde_json::Value::Null);
    assert_eq!(value["risk_gate"]["decision_class"], decision_class);
    assert_eq!(value["risk_gate"]["execution_state"], "stopped");
    assert!(value["risk_gate"]["candidate_id"]
        .as_str()
        .unwrap()
        .starts_with("backtest.USDJPY.1h.tod_edge.p"));
    let artifact_path = PathBuf::from(value["risk_gate"]["artifact_path"].as_str().unwrap());
    assert!(
        artifact_path.starts_with(artifact_root.join("decisions")),
        "{decision_class} artifact path {artifact_path:?} should be under {:?}",
        artifact_root.join("decisions")
    );
    assert_eq!(value["cap_parity"]["status"], "not_applicable");
    assert_eq!(value["backtest_execution"]["status"], "not_run");
    assert_eq!(value["backtest_execution"]["reason"], "risk_gate_stop");
    assert_eq!(value["backtest_execution"]["backtest_invocation_count"], 0);
}

#[test]
fn backtest_risk_gate_continue() {
    assert_backtest_continue_decision("size");
    assert_backtest_continue_decision("cap");
}

#[test]
fn backtest_risk_gate_binding_cap_applies_runtime_size_to_metrics_without_scaling_trade_count() {
    let (size_output, _size_tmp, _size_artifact_root) = gated_backtest_output("size");
    let (cap_output, _cap_tmp, _cap_artifact_root) = gated_backtest_output("cap");
    let size_stderr = String::from_utf8_lossy(&size_output.stderr);
    let cap_stderr = String::from_utf8_lossy(&cap_output.stderr);
    assert!(
        size_output.status.success(),
        "size stderr was: {size_stderr}"
    );
    assert!(cap_output.status.success(), "cap stderr was: {cap_stderr}");

    let size_value = parse_stdout_json(&size_output);
    let cap_value = parse_stdout_json(&cap_output);

    assert_eq!(
        size_value["metrics"]["num_trades"],
        cap_value["metrics"]["num_trades"]
    );
    assert_ne!(
        size_value["metrics"]["total_return"],
        cap_value["metrics"]["total_return"]
    );
    assert_eq!(cap_value["risk_gate"]["application_status"], "applied");
    assert_eq!(cap_value["risk_gate"]["runtime_sizing_applied"], true);
    assert_eq!(cap_value["risk_gate"]["sizing_effect"], "reduced");
}

#[test]
fn backtest_risk_gate_v2_opt_in_emits_v2_validator_proof_and_artifact() {
    let (output, _tmp, artifact_root) =
        gated_backtest_output_with_contract_version("cap", Some("v2"));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(output.status.success(), "v2 cap stderr was: {stderr}");

    let value = parse_stdout_json(&output);

    assert_eq!(value["schema_version"], "side-cli.backtest.result.v1");
    assert_eq!(value["risk_gate_enabled"], true);
    assert_eq!(value["run_status"], "completed");
    assert_eq!(value["risk_gate"]["decision_class"], "cap");
    assert_eq!(value["risk_gate"]["execution_state"], "continued");
    assert_eq!(value["risk_gate"]["schema_version"], "risk_contract.v2");
    assert_eq!(value["risk_gate"]["contract_version"], "v2");
    assert_eq!(
        value["risk_gate"]["validator_result_schema_version"],
        "risk_contract_validator_result.v2"
    );
    assert_eq!(
        value["risk_gate"]["validated_schema_ref"],
        "risk/contracts/v2/risk_contract_v2.schema.json"
    );
    assert_eq!(
        value["risk_gate"]["schema_ref"],
        "risk/contracts/v2/risk_contract_v2.schema.json"
    );
    assert_eq!(value["risk_gate"]["application_status"], "applied");
    assert_eq!(value["risk_gate"]["runtime_sizing_applied"], true);
    assert_eq!(
        value["risk_gate"]["requested_size_basis"],
        "unit_backtest_run"
    );
    assert_eq!(value["risk_gate"]["effective_size"], 0.25);

    let candidate_id = value["risk_gate"]["candidate_id"].as_str().unwrap();
    let candidate_path = artifact_root
        .join("candidates")
        .join(format!("{candidate_id}.json"));
    let candidate: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(candidate_path).unwrap()).unwrap();
    assert_eq!(
        candidate["candidate_schema_version"],
        "risk_contract.v2.candidate.v1"
    );
    assert_eq!(candidate["surface"]["runtime_surface"], "backtest");
    assert_eq!(candidate["surface"]["surface_status"], "implemented");
    assert_eq!(candidate["surface"]["analysis_scope"], "none");
    assert_eq!(
        candidate["sizing"]["requested_size_basis"],
        "unit_backtest_run"
    );

    let artifact_path = PathBuf::from(value["risk_gate"]["artifact_path"].as_str().unwrap());
    assert!(
        artifact_path.starts_with(artifact_root.join("decisions")),
        "v2 artifact path {artifact_path:?} should be under {:?}",
        artifact_root.join("decisions")
    );
    let artifact: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(artifact_path).unwrap()).unwrap();
    assert_eq!(artifact["schema_version"], "risk_contract.v2");
    assert_eq!(artifact["contract_version"], "v2");
    assert_eq!(artifact["application"]["application_status"], "applied");
    assert_eq!(artifact["application"]["runtime_sizing_applied"], true);
    assert_eq!(
        artifact["trace"]["validator_result_schema_version"],
        "risk_contract_validator_result.v2"
    );
}

#[test]
fn backtest_risk_gate_repeated_same_root_fails_without_overwrite() {
    let tmp = TempDir::new_in(env!("CARGO_MANIFEST_DIR")).unwrap();
    let policy = tmp.path().join("size.policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    write_backtest_policy(&policy, "size");

    let first = gated_backtest_command_with_paths(&policy, &artifact_root)
        .output()
        .expect("failed to spawn first gated backtest");
    let first_stderr = String::from_utf8_lossy(&first.stderr);
    assert!(first.status.success(), "first stderr was: {first_stderr}");
    let first_json = parse_stdout_json(&first);
    let candidate_id = first_json["risk_gate"]["candidate_id"].as_str().unwrap();
    let candidate_path = artifact_root
        .join("candidates")
        .join(format!("{candidate_id}.json"));
    let decision_path = PathBuf::from(first_json["risk_gate"]["artifact_path"].as_str().unwrap());
    let candidate_before = std::fs::read(&candidate_path).unwrap();
    let decision_before = std::fs::read(&decision_path).unwrap();

    let second = gated_backtest_command_with_paths(&policy, &artifact_root)
        .output()
        .expect("failed to spawn second gated backtest");

    assert!(!second.status.success(), "second run should fail");
    assert!(
        second.stdout.is_empty(),
        "second stdout should be empty, got {}",
        String::from_utf8_lossy(&second.stdout)
    );
    let second_stderr = String::from_utf8_lossy(&second.stderr);
    assert!(
        second_stderr.contains("already exists"),
        "second stderr should explain duplicate artifact, got {second_stderr}"
    );
    assert_eq!(std::fs::read(&candidate_path).unwrap(), candidate_before);
    assert_eq!(std::fs::read(&decision_path).unwrap(), decision_before);
}

fn assert_backtest_continue_decision(decision_class: &str) {
    let (output, _tmp, _artifact_root) = gated_backtest_output(decision_class);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        output.status.success(),
        "{decision_class} stderr was: {stderr}"
    );

    let value = parse_stdout_json(&output);
    assert_eq!(value["risk_gate_enabled"], true);
    assert_eq!(value["run_status"], "completed");
    let metrics = value["metrics"].as_object().unwrap();
    assert!(metrics.contains_key("profit_factor"));
    assert!(metrics.contains_key("num_trades"));
    assert!(metrics.contains_key("total_return"));
    assert_eq!(value["risk_gate"]["decision_class"], decision_class);
    assert_eq!(value["risk_gate"]["execution_state"], "continued");
    assert_eq!(value["backtest_execution"]["status"], "run");
    assert_eq!(
        value["backtest_execution"]["reason"],
        serde_json::Value::Null
    );
    assert_eq!(value["backtest_execution"]["backtest_invocation_count"], 1);

    if decision_class == "cap" {
        assert_eq!(value["risk_gate"]["requested_size"], 1.0);
        assert_eq!(
            value["risk_gate"]["requested_size_basis"],
            "unit_backtest_run"
        );
        assert_eq!(value["risk_gate"]["allowed_size"], 0.25);
        assert_eq!(value["risk_gate"]["effective_size"], 0.25);
        assert_eq!(value["risk_gate"]["application_status"], "applied");
        assert_eq!(value["risk_gate"]["runtime_sizing_applied"], true);
        assert_eq!(value["risk_gate"]["sizing_effect"], "reduced");
        assert_eq!(value["cap_parity"]["status"], "not_applicable");
    } else {
        assert_eq!(
            value["risk_gate"]["application_status"],
            serde_json::Value::Null
        );
        assert_eq!(value["cap_parity"]["status"], "not_applicable");
    }
}

struct GateInfraCase {
    name: &'static str,
    policy: String,
    artifact_root: String,
    expected_stderr: &'static str,
}

#[test]
fn backtest_rejects_invalid_inputs() {
    for case in rejection_cases() {
        let mut command = case.command;
        let output = command.output().expect("failed to spawn side-cli backtest");
        assert!(!output.status.success(), "{} should fail", case.name);
        assert!(
            output.stdout.is_empty(),
            "{} stdout should be empty, got {}",
            case.name,
            String::from_utf8_lossy(&output.stdout)
        );
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            stderr.contains(case.expected_stderr),
            "{} stderr should contain {:?}, got {stderr}",
            case.name,
            case.expected_stderr
        );
    }
}

struct RejectionCase {
    name: &'static str,
    command: Command,
    expected_stderr: &'static str,
}

fn rejection_cases() -> Vec<RejectionCase> {
    let mut cases = Vec::new();

    let mut unsupported_strategy = base_backtest_command();
    replace_arg_value(&mut unsupported_strategy, "--strategy", "ema_atr");
    cases.push(RejectionCase {
        name: "unsupported strategy",
        command: unsupported_strategy,
        expected_stderr: "unsupported strategy",
    });

    let mut unsupported_walks = base_backtest_command();
    unsupported_walks.args(["--walks", "2"]);
    cases.push(RejectionCase {
        name: "unsupported walks",
        command: unsupported_walks,
        expected_stderr: "unsupported --walks",
    });

    for (name, params, expected_stderr) in [
        ("malformed params", "{", "params"),
        (
            "missing hold_h",
            r#"{"entry_minute":0,"direction":"long"}"#,
            "hold_h",
        ),
        (
            "extra params",
            r#"{"entry_minute":0,"direction":"long","hold_h":3,"news_blackout":true}"#,
            "unknown",
        ),
        (
            "invalid direction",
            r#"{"entry_minute":0,"direction":"flat","hold_h":3}"#,
            "direction",
        ),
        (
            "invalid hold_h",
            r#"{"entry_minute":0,"direction":"long","hold_h":10}"#,
            "hold_h",
        ),
        (
            "invalid entry_minute",
            r#"{"entry_minute":1440,"direction":"long","hold_h":3}"#,
            "entry_minute",
        ),
    ] {
        let mut command = base_backtest_command();
        replace_arg_value(&mut command, "--params", params);
        cases.push(RejectionCase {
            name,
            command,
            expected_stderr,
        });
    }

    let mut invalid_extension = base_backtest_command();
    replace_arg_value(
        &mut invalid_extension,
        "--data",
        "tests/fixtures/not_parquet.csv",
    );
    cases.push(RejectionCase {
        name: "invalid extension",
        command: invalid_extension,
        expected_stderr: "parquet",
    });

    let mut unsupported_timeframe = base_backtest_command();
    replace_arg_value(&mut unsupported_timeframe, "--timeframe", "2h");
    cases.push(RejectionCase {
        name: "unsupported timeframe",
        command: unsupported_timeframe,
        expected_stderr: "unsupported timeframe",
    });

    let mut invalid_fee = base_backtest_command();
    replace_arg_value(&mut invalid_fee, "--fee-bps", "-1");
    cases.push(RejectionCase {
        name: "invalid fee",
        command: invalid_fee,
        expected_stderr: "--fee-bps",
    });

    let mut missing_data = Command::new(side_cli_binary());
    missing_data.args([
        "backtest",
        "--asset",
        "USDJPY",
        "--timeframe",
        "1h",
        "--strategy",
        "tod_edge",
        "--params",
        r#"{"entry_minute":0,"direction":"long","hold_h":3}"#,
        "--fee-bps",
        "1.0",
    ]);
    cases.push(RejectionCase {
        name: "missing data",
        command: missing_data,
        expected_stderr: "--data",
    });

    let mut missing_params = Command::new(side_cli_binary());
    missing_params.args([
        "backtest",
        "--asset",
        "USDJPY",
        "--timeframe",
        "1h",
        "--data",
        fixtures_dir()
            .join("usdjpy_1h_sample.parquet")
            .to_str()
            .unwrap(),
        "--strategy",
        "tod_edge",
        "--fee-bps",
        "1.0",
    ]);
    cases.push(RejectionCase {
        name: "missing params",
        command: missing_params,
        expected_stderr: "--params",
    });

    cases
}

fn replace_arg_value(command: &mut Command, key: &str, value: &str) {
    let args = command
        .get_args()
        .map(|arg| arg.to_owned())
        .collect::<Vec<_>>();
    let mut rebuilt = Command::new(side_cli_binary());
    let mut skip_next = false;
    for (idx, arg) in args.iter().enumerate() {
        if skip_next {
            skip_next = false;
            continue;
        }
        if arg == key {
            rebuilt.arg(arg);
            rebuilt.arg(value);
            skip_next = true;
        } else if idx > 0 || arg != "side" {
            rebuilt.arg(arg);
        }
    }
    *command = rebuilt;
}
