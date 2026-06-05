use clap::Parser;
use serde::Serialize;
use std::path::PathBuf;

use side_cli::cmd::paper::{evaluate_paper_risk_once_with_result, PaperRiskGateOptions};
use side_cli::cmd::risk_gate::RiskGateContractVersion;
use side_engine::paper::PaperConfig;

#[derive(Parser, Debug)]
#[command(
    name = "paper_v2_evidence_replay",
    about = "Replay one paper risk gate evaluation without running the paper tick loop"
)]
struct Args {
    #[arg(long)]
    config: PathBuf,
    #[arg(long)]
    policy: PathBuf,
    #[arg(long)]
    artifact_root: PathBuf,
    #[arg(long)]
    evidence_root: PathBuf,
    #[arg(long, default_value_t = 1.5)]
    fee_bps: f64,
    #[arg(long, default_value_t = 0.5)]
    spread_bps: f64,
}

#[derive(Serialize)]
struct RuntimeSizeOverrideRow {
    slot_id: String,
    effective_size: f64,
}

#[derive(Serialize)]
struct ReplayOutput {
    schema_version: &'static str,
    paper_risk_mode: &'static str,
    contract_version: &'static str,
    evidence_paths: Vec<String>,
    should_run_tick: bool,
    runtime_size_overrides: Vec<RuntimeSizeOverrideRow>,
}

fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    let config = PaperConfig::from_file(&args.config)?;
    let result = evaluate_paper_risk_once_with_result(
        &config,
        PaperRiskGateOptions {
            mode: "apply",
            policy: &args.policy,
            artifact_root: &args.artifact_root,
            evidence_root: &args.evidence_root,
            contract_version: RiskGateContractVersion::V2,
            fee_bps: args.fee_bps,
            spread_bps: args.spread_bps,
            db_before_artifact_path: None,
            db_before_artifact_sha256: None,
            db_after_artifact_path: None,
            db_after_artifact_sha256: None,
            health_artifact_path: None,
            health_artifact_sha256: None,
        },
    )?;

    let output = ReplayOutput {
        schema_version: "side-cli.paper_v2_evidence_replay.result.v1",
        paper_risk_mode: "apply",
        contract_version: "v2",
        evidence_paths: result
            .evidence_paths
            .iter()
            .map(|path| path.display().to_string())
            .collect(),
        should_run_tick: result.should_run_tick,
        runtime_size_overrides: result
            .runtime_size_overrides
            .into_iter()
            .map(|item| RuntimeSizeOverrideRow {
                slot_id: item.slot_id,
                effective_size: item.effective_size,
            })
            .collect(),
    };

    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}
