pub mod macro_event;
pub use macro_event::{
    macro_event_slots, macro_event_wfd_config, run_fomc_event_fee_sweep, run_macro_event_fee_sweep,
    run_macro_event_path, DurationBucket, FeeResult, LiquidityRegime, MacroEventSlot,
    MacroEventSlotResult, SlotReport,
};
pub mod metadata;
pub use metadata::{EventFilterStats, ScanConfigMirror, ScanMetadata, ScanOutput};
pub mod optimizer;
pub mod param_space;
pub mod robustness;

use std::collections::{BTreeMap, HashMap, HashSet};

use rand::rngs::StdRng;
use rand::Rng;
use rand::SeedableRng;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tracing::info;

use crate::strategies::Ohlcv;
use crate::wfd::{self, ExitConfig, WfdConfig};

use self::optimizer::{Direction, MultiObjectiveStudy, Trial, TrialPruned, TrialState};
use self::param_space::{load_param_spaces, StrategyParamSpace};
use self::robustness::{
    check_parameter_plateau, commit_gate, deflated_sharpe_ratio, monte_carlo_perturbation,
    random_entry_benchmark, CommitGateResult, MonteCarloResult, PlateauResult, RandomBenchResult,
};

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct ScanConfig {
    pub assets: Vec<String>,
    pub strategies: Vec<String>,
    pub timeframes: Vec<String>,
    pub n_trials: usize,
    pub batch_size: usize,
    pub mode: i8,
    pub mc_simulations: usize,
    pub random_benchmark_n: usize,
    pub wfd_config: WfdConfig,
    pub param_spaces_path: std::path::PathBuf,
    pub max_pareto_candidates: usize,
    /// When true, ScanCellResult.all_trials is populated with every trial
    /// (including pruned ones), regardless of whether they passed the
    /// commit gate. Default false to keep JSON output small.
    pub export_all_trials: bool,
}

impl Default for ScanConfig {
    fn default() -> Self {
        Self {
            assets: Vec::new(),
            strategies: Vec::new(),
            timeframes: vec!["1h".to_string()],
            n_trials: 200,
            batch_size: 32,
            mode: 1, // long_only
            mc_simulations: 100,
            random_benchmark_n: 200,
            wfd_config: WfdConfig::default(),
            param_spaces_path: std::path::PathBuf::from("config/param_spaces.json"),
            max_pareto_candidates: 3,
            export_all_trials: false,
        }
    }
}

// ---------------------------------------------------------------------------
// OHLCV owned data
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct OhlcvData {
    pub open: Vec<f64>,
    pub high: Vec<f64>,
    pub low: Vec<f64>,
    pub close: Vec<f64>,
    pub volume: Vec<f64>,
    pub datetimes_ns: Vec<i64>,
    pub aux_close: Option<Vec<f64>>,
}

impl OhlcvData {
    pub fn as_ref(&self) -> Ohlcv<'_> {
        Ohlcv {
            open: &self.open,
            high: &self.high,
            low: &self.low,
            close: &self.close,
            volume: &self.volume,
            datetimes_ns: Some(&self.datetimes_ns),
            aux_close: self.aux_close.as_deref(),
        }
    }
}

// ---------------------------------------------------------------------------
// Result types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidatedTrial {
    pub params: HashMap<String, Value>,
    pub exit_config: Option<HashMap<String, Value>>,
    pub oos_pf: f64,
    pub oos_sharpe: f64,
    pub oos_max_dd: f64,
    pub wfd_pass: bool,
    pub monte_carlo: MonteCarloResult,
    pub dsr_pvalue: f64,
    pub dsr_significant: bool,
    pub random_benchmark: RandomBenchResult,
    pub pareto_rank: usize,
    pub plateau: PlateauResult,
    pub commit_gate: CommitGateResult,
}

/// Lightweight projection of a Trial for diagnostic export.
///
/// Contains enough information to inspect every trial (including pruned
/// ones) without serializing the full Trial struct (which carries
/// debug-only state like Option<ExitConfig> and user_attrs).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrialSummary {
    pub id: usize,
    pub params: HashMap<String, Value>,
    pub exit_meta: HashMap<String, Value>,
    /// "complete" or "pruned" — kept as String for stable JSON output
    /// regardless of TrialState repr changes.
    pub state: String,
    /// Multi-objective values: [oos_pf, oos_sharpe, oos_max_dd] for completed
    /// trials, empty for pruned ones.
    pub values: Vec<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScanCellResult {
    pub asset: String,
    pub strategy: String,
    pub timeframe: String,
    pub best_trials: Vec<ValidatedTrial>,
    pub n_trials_completed: usize,
    pub n_trials_pruned: usize,
    /// Populated when ScanConfig.export_all_trials is true. Contains every
    /// trial (including pruned ones) for downstream diagnostic / hypothesis
    /// generation. Skipped from JSON when None to keep output small.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub all_trials: Option<Vec<TrialSummary>>,
}

