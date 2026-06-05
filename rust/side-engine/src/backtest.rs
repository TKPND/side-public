/// Time-of-day spread multiplier buckets (CONTEXT D-27).
///
/// | UTC hour | Session       | Multiplier |
/// |----------|---------------|------------|
/// |   0, 1   | Tokyo fix     |    0.8x    |
/// |   7      | London open   |    1.0x    |
/// |   21     | NY rollover   |    2.0x    |
/// |  other   | base          |    1.0x    |
///
/// Pure function — no side effects, no allocations.
pub fn tod_multiplier(dt: &chrono::DateTime<chrono::Utc>) -> f64 {
    use chrono::Timelike;
    match dt.hour() {
        0 | 1 => 0.8,
        7 => 1.0,
        21 => 2.0,
        _ => 1.0,
    }
}

/// Backtest result metrics.
pub struct BacktestResult {
    pub total_return: f64,
    pub sharpe_ratio: f64,
    pub max_drawdown: f64,
    pub win_rate: f64,
    pub num_trades: usize,
    pub gross_profit: f64,
    pub gross_loss: f64,
    pub profit_factor: f64,
    pub equity_curve: Vec<f64>,
    pub timestamps: Vec<i64>,
}

/// Timeframe → periods per year mapping.
pub fn periods_per_year(timeframe: &str) -> f64 {
    match timeframe {
        "1m" => 525600.0,
        "5m" => 105120.0,
        "15m" => 35040.0,
        "30m" => 17520.0,
        "1h" => 8760.0,
        "2h" => 4380.0,
        "4h" => 2190.0,
        "1d" => 365.0,
        _ => 8760.0,
    }
}

/// Vectorized backtest (always-in-market, position tracking).
///
/// Matches Python `run_backtest()` exactly:
/// 1. Shift signals by 1 bar (look-ahead bias fix)
/// 2. Position is sticky: hold until next non-zero signal
/// 3. mode: 0=both, 1=long_only, 2=short_only
/// 4. Fees applied on position changes
///
/// Signal encoding:
///   -  0: no change (hold current position)
///   - +1: enter/flip to LONG
///   - -1: enter/flip to SHORT
///   - +2 or -2: FLATTEN (exit to position=0). Used by time-based strategies
///     (e.g. `time_of_day_drift`) that need explicit exit-to-flat, which
///     cannot be expressed with the {-1, 0, +1} entry-only encoding.
pub fn run_backtest(
    close: &[f64],
    signals: &[i8],
    fee: f64,
    ppy: f64,
    mode: i8,
    datetimes_ns: &[i64],
) -> BacktestResult {
    let n = close.len();
    if n < 2 {
        return empty_result();
    }

    // Step 1: Apply signal shift by 1 bar + sticky position
    let mut positions = vec![0i32; n];
    let mut pos: i32 = 0;
    for i in 1..n {
        let sig = signals[i - 1]; // shift(1): use previous bar's signal
        if sig == 2 || sig == -2 {
            pos = 0; // explicit flatten
        } else if sig != 0 {
            pos = sig as i32;
        }
        // Mode filtering
        if mode == 1 {
            pos = pos.max(0); // long_only
        } else if mode == 2 {
            pos = pos.min(0); // short_only
        }
        positions[i] = pos;
    }

    // Step 2: Compute returns
    let mut returns = vec![0.0f64; n];
    let mut gross_profit = 0.0f64;
    let mut gross_loss = 0.0f64;
    let mut trade_returns_positive = 0usize;
    let mut trade_returns_total = 0usize;

    for i in 1..n {
        let market_return = (close[i] - close[i - 1]) / close[i - 1];
        let strategy_return = positions[i - 1] as f64 * market_return;

        // Fee on position change
        let trade = (positions[i] - positions[i - 1]).unsigned_abs() as f64;
        let fee_cost = trade * fee;

        returns[i] = strategy_return - fee_cost;

        if returns[i] > 0.0 {
            gross_profit += returns[i];
        } else if returns[i] < 0.0 {
            gross_loss += returns[i].abs();
        }

        // Trade count: count bars where position changed
        if trade > 0.0 {
            trade_returns_total += 1;
            if returns[i] > 0.0 {
                trade_returns_positive += 1;
            }
        }
    }

    // Step 3: Equity curve
    let mut equity = vec![1.0f64; n];
    for i in 1..n {
        equity[i] = equity[i - 1] * (1.0 + returns[i]);
    }

    // Metrics
    let total_return = equity[n - 1] / equity[0] - 1.0;
    let profit_factor = if gross_loss > 0.0 {
        gross_profit / gross_loss
    } else {
        f64::INFINITY
    };

    // Sharpe
    let mean_ret: f64 = returns.iter().sum::<f64>() / n as f64;
    let var: f64 = returns.iter().map(|r| (r - mean_ret).powi(2)).sum::<f64>() / (n as f64 - 1.0);
    let std_ret = var.sqrt();
    let sharpe = if std_ret > 0.0 {
        mean_ret / std_ret * ppy.sqrt()
    } else {
        0.0
    };

    // Max drawdown
    let mut peak = equity[0];
    let mut max_dd = 0.0f64;
    for &eq in &equity {
        if eq > peak {
            peak = eq;
        }
        let dd = (eq - peak) / peak;
        if dd < max_dd {
            max_dd = dd;
        }
    }

    let win_rate = if trade_returns_total > 0 {
        trade_returns_positive as f64 / trade_returns_total as f64
    } else {
        0.0
    };

    BacktestResult {
        total_return,
        sharpe_ratio: sharpe,
        max_drawdown: max_dd,
        win_rate,
        num_trades: trade_returns_total,
        gross_profit,
        gross_loss,
        profit_factor,
        equity_curve: equity,
        timestamps: datetimes_ns.to_vec(),
    }
}

