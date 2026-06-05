use anyhow::Result;
use clap::Args;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::Mutex;
use tracing::{error, info};

use crate::cmd::risk_gate::{evaluate_risk_gate, RiskGateContractVersion, RiskGateInvocation};
use side_engine::fetcher::aux;
use side_engine::fetcher::dukascopy;
use side_engine::fetcher::types::Timeframe;
use side_engine::paper::db::PaperDb;
use side_engine::paper::risk::{
    build_paper_candidate, build_paper_v2_candidate, paper_apply_action_for_decision,
    validate_paper_runtime_sizing_guard, PaperApplyAction, PaperCandidateInput, PaperCostModel,
    PaperRiskCandidate, PaperRiskCandidateV2, PaperRiskDecision, PaperRiskEvidence,
    PaperRiskEvidenceCandidateRef, PaperRiskEvidenceInput, PaperRiskExecutionState, PaperRiskMode,
    PaperV2CandidateInput,
};
use side_engine::paper::{PaperConfig, PaperTrader, RuntimeAccountingMode};

#[derive(Args, Debug)]
pub struct PaperArgs {
    /// Path to paper_slots.json config
    #[arg(long, default_value = "config/paper_slots.json")]
    pub config: String,

    /// Initial capital (overrides config)
    #[arg(long)]
    pub initial_capital: Option<f64>,

    /// Database path (overrides config)
    #[arg(long)]
    pub db: Option<String>,

    /// Run once (single tick) instead of daemon
    #[arg(long)]
    pub once: bool,

    /// Health file output path (overrides config)
    #[arg(long)]
    pub health_file: Option<String>,

    /// Paper risk mode: off, observe, or apply
    #[arg(long, default_value = "off")]
    pub paper_risk_mode: String,

    /// Risk gate policy JSON path for paper observe/apply modes
    #[arg(long)]
    pub risk_gate_policy: Option<std::path::PathBuf>,

    /// Risk gate artifact root for paper observe/apply modes
    #[arg(long)]
    pub risk_gate_artifact_root: Option<std::path::PathBuf>,

    /// Risk gate contract version for paper risk apply mode
    #[arg(long, value_enum, default_value_t = RiskGateContractVersion::V1)]
    pub risk_gate_contract_version: RiskGateContractVersion,

    /// Paper risk evidence output root
    #[arg(long)]
    pub paper_risk_evidence_root: Option<std::path::PathBuf>,

    /// Paper fee estimate in basis points for evidence-only cost model
    #[arg(long, default_value_t = 0.0, allow_hyphen_values = true)]
    pub paper_fee_bps: f64,

    /// Paper spread estimate in basis points for evidence-only cost model
    #[arg(long, default_value_t = 0.0, allow_hyphen_values = true)]
    pub paper_spread_bps: f64,

    /// Paper runtime accounting mode: legacy_gross or estimated_net
    #[arg(long, default_value = "legacy_gross")]
    pub runtime_accounting_mode: String,
}

