/// Portfolio engine — weight allocation, timestamp intersection, equity curve synthesis,
/// metrics computation, and the `build_portfolio` public entry point.
use std::collections::{HashMap, HashSet};

use anyhow::anyhow;

use crate::backtest::BacktestResult;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// A single backtest slot to be combined into a portfolio.
pub struct SlotInput<'a> {
    pub label: String,
    pub result: &'a BacktestResult,
    pub timeframe: &'a str,
}

/// Weight allocation method.
pub enum WeightMethod {
    Equal,
    RiskParity,
}

/// Combined portfolio result with synthesized equity curve and all portfolio metrics.
#[derive(Debug)]
pub struct PortfolioResult {
    pub equity_curve: Vec<f64>,
    pub timestamps: Vec<i64>,
    pub weights: Vec<f64>,
    pub slot_labels: Vec<String>,
    pub sharpe: f64,
    pub calmar: f64,
    pub max_drawdown: f64,
    pub cagr: f64,
    pub mean_correlation: f64,
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Population standard deviation of a slice. Returns 0.0 for empty or
/// single-element slices.
fn std_dev(data: &[f64]) -> f64 {
    let n = data.len();
    if n < 2 {
        return 0.0;
    }
    let mean = data.iter().sum::<f64>() / n as f64;
    let variance = data.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / n as f64;
    variance.sqrt()
}

// ---------------------------------------------------------------------------
// Weight functions
// ---------------------------------------------------------------------------

/// Assign equal weight 1/N to each of N slots.
/// Returns an empty vec when n == 0.
pub fn equal_weights(n: usize) -> Vec<f64> {
    if n == 0 {
        return Vec::new();
    }
    vec![1.0 / n as f64; n]
}

/// Compute inverse-volatility (risk parity) weights.
///
/// - `returns_per_slot` — per-slot returns series (e.g. daily/bar returns)
/// - Slots with vol < 1e-12 are treated as zero-vol and receive weight 0.0
/// - If ALL slots have zero vol, falls back to equal weights with a warning
pub fn risk_parity_weights(returns_per_slot: &[Vec<f64>]) -> Vec<f64> {
    let n = returns_per_slot.len();
    if n == 0 {
        return Vec::new();
    }

    let vols: Vec<f64> = returns_per_slot.iter().map(|r| std_dev(r)).collect();

    // Identify non-zero-vol slots
    let inv_vols: Vec<f64> = vols
        .iter()
        .map(|&v| if v < 1e-12 { 0.0 } else { 1.0 / v })
        .collect();

    let inv_vol_sum: f64 = inv_vols.iter().sum();

    if inv_vol_sum < 1e-12 {
        // All slots have zero volatility — fall back to equal weights
        tracing::warn!("all slots have zero volatility - using equal weights");
        return equal_weights(n);
    }

    inv_vols.iter().map(|&iv| iv / inv_vol_sum).collect()
}

// ---------------------------------------------------------------------------
// Timestamp intersection
// ---------------------------------------------------------------------------

/// Compute the sorted intersection of multiple timestamp slices.
/// Empty input or no-overlap returns an empty vec.
pub fn timestamp_intersection(slot_timestamps: &[&[i64]]) -> Vec<i64> {
    if slot_timestamps.is_empty() {
        return Vec::new();
    }

    // Start with the first slot's timestamps as a HashSet
    let mut common: HashSet<i64> = slot_timestamps[0].iter().copied().collect();

    // Intersect with each subsequent slot
    for &ts in &slot_timestamps[1..] {
        let other: HashSet<i64> = ts.iter().copied().collect();
        common = common.intersection(&other).copied().collect();
    }

    let mut result: Vec<i64> = common.into_iter().collect();
    result.sort_unstable();
    result
}

// ---------------------------------------------------------------------------
// Equity curve synthesis
// ---------------------------------------------------------------------------

/// Synthesize a weighted portfolio equity curve from multiple slots.
///
/// Steps:
/// 1. Compute timestamp intersection across all slots
/// 2. Extract equity values at intersection timestamps via HashMap lookup
/// 3. Compute per-bar % returns for each slot
/// 4. Compute weighted portfolio return per bar
/// 5. Build cumulative equity curve starting at 1.0
///
/// Returns (equity_curve, intersection_timestamps).
pub fn combine_curves(
    slots: &[SlotInput<'_>],
    weights: &[f64],
) -> anyhow::Result<(Vec<f64>, Vec<i64>)> {
    if slots.is_empty() {
        return Ok((Vec::new(), Vec::new()));
    }

    // Step 1: Compute timestamp intersection
    let all_ts: Vec<&[i64]> = slots
        .iter()
        .map(|s| s.result.timestamps.as_slice())
        .collect();
    let common_ts = timestamp_intersection(&all_ts);

    if common_ts.len() < 2 {
        return Ok((Vec::new(), common_ts));
    }

    // Step 2: For each slot, build a ts -> equity index
    let slot_equity_at_ts: Vec<Vec<f64>> = slots
        .iter()
        .map(|slot| {
            let index: HashMap<i64, usize> = slot
                .result
                .timestamps
                .iter()
                .copied()
                .enumerate()
                .map(|(i, ts)| (ts, i))
                .collect();
            common_ts
                .iter()
                .map(|ts| {
                    let idx = index[ts];
                    slot.result.equity_curve[idx]
                })
                .collect()
        })
        .collect();

    // Step 3: Per-bar % returns for each slot (N-1 returns from N bars)
    let m = common_ts.len();
    let n_slots = slots.len();

    // Step 4 + 5: Weighted portfolio equity curve
    // equity[0] = 1.0, equity[i] = equity[i-1] * (1 + weighted_return[i-1])
    let mut equity_curve = vec![1.0f64; m];
    for i in 1..m {
        let weighted_return: f64 = (0..n_slots)
            .map(|s| {
                let prev = slot_equity_at_ts[s][i - 1];
                let curr = slot_equity_at_ts[s][i];
                let ret = if prev.abs() > 1e-12 {
                    curr / prev - 1.0
                } else {
                    0.0
                };
                weights[s] * ret
            })
            .sum();
        equity_curve[i] = equity_curve[i - 1] * (1.0 + weighted_return);
    }

    Ok((equity_curve, common_ts))
}

// ---------------------------------------------------------------------------
// Sanitize helper (D-08)
// ---------------------------------------------------------------------------

/// Replace non-finite metric values with 0.0, logging a warning.
fn sanitize(v: f64, label: &str) -> f64 {
    if v.is_finite() {
        v
    } else {
        tracing::warn!(
            "metric '{}' is non-finite ({}) - replacing with 0.0",
            label,
            v
        );
        0.0
    }
}

// ---------------------------------------------------------------------------
// Metric functions
// ---------------------------------------------------------------------------

/// Annualised Sharpe ratio from a bar-returns series.
///
/// Uses population std dev (same as `std_dev` helper). Returns 0.0 when
/// std dev < 1e-12 to avoid divide-by-zero.
fn portfolio_sharpe(returns: &[f64], ppy: f64) -> f64 {
    let n = returns.len();
    if n < 2 {
        return 0.0;
    }
    let mean = returns.iter().sum::<f64>() / n as f64;
    let sd = std_dev(returns);
    if sd < 1e-12 {
        return 0.0;
    }
    mean / sd * ppy.sqrt()
}

/// Maximum drawdown of an equity curve (negative value, e.g. -0.20).
///
/// Returns 0.0 for empty or single-element curves.
fn portfolio_max_drawdown(equity: &[f64]) -> f64 {
    let n = equity.len();
    if n < 2 {
        return 0.0;
    }
    let mut peak = equity[0];
    let mut max_dd = 0.0f64;
    for &e in &equity[1..] {
        if e > peak {
            peak = e;
        }
        if peak.abs() > 1e-12 {
            let dd = (e - peak) / peak;
            if dd < max_dd {
                max_dd = dd;
            }
        }
    }
    max_dd
}

/// Compound Annual Growth Rate from an equity curve.
///
/// Returns -1.0 when equity ratio <= 0, 0.0 when n_bars < 2.
fn portfolio_cagr(equity: &[f64], ppy: f64) -> f64 {
    let n = equity.len();
    if n < 2 {
        return 0.0;
    }
    let first = equity[0];
    let last = *equity.last().unwrap();
    if first.abs() < 1e-12 {
        return 0.0;
    }
    let ratio = last / first;
    if ratio <= 0.0 {
        return -1.0;
    }
    ratio.powf(ppy / n as f64) - 1.0
}

/// Calmar ratio: cagr / abs(max_dd). Returns 0.0 when abs(max_dd) < 1e-12.
fn portfolio_calmar(cagr_val: f64, max_dd: f64) -> f64 {
    if max_dd.abs() < 1e-12 {
        return 0.0;
    }
    cagr_val / max_dd.abs()
}

// ---------------------------------------------------------------------------
// Correlation (PORT-04)
// ---------------------------------------------------------------------------

/// Mean pairwise Pearson correlation across N return series.
///
/// Returns f64::NAN for N < 2 or when no valid pairs exist.
///
/// Pearson formula used inline (not via `statrs`) to avoid API uncertainty.
/// TODO(refactor): consider using statrs::statistics::Statistics::correlation after verifying API
fn mean_pairwise_correlation(returns_matrix: &[Vec<f64>]) -> f64 {
    let n = returns_matrix.len();
    if n < 2 {
        return f64::NAN;
    }

    let mut sum_corr = 0.0f64;
    let mut count = 0usize;

    for i in 0..n {
        for j in (i + 1)..n {
            let x = &returns_matrix[i];
            let y = &returns_matrix[j];
            let len = x.len().min(y.len());
            if len < 2 {
                continue;
            }
            let x = &x[..len];
            let y = &y[..len];

            let sx = std_dev(x);
            let sy = std_dev(y);
            if sx < 1e-12 || sy < 1e-12 {
                continue;
            }

            let mean_x = x.iter().sum::<f64>() / len as f64;
            let mean_y = y.iter().sum::<f64>() / len as f64;
            let mean_xy = x.iter().zip(y.iter()).map(|(&a, &b)| a * b).sum::<f64>() / len as f64;
            let cov = mean_xy - mean_x * mean_y;
            let corr = cov / (sx * sy);
            sum_corr += corr;
            count += 1;
        }
    }

    if count == 0 {
        return f64::NAN;
    }
    sum_corr / count as f64
}

// ---------------------------------------------------------------------------
// Public entry point (PORT-07)
// ---------------------------------------------------------------------------

/// Build a portfolio from multiple backtest slots.
///
/// Validates inputs, computes weights, synthesizes equity curve, calculates
/// all metrics, and returns a [`PortfolioResult`].
///
/// # Errors
/// - Empty slots slice
/// - Mixed timeframes across slots (D-04)
pub fn build_portfolio(
    slots: &[SlotInput<'_>],
    method: WeightMethod,
) -> anyhow::Result<PortfolioResult> {
    // Guard: empty input
    if slots.is_empty() {
        return Err(anyhow!("no slots provided"));
    }

    // Validate timeframes — collect unique set
    let unique_tfs: Vec<&str> = {
        let mut seen = std::collections::HashSet::new();
        slots
            .iter()
            .map(|s| s.timeframe)
            .filter(|tf| seen.insert(*tf))
            .collect()
    };
    if unique_tfs.len() > 1 {
        return Err(anyhow!("mixed timeframes: {:?}", unique_tfs));
    }

    let ppy = crate::backtest::periods_per_year(slots[0].timeframe);

    // Compute per-slot returns from equity curves (needed for risk_parity)
    let slot_returns: Vec<Vec<f64>> = slots
        .iter()
        .map(|s| {
            let eq = &s.result.equity_curve;
            (1..eq.len())
                .map(|i| {
                    if eq[i - 1].abs() > 1e-12 {
                        eq[i] / eq[i - 1] - 1.0
                    } else {
                        0.0
                    }
                })
                .collect()
        })
        .collect();

    // Compute weights
    let weights = match method {
        WeightMethod::Equal => equal_weights(slots.len()),
        WeightMethod::RiskParity => risk_parity_weights(&slot_returns),
    };

    // Synthesize portfolio equity curve via timestamp intersection
    let (equity_curve, timestamps) = combine_curves(slots, &weights)?;

    // Portfolio bar returns from synthesized equity
    let portfolio_returns: Vec<f64> = (1..equity_curve.len())
        .map(|i| {
            if equity_curve[i - 1].abs() > 1e-12 {
                equity_curve[i] / equity_curve[i - 1] - 1.0
            } else {
                0.0
            }
        })
        .collect();

    // Compute metrics with sanitization
    let sharpe = sanitize(portfolio_sharpe(&portfolio_returns, ppy), "sharpe");
    let max_drawdown = sanitize(portfolio_max_drawdown(&equity_curve), "max_drawdown");
    let cagr = sanitize(portfolio_cagr(&equity_curve, ppy), "cagr");
    let calmar = sanitize(portfolio_calmar(cagr, max_drawdown), "calmar");

    // Per-slot returns at intersection timestamps for correlation
    let common_ts_set: HashSet<i64> = timestamps.iter().copied().collect();
    let returns_matrix: Vec<Vec<f64>> = slots
        .iter()
        .map(|slot| {
            // Build index from timestamps to equity values for this slot
            let index: HashMap<i64, f64> = slot
                .result
                .timestamps
                .iter()
                .copied()
                .zip(slot.result.equity_curve.iter().copied())
                .collect();
            // Collect equity at intersection timestamps (sorted)
            let mut sorted_ts: Vec<i64> = timestamps.clone();
            sorted_ts.sort_unstable();
            let eq_at_ts: Vec<f64> = sorted_ts
                .iter()
                .filter(|ts| common_ts_set.contains(ts))
                .filter_map(|ts| index.get(ts).copied())
                .collect();
            // Convert to returns
            (1..eq_at_ts.len())
                .map(|i| {
                    if eq_at_ts[i - 1].abs() > 1e-12 {
                        eq_at_ts[i] / eq_at_ts[i - 1] - 1.0
                    } else {
                        0.0
                    }
                })
                .collect()
        })
        .collect();

    let mean_correlation = sanitize(
        mean_pairwise_correlation(&returns_matrix),
        "mean_correlation",
    );

    let slot_labels: Vec<String> = slots.iter().map(|s| s.label.clone()).collect();

    Ok(PortfolioResult {
        equity_curve,
        timestamps,
        weights,
        slot_labels,
        sharpe,
        calmar,
        max_drawdown,
        cagr,
        mean_correlation,
    })
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backtest::BacktestResult;

    fn make_result(equity: Vec<f64>, timestamps: Vec<i64>) -> BacktestResult {
        BacktestResult {
            total_return: 0.0,
            sharpe_ratio: 0.0,
            max_drawdown: 0.0,
            win_rate: 0.0,
            num_trades: 0,
            gross_profit: 0.0,
            gross_loss: 0.0,
            profit_factor: 0.0,
            equity_curve: equity,
            timestamps,
        }
    }

    // ------ equal_weights ------

    #[test]
    fn equal_weight_3_slots() {
        let w = equal_weights(3);
        assert_eq!(w.len(), 3);
        for &wi in &w {
            let diff = (wi - 1.0 / 3.0).abs();
            assert!(diff < 1e-12, "weight {wi} != 1/3");
        }
        let sum: f64 = w.iter().sum();
        assert!((sum - 1.0).abs() < 1e-12, "sum {sum} != 1.0");
    }

    #[test]
    fn equal_weight_1_slot() {
        let w = equal_weights(1);
        assert_eq!(w, vec![1.0]);
    }

    // ------ risk_parity_weights ------

    #[test]
    fn risk_parity_different_vols() {
        // Slot A: 5 returns with std ~0.02 → stdev computed from actual data
        // We construct returns such that slot A vol ≈ 2x slot B vol.
        // Simple approach: slot A has values all 0.04, slot B all 0.02.
        // Actually std_dev of constant data is 0, so we need varying data.
        // Use two slots: A = [-0.02, 0.02, -0.02, 0.02], B = [-0.04, 0.04, -0.04, 0.04]
        // std_dev(A) = 0.02, std_dev(B) = 0.04
        // inv_vol(A) = 1/0.02 = 50, inv_vol(B) = 1/0.04 = 25
        // weight(A) = 50/75 = 2/3, weight(B) = 25/75 = 1/3
        let slot_a = vec![-0.02f64, 0.02, -0.02, 0.02];
        let slot_b = vec![-0.04f64, 0.04, -0.04, 0.04];
        let w = risk_parity_weights(&[slot_a, slot_b]);
        assert_eq!(w.len(), 2);
        let expected_a = 2.0 / 3.0;
        let expected_b = 1.0 / 3.0;
        assert!(
            (w[0] - expected_a).abs() < 1e-10,
            "w[0]={} expected {expected_a}",
            w[0]
        );
        assert!(
            (w[1] - expected_b).abs() < 1e-10,
            "w[1]={} expected {expected_b}",
            w[1]
        );
        let sum: f64 = w.iter().sum();
        assert!((sum - 1.0).abs() < 1e-12, "sum={sum}");
    }

    #[test]
    fn risk_parity_one_zero_vol() {
        // Slot A: zero vol (constant), slot B & C: non-zero
        // slot B vol = 0.02, slot C vol = 0.04
        // Only B and C share the weight; A gets 0.0
        let slot_a = vec![0.0f64, 0.0, 0.0, 0.0]; // zero vol
        let slot_b = vec![-0.02f64, 0.02, -0.02, 0.02];
        let slot_c = vec![-0.04f64, 0.04, -0.04, 0.04];
        let w = risk_parity_weights(&[slot_a, slot_b, slot_c]);
        assert_eq!(w.len(), 3);
        assert!(
            (w[0]).abs() < 1e-12,
            "zero-vol slot weight should be 0, got {}",
            w[0]
        );
        let sum: f64 = w.iter().sum();
        assert!((sum - 1.0).abs() < 1e-12, "sum={sum}");
        // B gets 2/3, C gets 1/3 (same as risk_parity_different_vols)
        assert!((w[1] - 2.0 / 3.0).abs() < 1e-10, "w[1]={}", w[1]);
        assert!((w[2] - 1.0 / 3.0).abs() < 1e-10, "w[2]={}", w[2]);
    }

    #[test]
    fn risk_parity_all_zero_vol() {
        // All zero vol → fall back to equal weights
        let slot_a = vec![0.0f64, 0.0, 0.0];
        let slot_b = vec![0.0f64, 0.0, 0.0];
        let w = risk_parity_weights(&[slot_a, slot_b]);
        assert_eq!(w.len(), 2);
        assert!((w[0] - 0.5).abs() < 1e-12, "w[0]={}", w[0]);
        assert!((w[1] - 0.5).abs() < 1e-12, "w[1]={}", w[1]);
    }

    // ------ timestamp_intersection ------

    #[test]
    fn timestamp_intersection_two_slots() {
        let a = vec![100i64, 200, 300, 400];
        let b = vec![200i64, 300, 500];
        let result = timestamp_intersection(&[&a, &b]);
        assert_eq!(result, vec![200i64, 300]);
    }

    #[test]
    fn timestamp_intersection_no_overlap() {
        let a = vec![100i64, 200];
        let b = vec![300i64, 400];
        let result = timestamp_intersection(&[&a, &b]);
        assert!(result.is_empty());
    }

    // ------ combine_curves ------

    #[test]
    fn combine_curves_equal_weight_2_slots() {
        // Two slots with known equity curves sharing timestamps [0,1,2,3]
        // Slot A: equity [1.0, 1.1, 1.21, 1.331] — returns 10% each bar
        // Slot B: equity [1.0, 1.0, 1.0, 1.0]   — returns 0% each bar
        // Equal weight: 0.5 each → portfolio return = 0.5*10% + 0.5*0% = 5%
        // Expected equity: [1.0, 1.05, 1.1025, 1.157625]
        let ts = vec![0i64, 1, 2, 3];
        let r_a = make_result(vec![1.0, 1.1, 1.21, 1.331], ts.clone());
        let r_b = make_result(vec![1.0, 1.0, 1.0, 1.0], ts.clone());
        let slots = vec![
            SlotInput {
                label: "A".to_string(),
                result: &r_a,
                timeframe: "1h",
            },
            SlotInput {
                label: "B".to_string(),
                result: &r_b,
                timeframe: "1h",
            },
        ];
        let weights = vec![0.5, 0.5];
        let (eq, common_ts) = combine_curves(&slots, &weights).unwrap();
        assert_eq!(common_ts, ts);
        assert_eq!(eq.len(), 4);
        assert!((eq[0] - 1.0).abs() < 1e-10, "eq[0]={}", eq[0]);
        assert!((eq[1] - 1.05).abs() < 1e-10, "eq[1]={}", eq[1]);
        assert!((eq[2] - 1.1025).abs() < 1e-8, "eq[2]={}", eq[2]);
        assert!((eq[3] - 1.157625).abs() < 1e-8, "eq[3]={}", eq[3]);
    }

    #[test]
    fn combine_curves_misaligned_timestamps() {
        // Slot A: timestamps [0,1,2,3], Slot B: timestamps [1,2,3,4]
        // Intersection = [1,2,3] → result length = 3
        let r_a = make_result(vec![1.0, 1.1, 1.21, 1.331], vec![0i64, 1, 2, 3]);
        let r_b = make_result(vec![1.0, 1.1, 1.21, 1.331], vec![1i64, 2, 3, 4]);
        let slots = vec![
            SlotInput {
                label: "A".to_string(),
                result: &r_a,
                timeframe: "1h",
            },
            SlotInput {
                label: "B".to_string(),
                result: &r_b,
                timeframe: "1h",
            },
        ];
        let weights = vec![0.5, 0.5];
        let (eq, common_ts) = combine_curves(&slots, &weights).unwrap();
        assert_eq!(common_ts, vec![1i64, 2, 3]);
        assert_eq!(eq.len(), 3);
    }

    // ------ sanitize ------

    #[test]
    fn sanitize_nan() {
        assert_eq!(sanitize(f64::NAN, "test"), 0.0);
    }

    #[test]
    fn sanitize_infinity() {
        assert_eq!(sanitize(f64::INFINITY, "test"), 0.0);
    }

    #[test]
    fn sanitize_finite() {
        assert_eq!(sanitize(1.5, "test"), 1.5);
    }

    // ------ portfolio_sharpe ------

    #[test]
    fn sharpe_ratio_known_returns() {
        // returns: [0.01, 0.02, -0.005, 0.015, 0.01]
        // mean = (0.01+0.02-0.005+0.015+0.01)/5 = 0.05/5 = 0.01
        // variance (pop): sum((x-mean)^2)/n
        // deviations: [0.0, 0.01, -0.015, 0.005, 0.0]
        // sq: [0.0, 0.0001, 0.000225, 0.000025, 0.0]
        // sum_sq = 0.00035, variance = 0.00035/5 = 0.00007, std = sqrt(0.00007)
        // sharpe = 0.01 / sqrt(0.00007) * sqrt(252)
        let returns = vec![0.01f64, 0.02, -0.005, 0.015, 0.01];
        let ppy = 252.0f64;
        let mean = returns.iter().sum::<f64>() / returns.len() as f64;
        let variance =
            returns.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / returns.len() as f64;
        let std = variance.sqrt();
        let expected = mean / std * ppy.sqrt();
        let result = portfolio_sharpe(&returns, ppy);
        assert!(
            (result - expected).abs() < 1e-10,
            "sharpe={result} expected={expected}"
        );
    }

    // ------ portfolio_max_drawdown ------

    #[test]
    fn max_drawdown_known_curve() {
        // equity: [1.0, 1.1, 0.9, 0.95, 1.2]
        // peak at index 1 = 1.1, drawdown at index 2 = (0.9-1.1)/1.1 = -0.1818...
        let equity = vec![1.0f64, 1.1, 0.9, 0.95, 1.2];
        let dd = portfolio_max_drawdown(&equity);
        let expected = (0.9 - 1.1) / 1.1;
        assert!(
            (dd - expected).abs() < 1e-10,
            "max_dd={dd} expected={expected}"
        );
    }

    // ------ portfolio_cagr ------

    #[test]
    fn cagr_known_curve() {
        // equity[0]=1.0, equity[last]=1.5, n_bars=1000, ppy=8760
        // CAGR = (1.5/1.0)^(8760/1000) - 1
        let n = 1000usize;
        let mut equity = vec![1.0f64; n];
        *equity.last_mut().unwrap() = 1.5;
        let ppy = 8760.0;
        let expected = (1.5f64 / 1.0).powf(ppy / n as f64) - 1.0;
        let result = portfolio_cagr(&equity, ppy);
        assert!(
            (result - expected).abs() < 1e-10,
            "cagr={result} expected={expected}"
        );
    }

    // ------ portfolio_calmar ------

    #[test]
    fn calmar_known() {
        // cagr=0.15, max_dd=-0.10 → calmar = 0.15/0.10 = 1.5
        let result = portfolio_calmar(0.15, -0.10);
        assert!((result - 1.5).abs() < 1e-10, "calmar={result}");
    }

    #[test]
    fn calmar_zero_dd() {
        // max_dd=0.0 → calmar = 0.0 (no division by zero)
        let result = portfolio_calmar(0.15, 0.0);
        assert_eq!(result, 0.0);
    }

    // ------ mean_pairwise_correlation ------

    #[test]
    fn mean_correlation_two_identical_slots() {
        // Two identical return series → correlation = 1.0
        let returns = vec![0.01f64, -0.02, 0.03, -0.01, 0.02];
        let matrix = vec![returns.clone(), returns.clone()];
        let corr = mean_pairwise_correlation(&matrix);
        assert!((corr - 1.0).abs() < 1e-10, "corr={corr}");
    }

    #[test]
    fn mean_correlation_single_slot() {
        // N < 2 → returns NaN, which gets sanitized to 0.0
        let returns = vec![0.01f64, -0.02, 0.03];
        let matrix = vec![returns];
        let corr = mean_pairwise_correlation(&matrix);
        assert!(corr.is_nan(), "single slot should return NaN from raw fn");
    }

    // ------ build_portfolio ------

    #[test]
    fn build_portfolio_equal_2_slots() {
        // Two slots with equal timestamps and known uptrend equity
        let ts: Vec<i64> = (0..10).map(|i| i as i64).collect();
        // Slot A: linear uptrend 1.0 → 1.9
        let eq_a: Vec<f64> = (0..10).map(|i| 1.0 + i as f64 * 0.1).collect();
        // Slot B: linear uptrend 1.0 → 1.45
        let eq_b: Vec<f64> = (0..10).map(|i| 1.0 + i as f64 * 0.05).collect();
        let r_a = make_result(eq_a, ts.clone());
        let r_b = make_result(eq_b, ts.clone());
        let slots = vec![
            SlotInput {
                label: "A".to_string(),
                result: &r_a,
                timeframe: "1h",
            },
            SlotInput {
                label: "B".to_string(),
                result: &r_b,
                timeframe: "1h",
            },
        ];
        let result = build_portfolio(&slots, WeightMethod::Equal).unwrap();
        assert_eq!(result.weights.len(), 2);
        assert!((result.weights[0] - 0.5).abs() < 1e-12);
        assert!((result.weights[1] - 0.5).abs() < 1e-12);
        assert_eq!(result.equity_curve.len(), 10);
        assert_eq!(result.timestamps.len(), 10);
        assert!(result.sharpe.is_finite());
        assert!(result.max_drawdown.is_finite());
        assert!(result.cagr.is_finite());
        assert!(result.calmar.is_finite());
        assert!(result.mean_correlation.is_finite());
        assert_eq!(result.slot_labels, vec!["A", "B"]);
    }

    #[test]
    fn build_portfolio_mixed_timeframes_error() {
        // Two slots with "1h" and "1d" → returns Err (per D-04)
        let ts: Vec<i64> = (0..5).map(|i| i as i64).collect();
        let r_a = make_result(vec![1.0, 1.1, 1.2, 1.3, 1.4], ts.clone());
        let r_b = make_result(vec![1.0, 1.1, 1.2, 1.3, 1.4], ts.clone());
        let slots = vec![
            SlotInput {
                label: "A".to_string(),
                result: &r_a,
                timeframe: "1h",
            },
            SlotInput {
                label: "B".to_string(),
                result: &r_b,
                timeframe: "1d",
            },
        ];
        let result = build_portfolio(&slots, WeightMethod::Equal);
        assert!(result.is_err(), "mixed timeframes should return Err");
        let msg = result.unwrap_err().to_string();
        assert!(msg.contains("mixed timeframes"), "error msg: {msg}");
    }

    #[test]
    fn build_portfolio_empty_slots() {
        let result = build_portfolio(&[], WeightMethod::Equal);
        assert!(result.is_err());
        let msg = result.unwrap_err().to_string();
        assert!(msg.contains("no slots provided"), "error msg: {msg}");
    }

    #[test]
    fn build_portfolio_single_slot() {
        // N=1 slot → mean_correlation = 0.0 (D-09: NaN → 0.0 via D-08)
        let ts: Vec<i64> = (0..10).map(|i| i as i64).collect();
        let eq: Vec<f64> = (0..10).map(|i| 1.0 + i as f64 * 0.1).collect();
        let r = make_result(eq, ts);
        let slots = vec![SlotInput {
            label: "Solo".to_string(),
            result: &r,
            timeframe: "1h",
        }];
        let result = build_portfolio(&slots, WeightMethod::Equal).unwrap();
        assert_eq!(result.weights, vec![1.0]);
        assert_eq!(
            result.mean_correlation, 0.0,
            "single slot correlation should be 0.0"
        );
        assert_eq!(result.slot_labels, vec!["Solo"]);
    }
}