/// Vectorized backtest with an explicit effective size for v5.11 runtime cap sizing.
///
/// `effective_size` is in `unit_backtest_run` terms and must be finite with
/// `0.0 < effective_size <= 1.0`. Full-size calls delegate to [`run_backtest`]
/// so the existing baseline path remains unchanged.
pub fn run_backtest_sized(
    close: &[f64],
    signals: &[i8],
    fee: f64,
    ppy: f64,
    mode: i8,
    datetimes_ns: &[i64],
    effective_size: f64,
) -> anyhow::Result<BacktestResult> {
    if !effective_size.is_finite() || effective_size <= 0.0 || effective_size > 1.0 {
        anyhow::bail!("effective_size must be finite and satisfy 0.0 < effective_size <= 1.0");
    }

    if effective_size == 1.0 {
        return Ok(run_backtest(close, signals, fee, ppy, mode, datetimes_ns));
    }

    let n = close.len();
    if n < 2 {
        return Ok(empty_result());
    }

    // Step 1: Apply signal shift by 1 bar + sticky position
    let mut positions = vec![0i32; n];
    let mut pos: i32 = 0;
    for i in 1..n {
        let sig = signals[i - 1]; // shift(1): use previous bar's signal
        if sig == 2 || sig == -2 {
            pos = 0; // explicit flatten
        } else if sig != 0 {
            pos = sig as i32;
        }
        // Mode filtering
        if mode == 1 {
            pos = pos.max(0); // long_only
        } else if mode == 2 {
            pos = pos.min(0); // short_only
        }
        positions[i] = pos;
    }

    // Step 2: Compute returns with exposure and fee scaled by the same scalar.
    let mut returns = vec![0.0f64; n];
    let mut gross_profit = 0.0f64;
    let mut gross_loss = 0.0f64;
    let mut trade_returns_positive = 0usize;
    let mut trade_returns_total = 0usize;

    for i in 1..n {
        let market_return = (close[i] - close[i - 1]) / close[i - 1];
        let strategy_return = positions[i - 1] as f64 * market_return * effective_size;

        // Fee on position change, scaled by the same effective size as exposure.
        let trade = (positions[i] - positions[i - 1]).unsigned_abs() as f64;
        let fee_cost = trade * fee * effective_size;

        returns[i] = strategy_return - fee_cost;

        if returns[i] > 0.0 {
            gross_profit += returns[i];
        } else if returns[i] < 0.0 {
            gross_loss += returns[i].abs();
        }

        // Trade count remains an event count and is not scaled by effective_size.
        if trade > 0.0 {
            trade_returns_total += 1;
            if returns[i] > 0.0 {
                trade_returns_positive += 1;
            }
        }
    }

    // Step 3: Equity curve
    let mut equity = vec![1.0f64; n];
    for i in 1..n {
        equity[i] = equity[i - 1] * (1.0 + returns[i]);
    }

    // Metrics
    let total_return = equity[n - 1] / equity[0] - 1.0;
    let profit_factor = if gross_loss > 0.0 {
        gross_profit / gross_loss
    } else {
        f64::INFINITY
    };

    // Sharpe
    let mean_ret: f64 = returns.iter().sum::<f64>() / n as f64;
    let var: f64 = returns.iter().map(|r| (r - mean_ret).powi(2)).sum::<f64>() / (n as f64 - 1.0);
    let std_ret = var.sqrt();
    let sharpe = if std_ret > 0.0 {
        mean_ret / std_ret * ppy.sqrt()
    } else {
        0.0
    };

    // Max drawdown
    let mut peak = equity[0];
    let mut max_dd = 0.0f64;
    for &eq in &equity {
        if eq > peak {
            peak = eq;
        }
        let dd = (eq - peak) / peak;
        if dd < max_dd {
            max_dd = dd;
        }
    }

    let win_rate = if trade_returns_total > 0 {
        trade_returns_positive as f64 / trade_returns_total as f64
    } else {
        0.0
    };

    Ok(BacktestResult {
        total_return,
        sharpe_ratio: sharpe,
        max_drawdown: max_dd,
        win_rate,
        num_trades: trade_returns_total,
        gross_profit,
        gross_loss,
        profit_factor,
        equity_curve: equity,
        timestamps: datetimes_ns.to_vec(),
    })
}

