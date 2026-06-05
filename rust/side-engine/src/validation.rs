//! Phase 2 validation module — Wave 0 scaffolds.
//! Wave 1 plans 02-02..02-05 will implement each function.

use anyhow::Result;
use rand::rngs::StdRng;
use rand::{RngExt, SeedableRng};
use serde::{Deserialize, Serialize};
use statrs::distribution::{ContinuousCDF, Normal};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub enum PassMode {
    Strict,
    Relaxed,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FoldSplit {
    pub fold_idx: usize,
    pub oos_indices: Vec<usize>,
    pub is_indices: Vec<usize>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GateResult {
    pub gate: String,
    pub passed: bool,
    pub value: f64,
    pub threshold: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind")]
pub enum VerdictKind {
    Pass,
    Fail { failed_gates: Vec<String> },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Verdict {
    pub gates: Vec<GateResult>, // always length 6
    pub verdict: VerdictKind,
    pub pass_mode: PassMode,
}

/// Minimum trades per WFD fold required for a fold to count toward Gate-D.
/// Phase 56 (BUG-03): prevents zero-trade folds with `profit_factor = inf`
/// from being counted as "passing" in `compute_gate_input`.
pub const MIN_TRADES_PER_FOLD: usize = 1;

#[derive(Clone, Debug)]
pub struct GateInput {
    pub abs_t_stat: f64,
    pub dsr_pvalue: f64,
    pub mean_pip: f64,
    pub round_trip_cost_pip: f64,
    pub folds_passing_oos_pf: usize,
    pub h1_mean_pip: f64,
    pub h2_mean_pip: f64,
    pub median_fold_ci_low: f64,
    pub median_fold_ci_high: f64,
    pub trades_per_fold: Vec<usize>,
}

/// Euler-Mascheroni constant γ ≈ 0.5772.
pub const EULER_MASCHERONI: f64 = 0.5772156649015329;

/// Compute the Deflated Sharpe Ratio (Bailey & LdP 2014, eq.9).
///
/// Returns DSR = Φ(z) — the probability that the observed SR is genuinely
/// significant after correcting for multiple-testing across `n_trials`.
///
/// Returns `NaN` for degenerate inputs: `n_obs < 2`, `n_trials == 0`,
/// non-finite `sharpe`, or negative variance (rare with extreme skew/kurt).
pub fn compute_dsr(sharpe: f64, n_trials: usize, skew: f64, kurt: f64, n_obs: usize) -> f64 {
    if n_obs < 2 || n_trials == 0 || !sharpe.is_finite() {
        return f64::NAN;
    }
    let n = n_trials as f64;
    let normal = Normal::new(0.0, 1.0).unwrap();

    // Expected maximum SR from n independent trials (Bailey & LdP 2014, eq.9 numerator)
    let phi_inv_1 = normal.inverse_cdf(1.0 - 1.0 / n);
    let phi_inv_2 = normal.inverse_cdf(1.0 - 1.0 / (n * std::f64::consts::E));
    let sharpe_0 = (1.0 - EULER_MASCHERONI) * phi_inv_1 + EULER_MASCHERONI * phi_inv_2;

    // Non-normality-adjusted std of SR estimator (Mertens 2002 / Bailey & LdP 2014)
    // kurt here is regular kurtosis (normal = 3.0), not excess kurtosis.
    let var_num = 1.0 - skew * sharpe + ((kurt - 1.0) / 4.0) * sharpe.powi(2);
    if var_num <= 0.0 {
        return f64::NAN;
    }
    let std_sr = (var_num / (n_obs as f64 - 1.0)).sqrt();
    if std_sr <= 0.0 {
        return if sharpe >= sharpe_0 { 1.0 } else { 0.0 };
    }

    let z = (sharpe - sharpe_0) / std_sr;
    normal.cdf(z)
}

/// Generate purged k-fold split indices with embargo (Lopez de Prado 2018, ch. 7).
///
/// Each fold uses 1/k of the data as OOS. The IS set excludes the OOS fold plus
/// `embargo_bars` rows on both sides to prevent information leakage.
pub fn purged_kfold_indices(
    n_bars: usize,
    k: usize,
    embargo_bars: usize,
) -> Result<Vec<FoldSplit>> {
    if k == 0 || n_bars < k {
        anyhow::bail!(
            "purged_kfold: n_bars={} k={} — too few bars (need n_bars >= k >= 1)",
            n_bars,
            k
        );
    }
    let fold_size = n_bars / k;
    if fold_size < 2 * embargo_bars.max(1) {
        anyhow::bail!(
            "purged_kfold: degenerate — fold_size={} < 2*embargo_bars={} (increase n_bars or reduce embargo)",
            fold_size,
            2 * embargo_bars
        );
    }
    let mut splits = Vec::with_capacity(k);
    for i in 0..k {
        let oos_start = i * fold_size;
        let oos_end = if i == k - 1 {
            n_bars
        } else {
            (i + 1) * fold_size
        };
        let purge_start = oos_start.saturating_sub(embargo_bars);
        let purge_end = (oos_end + embargo_bars).min(n_bars);
        let is_indices: Vec<usize> = (0..purge_start).chain(purge_end..n_bars).collect();
        if is_indices.len() < fold_size.min(10) {
            anyhow::bail!(
                "purged_kfold: fold {} IS too small ({} bars) — degenerate; increase n_bars or reduce embargo",
                i,
                is_indices.len()
            );
        }
        let oos_indices: Vec<usize> = (oos_start..oos_end).collect();
        splits.push(FoldSplit {
            fold_idx: i,
            oos_indices,
            is_indices,
        });
    }
    Ok(splits)
}

/// Estimate bars per day from a nanosecond timestamp slice.
///
/// Counts distinct UTC calendar dates and returns `n_bars / n_days` (rounded down, min 1).
/// Returns 1 for empty input to avoid division by zero.
pub fn bars_per_day_from_datetimes_ns(datetimes_ns: &[i64]) -> usize {
    if datetimes_ns.is_empty() {
        return 1;
    }
    use std::collections::HashSet;
    let mut days: HashSet<(i32, u32)> = HashSet::new();
    const NS_PER_DAY: i64 = 86_400_000_000_000;
    for &ns in datetimes_ns {
        // Fast UTC day computation without chrono dependency: floor to whole days
        let day_number = if ns >= 0 {
            ns / NS_PER_DAY
        } else {
            // For negative timestamps (pre-epoch), floor division
            (ns - NS_PER_DAY + 1) / NS_PER_DAY
        };
        // Store as (high_bits, low_bits) to satisfy HashSet<(i32, u32)> type
        days.insert(((day_number >> 32) as i32, day_number as u32));
    }
    let n_days = days.len().max(1);
    (datetimes_ns.len() / n_days).max(1)
}

/// Inverse-CDF geometric block-length sampler. Returns a block length >= 1
/// drawn from `Geometric(p)` (zero new deps; uses only `rand` core API).
fn sample_geometric_block(rng: &mut StdRng, p: f64) -> usize {
    let u: f64 = rng.random_range(0.0..1.0_f64).max(1e-12);
    let denom: f64 = (1.0_f64 - p).ln();
    if denom == 0.0 {
        return 1;
    }
    ((1.0_f64 - u).ln() / denom).floor() as usize + 1
}

/// Politis & Romano (1994) stationary block bootstrap 95% confidence interval
/// for the mean of a (possibly auto-correlated) PnL series.
///
/// Mean block length is set to `sqrt(n)` per Politis-Romano asymptotic optimum.
/// Determinism: identical `(pnl, n_resamples, seed)` always produces byte-identical output.
///
/// Degenerate inputs: empty series or `n_resamples == 0` returns `(0.0, 0.0)`.
pub fn stationary_bootstrap_ci(pnl_series: &[f64], n_resamples: usize, seed: u64) -> (f64, f64) {
    let n = pnl_series.len();
    if n == 0 || n_resamples == 0 {
        return (0.0, 0.0);
    }
    let mean_block = (n as f64).sqrt().max(1.0);
    let p = (1.0 / mean_block).clamp(1e-6, 1.0 - 1e-6);
    let mut rng = StdRng::seed_from_u64(seed);

    let mut replicate_means: Vec<f64> = Vec::with_capacity(n_resamples);
    for _ in 0..n_resamples {
        let mut sum = 0.0_f64;
        let mut filled = 0usize;
        while filled < n {
            let start = rng.random_range(0..n);
            let block_len = sample_geometric_block(&mut rng, p).min(n - filled);
            for j in 0..block_len {
                sum += pnl_series[(start + j) % n];
            }
            filled += block_len;
        }
        replicate_means.push(sum / n as f64);
    }

    replicate_means.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let lo_idx = ((0.025 * n_resamples as f64).floor() as usize).min(n_resamples - 1);
    let hi_idx = ((0.975 * n_resamples as f64).ceil() as usize).min(n_resamples - 1);
    (replicate_means[lo_idx], replicate_means[hi_idx])
}

/// Names of the 6 gates in fixed order — index matters for downstream consumers.
pub const GATE_NAMES: [&str; 6] = [
    "abs_t_stat",
    "dsr_pvalue",
    "mean_pip_vs_cost",
    "fold_pf_4of5",
    "h1_h2_sign_agree",
    "bootstrap_ci_excludes_zero",
];

/// Evaluate the composite 6-gate go/no-go verdict (VAL-06).
///
/// Returns a `Verdict` containing exactly 6 `GateResult` entries in the fixed order
/// defined by `GATE_NAMES`. `VerdictKind::Pass` only when all 6 gates pass; otherwise
/// `VerdictKind::Fail { failed_gates }` with the names of every gate that failed.
///
/// `pass_mode` is carried verbatim into the returned struct as audit metadata —
/// it does NOT alter gate logic. The CLI (plan 02-06) bypasses this function in
/// Relaxed mode and populates `relaxed_pass` instead.
pub fn six_gate_verdict(input: &GateInput, pass_mode: PassMode) -> Verdict {
    let gate_cost_threshold = 2.0 * input.round_trip_cost_pip;

    let h1h2_pass = input.h1_mean_pip != 0.0
        && input.h2_mean_pip != 0.0
        && input.h1_mean_pip.signum() == input.h2_mean_pip.signum();

    let ci_pass = input.median_fold_ci_low > 0.0 || input.median_fold_ci_high < 0.0;

    let gates = vec![
        GateResult {
            gate: GATE_NAMES[0].to_string(),
            passed: input.abs_t_stat > 4.40,
            value: input.abs_t_stat,
            threshold: 4.40,
        },
        GateResult {
            gate: GATE_NAMES[1].to_string(),
            passed: input.dsr_pvalue < 0.05,
            value: input.dsr_pvalue,
            threshold: 0.05,
        },
        GateResult {
            gate: GATE_NAMES[2].to_string(),
            passed: input.mean_pip > gate_cost_threshold,
            value: input.mean_pip,
            threshold: gate_cost_threshold,
        },
        GateResult {
            gate: GATE_NAMES[3].to_string(),
            passed: input.folds_passing_oos_pf >= 4,
            value: input.folds_passing_oos_pf as f64,
            threshold: 4.0,
        },
        GateResult {
            gate: GATE_NAMES[4].to_string(),
            passed: h1h2_pass,
            value: input.h1_mean_pip,
            threshold: input.h2_mean_pip,
        },
        GateResult {
            gate: GATE_NAMES[5].to_string(),
            passed: ci_pass,
            value: input.median_fold_ci_low,
            threshold: input.median_fold_ci_high,
        },
    ];

    let failed: Vec<String> = gates
        .iter()
        .filter(|g| !g.passed)
        .map(|g| g.gate.clone())
        .collect();

    let verdict_kind = if failed.is_empty() {
        VerdictKind::Pass
    } else {
        VerdictKind::Fail {
            failed_gates: failed,
        }
    };

    Verdict {
        gates,
        verdict: verdict_kind,
        pass_mode,
    }
}

/// Build a `GateInput` from raw slot statistics + per-fold backtest outputs.
///
/// Used by the `side scan --pass-mode strict` CLI path (plan 02-06). Aggregates:
/// - `folds_passing_oos_pf` = count of `fold_pfs >= 2.0` **with at least
///   `MIN_TRADES_PER_FOLD` trades** (Phase 56 / BUG-03: zero-trade folds with
///   `profit_factor = inf` are excluded from Gate-D counting)
/// - `h1_mean_pip` / `h2_mean_pip` from a midpoint split of `full_pnl`
/// - `median_fold_ci_low` / `median_fold_ci_high` from per-fold
///   `stationary_bootstrap_ci`, sorted by `ci_low`, middle index
///
/// `abs_t_stat`, `dsr_pvalue`, `mean_pip`, `round_trip_cost_pip` are passed
/// through verbatim — the caller is responsible for computing these from the
/// full backtest.
#[allow(clippy::too_many_arguments)]
pub fn compute_gate_input(
    abs_t_stat: f64,
    dsr_pvalue: f64,
    mean_pip: f64,
    round_trip_cost_pip: f64,
    fold_pnls: &[Vec<f64>],
    fold_pfs: &[f64],
    trades_per_fold: &[usize],
    full_pnl: &[f64],
    bootstrap_n: usize,
    bootstrap_seed: u64,
) -> GateInput {
    let folds_passing_oos_pf = fold_pfs
        .iter()
        .zip(trades_per_fold.iter())
        .filter(|(&pf, &t)| pf >= 2.0 && t >= MIN_TRADES_PER_FOLD)
        .count();

    let n = full_pnl.len();
    let (h1_mean_pip, h2_mean_pip) = if n >= 2 {
        let mid = n / 2;
        let h1 = full_pnl[..mid].iter().sum::<f64>() / mid.max(1) as f64;
        let h2 = full_pnl[mid..].iter().sum::<f64>() / (n - mid).max(1) as f64;
        (h1, h2)
    } else {
        (0.0, 0.0)
    };

    let mut fold_cis: Vec<(f64, f64)> = fold_pnls
        .iter()
        .map(|pnl| stationary_bootstrap_ci(pnl, bootstrap_n, bootstrap_seed))
        .collect();
    fold_cis.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    let (median_fold_ci_low, median_fold_ci_high) = if fold_cis.is_empty() {
        (0.0, 0.0)
    } else {
        fold_cis[fold_cis.len() / 2]
    };

    GateInput {
        abs_t_stat,
        dsr_pvalue,
        mean_pip,
        round_trip_cost_pip,
        folds_passing_oos_pf,
        h1_mean_pip,
        h2_mean_pip,
        median_fold_ci_low,
        median_fold_ci_high,
        trades_per_fold: trades_per_fold.to_vec(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Phase 56: zero-trade false-pass bug fix (BUG-03).
    ///
    /// Verifies the filter-layer fix: `compute_gate_input` now rejects folds
    /// with `trades < MIN_TRADES_PER_FOLD`, so inf-PF zero-trade folds no longer
    /// sneak through Gate-D. End-to-end path:
    ///   `fold_pfs=inf + trades_per_fold=0 → folds_passing_oos_pf=0 → Gate-D fail → Verdict::Fail`
    #[test]
    fn rejects_zero_trade_pass() {
        // ---------------------------------------------------------------
        // Auxiliary assert — Phase 56 fix: t=0 folds must NOT count.
        // ---------------------------------------------------------------
        let aux_input = compute_gate_input(
            /* abs_t_stat            = */ 5.0,
            /* dsr_pvalue            = */ 0.01,
            /* mean_pip              = */ 1.0,
            /* round_trip_cost_pip   = */ 0.1,
            /* fold_pnls             = */ &[],
            /* fold_pfs              = */ &[f64::INFINITY; 5],
            /* trades_per_fold       = */ &[0usize; 5],
            /* full_pnl              = */ &[],
            /* bootstrap_n           = */ 0,
            /* bootstrap_seed        = */ 0,
        );
        assert_eq!(
            aux_input.folds_passing_oos_pf, 0,
            "Phase 56 fix: t=0 folds must not count even if pf=inf (BUG-03)"
        );

        // ---------------------------------------------------------------
        // Primary assert — end-to-end filter-layer path (D-08 compliant).
        // ---------------------------------------------------------------
        //
        // `compute_gate_input` with fold_pfs=inf and trades=0 produces
        // `folds_passing_oos_pf = 0`, which causes Gate-D (requires >= 4) to fail,
        // resulting in Verdict::Fail. No trade-count inspection in six_gate_verdict.
        let computed = compute_gate_input(
            5.0,
            0.01,
            1.0,
            0.1,
            &[],
            &[f64::INFINITY; 5],
            &[0usize; 5],
            &[],
            0,
            0,
        );
        let verdict = six_gate_verdict(&computed, PassMode::Strict);
        assert!(
            matches!(verdict.verdict, VerdictKind::Fail { .. }),
            "zero-trade input must Fail (BUG-03 fix: folds_passing_oos_pf should drop to 0), got {:?}",
            verdict.verdict
        );
    }

    /// Phase 56 (D-11): boundary regression — `trades_per_fold = 1` (== MIN_TRADES_PER_FOLD)
    /// must count toward Gate-D, and `trades_per_fold = 0` must be excluded.
    #[test]
    fn accepts_min_one_trade_per_fold() {
        // Auxiliary 1: boundary t=1 passes
        let gi_min = compute_gate_input(
            /* abs_t_stat            = */ 5.0,
            /* dsr_pvalue            = */ 0.01,
            /* mean_pip              = */ 1.0,
            /* round_trip_cost_pip   = */ 0.1,
            /* fold_pnls             = */ &[],
            /* fold_pfs              = */ &[3.0f64; 5],
            /* trades_per_fold       = */ &[1usize; 5],
            /* full_pnl              = */ &[],
            /* bootstrap_n           = */ 0,
            /* bootstrap_seed        = */ 0,
        );
        assert_eq!(
            gi_min.folds_passing_oos_pf, 5,
            "t=1 (>=MIN_TRADES_PER_FOLD) must count"
        );
        assert_eq!(gi_min.trades_per_fold, vec![1usize; 5]);

        // Auxiliary 2: mixed boundary — one t=0 must be filtered
        let gi_mixed = compute_gate_input(
            5.0,
            0.01,
            1.0,
            0.1,
            &[],
            &[3.0f64; 5],
            &[0usize, 1, 1, 1, 1],
            &[],
            0,
            0,
        );
        assert_eq!(
            gi_mixed.folds_passing_oos_pf, 4,
            "t=0 fold must be excluded"
        );

        // Primary: hand-built GateInput with all 6 gates satisfied + trades_per_fold=[1;5]
        // must return Verdict::Pass
        let input = GateInput {
            abs_t_stat: 5.0,
            dsr_pvalue: 0.01,
            mean_pip: 1.0,
            round_trip_cost_pip: 0.1,
            folds_passing_oos_pf: 5,
            h1_mean_pip: 1.20,
            h2_mean_pip: 0.80,
            median_fold_ci_low: 0.05,
            median_fold_ci_high: 0.95,
            trades_per_fold: vec![1usize; 5],
        };
        let v = six_gate_verdict(&input, PassMode::Strict);
        assert!(
            matches!(v.verdict, VerdictKind::Pass),
            "min trade boundary must Pass, got {:?}",
            v.verdict
        );
    }
}