pub async fn run(args: PaperArgs) -> Result<()> {
    let runtime_accounting_mode = parse_runtime_accounting_mode_arg(&args.runtime_accounting_mode)?;
    validate_paper_risk_flags(&args)?;

    info!("loading config from {}", args.config);
    let mut config = PaperConfig::from_file(&args.config)?;

    if let Some(cap) = args.initial_capital {
        config.initial_capital = cap;
    }
    if let Some(ref db_path) = args.db {
        config.db_path = db_path.clone();
    }
    if let Some(ref hf) = args.health_file {
        config.health_file = hf.clone();
    }
    config.paper_fee_bps = args.paper_fee_bps;
    config.paper_spread_bps = args.paper_spread_bps;
    config.runtime_accounting_mode = runtime_accounting_mode;

    let db = PaperDb::open(&config.db_path)?;
    info!(
        slots = config.slots.len(),
        capital = config.initial_capital,
        "paper trader initialized"
    );

    let trader = Arc::new(Mutex::new(PaperTrader::new(config.clone(), db)));
    let risk_result = if args.paper_risk_mode != "off" {
        let policy = args.risk_gate_policy.as_ref().unwrap();
        let artifact_root = args.risk_gate_artifact_root.as_ref().unwrap();
        let evidence_root = args.paper_risk_evidence_root.as_ref().unwrap();
        let result = evaluate_paper_risk_once_with_result(
            &config,
            PaperRiskGateOptions {
                mode: &args.paper_risk_mode,
                policy,
                artifact_root,
                evidence_root,
                contract_version: args.risk_gate_contract_version,
                fee_bps: args.paper_fee_bps,
                spread_bps: args.paper_spread_bps,
                db_before_artifact_path: None,
                db_before_artifact_sha256: None,
                db_after_artifact_path: None,
                db_after_artifact_sha256: None,
                health_artifact_path: Some(Path::new(&config.health_file)),
                health_artifact_sha256: None,
            },
        )?;
        if let Some(first) = result.evidence_paths.first() {
            let evidence_value: serde_json::Value =
                serde_json::from_str(&std::fs::read_to_string(first)?)?;
            let mut t = trader.lock().await;
            t.set_last_risk_summary_from_evidence(
                &args.paper_risk_mode,
                first.display().to_string(),
                if args.paper_risk_mode == "observe" {
                    "observed"
                } else {
                    "evaluated"
                },
                None,
                &evidence_value,
            );
            for runtime_size_override in &result.runtime_size_overrides {
                t.apply_runtime_size_override(
                    &runtime_size_override.slot_id,
                    runtime_size_override.effective_size,
                )?;
            }
        }
        Some(result)
    } else {
        None
    };
    if let Some(result) = &risk_result {
        info!(
            evidence_count = result.evidence_paths.len(),
            risk_mode = args.paper_risk_mode,
            "paper risk evidence written"
        );
    }

    if args.once {
        if risk_result
            .as_ref()
            .map(|result| result.should_run_tick)
            .unwrap_or(true)
        {
            run_tick(&trader, &config).await?;
        } else {
            let t = trader.lock().await;
            write_health(&t, &config.health_file);
        }
        return Ok(());
    }

    info!("running initial tick");
    if risk_result
        .as_ref()
        .map(|result| result.should_run_tick)
        .unwrap_or(true)
    {
        run_tick(&trader, &config).await?;
    } else {
        let t = trader.lock().await;
        write_health(&t, &config.health_file);
    }

    use tokio_cron_scheduler::{Job, JobScheduler};
    let mut sched = JobScheduler::new().await?;

    let trader_clone = Arc::clone(&trader);
    let config_clone = config.clone();
    sched
        .add(Job::new_async("0 0 * * * *", move |_uuid, _l| {
            let trader = Arc::clone(&trader_clone);
            let cfg = config_clone.clone();
            Box::pin(async move {
                if let Err(e) = run_tick(&trader, &cfg).await {
                    error!(error = %e, "tick failed");
                }
            })
        })?)
        .await?;

    sched.start().await?;
    info!("scheduler started, waiting for SIGTERM/SIGINT");

    shutdown_signal().await;
    info!("shutdown signal received, stopping scheduler");
    sched.shutdown().await?;

    let t = trader.lock().await;
    write_health(&t, &config.health_file);
    info!("paper trader stopped gracefully");
    Ok(())
}

pub fn parse_runtime_accounting_mode_arg(value: &str) -> Result<RuntimeAccountingMode> {
    match value {
        "legacy_gross" => Ok(RuntimeAccountingMode::LegacyGross),
        "estimated_net" => Ok(RuntimeAccountingMode::EstimatedNet),
        _ => anyhow::bail!("--runtime-accounting-mode must be legacy_gross or estimated_net"),
    }
}

