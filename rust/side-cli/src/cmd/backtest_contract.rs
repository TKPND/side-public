use std::collections::HashMap;
use std::path::Path;

use anyhow::{bail, Context};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::cmd::backtest_risk_adapter::{BACKTEST_REQUESTED_SIZE, BACKTEST_REQUESTED_SIZE_BASIS};
use crate::cmd::risk_gate::{RiskGateExecutionState, RiskGateSlotOutput, RiskGateSummary};

pub const BACKTEST_RESULT_SCHEMA_VERSION: &str = "side-cli.backtest.result.v1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TodEdgeParams {
    pub entry_minute: u16,
    pub direction: String,
    pub hold_h: u8,
}

#[derive(Debug, Clone, Serialize)]
pub struct BacktestMetrics {
    pub profit_factor: Value,
    pub num_trades: usize,
    pub total_return: Value,
}

#[derive(Debug, Clone, Serialize)]
pub struct BacktestResultOutput {
    pub schema_version: &'static str,
    pub risk_gate_enabled: bool,
    pub run_status: &'static str,
    pub asset: String,
    pub strategy: &'static str,
    pub timeframe: String,
    pub params: TodEdgeParams,
    pub data_ref: String,
    pub data_fingerprint: String,
    pub fee_bps: f64,
    pub metrics: Option<BacktestMetrics>,
    pub risk_gate: Option<RiskGateSlotOutput>,
    pub cap_parity: CapParityOutput,
    pub backtest_execution: BacktestExecutionOutput,
}

#[derive(Debug, Clone, Serialize)]
pub struct CapParityOutput {
    pub status: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub struct BacktestExecutionOutput {
    pub status: &'static str,
    pub reason: Option<&'static str>,
    pub backtest_invocation_count: usize,
}

#[derive(Debug, Clone)]
pub struct BacktestRuntimeSizing {
    pub requested_size: f64,
    pub requested_size_basis: &'static str,
    pub allowed_size: f64,
    pub effective_size: f64,
    pub application_status: &'static str,
    pub runtime_sizing_applied: bool,
    pub sizing_effect: &'static str,
}

pub fn parse_tod_edge_params_json(input: &str) -> anyhow::Result<TodEdgeParams> {
    let value: Value = serde_json::from_str(input).context("params JSON parse failed")?;
    let object = value
        .as_object()
        .ok_or_else(|| anyhow::anyhow!("params must be a JSON object"))?;

    for key in object.keys() {
        if !matches!(key.as_str(), "entry_minute" | "direction" | "hold_h") {
            bail!("unknown tod_edge params key: {key}");
        }
    }

    let entry_minute = object
        .get("entry_minute")
        .ok_or_else(|| anyhow::anyhow!("missing entry_minute"))?
        .as_u64()
        .ok_or_else(|| anyhow::anyhow!("entry_minute must be an integer"))?;
    anyhow::ensure!(
        entry_minute <= 1439,
        "entry_minute must be in 0..=1439, got {entry_minute}"
    );

    let direction = object
        .get("direction")
        .ok_or_else(|| anyhow::anyhow!("missing direction"))?
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("direction must be a string"))?;
    anyhow::ensure!(
        matches!(direction, "long" | "short"),
        "direction must be exactly 'long' or 'short', got {direction:?}"
    );

    let hold_h = object
        .get("hold_h")
        .ok_or_else(|| anyhow::anyhow!("missing hold_h"))?
        .as_u64()
        .ok_or_else(|| anyhow::anyhow!("hold_h must be an integer"))?;
    anyhow::ensure!(
        (1..=9).contains(&hold_h),
        "hold_h must be in 1..=9, got {hold_h}"
    );

    Ok(TodEdgeParams {
        entry_minute: entry_minute as u16,
        direction: direction.to_string(),
        hold_h: hold_h as u8,
    })
}

pub fn tod_edge_strategy_params(params: &TodEdgeParams) -> HashMap<String, Value> {
    HashMap::from([
        ("entry_minute".to_string(), Value::from(params.entry_minute)),
        (
            "direction".to_string(),
            Value::from(params.direction.clone()),
        ),
        ("hold_h".to_string(), Value::from(params.hold_h)),
    ])
}

pub fn metric_json_value(value: f64) -> Value {
    if value.is_finite() {
        serde_json::Number::from_f64(value)
            .map(Value::Number)
            .unwrap_or(Value::Null)
    } else {
        Value::Null
    }
}

pub fn data_fingerprint(path: &Path) -> anyhow::Result<String> {
    let bytes = std::fs::read(path).with_context(|| {
        format!(
            "failed to read data file for fingerprint: {}",
            path.display()
        )
    })?;
    let digest = Sha256::digest(&bytes);
    Ok(format!("sha256:{}", hex::encode(digest)))
}

