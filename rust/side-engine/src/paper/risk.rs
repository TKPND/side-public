use anyhow::{anyhow, bail, Context};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use super::SlotConfig;

pub const PAPER_RISK_CANDIDATE_SCHEMA_VERSION: &str = "risk_contract.v1.paper_candidate.v1";
pub const PAPER_V2_CANDIDATE_SCHEMA_VERSION: &str = "risk_contract.v2.candidate.v1";
pub const PAPER_V2_SCHEMA_REF: &str = "risk/contracts/v2/risk_contract_v2.schema.json";
pub const PAPER_V2_RESULT_SCHEMA_REF: &str =
    "risk/contracts/v2/risk_contract_validator_result_v2.schema.json";
pub const PAPER_V2_CONTRACT_SCHEMA_VERSION: &str = "risk_contract.v2";
pub const PAPER_V2_CONTRACT_VERSION: &str = "v2";
pub const PAPER_V2_VALIDATOR_RESULT_SCHEMA_VERSION: &str = "risk_contract_validator_result.v2";
pub const PAPER_RISK_GATE_VALIDATOR_REF: &str = "scripts/validate_risk_contract.py";
pub const PAPER_REQUESTED_SIZE_BASIS: &str = "paper_slot_allocation_current_behavior";
pub const PAPER_V2_REQUESTED_SIZE_BASIS: &str = "unit_paper_slot_allocation";
pub const PAPER_ALLOWED_SIZE_BASIS: &str = "risk_decision_observed_only";
pub const PAPER_CAP_APPLICATION_STATUS: &str = "deferred_with_reason";
pub const PAPER_CAP_APPLICATION_STATUS_APPLIED: &str = "applied";
pub const PAPER_CAP_RUNTIME_SIZING_DEPENDENCY_STATUS_PNL_INDEPENDENT_PROVEN: &str =
    "pnl_source_independent_proven";
pub const PAPER_CAP_RUNTIME_SIZING_DEPENDENCY_STATUS_PNL_DEPENDENCY_UNPROVEN: &str =
    "pnl_source_dependency_unproven";
pub const PAPER_CAP_RUNTIME_SIZING_DEPENDENCY_STATUS_PNL_DEPENDENT: &str = "pnl_source_dependent";
pub const PAPER_CAP_RUNTIME_SIZING_CLAIM_BLOCK_REASON_NOT_APPLIED: &str =
    "cap_runtime_sizing_not_applied";
pub const PAPER_CAP_RUNTIME_SIZING_CLAIM_BLOCK_REASON_RUNTIME_PNL_SOURCE_UNKNOWN: &str =
    "runtime_pnl_source_unknown";
pub const PAPER_CAP_DEPENDENCY_PROOF_PNL_INDEPENDENT: &str = "cap sizing uses requested_size and validator allowed_size only; runtime equity, realized PnL, trades.pnl, and runtime_pnl_ledger are not read by the current paper cap apply boundary";
pub const PAPER_COST_MODEL_SCHEMA_VERSION: u32 = 1;
pub const PAPER_COST_BASIS: &str = "paper_notional_round_trip_bps";
pub const PAPER_COST_MODEL_SOURCE_CLI: &str = "cli";
pub const PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO: &str = "missing_or_zero_cost_model";
pub const PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO: &str = "explicit_nonzero_cost_model";
pub const PAPER_FEE_MODEL_STATUS_INVALID: &str = "invalid_cost_model";
pub const PAPER_FEE_MODEL_STATUS: &str = PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO;
pub const PAPER_PNL_SEMANTICS_LEGACY_GROSS: &str = "legacy_gross_cost_unadjusted";
pub const PAPER_PNL_SEMANTICS_ESTIMATED_NET: &str = "estimated_net_cost_adjusted";
pub const PAPER_PNL_SEMANTICS_UNKNOWN: &str = "unknown";
pub const PAPER_RUNTIME_NET_PNL_CONTRACT_STATUS: &str = "additive_breakdown_only";
pub const PAPER_RUNTIME_PNL_SOURCE_LEGACY_GROSS: &str = PAPER_PNL_SEMANTICS_LEGACY_GROSS;
pub const PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET: &str = PAPER_PNL_SEMANTICS_ESTIMATED_NET;
pub const PAPER_RUNTIME_PNL_SOURCE_MIXED_OR_UNKNOWN: &str = "mixed_or_unknown";
pub const PAPER_RUNTIME_NET_PNL_INTEGRATION_STATUS: &str = "additive_ledger_ready";
pub const PAPER_RUNTIME_NET_PNL_LEDGER_CLAIM_BLOCK_REASON: &str =
    "runtime_equity_not_net_integrated";
