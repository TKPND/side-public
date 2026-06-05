use clap::Parser;

use crate::cmd::backtest_contract::{self, TodEdgeParams};
use crate::cmd::backtest_risk_adapter::{
    build_backtest_candidate, build_backtest_v2_candidate, candidate_staging_path,
    write_backtest_candidate_json, BacktestCandidateInput,
};
use crate::cmd::risk_gate::{
    evaluate_risk_gate, RiskGateContractVersion, RiskGateExecutionState, RiskGateInvocation,
    RiskGateSummary,
};

#[derive(Parser)]
#[command(about = "Backtest strategies on historical data")]
pub struct BacktestArgs {
    /// Asset to backtest
    #[arg(short, long)]
    pub asset: String,

    /// Strategy name/identifier
    #[arg(short, long)]
    pub strategy: String,

    /// Strategy parameters (JSON format)
    #[arg(short, long)]
    pub params: String,

    /// OHLCV parquet data path
    #[arg(long)]
    pub data: std::path::PathBuf,

    /// Number of walk-forward divisions
    #[arg(short, long)]
    pub walks: Option<u32>,

    /// Timeframe (e.g., 1h, 4h, 1d)
    #[arg(short, long, default_value = "1h")]
    pub timeframe: String,

    /// Fee in basis points per position change (1 bps = 0.0001 ratio).
    /// Default 1.0 bps (~2 bps round-trip) matches FX retail realistic cost.
    #[arg(long, default_value_t = 1.0, allow_hyphen_values = true)]
    pub fee_bps: f64,

    #[arg(long)]
    pub risk_gate_policy: Option<std::path::PathBuf>,

    #[arg(long)]
    pub risk_gate_artifact_root: Option<std::path::PathBuf>,

    #[arg(long, value_enum, default_value_t = RiskGateContractVersion::V1)]
    pub risk_gate_contract_version: RiskGateContractVersion,
}

pub async fn run(args: BacktestArgs) -> anyhow::Result<()> {
    let risk_gate = validate_risk_gate_flags(&args)?;
    anyhow::ensure!(
        args.strategy == "tod_edge",
        "unsupported strategy: {}",
        args.strategy
    );
    if let Some(walks) = args.walks {
        anyhow::ensure!(walks == 1, "unsupported --walks: {walks}");
    }
    anyhow::ensure!(
        args.fee_bps.is_finite() && args.fee_bps >= 0.0,
        "--fee-bps must be finite and >= 0.0"
    );

    let params = backtest_contract::parse_tod_edge_params_json(&args.params)?;
    let data_ref = args.data.display().to_string();
    let data = side_engine::parquet_loader::load_ohlcv_parquet(&args.data)?;
    side_engine::parquet_loader::validate_ohlcv_contract(&data, &args.timeframe)?;
    let data_fingerprint = backtest_contract::data_fingerprint(&args.data)?;
    let risk_gate_summary = if let Some((policy, artifact_root)) = risk_gate {
        let cli_cwd = std::env::current_dir()?;
        let summary = evaluate_backtest_risk_gate(BacktestGateInput {
            policy,
            artifact_root,
            cli_cwd: &cli_cwd,
            asset: &args.asset,
            timeframe: &args.timeframe,
            params: &params,
            data_ref: &data_ref,
            data_fingerprint: &data_fingerprint,
            fee_bps: args.fee_bps,
            contract_version: args.risk_gate_contract_version,
        })?;
        if summary.execution_state()? == RiskGateExecutionState::Stopped {
            let output = backtest_contract::gated_stopped_output(
                args.asset,
                args.timeframe,
                params,
                data_ref,
                data_fingerprint,
                args.fee_bps,
                &summary,
            )?;
            println!("{}", serde_json::to_string(&output)?);
            return Ok(());
        }
        Some(summary)
    } else {
        None
    };
    let strategy_params = backtest_contract::tod_edge_strategy_params(&params);
    let ohlcv = data.as_ref();
    let signals = side_engine::strategies::generate_signals("tod_edge", &ohlcv, &strategy_params);
    let (fee, ppy, mode) =
        side_engine::backtest::backtest_call_args(args.fee_bps / 10_000.0, &args.timeframe);
    let runtime_sizing = risk_gate_summary
        .as_ref()
        .filter(|summary| summary.decision_class == "cap")
        .map(backtest_contract::backtest_runtime_sizing_for_summary)
        .transpose()?;
    let result = if let Some(runtime_sizing) = runtime_sizing.as_ref() {
        side_engine::backtest::run_backtest_sized(
            &data.close,
            &signals,
            fee,
            ppy,
            mode,
            &data.datetimes_ns,
            runtime_sizing.effective_size,
        )?
    } else {
        side_engine::backtest::run_backtest(
            &data.close,
            &signals,
            fee,
            ppy,
            mode,
            &data.datetimes_ns,
        )
    };
    let output = if let Some(summary) = risk_gate_summary {
        backtest_contract::gated_completed_output(
            args.asset,
            args.timeframe,
            params,
            data_ref,
            data_fingerprint,
            args.fee_bps,
            &summary,
            &result,
            runtime_sizing.as_ref(),
        )?
    } else {
        backtest_contract::ungated_completed_output(
            args.asset,
            args.timeframe,
            params,
            data_ref,
            data_fingerprint,
            args.fee_bps,
            &result,
        )
    };
    println!("{}", serde_json::to_string(&output)?);
    Ok(())
}