/// Vectorized backtest with time-of-day spread curve.
///
/// Mirrors [`run_backtest`] EXACTLY (same positional args, same behavior)
/// except that the per-bar fee is scaled by [`tod_multiplier`] based on
/// the bar's UTC hour. Used by Phase 1 fee-aware validation (FEE-04).
///
/// Note: `run_backtest` is intentionally NOT modified (CONTEXT D-29).
pub fn run_backtest_with_tod(
    close: &[f64],
    signals: &[i8],
    fee: f64,
    ppy: f64,
    mode: i8,
    datetimes_ns: &[i64],
) -> BacktestResult {
    let n = close.len();
    if n < 2 {
        return empty_result();
    }

    // Step 1: Apply signal shift by 1 bar + sticky position
    let mut positions = vec![0i32; n];
    let mut pos: i32 = 0;
    for i in 1..n {
        let sig = signals[i - 1];
        if sig != 0 {
            pos = sig as i32;
        }
        if mode == 1 {
            pos = pos.max(0);
        } else if mode == 2 {
            pos = pos.min(0);
        }
        positions[i] = pos;
    }

    // Step 2: Compute returns with TOD-scaled per-bar fee
    let mut returns = vec![0.0f64; n];
    let mut gross_profit = 0.0f64;
    let mut gross_loss = 0.0f64;
    let mut trade_returns_positive = 0usize;
    let mut trade_returns_total = 0usize;

    for i in 1..n {
        let market_return = (close[i] - close[i - 1]) / close[i - 1];
        let strategy_return = positions[i - 1] as f64 * market_return;

        // TOD-scaled fee on position change. Timestamp of the bar where the
        // cost is realized (bar `i`) drives the multiplier.
        let trade = (positions[i] - positions[i - 1]).unsigned_abs() as f64;
        let bar_ts_sec = datetimes_ns[i] / 1_000_000_000;
        let bar_dt = chrono::DateTime::<chrono::Utc>::from_timestamp(bar_ts_sec, 0)
            .expect("valid bar timestamp for tod_multiplier lookup");
        let effective_fee = fee * tod_multiplier(&bar_dt);
        let fee_cost = trade * effective_fee;

        returns[i] = strategy_return - fee_cost;

        if returns[i] > 0.0 {
            gross_profit += returns[i];
        } else if returns[i] < 0.0 {
            gross_loss += returns[i].abs();
        }

        if trade > 0.0 {
            trade_returns_total += 1;
            if returns[i] > 0.0 {
                trade_returns_positive += 1;
            }
        }
    }

    // Step 3: Equity curve
    let mut equity = vec![1.0f64; n];
    for i in 1..n {
        equity[i] = equity[i - 1] * (1.0 + returns[i]);
    }

    let total_return = equity[n - 1] / equity[0] - 1.0;
    let profit_factor = if gross_loss > 0.0 {
        gross_profit / gross_loss
    } else {
        f64::INFINITY
    };

    let mean_ret: f64 = returns.iter().sum::<f64>() / n as f64;
    let var: f64 = returns.iter().map(|r| (r - mean_ret).powi(2)).sum::<f64>() / (n as f64 - 1.0);
    let std_ret = var.sqrt();
    let sharpe = if std_ret > 0.0 {
        mean_ret / std_ret * ppy.sqrt()
    } else {
        0.0
    };

    let mut peak = equity[0];
    let mut max_dd = 0.0f64;
    for &eq in &equity {
        if eq > peak {
            peak = eq;
        }
        let dd = (eq - peak) / peak;
        if dd < max_dd {
            max_dd = dd;
        }
    }

    let win_rate = if trade_returns_total > 0 {
        trade_returns_positive as f64 / trade_returns_total as f64
    } else {
        0.0
    };

    BacktestResult {
        total_return,
        sharpe_ratio: sharpe,
        max_drawdown: max_dd,
        win_rate,
        num_trades: trade_returns_total,
        gross_profit,
        gross_loss,
        profit_factor,
        equity_curve: equity,
        timestamps: datetimes_ns.to_vec(),
    }
}

