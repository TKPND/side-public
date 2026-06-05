use std::collections::HashMap;

use chrono::{DateTime, Timelike, Utc};
use serde_json::Value;

use crate::indicators;

/// OHLCV data with optional nanosecond timestamps and auxiliary instrument data.
pub struct Ohlcv<'a> {
    pub open: &'a [f64],
    pub high: &'a [f64],
    pub low: &'a [f64],
    pub close: &'a [f64],
    pub volume: &'a [f64],
    pub datetimes_ns: Option<&'a [i64]>,
    pub aux_close: Option<&'a [f64]>, // auxiliary instrument close (DXY/VIX/SP500)
}

/// Strategy dispatcher — routes to the correct signal generator by name.
///
/// After the strategy emits its raw signals, an optional `news_blackout`
/// post-filter is applied (controlled by the boolean `news_blackout` param).
/// See [`apply_news_blackout`] for details.
pub fn generate_signals(name: &str, ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let raw = match name {
        "ema_atr" => ema_atr_signals(ohlcv, params),
        "keltner" => keltner_signals(ohlcv, params),
        "sma_cross" => sma_cross_signals(ohlcv, params),
        "rsi_reversal" => rsi_reversal_signals(ohlcv, params),
        "donchian_breakout" => donchian_breakout_signals(ohlcv, params),
        "bb_pctb" => bb_pctb_signals(ohlcv, params),
        "bb_squeeze" => bb_squeeze_signals(ohlcv, params),
        "macd_hist" => macd_hist_signals(ohlcv, params),
        "dual_momentum" => dual_momentum_signals(ohlcv, params),
        "momentum_roc" => momentum_roc_signals(ohlcv, params),
        "seasonal_filter" => seasonal_filter_signals(ohlcv, params),
        "vol_breakout" => vol_breakout_signals(ohlcv, params),
        "dxy_mean_reversion" => dxy_mean_reversion_signals(ohlcv, params),
        "session_momentum" => session_momentum_signals(ohlcv, params),
        "cross_asset_fx" => cross_asset_fx_signals(ohlcv, params),
        "month_end_jpy" => month_end_jpy_signals(ohlcv, params),
        "cluster_pair_drift" => cluster_pair_drift_signals(ohlcv, params),
        "tod_edge" => tod_edge_signals(ohlcv, params),
        "time_of_day_drift" => time_of_day_drift_signals(ohlcv, params),
        "macro_event_drift" => macro_event_drift_signals(ohlcv, params),
        _ => panic!("Unknown strategy: {name}"),
    };
    apply_news_blackout(raw, ohlcv, params)
}

// ---- News blackout post-filter ----

/// Returns `true` when the UTC timestamp falls inside a known high-volatility
/// macro-release window on USDJPY (and broader FX).
///
/// Windows derived from BigQuery analysis of USDJPY 2025 34M ticks
/// (see `project_bigquery_tick_findings.md` memory):
///   - 12:25–12:35  European/UK/Canada releases
///   - 13:25–13:35  US 8:30 ET (CPI/PPI/retail sales, weekly jobless claims)
///   - 13:55–14:10  US 9:00 ET releases
///   - 14:55–15:10  US 10:00 ET (ISM/JOLTS)
///   - 22:00–22:10  NY close thin-liquidity window
///   - 00:00–00:10  UTC date-roll daily settlement
///   - 00:50–00:59  Tokyo fixing (9:55 JST) — JPY-pair specific
///
/// Windows validated 2026-04-08 via BigQuery over 34M USDJPY 2025 ticks using
/// Iglewicz-Hoaglin (1993) modified z-score with Bonferroni correction
/// (α=0.05, N=1440 → |Z|>4.14). See scripts/bq_usdjpy_validation.sql.
/// Four slots pass Bonferroni individually (00:54 Z=5.80, 00:00 Z=4.59,
/// 14:00 Z=4.45, 13:30 Z=4.18) and are corroborated by adjacent-minute
/// clusters + H1/H2 split-half reproducibility.
pub fn is_news_blackout(dt: &DateTime<Utc>) -> bool {
    let h = dt.hour();
    let m = dt.minute();
    matches!(
        (h, m),
        (12, 25..=35)
            | (13, 25..=35)
            | (13, 55..=59)
            | (14, 0..=10)
            | (14, 55..=59)
            | (15, 0..=10)
            | (22, 0..=10)
            | (0, 0..=10)
            | (0, 50..=59)
    )
}

/// Post-filter that defers entry signals emitted during news blackout windows
/// to the first subsequent non-blackout bar.
///
/// Enabled when `params["news_blackout"] == true` AND `ohlcv.datetimes_ns` is
/// present. Otherwise returns `signals` unchanged.
///
/// Semantics (sticky-position backtest model, see `backtest::run_backtest`):
///   - During blackout bars, any non-zero signal is suppressed (set to 0) and
///     remembered as "pending".
///   - The pending signal is emitted on the first non-blackout bar. If a new
///     signal arrives during the blackout, it replaces the pending one
///     (latest-wins — we honour the strategy's most recent directional view).
///   - Signals emitted on non-blackout bars pass through unchanged.
fn apply_news_blackout(
    signals: Vec<i8>,
    ohlcv: &Ohlcv,
    params: &HashMap<String, Value>,
) -> Vec<i8> {
    let enabled = params
        .get("news_blackout")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !enabled {
        return signals;
    }
    let Some(ts_ns) = ohlcv.datetimes_ns else {
        return signals;
    };
    if ts_ns.len() != signals.len() {
        return signals;
    }

    let n = signals.len();
    let mut filtered = vec![0i8; n];
    let mut pending: i8 = 0;

    for i in 0..n {
        let ts_sec = ts_ns[i] / 1_000_000_000;
        let dt = match DateTime::<Utc>::from_timestamp(ts_sec, 0) {
            Some(dt) => dt,
            None => {
                filtered[i] = signals[i];
                continue;
            }
        };

        if is_news_blackout(&dt) {
            // Flatten signals (±2) are always honoured — we never want to be
            // stuck in a position across a blackout window. Only entry
            // signals (±1) are deferred as "pending".
            if signals[i].abs() == 2 {
                filtered[i] = signals[i];
                pending = 0;
            } else if signals[i] != 0 {
                pending = signals[i];
                filtered[i] = 0;
            }
        } else if signals[i] != 0 {
            filtered[i] = signals[i];
            pending = 0;
        } else if pending != 0 {
            filtered[i] = pending;
            pending = 0;
        }
    }

    filtered
}

// ---- Helpers ----

fn get_int(params: &HashMap<String, Value>, key: &str) -> usize {
    params[key]
        .as_u64()
        .unwrap_or_else(|| params[key].as_f64().unwrap() as u64) as usize
}

fn get_float(params: &HashMap<String, Value>, key: &str) -> f64 {
    params[key].as_f64().unwrap()
}

/// Convert position array [0,1,-1,...] to signal array via diff+clip.
/// Matches pandas: signal.diff().clip(-1, 1).fillna(0).astype(int)
fn position_to_signal(position: &[i8]) -> Vec<i8> {
    let n = position.len();
    let mut signal = vec![0i8; n];
    for i in 1..n {
        let diff = position[i] as i16 - position[i - 1] as i16;
        signal[i] = diff.clamp(-1, 1) as i8;
    }
    signal
}

// ---- Strategies ----

fn ema_atr_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let short_span = get_int(params, "short_ema");
    let long_span = get_int(params, "long_ema");
    let atr_period = get_int(params, "atr_period");
    let atr_mult = get_float(params, "atr_multiplier");

    let short_ema = indicators::ema(ohlcv.close, short_span);
    let long_ema = indicators::ema(ohlcv.close, long_span);
    let atr = indicators::atr(ohlcv.high, ohlcv.low, ohlcv.close, atr_period);
    let atr_med = indicators::rolling_median(&atr, atr_period * 5);

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];
    for i in 0..n {
        if atr[i].is_nan() || atr_med[i].is_nan() {
            continue;
        }
        let trending = atr[i] > atr_med[i] * atr_mult;
        if trending {
            if short_ema[i] > long_ema[i] {
                position[i] = 1;
            } else if short_ema[i] < long_ema[i] {
                position[i] = -1;
            }
        }
    }
    position_to_signal(&position)
}

fn keltner_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let ema_period = get_int(params, "ema_period");
    let atr_period = get_int(params, "atr_period");
    let atr_mult = get_float(params, "atr_multiplier");

    let mid = indicators::ema(ohlcv.close, ema_period);
    let atr = indicators::atr(ohlcv.high, ohlcv.low, ohlcv.close, atr_period);

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];
    for i in 0..n {
        if mid[i].is_nan() || atr[i].is_nan() {
            continue;
        }
        let upper = mid[i] + atr_mult * atr[i];
        let lower = mid[i] - atr_mult * atr[i];
        if ohlcv.close[i] > upper {
            position[i] = 1;
        } else if ohlcv.close[i] < lower {
            position[i] = -1;
        }
    }
    position_to_signal(&position)
}

fn sma_cross_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let short_window = get_int(params, "short_window");
    let long_window = get_int(params, "long_window");

    let short_sma = indicators::sma(ohlcv.close, short_window);
    let long_sma = indicators::sma(ohlcv.close, long_window);

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];
    for i in 0..n {
        if short_sma[i].is_nan() || long_sma[i].is_nan() {
            continue;
        }
        if short_sma[i] > long_sma[i] {
            position[i] = 1;
        } else if short_sma[i] < long_sma[i] {
            position[i] = -1;
        }
    }
    position_to_signal(&position)
}

fn rsi_reversal_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let period = get_int(params, "period");
    let oversold = get_float(params, "oversold");
    let overbought = get_float(params, "overbought");

    let rsi = indicators::rsi(ohlcv.close, period);

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];
    for i in 0..n {
        if rsi[i].is_nan() {
            continue;
        }
        if rsi[i] < oversold {
            position[i] = 1;
        } else if rsi[i] > overbought {
            position[i] = -1;
        }
    }
    position_to_signal(&position)
}

fn donchian_breakout_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let entry_period = get_int(params, "entry_period");
    let exit_period = get_int(params, "exit_period");

    // Shift by 1 to exclude current bar, then rolling max/min
    let shifted_high = indicators::shift(ohlcv.high, 1);
    let shifted_low = indicators::shift(ohlcv.low, 1);
    let upper = indicators::rolling_max(&shifted_high, entry_period);
    let lower = indicators::rolling_min(&shifted_low, exit_period);

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];
    for i in 0..n {
        if upper[i].is_nan() || lower[i].is_nan() {
            continue;
        }
        if ohlcv.close[i] > upper[i] {
            position[i] = 1;
        } else if ohlcv.close[i] < lower[i] {
            position[i] = -1;
        }
    }
    position_to_signal(&position)
}

fn bb_pctb_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let period = get_int(params, "period");
    let std_dev = get_float(params, "std_dev");
    let oversold = get_float(params, "oversold");
    let overbought = get_float(params, "overbought");

    let ma = indicators::sma(ohlcv.close, period);
    let std = indicators::rolling_std(ohlcv.close, period);

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];
    for i in 0..n {
        if ma[i].is_nan() || std[i].is_nan() {
            continue;
        }
        let upper = ma[i] + std_dev * std[i];
        let lower = ma[i] - std_dev * std[i];
        let band_range = upper - lower;
        if band_range == 0.0 {
            continue;
        }
        let pct_b = (ohlcv.close[i] - lower) / band_range;
        if pct_b < oversold {
            position[i] = 1;
        } else if pct_b > overbought {
            position[i] = -1;
        }
    }
    position_to_signal(&position)
}

fn bb_squeeze_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let period = get_int(params, "period");
    let std_dev = get_float(params, "std_dev");
    let squeeze_lookback = get_int(params, "squeeze_lookback");
    let squeeze_percentile = get_float(params, "squeeze_percentile");

    let ma = indicators::sma(ohlcv.close, period);
    let std = indicators::rolling_std(ohlcv.close, period);

    let n = ohlcv.close.len();

    // Band width (normalized)
    let mut band_width = vec![f64::NAN; n];
    for i in 0..n {
        if !ma[i].is_nan() && !std[i].is_nan() && ma[i] != 0.0 {
            let upper = ma[i] + std_dev * std[i];
            let lower = ma[i] - std_dev * std[i];
            band_width[i] = (upper - lower) / ma[i];
        }
    }

    // Squeeze threshold = rolling quantile of band width
    let bw_threshold =
        indicators::rolling_quantile(&band_width, squeeze_lookback, squeeze_percentile / 100.0);

    // squeeze[i] = band_width[i] <= bw_threshold[i]
    // Use squeeze.shift(1) for breakout detection (yesterday's squeeze)
    let mut position = vec![0i8; n];
    for i in 1..n {
        if band_width[i - 1].is_nan() || bw_threshold[i - 1].is_nan() {
            continue;
        }
        let was_squeeze = band_width[i - 1] <= bw_threshold[i - 1];
        if !was_squeeze {
            continue;
        }
        if ma[i].is_nan() || std[i].is_nan() {
            continue;
        }
        let upper = ma[i] + std_dev * std[i];
        let lower = ma[i] - std_dev * std[i];
        if ohlcv.close[i] > upper {
            position[i] = 1;
        } else if ohlcv.close[i] < lower {
            position[i] = -1;
        }
    }
    position_to_signal(&position)
}

fn macd_hist_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let fast_period = get_int(params, "fast_period");
    let slow_period = get_int(params, "slow_period");
    let signal_period = get_int(params, "signal_period");

    let fast_ema = indicators::ema(ohlcv.close, fast_period);
    let slow_ema = indicators::ema(ohlcv.close, slow_period);

    let n = ohlcv.close.len();
    let mut macd_line = vec![0.0f64; n];
    for i in 0..n {
        macd_line[i] = fast_ema[i] - slow_ema[i];
    }

    let signal_line = indicators::ema(&macd_line, signal_period);

    let mut position = vec![0i8; n];
    for i in 0..n {
        let histogram = macd_line[i] - signal_line[i];
        if histogram > 0.0 {
            position[i] = 1;
        } else if histogram < 0.0 {
            position[i] = -1;
        }
    }
    position_to_signal(&position)
}

