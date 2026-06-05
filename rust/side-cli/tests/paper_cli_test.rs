use std::path::PathBuf;
use std::process::Command;

use sha2::{Digest, Sha256};
use side_cli::cmd::paper::{
    evaluate_paper_risk_once, evaluate_paper_risk_once_with_result,
    parse_runtime_accounting_mode_arg, PaperRiskGateOptions,
};
use side_cli::cmd::risk_gate::RiskGateContractVersion;
use side_engine::paper::{PaperConfig, RuntimeAccountingMode};
use tempfile::TempDir;

fn side_cli_binary() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_side"))
}

fn write_paper_policy(path: &std::path::Path, decision_class: &str) {
    let allowed_size = if decision_class == "cap" {
        r#","allowed_size":0.25"#
    } else {
        ""
    };
    let policy = format!(
        r#"{{
          "version": "risk-policy.v1.v6-paper-test",
          "owner": "side-v6-paper-parity",
          "effective_from": "2026-05-13",
          "required_fields": [
            "candidate.strategy_id",
            "candidate.requested_size",
            "evidence.refs",
            "context.emitted_artifact_path"
          ],
          "fail_close_rules": [
            {{
              "condition": "candidate requested size invalid",
              "decision_class": "block",
              "fail_close_reason": "candidate_validation_failure"
            }}
          ],
          "rules": [
            {{
              "id": "v6-paper.{decision_class}",
              "decision_class": "{decision_class}",
              "when": {{
                "path": "candidate.requested_size",
                "op": "exists",
                "value": true
              }},
              "fail_close_reason": "insufficient_validation_power"{allowed_size}
            }}
          ]
        }}"#
    );
    std::fs::write(path, policy).unwrap();
}

fn write_paper_policy_with_cap_allowed_size(path: &std::path::Path, cap_allowed_size: f64) {
    let policy = format!(
        r#"{{
          "version": "risk-policy.v1.v6-paper-test",
          "owner": "side-v6-paper-parity",
          "effective_from": "2026-05-13",
          "required_fields": [
            "candidate.strategy_id",
            "candidate.requested_size",
            "evidence.refs",
            "context.emitted_artifact_path"
          ],
          "fail_close_rules": [
            {{
              "condition": "candidate requested size invalid",
              "decision_class": "block",
              "fail_close_reason": "candidate_validation_failure"
            }}
          ],
          "rules": [
            {{
              "id": "v6-paper.cap",
              "decision_class": "cap",
              "when": {{
                "path": "candidate.requested_size",
                "op": "exists",
                "value": true
              }},
              "fail_close_reason": "insufficient_validation_power",
              "allowed_size": {cap_allowed_size}
            }}
          ]
        }}"#
    );
    std::fs::write(path, policy).unwrap();
}

fn risk_options<'a>(
    mode: &'a str,
    policy: &'a std::path::Path,
    artifact_root: &'a std::path::Path,
    evidence_root: &'a std::path::Path,
    fee_bps: f64,
    spread_bps: f64,
) -> PaperRiskGateOptions<'a> {
    risk_options_with_contract_version(
        mode,
        policy,
        artifact_root,
        evidence_root,
        fee_bps,
        spread_bps,
        RiskGateContractVersion::V1,
    )
}

fn risk_options_with_contract_version<'a>(
    mode: &'a str,
    policy: &'a std::path::Path,
    artifact_root: &'a std::path::Path,
    evidence_root: &'a std::path::Path,
    fee_bps: f64,
    spread_bps: f64,
    contract_version: RiskGateContractVersion,
) -> PaperRiskGateOptions<'a> {
    PaperRiskGateOptions {
        mode,
        policy,
        artifact_root,
        evidence_root,
        contract_version,
        fee_bps,
        spread_bps,
        db_before_artifact_path: None,
        db_before_artifact_sha256: None,
        db_after_artifact_path: None,
        db_after_artifact_sha256: None,
        health_artifact_path: None,
        health_artifact_sha256: None,
    }
}