pub const PAPER_RUNTIME_EQUITY_USES_NET_PNL: bool = false;
pub const PAPER_RUNTIME_ACCOUNTING_MODE_LEGACY_GROSS: &str = "legacy_gross";
pub const PAPER_RUNTIME_ACCOUNTING_MODE_ESTIMATED_NET: &str = "estimated_net";
pub const PAPER_RUNTIME_ACCOUNTING_EVENT_KIND_NORMAL_EXIT: &str = "normal_exit";
pub const PAPER_RUNTIME_ACCOUNTING_EVENT_KIND_LIQUIDATION: &str = "liquidation";
pub const PAPER_RUNTIME_ACCOUNTING_CLAIM_SCOPE_RUNTIME_EQUITY: &str = "runtime_equity_accounting";
pub const PAPER_RUNTIME_EQUITY_NET_ACCOUNTING_DEFERRED_REASON: &str =
    "runtime_net_accounting_deferred_v7_1";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PaperRiskMode {
    Off,
    Observe,
    Apply,
}

impl PaperRiskMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Observe => "observe",
            Self::Apply => "apply",
        }
    }
}

#[derive(Debug, Clone)]
pub struct PaperCandidateInput<'a> {
    pub slot_index: usize,
    pub slot_id: &'a str,
    pub slot: &'a SlotConfig,
    pub config_fingerprint: &'a str,
    pub data_window_fingerprint: &'a str,
    pub latest_bar_timestamp: &'a str,
    pub requested_size: f64,
    pub risk_mode: PaperRiskMode,
    pub artifact_root: &'a str,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperRiskCandidate {
    pub paper_candidate_schema_version: &'static str,
    pub strategy_id: String,
    pub candidate_id: String,
    pub symbol_or_universe: String,
    pub slot_index: usize,
    pub slot_id: String,
    pub slot_key: String,
    pub asset: String,
    pub timeframe: String,
    pub strategy_name: String,
    pub params_hash: String,
    pub aux_source_id: Option<String>,
    pub config_fingerprint: String,
    pub data_window_fingerprint: String,
    pub latest_bar_timestamp: String,
    pub requested_size: f64,
    pub requested_size_basis: &'static str,
    pub risk_mode: String,
    pub artifact_root: String,
    pub validation_refs: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct PaperV2CandidateInput<'a> {
    pub base: PaperCandidateInput<'a>,
    pub initial_capital: f64,
    pub slot_count: usize,
    pub effective_leverage: f64,
    pub runtime_accounting_mode: &'a str,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperRiskCandidateV2 {
    pub candidate_schema_version: &'static str,
    pub candidate_id: String,
    pub strategy_id: String,
    pub symbol_or_universe: String,
    pub timeframe: String,
    pub validation_refs: Vec<String>,
    pub surface: PaperV2CandidateSurface,
    pub sizing: PaperV2CandidateSizing,
    pub surface_payload: PaperV2CandidateSurfacePayload,
    pub artifact_root: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperV2CandidateSurface {
    pub runtime_surface: &'static str,
    pub surface_status: &'static str,
    pub analysis_scope: &'static str,
    pub analysis_scope_status: &'static str,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperV2CandidateSizing {
    pub requested_size: f64,
    pub requested_size_basis: &'static str,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperV2CandidateSurfacePayload {
    pub slot_id: String,
    pub slot_index: usize,
    pub slot_key: String,
    pub allocation_source: &'static str,
    pub allocation_method: &'static str,
    pub initial_capital: f64,
    pub slot_count: usize,
    pub effective_leverage: f64,
    pub runtime_accounting_mode: String,
    pub paper_risk_mode: String,
}

