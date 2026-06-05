use std::path::{Path, PathBuf};
use std::process::{Command, Output};

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

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}

fn write_policy(path: &Path, decision_class: &str) {
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
          "version": "risk-policy.v1.phase135.rust-gate-test",
          "owner": "side-v5.7-risk-gate",
          "effective_from": "2026-05-07",
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
              "id": "phase135.{decision_class}",
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

fn write_cap_policy_with_allowed_size(path: &Path, allowed_size: &str) {
    let policy = format!(
        r#"{{
          "version": "risk-policy.v1.phase135.rust-gate-test",
          "owner": "side-v5.7-risk-gate",
          "effective_from": "2026-05-07",
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
              "id": "phase135.cap",
              "decision_class": "cap",
              "when": {{
                "path": "candidate.requested_size",
                "op": "exists",
                "value": true
              }},
              "fail_close_reason": "insufficient_validation_power",
              "allowed_size": {allowed_size}
            }}
          ]
        }}"#
    );
    std::fs::write(path, policy).unwrap();
}

fn run_scan_with_policy(
    tmp: &TempDir,
    policy: &Path,
    output_name: &str,
    extra_args: &[&str],
) -> (Output, PathBuf, PathBuf) {
    let fixtures = fixtures_dir();
    let artifact_root = tmp.path().join(format!("{output_name}.risk-artifacts"));
    let output = tmp.path().join(format!("{output_name}.scan.json"));

    let mut cmd = Command::new(side_cli_binary());
    cmd.args([
        "scan",
        "--asset",
        "USDJPY",
        "--timeframe",
        "1h",
        "--fixture-parquet",
        fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
        "--edges",
        fixtures.join("edges_sample.json").to_str().unwrap(),
        "--spread-bps-rt",
        "1.5",
        "--commission-bps-rt",
        "0.5",
        "--risk-gate-policy",
        policy.to_str().unwrap(),
        "--risk-gate-artifact-root",
        artifact_root.to_str().unwrap(),
        "--output",
        output.to_str().unwrap(),
    ]);
    cmd.args(extra_args);
    cmd.env("SIDE_RISK_GATE_NO_WFD_SENTINEL", "panic");

    (
        cmd.output().expect("failed to spawn side-cli"),
        output,
        artifact_root,
    )
}

fn run_scan_with_gate(
    tmp: &TempDir,
    decision_class: &str,
    extra_args: &[&str],
) -> (Output, PathBuf, PathBuf) {
    let fixtures = fixtures_dir();
    let policy = tmp.path().join(format!("{decision_class}.policy.json"));
    let artifact_root = tmp.path().join("risk-artifacts");
    let output = tmp.path().join(format!("{decision_class}.scan.json"));
    write_policy(&policy, decision_class);

    let mut cmd = Command::new(side_cli_binary());
    cmd.args([
        "scan",
        "--asset",
        "USDJPY",
        "--timeframe",
        "1h",
        "--fixture-parquet",
        fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
        "--edges",
        fixtures.join("edges_sample.json").to_str().unwrap(),
        "--spread-bps-rt",
        "1.5",
        "--commission-bps-rt",
        "0.5",
        "--risk-gate-policy",
        policy.to_str().unwrap(),
        "--risk-gate-artifact-root",
        artifact_root.to_str().unwrap(),
        "--output",
        output.to_str().unwrap(),
    ]);
    cmd.args(extra_args);
    cmd.env("SIDE_RISK_GATE_NO_WFD_SENTINEL", "panic");

    (
        cmd.output().expect("failed to spawn side-cli"),
        output,
        artifact_root,
    )
}

fn run_scan_without_gate(tmp: &TempDir) -> (Output, PathBuf) {
    let fixtures = fixtures_dir();
    let output = tmp.path().join("ungated.scan.json");
    let out = Command::new(side_cli_binary())
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--edges",
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--spread-bps-rt",
            "1.5",
            "--commission-bps-rt",
            "0.5",
            "--output",
            output.to_str().unwrap(),
        ])
        .output()
        .expect("failed to spawn side-cli");
    (out, output)
}