fn sha256_prefixed(path: &std::path::Path) -> String {
    format!(
        "sha256:{}",
        hex::encode(Sha256::digest(std::fs::read(path).unwrap()))
    )
}

#[test]
fn paper_help_lists_risk_flags() {
    let output = Command::new(side_cli_binary())
        .args(["paper", "--help"])
        .output()
        .unwrap();
    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("--paper-risk-mode"));
    assert!(stdout.contains("--risk-gate-policy"));
    assert!(stdout.contains("--risk-gate-artifact-root"));
    assert!(stdout.contains("--paper-risk-evidence-root"));
    assert!(stdout.contains("--paper-fee-bps"));
    assert!(stdout.contains("--paper-spread-bps"));
    assert!(stdout.contains("--risk-gate-contract-version"));
}

#[test]
fn paper_help_lists_runtime_accounting_mode_flag() {
    let output = Command::new(side_cli_binary())
        .args(["paper", "--help"])
        .output()
        .unwrap();
    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("--runtime-accounting-mode"));
}

#[test]
fn paper_rejects_unknown_runtime_accounting_mode() {
    let output = Command::new(side_cli_binary())
        .args(["paper", "--once", "--runtime-accounting-mode", "net_now"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--runtime-accounting-mode must be legacy_gross or estimated_net"));
}

#[test]
fn paper_runtime_accounting_mode_arg_accepts_supported_values() {
    assert_eq!(
        parse_runtime_accounting_mode_arg("legacy_gross").unwrap(),
        RuntimeAccountingMode::LegacyGross
    );
    assert_eq!(
        parse_runtime_accounting_mode_arg("estimated_net").unwrap(),
        RuntimeAccountingMode::EstimatedNet
    );
}

#[test]
fn paper_rejects_risk_policy_without_mode() {
    let output = Command::new(side_cli_binary())
        .args(["paper", "--once", "--risk-gate-policy", "policy.json"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("paper risk flags require --paper-risk-mode observe or apply"));
}

#[test]
fn paper_rejects_apply_without_artifact_root() {
    let output = Command::new(side_cli_binary())
        .args([
            "paper",
            "--once",
            "--paper-risk-mode",
            "apply",
            "--risk-gate-policy",
            "policy.json",
            "--paper-risk-evidence-root",
            "reports/v6.2/paper_risk",
        ])
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--risk-gate-artifact-root is required"));
}

#[test]
fn paper_rejects_v2_observe_before_output() {
    let output = Command::new(side_cli_binary())
        .args([
            "paper",
            "--once",
            "--paper-risk-mode",
            "observe",
            "--risk-gate-contract-version",
            "v2",
            "--risk-gate-policy",
            "policy.json",
            "--risk-gate-artifact-root",
            "reports/risk-contract-v2/paper-runtime-adoption/test",
            "--paper-risk-evidence-root",
            "reports/risk-contract-v2/paper-runtime-adoption/evidence",
        ])
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--risk-gate-contract-version v2 requires --paper-risk-mode apply"));
}

#[test]
fn paper_observe_helper_writes_evidence_without_runtime_sizing() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("paper.policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    let evidence_root = tmp.path().join("paper-evidence");
    write_paper_policy(&policy, "cap");
    let config: PaperConfig = serde_json::from_str(
        r#"{
          "slots": [
            {
              "asset": "USD/JPY",
              "strategy_name": "keltner",
              "params": {"ema_period": 20, "atr_period": 14},
              "aux_source": {"id": "yf:^VIX"},
              "timeframe": "1h",
              "leverage": 500
            }
          ],
          "initial_capital": 10000
        }"#,
    )
    .unwrap();

    let evidence_paths = evaluate_paper_risk_once(
        &config,
        risk_options("observe", &policy, &artifact_root, &evidence_root, 0.0, 0.0),
    )
    .unwrap();

    assert_eq!(evidence_paths.len(), 1);
    let evidence: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&evidence_paths[0]).unwrap()).unwrap();
    assert_eq!(evidence["risk_mode"], "observe");
    assert_eq!(evidence["decision_class"], "cap");
    assert_eq!(evidence["runtime_sizing_applied"], false);
    assert_eq!(evidence["cap_application_status"], "deferred_with_reason");
    assert!(evidence.get("risk_contract_version").is_none());
    assert!(evidence.get("validator_result_schema_version").is_none());
    assert_eq!(
        evidence["paper_fee_model_status"],
        "missing_or_zero_cost_model"
    );
    assert_eq!(evidence["cost_model_schema_version"], 1);
    assert_eq!(evidence["fee_bps"], 0.0);
    assert_eq!(evidence["spread_bps"], 0.0);
    assert_eq!(evidence["cost_basis"], "paper_notional_round_trip_bps");
    assert_eq!(evidence["gross_pnl"], 0.0);
    assert_eq!(evidence["estimated_cost"], 0.0);
    assert_eq!(evidence["estimated_net_pnl"], 0.0);
    assert_eq!(evidence["estimated_net_pnl_claim_allowed"], false);
    assert_eq!(evidence["parity_claim_allowed"], false);
    assert_eq!(evidence["alpha_claim_allowed"], false);
    assert_eq!(evidence["claim_block_reason"], "missing_or_zero_cost_model");
    assert!(evidence["candidate_id"]
        .as_str()
        .unwrap()
        .starts_with("paper."));
    assert_eq!(evidence["position_mutation"], false);
}