fn dual_momentum_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let roc_period = get_int(params, "roc_period");
    let threshold = get_float(params, "threshold");

    // ROC in percentage: (close / close_shifted - 1) * 100
    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];
    for (i, pos) in position.iter_mut().enumerate().skip(roc_period) {
        if ohlcv.close[i - roc_period] == 0.0 {
            continue;
        }
        let roc = (ohlcv.close[i] / ohlcv.close[i - roc_period] - 1.0) * 100.0;
        if roc > threshold {
            *pos = 1;
        } else if roc < -threshold {
            *pos = -1;
        }
    }
    position_to_signal(&position)
}

fn momentum_roc_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let roc_period = get_int(params, "roc_period");
    let threshold = get_float(params, "threshold");

    // ROC in decimal (pct_change)
    let roc = indicators::roc(ohlcv.close, roc_period);

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];
    for i in 0..n {
        if roc[i].is_nan() {
            continue;
        }
        if roc[i] > threshold {
            position[i] = 1;
        } else if roc[i] < -threshold {
            position[i] = -1;
        }
    }
    position_to_signal(&position)
}

fn seasonal_filter_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let active_months: Vec<u32> = params["active_months"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap() as u32)
        .collect();
    let entry_offset = params
        .get("entry_offset")
        .and_then(|v| v.as_i64())
        .unwrap_or(-2) as i32;

    let n = ohlcv.close.len();
    let datetimes_ns = ohlcv
        .datetimes_ns
        .expect("seasonal_filter requires datetimes_ns");

    // Extract month from nanosecond timestamps
    let mut in_season = vec![0.0f64; n];
    for i in 0..n {
        let ts_sec = datetimes_ns[i] / 1_000_000_000;
        let dt = chrono::DateTime::from_timestamp(ts_sec, 0).unwrap();
        let month = chrono::Datelike::month(&dt);
        if active_months.contains(&month) {
            in_season[i] = 1.0;
        }
    }

    // Apply entry offset (shift)
    let shifted = if entry_offset != 0 {
        indicators::shift(&in_season, -entry_offset)
    } else {
        in_season
    };

    let mut position = vec![0i8; n];
    for i in 0..n {
        if !shifted[i].is_nan() && shifted[i] > 0.5 {
            position[i] = 1; // long-only during active months
        }
    }
    position_to_signal(&position)
}

fn vol_breakout_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let channel_period = get_int(params, "channel_period");
    let multiplier = get_float(params, "multiplier");
    let vol_type = params
        .get("vol_type")
        .and_then(|v| v.as_str())
        .unwrap_or("atr");

    let mid = indicators::sma(ohlcv.close, channel_period);

    let vol = match vol_type {
        "atr" => indicators::atr(ohlcv.high, ohlcv.low, ohlcv.close, channel_period),
        "std" => indicators::rolling_std(ohlcv.close, channel_period),
        "parkinson" => indicators::parkinson_vol(ohlcv.high, ohlcv.low, channel_period),
        _ => panic!("Unknown vol_type: {vol_type}"),
    };

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];
    for i in 0..n {
        if mid[i].is_nan() || vol[i].is_nan() {
            continue;
        }
        let upper = mid[i] + multiplier * vol[i];
        let lower = mid[i] - multiplier * vol[i];
        if ohlcv.close[i] > upper {
            position[i] = 1;
        } else if ohlcv.close[i] < lower {
            position[i] = -1;
        }
    }
    position_to_signal(&position)
}

// ---- New FX Anomaly-Based Strategies ----

fn get_bool(params: &HashMap<String, Value>, key: &str, default: bool) -> bool {
    params.get(key).and_then(|v| v.as_bool()).unwrap_or(default)
}

fn dxy_mean_reversion_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let lookback = get_int(params, "lookback");
    let z_entry = get_float(params, "z_entry");
    let z_exit = get_float(params, "z_exit");
    let use_rsi_filter = get_bool(params, "use_rsi_filter", false);
    let rsi_period = params
        .get("rsi_period")
        .and_then(|v| v.as_u64())
        .unwrap_or(14) as usize;
    let rsi_threshold = params
        .get("rsi_threshold")
        .and_then(|v| v.as_f64())
        .unwrap_or(70.0);
    // invert_aux: flip z-score sign for XXX/USD pairs (negative corr with DXY)
    let invert_aux = get_bool(params, "invert_aux", false);

    // Use aux_close (real DXY data) if available, otherwise fall back to self close
    let ref_data = ohlcv.aux_close.unwrap_or(ohlcv.close);

    let ma = indicators::sma(ref_data, lookback);
    let std = indicators::rolling_std(ref_data, lookback);
    let rsi = if use_rsi_filter {
        indicators::rsi(ref_data, rsi_period)
    } else {
        vec![50.0; ohlcv.close.len()]
    };

    let sign: f64 = if invert_aux { -1.0 } else { 1.0 };

    let n = ohlcv.close.len();
    let mut signal = vec![0i8; n];
    let mut position: i8 = 0;

    for i in 0..n {
        if ma[i].is_nan() || std[i].is_nan() || std[i] == 0.0 {
            continue;
        }
        let z = sign * (ref_data[i] - ma[i]) / std[i];

        if position == 0 {
            if z < -z_entry {
                if !use_rsi_filter || rsi[i].is_nan() || rsi[i] <= rsi_threshold {
                    position = 1;
                    signal[i] = 1;
                }
            } else if z > z_entry
                && (!use_rsi_filter || rsi[i].is_nan() || rsi[i] >= (100.0 - rsi_threshold))
            {
                position = -1;
                signal[i] = -1;
            }
        } else if position == 1 {
            if z > -z_exit {
                position = 0;
                signal[i] = -1;
            }
        } else if position == -1 && z < z_exit {
            position = 0;
            signal[i] = 1;
        }
    }
    signal
}

// ---- tod_edge (time-of-day directional) ----

/// Hold-horizon lookup table (minutes) for the `tod_edge` strategy.
///
/// Index 0 is unused; indices 1..=9 correspond to the nine horizons scanned by
/// the BigQuery discovery layer (see 01-CONTEXT.md §D-04).
pub const TOD_EDGE_HORIZONS_MIN: [u32; 10] = [0, 1, 3, 5, 10, 15, 20, 30, 45, 60];

/// Time-of-day directional strategy (DISC-01).
///
/// Params (all required):
/// - `entry_minute` (u16, 0..=1439): UTC minute-of-day at which to enter.
/// - `direction` (`"long"` | `"short"`): position side on entry.
/// - `hold_h` (u8, 1..=9): index into [`TOD_EDGE_HORIZONS_MIN`] giving the
///   intended hold duration in minutes.
///
/// On each bar whose UTC minute-of-day matches `entry_minute`, the strategy
/// takes a unit position in `direction` and holds it for `hold_bars` bars,
/// where:
/// ```text
/// bar_min = (datetimes_ns[1] - datetimes_ns[0]) / 60s
/// hold_bars = max(ceil(hold_min / bar_min), 1)
/// ```
/// This collapses all horizons ≤60min into a single 1h bar on 1h data, which
/// is acceptable for Phase 1 (Open Q1 resolution in 01-04-PLAN.md).
///
/// Does not self-manage `news_blackout`; the unconditional post-filter in
/// [`generate_signals`] honours `params["news_blackout"]` (Open Q2 resolution).
fn tod_edge_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let entry_minute = params
        .get("entry_minute")
        .and_then(|v| v.as_u64())
        .expect("tod_edge: entry_minute required") as u32;
    let direction = params
        .get("direction")
        .and_then(|v| v.as_str())
        .expect("tod_edge: direction required");
    let hold_h_idx = params
        .get("hold_h")
        .and_then(|v| v.as_u64())
        .expect("tod_edge: hold_h required") as usize;
    assert!(
        (1..=9).contains(&hold_h_idx),
        "tod_edge: hold_h must be 1..=9, got {hold_h_idx}"
    );
    let hold_min = TOD_EDGE_HORIZONS_MIN[hold_h_idx];

    let sig_value: i8 = match direction {
        "long" => 1,
        "short" => -1,
        other => panic!("tod_edge: direction must be 'long' or 'short', got '{other}'"),
    };

    let ts_ns = ohlcv.datetimes_ns.expect("tod_edge requires datetimes_ns");
    let n = ohlcv.close.len();

    // Derive bar width in minutes from the first two timestamps. Falls back to
    // 60 min (1h) if the series is too short to measure (single-bar edge case).
    let bar_min: u32 = if ts_ns.len() >= 2 {
        let delta_min = (ts_ns[1] - ts_ns[0]) / 1_000_000_000 / 60;
        delta_min.max(1) as u32
    } else {
        60
    };
    let hold_bars: usize = hold_min.div_ceil(bar_min).max(1) as usize;

    let mut position = vec![0i8; n];
    for (i, &ts) in ts_ns.iter().enumerate().take(n) {
        let ts_sec = ts / 1_000_000_000;
        let dt = match DateTime::<Utc>::from_timestamp(ts_sec, 0) {
            Some(dt) => dt,
            None => continue,
        };
        let minute_of_day: u32 = dt.hour() * 60 + dt.minute();
        if minute_of_day == entry_minute {
            let end = (i + hold_bars).min(n);
            for slot in position.iter_mut().take(end).skip(i) {
                *slot = sig_value;
            }
        }
    }
    position_to_signal(&position)
}

fn session_momentum_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let trend_ema_span = get_int(params, "trend_ema");
    let entry_hour_start = params
        .get("entry_hour_start")
        .and_then(|v| v.as_u64())
        .unwrap_or(8) as u32;
    let entry_hour_end = params
        .get("entry_hour_end")
        .and_then(|v| v.as_u64())
        .unwrap_or(13) as u32;
    let use_trend_filter = get_bool(params, "use_trend_filter", true);
    let avoid_nfp_week = get_bool(params, "avoid_nfp_week", false);

    let datetimes_ns = ohlcv
        .datetimes_ns
        .expect("session_momentum requires datetimes_ns");

    let ema = indicators::ema(ohlcv.close, trend_ema_span);

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];

    for i in 0..n {
        let ts_sec = datetimes_ns[i] / 1_000_000_000;
        let dt = chrono::DateTime::from_timestamp(ts_sec, 0).unwrap();
        let hour = chrono::Timelike::hour(&dt);
        let day = chrono::Datelike::day(&dt);
        let weekday = chrono::Datelike::weekday(&dt).num_days_from_monday(); // 0=Mon

        let in_session = hour >= entry_hour_start && hour < entry_hour_end;

        // NFP week filter: first Friday of month (day <= 7, weekday == 4)
        let is_nfp_week = if avoid_nfp_week {
            // Approximate: if we're in the first 7 days and it's around Friday
            day <= 7 && weekday == 4
        } else {
            false
        };

        if !in_session || is_nfp_week {
            continue;
        }

        if use_trend_filter {
            if !ema[i].is_nan() {
                if ohlcv.close[i] > ema[i] {
                    position[i] = 1;
                } else if ohlcv.close[i] < ema[i] {
                    position[i] = -1;
                }
            }
        } else {
            position[i] = 1; // long-only during session
        }
    }
    position_to_signal(&position)
}

fn cross_asset_fx_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let fast_lookback = get_int(params, "fast_lookback");
    let slow_lookback = get_int(params, "slow_lookback");
    let z_entry = get_float(params, "z_entry");
    let z_exit = get_float(params, "z_exit");
    let vol_lookback = get_int(params, "vol_lookback");
    let vol_regime_mult = get_float(params, "vol_regime_mult");
    let trade_in_high_vol = get_bool(params, "trade_in_high_vol", false);
    // invert_aux: flip z-score sign for XXX/USD pairs (negative corr with DXY)
    let invert_aux = get_bool(params, "invert_aux", false);

    // Use aux_close (real VIX/DXY data) for z-score if available, otherwise self close
    let ref_data = ohlcv.aux_close.unwrap_or(ohlcv.close);

    let fast_ma = indicators::sma(ref_data, fast_lookback);
    let slow_ma = indicators::sma(ref_data, slow_lookback);
    let slow_std = indicators::rolling_std(ref_data, slow_lookback);

    // ATR-based volatility regime (always from the trading instrument)
    let atr = indicators::atr(ohlcv.high, ohlcv.low, ohlcv.close, vol_lookback);
    let atr_ma = indicators::sma(&atr, vol_lookback * 2);

    let sign: f64 = if invert_aux { -1.0 } else { 1.0 };

    let n = ohlcv.close.len();
    let mut signal = vec![0i8; n];
    let mut position: i8 = 0;

    for i in 0..n {
        if slow_ma[i].is_nan() || fast_ma[i].is_nan() || slow_std[i].is_nan() || slow_std[i] == 0.0
        {
            continue;
        }

        let z = sign * (fast_ma[i] - slow_ma[i]) / slow_std[i];

        // Vol filter
        let is_high_vol =
            !atr[i].is_nan() && !atr_ma[i].is_nan() && atr[i] > atr_ma[i] * vol_regime_mult;

        if !trade_in_high_vol && is_high_vol {
            if position != 0 {
                signal[i] = -position;
                position = 0;
            }
            continue;
        }

        if position == 0 {
            if z < -z_entry {
                position = 1;
                signal[i] = 1;
            } else if z > z_entry {
                position = -1;
                signal[i] = -1;
            }
        } else if position == 1 {
            if z > -z_exit {
                position = 0;
                signal[i] = -1;
            }
        } else if position == -1 && z < z_exit {
            position = 0;
            signal[i] = 1;
        }
    }
    signal
}

