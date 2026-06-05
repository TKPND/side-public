//! Phase 2 validation unit tests — Wave 0 scaffolds.
//! Wave 1 plans 02-02..02-05 will un-ignore and fill each test.

#![allow(clippy::field_reassign_with_default, clippy::len_zero)]

use side_engine::constants::{DEFAULT_BOOTSTRAP_N, DEFAULT_BOOTSTRAP_SEED};
use side_engine::validation::{
    bars_per_day_from_datetimes_ns, compute_dsr, compute_gate_input, purged_kfold_indices,
    six_gate_verdict, stationary_bootstrap_ci, GateInput, PassMode, VerdictKind,
};

// ----- VAL-03: compute_dsr -----
#[test]
fn compute_dsr_known_values() {
    // Bailey & LdP 2014: SR=0.5, n_trials=100, skew=0, kurt=3, n_obs=100 → DSR < 0.10
    let dsr = compute_dsr(0.5, 100, 0.0, 3.0, 100);
    assert!(
        dsr < 0.10,
        "DSR should be low with 100 competing strategies, got {}",
        dsr
    );
}

#[test]
fn compute_dsr_high_sr_few_trials() {
    let dsr = compute_dsr(3.0, 10, 0.0, 3.0, 250);
    assert!(
        dsr > 0.90,
        "DSR should be high for genuinely strong strategy, got {}",
        dsr
    );
}

#[test]
fn compute_dsr_boundary_at_expected_max() {
    // At sharpe=0 (well below expected max), DSR should be a finite probability
    let dsr = compute_dsr(0.0, 100, 0.0, 3.0, 100);
    assert!(dsr.is_finite(), "DSR must be finite, got {}", dsr);
    assert!(
        (0.0..=1.0).contains(&dsr),
        "DSR must be a probability, got {}",
        dsr
    );
}

#[test]
fn compute_dsr_handles_degenerate_inputs() {
    assert!(
        compute_dsr(0.5, 0, 0.0, 3.0, 100).is_nan(),
        "n_trials=0 must be NaN"
    );
    assert!(
        compute_dsr(0.5, 100, 0.0, 3.0, 1).is_nan(),
        "n_obs<2 must be NaN"
    );
    assert!(compute_dsr(f64::INFINITY, 100, 0.0, 3.0, 100).is_nan());
}

#[test]
fn compute_dsr_production_parameters() {
    // Phase 4 production: sharpe=1.0, n_trials=12960, normal dist, 500 bars
    let dsr = compute_dsr(1.0, 12_960, 0.0, 3.0, 500);
    assert!(dsr.is_finite());
    assert!(
        dsr < 0.5,
        "SR=1.0 across 12960 trials should NOT clear the deflated bar, got {}",
        dsr
    );
}

// ----- VAL-01: purged_kfold_indices -----
#[test]
fn purged_kfold_indices_boundaries() {
    let splits = purged_kfold_indices(100, 5, 2).unwrap();
    assert_eq!(splits.len(), 5);
    assert_eq!(splits[0].oos_indices.len(), 20);
}

#[test]
fn purged_kfold_indices_embargo_respected() {
    let splits = purged_kfold_indices(100, 5, 2).unwrap();
    for split in &splits {
        let oos_set: std::collections::HashSet<_> = split.oos_indices.iter().copied().collect();
        for is_bar in &split.is_indices {
            assert!(!oos_set.contains(is_bar));
        }
    }
}

#[test]
fn purged_kfold_indices_degenerate_guard() {
    // 20 bars, 5 folds, embargo=5 bars → IS set per fold may be too small
    let result = purged_kfold_indices(20, 5, 5);
    assert!(result.is_err(), "Degenerate split should Err, got Ok");
}

// ----- VAL-01: bars_per_day helper -----
#[test]
fn bars_per_day_hourly_fixture() {
    // 48 consecutive hourly timestamps spanning 2 UTC calendar days → 24 bars/day
    const NS_PER_HOUR: i64 = 3_600_000_000_000;
    // Start at 2024-01-01T00:00:00Z = 1704067200_000_000_000 ns
    let start_ns: i64 = 1_704_067_200_000_000_000;
    let ts: Vec<i64> = (0..48).map(|i| start_ns + i * NS_PER_HOUR).collect();
    assert_eq!(bars_per_day_from_datetimes_ns(&ts), 24);
}

#[test]
fn bars_per_day_empty_returns_one() {
    assert_eq!(bars_per_day_from_datetimes_ns(&[]), 1);
}