fn validate_paper_risk_flags(args: &PaperArgs) -> anyhow::Result<()> {
    match args.paper_risk_mode.as_str() {
        "off" => {
            if args.risk_gate_policy.is_some()
                || args.risk_gate_artifact_root.is_some()
                || args.paper_risk_evidence_root.is_some()
                || args.risk_gate_contract_version != RiskGateContractVersion::V1
            {
                anyhow::bail!("paper risk flags require --paper-risk-mode observe or apply");
            }
        }
        "observe" => {
            if args.risk_gate_contract_version != RiskGateContractVersion::V1 {
                anyhow::bail!("--risk-gate-contract-version v2 requires --paper-risk-mode apply");
            }
            if args.risk_gate_policy.is_none() {
                anyhow::bail!("--risk-gate-policy is required for paper risk mode");
            }
            if args.risk_gate_artifact_root.is_none() {
                anyhow::bail!("--risk-gate-artifact-root is required for paper risk mode");
            }
            if args.paper_risk_evidence_root.is_none() {
                anyhow::bail!("--paper-risk-evidence-root is required for paper risk mode");
            }
        }
        "apply" => {
            if args.risk_gate_policy.is_none() {
                anyhow::bail!("--risk-gate-policy is required for paper risk mode");
            }
            if args.risk_gate_artifact_root.is_none() {
                anyhow::bail!("--risk-gate-artifact-root is required for paper risk mode");
            }
            if args.paper_risk_evidence_root.is_none() {
                anyhow::bail!("--paper-risk-evidence-root is required for paper risk mode");
            }
        }
        other => anyhow::bail!("unknown --paper-risk-mode: {other}"),
    }
    if !args.paper_fee_bps.is_finite() || args.paper_fee_bps < 0.0 {
        anyhow::bail!("--paper-fee-bps must be finite and non-negative");
    }
    if !args.paper_spread_bps.is_finite() || args.paper_spread_bps < 0.0 {
        anyhow::bail!("--paper-spread-bps must be finite and non-negative");
    }
    Ok(())
}

pub struct PaperRiskGateOptions<'a> {
    pub mode: &'a str,
    pub policy: &'a Path,
    pub artifact_root: &'a Path,
    pub evidence_root: &'a Path,
    pub contract_version: RiskGateContractVersion,
    pub fee_bps: f64,
    pub spread_bps: f64,
    pub db_before_artifact_path: Option<&'a Path>,
    pub db_before_artifact_sha256: Option<String>,
    pub db_after_artifact_path: Option<&'a Path>,
    pub db_after_artifact_sha256: Option<String>,
    pub health_artifact_path: Option<&'a Path>,
    pub health_artifact_sha256: Option<String>,
}

#[derive(Debug)]
pub struct PaperRiskEvaluationResult {
    pub evidence_paths: Vec<PathBuf>,
    pub should_run_tick: bool,
    pub runtime_size_overrides: Vec<PaperRuntimeSizeOverride>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PaperRuntimeSizeOverride {
    pub slot_id: String,
    pub effective_size: f64,
}

#[derive(Debug)]
enum BuiltPaperRiskCandidate {
    V1(PaperRiskCandidate),
    V2(PaperRiskCandidateV2),
}

impl BuiltPaperRiskCandidate {
    fn candidate_id(&self) -> &str {
        match self {
            Self::V1(candidate) => &candidate.candidate_id,
            Self::V2(candidate) => &candidate.candidate_id,
        }
    }

    fn validation_refs(&self) -> &[String] {
        match self {
            Self::V1(candidate) => &candidate.validation_refs,
            Self::V2(candidate) => &candidate.validation_refs,
        }
    }

    fn write_json_create_new(&self, path: &Path) -> anyhow::Result<()> {
        match self {
            Self::V1(candidate) => write_json_create_new(path, candidate),
            Self::V2(candidate) => write_json_create_new(path, candidate),
        }
    }
}

impl PaperRiskEvidenceCandidateRef for BuiltPaperRiskCandidate {
    fn slot_id(&self) -> &str {
        match self {
            Self::V1(candidate) => candidate.slot_id(),
            Self::V2(candidate) => candidate.slot_id(),
        }
    }

    fn candidate_id(&self) -> &str {
        match self {
            Self::V1(candidate) => candidate.candidate_id(),
            Self::V2(candidate) => candidate.candidate_id(),
        }
    }

    fn requested_size(&self) -> f64 {
        match self {
            Self::V1(candidate) => candidate.requested_size(),
            Self::V2(candidate) => candidate.requested_size(),
        }
    }

    fn requested_size_basis(&self) -> &str {
        match self {
            Self::V1(candidate) => candidate.requested_size_basis(),
            Self::V2(candidate) => candidate.requested_size_basis(),
        }
    }

    fn risk_mode(&self) -> &str {
        match self {
            Self::V1(candidate) => candidate.risk_mode(),
            Self::V2(candidate) => candidate.risk_mode(),
        }
    }

