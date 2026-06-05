use anyhow::Context;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RiskGateExecutionState {
    Stopped,
    Continued,
    GateError,
}

pub const RISK_GATE_VALIDATION_STATUS: &str = "validated";
pub const RISK_GATE_VALIDATOR: &str = "scripts/validate_risk_contract.py";
pub const RISK_GATE_SCHEMA_REF: &str = "risk/contracts/v1/risk_contract_v1.schema.json";
pub const RISK_GATE_APPLICATION_STATUS_DEFERRED: &str = "deferred";

#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum RiskGateContractVersion {
    V1,
    V2,
}

impl RiskGateExecutionState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Stopped => "stopped",
            Self::Continued => "continued",
            Self::GateError => "gate_error",
        }
    }
}

impl RiskGateContractVersion {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::V1 => "v1",
            Self::V2 => "v2",
        }
    }
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct RiskGateSummary {
    pub decision_class: String,
    pub allowed_size: f64,
    pub binding_rule: String,
    pub fail_close_reason: String,
    pub policy_version: String,
    pub candidate_id: String,
    pub artifact_path: String,
    #[serde(default)]
    pub schema_version: Option<String>,
    #[serde(default)]
    pub contract_version: Option<String>,
    #[serde(default)]
    pub validator_result_schema_version: Option<String>,
    #[serde(default)]
    pub validated_schema_ref: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RiskGateSlotOutput {
    pub decision_class: String,
    pub allowed_size: f64,
    pub binding_rule: String,
    pub fail_close_reason: String,
    pub policy_version: String,
    pub candidate_id: String,
    pub artifact_path: String,
    pub execution_state: String,
    pub validation_status: String,
    pub validator: String,
    pub schema_ref: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub schema_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub contract_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validator_result_schema_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validated_schema_ref: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub application_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub runtime_sizing_applied: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sizing_effect: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub requested_size: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub requested_size_basis: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub effective_size: Option<f64>,
}

pub fn execution_state_for(decision_class: &str) -> anyhow::Result<RiskGateExecutionState> {
    match decision_class {
        "block" | "kill" | "reject" => Ok(RiskGateExecutionState::Stopped),
        "cap" | "size" => Ok(RiskGateExecutionState::Continued),
        other => anyhow::bail!("unknown risk gate decision_class: {other}"),
    }
}

impl RiskGateSummary {
    pub fn execution_state(&self) -> anyhow::Result<RiskGateExecutionState> {
        execution_state_for(&self.decision_class)
    }

    pub fn to_slot_output(&self) -> anyhow::Result<RiskGateSlotOutput> {
        let schema_ref = self
            .validated_schema_ref
            .clone()
            .unwrap_or_else(|| RISK_GATE_SCHEMA_REF.to_string());
        Ok(RiskGateSlotOutput {
            decision_class: self.decision_class.clone(),
            allowed_size: self.allowed_size,
            binding_rule: self.binding_rule.clone(),
            fail_close_reason: self.fail_close_reason.clone(),
            policy_version: self.policy_version.clone(),
            candidate_id: self.candidate_id.clone(),
            artifact_path: self.artifact_path.clone(),
            execution_state: self.execution_state()?.as_str().to_string(),
            validation_status: RISK_GATE_VALIDATION_STATUS.to_string(),
            validator: RISK_GATE_VALIDATOR.to_string(),
            schema_ref,
            schema_version: self.schema_version.clone(),
            contract_version: self.contract_version.clone(),
            validator_result_schema_version: self.validator_result_schema_version.clone(),
            validated_schema_ref: self.validated_schema_ref.clone(),
            application_status: (self.decision_class == "cap")
                .then(|| RISK_GATE_APPLICATION_STATUS_DEFERRED.to_string()),
            runtime_sizing_applied: (self.decision_class == "cap").then_some(false),
            sizing_effect: (self.decision_class == "cap").then(|| "none".to_string()),
            requested_size: None,
            requested_size_basis: None,
            effective_size: None,
        })
    }
}

pub struct RiskGateInvocation<'a> {
    pub policy: &'a Path,
    pub candidate: &'a Path,
    pub evidence: &'a Path,
    pub context: &'a Path,
    pub out: &'a Path,
    pub contract_version: RiskGateContractVersion,
}