#[test]
fn paper_observe_helper_writes_explicit_nonzero_cost_evidence() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("paper.policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    let evidence_root = tmp.path().join("paper-evidence");
    write_paper_policy(&policy, "size");
    let config: PaperConfig = serde_json::from_str(
        r#"{
          "slots": [{
            "asset": "USD/JPY",
            "strategy_name": "keltner",
            "params": {"ema_period": 20, "atr_period": 14},
            "aux_source": {"id": "yf:^VIX"},
            "timeframe": "1h",
            "leverage": 500
          }],
          "initial_capital": 2000
        }"#,
    )
    .unwrap();

    let evidence_paths = evaluate_paper_risk_once(
        &config,
        risk_options("observe", &policy, &artifact_root, &evidence_root, 1.5, 0.5),
    )
    .unwrap();

    let evidence: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&evidence_paths[0]).unwrap()).unwrap();
    assert_eq!(
        evidence["paper_fee_model_status"],
        "explicit_nonzero_cost_model"
    );
    assert_eq!(evidence["fee_bps"], 1.5);
    assert_eq!(evidence["spread_bps"], 0.5);
    assert_eq!(evidence["cost_notional"], 1_000_000.0);
    assert_eq!(evidence["gross_pnl"], 0.0);
    assert_eq!(evidence["estimated_cost"], 200.0);
    assert_eq!(evidence["estimated_net_pnl"], -200.0);
    assert_eq!(evidence["estimated_net_pnl_claim_allowed"], true);
    assert_eq!(evidence["parity_claim_allowed"], false);
    assert_eq!(evidence["alpha_claim_allowed"], false);
    assert_eq!(
        evidence["claim_block_reason"],
        "runtime_net_pnl_not_integrated"
    );
    assert!(evidence["cost_model_fingerprint"]
        .as_str()
        .unwrap()
        .starts_with("sha256:"));
    assert_eq!(evidence["runtime_sizing_applied"], false);
    assert_eq!(evidence["position_mutation"], false);
}

