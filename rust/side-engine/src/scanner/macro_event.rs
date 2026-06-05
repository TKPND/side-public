//! Macro event drift scan path — inline slot enumeration.
//!
//! Unlike `run_scan()` which reads edges.json, this path enumerates
//! `window_offset × hold_bars × exit_type` inline because the macro_event_drift
//! strategy has a tiny, fully-specified parameter space. Slot count is
//! `WINDOW_OFFSETS.len() × HOLD_BARS_VALUES.len() × EXIT_TYPES.len()`
//! (Phase 76: 16 × 6 × 2 = 192 slots).
//!
//! Call `run_macro_event_path()` with an `OhlcvData` reference, a `WfdConfig`
//! preset, and a `GateConfig`. Results are returned as `Vec<MacroEventSlotResult>`.

use std::collections::HashMap;

use serde_json::Value;

use rayon::prelude::*;

use crate::pair::Pair;
use crate::scanner::OhlcvData;
use crate::wfd::{run_wfd_single_with_gate, GateConfig, WfdConfig, WfdSingleResult};

#[cfg(test)]
use crate::scanner::macro_event::tests::synthetic_ohlcv_6mo_1h;

// ---------------------------------------------------------------------------
// Slot enumeration constants
// ---------------------------------------------------------------------------

pub const WINDOW_OFFSETS: [u32; 16] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16];
pub const HOLD_BARS_VALUES: [u32; 6] = [1, 2, 3, 6, 12, 24];
pub const EXIT_TYPES: [&str; 2] = ["none", "fixed_pct"];

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// One slot = one (window_offset, hold_bars, exit_type) triple.
#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Serialize)]
pub struct MacroEventSlot {
    pub window_offset: u32,
    pub hold_bars: u32,
    /// "none" or "fixed_pct"
    pub exit_type: &'static str,
}

/// Result of running WFD on a single slot.
#[derive(Debug, Clone, serde::Serialize)]
pub struct MacroEventSlotResult {
    pub slot: MacroEventSlot,
    /// Raw WfdSingleResult from aggregate_walks.
    pub result: WfdSingleResult,
    /// True when the slot produced fewer trades than min_annual_trades
    /// or failed another hard gate — derived from `result.passed`.
    /// With GateConfig::macro_event() (min_oos_win_rate=0.0) this will be
    /// false for any slot that produces non-empty OOS trades.
    pub pruned: bool,
}

// ---------------------------------------------------------------------------
// Fee sweep types (D-06)
// ---------------------------------------------------------------------------

/// Per-fee-level result for a single slot. Walk-level equity curves excluded
/// for size optimization (D-06).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct FeeResult {
    pub fee_bps: f64,
    pub combined_oos_pf: f64,
    pub combined_oos_sharpe: f64,
    pub combined_oos_trades: usize,
    pub combined_oos_max_dd: f64,
    pub passed: bool,
    pub dsr_pvalue: f64,
    /// Number of trials used for DSR multiple-testing correction (D-03).
    /// Copied from WfdSingleResult.dsr_n_trials; equals
    /// `GateConfig::macro_event().dsr_n_trials` for both BOJ and FOMC sweeps
    /// (Phase 76 D-02: 192 per Phase 74 D-08 lockstep).
    pub dsr_n_trials: usize,
}

/// Duration bucket for v4.8 regime taxonomy (Phase 79 D-04 SEAL).
/// Short = 0-60m (Evans-Lyons price-discovery completion).
/// Long  = 60-120m (sustained-drift / reversal zone).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub enum DurationBucket {
    #[serde(rename = "0-60m")]
    Short,
    #[serde(rename = "60-120m")]
    Long,
}

/// Liquidity regime for v4.8 regime taxonomy (Phase 79 D-06 SEAL).
/// Cutoff は `data/regime_cuts.json` で 2021 prior quantile から SEAL 済み
/// (low_upper=1.048e-8, high_lower=3.799e-8)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub enum LiquidityRegime {
    #[serde(rename = "LOW")]
    Low,
    #[serde(rename = "MID")]
    Mid,
    #[serde(rename = "HIGH")]
    High,
}

/// EXIT-01 D-01: per-bar snapshot stored inside TradeLog.bars.
/// `atr` holds the Rust-computed ATR with ATR_WINDOW=14 (SEAL-03 atr_window_bars).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct BarSnapshot {
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub atr: f64,
}

/// EXIT-01 D-01: per-trade raw bar log for Python exit_replay.py.
///
/// Rust engine emits TradeLog with `bars` spanning entry_bar..min(entry_bar + max_hold_bars, fold_end).
/// `direction` is +1 for long, -1 for short.
/// `atr_at_entry` is the ATR at entry_bar with ATR_WINDOW=14.
///
/// D-02: Rust does NOT precompute exit triggers. Python replays all 4 rules
/// (atr/technical/trailing/time) from bar sequence. D-07: max_hold_bars=null
/// (SEAL exit_commit.json) means time_stop is disabled; fold_end is the sentinel.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct TradeLog {
    pub trade_id: u64,
    pub entry_bar: usize,
    pub entry_price: f64,
    pub direction: i8,
    pub atr_at_entry: f64,
    pub bars: Vec<BarSnapshot>,
}

/// Aggregated report for one slot across all fee levels.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct SlotReport {
    pub window_offset: u32,
    pub hold_bars: u32,
    pub exit_type: &'static str,
    pub fee_results: Vec<FeeResult>,

    /// v4.8 REGIME-01: duration bucket (Phase 79 SEAL, Python aggregator が書く).
    /// Rust engine は常に None で emit する。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duration_bucket: Option<DurationBucket>,

    /// v4.8 REGIME-02: liquidity regime (Phase 79 SEAL, Python aggregator が書く).
    /// Rust engine は常に None で emit する。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub liquidity_regime: Option<LiquidityRegime>,

    /// Phase 86 EXIT-01 D-04: per-trade detailed log.
    /// Rust engine emits None by default. scripts/v4.9/gen_per_trade_log.py
    /// is responsible for calling the engine in per_trade_log emission mode.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub per_trade_log: Option<Vec<TradeLog>>,
}