fn validate_risk_gate_flags(
    args: &BacktestArgs,
) -> anyhow::Result<Option<(&std::path::Path, &std::path::Path)>> {
    match (&args.risk_gate_policy, &args.risk_gate_artifact_root) {
        (None, None) if args.risk_gate_contract_version != RiskGateContractVersion::V1 => {
            anyhow::bail!("--risk-gate-contract-version requires --risk-gate-policy and --risk-gate-artifact-root")
        }
        (None, None) => Ok(None),
        (Some(policy), Some(artifact_root)) => {
            Ok(Some((policy.as_path(), artifact_root.as_path())))
        }
        _ => anyhow::bail!(
            "--risk-gate-policy and --risk-gate-artifact-root must be supplied together"
        ),
    }
}

struct BacktestGateInput<'a> {
    policy: &'a std::path::Path,
    artifact_root: &'a std::path::Path,
    cli_cwd: &'a std::path::Path,
    asset: &'a str,
    timeframe: &'a str,
    params: &'a TodEdgeParams,
    data_ref: &'a str,
    data_fingerprint: &'a str,
    fee_bps: f64,
    contract_version: RiskGateContractVersion,
}

fn evaluate_backtest_risk_gate(input: BacktestGateInput<'_>) -> anyhow::Result<RiskGateSummary> {
    let policy_path = resolve_cli_path(input.policy, input.cli_cwd);
    let artifact_root = input
        .artifact_root
        .to_str()
        .ok_or_else(|| anyhow::anyhow!("unsafe artifact_root: non-UTF-8 path"))?;
    let candidate_input = BacktestCandidateInput {
        asset: input.asset,
        timeframe: input.timeframe,
        strategy: "tod_edge",
        params: input.params,
        fee_bps: input.fee_bps,
        data_ref: input.data_ref,
        data_fingerprint: input.data_fingerprint,
        artifact_root,
    };
    let (candidate_id, validation_refs) = if input.contract_version == RiskGateContractVersion::V2 {
        let candidate = build_backtest_v2_candidate(candidate_input)?;
        let candidate_id = candidate.candidate_id.clone();
        let validation_refs = candidate.validation_refs.clone();
        let candidate_path = resolve_cli_path(
            &candidate_staging_path(input.artifact_root, &candidate_id)?,
            input.cli_cwd,
        );
        write_backtest_candidate_json(&candidate, &candidate_path)?;
        (candidate_id, validation_refs)
    } else {
        let candidate = build_backtest_candidate(candidate_input)?;
        let candidate_id = candidate.candidate_id.clone();
        let validation_refs = candidate.validation_refs.clone();
        let candidate_path = resolve_cli_path(
            &candidate_staging_path(input.artifact_root, &candidate_id)?,
            input.cli_cwd,
        );
        write_backtest_candidate_json(&candidate, &candidate_path)?;
        (candidate_id, validation_refs)
    };
    let candidate_path = resolve_cli_path(
        &candidate_staging_path(input.artifact_root, &candidate_id)?,
        input.cli_cwd,
    );
    let artifact_out = resolve_cli_path(
        &input
            .artifact_root
            .join("decisions")
            .join(format!("{}.json", candidate_id)),
        input.cli_cwd,
    );
    let gate_dir = std::env::temp_dir().join(format!(
        "side-backtest-risk-gate-{}-{}",
        std::process::id(),
        candidate_id
    ));
    std::fs::create_dir_all(&gate_dir)?;
    let evidence_path = gate_dir.join("evidence.json");
    let context_path = gate_dir.join("context.json");

    let evidence = serde_json::json!({
        "refs": validation_refs,
    });
    std::fs::write(
        &evidence_path,
        format!("{}\n", serde_json::to_string_pretty(&evidence)?),
    )?;
    let context = serde_json::json!({
        "phase": "139-opt-in-backtest-risk-gate",
        "candidate_artifact_path": candidate_path.display().to_string(),
        "emitted_artifact_path": artifact_out.display().to_string(),
    });
    std::fs::write(
        &context_path,
        format!("{}\n", serde_json::to_string_pretty(&context)?),
    )?;

    let summary = evaluate_risk_gate(RiskGateInvocation {
        policy: &policy_path,
        candidate: &candidate_path,
        evidence: &evidence_path,
        context: &context_path,
        out: &artifact_out,
        contract_version: input.contract_version,
    })
    .map_err(|err| anyhow::anyhow!("risk gate execution_state=gate_error: {err:#}"))?;

    reconcile_backtest_risk_gate_summary(summary, &candidate_id, &artifact_out)
}

