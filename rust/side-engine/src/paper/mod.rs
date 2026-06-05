pub mod db;
pub mod position;
pub mod risk;

/// Special signal value used to mark a forced margin liquidation trade.
pub const SIGNAL_LIQUIDATION: i8 = -99;

use crate::fetcher::types::Bar;
use chrono::Datelike;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::path::Path;

use self::risk::{
    PAPER_PNL_SEMANTICS_LEGACY_GROSS, PAPER_RUNTIME_ACCOUNTING_MODE_ESTIMATED_NET,
    PAPER_RUNTIME_ACCOUNTING_MODE_LEGACY_GROSS,
    PAPER_RUNTIME_EQUITY_NET_ACCOUNTING_DEFERRED_REASON, PAPER_RUNTIME_EQUITY_USES_NET_PNL,
    PAPER_RUNTIME_NET_PNL_CONTRACT_STATUS, PAPER_RUNTIME_NET_PNL_INTEGRATION_STATUS,
    PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET, PAPER_RUNTIME_PNL_SOURCE_LEGACY_GROSS,
};

/// Returns true if current UTC time is during FX weekend (market closed).
/// FX closes Friday ~22:00 UTC, reopens Sunday ~22:00 UTC.
/// We use a conservative check: skip Saturday and Sunday entirely.
pub fn is_weekend() -> bool {
    let now = chrono::Utc::now();
    matches!(now.weekday(), chrono::Weekday::Sat | chrono::Weekday::Sun)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperConfig {
    pub slots: Vec<SlotConfig>,
    #[serde(default = "default_capital")]
    pub initial_capital: f64,
    #[serde(default = "default_weight_method")]
    pub weight_method: String,
    #[serde(default = "default_lookback")]
    pub data_lookback_bars: usize,
    #[serde(default = "default_tick_interval")]
    pub tick_interval_seconds: u64,
    #[serde(default = "default_health_file")]
    pub health_file: String,
    #[serde(default = "default_db_path")]
    pub db_path: String,
    #[serde(default = "default_leverage")]
    pub leverage: f64,
    #[serde(default = "default_maintenance_margin")]
    pub maintenance_margin_pct: f64,
    #[serde(default)]
    pub paper_fee_bps: f64,
    #[serde(default)]
    pub paper_spread_bps: f64,
    #[serde(default = "default_paper_cost_model_source")]
    pub paper_cost_model_source: String,
    #[serde(default = "default_runtime_accounting_mode")]
    pub runtime_accounting_mode: RuntimeAccountingMode,
}

fn default_capital() -> f64 {
    10000.0
}
fn default_weight_method() -> String {
    "equal".to_string()
}
fn default_lookback() -> usize {
    500
}
fn default_tick_interval() -> u64 {
    3600
}
fn default_health_file() -> String {
    "data/paper_health.json".to_string()
}
fn default_db_path() -> String {
    "data/paper_trades.db".to_string()
}
fn default_leverage() -> f64 {
    1.0
}
fn default_maintenance_margin() -> f64 {
    0.2
}
fn default_paper_cost_model_source() -> String {
    risk::PAPER_COST_MODEL_SOURCE_CLI.to_string()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeAccountingMode {
    LegacyGross,
    EstimatedNet,
}

impl RuntimeAccountingMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::LegacyGross => PAPER_RUNTIME_ACCOUNTING_MODE_LEGACY_GROSS,
            Self::EstimatedNet => PAPER_RUNTIME_ACCOUNTING_MODE_ESTIMATED_NET,
        }
    }
}