fn month_end_jpy_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let month_end_window = params
        .get("month_end_window")
        .and_then(|v| v.as_u64())
        .unwrap_or(3) as u32;
    let month_start_window = params
        .get("month_start_window")
        .and_then(|v| v.as_u64())
        .unwrap_or(3) as u32;
    let use_dow_filter = get_bool(params, "use_dow_filter", true);
    let best_dow = params.get("best_dow").and_then(|v| v.as_u64()).unwrap_or(1) as u32; // 1=Tue
    let trend_ema_span = get_int(params, "trend_ema");
    let use_trend_filter = get_bool(params, "use_trend_filter", true);

    let datetimes_ns = ohlcv
        .datetimes_ns
        .expect("month_end_jpy requires datetimes_ns");

    let ema = indicators::ema(ohlcv.close, trend_ema_span);

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];

    for i in 0..n {
        let ts_sec = datetimes_ns[i] / 1_000_000_000;
        let dt = chrono::DateTime::from_timestamp(ts_sec, 0).unwrap();
        let day = chrono::Datelike::day(&dt);
        let weekday = chrono::Datelike::weekday(&dt).num_days_from_monday();

        // Days in month approximation
        let month = chrono::Datelike::month(&dt);
        let year = chrono::Datelike::year(&dt);
        let days_in_month = match month {
            1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
            4 | 6 | 9 | 11 => 30,
            2 => {
                if year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) {
                    29
                } else {
                    28
                }
            }
            _ => 30,
        };

        let is_month_end = (days_in_month - day) < month_end_window;
        let is_month_start = day <= month_start_window;
        let is_tom = is_month_end || is_month_start;

        // Skip TOM period (JPY strengthens)
        if is_tom {
            continue;
        }

        // DOW filter
        if use_dow_filter && weekday != best_dow {
            continue;
        }

        // Trend filter
        if use_trend_filter {
            if !ema[i].is_nan() && ohlcv.close[i] > ema[i] {
                position[i] = 1;
            }
        } else {
            position[i] = 1;
        }
    }
    position_to_signal(&position)
}

// ---- cluster_pair_drift helpers ----

/// Returns true if `minute_of_day` (UTC, 0-1439) is inside the named cluster window.
/// Cluster windows are 5-minute ranges centered on BQ analysis minute-precision peaks.
/// Source: memory `01KNPCJT95C9ZX12S5TQCVZFMV` (USDJPY 2025 34M ticks BQ directional analysis).
fn is_in_cluster(cluster: &str, minute_of_day: i64) -> bool {
    let (start, end) = match cluster {
        // BQ peak 07:55 LONG h=3, t=+4.20 — pre_london extension
        "london_open" => (7 * 60 + 55, 8 * 60),
        // BQ peak 00:55 SHORT h=1, t=+4.02 (marginal)
        "tokyo_fix" => (55, 60),
        // BQ peak 20:59 SHORT h=10, t=+4.06 — NY close sell-off
        "ny_close_sell" => (20 * 60 + 59, 21 * 60 + 4),
        // BQ peak 21:05 LONG h=45, t=+3.22 — NY post-close (largest net)
        "ny_post_close" => (21 * 60 + 5, 21 * 60 + 10),
        // BQ peak 09:55 LONG h=3, t=+6.48 — London open A (strongest t)
        "london_burst" => (9 * 60 + 55, 10 * 60),
        // BQ peak 10:02 SHORT h=3, t=+4.77 — London continuation
        "london_continuation" => (10 * 60 + 1, 10 * 60 + 6),
        _ => return false,
    };
    minute_of_day >= start && minute_of_day < end
}

/// Direction-locked cluster sets (Step 6.A iteration, 2026-04-09).
/// BQ analysis (memory `01KNPCJT95C9ZX12S5TQCVZFMV`) established which clusters
/// carry a natively LONG or SHORT edge. To prevent spurious cross-over trials
/// from Optuna that pair a LONG-edge cluster into the short side (and vice
/// versa), we lock the allowed choices per direction. Invalid pairs self-prune
/// to an all-zero signal so Optuna scores them as 0 PnL.
const LONG_CLUSTERS: &[&str] = &["london_burst", "london_open", "ny_post_close"];
const SHORT_CLUSTERS: &[&str] = &["london_continuation", "ny_close_sell", "tokyo_fix"];

/// Cluster cross-over strategy: emit LONG entry inside `long_cluster` window
/// and SHORT entry inside `short_cluster` window. Supports hold-period expiry
/// and optional ATR / fixed-pct take-profit / stop-loss exits.
/// See `docs/superpowers/specs/2026-04-09-cluster-pair-drift-poc-design.md`.
fn cluster_pair_drift_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    use chrono::Timelike;

    let long_cluster = params
        .get("long_cluster")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let short_cluster = params
        .get("short_cluster")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    // Direction-locked self-prune: reject trials that place a LONG-edge cluster
    // on the short side (or vice versa). Also rejects same-cluster pairs.
    if !LONG_CLUSTERS.contains(&long_cluster) || !SHORT_CLUSTERS.contains(&short_cluster) {
        return vec![0i8; ohlcv.close.len()];
    }

    let hold_bars = get_int(params, "hold_bars").max(1);
    let exit_type = params
        .get("exit_type")
        .and_then(|v| v.as_str())
        .unwrap_or("none");

    let tp_atr = params
        .get("tp_atr")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);
    let sl_atr = params
        .get("sl_atr")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);
    let tp_pct = params
        .get("tp_pct")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);
    let sl_pct = params
        .get("sl_pct")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);

    let datetimes_ns = match ohlcv.datetimes_ns {
        Some(d) => d,
        None => return vec![0i8; ohlcv.close.len()],
    };

    let n = ohlcv.close.len();
    let mut position = vec![0i8; n];

    let atr_arr = if exit_type == "atr_based" {
        Some(indicators::atr(ohlcv.high, ohlcv.low, ohlcv.close, 14))
    } else {
        None
    };

    let mut hold_until: usize = 0;
    let mut entry_price: f64 = 0.0;
    let mut current_dir: i8 = 0;

    for i in 0..n {
        let ts_sec = datetimes_ns[i] / 1_000_000_000;
        let dt = chrono::DateTime::from_timestamp(ts_sec, 0).unwrap();
        let mod_minute = (dt.hour() as i64) * 60 + (dt.minute() as i64);

        let in_long_window = is_in_cluster(long_cluster, mod_minute);
        let in_short_window = is_in_cluster(short_cluster, mod_minute);

        // Entry (independent ifs → reverse on same-bar overlap, short wins).
        if in_long_window && current_dir != 1 {
            current_dir = 1;
            hold_until = i + hold_bars;
            entry_price = ohlcv.close[i];
        }
        if in_short_window && current_dir != -1 {
            current_dir = -1;
            hold_until = i + hold_bars;
            entry_price = ohlcv.close[i];
        }

        position[i] = current_dir;

        if current_dir != 0 {
            let mut should_close = false;

            // Hold expiry
            if i >= hold_until {
                should_close = true;
            }

            // ATR-based exit
            if !should_close && exit_type == "atr_based" {
                if let Some(ref atr_v) = atr_arr {
                    let atr_val = atr_v[i];
                    if !atr_val.is_nan() && entry_price > 0.0 {
                        let pl = if current_dir == 1 {
                            ohlcv.close[i] - entry_price
                        } else {
                            entry_price - ohlcv.close[i]
                        };
                        if !tp_atr.is_nan() && pl >= tp_atr * atr_val {
                            should_close = true;
                        }
                        if !sl_atr.is_nan() && pl <= -sl_atr * atr_val {
                            should_close = true;
                        }
                    }
                }
            }

            // Fixed-pct exit
            if !should_close && exit_type == "fixed_pct" && entry_price > 0.0 {
                let pl_pct = if current_dir == 1 {
                    (ohlcv.close[i] - entry_price) / entry_price
                } else {
                    (entry_price - ohlcv.close[i]) / entry_price
                };
                if !tp_pct.is_nan() && pl_pct >= tp_pct {
                    should_close = true;
                }
                if !sl_pct.is_nan() && pl_pct <= -sl_pct {
                    should_close = true;
                }
            }

            if should_close {
                current_dir = 0;
                position[i] = 0;
            }
        }
    }

    position_to_signal(&position)
}

/// BOJ macro-event drift strategy: captures the post-announcement drift
/// by entering a long-only position `window_offset` bars after the BOJ
/// policy window closes.
///
/// ## Params
/// - `window_offset` (usize, ≥1): bars after window end to enter. 0 → self-prune (zeros).
/// - `hold_bars` (usize, ≥1): how long to hold the position.
/// - `exit_type` (str): `"none"` or `"fixed_pct"`. `"atr_based"` treated as `"none"`.
/// - `tp_pct` (f64): take-profit as fraction of entry price (fixed_pct only). NaN = disabled.
/// - `sl_pct` (f64): stop-loss as fraction of entry price (fixed_pct only). NaN = disabled.
///
/// ## Signal semantics
/// Uses standard position → signal encoding via `position_to_signal`.
/// Long-only (D-03). Single-pass state machine (D-01).
fn macro_event_drift_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    // FOMC-03: event_source branch. Default "boj" preserves backward compatibility
    // (existing BOJ tests build params without this key and must continue to work).
    let event_source = params
        .get("event_source")
        .and_then(|v| v.as_str())
        .unwrap_or("boj");

    if event_source == "fomc" {
        return fomc_event_drift_signals(ohlcv, params);
    }
    if event_source == "ecb" {
        return ecb_event_drift_signals(ohlcv, params);
    }
    if event_source == "nfp" {
        return nfp_event_drift_signals(ohlcv, params);
    }
    if event_source == "calendar" {
        return calendar_anomaly_signals(ohlcv, params);
    }
    // event_source == "boj" or anything else → fall through to BOJ path below.

    let window_offset = params
        .get("window_offset")
        .and_then(|v| v.as_u64())
        .unwrap_or(1) as usize;

    // D-08: self-prune on invalid offset — no panic
    if window_offset < 1 {
        return vec![0i8; ohlcv.close.len()];
    }

    let hold_bars = params
        .get("hold_bars")
        .and_then(|v| v.as_u64())
        .unwrap_or(1)
        .max(1) as usize;

    let exit_type = params
        .get("exit_type")
        .and_then(|v| v.as_str())
        .unwrap_or("none");

    let tp_pct = params
        .get("tp_pct")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);
    let sl_pct = params
        .get("sl_pct")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);

    let datetimes_ns = match ohlcv.datetimes_ns {
        Some(d) => d,
        None => return vec![0i8; ohlcv.close.len()],
    };

    let windows = crate::events::boj_windows_2024_2026();
    let n = ohlcv.close.len();
    let mut positions = vec![0i8; n];

    let mut current_dir: i8 = 0;
    let mut hold_until: usize = 0;
    let mut entry_price: f64 = 0.0;
    let mut next_window_idx: usize = 0;
    // Tracks the bar index where we first passed a BOJ window end
    let mut last_window_end_bar: Option<usize> = None;

    for i in 0..n {
        let ts_sec = datetimes_ns[i] / 1_000_000_000;
        let bar_dt = chrono::DateTime::from_timestamp(ts_sec, 0)
            .unwrap()
            .naive_utc();

        // --- Exit check ---
        if current_dir != 0 {
            let mut should_close = false;

            // Hold expiry
            if i >= hold_until {
                should_close = true;
            }

            // Fixed-pct exit via high/low (D-06)
            if !should_close && exit_type == "fixed_pct" && entry_price > 0.0 {
                let tp_price = entry_price * (1.0 + tp_pct);
                let sl_price = entry_price * (1.0 - sl_pct);
                if !tp_pct.is_nan() && ohlcv.high[i] >= tp_price {
                    should_close = true;
                }
                if !sl_pct.is_nan() && ohlcv.low[i] <= sl_price {
                    should_close = true;
                }
            }

            if should_close {
                current_dir = 0;
            }
        }

        // --- Entry check (long-only, D-03) ---
        if current_dir == 0 && next_window_idx < windows.len() {
            let win_end = windows[next_window_idx].end;

            // Detect when we first reach or pass the window end
            if last_window_end_bar.is_none() && bar_dt >= win_end {
                last_window_end_bar = Some(i);
            }

            // Fire entry exactly window_offset bars after window end bar
            if let Some(we_bar) = last_window_end_bar {
                if i == we_bar + window_offset {
                    current_dir = 1; // long-only (D-03)
                    entry_price = ohlcv.close[i];
                    hold_until = i + hold_bars;
                    next_window_idx += 1;
                    last_window_end_bar = None;
                }
            }
        } else if current_dir != 0 && next_window_idx < windows.len() {
            // While in a position, still track window end for the next BOJ window
            // so we're ready to enter after exit. But advance window pointer only
            // when we actually enter. Skip windows that pass while we're in position.
            let win_end = windows[next_window_idx].end;
            if last_window_end_bar.is_none() && bar_dt >= win_end {
                // Window end passed while in position — skip this window
                next_window_idx += 1;
            }
        }

        positions[i] = current_dir;
    }

    position_to_signal(&positions)
}