pub trait PaperRiskEvidenceCandidateRef {
    fn slot_id(&self) -> &str;
    fn candidate_id(&self) -> &str;
    fn requested_size(&self) -> f64;
    fn requested_size_basis(&self) -> &str;
    fn risk_mode(&self) -> &str;

    fn risk_contract_schema_version(&self) -> Option<&'static str> {
        None
    }

    fn risk_contract_version(&self) -> Option<&'static str> {
        None
    }

    fn validator_result_schema_version(&self) -> Option<&'static str> {
        None
    }

    fn validated_schema_ref(&self) -> Option<&'static str> {
        None
    }

    fn validator(&self) -> Option<&'static str> {
        None
    }
}

impl PaperRiskEvidenceCandidateRef for PaperRiskCandidate {
    fn slot_id(&self) -> &str {
        &self.slot_id
    }

    fn candidate_id(&self) -> &str {
        &self.candidate_id
    }

    fn requested_size(&self) -> f64 {
        self.requested_size
    }

    fn requested_size_basis(&self) -> &str {
        self.requested_size_basis
    }

    fn risk_mode(&self) -> &str {
        &self.risk_mode
    }
}

impl PaperRiskEvidenceCandidateRef for PaperRiskCandidateV2 {
    fn slot_id(&self) -> &str {
        &self.surface_payload.slot_id
    }

    fn candidate_id(&self) -> &str {
        &self.candidate_id
    }

    fn requested_size(&self) -> f64 {
        self.sizing.requested_size
    }

    fn requested_size_basis(&self) -> &str {
        self.sizing.requested_size_basis
    }

    fn risk_mode(&self) -> &str {
        &self.surface_payload.paper_risk_mode
    }

    fn risk_contract_schema_version(&self) -> Option<&'static str> {
        Some(PAPER_V2_CONTRACT_SCHEMA_VERSION)
    }

    fn risk_contract_version(&self) -> Option<&'static str> {
        Some(PAPER_V2_CONTRACT_VERSION)
    }

    fn validator_result_schema_version(&self) -> Option<&'static str> {
        Some(PAPER_V2_VALIDATOR_RESULT_SCHEMA_VERSION)
    }

    fn validated_schema_ref(&self) -> Option<&'static str> {
        Some(PAPER_V2_SCHEMA_REF)
    }

    fn validator(&self) -> Option<&'static str> {
        Some(PAPER_RISK_GATE_VALIDATOR_REF)
    }
}

pub fn canonical_json_bytes(value: &Value) -> anyhow::Result<Vec<u8>> {
    serde_json::to_vec(&canonicalize_json(value)).context("failed to serialize canonical JSON")
}

fn canonicalize_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut sorted = Map::new();
            let mut keys: Vec<_> = map.keys().collect();
            keys.sort();
            for key in keys {
                sorted.insert(key.clone(), canonicalize_json(&map[key]));
            }
            Value::Object(sorted)
        }
        Value::Array(values) => Value::Array(values.iter().map(canonicalize_json).collect()),
        other => other.clone(),
    }
}

fn digest12(value: &Value) -> anyhow::Result<String> {
    let digest = Sha256::digest(canonical_json_bytes(value)?);
    Ok(hex::encode(digest)[..12].to_string())
}

pub fn normalize_component(value: &str) -> anyhow::Result<String> {
    let normalized: String = value
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .collect();
    if normalized.is_empty() {
        bail!("candidate component normalizes to empty: {value:?}");
    }
    Ok(normalized)
}

pub fn paper_slot_key(slot_id: &str) -> anyhow::Result<String> {
    let normalized: String = slot_id
        .chars()
        .map(|ch| if ch.is_ascii_alphanumeric() { ch } else { '_' })
        .collect();
    if normalized.is_empty() || normalized.contains("..") {
        bail!("unsafe paper slot_id: {slot_id:?}");
    }
    Ok(normalized)
}

pub fn paper_candidate_id(input: &PaperCandidateInput<'_>) -> anyhow::Result<String> {
    paper_candidate_id_for_schema(input, PAPER_RISK_CANDIDATE_SCHEMA_VERSION)
}