    fn risk_contract_schema_version(&self) -> Option<&'static str> {
        match self {
            Self::V1(candidate) => candidate.risk_contract_schema_version(),
            Self::V2(candidate) => candidate.risk_contract_schema_version(),
        }
    }

    fn risk_contract_version(&self) -> Option<&'static str> {
        match self {
            Self::V1(candidate) => candidate.risk_contract_version(),
            Self::V2(candidate) => candidate.risk_contract_version(),
        }
    }

    fn validator_result_schema_version(&self) -> Option<&'static str> {
        match self {
            Self::V1(candidate) => candidate.validator_result_schema_version(),
            Self::V2(candidate) => candidate.validator_result_schema_version(),
        }
    }

    fn validated_schema_ref(&self) -> Option<&'static str> {
        match self {
            Self::V1(candidate) => candidate.validated_schema_ref(),
            Self::V2(candidate) => candidate.validated_schema_ref(),
        }
    }

    fn validator(&self) -> Option<&'static str> {
        match self {
            Self::V1(candidate) => candidate.validator(),
            Self::V2(candidate) => candidate.validator(),
        }
    }
}

pub fn evaluate_paper_risk_once(
    config: &PaperConfig,
    options: PaperRiskGateOptions<'_>,
) -> anyhow::Result<Vec<PathBuf>> {
    Ok(evaluate_paper_risk_once_with_result(config, options)?.evidence_paths)
}

