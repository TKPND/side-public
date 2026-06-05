use std::collections::HashMap;

use rand::{Rng, RngExt};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use statrs::distribution::{ContinuousCDF, Normal};

use crate::wfd::{self, ExitConfig};

use super::optimizer::Trial;

// ---------------------------------------------------------------------------
// Result types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MonteCarloResult {
    pub mean_pf: f64,
    pub std_pf: f64,
    pub p5_pf: f64,
    pub p95_pf: f64,
    pub cliff_detected: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RandomBenchResult {
    pub strategy_pf: f64,
    pub random_mean_pf: f64,
    pub random_p95_pf: f64,
    pub percentile_rank: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlateauResult {
    pub plateau_score: f64,
    pub n_neighbors: usize,
    pub neighbor_pf_mean: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CommitGateResult {
    pub checks: HashMap<String, bool>,
    pub majority_pass: bool,
    pub catastrophic_veto: bool,
    pub approved: bool,
}

// ---------------------------------------------------------------------------
// Deflated Sharpe Ratio (Bailey & Lopez de Prado)
// ---------------------------------------------------------------------------

/// Returns p-value. < 0.05 means statistically significant.
pub fn deflated_sharpe_ratio(
    observed_sr: f64,
    n_trials: usize,
    skewness: f64,
    kurtosis: f64,
    t_periods: usize,
) -> f64 {
    if n_trials <= 1 || t_periods <= 1 {
        return 1.0;
    }

    let normal = Normal::new(0.0, 1.0).unwrap();
    let euler_mascheroni: f64 = 0.5772156649015329;
    let n = n_trials as f64;

    // Expected maximum SR from n independent trials
    let e_max_sr = (1.0 - euler_mascheroni) * normal.inverse_cdf(1.0 - 1.0 / n)
        + euler_mascheroni * normal.inverse_cdf(1.0 - 1.0 / (n * std::f64::consts::E));

    // SR standard deviation (adjusted for skewness and kurtosis)
    let t = t_periods as f64;
    let variance = (1.0 - skewness * observed_sr
        + (kurtosis - 1.0) / 4.0 * observed_sr * observed_sr)
        / (t - 1.0);
    let sr_std = variance.max(0.0).sqrt();

    if sr_std <= 0.0 {
        return 0.0;
    }

    let z = (observed_sr - e_max_sr) / sr_std;
    1.0 - normal.cdf(z)
}

// ---------------------------------------------------------------------------
// Monte Carlo perturbation
// ---------------------------------------------------------------------------

/// Generate perturbed parameters by ±perturbation_pct.
fn generate_perturbed_params(
    base_params: &HashMap<String, Value>,
    n_simulations: usize,
    perturbation_pct: f64,
    rng: &mut impl Rng,
) -> Vec<HashMap<String, Value>> {
    (0..n_simulations)
        .map(|_| {
            let mut perturbed = HashMap::new();
            for (k, v) in base_params {
                let new_v = match v {
                    Value::Number(n) => {
                        if let Some(i) = n.as_i64() {
                            let delta = (i.unsigned_abs() as f64 * perturbation_pct)
                                .max(1.0)
                                .round() as i64;
                            let new_i = (i + rng.random_range(-delta..=delta)).max(1);
                            Value::from(new_i)
                        } else if let Some(f) = n.as_f64() {
                            let new_f =
                                f * (1.0 + rng.random_range(-perturbation_pct..=perturbation_pct));
                            Value::from(new_f)
                        } else {
                            v.clone()
                        }
                    }
                    _ => v.clone(), // bool, string, etc. unchanged
                };
                perturbed.insert(k.clone(), new_v);
            }
            perturbed
        })
        .collect()
}

/// Run Monte Carlo perturbation analysis.
/// Returns statistics of PF distribution across perturbed parameters.
#[allow(clippy::too_many_arguments)]
pub fn monte_carlo_perturbation(
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
    datetimes_ns: &[i64],
    aux_close: Option<&[f64]>,
    strategy_name: &str,
    base_params: &HashMap<String, Value>,
    timeframe: &str,
    exit_config: Option<&ExitConfig>,
    mode: i8,
    n_simulations: usize,
    perturbation_pct: f64,
    rng: &mut impl Rng,
    fee_bps: f64,
) -> MonteCarloResult {
    let perturbed_list =
        generate_perturbed_params(base_params, n_simulations, perturbation_pct, rng);

    let pf_values = wfd::run_backtest_batch(
        open,
        high,
        low,
        close,
        volume,
        datetimes_ns,
        aux_close,
        &perturbed_list,
        strategy_name,
        timeframe,
        exit_config,
        mode,
        fee_bps,
    );

    let pfs: Vec<f64> = pf_values
        .into_iter()
        .filter(|&pf| pf != f64::INFINITY && pf.is_finite())
        .collect();

    if pfs.is_empty() {
        return MonteCarloResult {
            mean_pf: 0.0,
            std_pf: 0.0,
            p5_pf: 0.0,
            p95_pf: 0.0,
            cliff_detected: false,
        };
    }

    let mean_pf = pfs.iter().sum::<f64>() / pfs.len() as f64;
    let var = pfs.iter().map(|x| (x - mean_pf).powi(2)).sum::<f64>() / pfs.len() as f64;
    let std_pf = var.sqrt();

    let mut sorted = pfs.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let p5_pf = percentile_sorted(&sorted, 5.0);
    let p95_pf = percentile_sorted(&sorted, 95.0);

    let cliff_detected = if mean_pf > 0.0 {
        std_pf / mean_pf > 0.5
    } else {
        false
    };

    MonteCarloResult {
        mean_pf,
        std_pf,
        p5_pf,
        p95_pf,
        cliff_detected,
    }
}

// ---------------------------------------------------------------------------
// Random entry benchmark
// ---------------------------------------------------------------------------

/// Compare strategy vs random entries.
#[allow(clippy::too_many_arguments)]
pub fn random_entry_benchmark(
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
    datetimes_ns: &[i64],
    aux_close: Option<&[f64]>,
    strategy_name: &str,
    params: &HashMap<String, Value>,
    timeframe: &str,
    exit_config: Option<&ExitConfig>,
    mode: i8,
    n_random: usize,
    seed: u64,
    fee_bps: f64,
) -> RandomBenchResult {
    let (strategy_pf, random_pfs) = wfd::run_random_benchmark(
        open,
        high,
        low,
        close,
        volume,
        datetimes_ns,
        aux_close,
        strategy_name,
        params,
        n_random,
        timeframe,
        exit_config,
        mode,
        seed,
        fee_bps,
    );

    if random_pfs.is_empty() {
        return RandomBenchResult {
            strategy_pf,
            random_mean_pf: 0.0,
            random_p95_pf: 0.0,
            percentile_rank: 100.0,
        };
    }

    let random_mean_pf = random_pfs.iter().sum::<f64>() / random_pfs.len() as f64;
    let mut sorted = random_pfs;
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let random_p95_pf = percentile_sorted(&sorted, 95.0);

    // Percentile rank: % of random PFs that are <= strategy PF
    let n_below = sorted.iter().filter(|&&pf| pf <= strategy_pf).count();
    let percentile_rank = n_below as f64 / sorted.len() as f64 * 100.0;

    RandomBenchResult {
        strategy_pf,
        random_mean_pf,
        random_p95_pf,
        percentile_rank,
    }
}

// ---------------------------------------------------------------------------
// Parameter plateau check
// ---------------------------------------------------------------------------

/// Check if the candidate trial sits on a parameter plateau.
pub fn check_parameter_plateau(
    candidate: &Trial,
    all_trials: &[Trial],
    plateau_pct: f64,
) -> PlateauResult {
    if candidate.values.is_empty() {
        return PlateauResult {
            plateau_score: 0.0,
            n_neighbors: 0,
            neighbor_pf_mean: 0.0,
        };
    }

    let candidate_pf = candidate.values[0]; // first objective = PF

    // Collect numeric params from candidate
    let numeric_params: Vec<(&String, f64)> = candidate
        .params
        .iter()
        .filter_map(|(k, v)| v.as_f64().map(|f| (k, f)))
        .collect();

    if numeric_params.is_empty() {
        return PlateauResult {
            plateau_score: 0.0,
            n_neighbors: 0,
            neighbor_pf_mean: 0.0,
        };
    }

    let mut neighbors = Vec::new();

    for trial in all_trials {
        if trial.id == candidate.id {
            continue;
        }
        if trial.values.is_empty() {
            continue;
        }

        let mut in_range = true;
        for &(k, v) in &numeric_params {
            let t_val = match trial.params.get(k).and_then(|x| x.as_f64()) {
                Some(f) => f,
                None => {
                    in_range = false;
                    break;
                }
            };

            let within = if v.abs() > 1e-9 {
                ((t_val - v) / v).abs() <= plateau_pct
            } else {
                (t_val - v).abs() <= plateau_pct
            };
            if !within {
                in_range = false;
                break;
            }
        }

        if in_range {
            neighbors.push(trial.values[0]); // PF
        }
    }

    if neighbors.is_empty() {
        return PlateauResult {
            plateau_score: 0.0,
            n_neighbors: 0,
            neighbor_pf_mean: 0.0,
        };
    }

    let neighbor_pf_mean = neighbors.iter().sum::<f64>() / neighbors.len() as f64;
    let base = if candidate_pf > 0.0 {
        candidate_pf
    } else {
        1e-9
    };
    let plateau_score = (neighbor_pf_mean / base).min(1.0);

    PlateauResult {
        plateau_score,
        n_neighbors: neighbors.len(),
        neighbor_pf_mean,
    }
}

// ---------------------------------------------------------------------------
// Commit gate
// ---------------------------------------------------------------------------

#[allow(clippy::too_many_arguments)]
pub fn commit_gate(
    wfd_pass: bool,
    dsr_pvalue: f64,
    mc: &MonteCarloResult,
    rb: &RandomBenchResult,
    plateau: &PlateauResult,
    oos_max_dd: f64,
    oos_pf: f64,
) -> CommitGateResult {
    let mut checks = HashMap::new();
    checks.insert("wfd_pass".to_string(), wfd_pass);
    checks.insert("dsr_significant".to_string(), dsr_pvalue < 0.05);
    checks.insert("mc_robust".to_string(), !mc.cliff_detected);
    checks.insert("random_beats".to_string(), rb.percentile_rank >= 95.0);
    checks.insert("plateau_ok".to_string(), plateau.plateau_score >= 0.5);

    let pass_count = checks.values().filter(|&&v| v).count();
    let majority_pass = pass_count >= 3;
    // Catastrophic veto: trades that lose money OR draw down too hard cannot be approved,
    // regardless of how many robustness gates pass.
    let catastrophic_veto = oos_max_dd < -0.40 || oos_pf < 1.0;

    CommitGateResult {
        checks,
        majority_pass,
        catastrophic_veto,
        approved: majority_pass && !catastrophic_veto,
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn percentile_sorted(sorted: &[f64], pct: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = (pct / 100.0 * (sorted.len() - 1) as f64).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use rand::SeedableRng;

    // DSR: cross-validate with Python scipy.stats.norm
    // Python code:
    //   from scipy.stats import norm
    //   import numpy as np
    //   euler = 0.5772156649015329
    //   def dsr(sr, n, skew, kurt, t):
    //       e_max = (1-euler)*norm.ppf(1-1/n) + euler*norm.ppf(1-1/(n*np.e))
    //       var = (1 - skew*sr + (kurt-1)/4*sr**2)/(t-1)
    //       z = (sr - e_max)/np.sqrt(max(0,var))
    //       return 1 - norm.cdf(z)

    #[test]
    fn test_dsr_known_values() {
        // Case 1: moderate SR, many trials
        let p = deflated_sharpe_ratio(1.5, 200, 0.0, 3.0, 252);
        // With 200 trials, E[max SR] is high, so observed 1.5 should NOT be significant
        assert!(p > 0.05, "p={p}, expected > 0.05 for SR=1.5, n=200");

        // Case 2: high SR, few trials
        let p = deflated_sharpe_ratio(3.0, 5, 0.0, 3.0, 252);
        // With only 5 trials, SR=3.0 should be significant
        assert!(p < 0.05, "p={p}, expected < 0.05 for SR=3.0, n=5");

        // Case 3: trivial case
        let p = deflated_sharpe_ratio(0.0, 100, 0.0, 3.0, 252);
        assert!(p > 0.5, "p={p}, SR=0 should be very non-significant");

        // Case 4: edge — n_trials=1 returns 1.0
        let p = deflated_sharpe_ratio(2.0, 1, 0.0, 3.0, 252);
        assert_eq!(p, 1.0);
    }

    #[test]
    fn test_dsr_skew_kurtosis_effect() {
        // Positive skewness makes DSR harder to pass (inflated SR)
        let p_normal = deflated_sharpe_ratio(2.0, 50, 0.0, 3.0, 252);
        let p_skewed = deflated_sharpe_ratio(2.0, 50, 1.0, 3.0, 252);
        // Both should differ (skewness affects variance adjustment)
        assert!(
            (p_normal - p_skewed).abs() > 1e-6,
            "skewness should affect DSR: normal={p_normal}, skewed={p_skewed}"
        );
    }

    #[test]
    fn test_perturbation_ranges() {
        let mut rng = rand::rngs::StdRng::seed_from_u64(42);
        let base = HashMap::from([
            ("period".to_string(), Value::from(20)),
            ("mult".to_string(), Value::from(1.5)),
            ("flag".to_string(), Value::from(true)),
        ]);

        let perturbed = generate_perturbed_params(&base, 100, 0.20, &mut rng);
        assert_eq!(perturbed.len(), 100);

        for p in &perturbed {
            // int param: 20 ± max(1, 20*0.2) = 20 ± 4
            let period = p["period"].as_i64().unwrap();
            assert!(period >= 1, "period should be >= 1, got {period}");

            // float param: 1.5 * (1 ± 0.2) = [1.2, 1.8]
            let mult = p["mult"].as_f64().unwrap();
            assert!(
                (1.2..=1.8).contains(&mult),
                "mult should be in [1.2, 1.8], got {mult}"
            );

            // bool unchanged
            assert_eq!(p["flag"], Value::from(true));
        }
    }

    #[test]
    fn test_plateau_detection() {
        use super::super::optimizer::TrialState;

        let candidate = Trial {
            id: 0,
            params: HashMap::from([
                ("period".to_string(), Value::from(20)),
                ("mult".to_string(), Value::from(1.5)),
            ]),
            exit_config: None,
            exit_meta: HashMap::new(),
            state: TrialState::Complete,
            values: vec![2.0], // PF = 2.0
            user_attrs: HashMap::new(),
        };

        // Create neighbors: 3 within ±10%, 2 outside
        let trials = vec![
            Trial {
                id: 1,
                params: HashMap::from([
                    ("period".to_string(), Value::from(21)), // 20*1.05
                    ("mult".to_string(), Value::from(1.55)),
                ]),
                exit_config: None,
                exit_meta: HashMap::new(),
                state: TrialState::Complete,
                values: vec![1.9],
                user_attrs: HashMap::new(),
            },
            Trial {
                id: 2,
                params: HashMap::from([
                    ("period".to_string(), Value::from(19)),
                    ("mult".to_string(), Value::from(1.45)),
                ]),
                exit_config: None,
                exit_meta: HashMap::new(),
                state: TrialState::Complete,
                values: vec![2.1],
                user_attrs: HashMap::new(),
            },
            Trial {
                id: 3,
                params: HashMap::from([
                    ("period".to_string(), Value::from(22)), // 20*1.10
                    ("mult".to_string(), Value::from(1.65)), // 1.5*1.10
                ]),
                exit_config: None,
                exit_meta: HashMap::new(),
                state: TrialState::Complete,
                values: vec![1.8],
                user_attrs: HashMap::new(),
            },
            Trial {
                id: 4,
                params: HashMap::from([
                    ("period".to_string(), Value::from(30)), // outside
                    ("mult".to_string(), Value::from(2.5)),
                ]),
                exit_config: None,
                exit_meta: HashMap::new(),
                state: TrialState::Complete,
                values: vec![0.5],
                user_attrs: HashMap::new(),
            },
        ];

        let result = check_parameter_plateau(&candidate, &trials, 0.10);
        assert!(result.n_neighbors >= 2, "should find neighbors near ±10%");
        assert!(
            result.plateau_score > 0.0,
            "plateau_score should be > 0 with neighbors"
        );
    }

    #[test]
    fn test_commit_gate_majority() {
        let mc_good = MonteCarloResult {
            mean_pf: 2.0,
            std_pf: 0.3,
            p5_pf: 1.5,
            p95_pf: 2.5,
            cliff_detected: false,
        };
        let rb_good = RandomBenchResult {
            strategy_pf: 2.0,
            random_mean_pf: 1.0,
            random_p95_pf: 1.3,
            percentile_rank: 97.0,
        };
        let plateau_good = PlateauResult {
            plateau_score: 0.8,
            n_neighbors: 5,
            neighbor_pf_mean: 1.8,
        };

        // All pass
        let result = commit_gate(true, 0.01, &mc_good, &rb_good, &plateau_good, -0.15, 2.0);
        assert!(result.approved);
        assert!(result.majority_pass);
        assert!(!result.catastrophic_veto);

        // Catastrophic veto: MaxDD > 40%
        let result = commit_gate(true, 0.01, &mc_good, &rb_good, &plateau_good, -0.45, 2.0);
        assert!(!result.approved);
        assert!(result.catastrophic_veto);

        // Only 2/5 pass → not majority
        let mc_bad = MonteCarloResult {
            cliff_detected: true,
            ..mc_good.clone()
        };
        let rb_bad = RandomBenchResult {
            percentile_rank: 50.0,
            ..rb_good.clone()
        };
        let plateau_bad = PlateauResult {
            plateau_score: 0.1,
            ..plateau_good.clone()
        };
        let result = commit_gate(true, 0.01, &mc_bad, &rb_bad, &plateau_bad, -0.15, 2.0);
        // wfd_pass=true, dsr=true, mc=false, random=false, plateau=false → 2/5
        assert!(!result.majority_pass);
        assert!(!result.approved);
    }

    #[test]
    fn commit_gate_rejects_loser_despite_robust_passes() {
        // Bug repro: commit_gate currently approves PF<1.0 strategies as long as
        // 3/5 robust gates pass. Real case from data/scan-tod-drift-1m-fee1bps-260408.json:
        // USDJPY 1m fee_bps=1.0 PF=0.75 was approved=true.
        //
        // After fix: oos_pf<1.0 must trigger catastrophic_veto regardless of other gates.
        let mc_good = MonteCarloResult {
            mean_pf: 1.0,
            std_pf: 0.1,
            p5_pf: 0.9,
            p95_pf: 1.1,
            cliff_detected: false,
        };
        let rb_good = RandomBenchResult {
            strategy_pf: 0.75,
            random_mean_pf: 0.5,
            random_p95_pf: 0.7,
            percentile_rank: 97.0, // beats random
        };
        let plateau_good = PlateauResult {
            plateau_score: 0.8,
            n_neighbors: 5,
            neighbor_pf_mean: 0.7,
        };

        // wfd_pass=false, dsr_p=1.0 (not significant) → 2 fail
        // mc_robust=true, random_beats=true, plateau_ok=true → 3 pass
        // majority_pass=true, oos_max_dd=-0.05 (no DD veto)
        // Bug: approved=true even though strategy is a loser (PF=0.75)
        // Fix: oos_pf<1.0 → catastrophic_veto → approved=false
        let result = commit_gate(false, 1.0, &mc_good, &rb_good, &plateau_good, -0.05, 0.75);

        assert!(
            !result.approved,
            "PF<1.0 loser must be rejected even with 3/5 robust gates passing"
        );
        assert!(
            result.catastrophic_veto,
            "oos_pf<1.0 should trigger catastrophic_veto"
        );
    }

    #[test]
    fn test_percentile_sorted() {
        let data = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        assert_eq!(percentile_sorted(&data, 0.0), 1.0);
        assert_eq!(percentile_sorted(&data, 50.0), 3.0);
        assert_eq!(percentile_sorted(&data, 100.0), 5.0);
    }
}