#[test]
fn risk_gate_flags_reject_exactly_one_flag_before_output() {
    let tmp = TempDir::new().unwrap();
    let fixtures = fixtures_dir();
    let policy = tmp.path().join("policy.json");
    let output = tmp.path().join("out.json");
    write_policy(&policy, "size");

    let out = Command::new(side_cli_binary())
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--edges",
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--risk-gate-policy",
            policy.to_str().unwrap(),
            "--output",
            output.to_str().unwrap(),
        ])
        .output()
        .expect("failed to spawn side-cli");

    assert!(
        !out.status.success(),
        "scan should reject exactly one gate flag"
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr
            .contains("--risk-gate-policy and --risk-gate-artifact-root must be supplied together"),
        "stderr was: {stderr}"
    );
    assert!(
        !output.exists(),
        "validation failure must not write output JSON"
    );
}

#[test]
fn risk_gate_flags_require_edges() {
    let tmp = TempDir::new().unwrap();
    let fixtures = fixtures_dir();
    let policy = tmp.path().join("policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    let output = tmp.path().join("out.json");
    write_policy(&policy, "size");

    let out = Command::new(side_cli_binary())
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--risk-gate-policy",
            policy.to_str().unwrap(),
            "--risk-gate-artifact-root",
            artifact_root.to_str().unwrap(),
            "--output",
            output.to_str().unwrap(),
        ])
        .output()
        .expect("failed to spawn side-cli");

    assert!(
        !out.status.success(),
        "scan should reject gate flags without --edges"
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("--risk-gate-policy and --risk-gate-artifact-root require --edges"),
        "stderr was: {stderr}"
    );
    assert!(
        !output.exists(),
        "validation failure must not write output JSON"
    );
}

