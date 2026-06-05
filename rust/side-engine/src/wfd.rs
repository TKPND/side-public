use std::collections::HashMap;
use std::sync::Once;

use rand::prelude::*;
use rayon::prelude::*;
use serde::Deserialize;
use serde_json::Value;

use crate::backtest::{self, BacktestResult};
use crate::indicators;
use crate::strategies::{self, Ohlcv};

/// One-shot deprecation warning for Naive70_30 callers (once per process).
static NAIVE_70_30_WARN: Once = Once::new();

/// Internal walk representation supporting non-contiguous IS segments (purged k-fold).
/// Under Naive70_30, `is_segments` always has exactly one entry: `(0, is_end)`.
#[derive(Clone, Debug)]
struct WalkRanges {
    /// IS segments as `[start, end)` half-open ranges. May have 2 segments when
    /// the OOS fold sits in the middle of the series (purged k-fold).
    is_segments: Vec<(usize, usize)>,
    /// OOS range `[start, end)`.
    oos: (usize, usize),
    /// Fold/walk index for labelling WalkResult.
    fold_idx: usize,
}

fn deserialize_usize_from_float<'de, D>(deserializer: D) -> Result<usize, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let v: f64 = Deserialize::deserialize(deserializer)?;
    Ok(v as usize)
}

/// Deserialize null/None as NaN, number as f64.
fn deserialize_f64_or_nan<'de, D>(deserializer: D) -> Result<f64, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let v: Option<f64> = Deserialize::deserialize(deserializer)?;
    Ok(v.unwrap_or(f64::NAN))
}

/// Default fee in basis points per position change.
/// 1 bps = 0.0001 ratio. Calibrated for FX retail realistic round-trip cost (~2 bps RT).
/// Crypto backtests should override to ~10 bps. `#[serde(default)]` on the field makes
/// this apply when an older JSON config is deserialized without a `fee_bps` key.
fn default_fee_bps() -> f64 {
    1.0
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize, PartialEq)]
#[serde(tag = "type")]
pub enum CvMode {
    PurgedKFold { k: usize, embargo_days: usize },
    Naive70_30,
}

fn default_cv_mode() -> CvMode {
    CvMode::PurgedKFold {
        k: 5,
        embargo_days: 1,
    }
}

/// Per-strategy gate configuration for `aggregate_walks`.
///
/// Decouples gate constants (dsr_n_trials, t_stat_threshold, min_oos_win_rate)
/// from hardcoded values so low-frequency strategies like macro_event_drift can
/// bypass the oos_win_rate gate that would prune 3-trades/fold slots.
///
/// D-03: NOT a field of WfdConfig — passed as an independent argument to preserve
/// the serde contract on WfdConfig.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct GateConfig {
    /// Number of competing trials for Deflated Sharpe Ratio correction.
    /// tod_edge: 12_960 (1440 minutes × 9 horizons); macro_event: 96 (8 offset × 6 hold × 2 exit).
    pub dsr_n_trials: usize,
    /// Absolute t-stat threshold. Stored here for Plan 02 (validation.rs caller) to consume.
    /// aggregate_walks does not gate on t-stat directly — see validation.rs:252.
    pub t_stat_threshold: f64,
    /// Minimum OOS win-rate (fraction of walks with OOS PF > 1.0).
    /// Set to 0.0 to bypass the win-rate gate for low-frequency strategies.
    pub min_oos_win_rate: f64,
}

impl Default for GateConfig {
    /// tod_edge / default preset — preserves existing hardcoded gate constants.
    /// min_oos_win_rate matches WfdConfig::default().min_oos_win_rate (= 0.0).
    fn default() -> Self {
        Self {
            dsr_n_trials: 12_960,
            t_stat_threshold: 4.40,
            min_oos_win_rate: 0.0,
        }
    }
}

impl GateConfig {
    /// macro_event_drift preset — low-frequency slot preset.
    /// `dsr_n_trials` tracks the macro_event sweep dimension
    /// (`WINDOW_OFFSETS × HOLD_BARS_VALUES × EXIT_TYPES`); Phase 76 D-02
    /// bumps it from 96 to 192 in lockstep with WINDOW_OFFSETS 8→16
    /// (Phase 74 D-08 lockstep rule). Relaxed t-stat + bypassed win-rate
    /// gate let 3-trades/fold slots survive pruning.
    pub fn macro_event() -> Self {
        Self {
            dsr_n_trials: 192,
            t_stat_threshold: 3.3,
            min_oos_win_rate: 0.0,
        }
    }
}

/// WFD configuration — mirrors Python WfdConfig.
#[derive(serde::Serialize, Deserialize)]
pub struct WfdConfig {
    #[serde(deserialize_with = "deserialize_usize_from_float")]
    pub is_months: usize,
    #[serde(deserialize_with = "deserialize_usize_from_float")]
    pub oos_months: usize,
    #[serde(deserialize_with = "deserialize_usize_from_float")]
    pub num_walks: usize,
    pub min_oos_pf: f64,
    #[serde(deserialize_with = "deserialize_usize_from_float")]
    pub min_annual_trades: usize,
    pub min_wfe: f64,
    pub min_oos_win_rate: f64,
    pub max_oos_drawdown: f64,
    /// Fee in basis points per position change (1 bps = 0.0001 ratio).
    /// Default 1.0 bps (~2 bps round-trip) matches FX retail realistic cost.
    #[serde(default = "default_fee_bps")]
    pub fee_bps: f64,
    /// Cross-validation mode. Wave 0 default is Naive70_30 for backward compat.
    /// Plan 02-02 will flip default to PurgedKFold{k:5, embargo_days:1}.
    #[serde(default = "default_cv_mode")]
    pub cv_mode: CvMode,
}