pub fn paper_candidate_id_for_schema(
    input: &PaperCandidateInput<'_>,
    candidate_schema_version: &str,
) -> anyhow::Result<String> {
    let asset_norm = normalize_component(&input.slot.asset)?;
    let timeframe_norm = normalize_component(&input.slot.timeframe)?;
    let strategy_norm = normalize_component(&input.slot.strategy_name)?;
    let identity = Value::Object(Map::from_iter([
        (
            "paper_candidate_schema_version".to_string(),
            Value::from(candidate_schema_version),
        ),
        ("asset".to_string(), Value::from(input.slot.asset.clone())),
        (
            "timeframe".to_string(),
            Value::from(input.slot.timeframe.clone()),
        ),
        (
            "strategy_name".to_string(),
            Value::from(input.slot.strategy_name.clone()),
        ),
        (
            "params".to_string(),
            Value::Object(Map::from_iter(input.slot.params.clone())),
        ),
        (
            "aux_source_id".to_string(),
            input
                .slot
                .aux_source
                .as_ref()
                .map(|a| Value::from(a.id.clone()))
                .unwrap_or(Value::Null),
        ),
        (
            "config_fingerprint".to_string(),
            Value::from(input.config_fingerprint),
        ),
        (
            "data_window_fingerprint".to_string(),
            Value::from(input.data_window_fingerprint),
        ),
        (
            "latest_bar_timestamp".to_string(),
            Value::from(input.latest_bar_timestamp),
        ),
    ]));
    Ok(format!(
        "paper.{asset_norm}.{timeframe_norm}.{strategy_norm}.p{}",
        digest12(&identity)?
    ))
}

pub fn params_hash(slot: &SlotConfig) -> anyhow::Result<String> {
    digest12(&Value::Object(Map::from_iter(slot.params.clone())))
}

pub fn build_paper_candidate(input: PaperCandidateInput<'_>) -> anyhow::Result<PaperRiskCandidate> {
    if !input.requested_size.is_finite() || input.requested_size < 0.0 {
        return Err(anyhow!("requested_size must be finite and non-negative"));
    }
    let candidate_id = paper_candidate_id(&input)?;
    Ok(PaperRiskCandidate {
        paper_candidate_schema_version: PAPER_RISK_CANDIDATE_SCHEMA_VERSION,
        strategy_id: candidate_id.clone(),
        candidate_id,
        symbol_or_universe: input.slot.asset.clone(),
        slot_index: input.slot_index,
        slot_id: input.slot_id.to_string(),
        slot_key: paper_slot_key(input.slot_id)?,
        asset: input.slot.asset.clone(),
        timeframe: input.slot.timeframe.clone(),
        strategy_name: input.slot.strategy_name.clone(),
        params_hash: params_hash(input.slot)?,
        aux_source_id: input.slot.aux_source.as_ref().map(|a| a.id.clone()),
        config_fingerprint: input.config_fingerprint.to_string(),
        data_window_fingerprint: input.data_window_fingerprint.to_string(),
        latest_bar_timestamp: input.latest_bar_timestamp.to_string(),
        requested_size: input.requested_size,
        requested_size_basis: PAPER_REQUESTED_SIZE_BASIS,
        risk_mode: input.risk_mode.as_str().to_string(),
        artifact_root: input.artifact_root.to_string(),
        validation_refs: vec![
            "risk/contracts/v1/risk_contract_v1.schema.json".to_string(),
            "scripts/validate_risk_contract.py".to_string(),
        ],
    })
}