#[test]
fn risk_gate_block_stops_before_metrics() {
    let tmp = TempDir::new().unwrap();
    let (out, output, _artifact_root) = run_scan_with_gate(&tmp, "block", &[]);

    assert!(
        out.status.success(),
        "scan should succeed with stopped slots\nstderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(!stderr.contains("SIDE_RISK_GATE_NO_WFD_SENTINEL"));

    let slots = read_slots(&output);
    let slot_array = slots.as_array().unwrap();
    assert!(
        !slot_array.is_empty(),
        "fixture should produce stopped slots"
    );
    for slot in slot_array {
        assert_stopped_slot(slot);
    }
}

#[test]
fn risk_gate_block_stops_before_metrics_in_strict_and_relaxed() {
    for extra_args in [Vec::<&str>::new(), vec!["--pass-mode", "relaxed"]] {
        let tmp = TempDir::new().unwrap();
        let (out, output, _artifact_root) = run_scan_with_gate(&tmp, "block", &extra_args);

        assert!(
            out.status.success(),
            "scan should succeed with block stopped slots\nstderr:\n{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(
            !stderr.contains("SIDE_RISK_GATE_NO_WFD_SENTINEL: stopped decision reached fee loop")
        );
        assert!(!stderr.contains(
            "SIDE_RISK_GATE_NO_WFD_SENTINEL: stopped decision reached run_scan_fixed_params"
        ));
        assert!(!stderr
            .contains("SIDE_RISK_GATE_NO_WFD_SENTINEL: stopped decision reached run_wfd_single"));

        let slots = read_slots(&output);
        for slot in slots.as_array().unwrap() {
            assert_stopped_slot(slot);
        }
    }
}

#[test]
fn risk_gate_kill_and_reject_stop_before_metrics() {
    for decision_class in ["kill", "reject"] {
        let tmp = TempDir::new().unwrap();
        let (out, output, _artifact_root) = run_scan_with_gate(&tmp, decision_class, &[]);

        assert!(
            out.status.success(),
            "scan should succeed with {decision_class} stopped slots\nstderr:\n{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(!stderr.contains("SIDE_RISK_GATE_NO_WFD_SENTINEL"));

        let slots = read_slots(&output);
        for slot in slots.as_array().unwrap() {
            assert_stopped_slot(slot);
        }
    }
}

#[test]
fn risk_gate_size_continues_existing_metrics() {
    let tmp = TempDir::new().unwrap();
    let (out, output, _artifact_root) = run_scan_with_gate(&tmp, "size", &[]);

    assert!(
        out.status.success(),
        "scan should continue for size\nstderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let slots = read_slots(&output);
    let first = &slots.as_array().unwrap()[0];
    assert_continued_slot(first);
    assert_risk_gate_summary(first, "size", "continued", None);
    assert_no_runtime_sizing_fields(&first["risk_gate"]);
}

#[test]
fn risk_gate_cap_emits_applied_runtime_sizing_and_preserves_metrics() {
    let tmp = TempDir::new().unwrap();
    let (ungated_out, ungated_output) = run_scan_without_gate(&tmp);
    assert!(
        ungated_out.status.success(),
        "ungated scan failed\nstderr:\n{}",
        String::from_utf8_lossy(&ungated_out.stderr)
    );

    let (gated_out, gated_output, _artifact_root) = run_scan_with_gate(&tmp, "cap", &[]);
    assert!(
        gated_out.status.success(),
        "cap gate scan failed\nstderr:\n{}",
        String::from_utf8_lossy(&gated_out.stderr)
    );

    let ungated = read_slots(&ungated_output);
    let gated = read_slots(&gated_output);
    let ungated_array = ungated.as_array().unwrap();
    let gated_array = gated.as_array().unwrap();
    assert_eq!(gated_array.len(), ungated_array.len());
    for (idx, (ungated_slot, gated_slot)) in
        ungated_array.iter().zip(gated_array.iter()).enumerate()
    {
        assert!(
            ungated_slot.get("risk_gate").is_none(),
            "ungated scan must omit risk_gate at slot {idx}"
        );
        assert_continued_slot(gated_slot);
        assert_risk_gate_summary(gated_slot, "cap", "continued", Some("applied"));
        assert_scan_cap_runtime_sizing(&gated_slot["risk_gate"], 0.25, "reduced");
        assert_eq!(
            gated_slot["fee_curve"], ungated_slot["fee_curve"],
            "slot {idx}"
        );
        assert_eq!(
            gated_slot["pf_gross"], ungated_slot["pf_gross"],
            "slot {idx}"
        );
        assert_eq!(
            gated_slot["pf_net@2bps_rt"], ungated_slot["pf_net@2bps_rt"],
            "slot {idx}"
        );
        assert_eq!(
            gated_slot["alpha_cliff"], ungated_slot["alpha_cliff"],
            "slot {idx}"
        );
        assert_eq!(gated_slot["verdict"], ungated_slot["verdict"], "slot {idx}");
        assert_eq!(
            gated_slot["verdicts_per_fee"], ungated_slot["verdicts_per_fee"],
            "slot {idx}"
        );
    }
}

#[test]
fn risk_gate_cap_over_request_policy_clamps_to_requested_scan_slot() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("cap-over-request.policy.json");
    write_cap_policy_with_allowed_size(&policy, "1.25");

    let (out, output, _artifact_root) =
        run_scan_with_policy(&tmp, &policy, "cap-over-request", &[]);

    assert!(
        out.status.success(),
        "over-request cap should be clamped by risk engine and continue\nstderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let slots = read_slots(&output);
    let first = &slots.as_array().unwrap()[0];
    assert_continued_slot(first);
    assert_risk_gate_summary(first, "cap", "continued", Some("applied"));
    assert_scan_cap_runtime_sizing(&first["risk_gate"], 1.0, "none");
}

#[test]
fn risk_gate_scan_v2_opt_in_emits_v2_validator_proof_and_artifact() {
    let tmp = TempDir::new().unwrap();
    let (out, output, artifact_root) =
        run_scan_with_gate(&tmp, "cap", &["--risk-gate-contract-version", "v2"]);

    assert!(
        out.status.success(),
        "v2 scan gate should succeed\nstderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let slots = read_slots(&output);
    let first = &slots.as_array().unwrap()[0];
    assert_continued_slot(first);
    assert_risk_gate_v2_summary(first, "cap", "continued", Some("applied"));
    assert_scan_cap_runtime_sizing(&first["risk_gate"], 0.25, "reduced");
    assert_eq!(first["risk_gate"]["schema_version"], "risk_contract.v2");
    assert_eq!(first["risk_gate"]["contract_version"], "v2");
    assert_eq!(
        first["risk_gate"]["validator_result_schema_version"],
        "risk_contract_validator_result.v2"
    );
    assert_eq!(
        first["risk_gate"]["validated_schema_ref"],
        "risk/contracts/v2/risk_contract_v2.schema.json"
    );
    assert_eq!(
        first["risk_gate"]["schema_ref"],
        "risk/contracts/v2/risk_contract_v2.schema.json"
    );

    let candidate_id = first["risk_gate"]["candidate_id"].as_str().unwrap();
    let candidate_path = artifact_root
        .join("candidates")
        .join(format!("{candidate_id}.json"));
    let candidate: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(candidate_path).unwrap()).unwrap();
    assert_eq!(
        candidate["candidate_schema_version"],
        "risk_contract.v2.candidate.v1"
    );
    assert_eq!(candidate["surface"]["runtime_surface"], "scan");
    assert_eq!(candidate["surface"]["surface_status"], "implemented");
    assert_eq!(candidate["surface"]["analysis_scope"], "none");
    assert_eq!(
        candidate["surface"]["analysis_scope_status"],
        "not_applicable"
    );
    assert_eq!(
        candidate["sizing"]["requested_size_basis"],
        "unit_scan_slot"
    );

    let artifact_path = PathBuf::from(first["risk_gate"]["artifact_path"].as_str().unwrap());
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
    assert_eq!(artifact["application"]["metrics_rescaled"], false);
    assert_eq!(
        artifact["trace"]["validator_result_schema_version"],
        "risk_contract_validator_result.v2"
    );
}

#[test]
fn risk_gate_scan_v2_rejects_protected_artifact_root_before_output() {
    let tmp = TempDir::new_in(env!("CARGO_MANIFEST_DIR")).unwrap();
    let fixtures = fixtures_dir();
    let policy = tmp.path().join("cap.policy.json");
    let output = tmp.path().join("protected-output.json");
    write_policy(&policy, "cap");

    let out = Command::new(side_cli_binary())
        .current_dir(tmp.path())
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--edges",
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--risk-gate-policy",
            policy.to_str().unwrap(),
            "--risk-gate-artifact-root",
            "reports/v8.3/scan-v2-protected-test",
            "--risk-gate-contract-version",
            "v2",
            "--output",
            output.to_str().unwrap(),
        ])
        .output()
        .expect("failed to spawn side-cli");

    assert!(
        !out.status.success(),
        "protected v2 artifact root should fail before scan output"
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("unsafe v2 artifact_root: protected report root"),
        "stderr was: {stderr}"
    );
    assert!(
        !output.exists(),
        "protected-root validation failure must not write output JSON"
    );
    assert!(
        !tmp.path()
            .join("reports/v8.3/scan-v2-protected-test")
            .exists(),
        "protected-root validation should fail before artifact root creation"
    );
}

#[test]
fn risk_gate_public_summary_shape_for_stopped_slots() {
    for decision_class in ["block", "kill", "reject"] {
        let tmp = TempDir::new().unwrap();
        let (out, output, _artifact_root) = run_scan_with_gate(&tmp, decision_class, &[]);

        assert!(
            out.status.success(),
            "scan should succeed with {decision_class} stopped slots\nstderr:\n{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(!stderr.contains("SIDE_RISK_GATE_NO_WFD_SENTINEL"));

        let slots = read_slots(&output);
        let slot_array = slots.as_array().unwrap();
        assert!(
            !slot_array.is_empty(),
            "fixture should produce stopped slots"
        );
        for slot in slot_array {
            assert_stopped_slot(slot);
            assert_risk_gate_summary(slot, decision_class, "stopped", None);
            assert_no_runtime_sizing_fields(&slot["risk_gate"]);
        }
    }
}

#[test]
fn risk_gate_public_summary_shape_for_size() {
    let tmp = TempDir::new().unwrap();
    let (out, output, _artifact_root) = run_scan_with_gate(&tmp, "size", &[]);

    assert!(
        out.status.success(),
        "scan should continue for size\nstderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let slots = read_slots(&output);
    let slot_array = slots.as_array().unwrap();
    assert!(
        !slot_array.is_empty(),
        "fixture should produce continued slots"
    );
    for slot in slot_array {
        assert_continued_slot(slot);
        assert_risk_gate_summary(slot, "size", "continued", None);
        assert_no_runtime_sizing_fields(&slot["risk_gate"]);
    }
}

#[test]
fn risk_gate_artifact_paths_revalidate_with_contract_validator() {
    let tmp = TempDir::new().unwrap();
    let (out, output, _artifact_root) = run_scan_with_gate(&tmp, "size", &[]);

    assert!(
        out.status.success(),
        "scan should continue for size\nstderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );

    let slots = read_slots(&output);
    let mut checked = 0usize;
    for slot in slots.as_array().unwrap() {
        let artifact_path = slot["risk_gate"]["artifact_path"]
            .as_str()
            .expect("risk_gate.artifact_path must be a string");
        assert!(
            Path::new(artifact_path).exists(),
            "risk artifact path should exist: {artifact_path}"
        );
        assert_artifact_validates(artifact_path);
        checked += 1;
    }
    assert!(checked > 0, "expected at least one risk artifact path");
}

#[test]
fn risk_gate_wrapper_failure_aborts_scan_before_output() {
    let tmp = TempDir::new().unwrap();
    let fixtures = fixtures_dir();
    let policy = tmp.path().join("malformed-policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    let output = tmp.path().join("out.json");
    std::fs::write(&policy, "{not-json").unwrap();

    let out = Command::new(side_cli_binary())
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--edges",
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--risk-gate-policy",
            policy.to_str().unwrap(),
            "--risk-gate-artifact-root",
            artifact_root.to_str().unwrap(),
            "--output",
            output.to_str().unwrap(),
        ])
        .output()
        .expect("failed to spawn side-cli");

    assert!(!out.status.success(), "malformed policy should abort scan");
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("execution_state=gate_error"),
        "stderr was: {stderr}"
    );
    assert!(
        !output.exists(),
        "gate_error must not write successful scan output"
    );
}

#[test]
fn risk_gate_relative_policy_and_artifact_paths_resolve_from_cli_cwd() {
    let tmp = TempDir::new_in(env!("CARGO_MANIFEST_DIR")).unwrap();
    let fixtures = fixtures_dir();
    let policy = tmp.path().join("relative-policy.json");
    let artifact_root = tmp.path().join("relative-artifacts");
    let output = tmp.path().join("relative-output.json");
    write_policy(&policy, "size");

    let cwd = Path::new(env!("CARGO_MANIFEST_DIR"));
    let relative_policy = policy.strip_prefix(cwd).unwrap();
    let relative_artifact_root = artifact_root.strip_prefix(cwd).unwrap();
    let relative_output = output.strip_prefix(cwd).unwrap();

    let out = Command::new(side_cli_binary())
        .current_dir(cwd)
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--edges",
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--risk-gate-policy",
            relative_policy.to_str().unwrap(),
            "--risk-gate-artifact-root",
            relative_artifact_root.to_str().unwrap(),
            "--output",
            relative_output.to_str().unwrap(),
        ])
        .output()
        .expect("failed to spawn side-cli");

    assert!(
        out.status.success(),
        "relative gate paths should resolve from CLI cwd\nstderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        output.exists(),
        "scan output should be written via relative path"
    );
    assert!(
        artifact_root.exists(),
        "artifact root should be written via relative path"
    );
}

