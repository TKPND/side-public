use serde::{Deserialize, Serialize};
use serde_json::Value;
use side_engine::validation::Verdict;

use super::risk_gate::RiskGateSlotOutput;

/// One per-fee verdict produced by the per-fee WFD sweep (Phase 20 D-06).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeeVerdict {
    pub fee_bps_rt: f64,
    pub verdict: Verdict,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeeCurvePoint {
    pub fee_bps_rt: f64,
    pub pf: Option<f64>,
    pub mean_pip: Option<f64>,
    pub trades: usize,
}

/// One output record per (edge × hold_h_candidate) slot.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SlotOutput {
    pub name: String,
    pub params: Value,
    pub entry_minute: u16,
    pub direction: String,
    pub hold_h: u8,
    pub source_query: String,
    pub source_edge_index: usize,
    pub fee_curve: Vec<FeeCurvePoint>,
    pub pf_gross: Option<f64>,
    #[serde(rename = "pf_net@2bps_rt")]
    pub pf_net_2bps_rt: Option<f64>,
    pub alpha_cliff: Option<f64>,
    /// VAL-07: 6-gate composite verdict (Strict mode only).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verdict: Option<Verdict>,
    /// VAL-07: relaxed-mode pass flag (Relaxed mode only).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub relaxed_pass: Option<bool>,
    /// Phase 20 D-06: per-fee 6-gate verdicts for the fee sweep (Strict mode only).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verdicts_per_fee: Option<Vec<FeeVerdict>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub risk_gate: Option<RiskGateSlotOutput>,
}