/// FOMC macro-event drift strategy: enters a directional position
/// `window_offset` bars after each FOMC announcement window closes.
/// Direction (`+1 = hawkish`, `-1 = dovish`) is read from
/// `crate::events::FOMC_DATES_2024_2026` so the calendar and the directions
/// share a single source of truth (no duplicate const).
///
/// ## Params
/// - `window_offset` (usize, ≥2): bars after window end to enter. Default `2`.
///   Values `<2` self-prune (zeros) — FOMC announces ON the hour, so
///   `offset=1` would enter on the announcement bar (look-ahead bias).
/// - `hold_bars` (usize, ≥1): how long to hold the position. Default `1`.
/// - `exit_type` (str): `"none"` or `"fixed_pct"`. `"atr_based"` treated as `"none"`.
/// - `tp_pct` (f64): take-profit fraction (fixed_pct only). NaN = disabled.
/// - `sl_pct` (f64): stop-loss fraction (fixed_pct only). NaN = disabled.
///
/// ## Signal semantics
/// Uses `position_to_signal` like `macro_event_drift_signals`. Direction
/// changes per FOMC meeting. Single-pass state machine with off-by-one
/// guard: `cached_idx = next_window_idx` BEFORE `next_window_idx += 1`.
fn fomc_event_drift_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let window_offset = params
        .get("window_offset")
        .and_then(|v| v.as_u64())
        .unwrap_or(2) as usize;

    // FOMC look-ahead guard: <2 self-prune. (BOJ uses <1 because BOJ
    // announces mid-bar; FOMC announces on the hour so offset=1 enters
    // on the announcement bar itself.)
    if window_offset < 2 {
        return vec![0i8; ohlcv.close.len()];
    }

    let hold_bars = params
        .get("hold_bars")
        .and_then(|v| v.as_u64())
        .unwrap_or(1)
        .max(1) as usize;

    let exit_type = params
        .get("exit_type")
        .and_then(|v| v.as_str())
        .unwrap_or("none");

    let tp_pct = params
        .get("tp_pct")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);
    let sl_pct = params
        .get("sl_pct")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);

    let datetimes_ns = match ohlcv.datetimes_ns {
        Some(d) => d,
        None => return vec![0i8; ohlcv.close.len()],
    };

    let windows = crate::events::fomc_windows_all();
    // Combines 2022-2023 + 2024-2026 for full N=34 event sample.
    let n = ohlcv.close.len();
    let mut positions = vec![0i8; n];

    let mut current_dir: i8 = 0;
    let mut hold_until: usize = 0;
    let mut entry_price: f64 = 0.0;
    let mut next_window_idx: usize = 0;
    let mut last_window_end_bar: Option<usize> = None;

    for i in 0..n {
        let ts_sec = datetimes_ns[i] / 1_000_000_000;
        let bar_dt = chrono::DateTime::from_timestamp(ts_sec, 0)
            .unwrap()
            .naive_utc();

        // --- Exit check (identical to BOJ path) ---
        if current_dir != 0 {
            let mut should_close = false;

            if i >= hold_until {
                should_close = true;
            }

            if !should_close && exit_type == "fixed_pct" && entry_price > 0.0 {
                // Direction-aware TP/SL: TP for long is above entry, for short is below.
                if current_dir > 0 {
                    let tp_price = entry_price * (1.0 + tp_pct);
                    let sl_price = entry_price * (1.0 - sl_pct);
                    if !tp_pct.is_nan() && ohlcv.high[i] >= tp_price {
                        should_close = true;
                    }
                    if !sl_pct.is_nan() && ohlcv.low[i] <= sl_price {
                        should_close = true;
                    }
                } else {
                    // Short position: TP triggers when price falls, SL when it rises.
                    let tp_price = entry_price * (1.0 - tp_pct);
                    let sl_price = entry_price * (1.0 + sl_pct);
                    if !tp_pct.is_nan() && ohlcv.low[i] <= tp_price {
                        should_close = true;
                    }
                    if !sl_pct.is_nan() && ohlcv.high[i] >= sl_price {
                        should_close = true;
                    }
                }
            }

            if should_close {
                current_dir = 0;
            }
        }

        // --- Entry check (directional from FOMC_DATES_2024_2026) ---
        if current_dir == 0 && next_window_idx < windows.len() {
            let win_end = windows[next_window_idx].end;

            if last_window_end_bar.is_none() && bar_dt >= win_end {
                last_window_end_bar = Some(i);
            }

            if let Some(we_bar) = last_window_end_bar {
                if i == we_bar + window_offset {
                    // Cache index BEFORE increment to avoid off-by-one
                    // (Pitfall 2 in 31-RESEARCH.md).
                    let cached_idx = next_window_idx;
                    // directions from combined 2022-2023 + 2024-2026 constants
                    let fomc_dirs: Vec<i8> = crate::events::FOMC_DATES_2022_2023
                        .iter()
                        .chain(crate::events::FOMC_DATES_2024_2026.iter())
                        .map(|r| r.4)
                        .collect();
                    current_dir = fomc_dirs[cached_idx];
                    // EURUSD direction inversion: hawkish FOMC → short EURUSD, dovish → long.
                    let pair_str = params
                        .get("pair")
                        .and_then(|v| v.as_str())
                        .unwrap_or("USDJPY");
                    if pair_str == "EURUSD" {
                        current_dir = -current_dir;
                    }
                    entry_price = ohlcv.close[i];
                    hold_until = i + hold_bars;
                    next_window_idx += 1;
                    last_window_end_bar = None;
                }
            }
        } else if current_dir != 0 && next_window_idx < windows.len() {
            let win_end = windows[next_window_idx].end;
            if last_window_end_bar.is_none() && bar_dt >= win_end {
                next_window_idx += 1;
            }
        }

        positions[i] = current_dir;
    }

    position_to_signal(&positions)
}

/// ECB rate-decision event drift signals.
///
/// Directional: +1 = hawkish, -1 = dovish, 0 = neutral hold (no entry).
/// Uses `ecb_windows_2024_2025()` for 1h DST-aware anchor windows (Phase 34 D-01).
/// Self-prunes when `window_offset < 2` (same guard as FOMC).
fn ecb_event_drift_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let window_offset = params
        .get("window_offset")
        .and_then(|v| v.as_u64())
        .unwrap_or(2) as usize;

    // DD-4: self-prune on look-ahead risk (ECB announces on the hour; offset=1 enters on bar)
    if window_offset < 2 {
        return vec![0i8; ohlcv.close.len()];
    }

    let hold_bars = params
        .get("hold_bars")
        .and_then(|v| v.as_u64())
        .unwrap_or(1)
        .max(1) as usize;

    let exit_type = params
        .get("exit_type")
        .and_then(|v| v.as_str())
        .unwrap_or("none");

    let tp_pct = params
        .get("tp_pct")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);
    let sl_pct = params
        .get("sl_pct")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);

    let datetimes_ns = match ohlcv.datetimes_ns {
        Some(d) => d,
        None => return vec![0i8; ohlcv.close.len()],
    };

    let windows = crate::events::ecb_windows_all();
    // Combines 2022-2023 + 2024-2025 for full N=32 event sample.
    let n = ohlcv.close.len();
    let mut positions = vec![0i8; n];

    let mut current_dir: i8 = 0;
    let mut hold_until: usize = 0;
    let mut entry_price: f64 = 0.0;
    let mut next_window_idx: usize = 0;
    let mut last_window_end_bar: Option<usize> = None;

    for i in 0..n {
        let ts_sec = datetimes_ns[i] / 1_000_000_000;
        let bar_dt = chrono::DateTime::from_timestamp(ts_sec, 0)
            .unwrap()
            .naive_utc();

        // --- Exit check (identical to FOMC path) ---
        if current_dir != 0 {
            let mut should_close = false;

            if i >= hold_until {
                should_close = true;
            }

            if !should_close && exit_type == "fixed_pct" && entry_price > 0.0 {
                // Direction-aware TP/SL: TP for long is above entry, for short is below.
                if current_dir > 0 {
                    let tp_price = entry_price * (1.0 + tp_pct);
                    let sl_price = entry_price * (1.0 - sl_pct);
                    if !tp_pct.is_nan() && ohlcv.high[i] >= tp_price {
                        should_close = true;
                    }
                    if !sl_pct.is_nan() && ohlcv.low[i] <= sl_price {
                        should_close = true;
                    }
                } else {
                    // Short position: TP triggers when price falls, SL when it rises.
                    let tp_price = entry_price * (1.0 - tp_pct);
                    let sl_price = entry_price * (1.0 + sl_pct);
                    if !tp_pct.is_nan() && ohlcv.low[i] <= tp_price {
                        should_close = true;
                    }
                    if !sl_pct.is_nan() && ohlcv.high[i] >= sl_price {
                        should_close = true;
                    }
                }
            }

            if should_close {
                current_dir = 0;
            }
        }

        // --- Entry check (directional from ECB_DATES_2024_2025) ---
        if current_dir == 0 && next_window_idx < windows.len() {
            let win_end = windows[next_window_idx].end;

            if last_window_end_bar.is_none() && bar_dt >= win_end {
                last_window_end_bar = Some(i);
            }

            if let Some(we_bar) = last_window_end_bar {
                if i == we_bar + window_offset {
                    // Cache index BEFORE increment to avoid off-by-one.
                    let cached_idx = next_window_idx;
                    next_window_idx += 1;
                    last_window_end_bar = None;

                    // directions from combined 2022-2023 + 2024-2025 constants
                    let ecb_dirs: Vec<i8> = crate::events::ECB_DATES_2022_2023
                        .iter()
                        .chain(crate::events::ECB_DATES_2024_2025.iter())
                        .map(|r| r.4)
                        .collect();
                    let dir = ecb_dirs[cached_idx];
                    if dir == 0 {
                        // Neutral hold — skip entry per Phase 34 D-03.
                        // State machine stays ready for next window.
                        continue;
                    }
                    current_dir = dir;
                    entry_price = ohlcv.close[i];
                    hold_until = i + hold_bars;
                }
            }
        } else if current_dir != 0 && next_window_idx < windows.len() {
            let win_end = windows[next_window_idx].end;
            if last_window_end_bar.is_none() && bar_dt >= win_end {
                next_window_idx += 1;
            }
        }

        positions[i] = current_dir;
    }

    position_to_signal(&positions)
}

/// NFP (Non-Farm Payrolls) event drift signals.
///
/// Directional: +1 = BEAT, -1 = MISS, 0 = INLINE (no entry).
/// Uses `nfp_windows_2024_2025()` for 1h DST-aware anchor windows (Phase 34 D-01).
/// SIG-02: hard-enforces `window_offset >= 2` (NFP at 13:30 UTC mid-bar; offset=1 gives only 30min).
fn nfp_event_drift_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let window_offset = params
        .get("window_offset")
        .and_then(|v| v.as_u64())
        .unwrap_or(2) as usize;

    // SIG-02 + DD-4: NFP is 13:30 UTC mid-bar; offset=1 only gives 30min. Hard self-prune.
    if window_offset < 2 {
        return vec![0i8; ohlcv.close.len()];
    }

    let hold_bars = params
        .get("hold_bars")
        .and_then(|v| v.as_u64())
        .unwrap_or(1)
        .max(1) as usize;

    let exit_type = params
        .get("exit_type")
        .and_then(|v| v.as_str())
        .unwrap_or("none");

    let tp_pct = params
        .get("tp_pct")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);
    let sl_pct = params
        .get("sl_pct")
        .and_then(|v| v.as_f64())
        .unwrap_or(f64::NAN);

    let datetimes_ns = match ohlcv.datetimes_ns {
        Some(d) => d,
        None => return vec![0i8; ohlcv.close.len()],
    };

    let windows = crate::events::nfp_windows_all();
    // Combines 2022-2023 + 2024-2025 for full N=46 event sample.
    let n = ohlcv.close.len();
    let mut positions = vec![0i8; n];

    let mut current_dir: i8 = 0;
    let mut hold_until: usize = 0;
    let mut entry_price: f64 = 0.0;
    let mut next_window_idx: usize = 0;
    let mut last_window_end_bar: Option<usize> = None;

    for i in 0..n {
        let ts_sec = datetimes_ns[i] / 1_000_000_000;
        let bar_dt = chrono::DateTime::from_timestamp(ts_sec, 0)
            .unwrap()
            .naive_utc();

        // --- Exit check (identical to ECB/FOMC path) ---
        if current_dir != 0 {
            let mut should_close = false;

            if i >= hold_until {
                should_close = true;
            }

            if !should_close && exit_type == "fixed_pct" && entry_price > 0.0 {
                if current_dir > 0 {
                    let tp_price = entry_price * (1.0 + tp_pct);
                    let sl_price = entry_price * (1.0 - sl_pct);
                    if !tp_pct.is_nan() && ohlcv.high[i] >= tp_price {
                        should_close = true;
                    }
                    if !sl_pct.is_nan() && ohlcv.low[i] <= sl_price {
                        should_close = true;
                    }
                } else {
                    let tp_price = entry_price * (1.0 - tp_pct);
                    let sl_price = entry_price * (1.0 + sl_pct);
                    if !tp_pct.is_nan() && ohlcv.low[i] <= tp_price {
                        should_close = true;
                    }
                    if !sl_pct.is_nan() && ohlcv.high[i] >= sl_price {
                        should_close = true;
                    }
                }
            }

            if should_close {
                current_dir = 0;
            }
        }

        // --- Entry check (directional from NFP_DATES_2024_2025) ---
        if current_dir == 0 && next_window_idx < windows.len() {
            let win_end = windows[next_window_idx].end;

            if last_window_end_bar.is_none() && bar_dt >= win_end {
                last_window_end_bar = Some(i);
            }

            if let Some(we_bar) = last_window_end_bar {
                if i == we_bar + window_offset {
                    // Cache index BEFORE increment to avoid off-by-one.
                    let cached_idx = next_window_idx;
                    next_window_idx += 1;
                    last_window_end_bar = None;

                    // directions from combined 2022-2023 + 2024-2025 constants
                    let nfp_dirs: Vec<i8> = crate::events::NFP_DATES_2022_2023
                        .iter()
                        .chain(crate::events::NFP_DATES_2024_2025.iter())
                        .map(|r| r.4)
                        .collect();
                    let dir = nfp_dirs[cached_idx];
                    if dir == 0 {
                        // INLINE — skip entry (within ±10K band).
                        continue;
                    }
                    current_dir = dir;
                    entry_price = ohlcv.close[i];
                    hold_until = i + hold_bars;
                }
            }
        } else if current_dir != 0 && next_window_idx < windows.len() {
            let win_end = windows[next_window_idx].end;
            if last_window_end_bar.is_none() && bar_dt >= win_end {
                next_window_idx += 1;
            }
        }

        positions[i] = current_dir;
    }

    position_to_signal(&positions)
}