fn read_slots(path: &Path) -> serde_json::Value {
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn assert_stopped_slot(slot: &serde_json::Value) {
    assert_eq!(slot["fee_curve"].as_array().unwrap().len(), 0);
    assert!(slot["pf_gross"].is_null());
    assert!(slot["pf_net@2bps_rt"].is_null());
    assert!(slot["alpha_cliff"].is_null());
    assert!(slot.get("verdict").is_none() || slot["verdict"].is_null());
    assert!(slot.get("relaxed_pass").is_none() || slot["relaxed_pass"].is_null());
    assert!(slot.get("verdicts_per_fee").is_none() || slot["verdicts_per_fee"].is_null());
    assert_no_top_level_risk_fields(slot);
    if let Some(risk_gate) = slot.get("risk_gate") {
        assert_no_runtime_sizing_fields(risk_gate);
    }
}

fn assert_continued_slot(slot: &serde_json::Value) {
    assert!(
        !slot["fee_curve"].as_array().unwrap().is_empty(),
        "continued slot must keep fee_curve"
    );
    assert!(slot.get("pf_gross").is_some());
    assert!(slot.get("pf_net@2bps_rt").is_some());
    assert!(slot.get("alpha_cliff").is_some());
    assert_no_top_level_risk_fields(slot);
}

fn assert_no_top_level_risk_fields(slot: &serde_json::Value) {
    let slot = slot.as_object().expect("slot must be an object");
    for key in [
        "decision_class",
        "allowed_size",
        "candidate_id",
        "artifact_path",
    ] {
        assert!(
            !slot.contains_key(key),
            "unexpected top-level risk key {key}"
        );
    }
}

fn expected_fail_close_reason(decision_class: &str) -> &'static str {
    match decision_class {
        "block" => "malformed_policy",
        "kill" => "stale_evidence",
        _ => "insufficient_validation_power",
    }
}