pub fn build_paper_v2_candidate(
    input: PaperV2CandidateInput<'_>,
) -> anyhow::Result<PaperRiskCandidateV2> {
    if input.base.risk_mode != PaperRiskMode::Apply {
        bail!("v2 paper candidate requires paper risk apply mode");
    }
    if !input.base.requested_size.is_finite() || input.base.requested_size <= 0.0 {
        bail!("v2 paper requested_size must be finite and positive");
    }
    ensure_finite_non_negative("initial_capital", input.initial_capital)?;
    ensure_finite_non_negative("effective_leverage", input.effective_leverage)?;
    if input.slot_count == 0 {
        bail!("v2 paper slot_count must be positive");
    }
    if input.runtime_accounting_mode.trim().is_empty() {
        bail!("v2 paper runtime_accounting_mode must not be empty");
    }
    let candidate_id =
        paper_candidate_id_for_schema(&input.base, PAPER_V2_CANDIDATE_SCHEMA_VERSION)?;
    Ok(PaperRiskCandidateV2 {
        candidate_schema_version: PAPER_V2_CANDIDATE_SCHEMA_VERSION,
        candidate_id: candidate_id.clone(),
        strategy_id: candidate_id,
        symbol_or_universe: input.base.slot.asset.clone(),
        timeframe: input.base.slot.timeframe.clone(),
        validation_refs: vec![
            PAPER_V2_SCHEMA_REF.to_string(),
            PAPER_V2_RESULT_SCHEMA_REF.to_string(),
            PAPER_RISK_GATE_VALIDATOR_REF.to_string(),
        ],
        surface: PaperV2CandidateSurface {
            runtime_surface: "paper",
            surface_status: "implemented",
            analysis_scope: "none",
            analysis_scope_status: "not_applicable",
        },
        sizing: PaperV2CandidateSizing {
            requested_size: input.base.requested_size,
            requested_size_basis: PAPER_V2_REQUESTED_SIZE_BASIS,
        },
        surface_payload: PaperV2CandidateSurfacePayload {
            slot_id: input.base.slot_id.to_string(),
            slot_index: input.base.slot_index,
            slot_key: paper_slot_key(input.base.slot_id)?,
            allocation_source: "PaperConfig::allocations",
            allocation_method: "initial_capital_divided_by_slot_count",
            initial_capital: input.initial_capital,
            slot_count: input.slot_count,
            effective_leverage: input.effective_leverage,
            runtime_accounting_mode: input.runtime_accounting_mode.to_string(),
            paper_risk_mode: input.base.risk_mode.as_str().to_string(),
        },
        artifact_root: input.base.artifact_root.to_string(),
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PaperRiskExecutionState {
    Observed,
    Continued,
    Stopped,
    GateError,
}

impl PaperRiskExecutionState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Observed => "observed",
            Self::Continued => "continued",
            Self::Stopped => "stopped",
            Self::GateError => "gate_error",
        }
    }
}