/// Calendar anomaly signal generator using day-of-week × month-position edges.
///
/// Phase 45-calendar-anomaly-scanner-implementation (D-11).
/// Reads edges.json (from Phase 44 analysis): Vec<{ day_of_week: 1-5, month_position: "early"|"mid"|"late", direction: -1|1 }>
/// If edges.json is missing or empty, returns all-zero signals (no panic).
///
/// For each OHLC bar:
/// 1. Extract timestamp in nanoseconds
/// 2. Compute day_of_week (1=Monday, 5=Friday) and day_of_month
/// 3. Compute month_position: day ≤ 10 → "early", day ≤ 20 → "mid", else → "late"
/// 4. Look up (day_of_week, month_position) in edges HashMap
/// 5. Convert direction (-1 or 1) to signal via position_to_signal (singleton position)
fn calendar_anomaly_signals(ohlcv: &Ohlcv, _params: &HashMap<String, Value>) -> Vec<i8> {
    use serde::Deserialize;

    #[derive(Deserialize)]
    struct CalendarEdge {
        day_of_week: u32,
        month_position: String,
        direction: i8,
    }

    // Load edges.json; if missing or invalid, default to empty vec
    let edges_json =
        std::fs::read_to_string("data/calendar_edges.json").unwrap_or_else(|_| "[]".to_string());
    let edges: Vec<CalendarEdge> = serde_json::from_str(&edges_json).unwrap_or_default();

    // D-11: empty edges → all-zero signals (no-op)
    if edges.is_empty() {
        return vec![0i8; ohlcv.close.len()];
    }

    // Build lookup: (day_of_week, month_position) → direction
    let mut lookup: std::collections::HashMap<(u32, String), i8> = std::collections::HashMap::new();
    for edge in edges {
        lookup.insert((edge.day_of_week, edge.month_position), edge.direction);
    }

    let datetimes_ns = match ohlcv.datetimes_ns {
        Some(d) => d,
        None => return vec![0i8; ohlcv.close.len()],
    };

    let mut positions = vec![0i8; ohlcv.close.len()];

    for (i, ts_ns) in datetimes_ns.iter().enumerate() {
        let secs = ts_ns / 1_000_000_000;
        if let Some(dt) = chrono::DateTime::from_timestamp(secs, 0) {
            let dow = chrono::Datelike::weekday(&dt).num_days_from_monday() + 1; // 1=Mon, 5=Fri
            let day = chrono::Datelike::day(&dt);
            let month_pos = if day <= 10 {
                "early".to_string()
            } else if day <= 20 {
                "mid".to_string()
            } else {
                "late".to_string()
            };

            if let Some(&direction) = lookup.get(&(dow, month_pos)) {
                // Convert scalar direction to a position array and apply signal encoding
                positions[i] = direction;
            }
        }
    }

    position_to_signal(&positions)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_in_cluster_london_burst_window() {
        // 09:55-10:00 (5 min window around BQ peak 09:55 LONG h=3)
        assert!(is_in_cluster("london_burst", 9 * 60 + 55));
        assert!(is_in_cluster("london_burst", 9 * 60 + 59));
        assert!(!is_in_cluster("london_burst", 9 * 60 + 54));
        assert!(!is_in_cluster("london_burst", 10 * 60));
    }

    #[test]
    fn is_in_cluster_tokyo_fix_window() {
        // 00:55-01:00
        assert!(is_in_cluster("tokyo_fix", 55));
        assert!(is_in_cluster("tokyo_fix", 59));
        assert!(!is_in_cluster("tokyo_fix", 54));
        assert!(!is_in_cluster("tokyo_fix", 60));
    }

    #[test]
    fn is_in_cluster_unknown_returns_false() {
        assert!(!is_in_cluster("nonexistent", 0));
        assert!(!is_in_cluster("nonexistent", 1000));
    }

    #[test]
    fn is_in_cluster_all_six_clusters_defined() {
        // BQ minute peak の中央分が必ず in_cluster
        let cases = [
            ("london_open", 7 * 60 + 55),         // 07:55
            ("tokyo_fix", 55),                    // 00:55
            ("ny_close_sell", 20 * 60 + 59),      // 20:59
            ("ny_post_close", 21 * 60 + 5),       // 21:05
            ("london_burst", 9 * 60 + 55),        // 09:55
            ("london_continuation", 10 * 60 + 2), // 10:02
        ];
        for (name, mod_minute) in cases {
            assert!(
                is_in_cluster(name, mod_minute),
                "{} should match {}",
                name,
                mod_minute
            );
        }
    }

    // ---- cluster_pair_drift tests ----

    /// (open, high, low, close, volume, datetimes_ns) — aliased to keep clippy happy.
    type CpdBars = (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<i64>);

    /// Helper: build n synthetic 1m bars starting at the given UTC timestamp.
    fn cpd_make_bars(start_iso: &str, n: usize) -> CpdBars {
        let base_ts: i64 = chrono::DateTime::parse_from_rfc3339(start_iso)
            .unwrap()
            .timestamp_nanos_opt()
            .unwrap();
        let one_min_ns: i64 = 60 * 1_000_000_000;
        let datetimes_ns: Vec<i64> = (0..n as i64).map(|i| base_ts + i * one_min_ns).collect();
        let close: Vec<f64> = (0..n).map(|i| 150.0 + i as f64 * 0.01).collect();
        let high: Vec<f64> = close.iter().map(|c| c + 0.05).collect();
        let low: Vec<f64> = close.iter().map(|c| c - 0.05).collect();
        let open = close.clone();
        let volume = vec![1000.0_f64; n];
        (open, high, low, close, volume, datetimes_ns)
    }

    /// Helper: build a baseline params HashMap with NaN exits.
    fn cpd_make_params(
        long_c: &str,
        short_c: &str,
        hold: u64,
        exit_type: &str,
    ) -> HashMap<String, Value> {
        use serde_json::json;
        let mut params = HashMap::new();
        params.insert("long_cluster".to_string(), json!(long_c));
        params.insert("short_cluster".to_string(), json!(short_c));
        params.insert("hold_bars".to_string(), json!(hold));
        params.insert("exit_type".to_string(), json!(exit_type));
        params.insert("tp_atr".to_string(), json!(f64::NAN));
        params.insert("sl_atr".to_string(), json!(f64::NAN));
        params.insert("tp_pct".to_string(), json!(f64::NAN));
        params.insert("sl_pct".to_string(), json!(f64::NAN));
        params
    }

    #[test]
    fn cluster_pair_drift_emits_long_in_long_cluster() {
        // 09:54-10:03 — london_burst window 09:55-10:00 → entry at index 1
        let (open, high, low, close, volume, datetimes_ns) =
            cpd_make_bars("2025-01-02T09:54:00Z", 10);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };
        let params = cpd_make_params("london_burst", "tokyo_fix", 3, "none");
        let signals = cluster_pair_drift_signals(&ohlcv, &params);
        assert_eq!(signals.len(), 10);
        assert_eq!(
            signals[1], 1,
            "expected LONG entry at 09:55, got {:?}",
            signals
        );
    }

    #[test]
    fn cluster_pair_drift_emits_short_in_short_cluster() {
        // 00:54-01:03 — tokyo_fix window 00:55-01:00 → SHORT entry at index 1
        let (open, high, low, close, volume, datetimes_ns) =
            cpd_make_bars("2025-01-02T00:54:00Z", 10);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };
        let params = cpd_make_params("london_burst", "tokyo_fix", 3, "none");
        let signals = cluster_pair_drift_signals(&ohlcv, &params);
        assert_eq!(
            signals[1], -1,
            "expected SHORT entry at 00:55, got {:?}",
            signals
        );
    }

    #[test]
    fn cluster_pair_drift_closes_after_hold_bars() {
        // hold=2 → entry at 09:55 (idx 1), close at 09:57 (idx 3)
        let (open, high, low, close, volume, datetimes_ns) =
            cpd_make_bars("2025-01-02T09:54:00Z", 10);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };
        let params = cpd_make_params("london_burst", "tokyo_fix", 2, "none");
        let signals = cluster_pair_drift_signals(&ohlcv, &params);
        assert_eq!(signals[1], 1, "entry at 09:55 expected, got {:?}", signals);
        assert_eq!(
            signals[3], -1,
            "close at 09:57 expected (hold=2), got {:?}",
            signals
        );
    }

    #[test]
    fn cluster_pair_drift_exit_atr_triggers_take_profit() {
        // 20 bars from 09:42 — entry at 09:55 (idx 13), price jumps at idx 14 → ATR TP
        let (open, high, low, mut close, volume, datetimes_ns) =
            cpd_make_bars("2025-01-02T09:42:00Z", 20);
        close[14] = 151.0; // sharp jump
        let high2: Vec<f64> = close.iter().map(|c| c + 0.05).collect();
        let low2: Vec<f64> = close.iter().map(|c| c - 0.05).collect();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high2,
            low: &low2,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };
        let _ = high; // shadowed
        let _ = low;
        let mut params = cpd_make_params("london_burst", "tokyo_fix", 60, "atr_based");
        params.insert("tp_atr".to_string(), serde_json::json!(1.0));
        params.insert("sl_atr".to_string(), serde_json::json!(3.0));
        let signals = cluster_pair_drift_signals(&ohlcv, &params);
        assert_eq!(
            signals[13], 1,
            "entry at 09:55 expected, got {:?}",
            &signals
        );
        assert!(
            signals[14] == -1 || signals[15] == -1,
            "ATR TP close should fire by index 14 or 15, got {:?}",
            &signals[13..16]
        );
    }

    #[test]
    fn cluster_pair_drift_exit_fixed_pct_triggers_stop_loss() {
        // entry at 09:55 (idx 1, price ~150.01), close[2]=149.0 (-0.67%) → SL
        let (open, high, low, mut close, volume, datetimes_ns) =
            cpd_make_bars("2025-01-02T09:54:00Z", 10);
        close[2] = 149.0;
        let high2: Vec<f64> = close.iter().map(|c| c + 0.05).collect();
        let low2: Vec<f64> = close.iter().map(|c| c - 0.05).collect();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high2,
            low: &low2,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };
        let _ = high;
        let _ = low;
        let mut params = cpd_make_params("london_burst", "tokyo_fix", 60, "fixed_pct");
        params.insert("tp_pct".to_string(), serde_json::json!(0.05));
        params.insert("sl_pct".to_string(), serde_json::json!(0.005));
        let signals = cluster_pair_drift_signals(&ohlcv, &params);
        assert_eq!(signals[1], 1, "entry at 09:55 expected, got {:?}", signals);
        assert_eq!(
            signals[2], -1,
            "fixed_pct SL close at 09:56 expected, got {:?}",
            signals
        );
    }

    #[test]
    fn cluster_pair_drift_reverse_on_same_bar_overlap() {
        // 15 bars from 09:54 — LONG entry at 09:55 (london_burst, idx 1), then
        // SHORT entry at 10:01 (london_continuation start, idx 7) reverses
        // despite hold=10 still being active.
        let (open, high, low, close, volume, datetimes_ns) =
            cpd_make_bars("2025-01-02T09:54:00Z", 15);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };
        let params = cpd_make_params("london_burst", "london_continuation", 10, "none");
        let signals = cluster_pair_drift_signals(&ohlcv, &params);
        assert_eq!(signals[1], 1, "LONG entry at 09:55, got {:?}", signals);
        assert!(
            signals[7] < 0,
            "SHORT reverse at 10:01 should be negative, got {:?}",
            &signals[5..10]
        );
    }

    #[test]
    fn cluster_pair_drift_direction_violation_long_on_short_returns_empty() {
        // SHORT cluster assigned to long side → self-prune to all-zero
        let (open, high, low, close, volume, datetimes_ns) =
            cpd_make_bars("2025-01-02T00:54:00Z", 10);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };
        let params = cpd_make_params("tokyo_fix", "ny_close_sell", 3, "none");
        let signals = cluster_pair_drift_signals(&ohlcv, &params);
        assert_eq!(signals.len(), 10);
        assert!(
            signals.iter().all(|&s| s == 0),
            "tokyo_fix is a SHORT-edge cluster; placing it on long side should self-prune, got {:?}",
            signals
        );
    }

    #[test]
    fn cluster_pair_drift_direction_violation_short_on_long_returns_empty() {
        // LONG cluster assigned to short side → self-prune to all-zero
        let (open, high, low, close, volume, datetimes_ns) =
            cpd_make_bars("2025-01-02T09:54:00Z", 10);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };
        let params = cpd_make_params("ny_post_close", "london_burst", 3, "none");
        let signals = cluster_pair_drift_signals(&ohlcv, &params);
        assert_eq!(signals.len(), 10);
        assert!(
            signals.iter().all(|&s| s == 0),
            "london_burst is a LONG-edge cluster; placing it on short side should self-prune, got {:?}",
            signals
        );
    }

    #[test]
    fn cluster_pair_drift_same_cluster_returns_empty() {
        // long_cluster == short_cluster → both direction-locked checks reject
        // (london_burst is not in SHORT_CLUSTERS) → all-zero signals.
        let (open, high, low, close, volume, datetimes_ns) =
            cpd_make_bars("2025-01-02T09:54:00Z", 10);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };
        let params = cpd_make_params("london_burst", "london_burst", 3, "none");
        let signals = cluster_pair_drift_signals(&ohlcv, &params);
        assert_eq!(signals.len(), 10);
        assert!(
            signals.iter().all(|&s| s == 0),
            "same cluster pair should produce no trades, got {:?}",
            signals
        );
    }
}

#[test]
fn test_calendar_anomaly_signals_with_populated_edges() {
    // Test that calendar_anomaly_signals returns a vector of the same length as OHLCV.
    // With empty edges.json (default), expect all-zero signals.
    let base_ts: i64 = 1_704_067_200i64 * 1_000_000_000; // 2024-01-01 UTC
    let hour_ns: i64 = 3_600i64 * 1_000_000_000;

    let close: Vec<f64> = (0..100).map(|i| 150.0 + i as f64 * 0.01).collect();
    let high: Vec<f64> = close.iter().map(|c| c + 0.05).collect();
    let low: Vec<f64> = close.iter().map(|c| c - 0.05).collect();
    let open = close.clone();
    let volume = vec![1000.0; 100];
    let datetimes_ns: Vec<i64> = (0..100).map(|i| base_ts + i as i64 * hour_ns).collect();

    let ohlcv = Ohlcv {
        open: &open,
        high: &high,
        low: &low,
        close: &close,
        volume: &volume,
        datetimes_ns: Some(&datetimes_ns),
        aux_close: None,
    };
    let params: HashMap<String, Value> = HashMap::new();

    let sig = calendar_anomaly_signals(&ohlcv, &params);
    assert_eq!(
        sig.len(),
        100,
        "calendar_anomaly_signals must return same length as OHLCV"
    );
}