fn assert_risk_gate_summary(
    slot: &serde_json::Value,
    decision_class: &str,
    execution_state: &str,
    application_status: Option<&str>,
) {
    assert_no_top_level_risk_fields(slot);
    let risk_gate = slot["risk_gate"]
        .as_object()
        .expect("slot should contain risk_gate object");

    assert_eq!(risk_gate["decision_class"], decision_class);
    assert!(
        risk_gate["allowed_size"].is_number(),
        "risk_gate.allowed_size must be numeric"
    );
    assert_eq!(
        risk_gate["binding_rule"],
        format!("phase135.{decision_class}")
    );
    assert_eq!(
        risk_gate["fail_close_reason"],
        expected_fail_close_reason(decision_class)
    );
    assert_eq!(
        risk_gate["policy_version"],
        "risk-policy.v1.phase135.rust-gate-test"
    );
    assert!(
        risk_gate["candidate_id"]
            .as_str()
            .map(|s| !s.is_empty())
            .unwrap_or(false),
        "risk_gate.candidate_id must be non-empty"
    );
    assert!(
        risk_gate["artifact_path"]
            .as_str()
            .map(|s| !s.is_empty())
            .unwrap_or(false),
        "risk_gate.artifact_path must be non-empty"
    );
    assert_eq!(risk_gate["execution_state"], execution_state);
    assert_eq!(risk_gate["validation_status"], "validated");
    assert_eq!(risk_gate["validator"], "scripts/validate_risk_contract.py");
    assert_eq!(
        risk_gate["schema_ref"],
        "risk/contracts/v1/risk_contract_v1.schema.json"
    );
    match application_status {
        Some(expected) => assert_eq!(risk_gate["application_status"], expected),
        None => assert!(
            !risk_gate.contains_key("application_status"),
            "application_status should be omitted"
        ),
    }
}