/// Exit configuration — mirrors Python ExitConfig.
#[derive(Debug, Clone, Copy, Deserialize)]
pub struct ExitConfig {
    #[serde(deserialize_with = "deserialize_f64_or_nan")]
    pub sl_pct: f64, // NaN if not set
    #[serde(deserialize_with = "deserialize_f64_or_nan")]
    pub tp_pct: f64,
    #[serde(deserialize_with = "deserialize_f64_or_nan")]
    pub sl_atr: f64,
    #[serde(deserialize_with = "deserialize_f64_or_nan")]
    pub tp_atr: f64,
    #[serde(deserialize_with = "deserialize_usize_from_float")]
    pub atr_period: usize,
}

/// Per-walk result.
#[derive(Debug, Default, serde::Serialize, serde::Deserialize)]
pub struct WalkResult {
    pub walk_id: usize,
    pub is_pf: f64,
    pub oos_pf: f64,
    pub oos_sharpe: f64,
    pub oos_max_dd: f64,
    pub oos_trades: usize,
    pub oos_gross_profit: f64,
    pub oos_gross_loss: f64,
    pub wfe: f64,
    pub is_equity_curve: Vec<f64>,
    pub oos_equity_curve: Vec<f64>,
    pub oos_start_bar: usize,
    pub oos_end_bar: usize,
    /// Politis-Romano 1994 stationary block bootstrap 95% CI lower bound
    /// for the per-bar OOS PnL mean. Defaults to 0.0 if not yet computed.
    /// Plan 02-05 Gate 6 reads the median fold's CI to verify excludes 0.
    #[serde(default)]
    pub pnl_ci_low: f64,
    /// Bootstrap 95% CI upper bound for per-bar OOS PnL mean.
    #[serde(default)]
    pub pnl_ci_high: f64,
}

/// Full WFD single-parameter result.
#[derive(Debug, Default, serde::Serialize, serde::Deserialize)]
pub struct WfdSingleResult {
    pub combined_oos_pf: f64,
    pub combined_oos_sharpe: f64,
    pub combined_oos_trades: usize,
    pub combined_oos_max_dd: f64,
    pub mean_wfe: f64,
    pub oos_win_rate: f64,
    pub passed: bool,
    pub walks: Vec<WalkResult>,
    /// Deflated Sharpe Ratio p-value (1 - DSR). Computed from combined OOS sharpe
    /// and trade count, deflated against `dsr_n_trials` competing strategies
    /// (Bailey & Lopez de Prado 2014, eq.9). Lower is better; Gate 2 of the
    /// 6-gate verdict requires `dsr_pvalue < 0.05`.
    #[serde(default)]
    pub dsr_pvalue: f64,
    /// Number of competing trials assumed for the DSR multiple-testing correction.
    /// Defaults to 12,960 (= 1440 minutes-of-day × 9 horizons).
    #[serde(default = "default_dsr_n_trials")]
    pub dsr_n_trials: usize,
}

fn default_dsr_n_trials() -> usize {
    crate::constants::DEFAULT_DSR_N_TRIALS
}

/// Timeframe → hours per bar.
fn tf_hours(timeframe: &str) -> f64 {
    match timeframe {
        "1m" => 1.0 / 60.0,
        "5m" => 5.0 / 60.0,
        "15m" => 0.25,
        "30m" => 0.5,
        "1h" => 1.0,
        "2h" => 2.0,
        "4h" => 4.0,
        "1d" => 24.0,
        _ => 1.0,
    }
}

fn annualized_trades(num_trades: usize, bars: usize, timeframe: &str) -> f64 {
    let hours_per_bar = tf_hours(timeframe);
    let total_hours = bars as f64 * hours_per_bar;
    if total_hours == 0.0 {
        return 0.0;
    }
    num_trades as f64 / total_hours * 8760.0
}

/// Split walks: anchored IS/OOS pairs.
fn split_walks(
    n_bars: usize,
    datetimes_ns: &[i64],
    config: &WfdConfig,
) -> Vec<(usize, usize, usize)> {
    // (is_start=0, is_end, oos_end)
    if n_bars == 0 || datetimes_ns.is_empty() {
        return vec![];
    }

    let start_ns = datetimes_ns[0];
    let end_ns = datetimes_ns[n_bars - 1];
    let total_duration_days = (end_ns - start_ns) as f64 / 86_400_000_000_000.0;
    let bars_per_month = n_bars as f64 / (total_duration_days / 30.44);

    let mut walks = Vec::new();
    for walk_i in 0..config.num_walks {
        let is_end_months = config.is_months + walk_i * config.oos_months;
        let oos_end_months = is_end_months + config.oos_months;

        let is_end_bar = (is_end_months as f64 * bars_per_month) as usize;
        let oos_end_bar = (oos_end_months as f64 * bars_per_month) as usize;

        if oos_end_bar > n_bars {
            break;
        }

        walks.push((is_end_bar, oos_end_bar, walk_i));
    }
    walks
}