#[derive(Debug, Clone)]
pub struct PaperRiskDecision {
    pub decision_class: String,
    pub allowed_size: f64,
    pub binding_rule: String,
    pub fail_close_reason: String,
    pub policy_version: String,
    pub decision_artifact_path: String,
    pub decision_artifact_sha256: String,
    pub policy_path: String,
    pub policy_sha256: String,
    pub validator_valid: bool,
    pub validator_errors: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperCostModel {
    pub schema_version: u32,
    pub status: String,
    pub fee_bps: f64,
    pub spread_bps: f64,
    pub cost_basis: String,
    pub cost_model_source: String,
    pub cost_model_fingerprint: String,
    pub estimated_net_pnl_claim_allowed: bool,
    pub parity_claim_allowed: bool,
    pub alpha_claim_allowed: bool,
    pub claim_block_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperCostEstimate {
    pub cost_notional: f64,
    pub gross_pnl: f64,
    pub estimated_cost: f64,
    pub estimated_net_pnl: f64,
}

impl PaperCostModel {
    pub fn new(fee_bps: f64, spread_bps: f64, source: impl Into<String>) -> anyhow::Result<Self> {
        ensure_valid_bps("fee_bps", fee_bps)?;
        ensure_valid_bps("spread_bps", spread_bps)?;
        let source = source.into();
        if source.trim().is_empty() {
            bail!("cost_model_source must not be empty");
        }
        let status = if fee_bps == 0.0 && spread_bps == 0.0 {
            PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO
        } else {
            PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO
        };
        let estimated_net_pnl_claim_allowed = status == PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO;
        let claim_block_reason = if estimated_net_pnl_claim_allowed {
            Some("runtime_net_pnl_not_integrated".to_string())
        } else {
            Some(status.to_string())
        };
        let fingerprint = cost_model_fingerprint(fee_bps, spread_bps, PAPER_COST_BASIS, &source)?;
        Ok(Self {
            schema_version: PAPER_COST_MODEL_SCHEMA_VERSION,
            status: status.to_string(),
            fee_bps,
            spread_bps,
            cost_basis: PAPER_COST_BASIS.to_string(),
            cost_model_source: source,
            cost_model_fingerprint: fingerprint,
            estimated_net_pnl_claim_allowed,
            parity_claim_allowed: false,
            alpha_claim_allowed: false,
            claim_block_reason,
        })
    }

    pub fn missing_or_zero() -> Self {
        Self::new(0.0, 0.0, PAPER_COST_MODEL_SOURCE_CLI).expect("zero paper cost model is valid")
    }

    pub fn estimate(
        &self,
        requested_size: f64,
        leverage: f64,
        gross_pnl: f64,
    ) -> anyhow::Result<PaperCostEstimate> {
        ensure_finite_non_negative("requested_size", requested_size)?;
        ensure_finite_non_negative("leverage", leverage)?;
        if !gross_pnl.is_finite() {
            bail!("gross_pnl must be finite");
        }
        let cost_notional = requested_size * leverage;
        ensure_finite("cost_notional", cost_notional)?;
        let estimated_cost = cost_notional * ((self.fee_bps + self.spread_bps) / 10_000.0);
        ensure_finite("estimated_cost", estimated_cost)?;
        let estimated_net_pnl = gross_pnl - estimated_cost;
        ensure_finite("estimated_net_pnl", estimated_net_pnl)?;
        Ok(PaperCostEstimate {
            cost_notional,
            gross_pnl,
            estimated_cost,
            estimated_net_pnl,
        })
    }
}

fn ensure_valid_bps(name: &str, value: f64) -> anyhow::Result<()> {
    ensure_finite_non_negative(name, value)?;
    if value > 10_000.0 {
        bail!("{name} must be <= 10000 bps");
    }
    Ok(())
}

fn ensure_finite_non_negative(name: &str, value: f64) -> anyhow::Result<()> {
    if !value.is_finite() || value < 0.0 {
        bail!("{name} must be finite and non-negative");
    }
    Ok(())
}

fn ensure_finite(name: &str, value: f64) -> anyhow::Result<()> {
    if !value.is_finite() {
        bail!("{name} must be finite");
    }
    Ok(())
}

fn cost_model_fingerprint(
    fee_bps: f64,
    spread_bps: f64,
    cost_basis: &str,
    source: &str,
) -> anyhow::Result<String> {
    let value = Value::Object(Map::from_iter([
        (
            "cost_model_schema_version".to_string(),
            Value::from(PAPER_COST_MODEL_SCHEMA_VERSION),
        ),
        ("fee_bps".to_string(), Value::from(fee_bps)),
        ("spread_bps".to_string(), Value::from(spread_bps)),
        ("cost_basis".to_string(), Value::from(cost_basis)),
        ("cost_model_source".to_string(), Value::from(source)),
    ]));
    let digest = Sha256::digest(canonical_json_bytes(&value)?);
    Ok(format!("sha256:{}", hex::encode(digest)))
}

#[derive(Debug, Clone)]
pub struct PaperRiskEvidenceInput<'a, C: PaperRiskEvidenceCandidateRef + ?Sized> {
    pub run_id: &'a str,
    pub tick_id: &'a str,
    pub candidate: &'a C,
    pub decision: PaperRiskDecision,
    pub candidate_artifact_path: &'a str,
    pub candidate_artifact_sha256: &'a str,
    pub execution_state: PaperRiskExecutionState,
    pub position_mutation: bool,
    pub db_before: &'a str,
    pub db_after: &'a str,
    pub db_before_artifact_path: &'a str,
    pub db_before_artifact_sha256: &'a str,
    pub db_after_artifact_path: &'a str,
    pub db_after_artifact_sha256: &'a str,
    pub health_artifact_path: &'a str,
    pub health_artifact_sha256: &'a str,
    pub parity_status: &'a str,
    pub cost_model: PaperCostModel,
    pub effective_leverage: f64,
    pub gross_pnl: f64,
    pub runtime_sizing_applied: bool,
    pub actual_effective_size: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperRiskEvidence {
    pub run_id: String,
    pub tick_id: String,
    pub slot_id: String,
    pub candidate_id: String,
    pub candidate_artifact_path: String,
    pub candidate_artifact_sha256: String,
    pub decision_artifact_path: String,
    pub decision_artifact_sha256: String,
    pub policy_path: String,
    pub policy_sha256: String,
    pub validator_valid: bool,
    pub validator_errors: Vec<String>,
    pub risk_mode: String,
    pub decision_class: String,
    pub execution_state: String,
    pub position_mutation: bool,
    pub db_before: String,
    pub db_after: String,
    pub requested_size: f64,
    pub requested_size_basis: String,
    pub allowed_size: f64,
    pub allowed_size_basis: String,
    pub would_effective_size: f64,
    pub actual_effective_size: f64,
    pub runtime_sizing_applied: bool,
    pub cap_application_status: Option<String>,
    pub cap_runtime_sizing_dependency_status: String,
    pub cap_depends_on_runtime_pnl: bool,
    pub cap_dependency_proof: String,
    pub cap_runtime_sizing_claim_allowed: bool,
    pub cap_runtime_sizing_claim_block_reason: Option<String>,
    pub paper_fee_model_status: String,
    pub cost_model_schema_version: u32,
    pub fee_bps: f64,
    pub spread_bps: f64,
    pub cost_basis: String,
    pub cost_model_source: String,
    pub cost_model_fingerprint: String,
    pub cost_notional: f64,
    pub gross_pnl: f64,
    pub estimated_cost: f64,
    pub estimated_net_pnl: f64,
    pub estimated_net_pnl_claim_allowed: bool,
    pub parity_claim_allowed: bool,
    pub alpha_claim_allowed: bool,
    pub claim_block_reason: Option<String>,
    pub db_before_artifact_path: String,
    pub db_before_artifact_sha256: String,
    pub db_after_artifact_path: String,
    pub db_after_artifact_sha256: String,
    pub health_artifact_path: String,
    pub health_artifact_sha256: String,
    pub parity_status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub risk_contract_schema_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub risk_contract_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validator_result_schema_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validated_schema_ref: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validator: Option<String>,
}

impl PaperRiskEvidence {
    pub fn from_input<C: PaperRiskEvidenceCandidateRef + ?Sized>(
        input: PaperRiskEvidenceInput<'_, C>,
    ) -> Self {
        let is_cap = input.decision.decision_class == "cap";
        let estimate = input
            .cost_model
            .estimate(
                input.candidate.requested_size(),
                input.effective_leverage,
                input.gross_pnl,
            )
            .expect("validated paper cost model estimate");
        Self {
            run_id: input.run_id.to_string(),
            tick_id: input.tick_id.to_string(),
            slot_id: input.candidate.slot_id().to_string(),
            candidate_id: input.candidate.candidate_id().to_string(),
            candidate_artifact_path: input.candidate_artifact_path.to_string(),
            candidate_artifact_sha256: input.candidate_artifact_sha256.to_string(),
            decision_artifact_path: input.decision.decision_artifact_path,
            decision_artifact_sha256: input.decision.decision_artifact_sha256,
            policy_path: input.decision.policy_path,
            policy_sha256: input.decision.policy_sha256,
            validator_valid: input.decision.validator_valid,
            validator_errors: input.decision.validator_errors,
            risk_mode: input.candidate.risk_mode().to_string(),
            decision_class: input.decision.decision_class,
            execution_state: input.execution_state.as_str().to_string(),
            position_mutation: input.position_mutation,
            db_before: input.db_before.to_string(),
            db_after: input.db_after.to_string(),
            requested_size: input.candidate.requested_size(),
            requested_size_basis: input.candidate.requested_size_basis().to_string(),
            allowed_size: input.decision.allowed_size,
            allowed_size_basis: PAPER_ALLOWED_SIZE_BASIS.to_string(),
            would_effective_size: input.decision.allowed_size,
            actual_effective_size: input.actual_effective_size,
            runtime_sizing_applied: input.runtime_sizing_applied,
            cap_application_status: is_cap.then(|| {
                if input.runtime_sizing_applied {
                    PAPER_CAP_APPLICATION_STATUS_APPLIED.to_string()
                } else {
                    PAPER_CAP_APPLICATION_STATUS.to_string()
                }
            }),
            cap_runtime_sizing_dependency_status: if is_cap {
                PAPER_CAP_RUNTIME_SIZING_DEPENDENCY_STATUS_PNL_INDEPENDENT_PROVEN.to_string()
            } else {
                PAPER_CAP_RUNTIME_SIZING_DEPENDENCY_STATUS_PNL_DEPENDENCY_UNPROVEN.to_string()
            },
            cap_depends_on_runtime_pnl: false,
            cap_dependency_proof: if is_cap {
                PAPER_CAP_DEPENDENCY_PROOF_PNL_INDEPENDENT.to_string()
            } else {
                "not_applicable_for_non_cap_decision".to_string()
            },
            cap_runtime_sizing_claim_allowed: is_cap
                && input.runtime_sizing_applied
                && input.cost_model.status == PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO,
            cap_runtime_sizing_claim_block_reason: if is_cap {
                if input.cost_model.status == PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO {
                    Some(PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO.to_string())
                } else if input.runtime_sizing_applied {
                    None
                } else {
                    Some(PAPER_CAP_RUNTIME_SIZING_CLAIM_BLOCK_REASON_NOT_APPLIED.to_string())
                }
            } else {
                None
            },
            paper_fee_model_status: input.cost_model.status.clone(),
            cost_model_schema_version: input.cost_model.schema_version,
            fee_bps: input.cost_model.fee_bps,
            spread_bps: input.cost_model.spread_bps,
            cost_basis: input.cost_model.cost_basis.clone(),
            cost_model_source: input.cost_model.cost_model_source.clone(),
            cost_model_fingerprint: input.cost_model.cost_model_fingerprint.clone(),
            cost_notional: estimate.cost_notional,
            gross_pnl: estimate.gross_pnl,
            estimated_cost: estimate.estimated_cost,
            estimated_net_pnl: estimate.estimated_net_pnl,
            estimated_net_pnl_claim_allowed: input.cost_model.estimated_net_pnl_claim_allowed,
            parity_claim_allowed: input.cost_model.parity_claim_allowed,
            alpha_claim_allowed: input.cost_model.alpha_claim_allowed,
            claim_block_reason: input.cost_model.claim_block_reason.clone(),
            db_before_artifact_path: input.db_before_artifact_path.to_string(),
            db_before_artifact_sha256: input.db_before_artifact_sha256.to_string(),
            db_after_artifact_path: input.db_after_artifact_path.to_string(),
            db_after_artifact_sha256: input.db_after_artifact_sha256.to_string(),
            health_artifact_path: input.health_artifact_path.to_string(),
            health_artifact_sha256: input.health_artifact_sha256.to_string(),
            parity_status: input.parity_status.to_string(),
            risk_contract_schema_version: input
                .candidate
                .risk_contract_schema_version()
                .map(str::to_string),
            risk_contract_version: input.candidate.risk_contract_version().map(str::to_string),
            validator_result_schema_version: input
                .candidate
                .validator_result_schema_version()
                .map(str::to_string),
            validated_schema_ref: input.candidate.validated_schema_ref().map(str::to_string),
            validator: input.candidate.validator().map(str::to_string),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PaperApplyAction {
    Continue,
    StopSlot,
    StopTick,
    ApplySizing,
}

pub fn validate_paper_runtime_sizing_guard(
    decision_class: &str,
    requested_size: f64,
    allowed_size: f64,
) -> anyhow::Result<()> {
    if !requested_size.is_finite() || requested_size < 0.0 {
        bail!("paper requested_size must be finite and non-negative");
    }
    if !allowed_size.is_finite() || allowed_size < 0.0 {
        bail!("paper {decision_class} allowed_size must be finite and non-negative");
    }
    if decision_class == "cap" && allowed_size > requested_size {
        bail!(
            "paper cap allowed_size must be <= requested_size: allowed_size={allowed_size}, requested_size={requested_size}"
        );
    }
    Ok(())
}

pub fn paper_apply_action_for_decision(decision_class: &str) -> anyhow::Result<PaperApplyAction> {
    match decision_class {
        "size" => Ok(PaperApplyAction::Continue),
        "reject" | "block" => Ok(PaperApplyAction::StopSlot),
        "kill" => Ok(PaperApplyAction::StopTick),
        "cap" => Ok(PaperApplyAction::ApplySizing),
        other => anyhow::bail!("unknown paper decision_class: {other}"),
    }
}