fn assert_risk_gate_v2_summary(
    slot: &serde_json::Value,
    decision_class: &str,
    execution_state: &str,
    application_status: Option<&str>,
) {
    assert_no_top_level_risk_fields(slot);
    let risk_gate = slot["risk_gate"]
        .as_object()
        .expect("slot should contain risk_gate object");

    assert_eq!(risk_gate["decision_class"], decision_class);
    assert!(
        risk_gate["allowed_size"].is_number(),
        "risk_gate.allowed_size must be numeric"
    );
    assert_eq!(
        risk_gate["binding_rule"],
        format!("phase135.{decision_class}")
    );
    assert_eq!(risk_gate["fail_close_reason"], "not_fail_closed");
    assert_eq!(
        risk_gate["policy_version"],
        "risk-policy.v1.phase135.rust-gate-test"
    );
    assert!(
        risk_gate["candidate_id"]
            .as_str()
            .map(|s| !s.is_empty())
            .unwrap_or(false),
        "risk_gate.candidate_id must be non-empty"
    );
    assert!(
        risk_gate["artifact_path"]
            .as_str()
            .map(|s| !s.is_empty())
            .unwrap_or(false),
        "risk_gate.artifact_path must be non-empty"
    );
    assert_eq!(risk_gate["execution_state"], execution_state);
    assert_eq!(risk_gate["validation_status"], "validated");
    assert_eq!(risk_gate["validator"], "scripts/validate_risk_contract.py");
    assert_eq!(
        risk_gate["schema_ref"],
        "risk/contracts/v2/risk_contract_v2.schema.json"
    );
    assert_eq!(risk_gate["schema_version"], "risk_contract.v2");
    assert_eq!(risk_gate["contract_version"], "v2");
    assert_eq!(
        risk_gate["validator_result_schema_version"],
        "risk_contract_validator_result.v2"
    );
    assert_eq!(
        risk_gate["validated_schema_ref"],
        "risk/contracts/v2/risk_contract_v2.schema.json"
    );
    match application_status {
        Some(expected) => assert_eq!(risk_gate["application_status"], expected),
        None => assert!(
            !risk_gate.contains_key("application_status"),
            "application_status should be omitted"
        ),
    }
}