/// Run backtest with pre-generated signals.
fn run_bt_with_signals(
    ohlcv_slice: &Ohlcv,
    signals: &[i8],
    timeframe: &str,
    exit_config: Option<&ExitConfig>,
    mode: i8,
    fee_bps: f64,
) -> BacktestResult {
    let ppy = backtest::periods_per_year(timeframe);
    let ts = ohlcv_slice.datetimes_ns.unwrap_or(&[]);
    // Single unit conversion site: bps → ratio (1 bps = 0.0001).
    let fee_ratio = fee_bps / 10_000.0;
    match exit_config {
        Some(ec) => {
            let atr = indicators::atr(
                ohlcv_slice.high,
                ohlcv_slice.low,
                ohlcv_slice.close,
                ec.atr_period,
            );
            let mut shifted = vec![0i8; signals.len()];
            if signals.len() > 1 {
                shifted[1..].copy_from_slice(&signals[..signals.len() - 1]);
            }
            backtest::run_backtest_sltp(
                ohlcv_slice.close,
                ohlcv_slice.high,
                ohlcv_slice.low,
                &atr,
                &shifted,
                fee_ratio,
                ppy,
                mode,
                ec.sl_atr,
                ec.tp_atr,
                ec.sl_pct,
                ec.tp_pct,
                ts,
            )
        }
        None => backtest::run_backtest(ohlcv_slice.close, signals, fee_ratio, ppy, mode, ts),
    }
}

/// Run a single backtest (IS or OOS) with the given slice of data.
#[allow(clippy::too_many_arguments)]
fn run_single_bt(
    ohlcv_slice: &Ohlcv,
    strategy_name: &str,
    params: &HashMap<String, Value>,
    timeframe: &str,
    exit_config: Option<&ExitConfig>,
    mode: i8,
    fee_bps: f64,
) -> BacktestResult {
    let signals = strategies::generate_signals(strategy_name, ohlcv_slice, params);
    run_bt_with_signals(ohlcv_slice, &signals, timeframe, exit_config, mode, fee_bps)
}

/// Wrap the Naive70_30 `split_walks` output as `WalkRanges` (single contiguous IS segment).
fn split_walks_as_ranges(
    n_bars: usize,
    datetimes_ns: &[i64],
    config: &WfdConfig,
) -> Vec<WalkRanges> {
    split_walks(n_bars, datetimes_ns, config)
        .into_iter()
        .map(|(is_end, oos_end, walk_id)| WalkRanges {
            is_segments: vec![(0, is_end)],
            oos: (is_end, oos_end),
            fold_idx: walk_id,
        })
        .collect()
}

/// Process a single walk using `WalkRanges` (supports purged k-fold multi-segment IS).
///
/// IS PnL is computed by concatenating signals from all IS segments and running
/// a single backtest on the combined data. OOS is always a single contiguous segment.
#[allow(clippy::too_many_arguments)]
fn process_walk_ranges(
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
    datetimes_ns: &[i64],
    aux_close: Option<&[f64]>,
    walk: &WalkRanges,
    strategy_name: &str,
    params: &HashMap<String, Value>,
    timeframe: &str,
    exit_config: Option<&ExitConfig>,
    mode: i8,
    fee_bps: f64,
) -> WalkResult {
    // --- IS: concatenate segments ---
    let is_open: Vec<f64> = walk
        .is_segments
        .iter()
        .flat_map(|&(s, e)| open[s..e].iter().copied())
        .collect();
    let is_high: Vec<f64> = walk
        .is_segments
        .iter()
        .flat_map(|&(s, e)| high[s..e].iter().copied())
        .collect();
    let is_low: Vec<f64> = walk
        .is_segments
        .iter()
        .flat_map(|&(s, e)| low[s..e].iter().copied())
        .collect();
    let is_close: Vec<f64> = walk
        .is_segments
        .iter()
        .flat_map(|&(s, e)| close[s..e].iter().copied())
        .collect();
    let is_volume: Vec<f64> = walk
        .is_segments
        .iter()
        .flat_map(|&(s, e)| volume[s..e].iter().copied())
        .collect();
    let is_dt: Vec<i64> = walk
        .is_segments
        .iter()
        .flat_map(|&(s, e)| datetimes_ns[s..e].iter().copied())
        .collect();
    let is_aux: Option<Vec<f64>> = aux_close.map(|a| {
        walk.is_segments
            .iter()
            .flat_map(|&(s, e)| a[s..e].iter().copied())
            .collect()
    });

    let is_ohlcv = Ohlcv {
        open: &is_open,
        high: &is_high,
        low: &is_low,
        close: &is_close,
        volume: &is_volume,
        datetimes_ns: Some(&is_dt),
        aux_close: is_aux.as_deref(),
    };
    let is_result = run_single_bt(
        &is_ohlcv,
        strategy_name,
        params,
        timeframe,
        exit_config,
        mode,
        fee_bps,
    );

    // --- OOS: single contiguous segment ---
    let (oos_start, oos_end) = walk.oos;
    let oos_ohlcv = Ohlcv {
        open: &open[oos_start..oos_end],
        high: &high[oos_start..oos_end],
        low: &low[oos_start..oos_end],
        close: &close[oos_start..oos_end],
        volume: &volume[oos_start..oos_end],
        datetimes_ns: Some(&datetimes_ns[oos_start..oos_end]),
        aux_close: aux_close.map(|a| &a[oos_start..oos_end]),
    };
    let oos_result = run_single_bt(
        &oos_ohlcv,
        strategy_name,
        params,
        timeframe,
        exit_config,
        mode,
        fee_bps,
    );

    let is_pf = is_result.profit_factor;
    let wfe = if is_pf > 0.0 && is_pf != f64::INFINITY {
        oos_result.profit_factor / is_pf
    } else {
        0.0
    };

    // VAL-05: per-walk Politis-Romano stationary block bootstrap CI on the
    // OOS per-bar PnL series (equity-curve diffs). Default n=1000, seed=42.
    let oos_pnl_per_bar: Vec<f64> = oos_result
        .equity_curve
        .windows(2)
        .map(|w| w[1] - w[0])
        .collect();
    let (pnl_ci_low, pnl_ci_high) = crate::validation::stationary_bootstrap_ci(
        &oos_pnl_per_bar,
        crate::constants::DEFAULT_BOOTSTRAP_N,
        crate::constants::DEFAULT_BOOTSTRAP_SEED,
    );

    WalkResult {
        walk_id: walk.fold_idx,
        is_pf,
        oos_pf: oos_result.profit_factor,
        oos_sharpe: oos_result.sharpe_ratio,
        oos_max_dd: oos_result.max_drawdown,
        oos_trades: oos_result.num_trades,
        oos_gross_profit: oos_result.gross_profit,
        oos_gross_loss: oos_result.gross_loss,
        wfe,
        is_equity_curve: is_result.equity_curve,
        oos_equity_curve: oos_result.equity_curve,
        oos_start_bar: oos_start,
        oos_end_bar: oos_end,
        pnl_ci_low,
        pnl_ci_high,
    }
}