/// SL/TP bar-by-bar backtest — matches Python `_sltp_loop()`.
#[allow(clippy::too_many_arguments)]
#[allow(unused_assignments)]
pub fn run_backtest_sltp(
    close: &[f64],
    high: &[f64],
    low: &[f64],
    atr: &[f64],
    signals: &[i8],
    fee: f64,
    ppy: f64,
    mode: i8,
    sl_atr_mult: f64,
    tp_atr_mult: f64,
    sl_pct: f64,
    tp_pct: f64,
    datetimes_ns: &[i64],
) -> BacktestResult {
    let n = close.len();
    if n < 2 {
        return empty_result();
    }

    let mut returns = vec![0.0f64; n];
    let mut position: i32 = 0;
    let mut entry_price: f64 = 0.0;
    let mut sl_price: f64 = f64::NAN;
    let mut tp_price: f64 = f64::NAN;
    let mut cooldown = false;
    let mut trade_count: usize = 0;

    for i in 1..n {
        let pc = close[i - 1];
        let mut bar_return = 0.0f64;

        // Phase 0: Explicit flatten signal (signal = ±2)
        // Time-based strategies (e.g. time_of_day_drift) emit ±2 to force an
        // exit regardless of price action. See run_backtest signal encoding.
        if position != 0 && (signals[i] == 2 || signals[i] == -2) {
            bar_return = position as f64 * (close[i] - pc) / pc - fee;
            position = 0;
            entry_price = 0.0;
            sl_price = f64::NAN;
            tp_price = f64::NAN;
            cooldown = true;
            trade_count += 1;
            returns[i] = bar_return;
            continue;
        }

        // Phase 1: SL/TP exit check
        if position != 0 {
            let mut sl_hit = false;
            let mut tp_hit = false;

            if position == 1 {
                if !sl_price.is_nan() {
                    sl_hit = low[i] <= sl_price;
                }
                if !tp_price.is_nan() {
                    tp_hit = high[i] >= tp_price;
                }
            } else {
                if !sl_price.is_nan() {
                    sl_hit = high[i] >= sl_price;
                }
                if !tp_price.is_nan() {
                    tp_hit = low[i] <= tp_price;
                }
            }

            if sl_hit || tp_hit {
                let exit_price = if sl_hit { sl_price } else { tp_price };
                bar_return = position as f64 * (exit_price - pc) / pc - fee;
                position = 0;
                entry_price = 0.0;
                sl_price = f64::NAN;
                tp_price = f64::NAN;
                cooldown = true;
                trade_count += 1;
            } else {
                bar_return = position as f64 * (close[i] - pc) / pc;
            }
        }

        // Phase 2: Entry check
        if position == 0 && !cooldown {
            let signal = signals[i]; // Note: Python uses shifted_signals[i]
            let mut direction: i32 = 0;

            if signal == 1 && mode != 2 {
                direction = 1;
            } else if signal == -1 && mode != 1 {
                direction = -1;
            }

            if direction != 0 {
                position = direction;
                entry_price = close[i];

                let atr_val = atr[i];
                // SL distance
                let sl_dist = if !sl_atr_mult.is_nan() && !atr_val.is_nan() {
                    atr_val * sl_atr_mult
                } else if !sl_pct.is_nan() {
                    entry_price * sl_pct
                } else {
                    f64::NAN
                };

                // TP distance
                let tp_dist = if !tp_atr_mult.is_nan() && !atr_val.is_nan() {
                    atr_val * tp_atr_mult
                } else if !tp_pct.is_nan() {
                    entry_price * tp_pct
                } else {
                    f64::NAN
                };

                if !sl_dist.is_nan() {
                    sl_price = entry_price - direction as f64 * sl_dist;
                } else {
                    sl_price = f64::NAN;
                }
                if !tp_dist.is_nan() {
                    tp_price = entry_price + direction as f64 * tp_dist;
                } else {
                    tp_price = f64::NAN;
                }

                bar_return -= fee;
            }
        }

        cooldown = false;
        returns[i] = bar_return;
    }

    // Compute metrics from returns
    compute_metrics(&returns, trade_count, ppy, datetimes_ns)
}