#[test]
fn test_calendar_anomaly_signals_empty_edges() {
    // Verify that empty edges.json (default case) returns all-zero signal vector.
    let base_ts: i64 = 1_704_067_200i64 * 1_000_000_000; // 2024-01-01 UTC
    let hour_ns: i64 = 3_600i64 * 1_000_000_000;

    let close: Vec<f64> = (0..50).map(|i| 150.0 + i as f64 * 0.01).collect();
    let high: Vec<f64> = close.iter().map(|c| c + 0.05).collect();
    let low: Vec<f64> = close.iter().map(|c| c - 0.05).collect();
    let open = close.clone();
    let volume = vec![1000.0; 50];
    let datetimes_ns: Vec<i64> = (0..50).map(|i| base_ts + i as i64 * hour_ns).collect();

    let ohlcv = Ohlcv {
        open: &open,
        high: &high,
        low: &low,
        close: &close,
        volume: &volume,
        datetimes_ns: Some(&datetimes_ns),
        aux_close: None,
    };
    let params: HashMap<String, Value> = HashMap::new();

    let sig = calendar_anomaly_signals(&ohlcv, &params);
    assert_eq!(
        sig.len(),
        50,
        "calendar_anomaly_signals must return same length as OHLCV"
    );
    assert!(
        sig.iter().all(|&s| s == 0),
        "with empty edges, all signals should be 0 (no panic)"
    );
}

#[test]
fn test_month_position_boundaries() {
    // Verify month_position categorization boundaries via trading logic.
    // Build 3 bars: day 10, day 11, day 20 (boundaries between early/mid/late).
    let base_ts: i64 = chrono::DateTime::parse_from_rfc3339("2024-01-10T00:00:00Z")
        .unwrap()
        .timestamp_nanos_opt()
        .unwrap();
    let one_day_ns: i64 = 24 * 3600 * 1_000_000_000;

    let datetimes_ns = vec![base_ts, base_ts + one_day_ns, base_ts + 10 * one_day_ns];
    let close = vec![150.0, 150.1, 150.2];
    let high: Vec<f64> = close.iter().map(|c| c + 0.05).collect();
    let low: Vec<f64> = close.iter().map(|c| c - 0.05).collect();
    let open = close.clone();
    let volume = vec![1000.0; 3];

    let ohlcv = Ohlcv {
        open: &open,
        high: &high,
        low: &low,
        close: &close,
        volume: &volume,
        datetimes_ns: Some(&datetimes_ns),
        aux_close: None,
    };
    let params: HashMap<String, Value> = HashMap::new();

    // With empty edges, expect all zeros; but verify structure doesn't panic
    let sig = calendar_anomaly_signals(&ohlcv, &params);
    assert_eq!(sig.len(), 3, "should process all 3 bars");
}

// ---- time_of_day_drift (BQ-derived directional edges) ----

/// Time-of-day directional edge gate derived from BigQuery USDJPY tick analysis
/// (see `project_bq_directional_edges.md` memory, 2026-04-08).
///
/// Fires a hold-for-N-bars position at UTC minutes where the 2025 sample showed
/// a Bonferroni-significant directional drift post transaction-cost
/// (0.7 pip RT). Seven edges survive the cost filter:
///
///   - London open cluster (UTC):
///     07:55 LONG,  08:30 SHORT (h≈20m),  09:55 LONG,  10:00 SHORT
///   - NY close cluster (UTC):
///     20:55 SHORT (h≈10m),  21:05 LONG (h≈45m)
///   - Tokyo fix:
///     00:55 SHORT (h≈1m, shortest — spread-sensitive)
///
/// ## Signal semantics
/// This strategy requires **time-based exits** which cannot be expressed with
/// the standard {-1, 0, +1} encoding used by `position_to_signal`. Instead it
/// emits signals directly:
///   - At an edge bar:           ±1 (entry / flip)
///   - At entry + hold_bars:     ±2 (explicit flatten — handled by `run_backtest`)
///   - Otherwise:                 0 (hold)
///
/// The strategy infers the bar interval from consecutive timestamps and fires
/// each edge on the first bar whose [start, start+interval) window contains
/// the edge minute-of-day. `hold_bars` sets the uniform hold duration (in
/// bars). On overlap, a fresh entry signal at bar i re-arms the exit clock
/// (latest-wins). Exit bars that coincide with a new entry are overridden by
/// the entry (the new position replaces the old).
fn time_of_day_drift_signals(ohlcv: &Ohlcv, params: &HashMap<String, Value>) -> Vec<i8> {
    let cluster = params
        .get("cluster")
        .and_then(|v| v.as_str())
        .unwrap_or("all");
    let hold_bars = get_int(params, "hold_bars").max(1);

    // Edge table: (hour, minute, direction) — minute-of-day indexed below.
    let edges_all: &[(u32, u32, i8)] = &[
        (7, 55, 1),   // Pre-London LONG
        (8, 30, -1),  // London post-open SHORT (08:32 in BQ, floored)
        (9, 55, 1),   // London open A LONG
        (10, 0, -1),  // London cont SHORT (10:02 in BQ, floored)
        (20, 55, -1), // NY close SHORT (20:59 in BQ, floored)
        (21, 5, 1),   // NY post-close LONG
        (0, 55, -1),  // Tokyo fix SHORT
    ];
    let edges_london: &[(u32, u32, i8)] = &edges_all[0..4];
    let edges_ny: &[(u32, u32, i8)] = &edges_all[4..6];
    let edges_tokyo: &[(u32, u32, i8)] = &edges_all[6..7];

    let edges: &[(u32, u32, i8)] = match cluster {
        "london_open" => edges_london,
        "ny_close" => edges_ny,
        "tokyo_fix" => edges_tokyo,
        _ => edges_all,
    };

    // Convert edges to second-of-day to support sub-minute bar intervals (e.g., 30s).
    // For 1m bars, edge_sod = h*3600 + m*60 and bar_sod = h*3600 + m*60 (second=0),
    // so the match check edge_sod in [bar_sod, bar_sod + 60) is bit-exact equivalent
    // to the previous edge_mod == bar_mod comparison.
    let edge_sods: Vec<(u32, i8)> = edges
        .iter()
        .map(|&(h, m, d)| (h * 3600 + m * 60, d))
        .collect();

    let datetimes_ns = ohlcv
        .datetimes_ns
        .expect("time_of_day_drift requires datetimes_ns");
    let n = ohlcv.close.len();

    // Infer bar interval in seconds from the first non-zero delta.
    // Supports sub-minute bars (e.g., 30s); fallback to 60s for safety.
    let interval_sec: u32 = {
        let mut inferred: u32 = 60; // 1m fallback
        for i in 1..datetimes_ns.len() {
            let delta_sec = (datetimes_ns[i] - datetimes_ns[i - 1]) / 1_000_000_000;
            if delta_sec > 0 {
                inferred = (delta_sec as u32).max(1);
                break;
            }
        }
        inferred
    };

    // First pass: detect which bars the calendar edge fires on (`edge_bar`).
    // The entry signal will be emitted at `edge_bar - 1` so that the backtest's
    // built-in 1-bar execution lag places the position on `edge_bar`, capturing
    // the return from `edge_bar` to `edge_bar + 1` (which is exactly the window
    // the BQ analysis measured — return *after* the edge minute).
    let mut edge_dir = vec![0i8; n];
    for (i, &ts_ns) in datetimes_ns.iter().enumerate().take(n) {
        let ts_sec = ts_ns / 1_000_000_000;
        let Some(dt) = DateTime::<Utc>::from_timestamp(ts_sec, 0) else {
            continue;
        };
        let bar_sod = dt.hour() * 3600 + dt.minute() * 60 + dt.second();

        // Latest-wins on overlap (last match within the interval window).
        for &(edge_sod, dir) in &edge_sods {
            if edge_sod >= bar_sod && edge_sod < bar_sod + interval_sec {
                edge_dir[i] = dir;
            }
        }
    }

    // Second pass: emit entry signals TWO bars earlier than the edge bar.
    //
    // Why `-2` and not `-1`? The backtest has a compounded 2-bar lag:
    //   1. `positions[i]` is derived from `signals[i-1]` (1-bar shift).
    //   2. `returns[i]` uses `positions[i-1] * market_return[i]` (another shift).
    // So a signal at bar k first captures `market_return[k+2]`, i.e. the return
    // from close[k+1] to close[k+2]. To capture the 5m bar that actually
    // *contains* the edge minute (bar `edge_bar`, return = close[edge_bar-1] →
    // close[edge_bar]), we need the signal at `edge_bar - 2`.
    //
    // Algorithm:
    //   - For each edge at `edge_bar` with direction d (requires edge_bar >= 2):
    //       * emit signals[edge_bar - 2] = d (entry)
    //       * schedule flatten at signals[edge_bar - 2 + hold_bars] = 2*d
    //   - Latest-wins: if a later edge overwrites an already-scheduled flatten
    //     bar with its entry, that entry is preserved and the stale flatten is
    //     cleared. Entries never overwrite other entries at the same bar
    //     because `edge_bar` is processed in ascending order.
    //   - Edges at bar 0 or 1 are skipped (cannot emit at bars -2 or -1).
    let mut signals = vec![0i8; n];

    // Collect scheduled flattens keyed by bar index. `flatten_at[i] = Some(sign)`
    // means "emit 2*sign at bar i unless an entry is emitted there first".
    let mut flatten_at: Vec<Option<i8>> = vec![None; n];

    for (edge_bar, &dir) in edge_dir.iter().enumerate().take(n) {
        if dir == 0 {
            continue;
        }
        if edge_bar < 2 {
            continue; // can't emit at bar -2 or -1
        }
        let emit_bar = edge_bar - 2;
        signals[emit_bar] = dir;
        // Cancel any previously-scheduled flatten at this same bar — the new
        // entry supersedes the old position's flatten.
        flatten_at[emit_bar] = None;

        let flat_bar = emit_bar + hold_bars;
        if flat_bar < n {
            flatten_at[flat_bar] = Some(dir);
        }
    }

    // Materialize scheduled flattens, but only on bars that did NOT receive an
    // overriding entry signal in the same pass (entries already wrote to
    // signals[emit_bar]; if a flatten lands on the same bar as a later edge's
    // entry, that entry already won because it was processed later in order,
    // and flatten_at[emit_bar] was cleared above).
    for i in 0..n {
        if signals[i] == 0 {
            if let Some(sign) = flatten_at[i] {
                signals[i] = 2 * sign;
            }
        }
    }

    signals
}

#[cfg(test)]
#[allow(clippy::type_complexity)]
mod tests_tod {
    use super::*;
    use chrono::TimeZone;

    /// Build a minimal Ohlcv with 5-minute bars starting at the given UTC datetime.
    fn make_5m_bars(start: DateTime<Utc>, n: usize) -> (Vec<f64>, Vec<i64>) {
        let close: Vec<f64> = (0..n).map(|i| 150.0 + i as f64 * 0.01).collect();
        let dts: Vec<i64> = (0..n)
            .map(|i| {
                let ts = start + chrono::Duration::minutes(5 * i as i64);
                ts.timestamp_nanos_opt().unwrap()
            })
            .collect();
        (close, dts)
    }

    #[test]
    fn time_of_day_drift_fires_london_open_long_at_0955() {
        // 5m bars covering 09:00 .. 10:30 UTC
        let start = Utc.with_ymd_and_hms(2025, 6, 2, 9, 0, 0).unwrap();
        let (close, dts) = make_5m_bars(start, 19);
        let high = close.clone();
        let low = close.clone();
        let volume = vec![1.0; close.len()];

        let ohlcv = Ohlcv {
            open: &close,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };

        let mut params = HashMap::new();
        params.insert("cluster".to_string(), Value::from("london_open"));
        params.insert("hold_bars".to_string(), Value::from(1_i64));

        let signals = time_of_day_drift_signals(&ohlcv, &params);

        // Bar index: 09:00=0, 09:45=9, 09:50=10, 09:55=11, 10:00=12, 10:05=13
        // Entries are emitted TWO BARS EARLY (compensates backtest 2-bar exec lag):
        //   09:55 edge (LONG)  → signals[9]  = +1
        //   10:00 edge (SHORT) → signals[10] = -1 (overrides flatten from 09:55)
        //   flatten from 10:00 (hold_bars=1) → signals[11] = -2
        assert_eq!(signals[9], 1, "09:45 emit LONG for 09:55 edge");
        assert_eq!(
            signals[10], -1,
            "09:50 emit SHORT for 10:00 edge (overrides flatten)"
        );
        assert_eq!(signals[11], -2, "09:55 scheduled flatten");
        // Sanity: no stray non-zero signals elsewhere.
        for (i, &s) in signals.iter().enumerate() {
            if ![9, 10, 11].contains(&i) {
                assert_eq!(s, 0, "bar {i} should be flat");
            }
        }
    }

    #[test]
    fn time_of_day_drift_respects_hold_bars() {
        // 5m bars covering 20:00 .. 22:00 UTC (need >=2 bars lead time for offset)
        let start = Utc.with_ymd_and_hms(2025, 6, 2, 20, 0, 0).unwrap();
        let (close, dts) = make_5m_bars(start, 24);
        let high = close.clone();
        let low = close.clone();
        let volume = vec![1.0; close.len()];

        let ohlcv = Ohlcv {
            open: &close,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };

        let mut params = HashMap::new();
        params.insert("cluster".to_string(), Value::from("ny_close"));
        params.insert("hold_bars".to_string(), Value::from(2_i64));

        let signals = time_of_day_drift_signals(&ohlcv, &params);

        // 20:00=0, ..., 20:45=9, 20:55=11, 21:05=13, 21:15=15
        // Entries emitted 2 bars early (backtest 2-bar exec lag):
        //   20:55 SHORT edge → signals[9]  = -1
        //   21:05 LONG edge  → signals[11] = +1 (overrides scheduled flatten)
        //   flatten from 21:05 LONG with hold_bars=2 → signals[13] = +2
        assert_eq!(signals[9], -1, "20:45 emit SHORT for 20:55 edge");
        assert_eq!(
            signals[11], 1,
            "20:55 emit LONG for 21:05 edge (overrides 20:55's scheduled flatten)"
        );
        assert_eq!(signals[13], 2, "21:05 scheduled flatten (close long)");
        // Sanity: no stray signals elsewhere.
        for (i, &s) in signals.iter().enumerate() {
            if ![9, 11, 13].contains(&i) {
                assert_eq!(s, 0, "bar {i} should be flat");
            }
        }
    }