// ----- VAL-05: stationary_bootstrap_ci -----
#[test]
fn stationary_bootstrap_ci_deterministic_seed42() {
    let pnl = vec![0.1_f64, -0.05, 0.2, 0.15, -0.1, 0.3, 0.05, 0.1];
    let (lo1, hi1) = stationary_bootstrap_ci(&pnl, DEFAULT_BOOTSTRAP_N, DEFAULT_BOOTSTRAP_SEED);
    let (lo2, hi2) = stationary_bootstrap_ci(&pnl, DEFAULT_BOOTSTRAP_N, DEFAULT_BOOTSTRAP_SEED);
    assert_eq!(lo1.to_bits(), lo2.to_bits());
    assert_eq!(hi1.to_bits(), hi2.to_bits());
}

#[test]
fn stationary_bootstrap_ci_width_sanity() {
    let pnl = vec![0.1_f64, -0.05, 0.2, 0.15, -0.1, 0.3, 0.05, 0.1];
    let (lo, hi) = stationary_bootstrap_ci(&pnl, 1000, 42);
    assert!(lo < hi, "CI must be ordered, got ({}, {})", lo, hi);
    assert!((hi - lo) < 1.0, "CI width should be < 1.0 for this fixture");
}

#[test]
fn bootstrap_constant_series_returns_constant_ci() {
    let pnl = vec![1.0_f64; 50];
    let (lo, hi) = stationary_bootstrap_ci(&pnl, 500, 42);
    assert!((lo - 1.0).abs() < 1e-9);
    assert!((hi - 1.0).abs() < 1e-9);
}

#[test]
fn bootstrap_handles_empty_input() {
    let (lo, hi) = stationary_bootstrap_ci(&[], 1000, 42);
    assert_eq!(lo, 0.0);
    assert_eq!(hi, 0.0);
}

#[test]
fn bootstrap_deterministic_seed42() {
    let pnl = vec![0.1_f64, -0.05, 0.2, 0.15, -0.1, 0.3, 0.05, 0.1];
    let (lo1, hi1) = stationary_bootstrap_ci(&pnl, 1000, 42);
    let (lo2, hi2) = stationary_bootstrap_ci(&pnl, 1000, 42);
    assert_eq!(lo1.to_bits(), lo2.to_bits());
    assert_eq!(hi1.to_bits(), hi2.to_bits());
}

fn gate_input_passing() -> GateInput {
    GateInput {
        abs_t_stat: 5.0,
        dsr_pvalue: 0.01,
        mean_pip: 10.0,
        round_trip_cost_pip: 1.0,
        folds_passing_oos_pf: 5,
        h1_mean_pip: 1.0,
        h2_mean_pip: 1.0,
        median_fold_ci_low: 0.1,
        median_fold_ci_high: 2.0,
        trades_per_fold: vec![3usize; 5],
    }
}

// ----- VAL-06: six_gate_verdict -----
#[test]
fn six_gate_verdict_gate1_boundary_pass() {
    let mut inp = gate_input_passing();
    inp.abs_t_stat = 4.401;
    let v = six_gate_verdict(&inp, PassMode::Strict);
    assert!(v.gates[0].passed);
}

#[test]
fn six_gate_verdict_gate1_boundary_fail() {
    let mut inp = gate_input_passing();
    inp.abs_t_stat = 4.399;
    let v = six_gate_verdict(&inp, PassMode::Strict);
    assert!(!v.gates[0].passed);
    assert!(matches!(v.verdict, VerdictKind::Fail { .. }));
}

#[test]
fn gate_boundary_conditions_all_six() {
    let inp = gate_input_passing();
    let v = six_gate_verdict(&inp, PassMode::Strict);
    assert_eq!(v.gates.len(), 6);
    assert!(matches!(v.verdict, VerdictKind::Pass));
}

#[test]
fn gate2_dsr_boundary_pass() {
    let mut inp = gate_input_passing();
    inp.dsr_pvalue = 0.0499;
    let v = six_gate_verdict(&inp, PassMode::Strict);
    assert!(v.gates[1].passed);
}

#[test]
fn gate2_dsr_boundary_fail() {
    let mut inp = gate_input_passing();
    inp.dsr_pvalue = 0.0501;
    let v = six_gate_verdict(&inp, PassMode::Strict);
    assert!(!v.gates[1].passed);
    assert!(matches!(v.verdict, VerdictKind::Fail { .. }));
}