/// Aggregate walk results into a WfdSingleResult.
fn aggregate_walks(
    walk_results: Vec<WalkResult>,
    walk_ranges: &[WalkRanges],
    config: &WfdConfig,
    gate: &GateConfig,
    timeframe: &str,
) -> WfdSingleResult {
    if walk_results.is_empty() {
        return WfdSingleResult {
            dsr_n_trials: gate.dsr_n_trials,
            ..Default::default()
        };
    }

    let total_gp: f64 = walk_results.iter().map(|w| w.oos_gross_profit).sum();
    let total_gl: f64 = walk_results.iter().map(|w| w.oos_gross_loss).sum();
    // BUGFIX-02: trades=0 (total_gl=0 && total_gp=0) must fail the OOS PF gate.
    // Previously returned f64::INFINITY which bypassed `combined_oos_pf >= min_oos_pf`,
    // causing trades=0 slots to be marked passed=true. See ecb-pass-gate-bug.md.
    let combined_oos_pf = if total_gl > 0.0 {
        total_gp / total_gl
    } else if total_gp > 0.0 {
        f64::INFINITY // all-winners, no losing trades — legitimate
    } else {
        0.0 // no trades at all — fail the gate
    };
    let combined_oos_trades: usize = walk_results.iter().map(|w| w.oos_trades).sum();
    let combined_oos_sharpe: f64 =
        walk_results.iter().map(|w| w.oos_sharpe).sum::<f64>() / walk_results.len() as f64;
    let mean_wfe: f64 = walk_results.iter().map(|w| w.wfe).sum::<f64>() / walk_results.len() as f64;
    let oos_win_count = walk_results.iter().filter(|w| w.oos_pf > 1.0).count();
    let oos_win_rate = oos_win_count as f64 / walk_results.len() as f64;
    let combined_oos_max_dd = walk_results
        .iter()
        .map(|w| w.oos_max_dd)
        .fold(0.0f64, f64::min);

    // Annualized trades — sum OOS bars from WalkRanges
    let total_oos_bars: usize = walk_ranges.iter().map(|w| w.oos.1 - w.oos.0).sum();
    let ann_trades = annualized_trades(combined_oos_trades, total_oos_bars, timeframe);

    let passed = combined_oos_pf >= config.min_oos_pf
        && oos_win_rate >= gate.min_oos_win_rate
        && mean_wfe >= config.min_wfe
        && ann_trades >= config.min_annual_trades as f64
        && combined_oos_max_dd >= config.max_oos_drawdown;

    // VAL-04: deflated Sharpe Ratio p-value, computed against gate.dsr_n_trials
    // competing trials. Default (GateConfig::default()) = 12_960 (1440 min × 9
    // horizons). macro_event preset = 96 (8 offset × 6 hold × 2 exit).
    let dsr_n_trials = gate.dsr_n_trials;
    let n_obs = combined_oos_trades.max(2);
    let dsr = crate::validation::compute_dsr(combined_oos_sharpe, dsr_n_trials, 0.0, 3.0, n_obs);
    let dsr_pvalue = if dsr.is_finite() {
        (1.0 - dsr).clamp(0.0, 1.0)
    } else {
        f64::NAN
    };

    WfdSingleResult {
        combined_oos_pf,
        combined_oos_sharpe,
        combined_oos_trades,
        combined_oos_max_dd,
        mean_wfe,
        oos_win_rate,
        passed,
        walks: walk_results,
        dsr_pvalue,
        dsr_n_trials,
    }
}