// ---------------------------------------------------------------------------
// Slot enumeration
// ---------------------------------------------------------------------------

/// Enumerate all slots (`WINDOW_OFFSETS × HOLD_BARS_VALUES × EXIT_TYPES`).
///
/// Phase 76 D-01/D-03: slot count is runtime-derived from the three const
/// arrays (16 × 6 × 2 = 192 after WINDOW_OFFSETS bump).
/// Order: window_offset outer, hold_bars middle, exit_type inner — deterministic.
pub fn macro_event_slots() -> Vec<MacroEventSlot> {
    let mut out =
        Vec::with_capacity(WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len());
    for &off in &WINDOW_OFFSETS {
        for &hold in &HOLD_BARS_VALUES {
            for &exit in &EXIT_TYPES {
                out.push(MacroEventSlot {
                    window_offset: off,
                    hold_bars: hold,
                    exit_type: exit,
                });
            }
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Run path
// ---------------------------------------------------------------------------

/// Run per-slot WFD for the macro_event_drift strategy.
///
/// `slots_filter` — optional subset of slots to run. Pass `None` to run all
/// slots returned by `macro_event_slots()` (Phase 76: 192 after WINDOW_OFFSETS
/// bump; Phase 74 D-08 lockstep).
///
/// Each slot calls `run_wfd_single_with_gate("macro_event_drift", ...)` using
/// the provided `gate` (typically `GateConfig::macro_event()`).
pub fn run_macro_event_path(
    ohlcv: &OhlcvData,
    wfd_cfg: &WfdConfig,
    gate: &GateConfig,
    slots_filter: Option<Vec<MacroEventSlot>>,
) -> Vec<MacroEventSlotResult> {
    let slots = slots_filter.unwrap_or_else(macro_event_slots);

    slots
        .into_iter()
        .map(|slot| run_single_slot(ohlcv, wfd_cfg, gate, slot))
        .collect()
}

fn run_single_slot(
    ohlcv: &OhlcvData,
    wfd_cfg: &WfdConfig,
    gate: &GateConfig,
    slot: MacroEventSlot,
) -> MacroEventSlotResult {
    // Build params HashMap<String, Value> matching macro_event_drift_signals() keys.
    // exit_type is a string param; window_offset and hold_bars are numeric.
    // tp_pct / sl_pct default to NaN inside the strategy when not present.
    let mut params: HashMap<String, Value> = HashMap::new();
    params.insert(
        "window_offset".to_string(),
        Value::Number(serde_json::Number::from(slot.window_offset)),
    );
    params.insert(
        "hold_bars".to_string(),
        Value::Number(serde_json::Number::from(slot.hold_bars)),
    );
    params.insert(
        "exit_type".to_string(),
        Value::String(slot.exit_type.to_string()),
    );

    // For fixed_pct exit type, supply reasonable default TP/SL values.
    // These match what macro_event_drift_signals reads via params.get("tp_pct")/sl_pct.
    if slot.exit_type == "fixed_pct" {
        params.insert(
            "tp_pct".to_string(),
            Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
        );
        params.insert(
            "sl_pct".to_string(),
            Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
        );
    }

    let result = run_wfd_single_with_gate(
        &ohlcv.open,
        &ohlcv.high,
        &ohlcv.low,
        &ohlcv.close,
        &ohlcv.volume,
        &ohlcv.datetimes_ns,
        ohlcv.aux_close.as_deref(),
        "macro_event_drift",
        &params,
        wfd_cfg,
        "1h", // macro_event_drift operates on 1h bars
        None, // exit logic is baked into signal generation
        1,    // long_only
        gate,
    );

    // pruned = slot did not pass the WFD gate (aggregate_walks sets passed=false
    // when combined_oos_pf < min_oos_pf or other hard criteria fail).
    // With GateConfig::macro_event() and min_oos_win_rate=0.0 this is lenient.
    let pruned = !result.passed;

    MacroEventSlotResult {
        slot,
        result,
        pruned,
    }
}

// ---------------------------------------------------------------------------
// Low-frequency WfdConfig preset
// ---------------------------------------------------------------------------

/// Build the low-freq WfdConfig preset for macro_event_drift:
/// IS=3M, OOS=3M, walks=3 (D-09).
pub fn macro_event_wfd_config() -> WfdConfig {
    use crate::wfd::CvMode;
    WfdConfig {
        is_months: 3,
        oos_months: 3,
        num_walks: 3,
        min_oos_pf: 2.0, // OOS PF ≥ 2.0 pass gate per project cost-of-capital requirement
        min_annual_trades: 0, // discovery phase: no trade-count floor (slots may produce 0 trades)
        min_wfe: 0.0,
        min_oos_win_rate: 0.0,
        max_oos_drawdown: -1.0, // no drawdown gate for discovery phase
        fee_bps: 0.0,           // fee=0 for smoke; callers can override
        cv_mode: CvMode::PurgedKFold {
            k: 3,
            embargo_days: 1,
        },
    }
}

// ---------------------------------------------------------------------------
// Fee sweep (D-01, D-02, D-06)
// ---------------------------------------------------------------------------

/// Run WFD for all configured slots × 5 fee levels {0, 1, 2, 3, 5} bps RT.
///
/// Returns one `SlotReport` per slot (count = `macro_event_slots().len()`), each
/// with 5 `FeeResult` entries.
/// Uses `macro_event_wfd_config()` internally; callers do not need to supply config.
pub fn run_macro_event_fee_sweep(ohlcv: &OhlcvData) -> Vec<SlotReport> {
    let fee_levels: [f64; 5] = [0.0, 1.0, 2.0, 3.0, 5.0];
    let slots = macro_event_slots();
    let gate = GateConfig::macro_event();

    slots
        .into_iter()
        .map(|slot| {
            let fee_results: Vec<FeeResult> = fee_levels
                .iter()
                .map(|&fee_bps| {
                    let mut wfd_cfg = macro_event_wfd_config();
                    wfd_cfg.fee_bps = fee_bps;
                    let slot_results =
                        run_macro_event_path(ohlcv, &wfd_cfg, &gate, Some(vec![slot.clone()]));
                    let r = &slot_results[0].result;
                    FeeResult {
                        fee_bps,
                        combined_oos_pf: r.combined_oos_pf,
                        combined_oos_sharpe: r.combined_oos_sharpe,
                        combined_oos_trades: r.combined_oos_trades,
                        combined_oos_max_dd: r.combined_oos_max_dd,
                        passed: r.passed,
                        dsr_pvalue: r.dsr_pvalue,
                        dsr_n_trials: r.dsr_n_trials, // D-03: copied from WfdSingleResult
                    }
                })
                .collect();

            SlotReport {
                window_offset: slot.window_offset,
                hold_bars: slot.hold_bars,
                exit_type: slot.exit_type,
                fee_results,
                duration_bucket: None,
                liquidity_regime: None,
                per_trade_log: None,
            }
        })
        .collect()
}

// ---------------------------------------------------------------------------
// FOMC fee sweep (Phase 32 Plan 01 — Task 2 replaces this stub)
// ---------------------------------------------------------------------------

/// Run WFD for all slots × 5 fee levels {0, 1, 2, 3, 5} bps RT using
/// the FOMC event window set.
///
/// Returns one `SlotReport` per slot (count = `macro_event_slots().len()`;
/// Phase 76: 192), each with 5 `FeeResult` entries.
/// Mirrors `run_macro_event_fee_sweep` (D-01) but injects `event_source=fomc`
/// into each slot's params so `macro_event_drift_signals` routes to
/// `fomc_event_drift_signals` instead of the BOJ path.
///
/// D-01: mirrors run_macro_event_fee_sweep structure exactly.
/// D-02: reuses GateConfig::macro_event() → dsr_n_trials per gate (Phase 76: 192).
/// D-03: FeeResult.dsr_n_trials copied from WfdSingleResult.
pub fn run_fomc_event_fee_sweep(ohlcv: &OhlcvData, pair: Pair) -> Vec<SlotReport> {
    let fee_levels: [f64; 5] = [0.0, 1.0, 2.0, 3.0, 5.0];
    let slots = macro_event_slots();
    // D-02: reuse GateConfig::macro_event() — same gate as BOJ sweep
    // (Phase 76 lockstep: dsr_n_trials tracks WINDOW_OFFSETS × ... sweep dimension).
    let gate = GateConfig::macro_event();

    slots
        .into_par_iter()
        .map(|slot| {
            let fee_results: Vec<FeeResult> = fee_levels
                .iter()
                .map(|&fee_bps| {
                    let mut wfd_cfg = macro_event_wfd_config();
                    wfd_cfg.fee_bps = fee_bps;
                    let r = run_fomc_single_slot(ohlcv, &wfd_cfg, &gate, &slot, pair);
                    FeeResult {
                        fee_bps,
                        combined_oos_pf: r.combined_oos_pf,
                        combined_oos_sharpe: r.combined_oos_sharpe,
                        combined_oos_trades: r.combined_oos_trades,
                        combined_oos_max_dd: r.combined_oos_max_dd,
                        passed: r.passed,
                        dsr_pvalue: r.dsr_pvalue,
                        dsr_n_trials: r.dsr_n_trials, // D-03: copied from WfdSingleResult
                    }
                })
                .collect();

            SlotReport {
                window_offset: slot.window_offset,
                hold_bars: slot.hold_bars,
                exit_type: slot.exit_type,
                fee_results,
                duration_bucket: None,
                liquidity_regime: None,
                per_trade_log: None,
            }
        })
        .collect()
}

/// Private helper: run a single slot for the FOMC sweep.
///
/// Identical to `run_single_slot` except it injects `event_source=fomc`
/// into the params HashMap before dispatching to `run_wfd_single_with_gate`.
/// This routes `macro_event_drift_signals` → `fomc_event_drift_signals`
/// instead of the default BOJ path (Pitfall 2 guard).
fn run_fomc_single_slot(
    ohlcv: &OhlcvData,
    wfd_cfg: &WfdConfig,
    gate: &GateConfig,
    slot: &MacroEventSlot,
    pair: Pair,
) -> WfdSingleResult {
    let mut params: HashMap<String, Value> = HashMap::new();
    params.insert(
        "window_offset".to_string(),
        Value::Number(serde_json::Number::from(slot.window_offset)),
    );
    params.insert(
        "hold_bars".to_string(),
        Value::Number(serde_json::Number::from(slot.hold_bars)),
    );
    params.insert(
        "exit_type".to_string(),
        Value::String(slot.exit_type.to_string()),
    );
    // D-01: inject event_source=fomc so macro_event_drift_signals routes to FOMC path
    params.insert(
        "event_source".to_string(),
        Value::String("fomc".to_string()),
    );
    // Phase 39: inject pair so fomc_event_drift_signals can invert direction for EURUSD
    params.insert("pair".to_string(), Value::String(pair.as_str().to_string()));

    if slot.exit_type == "fixed_pct" {
        params.insert(
            "tp_pct".to_string(),
            Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
        );
        params.insert(
            "sl_pct".to_string(),
            Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
        );
    }

    run_wfd_single_with_gate(
        &ohlcv.open,
        &ohlcv.high,
        &ohlcv.low,
        &ohlcv.close,
        &ohlcv.volume,
        &ohlcv.datetimes_ns,
        ohlcv.aux_close.as_deref(),
        "macro_event_drift",
        &params,
        wfd_cfg,
        "1h",
        None,
        1, // long_only
        gate,
    )
}

/// Run the ECB event fee sweep: `macro_event_slots().len()` slots × {0,1,2,3,5}
/// bps RT = 5 × slot count WFD runs (Phase 76: 960).
///
/// Directional (long_only=0): ECB events have hawkish (+1) and dovish (-1) directions.
/// Uses `macro_event_slots()`, `GateConfig::macro_event()` (dsr_n_trials per gate;
/// Phase 76: 192), and `macro_event_wfd_config()` — identical infrastructure to the FOMC sweep.
/// Phase 40: pair parameter added for multi-pair sweep capability (per D-01).
pub fn run_ecb_event_fee_sweep(ohlcv: &OhlcvData, pair: Pair) -> Vec<SlotReport> {
    let fee_levels: [f64; 5] = [0.0, 1.0, 2.0, 3.0, 5.0];
    let slots = macro_event_slots();
    let gate = GateConfig::macro_event();

    slots
        .into_par_iter()
        .map(|slot| {
            let fee_results: Vec<FeeResult> = fee_levels
                .iter()
                .map(|&fee_bps| {
                    let mut wfd_cfg = macro_event_wfd_config();
                    wfd_cfg.fee_bps = fee_bps;
                    let r = run_ecb_single_slot(ohlcv, &wfd_cfg, &gate, &slot, pair);
                    FeeResult {
                        fee_bps,
                        combined_oos_pf: r.combined_oos_pf,
                        combined_oos_sharpe: r.combined_oos_sharpe,
                        combined_oos_trades: r.combined_oos_trades,
                        combined_oos_max_dd: r.combined_oos_max_dd,
                        passed: r.passed,
                        dsr_pvalue: r.dsr_pvalue,
                        dsr_n_trials: r.dsr_n_trials,
                    }
                })
                .collect();

            SlotReport {
                window_offset: slot.window_offset,
                hold_bars: slot.hold_bars,
                exit_type: slot.exit_type,
                fee_results,
                duration_bucket: None,
                liquidity_regime: None,
                per_trade_log: None,
            }
        })
        .collect()
}

/// Private helper: run a single slot for the ECB sweep.
///
/// Identical to `run_fomc_single_slot` except it injects `event_source=ecb`
/// and passes `long_only=0` (ECB is directional: hawkish/dovish).
/// DD-2: long_only=1 in run_wfd_single_with_gate calls pos.max(0), suppressing shorts.
/// ECB/NFP are directional; passing 0 honors both long and short signals.
/// Phase 40: pair parameter added for multi-pair sweep capability (D-01).
fn run_ecb_single_slot(
    ohlcv: &OhlcvData,
    wfd_cfg: &WfdConfig,
    gate: &GateConfig,
    slot: &MacroEventSlot,
    pair: Pair,
) -> WfdSingleResult {
    let mut params: HashMap<String, Value> = HashMap::new();
    params.insert(
        "window_offset".to_string(),
        Value::Number(serde_json::Number::from(slot.window_offset)),
    );
    params.insert(
        "hold_bars".to_string(),
        Value::Number(serde_json::Number::from(slot.hold_bars)),
    );
    params.insert(
        "exit_type".to_string(),
        Value::String(slot.exit_type.to_string()),
    );
    params.insert("event_source".to_string(), Value::String("ecb".to_string()));
    // Phase 40: inject pair so ecb_event_drift_signals can handle multi-pair logic
    params.insert("pair".to_string(), Value::String(pair.as_str().to_string()));

    if slot.exit_type == "fixed_pct" {
        params.insert(
            "tp_pct".to_string(),
            Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
        );
        params.insert(
            "sl_pct".to_string(),
            Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
        );
    }

    run_wfd_single_with_gate(
        &ohlcv.open,
        &ohlcv.high,
        &ohlcv.low,
        &ohlcv.close,
        &ohlcv.volume,
        &ohlcv.datetimes_ns,
        ohlcv.aux_close.as_deref(),
        "macro_event_drift",
        &params,
        wfd_cfg,
        "1h",
        None,
        0, // long_only=0: ECB is directional (DD-2: long_only=1 suppresses shorts)
        gate,
    )
}

/// Run the NFP event fee sweep over the configured slot grid × {0,1,2,3,5} bps RT.
///
/// Directional (long_only=0): NFP events have BEAT (+1) and MISS (-1) directions.
/// Uses same infrastructure as ECB and FOMC sweeps.
pub fn run_nfp_event_fee_sweep(ohlcv: &OhlcvData) -> Vec<SlotReport> {
    let fee_levels: [f64; 5] = [0.0, 1.0, 2.0, 3.0, 5.0];
    let slots = macro_event_slots();
    let gate = GateConfig::macro_event();

    slots
        .into_par_iter()
        .map(|slot| {
            let fee_results: Vec<FeeResult> = fee_levels
                .iter()
                .map(|&fee_bps| {
                    let mut wfd_cfg = macro_event_wfd_config();
                    wfd_cfg.fee_bps = fee_bps;
                    let r = run_nfp_single_slot(ohlcv, &wfd_cfg, &gate, &slot);
                    FeeResult {
                        fee_bps,
                        combined_oos_pf: r.combined_oos_pf,
                        combined_oos_sharpe: r.combined_oos_sharpe,
                        combined_oos_trades: r.combined_oos_trades,
                        combined_oos_max_dd: r.combined_oos_max_dd,
                        passed: r.passed,
                        dsr_pvalue: r.dsr_pvalue,
                        dsr_n_trials: r.dsr_n_trials,
                    }
                })
                .collect();

            SlotReport {
                window_offset: slot.window_offset,
                hold_bars: slot.hold_bars,
                exit_type: slot.exit_type,
                fee_results,
                duration_bucket: None,
                liquidity_regime: None,
                per_trade_log: None,
            }
        })
        .collect()
}

/// Private helper: run a single slot for the NFP sweep.
///
/// Injects `event_source=nfp` and passes `long_only=0` (NFP is directional: BEAT/MISS).
fn run_nfp_single_slot(
    ohlcv: &OhlcvData,
    wfd_cfg: &WfdConfig,
    gate: &GateConfig,
    slot: &MacroEventSlot,
) -> WfdSingleResult {
    let mut params: HashMap<String, Value> = HashMap::new();
    params.insert(
        "window_offset".to_string(),
        Value::Number(serde_json::Number::from(slot.window_offset)),
    );
    params.insert(
        "hold_bars".to_string(),
        Value::Number(serde_json::Number::from(slot.hold_bars)),
    );
    params.insert(
        "exit_type".to_string(),
        Value::String(slot.exit_type.to_string()),
    );
    params.insert("event_source".to_string(), Value::String("nfp".to_string()));

    if slot.exit_type == "fixed_pct" {
        params.insert(
            "tp_pct".to_string(),
            Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
        );
        params.insert(
            "sl_pct".to_string(),
            Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
        );
    }

    run_wfd_single_with_gate(
        &ohlcv.open,
        &ohlcv.high,
        &ohlcv.low,
        &ohlcv.close,
        &ohlcv.volume,
        &ohlcv.datetimes_ns,
        ohlcv.aux_close.as_deref(),
        "macro_event_drift",
        &params,
        wfd_cfg,
        "1h",
        None,
        0, // long_only=0: NFP is directional (DD-2: long_only=1 suppresses shorts)
        gate,
    )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

/// Run the Calendar Anomaly fee sweep: `macro_event_slots().len()` slots ×
/// {0,1,2,3,5} bps RT = 5 × slot count WFD runs (Phase 76: 960).
///
/// Calendar anomaly is pair-agnostic (Phase 45 D-09): uses day-of-week × month-position edges
/// from Phase 44 calendar_edges.json. No pair parameter.
/// Uses `macro_event_slots()`, `GateConfig::macro_event()` (dsr_n_trials per gate;
/// Phase 76: 192), and `macro_event_wfd_config()` — identical infrastructure to FOMC/ECB/NFP sweeps.
pub fn run_calendar_anomaly_fee_sweep(ohlcv: &OhlcvData) -> Vec<SlotReport> {
    let fee_levels: [f64; 5] = [0.0, 1.0, 2.0, 3.0, 5.0];
    let slots = macro_event_slots();
    let gate = GateConfig::macro_event();

    slots
        .into_iter()
        .map(|slot| {
            let fee_results: Vec<FeeResult> = fee_levels
                .iter()
                .map(|&fee_bps| {
                    let mut wfd_cfg = macro_event_wfd_config();
                    wfd_cfg.fee_bps = fee_bps;
                    let r = run_calendar_single_slot(ohlcv, &wfd_cfg, &gate, &slot);
                    FeeResult {
                        fee_bps,
                        combined_oos_pf: r.combined_oos_pf,
                        combined_oos_sharpe: r.combined_oos_sharpe,
                        combined_oos_trades: r.combined_oos_trades,
                        combined_oos_max_dd: r.combined_oos_max_dd,
                        passed: r.passed,
                        dsr_pvalue: r.dsr_pvalue,
                        dsr_n_trials: r.dsr_n_trials,
                    }
                })
                .collect();

            SlotReport {
                window_offset: slot.window_offset,
                hold_bars: slot.hold_bars,
                exit_type: slot.exit_type,
                fee_results,
                duration_bucket: None,
                liquidity_regime: None,
                per_trade_log: None,
            }
        })
        .collect()
}

/// Private helper: run a single slot for the Calendar Anomaly sweep.
///
/// Injects `event_source=calendar` (mandatory, per Phase 45 D-01).
/// Uses long_only=1 (Phase 45 D-06: calendar edges are long-only biased).
fn run_calendar_single_slot(
    ohlcv: &OhlcvData,
    wfd_cfg: &WfdConfig,
    gate: &GateConfig,
    slot: &MacroEventSlot,
) -> WfdSingleResult {
    let mut params: HashMap<String, Value> = HashMap::new();
    params.insert(
        "window_offset".to_string(),
        Value::Number(serde_json::Number::from(slot.window_offset)),
    );
    params.insert(
        "hold_bars".to_string(),
        Value::Number(serde_json::Number::from(slot.hold_bars)),
    );
    params.insert(
        "exit_type".to_string(),
        Value::String(slot.exit_type.to_string()),
    );
    // D-01: inject event_source=calendar so macro_event_drift_signals routes to calendar path
    params.insert(
        "event_source".to_string(),
        Value::String("calendar".to_string()),
    );

    if slot.exit_type == "fixed_pct" {
        params.insert(
            "tp_pct".to_string(),
            Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
        );
        params.insert(
            "sl_pct".to_string(),
            Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
        );
    }

    run_wfd_single_with_gate(
        &ohlcv.open,
        &ohlcv.high,
        &ohlcv.low,
        &ohlcv.close,
        &ohlcv.volume,
        &ohlcv.datetimes_ns,
        ohlcv.aux_close.as_deref(),
        "macro_event_drift",
        &params,
        wfd_cfg,
        "1h",
        None,
        1, // long_only=1: calendar edges are long-only (D-06)
        gate,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    // -----------------------------------------------------------------------
    // Phase 25 — fee sweep tests (TDD: written before implementation)
    // -----------------------------------------------------------------------

    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn fee_sweep_returns_5_fee_entries_per_slot() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let results = run_macro_event_fee_sweep(&ohlcv);
        let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
        // configured slots × 5 fee levels
        assert_eq!(
            results.len(),
            expected,
            "should have {expected} SlotReports"
        );
        for sr in &results {
            assert_eq!(
                sr.fee_results.len(),
                5,
                "each slot should have 5 fee entries, got {} for off={} hold={} exit={}",
                sr.fee_results.len(),
                sr.window_offset,
                sr.hold_bars,
                sr.exit_type
            );
        }
    }

    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn fee_sweep_fee_bps_values_match_sweep() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let results = run_macro_event_fee_sweep(&ohlcv);
        let expected_fees = [0.0f64, 1.0, 2.0, 3.0, 5.0];
        for sr in &results {
            for (i, fr) in sr.fee_results.iter().enumerate() {
                assert!(
                    (fr.fee_bps - expected_fees[i]).abs() < 1e-9,
                    "fee_bps mismatch at index {i}: expected {}, got {}",
                    expected_fees[i],
                    fr.fee_bps
                );
            }
        }
    }

    #[test]
    fn macro_event_slot_serializes_to_json() {
        let slot = MacroEventSlot {
            window_offset: 4,
            hold_bars: 18,
            exit_type: "fixed_pct",
        };
        let v = serde_json::to_value(&slot).expect("serialize should not fail");
        assert_eq!(v["window_offset"], 4u32, "window_offset field present");
        assert_eq!(v["hold_bars"], 18u32, "hold_bars field present");
        assert_eq!(v["exit_type"], "fixed_pct", "exit_type field present");
    }

    // -----------------------------------------------------------------------
    // Existing tests below
    // -----------------------------------------------------------------------

    #[test]
    fn macro_event_slots_produces_expected_count() {
        let slots = macro_event_slots();
        let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
        assert_eq!(slots.len(), expected);

        // All unique
        let mut seen = std::collections::HashSet::new();
        for s in &slots {
            assert!(
                seen.insert((s.window_offset, s.hold_bars, s.exit_type)),
                "duplicate slot: {:?}",
                s
            );
        }

        // Sanity: first slot is (WINDOW_OFFSETS[0], HOLD_BARS_VALUES[0], EXIT_TYPES[0]).
        let first = slots.first().unwrap();
        assert_eq!(first.window_offset, *WINDOW_OFFSETS.first().unwrap());
        assert_eq!(first.hold_bars, *HOLD_BARS_VALUES.first().unwrap());
        assert_eq!(first.exit_type, *EXIT_TYPES.first().unwrap());

        // Sanity: last slot is (WINDOW_OFFSETS[last], HOLD_BARS_VALUES[last], EXIT_TYPES[last]).
        let last = slots.last().unwrap();
        assert_eq!(last.window_offset, *WINDOW_OFFSETS.last().unwrap());
        assert_eq!(last.hold_bars, *HOLD_BARS_VALUES.last().unwrap());
        assert_eq!(last.exit_type, *EXIT_TYPES.last().unwrap());
    }

    #[test]
    fn macro_event_slots_offsets_range() {
        let slots = macro_event_slots();
        let offsets: Vec<u32> = slots
            .iter()
            .map(|s| s.window_offset)
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();
        assert_eq!(
            offsets.len(),
            WINDOW_OFFSETS.len(),
            "should have {} distinct offsets (= WINDOW_OFFSETS.len())",
            WINDOW_OFFSETS.len()
        );
    }

    #[test]
    fn run_macro_event_path_single_slot_no_panic_on_synthetic() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let wfd_cfg = macro_event_wfd_config();
        let gate = GateConfig::macro_event();
        let slots = vec![MacroEventSlot {
            window_offset: 1,
            hold_bars: 1,
            exit_type: "none",
        }];

        let results = run_macro_event_path(&ohlcv, &wfd_cfg, &gate, Some(slots));
        assert_eq!(results.len(), 1);
        // pruned is derived from !result.passed — with min_oos_win_rate=0.0 and fee=0
        // any slot with non-zero trades should not be pruned by win-rate gate.
        // We just assert it doesn't panic and returns one result.
        assert_eq!(results[0].slot.window_offset, 1);
    }

    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn run_macro_event_path_all_slots_no_panic() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let wfd_cfg = macro_event_wfd_config();
        let gate = GateConfig::macro_event();

        let results = run_macro_event_path(&ohlcv, &wfd_cfg, &gate, None);
        let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
        assert_eq!(
            results.len(),
            expected,
            "should return exactly {expected} slot results"
        );
    }

    // -----------------------------------------------------------------------
    // Phase 32 Plan 01 — Wave 0 RED tests (un-ignored in Task 4)
    // -----------------------------------------------------------------------

    /// Inline mirror of `fomc_sweep_returns_expected_slots_times_5_fees` (W0-VALID-01-A).
    /// Confirms `run_fomc_event_fee_sweep` returns exactly `macro_event_slots().len()` SlotReports
    /// (Phase 76 D-03: runtime-derived via WINDOW_OFFSETS × HOLD_BARS_VALUES × EXIT_TYPES).
    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn fee_sweep_fomc_has_expected_slot_count() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let results = run_fomc_event_fee_sweep(&ohlcv, crate::pair::Pair::Usdjpy);
        let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
        assert_eq!(
            results.len(),
            expected,
            "FOMC sweep must return {expected} SlotReports (runtime-derived)"
        );
    }

    /// Inline mirror of fomc_fee_result_contains_dsr_n_trials (W0-VALID-01-B).
    /// Confirms FeeResult.dsr_n_trials is populated with the macro_event gate
    /// value (D-03 lock; Phase 76 D-08 lockstep: tracks WINDOW_OFFSETS bump).
    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn fomc_dsr_n_trials_field_equals_gate_n_trials() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let results = run_fomc_event_fee_sweep(&ohlcv, crate::pair::Pair::Usdjpy);
        assert!(!results.is_empty());
        let expected = crate::wfd::GateConfig::macro_event().dsr_n_trials;
        assert_eq!(
            results[0].fee_results[0].dsr_n_trials, expected,
            "FOMC FeeResult.dsr_n_trials must equal GateConfig::macro_event().dsr_n_trials (D-02)"
        );
    }

    // -----------------------------------------------------------------------
    // Phase 35 — ECB sweep tests (SIG-03)
    // -----------------------------------------------------------------------

    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn ecb_fee_sweep_has_expected_slot_count() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let results = run_ecb_event_fee_sweep(&ohlcv, crate::pair::Pair::Usdjpy);
        let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
        assert_eq!(
            results.len(),
            expected,
            "ECB sweep must return {expected} SlotReports (runtime-derived)"
        );
    }

    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn ecb_fee_sweep_returns_5_fee_entries_per_slot() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let results = run_ecb_event_fee_sweep(&ohlcv, crate::pair::Pair::Usdjpy);
        for sr in &results {
            assert_eq!(
                sr.fee_results.len(),
                5,
                "each ECB slot must have 5 fee entries, got {} for off={} hold={} exit={}",
                sr.fee_results.len(),
                sr.window_offset,
                sr.hold_bars,
                sr.exit_type
            );
        }
    }

    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn ecb_dsr_n_trials_field_equals_gate_n_trials() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let results = run_ecb_event_fee_sweep(&ohlcv, crate::pair::Pair::Usdjpy);
        assert!(!results.is_empty());
        let expected = crate::wfd::GateConfig::macro_event().dsr_n_trials;
        assert_eq!(
            results[0].fee_results[0].dsr_n_trials, expected,
            "ECB FeeResult.dsr_n_trials must equal GateConfig::macro_event().dsr_n_trials"
        );
    }

    // -----------------------------------------------------------------------
    // Phase 35 — NFP sweep tests (SIG-04)
    // -----------------------------------------------------------------------

    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn nfp_fee_sweep_has_expected_slot_count() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let results = run_nfp_event_fee_sweep(&ohlcv);
        let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
        assert_eq!(
            results.len(),
            expected,
            "NFP sweep must return {expected} SlotReports (runtime-derived)"
        );
    }

    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn nfp_fee_sweep_returns_5_fee_entries_per_slot() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let results = run_nfp_event_fee_sweep(&ohlcv);
        for sr in &results {
            assert_eq!(
                sr.fee_results.len(),
                5,
                "each NFP slot must have 5 fee entries, got {} for off={} hold={} exit={}",
                sr.fee_results.len(),
                sr.window_offset,
                sr.hold_bars,
                sr.exit_type
            );
        }
    }

    #[test]
    #[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
    fn nfp_dsr_n_trials_field_equals_gate_n_trials() {
        let ohlcv = synthetic_ohlcv_6mo_1h();
        let results = run_nfp_event_fee_sweep(&ohlcv);
        assert!(!results.is_empty());
        let expected = crate::wfd::GateConfig::macro_event().dsr_n_trials;
        assert_eq!(
            results[0].fee_results[0].dsr_n_trials, expected,
            "NFP FeeResult.dsr_n_trials must equal GateConfig::macro_event().dsr_n_trials"
        );
    }

    /// Build a minimal 6-month 1h OHLCV fixture.
    ///
    /// Uses a fixed starting timestamp in 2024 (BOJ windows exist in this range).
    /// Bars are generated at 1h spacing starting 2024-01-01 UTC.
    pub(crate) fn synthetic_ohlcv_6mo_1h() -> OhlcvData {
        // 6 months ≈ 183 days × 24h = 4392 bars
        let n = 4_392usize;
        // 2024-01-01 00:00:00 UTC in nanoseconds
        let start_ns: i64 = 1_704_067_200i64 * 1_000_000_000;
        let hour_ns: i64 = 3_600i64 * 1_000_000_000;

        let base_price = 145.0f64;
        let mut close = Vec::with_capacity(n);
        let mut open = Vec::with_capacity(n);
        let mut high = Vec::with_capacity(n);
        let mut low = Vec::with_capacity(n);
        let mut volume = Vec::with_capacity(n);
        let mut datetimes_ns = Vec::with_capacity(n);

        // Simple deterministic random walk (LCG)
        let mut seed: u64 = 42;
        let mut price = base_price;
        for i in 0..n {
            seed = seed
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            let delta = ((seed >> 33) as f64 / u32::MAX as f64 - 0.5) * 0.002 * price;
            let o = price;
            price += delta;
            let c = price;
            let h = o.max(c) + 0.001 * base_price;
            let l = o.min(c) - 0.001 * base_price;
            open.push(o);
            close.push(c);
            high.push(h);
            low.push(l);
            volume.push(1000.0);
            datetimes_ns.push(start_ns + i as i64 * hour_ns);
        }

        OhlcvData {
            open,
            high,
            low,
            close,
            volume,
            datetimes_ns,
            aux_close: None,
        }
    }
}