#[test]
fn gate3_mean_pip_vs_cost_boundary() {
    let mut inp = gate_input_passing();
    inp.round_trip_cost_pip = 1.0;
    inp.mean_pip = 2.0001;
    assert!(six_gate_verdict(&inp, PassMode::Strict).gates[2].passed);
    inp.mean_pip = 1.9999;
    assert!(!six_gate_verdict(&inp, PassMode::Strict).gates[2].passed);
}

#[test]
fn gate4_fold_pf_4of5_boundary() {
    let mut inp = gate_input_passing();
    inp.folds_passing_oos_pf = 4;
    assert!(six_gate_verdict(&inp, PassMode::Strict).gates[3].passed);
    inp.folds_passing_oos_pf = 3;
    assert!(!six_gate_verdict(&inp, PassMode::Strict).gates[3].passed);
}

#[test]
fn gate5_h1_h2_sign_agreement() {
    let mut inp = gate_input_passing();
    inp.h1_mean_pip = 1.0;
    inp.h2_mean_pip = -0.01;
    assert!(!six_gate_verdict(&inp, PassMode::Strict).gates[4].passed);
    inp.h2_mean_pip = 0.01;
    assert!(six_gate_verdict(&inp, PassMode::Strict).gates[4].passed);
    inp.h1_mean_pip = 0.0;
    assert!(!six_gate_verdict(&inp, PassMode::Strict).gates[4].passed);
}

#[test]
fn gate6_ci_excludes_zero() {
    let mut inp = gate_input_passing();
    inp.median_fold_ci_low = -0.1;
    inp.median_fold_ci_high = 0.1;
    assert!(!six_gate_verdict(&inp, PassMode::Strict).gates[5].passed);
    inp.median_fold_ci_low = 0.0001;
    inp.median_fold_ci_high = 0.2;
    assert!(six_gate_verdict(&inp, PassMode::Strict).gates[5].passed);
    inp.median_fold_ci_low = -0.5;
    inp.median_fold_ci_high = -0.001;
    assert!(six_gate_verdict(&inp, PassMode::Strict).gates[5].passed);
}

#[test]
fn compute_gate_input_populates_all_fields() {
    let fold_pnls = vec![vec![0.1, 0.2, 0.3], vec![-0.1, 0.2, 0.1]];
    let fold_pfs = vec![2.5, 1.8];
    let trades_per_fold: [usize; 2] = [3, 3];
    let full_pnl = vec![0.1, 0.2, 0.3, -0.1, 0.2, 0.1];
    let gi = compute_gate_input(
        5.0,
        0.01,
        10.0,
        1.0,
        &fold_pnls,
        &fold_pfs,
        &trades_per_fold,
        &full_pnl,
        100,
        42,
    );
    assert_eq!(gi.folds_passing_oos_pf, 1);
    assert!(gi.h1_mean_pip.is_finite());
    assert!(gi.h2_mean_pip.is_finite());
    assert_eq!(gi.abs_t_stat, 5.0);
    assert_eq!(gi.dsr_pvalue, 0.01);
    // Phase 56 D-12: trades_per_fold propagated verbatim.
    assert_eq!(gi.trades_per_fold, vec![3usize, 3usize]);
}

#[test]
fn verdict_lists_all_failed_gates() {
    let mut inp = gate_input_passing();
    inp.abs_t_stat = 2.0;
    inp.dsr_pvalue = 0.5;
    let v = six_gate_verdict(&inp, PassMode::Strict);
    match v.verdict {
        VerdictKind::Fail { failed_gates } => {
            assert!(failed_gates.contains(&"abs_t_stat".to_string()));
            assert!(failed_gates.contains(&"dsr_pvalue".to_string()));
            assert_eq!(failed_gates.len(), 2);
        }
        _ => panic!("expected Fail verdict"),
    }
}

// ----- VAL-05: WalkResult CI JSON schema -----
#[test]
fn walk_result_has_ci_fields_in_json() {
    use side_engine::wfd::WalkResult;
    let w = WalkResult {
        pnl_ci_low: -0.1,
        pnl_ci_high: 0.2,
        ..Default::default()
    };
    let val = serde_json::to_value(&w).expect("serialize");
    assert!(val.get("pnl_ci_low").is_some(), "pnl_ci_low key missing");
    assert!(val.get("pnl_ci_high").is_some(), "pnl_ci_high key missing");
    assert!((val["pnl_ci_low"].as_f64().unwrap() - (-0.1)).abs() < 1e-12);
    assert!((val["pnl_ci_high"].as_f64().unwrap() - 0.2).abs() < 1e-12);
}