/// Inner implementation with configurable parallelism for walks.
#[allow(clippy::too_many_arguments)]
fn run_wfd_single_inner(
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
    datetimes_ns: &[i64],
    aux_close: Option<&[f64]>,
    strategy_name: &str,
    params: &HashMap<String, Value>,
    config: &WfdConfig,
    timeframe: &str,
    exit_config: Option<&ExitConfig>,
    mode: i8,
    parallel_walks: bool,
    gate: &GateConfig,
) -> WfdSingleResult {
    let n = close.len();

    // Dispatch on CvMode to build WalkRanges
    let walk_ranges: Vec<WalkRanges> = match &config.cv_mode {
        CvMode::PurgedKFold { k, embargo_days } => {
            let bars_per_day = crate::validation::bars_per_day_from_datetimes_ns(datetimes_ns);
            let embargo_bars = embargo_days * bars_per_day;
            let splits = match crate::validation::purged_kfold_indices(n, *k, embargo_bars) {
                Ok(s) => s,
                Err(_) => {
                    return WfdSingleResult {
                        dsr_n_trials: GateConfig::default().dsr_n_trials,
                        ..Default::default()
                    }
                }
            };
            splits
                .into_iter()
                .map(|fs| {
                    let oos_lo = *fs.oos_indices.first().unwrap();
                    let oos_hi = fs.oos_indices.last().unwrap() + 1;
                    let purge_lo = oos_lo.saturating_sub(embargo_bars);
                    let purge_hi = (oos_hi + embargo_bars).min(n);
                    let mut segs: Vec<(usize, usize)> = Vec::new();
                    if purge_lo > 0 {
                        segs.push((0, purge_lo));
                    }
                    if purge_hi < n {
                        segs.push((purge_hi, n));
                    }
                    WalkRanges {
                        is_segments: segs,
                        oos: (oos_lo, oos_hi),
                        fold_idx: fs.fold_idx,
                    }
                })
                .collect()
        }
        CvMode::Naive70_30 => {
            NAIVE_70_30_WARN.call_once(|| {
                tracing::warn!(
                    "CvMode::Naive70_30 is deprecated; use PurgedKFold for \
                     information-leakage-free validation"
                );
            });
            split_walks_as_ranges(n, datetimes_ns, config)
        }
    };

    if walk_ranges.is_empty() {
        return WfdSingleResult {
            dsr_n_trials: GateConfig::default().dsr_n_trials,
            ..Default::default()
        };
    }

    let walk_results: Vec<WalkResult> = if parallel_walks {
        walk_ranges
            .par_iter()
            .map(|w| {
                process_walk_ranges(
                    open,
                    high,
                    low,
                    close,
                    volume,
                    datetimes_ns,
                    aux_close,
                    w,
                    strategy_name,
                    params,
                    timeframe,
                    exit_config,
                    mode,
                    config.fee_bps,
                )
            })
            .collect()
    } else {
        walk_ranges
            .iter()
            .map(|w| {
                process_walk_ranges(
                    open,
                    high,
                    low,
                    close,
                    volume,
                    datetimes_ns,
                    aux_close,
                    w,
                    strategy_name,
                    params,
                    timeframe,
                    exit_config,
                    mode,
                    config.fee_bps,
                )
            })
            .collect()
    };

    aggregate_walks(walk_results, &walk_ranges, config, gate, timeframe)
}

/// Full run_wfd_single in Rust (parallel walks for single trial).
#[allow(clippy::too_many_arguments)]
pub fn run_wfd_single(
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
    datetimes_ns: &[i64],
    aux_close: Option<&[f64]>,
    strategy_name: &str,
    params: &HashMap<String, Value>,
    config: &WfdConfig,
    timeframe: &str,
    exit_config: Option<&ExitConfig>,
    mode: i8,
) -> WfdSingleResult {
    run_wfd_single_with_gate(
        open,
        high,
        low,
        close,
        volume,
        datetimes_ns,
        aux_close,
        strategy_name,
        params,
        config,
        timeframe,
        exit_config,
        mode,
        &GateConfig::default(),
    )
}

/// run_wfd_single with explicit GateConfig — used by macro_event_drift and future
/// low-frequency paths that need non-default gate thresholds.
#[allow(clippy::too_many_arguments)]
pub fn run_wfd_single_with_gate(
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
    datetimes_ns: &[i64],
    aux_close: Option<&[f64]>,
    strategy_name: &str,
    params: &HashMap<String, Value>,
    config: &WfdConfig,
    timeframe: &str,
    exit_config: Option<&ExitConfig>,
    mode: i8,
    gate: &GateConfig,
) -> WfdSingleResult {
    run_wfd_single_inner(
        open,
        high,
        low,
        close,
        volume,
        datetimes_ns,
        aux_close,
        strategy_name,
        params,
        config,
        timeframe,
        exit_config,
        mode,
        true, // parallel walks for single trial
        gate,
    )
}

/// Batch WFD: parallelize at the trial level (sequential walks within each trial).
#[allow(clippy::too_many_arguments)]
pub fn run_wfd_batch(
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
    datetimes_ns: &[i64],
    aux_close: Option<&[f64]>,
    params_list: &[HashMap<String, Value>],
    strategy_name: &str,
    config: &WfdConfig,
    timeframe: &str,
    exit_config: &Option<ExitConfig>,
    mode: i8,
) -> Vec<WfdSingleResult> {
    params_list
        .par_iter()
        .map(|params| {
            run_wfd_single_inner(
                open,
                high,
                low,
                close,
                volume,
                datetimes_ns,
                aux_close,
                strategy_name,
                params,
                config,
                timeframe,
                exit_config.as_ref(),
                mode,
                false, // sequential walks to avoid nested parallelism
                &GateConfig::default(),
            )
        })
        .collect()
}

/// Batch single-backtest: run N param sets in parallel on full data.
/// Returns PF for each param set. Used by Monte Carlo perturbation.
#[allow(clippy::too_many_arguments)]
pub fn run_backtest_batch(
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
    datetimes_ns: &[i64],
    aux_close: Option<&[f64]>,
    params_list: &[HashMap<String, Value>],
    strategy_name: &str,
    timeframe: &str,
    exit_config: Option<&ExitConfig>,
    mode: i8,
    fee_bps: f64,
) -> Vec<f64> {
    let ohlcv = Ohlcv {
        open,
        high,
        low,
        close,
        volume,
        datetimes_ns: Some(datetimes_ns),
        aux_close,
    };
    params_list
        .par_iter()
        .map(|params| {
            let result = run_single_bt(
                &ohlcv,
                strategy_name,
                params,
                timeframe,
                exit_config,
                mode,
                fee_bps,
            );
            result.profit_factor
        })
        .collect()
}