fn compute_metrics(
    returns: &[f64],
    trade_count: usize,
    ppy: f64,
    datetimes_ns: &[i64],
) -> BacktestResult {
    let n = returns.len();

    // Equity curve
    let mut equity = vec![1.0f64; n];
    for i in 1..n {
        equity[i] = equity[i - 1] * (1.0 + returns[i]);
    }

    let total_return = if n > 0 {
        equity[n - 1] / equity[0] - 1.0
    } else {
        0.0
    };

    let mut gross_profit = 0.0f64;
    let mut gross_loss = 0.0f64;
    for &r in returns {
        if r > 0.0 {
            gross_profit += r;
        } else if r < 0.0 {
            gross_loss += r.abs();
        }
    }
    let profit_factor = if gross_loss > 0.0 {
        gross_profit / gross_loss
    } else {
        f64::INFINITY
    };

    // Sharpe
    let mean_ret: f64 = returns.iter().sum::<f64>() / n as f64;
    let var: f64 = returns.iter().map(|r| (r - mean_ret).powi(2)).sum::<f64>() / (n as f64 - 1.0);
    let std_ret = var.sqrt();
    let sharpe = if std_ret > 0.0 {
        mean_ret / std_ret * ppy.sqrt()
    } else {
        0.0
    };

    // Max drawdown
    let mut peak = equity[0];
    let mut max_dd = 0.0f64;
    for &eq in &equity {
        if eq > peak {
            peak = eq;
        }
        let dd = (eq - peak) / peak;
        if dd < max_dd {
            max_dd = dd;
        }
    }

    // Win rate from nonzero returns
    let nonzero: Vec<&f64> = returns.iter().filter(|&&r| r != 0.0).collect();
    let win_rate = if !nonzero.is_empty() {
        nonzero.iter().filter(|&&&r| r > 0.0).count() as f64 / nonzero.len() as f64
    } else {
        0.0
    };

    BacktestResult {
        total_return,
        sharpe_ratio: sharpe,
        max_drawdown: max_dd,
        win_rate,
        num_trades: trade_count,
        gross_profit,
        gross_loss,
        profit_factor,
        equity_curve: equity,
        timestamps: datetimes_ns.to_vec(),
    }
}

fn empty_result() -> BacktestResult {
    BacktestResult {
        total_return: 0.0,
        sharpe_ratio: 0.0,
        max_drawdown: 0.0,
        win_rate: 0.0,
        num_trades: 0,
        gross_profit: 0.0,
        gross_loss: 0.0,
        profit_factor: f64::INFINITY,
        equity_curve: vec![1.0],
        timestamps: vec![],
    }
}