// ----- VAL-04: JSON schema -----
#[test]
fn json_schema_dsr() {
    use side_engine::wfd::WfdSingleResult;
    let result = WfdSingleResult {
        dsr_pvalue: 0.032,
        dsr_n_trials: 12_960,
        ..Default::default()
    };
    let val = serde_json::to_value(&result).expect("serialize");
    assert!(val.get("dsr_pvalue").is_some(), "dsr_pvalue key missing");
    assert!(
        val.get("dsr_n_trials").is_some(),
        "dsr_n_trials key missing"
    );
    assert_eq!(val["dsr_n_trials"].as_u64(), Some(12_960));
    assert!(
        (val["dsr_pvalue"].as_f64().unwrap() - 0.032).abs() < 1e-12,
        "dsr_pvalue round-trips"
    );
}

// ----- D-02: round_trip_cost_pip scale correctness -----
#[test]
fn gate3_d02_cost_scale_correctness() {
    // D-02 fix: formula is 2.0 * fee_bps_per_side / 10_000.0
    // At fee_per_side=2bps → round_trip_cost = 2.0*2.0/10_000.0 = 0.0004
    let round_trip_cost = 2.0 * 2.0_f64 / 10_000.0;
    assert!(
        (round_trip_cost - 0.0004).abs() < 1e-12,
        "round_trip_cost at 2bps/side must be 0.0004, got {}",
        round_trip_cost
    );

    // Gate 3 passes when mean_pip > 2 * round_trip_cost (i.e., > 0.0008)
    let mut inp = gate_input_passing();
    inp.round_trip_cost_pip = round_trip_cost;
    inp.mean_pip = 0.0009; // > 0.0008 → should pass
    assert!(
        six_gate_verdict(&inp, PassMode::Strict).gates[2].passed,
        "gate3 should pass when mean_pip=0.0009 > 2×0.0004=0.0008"
    );

    // Gate 3 fails when mean_pip <= 2 * round_trip_cost (i.e., <= 0.0008)
    inp.mean_pip = 0.0003; // < 0.0008 → should fail
    assert!(
        !six_gate_verdict(&inp, PassMode::Strict).gates[2].passed,
        "gate3 should fail when mean_pip=0.0003 < 2×0.0004=0.0008"
    );
}

// ----- VAL-02: Naive70_30 compat -----
#[test]
fn naive_70_30_still_works() {
    use side_engine::wfd::{run_wfd_single, CvMode, WfdConfig};
    // Build a minimal WfdConfig with explicit Naive70_30 to verify the deprecation path
    // doesn't panic and still produces walks.
    let mut cfg = WfdConfig::default();
    cfg.cv_mode = CvMode::Naive70_30;
    // 6-month IS + 2-month OOS fits in ~3000 hourly bars (125 days ≈ 4 months)
    cfg.is_months = 2;
    cfg.oos_months = 1;
    cfg.num_walks = 1;
    cfg.min_annual_trades = 0;
    cfg.min_oos_pf = 0.0;

    // Synthetic linearly-rising OHLCV (3000 bars ≈ 125 days of hourly data)
    let n: usize = 3000;
    let close: Vec<f64> = (0..n).map(|i| 100.0 + i as f64 * 0.01).collect();
    let open = close.clone();
    let high: Vec<f64> = close.iter().map(|c| c + 0.5).collect();
    let low: Vec<f64> = close.iter().map(|c| c - 0.5).collect();
    let volume = vec![1000.0f64; n];
    // Hourly timestamps starting 2024-01-01
    const NS_PER_HOUR: i64 = 3_600_000_000_000;
    let start_ns: i64 = 1_704_067_200_000_000_000;
    let datetimes_ns: Vec<i64> = (0..n as i64).map(|i| start_ns + i * NS_PER_HOUR).collect();
    let params = {
        let mut p = std::collections::HashMap::new();
        p.insert("entry_minute".into(), serde_json::json!(0));
        p.insert("direction".into(), serde_json::json!("long"));
        p.insert("hold_h".into(), serde_json::json!(1));
        p
    };

    let result = run_wfd_single(
        &open,
        &high,
        &low,
        &close,
        &volume,
        &datetimes_ns,
        None,
        "tod_edge",
        &params,
        &cfg,
        "1h",
        None,
        1,
    );
    // Naive70_30 path: should produce at least 1 walk without panicking
    assert!(
        result.walks.len() >= 1,
        "Naive70_30 path must produce at least 1 walk, got {}",
        result.walks.len()
    );
}