/// Random entry benchmark: run strategy once, then N random-signal backtests.
/// Returns (strategy_pf, Vec<random_pfs>).
#[allow(clippy::too_many_arguments)]
pub fn run_random_benchmark(
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
    datetimes_ns: &[i64],
    aux_close: Option<&[f64]>,
    strategy_name: &str,
    params: &HashMap<String, Value>,
    n_random: usize,
    timeframe: &str,
    exit_config: Option<&ExitConfig>,
    mode: i8,
    seed: u64,
    fee_bps: f64,
) -> (f64, Vec<f64>) {
    let n = close.len();
    let ohlcv = Ohlcv {
        open,
        high,
        low,
        close,
        volume,
        datetimes_ns: Some(datetimes_ns),
        aux_close,
    };

    // Run strategy backtest
    let strat_result = run_single_bt(
        &ohlcv,
        strategy_name,
        params,
        timeframe,
        exit_config,
        mode,
        fee_bps,
    );
    let strategy_pf = strat_result.profit_factor;

    // Generate and run random backtests in parallel
    // Distribution: [-1, 0, 0, 0, 1] → 20% buy, 20% sell, 60% flat
    let choices: [i8; 5] = [-1, 0, 0, 0, 1];
    let random_pfs: Vec<f64> = (0..n_random)
        .into_par_iter()
        .map(|i| {
            let mut rng = StdRng::seed_from_u64(seed.wrapping_add(i as u64));
            let signals: Vec<i8> = (0..n).map(|_| choices[rng.random_range(0..5)]).collect();
            let r = run_bt_with_signals(&ohlcv, &signals, timeframe, exit_config, mode, fee_bps);
            r.profit_factor
        })
        .filter(|&pf| pf != f64::INFINITY)
        .collect();

    (strategy_pf, random_pfs)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a synthetic linearly-rising OHLCV for deterministic fee tests.
    #[allow(clippy::type_complexity)]
    fn synthetic_ohlcv() -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<i64>) {
        let n: usize = 100;
        let close: Vec<f64> = (0..n).map(|i| 100.0 + i as f64).collect();
        let open = close.clone();
        let high: Vec<f64> = close.iter().map(|c| c + 0.5).collect();
        let low: Vec<f64> = close.iter().map(|c| c - 0.5).collect();
        let volume = vec![1000.0; n];
        let datetimes_ns: Vec<i64> = (0..n as i64)
            .map(|i| 1_700_000_000_000_000_000 + i * 3_600_000_000_000)
            .collect();
        (open, high, low, close, volume, datetimes_ns)
    }

    #[test]
    fn wfd_config_default_fee_bps_is_one() {
        let config = WfdConfig::default();
        assert_eq!(config.fee_bps, 1.0, "default fee_bps should be 1.0 bps");
    }

    #[test]
    fn fee_bps_propagates_to_backtest_result() {
        let (open, high, low, close, volume, datetimes_ns) = synthetic_ohlcv();
        let n = close.len();

        // Enter long at bar 10, exit at bar 50 → 40-bar hold on a linearly-rising
        // close series, guaranteeing a profitable trade before fee deduction. This
        // produces exactly two position changes (fee_bps applied twice in round-trip).
        let mut signals = vec![0i8; n];
        signals[10] = 1;
        signals[50] = -1;

        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };

        // fee_bps=0.0 → zero fee deduction
        let result_no_fee = run_bt_with_signals(&ohlcv, &signals, "1h", None, 1, 0.0);
        // fee_bps=500.0 → 5% per position change, 10% round-trip
        let result_high_fee = run_bt_with_signals(&ohlcv, &signals, "1h", None, 1, 500.0);

        // Guard: the synthetic linear-uptrend long trade must actually execute and be
        // profitable. If this fires, the signal path isn't registering a trade and the
        // follow-up comparison would be meaningless (0.0 > 0.0 is false).
        assert!(
            result_no_fee.total_return > 0.0,
            "synthetic long trade should be profitable on rising prices (total_return={})",
            result_no_fee.total_return
        );

        assert!(
            result_no_fee.total_return > result_high_fee.total_return,
            "fee_bps=0.0 should yield higher return than fee_bps=500.0 (no_fee={}, high_fee={})",
            result_no_fee.total_return,
            result_high_fee.total_return
        );
    }

    // -----------------------------------------------------------------------
    // GateConfig unit tests (Task 1 — Plan 24-01)
    // -----------------------------------------------------------------------

    /// Build a minimal WalkResult for aggregate_walks tests.
    /// oos_pf > 1.0 means this walk "wins" for oos_win_rate calculation.
    fn mk_walk(
        oos_gross_profit: f64,
        oos_gross_loss: f64,
        oos_pf: f64,
        oos_trades: usize,
    ) -> WalkResult {
        WalkResult {
            walk_id: 0,
            is_pf: 2.0,
            oos_pf,
            oos_sharpe: 1.5,
            oos_max_dd: -0.05,
            oos_trades,
            oos_gross_profit,
            oos_gross_loss,
            wfe: 0.8,
            is_equity_curve: vec![],
            oos_equity_curve: vec![],
            oos_start_bar: 0,
            oos_end_bar: 1000,
            pnl_ci_low: 0.01,
            pnl_ci_high: 0.05,
        }
    }

    /// Build a permissive WfdConfig (all gates near zero so passing is easy).
    fn permissive_wfd_config() -> WfdConfig {
        WfdConfig {
            is_months: 6,
            oos_months: 3,
            num_walks: 5,
            min_oos_pf: 1.0,
            min_annual_trades: 0,
            min_wfe: 0.0,
            min_oos_win_rate: 0.0,
            max_oos_drawdown: -1.0,
            fee_bps: 1.0,
            cv_mode: CvMode::PurgedKFold {
                k: 5,
                embargo_days: 1,
            },
        }
    }

    /// Build dummy WalkRanges for use with aggregate_walks directly.
    fn mk_walk_ranges(n: usize) -> Vec<WalkRanges> {
        (0..n)
            .map(|i| WalkRanges {
                is_segments: vec![(0, 500)],
                oos: (500, 1000),
                fold_idx: i,
            })
            .collect()
    }

    #[test]
    fn gate_config_default_matches_legacy_tod_edge_constants() {
        let g = GateConfig::default();
        assert_eq!(g.dsr_n_trials, 12_960);
        assert!((g.t_stat_threshold - 4.40).abs() < 1e-9);
        // min_oos_win_rate must match WfdConfig::default().min_oos_win_rate (= 0.0).
        // Lock the exact value so any future change is detected.
        assert!((g.min_oos_win_rate - 0.0).abs() < 1e-9);
    }

    #[test]
    fn gate_config_macro_event_preset() {
        let g = GateConfig::macro_event();
        // Phase 76 D-02 lockstep: dsr_n_trials == WINDOW_OFFSETS.len() × HOLD_BARS_VALUES.len()
        // × EXIT_TYPES.len() (= 192 after Phase 76 Wave-1).
        let expected = crate::scanner::macro_event::WINDOW_OFFSETS.len()
            * crate::scanner::macro_event::HOLD_BARS_VALUES.len()
            * crate::scanner::macro_event::EXIT_TYPES.len();
        assert_eq!(g.dsr_n_trials, expected);
        assert!((g.t_stat_threshold - 3.3).abs() < 1e-9);
        assert_eq!(g.min_oos_win_rate, 0.0);
    }

    #[test]
    fn gate_config_serde_roundtrip() {
        let g = GateConfig::macro_event();
        let json = serde_json::to_string(&g).unwrap();
        let back: GateConfig = serde_json::from_str(&json).unwrap();
        let expected = crate::scanner::macro_event::WINDOW_OFFSETS.len()
            * crate::scanner::macro_event::HOLD_BARS_VALUES.len()
            * crate::scanner::macro_event::EXIT_TYPES.len();
        assert_eq!(back.dsr_n_trials, expected);
        assert!((back.t_stat_threshold - 3.3).abs() < 1e-9);
        assert_eq!(back.min_oos_win_rate, 0.0);
    }

    // -----------------------------------------------------------------------
    // Tod-edge WFD regression tests (Task 2 — Plan 24-01)
    // -----------------------------------------------------------------------

    /// Test 1 (passing case): strong walks with default GateConfig must pass.
    /// Locks: refactoring to GateConfig does not flip a previously-passing verdict.
    #[test]
    fn tod_edge_regression_passing_case_still_passes_with_default_gate() {
        // 5 walks: each profitable (oos_pf > 1.0), combined PF comfortably > 1.0
        let walks: Vec<WalkResult> = (0..5).map(|_| mk_walk(200.0, 100.0, 2.0, 10)).collect();
        let ranges = mk_walk_ranges(5);
        let cfg = permissive_wfd_config();

        let result = aggregate_walks(walks, &ranges, &cfg, &GateConfig::default(), "1h");
        assert!(
            result.passed,
            "default GateConfig must preserve pre-refactor pass verdict for strong walks"
        );
    }

    /// Test 2 (win-rate gate): GateConfig.min_oos_win_rate controls the win-rate gate.
    /// With threshold 0.8 → 3/5 winning walks (0.6) fails; with 0.0 → passes.
    #[test]
    fn gate_config_min_oos_win_rate_controls_win_rate_gate() {
        // 3 winning walks (oos_pf=2.0) + 2 losing walks (oos_pf=0.5)
        // oos_win_rate = 3/5 = 0.60
        let mut walks: Vec<WalkResult> = (0..3).map(|_| mk_walk(200.0, 100.0, 2.0, 10)).collect();
        walks.extend((0..2).map(|_| mk_walk(50.0, 100.0, 0.5, 10)));
        let ranges = mk_walk_ranges(5);
        let cfg = permissive_wfd_config();

        // Strict win-rate gate (0.8) — 0.60 < 0.80 → must fail
        let strict_gate = GateConfig {
            min_oos_win_rate: 0.8,
            ..GateConfig::default()
        };
        let strict_result = aggregate_walks(walks.clone(), &ranges, &cfg, &strict_gate, "1h");
        assert!(
            !strict_result.passed,
            "min_oos_win_rate=0.8 must prune walks with oos_win_rate=0.60"
        );

        // Bypass win-rate gate (0.0) — must pass given profitable combined PF
        let bypass_gate = GateConfig {
            min_oos_win_rate: 0.0,
            ..GateConfig::default()
        };
        let bypass_result = aggregate_walks(walks, &ranges, &cfg, &bypass_gate, "1h");
        assert!(
            bypass_result.passed,
            "min_oos_win_rate=0.0 must bypass win-rate gate for macro_event use case"
        );
    }

    /// Test 3 (dsr_n_trials gate): GateConfig.dsr_n_trials affects dsr_pvalue output.
    /// Proves the field is read from GateConfig, not DEFAULT_DSR_N_TRIALS constant.
    #[test]
    fn gate_config_dsr_n_trials_controls_dsr_pvalue() {
        let walks: Vec<WalkResult> = (0..5).map(|_| mk_walk(200.0, 100.0, 2.0, 10)).collect();
        let ranges = mk_walk_ranges(5);
        let cfg = permissive_wfd_config();

        // Fewer trials (macro_event gate; Phase 76: 192) → less multiple-testing correction → lower dsr_pvalue
        let macro_gate = GateConfig::macro_event();
        let default_gate = GateConfig::default(); // dsr_n_trials = 12_960

        let result_macro = aggregate_walks(walks.clone(), &ranges, &cfg, &macro_gate, "1h");
        let result_default = aggregate_walks(walks, &ranges, &cfg, &default_gate, "1h");

        let expected_macro = crate::scanner::macro_event::WINDOW_OFFSETS.len()
            * crate::scanner::macro_event::HOLD_BARS_VALUES.len()
            * crate::scanner::macro_event::EXIT_TYPES.len();
        assert_eq!(
            result_macro.dsr_n_trials, expected_macro,
            "macro_event preset must set dsr_n_trials == WINDOW_OFFSETS × HOLD_BARS × EXIT_TYPES"
        );
        assert_eq!(
            result_default.dsr_n_trials, 12_960,
            "default preset must set dsr_n_trials=12_960"
        );
        // More trials → more Bonferroni penalty → higher dsr_pvalue (harder to pass DSR gate)
        assert!(
            result_default.dsr_pvalue >= result_macro.dsr_pvalue,
            "default (12_960 trials) must have higher dsr_pvalue than macro_event \
             (n={expected_macro}): default={}, macro={}",
            result_default.dsr_pvalue,
            result_macro.dsr_pvalue
        );
    }

    // -----------------------------------------------------------------------
    // BUGFIX-01 regression tests — ECB pass-gate min_oos_pf contract
    // -----------------------------------------------------------------------

    /// BUGFIX-01 regression: ECB gate `min_oos_pf` must be 2.0.
    ///
    /// D-11 [REVISED 2026-04-22]: `macro_event_wfd_config()` already returns
    /// `min_oos_pf: 2.0` at `macro_event.rs:253`. This test guards against
    /// regression that would lower the threshold (e.g. to 0.0 or 1.0) and
    /// re-introduce the ecb-pass-gate-bug documented in
    /// `.planning/debug/ecb-pass-gate-bug.md`.
    ///
    /// Any future change that alters `macro_event_wfd_config` must keep this test green.
    #[test]
    fn test_ecb_gate_min_oos_pf() {
        use crate::scanner::macro_event::macro_event_wfd_config;

        let wfd_cfg = macro_event_wfd_config();
        assert_eq!(
            wfd_cfg.min_oos_pf, 2.0,
            "ECB gate min_oos_pf must be 2.0 (BUGFIX-01); \
             lowering this threshold re-introduces ecb-pass-gate-bug"
        );

        // Contract smoke-check for GateConfig::macro_event()
        let gate = GateConfig::macro_event();
        assert_eq!(gate.dsr_n_trials, 192, "dsr_n_trials contract");
        assert_eq!(gate.min_oos_win_rate, 0.0, "min_oos_win_rate contract");
    }

    /// BUGFIX-02 regression: zero-trades slot must fail OOS PF gate.
    ///
    /// Previously `combined_oos_pf` returned `f64::INFINITY` when total_gl=0,
    /// causing `INFINITY >= min_oos_pf` to always pass. For trades=0 slots this
    /// incorrectly flagged `passed=true`. See .planning/debug/ecb-pass-gate-bug.md.
    #[test]
    fn test_zero_trades_fails_pass_gate() {
        use crate::scanner::macro_event::macro_event_wfd_config;

        // Fabricate a zero-trades walk (no profit, no loss, no trades)
        let walks = vec![WalkResult {
            walk_id: 0,
            is_pf: 0.0,
            oos_pf: 0.0,
            oos_sharpe: 0.0,
            oos_max_dd: 0.0,
            oos_trades: 0,
            oos_gross_profit: 0.0,
            oos_gross_loss: 0.0,
            wfe: 0.0,
            is_equity_curve: vec![],
            oos_equity_curve: vec![],
            oos_start_bar: 0,
            oos_end_bar: 100,
            pnl_ci_low: 0.0,
            pnl_ci_high: 0.0,
        }];
        let ranges = vec![WalkRanges {
            is_segments: vec![(0, 100)],
            oos: (100, 200),
            fold_idx: 0,
        }];
        let cfg = macro_event_wfd_config();
        let gate = GateConfig::macro_event();

        let out = aggregate_walks(walks, &ranges, &cfg, &gate, "1h");

        assert_eq!(out.combined_oos_trades, 0, "zero-trades input");
        assert_eq!(
            out.combined_oos_pf, 0.0,
            "BUGFIX-02: zero-trades PF must be 0.0 (not INFINITY) to fail min_oos_pf gate"
        );
        assert!(
            !out.passed,
            "BUGFIX-02: zero-trades slot must NOT pass the gate"
        );
    }
}