#[test]
fn paper_evidence_records_db_and_health_artifact_linkage() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("paper.policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    let evidence_root = tmp.path().join("paper-evidence");
    let db_before = tmp.path().join("db_before.json");
    let db_after = tmp.path().join("db_after.json");
    let health = tmp.path().join("health.json");
    std::fs::write(&db_before, "{\"db_exists\":false}\n").unwrap();
    std::fs::write(&db_after, "[{\"db_exists\":1}]\n").unwrap();
    std::fs::write(&health, "{\"status\":\"running\"}\n").unwrap();
    let db_before_sha = sha256_prefixed(&db_before);
    let db_after_sha = sha256_prefixed(&db_after);
    let health_sha = sha256_prefixed(&health);
    write_paper_policy(&policy, "size");
    let config: PaperConfig = serde_json::from_str(
        r#"{
          "slots": [{
            "asset": "USD/JPY",
            "strategy_name": "keltner",
            "params": {"ema_period": 20, "atr_period": 14},
            "timeframe": "1h",
            "leverage": 500
          }],
          "initial_capital": 2000
        }"#,
    )
    .unwrap();

    let evidence_paths = evaluate_paper_risk_once(
        &config,
        PaperRiskGateOptions {
            mode: "observe",
            policy: &policy,
            artifact_root: &artifact_root,
            evidence_root: &evidence_root,
            contract_version: RiskGateContractVersion::V1,
            fee_bps: 1.5,
            spread_bps: 0.5,
            db_before_artifact_path: Some(&db_before),
            db_before_artifact_sha256: Some(db_before_sha.clone()),
            db_after_artifact_path: Some(&db_after),
            db_after_artifact_sha256: Some(db_after_sha.clone()),
            health_artifact_path: Some(&health),
            health_artifact_sha256: Some(health_sha.clone()),
        },
    )
    .unwrap();

    let evidence: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&evidence_paths[0]).unwrap()).unwrap();
    assert_eq!(
        evidence["db_before_artifact_path"],
        db_before.display().to_string()
    );
    assert_eq!(evidence["db_before_artifact_sha256"], db_before_sha);
    assert_eq!(
        evidence["db_after_artifact_path"],
        db_after.display().to_string()
    );
    assert_eq!(evidence["db_after_artifact_sha256"], db_after_sha);
    assert_eq!(
        evidence["health_artifact_path"],
        health.display().to_string()
    );
    assert_eq!(evidence["health_artifact_sha256"], health_sha);
}

#[test]
fn paper_apply_helper_records_kill_as_stopped_without_position_mutation() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("paper.policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    let evidence_root = tmp.path().join("paper-evidence");
    write_paper_policy(&policy, "kill");
    let config: PaperConfig = serde_json::from_str(
        r#"{
          "slots": [
            {
              "asset": "USD/JPY",
              "strategy_name": "keltner",
              "params": {"ema_period": 20, "atr_period": 14},
              "aux_source": {"id": "yf:^VIX"},
              "timeframe": "1h",
              "leverage": 500
            }
          ],
          "initial_capital": 10000
        }"#,
    )
    .unwrap();

    let evidence_paths = evaluate_paper_risk_once(
        &config,
        risk_options("apply", &policy, &artifact_root, &evidence_root, 0.0, 0.0),
    )
    .unwrap();

    assert_eq!(evidence_paths.len(), 1);
    let evidence: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&evidence_paths[0]).unwrap()).unwrap();
    assert_eq!(evidence["risk_mode"], "apply");
    assert_eq!(evidence["decision_class"], "kill");
    assert_eq!(evidence["execution_state"], "stopped");
    assert_eq!(evidence["position_mutation"], false);
    assert_eq!(evidence["runtime_sizing_applied"], false);
}

#[test]
fn paper_apply_kill_result_blocks_tick_mutation_phase() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("paper.policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    let evidence_root = tmp.path().join("paper-evidence");
    write_paper_policy(&policy, "kill");
    let config: PaperConfig = serde_json::from_str(
        r#"{
          "slots": [
            {
              "asset": "USD/JPY",
              "strategy_name": "keltner",
              "params": {"ema_period": 20, "atr_period": 14},
              "aux_source": {"id": "yf:^VIX"},
              "timeframe": "1h",
              "leverage": 500
            }
          ],
          "initial_capital": 10000
        }"#,
    )
    .unwrap();

    let result = evaluate_paper_risk_once_with_result(
        &config,
        risk_options("apply", &policy, &artifact_root, &evidence_root, 0.0, 0.0),
    )
    .unwrap();

    assert_eq!(result.evidence_paths.len(), 1);
    assert!(!result.should_run_tick);
}