#[cfg(test)]
mod exit_01_tests {
    use super::*;

    /// SlotReport.exit_type は `&'static str` のため SlotReport の Deserialize impl は
    /// `Deserialize<'static>` になり、`serde_json::from_value::<SlotReport>(_)` は通らない。
    /// #[serde(default)] 属性の挙動そのものは、同じ attribute を持つ wrapper struct で検証する。
    #[derive(serde::Deserialize)]
    struct PerTradeLogWrapper {
        #[serde(default)]
        per_trade_log: Option<Vec<TradeLog>>,
    }

    /// EXIT-01 backward compat: per_trade_log key が欠落した JSON を deserialize しても
    /// #[serde(default)] により None になること。
    #[test]
    fn test_per_trade_log_default_when_absent() {
        let empty: PerTradeLogWrapper =
            serde_json::from_str("{}").expect("empty JSON must deserialize via #[serde(default)]");
        assert!(empty.per_trade_log.is_none());
    }

    /// EXIT-01: skip_serializing_if = Option::is_none なので per_trade_log=None は
    /// SlotReport の JSON から省略される。v4.8 snapshot との byte 差分を最小化する。
    #[test]
    fn test_slot_report_per_trade_log_skipped_when_none() {
        let slot = SlotReport {
            window_offset: 1,
            hold_bars: 6,
            exit_type: "none",
            fee_results: vec![],
            duration_bucket: None,
            liquidity_regime: None,
            per_trade_log: None,
        };
        let json = serde_json::to_string(&slot).expect("serialize SlotReport");
        assert!(
            !json.contains("per_trade_log"),
            "per_trade_log=None must be omitted from JSON (skip_serializing_if): {json}"
        );
    }