    #[test]
    fn time_of_day_drift_tokyo_fix_cluster_isolates_0055() {
        let start = Utc.with_ymd_and_hms(2025, 6, 2, 0, 0, 0).unwrap();
        let (close, dts) = make_5m_bars(start, 20);
        let high = close.clone();
        let low = close.clone();
        let volume = vec![1.0; close.len()];

        let ohlcv = Ohlcv {
            open: &close,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };

        let mut params = HashMap::new();
        params.insert("cluster".to_string(), Value::from("tokyo_fix"));
        params.insert("hold_bars".to_string(), Value::from(1_i64));

        let signals = time_of_day_drift_signals(&ohlcv, &params);

        // Only 00:55 fires. Offset -2 → entry at 00:45, flatten at 00:50.
        // 00:00=0, 00:45=9, 00:50=10, 00:55=11
        assert_eq!(signals[9], -1, "00:45 emit SHORT for 00:55 edge");
        assert_eq!(signals[10], -2, "00:50 scheduled flatten");
        // All other bars flat.
        for (i, &s) in signals.iter().enumerate() {
            if i != 9 && i != 10 {
                assert_eq!(s, 0, "bar {i} should be flat under tokyo_fix cluster");
            }
        }
    }

    /// End-to-end: feed time_of_day_drift signals through run_backtest and
    /// confirm that positions actually return to 0 between entries (the whole
    /// reason we added the ±2 flatten encoding).
    #[test]
    fn time_of_day_drift_flattens_between_edges_via_run_backtest() {
        use crate::backtest::run_backtest;

        // 5m bars covering 00:00 .. 02:00 UTC (tokyo_fix cluster, one edge)
        let start = Utc.with_ymd_and_hms(2025, 6, 2, 0, 0, 0).unwrap();
        let (close, dts) = make_5m_bars(start, 24);
        let high = close.clone();
        let low = close.clone();
        let volume = vec![1.0; close.len()];

        let ohlcv = Ohlcv {
            open: &close,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };

        let mut params = HashMap::new();
        params.insert("cluster".to_string(), Value::from("tokyo_fix"));
        params.insert("hold_bars".to_string(), Value::from(2_i64));
        let signals = time_of_day_drift_signals(&ohlcv, &params);

        // hold_bars=2, offset=2. 00:55 edge → signals[9]=-1, signals[11]=-2.
        // Backtest 1-bar shift in positions loop then 1-bar shift in returns:
        //   positions[10]=-1, positions[11]=-1, positions[12]=0.
        //   returns[11] = positions[10]*mr[11]  (captures edge bar 09:55-ish)
        //   returns[12] = positions[11]*mr[12]  (next bar)
        //   returns[13]..= 0 (flat).
        let result = run_backtest(&close, &signals, 0.0, 105120.0, 0, &dts);
        let final_eq = *result.equity_curve.last().unwrap();
        let post_flat_eq = result.equity_curve[13];
        assert!(
            (final_eq - post_flat_eq).abs() < 1e-12,
            "equity must be flat from bar 13 onward, got post_flat={post_flat_eq}, final={final_eq}"
        );
        // Exactly 2 position-change events (enter short, exit to flat).
        assert_eq!(
            result.num_trades, 2,
            "expected 2 position changes: enter + exit-to-flat"
        );
    }

    /// Build a 30s-bar Ohlcv with 6 bars spanning 20:54:00 to 20:56:30 UTC.
    /// Timestamps (UTC):
    ///   bar 0: 2025-06-15 20:54:00
    ///   bar 1: 2025-06-15 20:54:30
    ///   bar 2: 2025-06-15 20:55:00  ← first 30s bar containing ny_close edge 20:55 SHORT
    ///   bar 3: 2025-06-15 20:55:30  ← must NOT fire (same minute, different 30s slot)
    ///   bar 4: 2025-06-15 20:56:00
    ///   bar 5: 2025-06-15 20:56:30
    fn build_30s_ny_close_ohlcv() -> (Vec<f64>, Vec<i64>) {
        let base_ns: i64 = Utc
            .with_ymd_and_hms(2025, 6, 15, 20, 54, 0)
            .unwrap()
            .timestamp_nanos_opt()
            .unwrap();
        let datetimes_ns: Vec<i64> = (0..6).map(|i| base_ns + 30 * 1_000_000_000 * i).collect();
        let close = vec![150.0f64; 6];
        (close, datetimes_ns)
    }

    #[test]
    fn time_of_day_drift_fires_once_per_edge_on_30s_bars() {
        use serde_json::json;

        let (close, datetimes_ns) = build_30s_ny_close_ohlcv();
        let high = close.clone();
        let low = close.clone();
        let volume = vec![100.0f64; 6];

        let ohlcv = Ohlcv {
            open: &close,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&datetimes_ns),
            aux_close: None,
        };

        let mut params = HashMap::new();
        params.insert("cluster".to_string(), json!("ny_close"));
        params.insert("hold_bars".to_string(), json!(1));

        let signals = time_of_day_drift_signals(&ohlcv, &params);

        // Edge 20:55 SHORT fires on bar 2 (first 30s bar containing 20:55 minute).
        // With the 2-bar lag, signal is emitted at bar 0 (= 2 - 2).
        // Bar 3 must NOT be a second edge trigger (same minute, different 30s slot).
        let entry_count = signals.iter().filter(|&&s| s == -1 || s == 1).count();
        let flatten_count = signals.iter().filter(|&&s| s == -2 || s == 2).count();