#[test]
fn paper_apply_cap_result_continues_with_runtime_size_override() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("paper.policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    let evidence_root = tmp.path().join("paper-evidence");
    write_paper_policy(&policy, "cap");
    let config: PaperConfig = serde_json::from_str(
        r#"{
          "slots": [
            {
              "asset": "USD/JPY",
              "strategy_name": "keltner",
              "params": {"ema_period": 20, "atr_period": 14},
              "aux_source": {"id": "yf:^VIX"},
              "timeframe": "1h",
              "leverage": 500
            }
          ],
          "initial_capital": 10000
        }"#,
    )
    .unwrap();

    let result = evaluate_paper_risk_once_with_result(
        &config,
        risk_options("apply", &policy, &artifact_root, &evidence_root, 1.5, 0.5),
    )
    .unwrap();

    assert_eq!(result.evidence_paths.len(), 1);
    assert!(result.should_run_tick);
    assert_eq!(result.runtime_size_overrides.len(), 1);
    assert_eq!(
        result.runtime_size_overrides[0].slot_id,
        "USD/JPY/keltner/^VIX#1"
    );
    assert_eq!(result.runtime_size_overrides[0].effective_size, 0.25);

    let evidence: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&result.evidence_paths[0]).unwrap()).unwrap();
    assert_eq!(evidence["risk_mode"], "apply");
    assert_eq!(evidence["decision_class"], "cap");
    assert_eq!(evidence["execution_state"], "continued");
    assert_eq!(evidence["runtime_sizing_applied"], true);
    assert_eq!(evidence["requested_size"], 10000.0);
    assert_eq!(evidence["allowed_size"], 0.25);
    assert_eq!(evidence["actual_effective_size"], 0.25);
    assert_eq!(evidence["would_effective_size"], 0.25);
    assert_eq!(evidence["cap_application_status"], "applied");
    assert_eq!(
        evidence["cap_runtime_sizing_dependency_status"],
        "pnl_source_independent_proven"
    );
    assert_eq!(evidence["cap_depends_on_runtime_pnl"], false);
    assert_eq!(evidence["cap_runtime_sizing_claim_allowed"], true);
    assert!(evidence["cap_runtime_sizing_claim_block_reason"].is_null());
}