fn reconcile_backtest_risk_gate_summary(
    summary: RiskGateSummary,
    expected_candidate_id: &str,
    expected_artifact_path: &std::path::Path,
) -> anyhow::Result<RiskGateSummary> {
    anyhow::ensure!(
        summary.candidate_id == expected_candidate_id,
        "risk gate candidate_id mismatch: expected {}, got {}",
        expected_candidate_id,
        summary.candidate_id
    );
    anyhow::ensure!(
        std::path::Path::new(&summary.artifact_path) == expected_artifact_path,
        "risk gate artifact_path mismatch: expected {}, got {}",
        expected_artifact_path.display(),
        summary.artifact_path
    );
    Ok(summary)
}

fn resolve_cli_path(path: &std::path::Path, cli_cwd: &std::path::Path) -> std::path::PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        cli_cwd.join(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_summary(candidate_id: &str, artifact_path: &str) -> RiskGateSummary {
        RiskGateSummary {
            decision_class: "size".to_string(),
            allowed_size: 1.0,
            binding_rule: "risk-policy.v1.size".to_string(),
            fail_close_reason: "none".to_string(),
            policy_version: "risk-policy.v1.test".to_string(),
            candidate_id: candidate_id.to_string(),
            artifact_path: artifact_path.to_string(),
            schema_version: None,
            contract_version: None,
            validator_result_schema_version: None,
            validated_schema_ref: None,
        }
    }

    #[test]
    fn backtest_risk_gate_summary_reconciliation_rejects_candidate_mismatch() {
        let summary = sample_summary(
            "backtest.USDJPY.1h.tod_edge.pbad",
            "/tmp/side/decisions/backtest.USDJPY.1h.tod_edge.pabc.json",
        );

        let err = reconcile_backtest_risk_gate_summary(
            summary,
            "backtest.USDJPY.1h.tod_edge.pabc",
            std::path::Path::new("/tmp/side/decisions/backtest.USDJPY.1h.tod_edge.pabc.json"),
        )
        .unwrap_err();

        assert!(err.to_string().contains("risk gate candidate_id mismatch"));
    }

    #[test]
    fn backtest_risk_gate_summary_reconciliation_rejects_artifact_path_mismatch() {
        let summary = sample_summary(
            "backtest.USDJPY.1h.tod_edge.pabc",
            "/tmp/side/decisions/stale.json",
        );

        let err = reconcile_backtest_risk_gate_summary(
            summary,
            "backtest.USDJPY.1h.tod_edge.pabc",
            std::path::Path::new("/tmp/side/decisions/backtest.USDJPY.1h.tod_edge.pabc.json"),
        )
        .unwrap_err();

        assert!(err.to_string().contains("risk gate artifact_path mismatch"));
    }
}