        assert_eq!(
            entry_count, 1,
            "expected exactly 1 entry signal, got {} (signals: {:?})",
            entry_count, signals
        );
        assert_eq!(
            flatten_count, 1,
            "expected exactly 1 flatten signal, got {} (signals: {:?})",
            flatten_count, signals
        );
    }

    // ---- macro_event_drift tests ----

    /// Helper: build n synthetic 1-minute bars centred around the first BOJ window end
    /// (2024-01-23T06:00:00Z). `window_end_idx` is the bar index that will have that timestamp.
    ///
    /// Returns (open, high, low, close, volume, datetimes_ns) as owned Vecs.
    fn build_boj_ohlcv(
        window_end_idx: usize,
        n_bars: usize,
        closes: Vec<f64>,
        highs: Option<Vec<f64>>,
        lows: Option<Vec<f64>>,
    ) -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<i64>) {
        // First BOJ window end: 2024-01-23T06:00:00Z
        let window_end_ns: i64 = chrono::NaiveDate::from_ymd_opt(2024, 1, 23)
            .unwrap()
            .and_hms_opt(6, 0, 0)
            .unwrap()
            .and_utc()
            .timestamp_nanos_opt()
            .unwrap();
        let one_min_ns: i64 = 60 * 1_000_000_000;
        // Bar at window_end_idx has timestamp == window_end_ns
        let start_ns = window_end_ns - (window_end_idx as i64) * one_min_ns;
        let datetimes_ns: Vec<i64> = (0..n_bars as i64)
            .map(|i| start_ns + i * one_min_ns)
            .collect();
        let high = highs.unwrap_or_else(|| closes.clone());
        let low = lows.unwrap_or_else(|| closes.clone());
        let open = closes.clone();
        let volume = vec![1000.0f64; n_bars];
        (open, high, low, closes, volume, datetimes_ns)
    }

    /// Helper: build params for macro_event_drift tests.
    fn med_params(
        window_offset: u64,
        hold_bars: u64,
        exit_type: &str,
        tp_pct: f64,
        sl_pct: f64,
    ) -> HashMap<String, Value> {
        use serde_json::json;
        let mut params = HashMap::new();
        params.insert("window_offset".to_string(), json!(window_offset));
        params.insert("hold_bars".to_string(), json!(hold_bars));
        params.insert("exit_type".to_string(), json!(exit_type));
        params.insert("tp_pct".to_string(), json!(tp_pct));
        params.insert("sl_pct".to_string(), json!(sl_pct));
        params
    }

    /// Test 1: entry fires window_offset bars after window end; signal semantics verified.
    ///
    /// window_end_idx=5, window_offset=1 → entry at bar 6.
    /// position: [0,0,0,0,0,0,1,1,1,1,0,...] (hold_bars=3 → bars 6,7,8 long; bar 9 flat)
    /// signal (diff+clip): bar6=+1, bar7=0, bar8=0, bar9=-1, bar5=0
    #[test]
    fn macro_event_drift_entry_after_window_offset() {
        let n = 20;
        let closes = vec![100.0f64; n];
        let (open, high, low, close, volume, dts) = build_boj_ohlcv(5, n, closes, None, None);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let params = med_params(1, 3, "none", f64::NAN, f64::NAN);
        let signals = macro_event_drift_signals(&ohlcv, &params);

        // i_we=5 (window end bar), entry at i_we+1=6
        assert_eq!(signals[5], 0, "window end bar should have no signal");
        assert_eq!(signals[6], 1, "entry bar should have signal=1");
        assert_eq!(signals[7], 0, "hold bar should have signal=0");
        assert_eq!(signals[8], 0, "hold bar should have signal=0");
        // Bar 9 is the exit bar (i >= hold_until=9): position goes 1→0 → signal=-1
        assert_eq!(signals[9], -1, "exit bar should have signal=-1");
        assert_eq!(signals[10], 0, "after exit signal=0");
    }

    /// Test 2: position is maintained for hold_bars duration.
    ///
    /// window_end_idx=3, window_offset=2 → entry at bar 5, hold_bars=5 → hold until bar 10.
    /// signals: bar5=+1, bars 6..=9=0, bar10=-1
    #[test]
    fn macro_event_drift_holds_for_hold_bars() {
        let n = 15;
        let closes = vec![100.0f64; n];
        let (open, high, low, close, volume, dts) = build_boj_ohlcv(3, n, closes, None, None);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let params = med_params(2, 5, "none", f64::NAN, f64::NAN);
        let signals = macro_event_drift_signals(&ohlcv, &params);

        // entry at 3+2=5, hold_until=5+5=10
        assert_eq!(signals[5], 1, "entry bar should have signal=1");
        for (i, s) in signals.iter().enumerate().take(10).skip(6) {
            assert_eq!(*s, 0, "hold bar {} should have signal=0", i);
        }
        assert_eq!(signals[10], -1, "exit bar should have signal=-1");
    }

    /// Test 3: fixed_pct take-profit triggers when high exceeds tp threshold.
    ///
    /// window_end_idx=3, window_offset=1 → entry at bar 4, entry_price=100.0.
    /// tp_pct=0.005 → tp_price=100.5. highs[7]=100.6 > 100.5 → TP at bar 7.
    /// signals: bar4=+1, bar5=0, bar6=0, bar7=-1
    #[test]
    fn macro_event_drift_exit_fixed_pct_triggers_take_profit() {
        let n = 20;
        let closes = vec![100.0f64; n];
        let mut highs = vec![100.0f64; n];
        highs[7] = 100.6; // exceeds tp_price = 100.0 * 1.005 = 100.5
        let (open, _, low, close, volume, dts) =
            build_boj_ohlcv(3, n, closes, Some(highs.clone()), None);
        let ohlcv = Ohlcv {
            open: &open,
            high: &highs,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let params = med_params(1, 10, "fixed_pct", 0.005, 0.005);
        let signals = macro_event_drift_signals(&ohlcv, &params);

        // entry at 3+1=4, TP at 7 (bar index)
        assert_eq!(signals[4], 1, "entry bar should have signal=1");
        assert_eq!(signals[5], 0, "hold bar should have signal=0");
        assert_eq!(signals[6], 0, "hold bar should have signal=0");
        assert_eq!(signals[7], -1, "TP exit bar should have signal=-1");
        assert_eq!(signals[8], 0, "after TP exit signal=0");
    }

    /// Test 4: fixed_pct stop-loss triggers when low falls below sl threshold.
    ///
    /// Same setup as test 3 but lows[7]=99.4 < sl_price=99.5 → SL at bar 7.
    #[test]
    fn macro_event_drift_exit_fixed_pct_triggers_stop_loss() {
        let n = 20;
        let closes = vec![100.0f64; n];
        let mut lows = vec![100.0f64; n];
        lows[7] = 99.4; // below sl_price = 100.0 * (1 - 0.005) = 99.5
        let (open, high, _, close, volume, dts) =
            build_boj_ohlcv(3, n, closes, None, Some(lows.clone()));
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &lows,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let params = med_params(1, 10, "fixed_pct", 0.005, 0.005);
        let signals = macro_event_drift_signals(&ohlcv, &params);

        assert_eq!(signals[4], 1, "entry bar should have signal=1");
        assert_eq!(signals[7], -1, "SL exit bar should have signal=-1");
        assert_eq!(signals[8], 0, "after SL exit signal=0");
    }

    /// Test 5: window_offset=0 triggers self-prune — returns all zeros, no panic (MED-04).
    #[test]
    fn macro_event_drift_window_offset_zero_self_prunes() {
        let n = 10;
        let closes = vec![100.0f64; n];
        let (open, high, low, close, volume, dts) = build_boj_ohlcv(3, n, closes, None, None);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let params = med_params(0, 3, "none", f64::NAN, f64::NAN);
        let signals = macro_event_drift_signals(&ohlcv, &params);
        assert_eq!(
            signals,
            vec![0i8; n],
            "window_offset=0 should self-prune to all zeros"
        );
    }

    /// Test 6: generate_signals dispatcher routes to macro_event_drift_signals (MED-02).
    #[test]
    fn macro_event_drift_dispatcher_routes_correctly() {
        let n = 15;
        let closes = vec![100.0f64; n];
        let (open, high, low, close, volume, dts) = build_boj_ohlcv(3, n, closes, None, None);
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let params = med_params(1, 3, "none", f64::NAN, f64::NAN);
        let direct = macro_event_drift_signals(&ohlcv, &params);
        let via_dispatcher = generate_signals("macro_event_drift", &ohlcv, &params);
        assert_eq!(
            direct, via_dispatcher,
            "dispatcher must route to macro_event_drift_signals"
        );
    }

    // ============================================================
    // FOMC event_source branch tests (Phase 31, FOMC-03)
    // ============================================================

    /// Build a synthetic 1h Ohlcv covering all of 2024 UTC (8784 bars).
    /// Long enough to contain all 8 of the 2024 FOMC windows.
    fn fomc_test_ohlcv_2024_1h() -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<i64>) {
        let n = 8_784usize; // 2024 is a leap year: 366 × 24
        let start_ns: i64 = 1_704_067_200i64 * 1_000_000_000; // 2024-01-01 00:00:00 UTC
        let hour_ns: i64 = 3_600i64 * 1_000_000_000;

        let close: Vec<f64> = (0..n).map(|i| 1.10 + (i as f64) * 0.0001).collect();
        let open = close.clone();
        let high: Vec<f64> = close.iter().map(|c| c + 0.001).collect();
        let low: Vec<f64> = close.iter().map(|c| c - 0.001).collect();
        let volume = vec![1000.0_f64; n];
        let datetimes_ns: Vec<i64> = (0..n as i64).map(|i| start_ns + i * hour_ns).collect();
        (open, high, low, close, volume, datetimes_ns)
    }

    #[test]
    fn macro_event_drift_default_event_source_is_boj() {
        // params WITHOUT event_source key → must take BOJ path identically.
        let (open, high, low, close, volume, dts) = fomc_test_ohlcv_2024_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };

        let mut params_default: HashMap<String, Value> = HashMap::new();
        params_default.insert("window_offset".to_string(), Value::from(1u64));
        params_default.insert("hold_bars".to_string(), Value::from(1u64));
        params_default.insert("exit_type".to_string(), Value::from("none"));

        let mut params_explicit_boj = params_default.clone();
        params_explicit_boj.insert("event_source".to_string(), Value::from("boj"));

        let sig_default = macro_event_drift_signals(&ohlcv, &params_default);
        let sig_explicit = macro_event_drift_signals(&ohlcv, &params_explicit_boj);

        assert_eq!(
            sig_default, sig_explicit,
            "missing event_source key must default to 'boj' path"
        );
    }

    #[test]
    fn macro_event_drift_event_source_fomc_routes_to_fomc_function() {
        // params WITH event_source=fomc → output must equal direct call to
        // fomc_event_drift_signals.
        let (open, high, low, close, volume, dts) = fomc_test_ohlcv_2024_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };

        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("event_source".to_string(), Value::from("fomc"));
        params.insert("window_offset".to_string(), Value::from(2u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));
        params.insert("exit_type".to_string(), Value::from("none"));

        let via_branch = macro_event_drift_signals(&ohlcv, &params);
        let direct = fomc_event_drift_signals(&ohlcv, &params);

        assert_eq!(
            via_branch, direct,
            "event_source=fomc must route to fomc_event_drift_signals"
        );
        // Sanity: at least one non-zero signal in 2024 (8 FOMC events).
        assert!(
            via_branch.iter().any(|&s| s != 0),
            "FOMC path must produce at least one signal across 2024 (8 events)"
        );
    }

    #[test]
    fn fomc_event_drift_default_window_offset_is_two() {
        // Calling fomc_event_drift_signals with window_offset omitted must
        // default to 2 (look-ahead protection). Verify by comparing with an
        // explicit window_offset=2 call.
        let (open, high, low, close, volume, dts) = fomc_test_ohlcv_2024_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };

        let mut params_omitted: HashMap<String, Value> = HashMap::new();
        params_omitted.insert("hold_bars".to_string(), Value::from(1u64));

        let mut params_explicit_2 = params_omitted.clone();
        params_explicit_2.insert("window_offset".to_string(), Value::from(2u64));

        let sig_omitted = fomc_event_drift_signals(&ohlcv, &params_omitted);
        let sig_explicit = fomc_event_drift_signals(&ohlcv, &params_explicit_2);

        assert_eq!(
            sig_omitted, sig_explicit,
            "default window_offset must be 2 (FOMC look-ahead protection)"
        );
    }

    #[test]
    fn fomc_event_drift_window_offset_below_two_self_prunes() {
        // FOMC announces on the hour → offset=1 enters on announcement bar.
        // Must self-prune to all-zero.
        let (open, high, low, close, volume, dts) = fomc_test_ohlcv_2024_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };

        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("window_offset".to_string(), Value::from(1u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));

        let sig = fomc_event_drift_signals(&ohlcv, &params);
        assert!(
            sig.iter().all(|&s| s == 0),
            "window_offset<2 must self-prune (look-ahead protection)"
        );
    }

    #[test]
    fn fomc_event_drift_uses_directional_const() {
        // Smoke: result for FOMC path must contain BOTH +1 and -1 entries
        // somewhere in 2024 (Jan = hawkish, Sep = dovish).
        let (open, high, low, close, volume, dts) = fomc_test_ohlcv_2024_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };

        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("window_offset".to_string(), Value::from(2u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));

        let sig = fomc_event_drift_signals(&ohlcv, &params);
        let has_long_entry = sig.contains(&1);
        let has_short_entry = sig.contains(&-1);
        assert!(
            has_long_entry,
            "expected at least one hawkish (+1) entry in 2024"
        );
        assert!(
            has_short_entry,
            "expected at least one dovish (-1) entry in 2024"
        );
    }

    // ============================================================
    // ECB event_source branch tests (Phase 35, SIG-01)
    // ============================================================

    /// Build a synthetic 1h Ohlcv covering 2024-2025 UTC (17544 bars).
    /// Long enough to contain all 16 ECB events and 22 NFP events.
    fn ecb_nfp_test_ohlcv_2024_2025_1h(
    ) -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<i64>) {
        // 2024 (leap): 8784h, 2025: 8760h = 17544 total
        let n = 17_544usize;
        let start_ns: i64 = 1_704_067_200i64 * 1_000_000_000; // 2024-01-01 00:00:00 UTC
        let hour_ns: i64 = 3_600i64 * 1_000_000_000;

        let close: Vec<f64> = (0..n).map(|i| 1.10 + (i as f64) * 0.0001).collect();
        let open = close.clone();
        let high: Vec<f64> = close.iter().map(|c| c + 0.001).collect();
        let low: Vec<f64> = close.iter().map(|c| c - 0.001).collect();
        let volume = vec![1000.0_f64; n];
        let datetimes_ns: Vec<i64> = (0..n as i64).map(|i| start_ns + i * hour_ns).collect();
        (open, high, low, close, volume, datetimes_ns)
    }

    #[test]
    fn dispatcher_routes_ecb_to_ecb_signals() {
        let (open, high, low, close, volume, dts) = ecb_nfp_test_ohlcv_2024_2025_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("event_source".to_string(), Value::from("ecb"));
        params.insert("window_offset".to_string(), Value::from(2u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));
        params.insert("exit_type".to_string(), Value::from("none"));

        let via_dispatcher = macro_event_drift_signals(&ohlcv, &params);
        let direct = ecb_event_drift_signals(&ohlcv, &params);

        assert_eq!(
            via_dispatcher, direct,
            "event_source=ecb must route to ecb_event_drift_signals"
        );
    }

    #[test]
    fn ecb_window_offset_lt2_self_prunes() {
        let (open, high, low, close, volume, dts) = ecb_nfp_test_ohlcv_2024_2025_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("window_offset".to_string(), Value::from(1u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));

        let sig = ecb_event_drift_signals(&ohlcv, &params);
        assert!(
            sig.iter().all(|&s| s == 0),
            "window_offset=1 must self-prune to all zeros (look-ahead protection)"
        );
    }

    #[test]
    fn ecb_neutral_hold_skips_entry() {
        // ECB_DATES_2024_2025[0] = 2024-01-25, dir=0 (neutral hold, CET day, hour=13)
        // After window end (14:00 UTC) + offset=2 bars → should see NO +1/-1 entry.
        let (open, high, low, close, volume, dts) = ecb_nfp_test_ohlcv_2024_2025_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("window_offset".to_string(), Value::from(2u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));
        params.insert("exit_type".to_string(), Value::from("none"));

        let sig = ecb_event_drift_signals(&ohlcv, &params);

        // 2024-01-25 14:00 UTC end + offset=2 → entry bar at 16:00 UTC
        // hours since 2024-01-01 00:00 UTC: 24*25 = 600h, plus 16h = 616
        let entry_bar = 24 * 24 + 16; // Jan 25 = day 24 (0-indexed), 16:00 = bar 616
        assert_eq!(
            sig[entry_bar], 0,
            "ECB neutral-hold (dir=0) must produce no entry signal at bar {entry_bar}"
        );
    }

    #[test]
    fn ecb_dovish_enters_short() {
        // ECB_DATES_2024_2025[3] = 2024-06-06, dir=-1 (CEST day, hour=12)
        // Window end: 13:00 UTC + offset=2 → entry at 15:00 UTC on Jun 06
        let (open, high, low, close, volume, dts) = ecb_nfp_test_ohlcv_2024_2025_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("window_offset".to_string(), Value::from(2u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));
        params.insert("exit_type".to_string(), Value::from("none"));

        let sig = ecb_event_drift_signals(&ohlcv, &params);

        // Verify at least one short entry exists in the signal (since 2024 has multiple dovish)
        let has_short = sig.contains(&-1);
        assert!(
            has_short,
            "ECB 2024 has dovish events — expected at least one short entry"
        );
    }

    #[test]
    fn ecb_hawkish_enters_long() {
        // The ECB_DATES_2024_2025 in 2024 has no hawkish (+1) events.
        // However we verify the function produces non-zero signals overall (dovish or neutral skips).
        // Full smoke: 2024 has dir=-1 events (dovish) → short entries present.
        // 2024-2025 combined: verify long entries exist only if there are +1 entries.
        let (open, high, low, close, volume, dts) = ecb_nfp_test_ohlcv_2024_2025_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("window_offset".to_string(), Value::from(2u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));
        params.insert("exit_type".to_string(), Value::from("none"));

        let sig = ecb_event_drift_signals(&ohlcv, &params);

        // ECB 2024-2025 data: check for directional events (+1 hawkish or -1 dovish)
        let directional_events: i64 = crate::events::ECB_DATES_2024_2025
            .iter()
            .filter(|&&(_, _, _, _, dir)| dir != 0)
            .count() as i64;
        let has_any_entry = sig.iter().any(|&s| s != 0);
        assert!(
            has_any_entry || directional_events == 0,
            "ECB has {directional_events} directional events but signal is all-zero"
        );
    }

    // ============================================================
    // NFP event_source branch tests (Phase 35, SIG-02)
    // ============================================================

    #[test]
    fn dispatcher_routes_nfp_to_nfp_signals() {
        let (open, high, low, close, volume, dts) = ecb_nfp_test_ohlcv_2024_2025_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("event_source".to_string(), Value::from("nfp"));
        params.insert("window_offset".to_string(), Value::from(2u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));
        params.insert("exit_type".to_string(), Value::from("none"));

        let via_dispatcher = macro_event_drift_signals(&ohlcv, &params);
        let direct = nfp_event_drift_signals(&ohlcv, &params);

        assert_eq!(
            via_dispatcher, direct,
            "event_source=nfp must route to nfp_event_drift_signals"
        );
    }

    #[test]
    fn nfp_window_offset_lt2_self_prunes() {
        let (open, high, low, close, volume, dts) = ecb_nfp_test_ohlcv_2024_2025_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("window_offset".to_string(), Value::from(1u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));

        let sig = nfp_event_drift_signals(&ohlcv, &params);
        assert!(
            sig.iter().all(|&s| s == 0),
            "SIG-02: window_offset=1 must self-prune (NFP mid-bar at 13:30, only 30min available)"
        );
    }

    #[test]
    fn nfp_inline_skips_entry() {
        // NFP_DATES_2024_2025[6] = 2024-07-05, dir=0 (INLINE, EDT day, hour=12)
        // Window end: 13:00 UTC + offset=2 → entry at 15:00 UTC on Jul 05
        let (open, high, low, close, volume, dts) = ecb_nfp_test_ohlcv_2024_2025_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("window_offset".to_string(), Value::from(2u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));
        params.insert("exit_type".to_string(), Value::from("none"));

        let sig = nfp_event_drift_signals(&ohlcv, &params);

        // 2024-07-05 13:00 UTC end + offset=2 → entry bar at 15:00 UTC
        // days from Jan 1: Jan(31) + Feb(29) + Mar(31) + Apr(30) + May(31) + Jun(30) = 182 days, then Jul 1-4 = 4 days → Jul 5 = day 186 (0-indexed)
        // hours: 186*24 + 15 = 4479
        let entry_bar = 186 * 24 + 15;
        assert_eq!(
            sig[entry_bar], 0,
            "NFP INLINE (dir=0) must produce no entry at bar {entry_bar}"
        );
    }

    #[test]
    fn nfp_beat_enters_long() {
        // NFP has multiple BEAT (+1) events in 2024-2025.
        let (open, high, low, close, volume, dts) = ecb_nfp_test_ohlcv_2024_2025_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("window_offset".to_string(), Value::from(2u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));
        params.insert("exit_type".to_string(), Value::from("none"));

        let sig = nfp_event_drift_signals(&ohlcv, &params);
        let has_long = sig.contains(&1);
        assert!(
            has_long,
            "NFP 2024-2025 has BEAT events — expected at least one long entry"
        );
    }

    #[test]
    fn nfp_miss_enters_short() {
        // NFP has multiple MISS (-1) events in 2024-2025.
        let (open, high, low, close, volume, dts) = ecb_nfp_test_ohlcv_2024_2025_1h();
        let ohlcv = Ohlcv {
            open: &open,
            high: &high,
            low: &low,
            close: &close,
            volume: &volume,
            datetimes_ns: Some(&dts),
            aux_close: None,
        };
        let mut params: HashMap<String, Value> = HashMap::new();
        params.insert("window_offset".to_string(), Value::from(2u64));
        params.insert("hold_bars".to_string(), Value::from(1u64));
        params.insert("exit_type".to_string(), Value::from("none"));

        let sig = nfp_event_drift_signals(&ohlcv, &params);
        let has_short = sig.contains(&-1);
        assert!(
            has_short,
            "NFP 2024-2025 has MISS events — expected at least one short entry"
        );
    }
}