fn assert_scan_cap_runtime_sizing(
    risk_gate: &serde_json::Value,
    expected_effective_size: f64,
    expected_sizing_effect: &str,
) {
    assert_eq!(risk_gate["runtime_sizing_applied"], true);
    assert_eq!(risk_gate["sizing_effect"], expected_sizing_effect);
    assert_eq!(risk_gate["requested_size"], 1.0);
    assert_eq!(risk_gate["requested_size_basis"], "unit_scan_slot");
    assert_eq!(risk_gate["allowed_size"], expected_effective_size);
    assert_eq!(risk_gate["effective_size"], expected_effective_size);
}

fn assert_no_runtime_sizing_fields(risk_gate: &serde_json::Value) {
    let risk_gate = risk_gate
        .as_object()
        .expect("risk_gate should be a JSON object");
    for key in [
        "runtime_sizing_applied",
        "sizing_effect",
        "requested_size",
        "requested_size_basis",
        "effective_size",
    ] {
        assert!(
            !risk_gate.contains_key(key),
            "{key} should be omitted outside scan cap applied-runtime slots"
        );
    }
}

fn assert_artifact_validates(path: &str) {
    let out = Command::new("uv")
        .current_dir(repo_root())
        .args(["run", "python", "scripts/validate_risk_contract.py", path])
        .output()
        .expect("failed to spawn risk contract validator");
    assert!(
        out.status.success(),
        "risk contract validator failed\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    let payload: serde_json::Value =
        serde_json::from_slice(&out.stdout).expect("validator stdout should be JSON");
    assert_eq!(payload["valid"], true);
}