#[test]
fn paper_apply_v2_cap_result_writes_v2_candidate_artifact_and_evidence_proof() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("paper.policy.json");
    let artifact_root = tmp.path().join("risk-artifacts-v2");
    let evidence_root = tmp.path().join("paper-evidence-v2");
    write_paper_policy(&policy, "cap");
    let config: PaperConfig = serde_json::from_str(
        r#"{
          "slots": [
            {
              "asset": "USD/JPY",
              "strategy_name": "keltner",
              "params": {"ema_period": 20, "atr_period": 14},
              "aux_source": {"id": "yf:^VIX"},
              "timeframe": "1h",
              "leverage": 500
            }
          ],
          "initial_capital": 10000
        }"#,
    )
    .unwrap();

    let result = evaluate_paper_risk_once_with_result(
        &config,
        risk_options_with_contract_version(
            "apply",
            &policy,
            &artifact_root,
            &evidence_root,
            1.5,
            0.5,
            RiskGateContractVersion::V2,
        ),
    )
    .unwrap();

    assert_eq!(result.evidence_paths.len(), 1);
    assert!(result.should_run_tick);
    assert_eq!(result.runtime_size_overrides.len(), 1);
    assert_eq!(result.runtime_size_overrides[0].effective_size, 0.25);

    let evidence: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&result.evidence_paths[0]).unwrap()).unwrap();
    assert_eq!(evidence["risk_mode"], "apply");
    assert_eq!(evidence["decision_class"], "cap");
    assert_eq!(evidence["execution_state"], "continued");
    assert_eq!(evidence["requested_size"], 10000.0);
    assert_eq!(
        evidence["requested_size_basis"],
        "unit_paper_slot_allocation"
    );
    assert_eq!(evidence["allowed_size"], 0.25);
    assert_eq!(evidence["actual_effective_size"], 0.25);
    assert_eq!(evidence["runtime_sizing_applied"], true);
    assert_eq!(evidence["cap_application_status"], "applied");
    assert_eq!(evidence["risk_contract_schema_version"], "risk_contract.v2");
    assert_eq!(evidence["risk_contract_version"], "v2");
    assert_eq!(
        evidence["validator_result_schema_version"],
        "risk_contract_validator_result.v2"
    );
    assert_eq!(
        evidence["validated_schema_ref"],
        "risk/contracts/v2/risk_contract_v2.schema.json"
    );
    assert_eq!(evidence["validator"], "scripts/validate_risk_contract.py");

    let candidate_path = evidence["candidate_artifact_path"].as_str().unwrap();
    let candidate: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(candidate_path).unwrap()).unwrap();
    assert_eq!(
        candidate["candidate_schema_version"],
        "risk_contract.v2.candidate.v1"
    );
    assert_eq!(candidate["surface"]["runtime_surface"], "paper");
    assert_eq!(candidate["surface"]["analysis_scope"], "none");
    assert_eq!(
        candidate["sizing"]["requested_size_basis"],
        "unit_paper_slot_allocation"
    );
    assert_eq!(
        candidate["surface_payload"]["allocation_source"],
        "PaperConfig::allocations"
    );
    assert_eq!(candidate["surface_payload"]["paper_risk_mode"], "apply");

    let artifact_path = evidence["decision_artifact_path"].as_str().unwrap();
    let artifact: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(artifact_path).unwrap()).unwrap();
    assert_eq!(artifact["schema_version"], "risk_contract.v2");
    assert_eq!(artifact["contract_version"], "v2");
    assert_eq!(artifact["candidate"]["surface"]["runtime_surface"], "paper");
    assert_eq!(
        artifact["candidate"]["sizing"]["requested_size_basis"],
        "unit_paper_slot_allocation"
    );
    assert_eq!(artifact["application"]["execution_state"], "continued");
    assert_eq!(artifact["application"]["application_status"], "applied");
    assert_eq!(artifact["application"]["runtime_sizing_applied"], true);
    assert_eq!(artifact["application"]["metrics_rescaled"], false);
    assert_eq!(
        artifact["trace"]["validator_result_schema_version"],
        "risk_contract_validator_result.v2"
    );
}

#[test]
fn paper_v2_rejects_protected_artifact_and_evidence_roots_before_output() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("paper.policy.json");
    let safe_artifact_root = tmp.path().join("risk-artifacts-v2");
    let safe_evidence_root = tmp.path().join("paper-evidence-v2");
    write_paper_policy(&policy, "cap");
    let config: PaperConfig = serde_json::from_str(
        r#"{
          "slots": [{
            "asset": "USD/JPY",
            "strategy_name": "keltner",
            "params": {"ema_period": 20, "atr_period": 14},
            "timeframe": "1h",
            "leverage": 500
          }],
          "initial_capital": 10000
        }"#,
    )
    .unwrap();

    let protected_artifact_root = PathBuf::from("risk/contracts/v2/paper-runtime-adoption-test");
    let artifact_err = evaluate_paper_risk_once_with_result(
        &config,
        risk_options_with_contract_version(
            "apply",
            &policy,
            &protected_artifact_root,
            &safe_evidence_root,
            1.5,
            0.5,
            RiskGateContractVersion::V2,
        ),
    )
    .unwrap_err();
    assert!(artifact_err
        .to_string()
        .contains("unsafe v2 artifact_root: protected contract root"));
    assert!(!safe_evidence_root.exists());

    let protected_evidence_root = PathBuf::from(".planning/paper-runtime-adoption-test");
    let evidence_err = evaluate_paper_risk_once_with_result(
        &config,
        risk_options_with_contract_version(
            "apply",
            &policy,
            &safe_artifact_root,
            &protected_evidence_root,
            1.5,
            0.5,
            RiskGateContractVersion::V2,
        ),
    )
    .unwrap_err();
    assert!(evidence_err
        .to_string()
        .contains("unsafe v2 evidence_root: protected planning root"));
    assert!(!safe_artifact_root.exists());
}