fn default_runtime_accounting_mode() -> RuntimeAccountingMode {
    RuntimeAccountingMode::LegacyGross
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SlotConfig {
    pub asset: String,
    pub strategy_name: String,
    pub params: HashMap<String, Value>,
    #[serde(default)]
    pub aux_source: Option<AuxSource>,
    #[serde(default = "default_timeframe")]
    pub timeframe: String,
    #[serde(default)]
    pub leverage: Option<f64>,
}

fn default_timeframe() -> String {
    "1h".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuxSource {
    pub id: String,
}

impl PaperConfig {
    pub fn from_file(path: impl AsRef<Path>) -> anyhow::Result<Self> {
        let content = std::fs::read_to_string(path)?;
        Ok(serde_json::from_str(&content)?)
    }

    pub fn slot_ids(&self) -> Vec<String> {
        self.slots
            .iter()
            .enumerate()
            .map(|(i, s)| {
                let aux_part = s
                    .aux_source
                    .as_ref()
                    .map(|a| a.id.split(':').nth(1).unwrap_or(&a.id).to_string())
                    .unwrap_or_default();
                if aux_part.is_empty() {
                    format!("{}/{}#{}", s.asset, s.strategy_name, i + 1)
                } else {
                    format!("{}/{}/{}#{}", s.asset, s.strategy_name, aux_part, i + 1)
                }
            })
            .collect()
    }

    pub fn allocations(&self) -> Vec<f64> {
        let n = self.slots.len() as f64;
        vec![self.initial_capital / n; self.slots.len()]
    }

    /// Returns the effective leverage for a given slot index.
    /// Per-slot override takes precedence over the global default.
    pub fn effective_leverage(&self, slot_index: usize) -> f64 {
        self.slots
            .get(slot_index)
            .and_then(|s| s.leverage)
            .unwrap_or(self.leverage)
    }

    pub fn paper_cost_model(&self) -> anyhow::Result<risk::PaperCostModel> {
        risk::PaperCostModel::new(
            self.paper_fee_bps,
            self.paper_spread_bps,
            self.paper_cost_model_source.clone(),
        )
    }
}

pub struct PaperTrader {
    config: PaperConfig,
    db: db::PaperDb,
    position_mgr: position::PositionManager,
    slot_ids: Vec<String>,
    allocations: Vec<f64>,
    last_tick: Option<String>,
    errors: Vec<String>,
    last_risk_summary: Option<PaperRiskHealthSummary>,
    last_runtime_pnl_summary: Option<PaperRuntimePnlHealthSummary>,
}

#[derive(Debug, Clone)]
struct PaperRiskHealthSummary {
    risk_mode: String,
    last_risk_evidence_path: String,
    last_risk_status: String,
    skipped_reason: Option<String>,
    paper_fee_model_status: Option<String>,
    cost_model_fingerprint: Option<String>,
    decision_class: Option<String>,
    runtime_sizing_applied: Option<bool>,
    cap_application_status: Option<String>,
    cap_runtime_sizing_dependency_status: Option<String>,
    cap_depends_on_runtime_pnl: Option<bool>,
    cap_runtime_sizing_claim_allowed: Option<bool>,
    cap_runtime_sizing_claim_block_reason: Option<String>,
}

#[derive(Debug, Clone)]
struct PaperRuntimePnlHealthSummary {
    trade_pnl_breakdown_id: i64,
    trade_pnl_breakdown_path: String,
    last_runtime_accounting_event_id: Option<i64>,
    last_runtime_pnl_ledger_id: Option<i64>,
    runtime_pnl_source: String,
    runtime_equity_pnl_source: String,
    runtime_realized_pnl_source: String,
    runtime_net_pnl_claim_allowed: bool,
    runtime_equity_uses_net_pnl: bool,
    runtime_equity_net_accounting_claim_allowed: bool,
    runtime_equity_net_accounting_claim_block_reason: Option<String>,
    paper_fee_model_status: String,
    cost_model_fingerprint: String,
}

impl PaperTrader {
    pub fn new(config: PaperConfig, db: db::PaperDb) -> Self {
        let slot_ids = config.slot_ids();
        let allocations = config.allocations();
        let position_mgr = position::PositionManager::new(config.initial_capital);
        Self {
            config,
            db,
            position_mgr,
            slot_ids,
            allocations,
            last_tick: None,
            errors: Vec::new(),
            last_risk_summary: None,
            last_runtime_pnl_summary: None,
        }
    }

    pub fn db(&self) -> &db::PaperDb {
        &self.db
    }

    pub fn apply_runtime_size_override(
        &mut self,
        slot_id: &str,
        effective_size: f64,
    ) -> anyhow::Result<()> {
        if !effective_size.is_finite() || effective_size < 0.0 {
            anyhow::bail!("effective_size must be finite and non-negative");
        }
        let Some(index) = self.slot_ids.iter().position(|id| id == slot_id) else {
            anyhow::bail!("unknown paper slot_id for runtime size override: {slot_id}");
        };
        self.allocations[index] = effective_size;
        Ok(())
    }

    pub fn tick_with_data(
        &mut self,
        data: &HashMap<String, (Vec<Bar>, Option<Vec<f64>>)>,
    ) -> anyhow::Result<()> {
        use crate::strategies::{generate_signals, Ohlcv};

        let now = chrono::Utc::now().to_rfc3339();
        self.last_tick = Some(now);

        // Build a price map (slot_id -> latest close) for margin checking
        let mut latest_prices: HashMap<String, f64> = HashMap::new();
        for (i, slot) in self.config.slots.iter().enumerate() {
            let slot_id = &self.slot_ids[i];
            if let Some((bars, _)) = data.get(&slot.asset) {
                if let Some(last_bar) = bars.last() {
                    latest_prices.insert(slot_id.clone(), last_bar.close);
                }
            }
        }

        // Check margin maintenance BEFORE processing new signals
        let maintenance_margin_pct = self.config.maintenance_margin_pct;
        let paper_cost_model = self.config.paper_cost_model()?;
        self.ensure_margin_liquidations_allowed_for_accounting_mode(
            &latest_prices,
            &paper_cost_model,
        )?;
        let mut runtime_close_recorded_this_tick = false;
        match self
            .position_mgr
            .check_margin_and_liquidate_with_accounting_mode(
                &self.db,
                maintenance_margin_pct,
                &latest_prices,
                self.config.runtime_accounting_mode,
                Some(&paper_cost_model),
            ) {
            Ok(outcomes) => {
                for outcome in outcomes {
                    if let Some(close) = &outcome.runtime_close {
                        self.set_last_runtime_pnl_summary_from_estimated_net_close(close);
                        runtime_close_recorded_this_tick = true;
                    }
                    tracing::warn!(
                        slot_id = outcome.slot_id,
                        "position liquidated due to margin call"
                    );
                }
            }
            Err(e) => {
                self.errors.push(format!("margin check error: {}", e));
                tracing::error!(error = %e, "margin check failed");
            }
        }

        for i in 0..self.config.slots.len() {
            let slot = self.config.slots[i].clone();
            let slot_id = self.slot_ids[i].clone();
            let allocation = self.allocations[i];

            let Some((bars, aux)) = data.get(&slot.asset) else {
                self.errors.push(format!("no data for {}", slot.asset));
                continue;
            };

            if bars.len() < 50 {
                self.errors.push(format!(
                    "insufficient bars for {}: {}",
                    slot.asset,
                    bars.len()
                ));
                continue;
            }

            let open: Vec<f64> = bars.iter().map(|b| b.open).collect();
            let high: Vec<f64> = bars.iter().map(|b| b.high).collect();
            let low: Vec<f64> = bars.iter().map(|b| b.low).collect();
            let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
            let volume: Vec<f64> = bars.iter().map(|b| b.volume).collect();
            let datetimes_ns: Vec<i64> = bars
                .iter()
                .map(|b| b.datetime.and_utc().timestamp_nanos_opt().unwrap_or(0))
                .collect();

            let ohlcv = Ohlcv {
                open: &open,
                high: &high,
                low: &low,
                close: &close,
                volume: &volume,
                datetimes_ns: Some(&datetimes_ns),
                aux_close: aux.as_deref(),
            };

            let signals = generate_signals(&slot.strategy_name, &ohlcv, &slot.params);
            let latest_signal = *signals.last().unwrap_or(&0);
            let latest_close = *close.last().unwrap_or(&0.0);
            let latest_aux = aux.as_ref().and_then(|a| a.last().copied());

            let effective_leverage = self.config.effective_leverage(i);
            let has_position = self.db.get_position(&slot_id)?.is_some();
            Self::ensure_close_allowed_for_accounting_mode(
                self.config.runtime_accounting_mode,
                has_position,
                latest_signal,
                &paper_cost_model,
            )?;
            match self.position_mgr.process_signal_with_accounting_mode(
                &self.db,
                &slot_id,
                latest_signal,
                latest_close,
                latest_aux,
                allocation,
                effective_leverage,
                self.config.runtime_accounting_mode,
                Some(&paper_cost_model),
            ) {
                Ok(outcome) => {
                    if let Some(close) = &outcome.runtime_close {
                        self.set_last_runtime_pnl_summary_from_estimated_net_close(close);
                        runtime_close_recorded_this_tick = true;
                    }
                    tracing::info!(
                        slot_id,
                        signal = latest_signal,
                        price = latest_close,
                        action = outcome.action,
                        "tick processed"
                    );
                }
                Err(e) => {
                    self.errors.push(format!("{}: {}", slot_id, e));
                    tracing::error!(slot_id, error = %e, "tick error");
                }
            }
        }

        if !runtime_close_recorded_this_tick
            && self.config.runtime_accounting_mode == RuntimeAccountingMode::LegacyGross
        {
            if let Some(ledger) = self.db.get_latest_runtime_pnl_ledger_entry()? {
                self.set_last_runtime_pnl_summary(
                    ledger.breakdown_id,
                    format!("sqlite:trade_pnl_breakdowns/{}", ledger.breakdown_id),
                    ledger.paper_fee_model_status.clone(),
                    ledger.cost_model_fingerprint.clone(),
                    ledger.runtime_net_pnl_claim_allowed,
                );
                self.set_last_runtime_pnl_ledger_summary(
                    ledger.id,
                    PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET,
                    ledger.runtime_net_pnl_claim_allowed,
                )?;
            }
        }

        self.position_mgr.record_portfolio_snapshot(&self.db)?;
        Ok(())
    }

    fn ensure_margin_liquidations_allowed_for_accounting_mode(
        &self,
        latest_prices: &HashMap<String, f64>,
        cost_model: &risk::PaperCostModel,
    ) -> anyhow::Result<()> {
        if self.config.runtime_accounting_mode != RuntimeAccountingMode::EstimatedNet {
            return Ok(());
        }

        for pos in self.db.get_all_open_positions()? {
            let Some(&current_price) = latest_prices.get(&pos.slot_id) else {
                continue;
            };
            let unrealized_pnl =
                (current_price - pos.entry_price) / pos.entry_price * pos.size * pos.leverage;
            let margin_ratio = (pos.size + unrealized_pnl) / pos.size;
            if margin_ratio <= self.config.maintenance_margin_pct {
                Self::ensure_close_allowed_for_accounting_mode(
                    self.config.runtime_accounting_mode,
                    true,
                    SIGNAL_LIQUIDATION,
                    cost_model,
                )?;
            }
        }

        Ok(())
    }

    fn ensure_close_allowed_for_accounting_mode(
        mode: RuntimeAccountingMode,
        has_position: bool,
        signal: i8,
        cost_model: &risk::PaperCostModel,
    ) -> anyhow::Result<()> {
        if mode != RuntimeAccountingMode::EstimatedNet || !has_position || signal == 1 {
            return Ok(());
        }
        if !cost_model.estimated_net_pnl_claim_allowed {
            anyhow::bail!("estimated_net runtime accounting requires explicit nonzero cost model");
        }
        Ok(())
    }

    pub fn health_json(&self) -> String {
        let (total_equity, total_return_pct) = self.position_mgr.portfolio_summary();
        let status = if self.errors.is_empty() {
            "running"
        } else {
            "error"
        };
        let mut health = serde_json::json!({
            "timestamp": chrono::Utc::now().to_rfc3339(),
            "status": status,
            "last_tick": self.last_tick,
            "total_equity": total_equity,
            "total_return_pct": total_return_pct,
            "runtime_accounting_mode": self.config.runtime_accounting_mode.as_str(),
            "runtime_equity_uses_net_pnl": PAPER_RUNTIME_EQUITY_USES_NET_PNL,
            "runtime_equity_pnl_source": PAPER_PNL_SEMANTICS_LEGACY_GROSS,
            "runtime_realized_pnl_source": PAPER_PNL_SEMANTICS_LEGACY_GROSS,
            "runtime_equity_net_accounting_claim_allowed": false,
            "runtime_equity_net_accounting_claim_block_reason":
                PAPER_RUNTIME_EQUITY_NET_ACCOUNTING_DEFERRED_REASON,
            "errors": self.errors,
        });
        if let Some(summary) = &self.last_risk_summary {
            if let Some(obj) = health.as_object_mut() {
                obj.insert("risk_mode".to_string(), summary.risk_mode.clone().into());
                obj.insert(
                    "last_risk_evidence_path".to_string(),
                    summary.last_risk_evidence_path.clone().into(),
                );
                obj.insert(
                    "last_risk_status".to_string(),
                    summary.last_risk_status.clone().into(),
                );
                if let Some(skipped_reason) = &summary.skipped_reason {
                    obj.insert("skipped_reason".to_string(), skipped_reason.clone().into());
                }
                if let Some(status) = &summary.paper_fee_model_status {
                    obj.insert("paper_fee_model_status".to_string(), status.clone().into());
                }
                if let Some(fingerprint) = &summary.cost_model_fingerprint {
                    obj.insert(
                        "cost_model_fingerprint".to_string(),
                        fingerprint.clone().into(),
                    );
                }
                if let Some(decision_class) = &summary.decision_class {
                    obj.insert(
                        "last_risk_decision_class".to_string(),
                        decision_class.clone().into(),
                    );
                }
                if let Some(runtime_sizing_applied) = summary.runtime_sizing_applied {
                    obj.insert(
                        "runtime_sizing_applied".to_string(),
                        runtime_sizing_applied.into(),
                    );
                }
                if let Some(status) = &summary.cap_application_status {
                    obj.insert("cap_application_status".to_string(), status.clone().into());
                }
                if let Some(status) = &summary.cap_runtime_sizing_dependency_status {
                    obj.insert(
                        "cap_runtime_sizing_dependency_status".to_string(),
                        status.clone().into(),
                    );
                }
                if let Some(depends_on_runtime_pnl) = summary.cap_depends_on_runtime_pnl {
                    obj.insert(
                        "cap_depends_on_runtime_pnl".to_string(),
                        depends_on_runtime_pnl.into(),
                    );
                }
                if let Some(claim_allowed) = summary.cap_runtime_sizing_claim_allowed {
                    obj.insert(
                        "cap_runtime_sizing_claim_allowed".to_string(),
                        claim_allowed.into(),
                    );
                }
                if summary.cap_application_status.is_some() {
                    match &summary.cap_runtime_sizing_claim_block_reason {
                        Some(reason) => {
                            obj.insert(
                                "cap_runtime_sizing_claim_block_reason".to_string(),
                                reason.clone().into(),
                            );
                        }
                        None => {
                            obj.insert(
                                "cap_runtime_sizing_claim_block_reason".to_string(),
                                serde_json::Value::Null,
                            );
                        }
                    }
                }
            }
        }
        if let Some(summary) = &self.last_runtime_pnl_summary {
            if let Some(obj) = health.as_object_mut() {
                obj.insert(
                    "last_trade_pnl_breakdown_id".to_string(),
                    summary.trade_pnl_breakdown_id.into(),
                );
                obj.insert(
                    "last_trade_pnl_breakdown_path".to_string(),
                    summary.trade_pnl_breakdown_path.clone().into(),
                );
                obj.insert(
                    "runtime_net_pnl_contract_status".to_string(),
                    PAPER_RUNTIME_NET_PNL_CONTRACT_STATUS.into(),
                );
                obj.insert(
                    "runtime_net_pnl_integration_status".to_string(),
                    PAPER_RUNTIME_NET_PNL_INTEGRATION_STATUS.into(),
                );
                obj.insert(
                    "runtime_pnl_source".to_string(),
                    summary.runtime_pnl_source.clone().into(),
                );
                obj.insert(
                    "runtime_equity_pnl_source".to_string(),
                    summary.runtime_equity_pnl_source.clone().into(),
                );
                obj.insert(
                    "runtime_realized_pnl_source".to_string(),
                    summary.runtime_realized_pnl_source.clone().into(),
                );
                if let Some(ledger_id) = summary.last_runtime_pnl_ledger_id {
                    obj.insert("last_runtime_pnl_ledger_id".to_string(), ledger_id.into());
                }
                if let Some(event_id) = summary.last_runtime_accounting_event_id {
                    obj.insert(
                        "last_runtime_accounting_event_id".to_string(),
                        event_id.into(),
                    );
                }
                obj.insert(
                    "runtime_pnl_ledger_claim_allowed".to_string(),
                    summary.runtime_net_pnl_claim_allowed.into(),
                );
                obj.insert(
                    "runtime_equity_net_accounting_claim_allowed".to_string(),
                    summary.runtime_equity_net_accounting_claim_allowed.into(),
                );
                match &summary.runtime_equity_net_accounting_claim_block_reason {
                    Some(reason) => {
                        obj.insert(
                            "runtime_equity_net_accounting_claim_block_reason".to_string(),
                            reason.clone().into(),
                        );
                    }
                    None => {
                        obj.insert(
                            "runtime_equity_net_accounting_claim_block_reason".to_string(),
                            serde_json::Value::Null,
                        );
                    }
                }
                obj.insert(
                    "runtime_equity_uses_net_pnl".to_string(),
                    summary.runtime_equity_uses_net_pnl.into(),
                );
                obj.insert(
                    "paper_fee_model_status".to_string(),
                    summary.paper_fee_model_status.clone().into(),
                );
                obj.insert(
                    "cost_model_fingerprint".to_string(),
                    summary.cost_model_fingerprint.clone().into(),
                );
            }
        }
        health.to_string()
    }

    fn json_string(value: &serde_json::Value, key: &str) -> Option<String> {
        value.get(key).and_then(|v| v.as_str()).map(str::to_string)
    }

    fn json_bool(value: &serde_json::Value, key: &str) -> Option<bool> {
        value.get(key).and_then(|v| v.as_bool())
    }

    pub fn set_last_risk_summary_from_evidence(
        &mut self,
        risk_mode: impl Into<String>,
        evidence_path: impl Into<String>,
        status: impl Into<String>,
        skipped_reason: Option<String>,
        evidence: &serde_json::Value,
    ) {
        let decision_class = Self::json_string(evidence, "decision_class");
        let is_cap_decision = decision_class.as_deref() == Some("cap");
        self.last_risk_summary = Some(PaperRiskHealthSummary {
            risk_mode: risk_mode.into(),
            last_risk_evidence_path: evidence_path.into(),
            last_risk_status: status.into(),
            skipped_reason,
            paper_fee_model_status: Self::json_string(evidence, "paper_fee_model_status"),
            cost_model_fingerprint: Self::json_string(evidence, "cost_model_fingerprint"),
            decision_class,
            runtime_sizing_applied: Self::json_bool(evidence, "runtime_sizing_applied"),
            cap_application_status: is_cap_decision
                .then(|| Self::json_string(evidence, "cap_application_status"))
                .flatten(),
            cap_runtime_sizing_dependency_status: is_cap_decision
                .then(|| Self::json_string(evidence, "cap_runtime_sizing_dependency_status"))
                .flatten(),
            cap_depends_on_runtime_pnl: is_cap_decision
                .then(|| Self::json_bool(evidence, "cap_depends_on_runtime_pnl"))
                .flatten(),
            cap_runtime_sizing_claim_allowed: is_cap_decision
                .then(|| Self::json_bool(evidence, "cap_runtime_sizing_claim_allowed"))
                .flatten(),
            cap_runtime_sizing_claim_block_reason: is_cap_decision
                .then(|| Self::json_string(evidence, "cap_runtime_sizing_claim_block_reason"))
                .flatten(),
        });
    }

    pub fn set_last_risk_summary(
        &mut self,
        risk_mode: impl Into<String>,
        evidence_path: impl Into<String>,
        status: impl Into<String>,
        skipped_reason: Option<String>,
        paper_fee_model_status: Option<String>,
        cost_model_fingerprint: Option<String>,
    ) {
        self.last_risk_summary = Some(PaperRiskHealthSummary {
            risk_mode: risk_mode.into(),
            last_risk_evidence_path: evidence_path.into(),
            last_risk_status: status.into(),
            skipped_reason,
            paper_fee_model_status,
            cost_model_fingerprint,
            decision_class: None,
            runtime_sizing_applied: None,
            cap_application_status: None,
            cap_runtime_sizing_dependency_status: None,
            cap_depends_on_runtime_pnl: None,
            cap_runtime_sizing_claim_allowed: None,
            cap_runtime_sizing_claim_block_reason: None,
        });
    }

    pub fn set_last_runtime_pnl_summary(
        &mut self,
        trade_pnl_breakdown_id: i64,
        trade_pnl_breakdown_path: impl Into<String>,
        paper_fee_model_status: impl Into<String>,
        cost_model_fingerprint: impl Into<String>,
        runtime_net_pnl_claim_allowed: bool,
    ) {
        self.last_runtime_pnl_summary = Some(PaperRuntimePnlHealthSummary {
            trade_pnl_breakdown_id,
            trade_pnl_breakdown_path: trade_pnl_breakdown_path.into(),
            last_runtime_accounting_event_id: None,
            last_runtime_pnl_ledger_id: None,
            runtime_pnl_source: PAPER_RUNTIME_PNL_SOURCE_LEGACY_GROSS.to_string(),
            runtime_equity_pnl_source: PAPER_PNL_SEMANTICS_LEGACY_GROSS.to_string(),
            runtime_realized_pnl_source: PAPER_PNL_SEMANTICS_LEGACY_GROSS.to_string(),
            runtime_net_pnl_claim_allowed,
            runtime_equity_uses_net_pnl: PAPER_RUNTIME_EQUITY_USES_NET_PNL,
            runtime_equity_net_accounting_claim_allowed: false,
            runtime_equity_net_accounting_claim_block_reason: Some(
                PAPER_RUNTIME_EQUITY_NET_ACCOUNTING_DEFERRED_REASON.to_string(),
            ),
            paper_fee_model_status: paper_fee_model_status.into(),
            cost_model_fingerprint: cost_model_fingerprint.into(),
        });
    }

    pub fn set_last_runtime_pnl_ledger_summary(
        &mut self,
        ledger_id: i64,
        runtime_pnl_source: impl Into<String>,
        runtime_net_pnl_claim_allowed: bool,
    ) -> anyhow::Result<()> {
        let Some(summary) = &mut self.last_runtime_pnl_summary else {
            anyhow::bail!("runtime pnl ledger summary requires runtime pnl breakdown summary");
        };
        summary.last_runtime_pnl_ledger_id = Some(ledger_id);
        summary.runtime_pnl_source = runtime_pnl_source.into();
        summary.runtime_net_pnl_claim_allowed = runtime_net_pnl_claim_allowed;
        Ok(())
    }

    pub fn set_last_runtime_pnl_summary_from_estimated_net_close(
        &mut self,
        close: &db::EstimatedNetRuntimeClose,
    ) {
        self.last_runtime_pnl_summary = Some(PaperRuntimePnlHealthSummary {
            trade_pnl_breakdown_id: close.breakdown.id,
            trade_pnl_breakdown_path: format!("sqlite:trade_pnl_breakdowns/{}", close.breakdown.id),
            last_runtime_accounting_event_id: Some(close.accounting_event_id),
            last_runtime_pnl_ledger_id: Some(close.ledger.id),
            runtime_pnl_source: PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET.to_string(),
            runtime_equity_pnl_source: PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET.to_string(),
            runtime_realized_pnl_source: PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET.to_string(),
            runtime_net_pnl_claim_allowed: close.ledger.runtime_net_pnl_claim_allowed,
            runtime_equity_uses_net_pnl: true,
            runtime_equity_net_accounting_claim_allowed: true,
            runtime_equity_net_accounting_claim_block_reason: None,
            paper_fee_model_status: close.breakdown.paper_fee_model_status.clone(),
            cost_model_fingerprint: close.breakdown.cost_model_fingerprint.clone(),
        });
    }

    pub fn set_last_runtime_pnl_summary_from_defaults(
        &mut self,
        trade_pnl_breakdown_id: i64,
        trade_pnl_breakdown_path: impl Into<String>,
        cost_model: &risk::PaperCostModel,
    ) {
        self.set_last_runtime_pnl_summary(
            trade_pnl_breakdown_id,
            trade_pnl_breakdown_path,
            cost_model.status.clone(),
            cost_model.cost_model_fingerprint.clone(),
            cost_model.estimated_net_pnl_claim_allowed,
        );
    }

    pub fn config(&self) -> &PaperConfig {
        &self.config
    }

    pub fn errors(&self) -> &[String] {
        &self.errors
    }

    pub fn clear_errors(&mut self) {
        self.errors.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn minimal_config_json() -> &'static str {
        r#"{
            "slots": [
                {
                    "asset": "USD/JPY",
                    "strategy_name": "sma_cross",
                    "params": {}
                }
            ]
        }"#
    }

    #[test]
    fn paper_config_leverage_defaults_to_1() {
        let cfg: PaperConfig = serde_json::from_str(minimal_config_json()).unwrap();
        assert_eq!(cfg.leverage, 1.0);
    }

    #[test]
    fn paper_config_maintenance_margin_defaults_to_0_2() {
        let cfg: PaperConfig = serde_json::from_str(minimal_config_json()).unwrap();
        assert_eq!(cfg.maintenance_margin_pct, 0.2);
    }

    #[test]
    fn paper_config_leverage_deserializes_from_json() {
        let json = r#"{
            "slots": [{"asset": "USD/JPY", "strategy_name": "sma_cross", "params": {}}],
            "leverage": 500,
            "maintenance_margin_pct": 0.2
        }"#;
        let cfg: PaperConfig = serde_json::from_str(json).unwrap();
        assert_eq!(cfg.leverage, 500.0);
        assert_eq!(cfg.maintenance_margin_pct, 0.2);
    }

    #[test]
    fn slot_config_per_slot_leverage_override() {
        let json = r#"{
            "slots": [
                {
                    "asset": "USD/JPY",
                    "strategy_name": "sma_cross",
                    "params": {},
                    "leverage": 200
                },
                {
                    "asset": "USD/CHF",
                    "strategy_name": "sma_cross",
                    "params": {}
                }
            ],
            "leverage": 500
        }"#;
        let cfg: PaperConfig = serde_json::from_str(json).unwrap();
        // Slot 0 has per-slot override of 200
        assert_eq!(cfg.slots[0].leverage, Some(200.0));
        // Slot 1 has no override, uses global 500
        assert_eq!(cfg.slots[1].leverage, None);
        // effective_leverage for slot 0 should be 200
        assert_eq!(cfg.effective_leverage(0), 200.0);
        // effective_leverage for slot 1 should be global 500
        assert_eq!(cfg.effective_leverage(1), 500.0);
    }

    #[test]
    fn backward_compat_no_leverage_fields() {
        // Existing config without leverage fields should still load
        let cfg: PaperConfig = serde_json::from_str(minimal_config_json()).unwrap();
        assert_eq!(cfg.leverage, 1.0);
        assert_eq!(cfg.maintenance_margin_pct, 0.2);
        assert_eq!(cfg.slots[0].leverage, None);
    }
}