pub fn evaluate_paper_risk_once_with_result(
    config: &PaperConfig,
    options: PaperRiskGateOptions<'_>,
) -> anyhow::Result<PaperRiskEvaluationResult> {
    let mode = match options.mode {
        "observe" => PaperRiskMode::Observe,
        "apply" => PaperRiskMode::Apply,
        "off" => {
            return Ok(PaperRiskEvaluationResult {
                evidence_paths: Vec::new(),
                should_run_tick: true,
                runtime_size_overrides: Vec::new(),
            });
        }
        other => anyhow::bail!("unknown --paper-risk-mode: {other}"),
    };
    let cli_cwd = std::env::current_dir()?;
    if options.contract_version == RiskGateContractVersion::V2 {
        anyhow::ensure!(
            mode == PaperRiskMode::Apply,
            "--risk-gate-contract-version v2 requires --paper-risk-mode apply"
        );
        reject_protected_v2_paper_root(options.artifact_root, &cli_cwd, "artifact_root")?;
        reject_protected_v2_paper_root(options.evidence_root, &cli_cwd, "evidence_root")?;
    }
    let policy_path = resolve_cli_path(options.policy, &cli_cwd);
    let artifact_root = resolve_cli_path(options.artifact_root, &cli_cwd);
    let evidence_root = resolve_cli_path(options.evidence_root, &cli_cwd);
    let cost_model = PaperCostModel::new(
        options.fee_bps,
        options.spread_bps,
        side_engine::paper::risk::PAPER_COST_MODEL_SOURCE_CLI,
    )?;
    let config_fingerprint = fingerprint_json(config)?;
    let policy_sha256 = sha256_file(&policy_path)?;
    let slot_ids = config.slot_ids();
    let allocations = config.allocations();
    let mut evidence_paths = Vec::new();
    let mut should_run_tick = true;
    let mut runtime_size_overrides = Vec::new();

    for (slot_index, slot) in config.slots.iter().enumerate() {
        let artifact_root_display = artifact_root.display().to_string();
        let base_candidate_input = PaperCandidateInput {
            slot_index,
            slot_id: &slot_ids[slot_index],
            slot,
            config_fingerprint: &config_fingerprint,
            data_window_fingerprint: "not_captured_observe_only",
            latest_bar_timestamp: "not_captured_observe_only",
            requested_size: allocations[slot_index],
            risk_mode: mode,
            artifact_root: &artifact_root_display,
        };
        let candidate = if options.contract_version == RiskGateContractVersion::V2 {
            BuiltPaperRiskCandidate::V2(build_paper_v2_candidate(PaperV2CandidateInput {
                base: base_candidate_input,
                initial_capital: config.initial_capital,
                slot_count: config.slots.len(),
                effective_leverage: config.effective_leverage(slot_index),
                runtime_accounting_mode: config.runtime_accounting_mode.as_str(),
            })?)
        } else {
            BuiltPaperRiskCandidate::V1(build_paper_candidate(base_candidate_input)?)
        };
        let candidate_path = evidence_root
            .join("candidates")
            .join(format!("{}.json", candidate.candidate_id()));
        candidate.write_json_create_new(&candidate_path)?;
        let candidate_sha256 = sha256_file(&candidate_path)?;

        let decision_path = artifact_root
            .join("decisions")
            .join(format!("{}.json", candidate.candidate_id()));
        let gate_dir = std::env::temp_dir().join(format!(
            "side-paper-risk-gate-{}-{}",
            std::process::id(),
            candidate.candidate_id()
        ));
        std::fs::create_dir_all(&gate_dir)?;
        let gate_evidence_path = gate_dir.join("evidence.json");
        let context_path = gate_dir.join("context.json");
        let gate_refs = if options.contract_version == RiskGateContractVersion::V2 {
            candidate.validation_refs().to_vec()
        } else {
            vec![
                "risk/contracts/v1/risk_contract_v1.schema.json".to_string(),
                "scripts/validate_risk_contract.py".to_string(),
                candidate_path.display().to_string(),
            ]
        };
        let gate_evidence = serde_json::json!({
            "refs": gate_refs,
        });
        std::fs::write(
            &gate_evidence_path,
            format!("{}\n", serde_json::to_string_pretty(&gate_evidence)?),
        )?;
        let context = serde_json::json!({
            "phase": if options.contract_version == RiskGateContractVersion::V2 {
                "paper-v2-runtime-adoption"
            } else {
                "v6-paper-risk-observe"
            },
            "candidate_artifact_path": candidate_path.display().to_string(),
            "emitted_artifact_path": decision_path.display().to_string(),
        });
        std::fs::write(
            &context_path,
            format!("{}\n", serde_json::to_string_pretty(&context)?),
        )?;

        let summary = evaluate_risk_gate(RiskGateInvocation {
            policy: &policy_path,
            candidate: &candidate_path,
            evidence: &gate_evidence_path,
            context: &context_path,
            out: &decision_path,
            contract_version: options.contract_version,
        })
        .map_err(|err| anyhow::anyhow!("paper risk gate execution_state=gate_error: {err:#}"))?;
        anyhow::ensure!(
            summary.candidate_id == candidate.candidate_id(),
            "paper risk gate candidate_id mismatch: expected {}, got {}",
            candidate.candidate_id(),
            summary.candidate_id
        );
        anyhow::ensure!(
            Path::new(&summary.artifact_path) == decision_path,
            "paper risk gate artifact_path mismatch: expected {}, got {}",
            decision_path.display(),
            summary.artifact_path
        );

        let mut runtime_sizing_applied = false;
        let mut actual_effective_size = candidate.requested_size();
        let execution_state = if mode == PaperRiskMode::Apply {
            match paper_apply_action_for_decision(&summary.decision_class)? {
                PaperApplyAction::StopSlot | PaperApplyAction::StopTick => {
                    should_run_tick = false;
                    PaperRiskExecutionState::Stopped
                }
                PaperApplyAction::Continue => PaperRiskExecutionState::Continued,
                PaperApplyAction::ApplySizing => {
                    validate_paper_runtime_sizing_guard(
                        &summary.decision_class,
                        candidate.requested_size(),
                        summary.allowed_size,
                    )?;
                    runtime_sizing_applied = true;
                    actual_effective_size = summary.allowed_size;
                    runtime_size_overrides.push(PaperRuntimeSizeOverride {
                        slot_id: candidate.slot_id().to_string(),
                        effective_size: summary.allowed_size,
                    });
                    PaperRiskExecutionState::Continued
                }
            }
        } else {
            PaperRiskExecutionState::Observed
        };
        let decision_sha256 = sha256_file(&decision_path)?;
        let db_before_artifact_path = options
            .db_before_artifact_path
            .map(|p| p.display().to_string())
            .unwrap_or_else(|| "not_captured".to_string());
        let db_before_artifact_sha256 = options
            .db_before_artifact_sha256
            .as_deref()
            .unwrap_or("not_captured")
            .to_string();
        let db_after_artifact_path = options
            .db_after_artifact_path
            .map(|p| p.display().to_string())
            .unwrap_or_else(|| "not_captured".to_string());
        let db_after_artifact_sha256 = options
            .db_after_artifact_sha256
            .as_deref()
            .unwrap_or("not_captured")
            .to_string();
        let health_artifact_path = options
            .health_artifact_path
            .map(|p| p.display().to_string())
            .unwrap_or_else(|| "not_captured".to_string());
        let health_artifact_sha256 = options
            .health_artifact_sha256
            .as_deref()
            .unwrap_or("not_captured")
            .to_string();
        let evidence = PaperRiskEvidence::from_input(PaperRiskEvidenceInput {
            run_id: "paper-risk-once",
            tick_id: "paper-risk-once",
            candidate: &candidate,
            decision: PaperRiskDecision {
                decision_class: summary.decision_class,
                allowed_size: summary.allowed_size,
                binding_rule: summary.binding_rule,
                fail_close_reason: summary.fail_close_reason,
                policy_version: summary.policy_version,
                decision_artifact_path: summary.artifact_path,
                decision_artifact_sha256: decision_sha256,
                policy_path: policy_path.display().to_string(),
                policy_sha256: policy_sha256.clone(),
                validator_valid: true,
                validator_errors: vec![],
            },
            candidate_artifact_path: &candidate_path.display().to_string(),
            candidate_artifact_sha256: &candidate_sha256,
            execution_state,
            position_mutation: false,
            db_before: "not_captured",
            db_after: "not_captured",
            db_before_artifact_path: &db_before_artifact_path,
            db_before_artifact_sha256: &db_before_artifact_sha256,
            db_after_artifact_path: &db_after_artifact_path,
            db_after_artifact_sha256: &db_after_artifact_sha256,
            health_artifact_path: &health_artifact_path,
            health_artifact_sha256: &health_artifact_sha256,
            parity_status: "observed",
            cost_model: cost_model.clone(),
            effective_leverage: config.effective_leverage(slot_index),
            gross_pnl: 0.0,
            runtime_sizing_applied,
            actual_effective_size,
        });
        let paper_evidence_path = evidence_root
            .join("evidence")
            .join(format!("{}.json", candidate.candidate_id()));
        write_json_create_new(&paper_evidence_path, &evidence)?;
        evidence_paths.push(paper_evidence_path);
    }

    Ok(PaperRiskEvaluationResult {
        evidence_paths,
        should_run_tick,
        runtime_size_overrides,
    })
}