fn find_repo_root() -> anyhow::Result<PathBuf> {
    let cwd = std::env::current_dir().context("failed to read current directory")?;
    for dir in cwd.ancestors() {
        if dir.join("pyproject.toml").is_file()
            && dir.join("scripts/evaluate_risk_gate.py").is_file()
            && dir
                .join("risk/contracts/v1/risk_contract_v1.schema.json")
                .is_file()
        {
            return Ok(dir.to_path_buf());
        }
    }
    anyhow::bail!(
        "failed to find repo root for risk gate wrapper from {}",
        cwd.display()
    )
}

fn parse_risk_gate_summary(stdout: &[u8]) -> anyhow::Result<RiskGateSummary> {
    serde_json::from_slice::<RiskGateSummary>(stdout)
        .context("failed to parse risk gate summary JSON")
}

fn summary_from_output(output: std::process::Output) -> anyhow::Result<RiskGateSummary> {
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        anyhow::bail!(
            "risk gate wrapper exited with status {}: {}",
            output.status,
            stderr.trim()
        );
    }
    let summary = parse_risk_gate_summary(&output.stdout)?;
    summary.execution_state()?;
    Ok(summary)
}

pub fn evaluate_risk_gate(invocation: RiskGateInvocation<'_>) -> anyhow::Result<RiskGateSummary> {
    let repo_root = find_repo_root()?;
    let output = std::process::Command::new("uv")
        .current_dir(&repo_root)
        .arg("run")
        .arg("python")
        .arg("scripts/evaluate_risk_gate.py")
        .arg("--policy")
        .arg(invocation.policy)
        .arg("--candidate")
        .arg(invocation.candidate)
        .arg("--evidence")
        .arg(invocation.evidence)
        .arg("--context")
        .arg(invocation.context)
        .arg("--out")
        .arg(invocation.out)
        .arg("--contract-version")
        .arg(invocation.contract_version.as_str())
        .output()
        .context("failed to spawn risk gate wrapper")?;
    summary_from_output(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn risk_gate_decision_mapping_stop_classes() {
        for decision_class in ["block", "kill", "reject"] {
            assert_eq!(
                execution_state_for(decision_class).unwrap(),
                RiskGateExecutionState::Stopped
            );
        }
        assert_eq!(RiskGateExecutionState::Stopped.as_str(), "stopped");
    }

    #[test]
    fn risk_gate_decision_mapping_continue_classes() {
        for decision_class in ["cap", "size"] {
            assert_eq!(
                execution_state_for(decision_class).unwrap(),
                RiskGateExecutionState::Continued
            );
        }
        assert_eq!(RiskGateExecutionState::Continued.as_str(), "continued");
        assert_eq!(RiskGateExecutionState::GateError.as_str(), "gate_error");
    }

    #[test]
    fn risk_gate_decision_mapping_unknown_class_errors() {
        let err = execution_state_for("halt").unwrap_err();
        assert!(format!("{err:#}").contains("unknown risk gate decision_class: halt"));
    }

    #[test]
    fn risk_gate_summary_parses_required_fields() {
        let summary: RiskGateSummary = serde_json::from_str(
            r#"{
              "decision_class": "size",
              "allowed_size": 1.0,
              "binding_rule": "rule.size",
              "fail_close_reason": "insufficient_validation_power",
              "policy_version": "risk-policy.v1.test",
              "candidate_id": "scan_edges.USDJPY.1h.edge0.m0.long.h1",
              "artifact_path": "reports/risk-gate/out.json"
            }"#,
        )
        .unwrap();

        assert_eq!(summary.decision_class, "size");
        assert_eq!(summary.allowed_size, 1.0);
        assert_eq!(summary.binding_rule, "rule.size");
        assert_eq!(summary.fail_close_reason, "insufficient_validation_power");
        assert_eq!(summary.policy_version, "risk-policy.v1.test");
        assert_eq!(
            summary.candidate_id,
            "scan_edges.USDJPY.1h.edge0.m0.long.h1"
        );
        assert_eq!(summary.artifact_path, "reports/risk-gate/out.json");
        assert_eq!(
            summary.execution_state().unwrap(),
            RiskGateExecutionState::Continued
        );
    }

    #[test]
    fn risk_gate_summary_to_slot_output_adds_public_validator_proof() {
        let summary = RiskGateSummary {
            decision_class: "size".to_string(),
            allowed_size: 1.0,
            binding_rule: "phase135.size".to_string(),
            fail_close_reason: "insufficient_validation_power".to_string(),
            policy_version: "risk-policy.v1.test".to_string(),
            candidate_id: "scan_edges.USDJPY.1h.edge0.m0.long.h1".to_string(),
            artifact_path: "reports/risk-gate/out.json".to_string(),
            schema_version: None,
            contract_version: None,
            validator_result_schema_version: None,
            validated_schema_ref: None,
        };

        let output = summary.to_slot_output().unwrap();

        assert_eq!(output.decision_class, "size");
        assert_eq!(output.allowed_size, 1.0);
        assert_eq!(output.binding_rule, "phase135.size");
        assert_eq!(output.fail_close_reason, "insufficient_validation_power");
        assert_eq!(output.policy_version, "risk-policy.v1.test");
        assert_eq!(output.candidate_id, "scan_edges.USDJPY.1h.edge0.m0.long.h1");
        assert_eq!(output.artifact_path, "reports/risk-gate/out.json");
        assert_eq!(output.execution_state, "continued");
        assert_eq!(output.validation_status, "validated");
        assert_eq!(output.validator, "scripts/validate_risk_contract.py");
        assert_eq!(
            output.schema_ref,
            "risk/contracts/v1/risk_contract_v1.schema.json"
        );
        assert_eq!(output.application_status, None);
    }

    #[test]
    fn risk_gate_cap_slot_output_marks_application_deferred() {
        let summary = RiskGateSummary {
            decision_class: "cap".to_string(),
            allowed_size: 0.25,
            binding_rule: "phase135.cap".to_string(),
            fail_close_reason: "insufficient_validation_power".to_string(),
            policy_version: "risk-policy.v1.test".to_string(),
            candidate_id: "scan_edges.USDJPY.1h.edge0.m0.long.h1".to_string(),
            artifact_path: "reports/risk-gate/out.json".to_string(),
            schema_version: None,
            contract_version: None,
            validator_result_schema_version: None,
            validated_schema_ref: None,
        };

        let output = summary.to_slot_output().unwrap();

        assert_eq!(output.execution_state, "continued");
        assert_eq!(output.application_status.as_deref(), Some("deferred"));
    }

    #[test]
    fn risk_gate_malformed_stdout_parse_failure() {
        let err = parse_risk_gate_summary(b"{not-json").unwrap_err();

        assert!(format!("{err:#}").contains("failed to parse risk gate summary JSON"));
    }

    #[test]
    fn risk_gate_nonzero_subprocess_status_errors() {
        #[cfg(unix)]
        let status = {
            use std::os::unix::process::ExitStatusExt;
            std::process::ExitStatus::from_raw(1 << 8)
        };

        #[cfg(not(unix))]
        let status = std::process::Command::new("cmd")
            .arg("/C")
            .arg("exit 1")
            .status()
            .unwrap();

        let output = std::process::Output {
            status,
            stdout: b"{}".to_vec(),
            stderr: b"forced wrapper failure".to_vec(),
        };
        let err = summary_from_output(output).unwrap_err();

        assert!(format!("{err:#}").contains("forced wrapper failure"));
    }
}