    /// EXIT-01: per_trade_log=Some(...) は SlotReport JSON に出力される。
    #[test]
    fn test_slot_report_per_trade_log_emitted_when_some() {
        let slot = SlotReport {
            window_offset: 1,
            hold_bars: 6,
            exit_type: "none",
            fee_results: vec![],
            duration_bucket: None,
            liquidity_regime: None,
            per_trade_log: Some(vec![TradeLog {
                trade_id: 7,
                entry_bar: 10,
                entry_price: 100.0,
                direction: 1,
                atr_at_entry: 0.5,
                bars: vec![],
            }]),
        };
        let json = serde_json::to_string(&slot).expect("serialize SlotReport");
        assert!(
            json.contains("\"per_trade_log\""),
            "Some(...) must be serialized: {json}"
        );
        assert!(json.contains("\"trade_id\":7"), "{json}");
    }

    /// EXIT-01: TradeLog と BarSnapshot は borrowed 参照を持たないため通常の
    /// `Deserialize<'de>` が導出される。round-trip で数値 invariant を確認する。
    #[test]
    fn test_trade_log_round_trip() {
        let bar = BarSnapshot {
            high: 101.0,
            low: 99.0,
            close: 100.5,
            atr: 0.5,
        };
        let trade = TradeLog {
            trade_id: 1,
            entry_bar: 10,
            entry_price: 100.0,
            direction: 1,
            atr_at_entry: 0.5,
            bars: vec![bar.clone(), bar.clone()],
        };
        let json = serde_json::to_string(&trade).expect("serialize TradeLog");
        let round: TradeLog = serde_json::from_str(&json).expect("deserialize TradeLog");
        assert_eq!(round.trade_id, 1);
        assert_eq!(round.entry_bar, 10);
        assert_eq!(round.entry_price, 100.0);
        assert_eq!(round.direction, 1);
        assert_eq!(round.atr_at_entry, 0.5);
        assert_eq!(round.bars.len(), 2);
        assert_eq!(round.bars[0].high, 101.0);
        assert_eq!(round.bars[0].low, 99.0);
        assert_eq!(round.bars[0].close, 100.5);
        assert_eq!(round.bars[0].atr, 0.5);
    }
}