#[test]
fn paper_apply_cap_result_clamps_expanding_policy_to_requested_size_before_override() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("paper.policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    let evidence_root = tmp.path().join("paper-evidence");
    write_paper_policy_with_cap_allowed_size(&policy, 10_001.0);
    let config: PaperConfig = serde_json::from_str(
        r#"{
          "slots": [
            {
              "asset": "USD/JPY",
              "strategy_name": "keltner",
              "params": {"ema_period": 20, "atr_period": 14},
              "aux_source": {"id": "yf:^VIX"},
              "timeframe": "1h",
              "leverage": 500
            }
          ],
          "initial_capital": 10000
        }"#,
    )
    .unwrap();

    let result = evaluate_paper_risk_once_with_result(
        &config,
        risk_options("apply", &policy, &artifact_root, &evidence_root, 1.5, 0.5),
    )
    .unwrap();

    assert_eq!(result.runtime_size_overrides.len(), 1);
    assert_eq!(result.runtime_size_overrides[0].effective_size, 10000.0);
    let evidence: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&result.evidence_paths[0]).unwrap()).unwrap();
    assert_eq!(evidence["decision_class"], "cap");
    assert_eq!(evidence["requested_size"], 10000.0);
    assert_eq!(evidence["allowed_size"], 10000.0);
    assert_eq!(evidence["actual_effective_size"], 10000.0);
    assert_eq!(evidence["runtime_sizing_applied"], true);
}

#[test]
fn paper_apply_cap_result_with_estimated_net_config_keeps_cap_evidence_independent() {
    let tmp = TempDir::new().unwrap();
    let policy = tmp.path().join("paper.policy.json");
    let artifact_root = tmp.path().join("risk-artifacts");
    let evidence_root = tmp.path().join("paper-evidence");
    write_paper_policy(&policy, "cap");
    let config: PaperConfig = serde_json::from_str(
        r#"{
          "slots": [
            {
              "asset": "USD/JPY",
              "strategy_name": "keltner",
              "params": {"ema_period": 20, "atr_period": 14},
              "aux_source": {"id": "yf:^VIX"},
              "timeframe": "1h",
              "leverage": 500
            }
          ],
          "initial_capital": 10000,
          "runtime_accounting_mode": "estimated_net"
        }"#,
    )
    .unwrap();
    assert_eq!(
        config.runtime_accounting_mode,
        RuntimeAccountingMode::EstimatedNet
    );

    let result = evaluate_paper_risk_once_with_result(
        &config,
        risk_options("apply", &policy, &artifact_root, &evidence_root, 1.5, 0.5),
    )
    .unwrap();

    assert_eq!(result.evidence_paths.len(), 1);
    assert!(result.should_run_tick);
    assert_eq!(result.runtime_size_overrides.len(), 1);
    assert_eq!(
        result.runtime_size_overrides[0].slot_id,
        "USD/JPY/keltner/^VIX#1"
    );
    assert_eq!(result.runtime_size_overrides[0].effective_size, 0.25);

    let evidence: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&result.evidence_paths[0]).unwrap()).unwrap();
    assert_eq!(evidence["decision_class"], "cap");
    assert_eq!(evidence["runtime_sizing_applied"], true);
    assert_eq!(evidence["actual_effective_size"], 0.25);
    assert_eq!(evidence["cap_application_status"], "applied");
    assert_eq!(
        evidence["cap_runtime_sizing_dependency_status"],
        "pnl_source_independent_proven"
    );
    assert_eq!(evidence["cap_depends_on_runtime_pnl"], false);
    assert_eq!(evidence["cap_runtime_sizing_claim_allowed"], true);
    assert!(evidence["cap_runtime_sizing_claim_block_reason"].is_null());
}

#[test]
fn paper_rejects_negative_cost_bps() {
    let output = Command::new(side_cli_binary())
        .args(["paper", "--once", "--paper-fee-bps", "-0.1"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--paper-fee-bps must be finite and non-negative"));
}