fn resolve_cli_path(path: &Path, cli_cwd: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        cli_cwd.join(path)
    }
}

fn reject_protected_v2_paper_root(root: &Path, cli_cwd: &Path, label: &str) -> anyhow::Result<()> {
    let scoped_root = if root.is_absolute() {
        root.strip_prefix(cli_cwd).unwrap_or(root)
    } else {
        root
    };
    let parts = scoped_root
        .components()
        .filter_map(|component| match component {
            std::path::Component::Normal(value) => value.to_str(),
            _ => None,
        })
        .collect::<Vec<_>>();

    if parts.first() == Some(&"reports") {
        if parts
            .get(1)
            .is_some_and(|version| version.starts_with("v8."))
            || parts.get(1) == Some(&"v5.8")
            || (parts.get(1) == Some(&"v5.7") && parts.get(2) == Some(&"risk_gate"))
        {
            anyhow::bail!("unsafe v2 {label}: protected report root {:?}", root);
        }
    }
    if parts.first() == Some(&".planning") {
        anyhow::bail!("unsafe v2 {label}: protected planning root {:?}", root);
    }
    if parts.first() == Some(&"docs")
        && parts.get(1) == Some(&"reports")
        && parts.get(2) == Some(&"v4")
    {
        anyhow::bail!("unsafe v2 {label}: protected v4 docs root {:?}", root);
    }
    if parts.first() == Some(&"data") && parts.get(1) == Some(&"v4") {
        anyhow::bail!("unsafe v2 {label}: protected v4 data root {:?}", root);
    }
    if parts.first() == Some(&"risk") && parts.get(1) == Some(&"contracts") {
        anyhow::bail!("unsafe v2 {label}: protected contract root {:?}", root);
    }
    Ok(())
}