pub fn ungated_completed_output(
    asset: String,
    timeframe: String,
    params: TodEdgeParams,
    data_ref: String,
    data_fingerprint: String,
    fee_bps: f64,
    result: &side_engine::backtest::BacktestResult,
) -> BacktestResultOutput {
    BacktestResultOutput {
        schema_version: BACKTEST_RESULT_SCHEMA_VERSION,
        risk_gate_enabled: false,
        run_status: "completed",
        asset,
        strategy: "tod_edge",
        timeframe,
        params,
        data_ref,
        data_fingerprint,
        fee_bps,
        metrics: Some(metrics_from_result(result)),
        risk_gate: None,
        cap_parity: CapParityOutput {
            status: "not_applicable",
        },
        backtest_execution: BacktestExecutionOutput {
            status: "run",
            reason: None,
            backtest_invocation_count: 1,
        },
    }
}

pub fn gated_stopped_output(
    asset: String,
    timeframe: String,
    params: TodEdgeParams,
    data_ref: String,
    data_fingerprint: String,
    fee_bps: f64,
    summary: &RiskGateSummary,
) -> anyhow::Result<BacktestResultOutput> {
    anyhow::ensure!(
        summary.execution_state()? == RiskGateExecutionState::Stopped,
        "risk gate stopped output requires a stop decision"
    );
    Ok(BacktestResultOutput {
        schema_version: BACKTEST_RESULT_SCHEMA_VERSION,
        risk_gate_enabled: true,
        run_status: "stopped",
        asset,
        strategy: "tod_edge",
        timeframe,
        params,
        data_ref,
        data_fingerprint,
        fee_bps,
        metrics: None,
        risk_gate: Some(summary.to_slot_output()?),
        cap_parity: CapParityOutput {
            status: "not_applicable",
        },
        backtest_execution: BacktestExecutionOutput {
            status: "not_run",
            reason: Some("risk_gate_stop"),
            backtest_invocation_count: 0,
        },
    })
}

pub fn gated_completed_output(
    asset: String,
    timeframe: String,
    params: TodEdgeParams,
    data_ref: String,
    data_fingerprint: String,
    fee_bps: f64,
    summary: &RiskGateSummary,
    result: &side_engine::backtest::BacktestResult,
    runtime_sizing: Option<&BacktestRuntimeSizing>,
) -> anyhow::Result<BacktestResultOutput> {
    anyhow::ensure!(
        summary.execution_state()? == RiskGateExecutionState::Continued,
        "risk gate completed output requires a continue decision"
    );
    Ok(BacktestResultOutput {
        schema_version: BACKTEST_RESULT_SCHEMA_VERSION,
        risk_gate_enabled: true,
        run_status: "completed",
        asset,
        strategy: "tod_edge",
        timeframe,
        params,
        data_ref,
        data_fingerprint,
        fee_bps,
        metrics: Some(metrics_from_result(result)),
        risk_gate: Some(risk_gate_slot_output(summary, runtime_sizing)?),
        cap_parity: CapParityOutput {
            status: "not_applicable",
        },
        backtest_execution: BacktestExecutionOutput {
            status: "run",
            reason: None,
            backtest_invocation_count: 1,
        },
    })
}

pub fn backtest_runtime_sizing_for_summary(
    summary: &RiskGateSummary,
) -> anyhow::Result<BacktestRuntimeSizing> {
    anyhow::ensure!(
        summary.decision_class == "cap",
        "backtest runtime sizing requires cap decision, got {}",
        summary.decision_class
    );
    let allowed_size = summary.allowed_size;
    anyhow::ensure!(
        allowed_size.is_finite() && allowed_size > 0.0 && allowed_size <= BACKTEST_REQUESTED_SIZE,
        "invalid backtest cap allowed_size: {allowed_size}"
    );
    let sizing_effect = if allowed_size < BACKTEST_REQUESTED_SIZE {
        "reduced"
    } else {
        "none"
    };
    Ok(BacktestRuntimeSizing {
        requested_size: BACKTEST_REQUESTED_SIZE,
        requested_size_basis: BACKTEST_REQUESTED_SIZE_BASIS,
        allowed_size,
        effective_size: allowed_size,
        application_status: "applied",
        runtime_sizing_applied: true,
        sizing_effect,
    })
}

fn risk_gate_slot_output(
    summary: &RiskGateSummary,
    runtime_sizing: Option<&BacktestRuntimeSizing>,
) -> anyhow::Result<RiskGateSlotOutput> {
    let mut output = summary.to_slot_output()?;
    if let Some(runtime_sizing) = runtime_sizing {
        anyhow::ensure!(
            summary.decision_class == "cap",
            "runtime sizing output is only supported for cap decisions"
        );
        output.application_status = Some(runtime_sizing.application_status.to_string());
        output.runtime_sizing_applied = Some(runtime_sizing.runtime_sizing_applied);
        output.sizing_effect = Some(runtime_sizing.sizing_effect.to_string());
        output.requested_size = Some(runtime_sizing.requested_size);
        output.requested_size_basis = Some(runtime_sizing.requested_size_basis.to_string());
        output.allowed_size = runtime_sizing.allowed_size;
        output.effective_size = Some(runtime_sizing.effective_size);
    }
    Ok(output)
}

fn metrics_from_result(result: &side_engine::backtest::BacktestResult) -> BacktestMetrics {
    BacktestMetrics {
        profit_factor: metric_json_value(result.profit_factor),
        num_trades: result.num_trades,
        total_return: metric_json_value(result.total_return),
    }
}