// ---------------------------------------------------------------------------
// Composite score (pruning)
// ---------------------------------------------------------------------------

pub fn composite_score(pf: f64, sharpe: f64, max_dd: f64) -> f64 {
    let norm_pf = (pf / 3.0).clamp(0.0, 1.0);
    let norm_sr = ((sharpe + 1.0) / 4.0).clamp(0.0, 1.0);
    let norm_dd = (1.0 - max_dd.abs()).clamp(0.0, 1.0);
    0.5 * norm_pf + 0.3 * norm_sr + 0.2 * norm_dd
}

// ---------------------------------------------------------------------------
// Single cell scan
// ---------------------------------------------------------------------------

pub fn run_single_cell(
    ohlcv: &OhlcvData,
    asset: &str,
    strategy_name: &str,
    timeframe: &str,
    param_space: &StrategyParamSpace,
    config: &ScanConfig,
) -> ScanCellResult {
    let mut rng = StdRng::seed_from_u64(42);
    let directions = vec![
        Direction::Maximize,
        Direction::Maximize,
        Direction::Maximize,
    ];
    let mut study = MultiObjectiveStudy::new(directions);

    info!(
        asset = asset,
        strategy = strategy_name,
        "starting scan cell ({} trials)",
        config.n_trials
    );

    // --- Batch ask/tell loop ---
    let mut n_done = 0;
    while n_done < config.n_trials {
        let batch_size = config.batch_size.min(config.n_trials - n_done);

        // Ask
        let trials: Vec<Trial> = (0..batch_size)
            .map(|_| study.ask(param_space, &mut rng))
            .collect();

        // Group by exit_config for batch WFD
        let mut groups: HashMap<String, Vec<usize>> = HashMap::new();
        for (idx, trial) in trials.iter().enumerate() {
            let key = exit_config_key(&trial.exit_config);
            groups.entry(key).or_default().push(idx);
        }

        // Run WFD batch per group
        let mut results: Vec<Option<wfd::WfdSingleResult>> = vec![None; trials.len()];
        for indices in groups.values() {
            let group_params: Vec<HashMap<String, Value>> =
                indices.iter().map(|&i| trials[i].params.clone()).collect();

            let exit_cfg = trials[indices[0]].exit_config;

            let group_results = wfd::run_wfd_batch(
                &ohlcv.open,
                &ohlcv.high,
                &ohlcv.low,
                &ohlcv.close,
                &ohlcv.volume,
                &ohlcv.datetimes_ns,
                ohlcv.aux_close.as_deref(),
                &group_params,
                strategy_name,
                &config.wfd_config,
                timeframe,
                &exit_cfg,
                config.mode,
            );

            for (j, &idx) in indices.iter().enumerate() {
                results[idx] = Some(group_results[j].clone());
            }
        }

        // Tell
        for (trial, wfd_result) in trials.into_iter().zip(results.into_iter()) {
            let wfd_r = wfd_result.unwrap();

            // Prune: too few trades
            if wfd_r.combined_oos_trades < 5 {
                study.tell(trial, Err(TrialPruned));
                n_done += 1;
                continue;
            }

            // Prune: composite score too low
            let scores: Vec<f64> = wfd_r
                .walks
                .iter()
                .map(|w| composite_score(w.oos_pf, w.oos_sharpe, w.oos_max_dd))
                .collect();
            let avg_score = scores.iter().sum::<f64>() / scores.len().max(1) as f64;
            if scores.len() >= 2 && avg_score < 0.1 {
                study.tell(trial, Err(TrialPruned));
                n_done += 1;
                continue;
            }

            let capped_pf = wfd_r.combined_oos_pf.min(10.0);
            study.tell(
                trial,
                Ok(vec![
                    capped_pf,
                    wfd_r.combined_oos_sharpe,
                    wfd_r.combined_oos_max_dd,
                ]),
            );
            n_done += 1;
        }
    }

    // --- Pareto selection ---
    let front = study.pareto_front();
    let mut candidates: Vec<&Trial> = front
        .into_iter()
        .filter(|t| t.values[2] >= -0.25) // MaxDD >= -25%
        .collect();
    candidates.sort_by(|a, b| {
        b.values[0]
            .partial_cmp(&a.values[0])
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    // Drop duplicate (params, exit_meta) entries so the top-N reflects real
    // diversity rather than the same config landing in rank 1-3 (see Stage 1
    // 1m/5m time_of_day_drift symptom: 3 approved trials all duplicate).
    candidates = dedup_trials_by_key(candidates);
    candidates.truncate(config.max_pareto_candidates);

    info!(
        asset = asset,
        strategy = strategy_name,
        "pareto selection: {} candidates from {} trials",
        candidates.len(),
        study.n_trials()
    );

    // --- Robustness validation ---
    let all_trials = study.trials();
    let t_periods = ohlcv.close.len();
    let n_total_trials = study.n_trials();

    let mut validated = Vec::new();
    for (rank, candidate) in candidates.iter().enumerate() {
        // Monte Carlo
        let mc = monte_carlo_perturbation(
            &ohlcv.open,
            &ohlcv.high,
            &ohlcv.low,
            &ohlcv.close,
            &ohlcv.volume,
            &ohlcv.datetimes_ns,
            ohlcv.aux_close.as_deref(),
            strategy_name,
            &candidate.params,
            timeframe,
            candidate.exit_config.as_ref(),
            config.mode,
            config.mc_simulations,
            0.20,
            &mut rng,
            config.wfd_config.fee_bps,
        );

        // WFD re-run for final validation
        let wfd_r = wfd::run_wfd_single(
            &ohlcv.open,
            &ohlcv.high,
            &ohlcv.low,
            &ohlcv.close,
            &ohlcv.volume,
            &ohlcv.datetimes_ns,
            ohlcv.aux_close.as_deref(),
            strategy_name,
            &candidate.params,
            &config.wfd_config,
            timeframe,
            candidate.exit_config.as_ref(),
            config.mode,
        );

        // DSR
        let dsr_p = deflated_sharpe_ratio(
            candidate.values[1], // oos_sharpe
            n_total_trials,
            0.0, // skewness (simplified: assume symmetric)
            3.0, // kurtosis (simplified: assume normal)
            t_periods,
        );

        // Random entry benchmark
        let seed = rng.random::<u64>();
        let rb = random_entry_benchmark(
            &ohlcv.open,
            &ohlcv.high,
            &ohlcv.low,
            &ohlcv.close,
            &ohlcv.volume,
            &ohlcv.datetimes_ns,
            ohlcv.aux_close.as_deref(),
            strategy_name,
            &candidate.params,
            timeframe,
            candidate.exit_config.as_ref(),
            config.mode,
            config.random_benchmark_n,
            seed,
            config.wfd_config.fee_bps,
        );

        // Plateau
        let plateau = check_parameter_plateau(candidate, all_trials, 0.10);

        // Commit gate
        let gate = commit_gate(
            wfd_r.passed,
            dsr_p,
            &mc,
            &rb,
            &plateau,
            candidate.values[2], // oos_max_dd
            candidate.values[0], // oos_pf
        );

        let exit_meta = if candidate.exit_meta.is_empty() {
            None
        } else {
            Some(candidate.exit_meta.clone())
        };

        validated.push(ValidatedTrial {
            params: candidate.params.clone(),
            exit_config: exit_meta,
            oos_pf: candidate.values[0],
            oos_sharpe: candidate.values[1],
            oos_max_dd: candidate.values[2],
            wfd_pass: wfd_r.passed,
            monte_carlo: mc,
            dsr_pvalue: dsr_p,
            dsr_significant: dsr_p < 0.05,
            random_benchmark: rb,
            pareto_rank: rank + 1,
            plateau,
            commit_gate: gate,
        });
    }

    let n_pruned = all_trials
        .iter()
        .filter(|t| t.state == TrialState::Pruned)
        .count();

    let exported = if config.export_all_trials {
        Some(summarize_trials(all_trials))
    } else {
        None
    };

    ScanCellResult {
        asset: asset.to_string(),
        strategy: strategy_name.to_string(),
        timeframe: timeframe.to_string(),
        best_trials: validated,
        n_trials_completed: n_total_trials,
        n_trials_pruned: n_pruned,
        all_trials: exported,
    }
}

// ---------------------------------------------------------------------------
// Full scan
// ---------------------------------------------------------------------------

pub fn run_scan(
    config: &ScanConfig,
    data: &HashMap<String, OhlcvData>,
) -> Result<Vec<ScanCellResult>, anyhow::Error> {
    let param_spaces = load_param_spaces(&config.param_spaces_path)?;

    let strategies: Vec<&str> = if config.strategies.is_empty() {
        param_spaces.keys().map(|s| s.as_str()).collect()
    } else {
        config.strategies.iter().map(|s| s.as_str()).collect()
    };

    let mut results = Vec::new();

    for asset in &config.assets {
        let ohlcv = match data.get(asset) {
            Some(d) => d,
            None => {
                info!(asset = asset.as_str(), "skipping — no data");
                continue;
            }
        };

        for strategy in &strategies {
            let space = match param_spaces.get(*strategy) {
                Some(s) => s,
                None => {
                    info!(strategy = *strategy, "skipping — no param space");
                    continue;
                }
            };

            for tf in &config.timeframes {
                let result = run_single_cell(ohlcv, asset, strategy, tf, space, config);
                info!(
                    asset = asset.as_str(),
                    strategy = *strategy,
                    timeframe = tf.as_str(),
                    approved = result.best_trials.iter().any(|t| t.commit_gate.approved),
                    "cell complete"
                );
                results.push(result);
            }
        }
    }

    Ok(results)
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn exit_config_key(ec: &Option<ExitConfig>) -> String {
    match ec {
        None => "none".to_string(),
        Some(ec) => {
            if !ec.sl_pct.is_nan() {
                format!("fixed_{:.4}_{:.4}", ec.sl_pct, ec.tp_pct)
            } else if !ec.sl_atr.is_nan() {
                format!("atr_{:.4}_{:.4}", ec.sl_atr, ec.tp_atr)
            } else {
                "none".to_string()
            }
        }
    }
}

/// Build a deterministic dedup key from a trial's params and exit_meta.
///
/// HashMap iteration order is non-deterministic, so we project into a sorted
/// BTreeMap before serializing. This ensures two trials with the same logical
/// params produce the same key regardless of insertion order.
fn trial_dedup_key(t: &Trial) -> String {
    let params_sorted: BTreeMap<&String, &Value> = t.params.iter().collect();
    let meta_sorted: BTreeMap<&String, &Value> = t.exit_meta.iter().collect();
    let p = serde_json::to_string(&params_sorted).unwrap_or_default();
    let m = serde_json::to_string(&meta_sorted).unwrap_or_default();
    format!("{p}|{m}")
}

/// Drop trials whose (params, exit_meta) duplicate an earlier entry.
/// Order of the input vec is preserved for the kept trials.
fn dedup_trials_by_key(trials: Vec<&Trial>) -> Vec<&Trial> {
    let mut seen: HashSet<String> = HashSet::new();
    trials
        .into_iter()
        .filter(|t| seen.insert(trial_dedup_key(t)))
        .collect()
}

/// Project a slice of Trials into TrialSummary records suitable for JSON
/// export. State is rendered as a stable lowercase string ("complete" or
/// "pruned") to insulate downstream consumers from TrialState repr changes.
fn summarize_trials(trials: &[Trial]) -> Vec<TrialSummary> {
    trials
        .iter()
        .map(|t| TrialSummary {
            id: t.id,
            params: t.params.clone(),
            exit_meta: t.exit_meta.clone(),
            state: match t.state {
                TrialState::Complete => "complete".to_string(),
                TrialState::Pruned => "pruned".to_string(),
            },
            values: t.values.clone(),
        })
        .collect()
}

// ---------------------------------------------------------------------------
// WfdConfig defaults for scanner
// ---------------------------------------------------------------------------

impl Default for WfdConfig {
    fn default() -> Self {
        Self {
            is_months: 6,
            oos_months: 2,
            num_walks: 5,
            min_oos_pf: 1.0,
            min_annual_trades: 10,
            min_wfe: 0.0,
            min_oos_win_rate: 0.0,
            max_oos_drawdown: -0.25,
            fee_bps: 1.0,
            cv_mode: crate::wfd::CvMode::PurgedKFold {
                k: 5,
                embargo_days: 1,
            },
        }
    }
}

impl Clone for WfdConfig {
    fn clone(&self) -> Self {
        Self {
            is_months: self.is_months,
            oos_months: self.oos_months,
            num_walks: self.num_walks,
            min_oos_pf: self.min_oos_pf,
            min_annual_trades: self.min_annual_trades,
            min_wfe: self.min_wfe,
            min_oos_win_rate: self.min_oos_win_rate,
            max_oos_drawdown: self.max_oos_drawdown,
            fee_bps: self.fee_bps,
            cv_mode: self.cv_mode.clone(),
        }
    }
}

impl std::fmt::Debug for WfdConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WfdConfig")
            .field("is_months", &self.is_months)
            .field("oos_months", &self.oos_months)
            .field("num_walks", &self.num_walks)
            .field("min_oos_pf", &self.min_oos_pf)
            .field("fee_bps", &self.fee_bps)
            .field("cv_mode", &self.cv_mode)
            .finish()
    }
}

// WfdSingleResult needs Clone for batch results
impl Clone for wfd::WfdSingleResult {
    fn clone(&self) -> Self {
        Self {
            combined_oos_pf: self.combined_oos_pf,
            combined_oos_sharpe: self.combined_oos_sharpe,
            combined_oos_trades: self.combined_oos_trades,
            combined_oos_max_dd: self.combined_oos_max_dd,
            mean_wfe: self.mean_wfe,
            oos_win_rate: self.oos_win_rate,
            passed: self.passed,
            walks: self.walks.clone(),
            dsr_pvalue: self.dsr_pvalue,
            dsr_n_trials: self.dsr_n_trials,
        }
    }
}

impl Clone for wfd::WalkResult {
    fn clone(&self) -> Self {
        Self {
            walk_id: self.walk_id,
            is_pf: self.is_pf,
            oos_pf: self.oos_pf,
            oos_sharpe: self.oos_sharpe,
            oos_max_dd: self.oos_max_dd,
            oos_trades: self.oos_trades,
            oos_gross_profit: self.oos_gross_profit,
            oos_gross_loss: self.oos_gross_loss,
            wfe: self.wfe,
            is_equity_curve: self.is_equity_curve.clone(),
            oos_equity_curve: self.oos_equity_curve.clone(),
            oos_start_bar: self.oos_start_bar,
            oos_end_bar: self.oos_end_bar,
            pnl_ci_low: self.pnl_ci_low,
            pnl_ci_high: self.pnl_ci_high,
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_composite_score() {
        // PF=3 → 1.0, SR=3 → 1.0, DD=0 → 1.0
        let s = composite_score(3.0, 3.0, 0.0);
        assert!(
            (s - 1.0).abs() < 1e-6,
            "perfect score should be 1.0, got {s}"
        );

        // PF=0, SR=-1, DD=-1
        let s = composite_score(0.0, -1.0, -1.0);
        assert!(
            (s - 0.0).abs() < 1e-6,
            "worst score should be ~0.0, got {s}"
        );

        // PF=1.5, SR=1.0, DD=-0.15
        let s = composite_score(1.5, 1.0, -0.15);
        let expected = 0.5 * (1.5 / 3.0) + 0.3 * (2.0 / 4.0) + 0.2 * 0.85;
        assert!((s - expected).abs() < 1e-6, "expected {expected}, got {s}");
    }

    #[test]
    fn test_exit_config_key() {
        assert_eq!(exit_config_key(&None), "none");

        let ec_fixed = ExitConfig {
            sl_pct: 0.02,
            tp_pct: 0.05,
            sl_atr: f64::NAN,
            tp_atr: f64::NAN,
            atr_period: 14,
        };
        let key = exit_config_key(&Some(ec_fixed));
        assert!(key.starts_with("fixed_"));

        let ec_atr = ExitConfig {
            sl_pct: f64::NAN,
            tp_pct: f64::NAN,
            sl_atr: 1.5,
            tp_atr: 3.0,
            atr_period: 14,
        };
        let key = exit_config_key(&Some(ec_atr));
        assert!(key.starts_with("atr_"));
    }

    fn make_trial_with_params(id: usize, params: HashMap<String, Value>) -> Trial {
        Trial {
            id,
            params,
            exit_config: None,
            exit_meta: HashMap::new(),
            state: TrialState::Complete,
            values: vec![1.0, 1.0, 0.0],
            user_attrs: HashMap::new(),
        }
    }

    fn make_trial_with_meta(
        id: usize,
        params: HashMap<String, Value>,
        exit_meta: HashMap<String, Value>,
    ) -> Trial {
        Trial {
            id,
            params,
            exit_config: None,
            exit_meta,
            state: TrialState::Complete,
            values: vec![1.0, 1.0, 0.0],
            user_attrs: HashMap::new(),
        }
    }

    #[test]
    fn dedup_trials_removes_duplicate_params() {
        let mut p1 = HashMap::new();
        p1.insert("cluster".to_string(), Value::from("london_open"));
        p1.insert("hold_bars".to_string(), Value::from(1));

        let t1 = make_trial_with_params(1, p1.clone());
        let t2 = make_trial_with_params(2, p1.clone());
        let t3 = make_trial_with_params(3, p1);

        let unique = dedup_trials_by_key(vec![&t1, &t2, &t3]);
        assert_eq!(unique.len(), 1, "all 3 trials share same params");
        assert_eq!(unique[0].id, 1, "first occurrence preserved");
    }

    #[test]
    fn dedup_trials_keeps_distinct_exit_meta() {
        let mut p = HashMap::new();
        p.insert("cluster".to_string(), Value::from("london_open"));

        let mut m1 = HashMap::new();
        m1.insert("exit".to_string(), Value::from("none"));
        let mut m2 = HashMap::new();
        m2.insert("exit".to_string(), Value::from("atr_2_3"));

        let t1 = make_trial_with_meta(1, p.clone(), m1);
        let t2 = make_trial_with_meta(2, p, m2);

        let unique = dedup_trials_by_key(vec![&t1, &t2]);
        assert_eq!(unique.len(), 2, "different exit_meta should not collapse");
    }

    #[test]
    fn dedup_trials_preserves_order_after_sort() {
        let mut p1 = HashMap::new();
        p1.insert("cluster".to_string(), Value::from("london_open"));
        let mut p2 = HashMap::new();
        p2.insert("cluster".to_string(), Value::from("ny_close"));
        let mut p3 = HashMap::new();
        p3.insert("cluster".to_string(), Value::from("tokyo_fix"));

        let t1 = make_trial_with_params(1, p1.clone());
        let t2 = make_trial_with_params(2, p2);
        let t3 = make_trial_with_params(3, p1); // duplicate of t1
        let t4 = make_trial_with_params(4, p3);

        let unique = dedup_trials_by_key(vec![&t1, &t2, &t3, &t4]);
        let ids: Vec<usize> = unique.iter().map(|t| t.id).collect();
        assert_eq!(ids, vec![1, 2, 4], "order preserved, duplicates dropped");
    }

    #[test]
    fn summarize_trials_includes_state_and_values() {
        let mut p = HashMap::new();
        p.insert("k".to_string(), Value::from(1));

        let mut t1 = make_trial_with_params(1, p.clone());
        t1.values = vec![1.5, 0.8, -0.05];

        let mut t2 = make_trial_with_params(2, p);
        t2.state = TrialState::Pruned;
        t2.values = vec![]; // pruned trials carry no metric values

        let summary = summarize_trials(&[t1, t2]);
        assert_eq!(summary.len(), 2);
        assert_eq!(summary[0].id, 1);
        assert_eq!(summary[0].state, "complete");
        assert_eq!(summary[0].values, vec![1.5, 0.8, -0.05]);
        assert_eq!(summary[1].id, 2);
        assert_eq!(summary[1].state, "pruned");
        assert!(summary[1].values.is_empty());
    }

    #[test]
    fn scan_config_default_does_not_export_all_trials() {
        let cfg = ScanConfig::default();
        assert!(
            !cfg.export_all_trials,
            "default should be false to keep JSON small"
        );
    }

    #[test]
    fn scan_cell_result_omits_all_trials_when_none() {
        let result = ScanCellResult {
            asset: "USDJPY".to_string(),
            strategy: "time_of_day_drift".to_string(),
            timeframe: "1m".to_string(),
            best_trials: vec![],
            n_trials_completed: 0,
            n_trials_pruned: 0,
            all_trials: None,
        };
        let json = serde_json::to_string(&result).unwrap();
        assert!(
            !json.contains("all_trials"),
            "None all_trials should be skipped from JSON output, got: {json}"
        );
    }
}