/// Returns (fee_bps_per_side, periods_per_year, mode) for the given timeframe.
/// Moved from csv_loader.rs in Phase 98 Wave 3 Task 1a.
pub fn backtest_call_args(fee_bps_per_side: f64, timeframe: &str) -> (f64, f64, i8) {
    let ppy = match timeframe {
        "1m" => 525_600.0,
        "5m" => 105_120.0,
        "15m" => 35_040.0,
        "30m" => 17_520.0,
        "1h" => 8_760.0,
        "4h" => 2_190.0,
        "1d" => 365.0,
        _ => 8_760.0,
    };
    (fee_bps_per_side, ppy, 0i8)
}

#[cfg(test)]
mod tests {
    use super::*;

    const EPS: f64 = 1e-12;

    fn assert_close(left: f64, right: f64) {
        assert!((left - right).abs() <= EPS, "left={left}, right={right}");
    }

    fn assert_result_close(left: &BacktestResult, right: &BacktestResult) {
        assert_close(left.total_return, right.total_return);
        assert_close(left.profit_factor, right.profit_factor);
        assert_eq!(left.num_trades, right.num_trades);
        assert_close(left.gross_profit, right.gross_profit);
        assert_close(left.gross_loss, right.gross_loss);
        assert_eq!(left.equity_curve.len(), right.equity_curve.len());
        for (left_value, right_value) in left.equity_curve.iter().zip(right.equity_curve.iter()) {
            assert_close(*left_value, *right_value);
        }
        assert_eq!(left.timestamps, right.timestamps);
    }

    fn fee_sensitive_fixture() -> (Vec<f64>, Vec<i8>, Vec<i64>) {
        (
            vec![100.0, 110.0, 99.0, 108.9, 98.01],
            vec![1, 0, -1, 0, 0],
            vec![1_000, 2_000, 3_000, 4_000, 5_000],
        )
    }

    #[test]
    fn sized_backtest_full_size_matches_baseline() {
        let (close, signals, timestamps) = fee_sensitive_fixture();
        let baseline = run_backtest(&close, &signals, 0.01, 365.0, 0, &timestamps);
        let sized = run_backtest_sized(&close, &signals, 0.01, 365.0, 0, &timestamps, 1.0)
            .expect("full-size sized helper should be valid");

        assert_result_close(&sized, &baseline);
    }

    #[test]
    fn sized_backtest_rejects_invalid_effective_sizes() {
        let (close, signals, timestamps) = fee_sensitive_fixture();
        for effective_size in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 0.0, -0.25, 1.25] {
            let result = run_backtest_sized(
                &close,
                &signals,
                0.01,
                365.0,
                0,
                &timestamps,
                effective_size,
            );
            assert!(
                result.is_err(),
                "effective_size={effective_size:?} should fail validation"
            );
        }
    }

    #[test]
    fn sized_backtest_scales_exposure_and_fees_without_scaling_trade_count() {
        let (close, signals, timestamps) = fee_sensitive_fixture();
        let full_size = run_backtest(&close, &signals, 0.01, 365.0, 0, &timestamps);
        let sized = run_backtest_sized(&close, &signals, 0.01, 365.0, 0, &timestamps, 0.25)
            .expect("canonical 0.25 effective_size should be valid");

        assert_eq!(sized.num_trades, full_size.num_trades);
        assert_eq!(sized.num_trades, 2);
        assert!(sized.total_return != full_size.total_return);
        assert!(sized.gross_profit != full_size.gross_profit);
        assert!(sized.gross_loss != full_size.gross_loss);

        assert_close(sized.gross_profit, 0.04500000000000001);
        assert_close(sized.gross_loss, 0.0275);
        assert_close(sized.profit_factor, 1.636363636363637);
        assert_close(sized.total_return, 0.01681409374999987);
        assert_eq!(sized.timestamps, timestamps);

        let expected_equity = [1.0, 0.9975, 0.9725625, 0.99201375, 1.0168140937499999];
        assert_eq!(sized.equity_curve.len(), expected_equity.len());
        for (actual, expected) in sized.equity_curve.iter().zip(expected_equity) {
            assert_close(*actual, expected);
        }
    }
}