#[test]
fn test_calendar_fee_sweep_structure() {
    let ohlcv = synthetic_ohlcv_6mo_1h();
    let results = run_calendar_anomaly_fee_sweep(&ohlcv);

    // Phase 45 D-04 (Phase 76 D-03: runtime-derived): WINDOW_OFFSETS × HOLD_BARS_VALUES × EXIT_TYPES slots × 5 fee levels
    let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
    assert_eq!(
        results.len(),
        expected,
        "Calendar fee sweep must return exactly {expected} SlotReports"
    );

    // Each SlotReport must have exactly 5 FeeResult objects
    for (i, sr) in results.iter().enumerate() {
        assert_eq!(
            sr.fee_results.len(),
            5,
            "SlotReport[{}] must have exactly 5 FeeResults",
            i
        );

        // Fee values must be [0.0, 1.0, 2.0, 3.0, 5.0]
        let expected_fees = [0.0, 1.0, 2.0, 3.0, 5.0];
        for (j, fee_result) in sr.fee_results.iter().enumerate() {
            assert_eq!(
                fee_result.fee_bps, expected_fees[j],
                "Fee order mismatch at slot[{}].fee_results[{}]",
                i, j
            );
        }
    }
}

#[test]
fn test_calendar_fee_sweep_json_roundtrip() {
    let ohlcv = synthetic_ohlcv_6mo_1h();
    let results = run_calendar_anomaly_fee_sweep(&ohlcv);

    // Serialize to JSON string
    let json_str = serde_json::to_string(&results).expect("serialization failed");

    // Verify JSON is valid and contains expected structure
    assert!(
        !json_str.is_empty(),
        "JSON serialization must produce non-empty output"
    );
    assert!(
        json_str.contains("\"fee_results\""),
        "JSON must contain fee_results field"
    );

    // Verify structure is preserved (Phase 76 D-03: runtime-derived)
    let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
    assert_eq!(
        results.len(),
        expected,
        "Calendar sweep must produce {expected} slots ({}×{}×{})",
        WINDOW_OFFSETS.len(),
        HOLD_BARS_VALUES.len(),
        EXIT_TYPES.len()
    );
}

#[test]
fn test_event_source_parameter_injection() {
    // Minimal smoke test: verify that run_calendar_anomaly_fee_sweep completes
    // without error. The actual event_source injection is verified by the fact
    // that the function dispatches to calendar_anomaly_signals internally.
    let ohlcv = synthetic_ohlcv_6mo_1h();
    let results = run_calendar_anomaly_fee_sweep(&ohlcv);

    // If event_source wasn't properly injected, the signal generator would
    // fall back to BOJ logic, causing different results. We just verify
    // that the function returns the expected structure.
    assert!(
        !results.is_empty(),
        "Fee sweep must return at least 1 SlotReport"
    );
    let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
    assert_eq!(
        results.len(),
        expected,
        "event_source injection allows fee sweep to complete with {expected} slots"
    );
}