fn fingerprint_json<T: serde::Serialize>(value: &T) -> anyhow::Result<String> {
    let bytes = serde_json::to_vec(value)?;
    let digest = Sha256::digest(bytes);
    Ok(format!("sha256:{}", hex::encode(digest)))
}

fn sha256_file(path: &Path) -> anyhow::Result<String> {
    let bytes = std::fs::read(path)?;
    let digest = Sha256::digest(bytes);
    Ok(format!("sha256:{}", hex::encode(digest)))
}

fn write_json_create_new<T: serde::Serialize>(path: &Path, value: &T) -> anyhow::Result<()> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent)?;
    }
    let json = serde_json::to_string_pretty(value)?;
    let payload = format!("{json}\n");
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|err| {
            if err.kind() == std::io::ErrorKind::AlreadyExists {
                anyhow::anyhow!("paper risk artifact already exists: {}", path.display())
            } else {
                anyhow::anyhow!(err).context(format!("failed to write {}", path.display()))
            }
        })?;
    use std::io::Write;
    file.write_all(payload.as_bytes())?;
    Ok(())
}

async fn run_tick(trader: &Arc<Mutex<PaperTrader>>, config: &PaperConfig) -> Result<()> {
    if side_engine::paper::is_weekend() {
        info!("weekend — skipping tick");
        return Ok(());
    }

    let mut assets: Vec<String> = config.slots.iter().map(|s| s.asset.clone()).collect();
    assets.sort();
    assets.dedup();

    let tf = Timeframe::parse(
        config
            .slots
            .first()
            .map(|s| s.timeframe.as_str())
            .unwrap_or("1h"),
    )?;

    let bars_per_day = match tf {
        Timeframe::H1 => 24u32,
        Timeframe::H4 => 6,
        Timeframe::D1 => 1,
        _ => 24,
    };
    let fetch_days = (config.data_lookback_bars as u32 / bars_per_day) + 2;

    let mut data = std::collections::HashMap::new();
    for asset in &assets {
        let dukascopy_symbol = asset.replace("/", "");
        let cache_dir = std::path::Path::new("data/cache");
        match dukascopy::fetch_ohlcv_cached(&dukascopy_symbol, fetch_days, tf, cache_dir).await {
            Ok(bars) => {
                let aux_id = config
                    .slots
                    .iter()
                    .find(|s| s.asset == *asset)
                    .and_then(|s| s.aux_source.as_ref())
                    .map(|a| a.id.clone());

                let aux_close = if let Some(ref aid) = aux_id {
                    let target_ms: Vec<i64> = bars
                        .iter()
                        .map(|b| b.datetime.and_utc().timestamp_millis())
                        .collect();
                    match aux::fetch_aligned_aux(aid, &target_ms, 30).await {
                        Ok(aligned) => Some(aligned),
                        Err(e) => {
                            error!(asset, aux_id = aid.as_str(), error = %e, "aux fetch failed");
                            None
                        }
                    }
                } else {
                    None
                };

                info!(
                    asset,
                    bars = bars.len(),
                    aux = aux_close.is_some(),
                    "data fetched"
                );
                data.insert(asset.clone(), (bars, aux_close));
            }
            Err(e) => {
                error!(asset, error = %e, "OHLCV fetch failed");
            }
        }
    }

    let mut t = trader.lock().await;
    t.clear_errors();
    t.tick_with_data(&data)?;
    write_health(&t, &config.health_file);
    Ok(())
}

fn write_health(trader: &PaperTrader, path: &str) {
    let json = trader.health_json();
    if let Err(e) = std::fs::write(path, &json) {
        error!(path, error = %e, "failed to write health file");
    }
}

async fn shutdown_signal() {
    let ctrl_c = tokio::signal::ctrl_c();
    #[cfg(unix)]
    {
        let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler");
        tokio::select! {
            _ = ctrl_c => {},
            _ = sigterm.recv() => {},
        }
    }
    #[cfg(not(unix))]
    {
        ctrl_c.await.ok();
    }
}
