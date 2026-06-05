//! Regression tests — REGR-01 (fee curve degeneracy) + REGR-02 (E2E smoke) + REGR-03 (VAL-02 embargo).
//!
//! # Wave 0 PF Baseline (measured 2026-04-10 on usdjpy_1h_sample.csv, 504 bars / ~21 days)
//!
//! london_open (entry_minute=540) + direction=long + hold_h=2:
//!   - fee=0.0 bps/side → PF = 0.9685 (trades=42)
//!   - fee=0.5 bps/side (1 bps RT) → PF = 0.0009 (trades=42)
//!
//! NOTE: Baseline diverges sharply from CONTEXT.md's nominal 3.37 / 1.003.
//! The 504-bar fixture (~21 days) is too short to reproduce the edge discovered
//! over the full 2024+2025 BQ dataset (~500 trades/slot). The fixture happens to
//! have a losing 540-minute window, so PF<1 at fee=0.
//!
//! REGR-01 consequence: Uses a SYNTHETIC in-memory fixture with a planted edge
//! (entry_minute=0, consistent upward move at midnight bars) rather than the
//! CSV fixture. Tolerances are set relative to the synthetic fixture's geometry
//! and are stable regardless of real market data changes. This is the correct
//! approach per the wave0_findings block in the plan.

#![allow(clippy::field_reassign_with_default, clippy::type_complexity)]

use std::collections::HashMap;
use std::path::PathBuf;

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
}

/// Build a synthetic OHLCV dataset with a planted edge at entry_minute=0.
///
/// Structure:
/// - 3000 hourly bars (~125 days) starting 2024-01-01T00:00:00Z
/// - Bars at UTC midnight (minute-of-day = 0, i.e. every 24th bar): strong
///   upward move — open=100, close=101 (gain = 1.0)
/// - All other bars: flat — open=100, close=100 (no gain)
///
/// With hold_h=1 (1-bar hold) and entry_minute=0, the strategy enters at
/// midnight bars and rides the planted edge. fee=0 gives PF >> 1. fee raises
/// the cost threshold and degrades PF, testing fee accounting.
fn synthetic_ohlcv_with_edge() -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<i64>) {
    const N: usize = 3000;
    const NS_PER_HOUR: i64 = 3_600_000_000_000;
    // 2024-01-01T00:00:00Z
    let start_ns: i64 = 1_704_067_200_000_000_000;

    let mut open = vec![100.0f64; N];
    let mut high = vec![100.5f64; N];
    let mut low = vec![99.5f64; N];
    let mut close = vec![100.0f64; N];
    let volume = vec![1000.0f64; N];
    let datetimes_ns: Vec<i64> = (0..N as i64).map(|i| start_ns + i * NS_PER_HOUR).collect();

    // Plant edge: every 24th bar (UTC midnight, minute-of-day=0) has a 1-pip
    // upward move. The tod_edge strategy enters at entry_minute=0, so these
    // bars trigger entries followed by an upward close, giving positive PnL.
    for i in (0..N).step_by(24) {
        open[i] = 100.0;
        high[i] = 101.5;
        low[i] = 99.5;
        close[i] = 101.0; // +1.0 gain on entry bar
    }

    (open, high, low, close, volume, datetimes_ns)
}

/// Run tod_edge with entry_minute=0, direction=long, hold_h=1 on the synthetic
/// fixture at a given fee (bps per side). Returns the OOS PF from the first walk.
///
/// Uses run_wfd_single with Naive70_30 (2-month IS, 1-month OOS, 1 walk) so
/// that the test runs fast and the planted edge is visible in the OOS window.
fn run_tod_edge_synthetic(fee_bps_per_side: f64) -> f64 {
    use side_engine::wfd::{run_wfd_single, CvMode, WfdConfig};

    let (open, high, low, close, volume, datetimes_ns) = synthetic_ohlcv_with_edge();

    let mut cfg = WfdConfig::default();
    cfg.cv_mode = CvMode::Naive70_30;
    cfg.is_months = 2;
    cfg.oos_months = 1;
    cfg.num_walks = 3;
    cfg.min_annual_trades = 0;
    cfg.min_oos_pf = 0.0;
    cfg.min_wfe = 0.0;
    cfg.min_oos_win_rate = 0.0;
    // fee_bps is per-side internally; the CLI exposes RT (round-trip).
    // run_wfd_single uses cfg.fee_bps as per-side fee.
    cfg.fee_bps = fee_bps_per_side;

    let mut params = HashMap::new();
    params.insert("entry_minute".into(), serde_json::json!(0));
    params.insert("direction".into(), serde_json::json!("long"));
    params.insert("hold_h".into(), serde_json::json!(1));

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

    // Return median OOS PF across walks (or first walk if only 1).
    // The planted edge should give PF > 1 at fee=0 and degrade with fees.
    if result.walks.is_empty() {
        return 0.0;
    }
    let mut pfs: Vec<f64> = result.walks.iter().map(|w| w.oos_pf).collect();
    pfs.sort_by(|a, b| a.partial_cmp(b).unwrap());
    pfs[pfs.len() / 2]
}

#[test]
fn london_open_atr_exit_fee_curve_regression() {
    // REGR-01: Verify that the fee accounting is working correctly by running
    // a synthetic fixture with a planted directional edge. The edge must be
    // detectable at fee=0 and degrade monotonically with fees.
    //
    // Tolerances are set relative to the synthetic fixture's geometry:
    //   - fee=0.0 bps/side: consistent upward edge, PF must be > 1.5
    //   - fee=5.0 bps/side: fee erodes the edge, PF must be < 1.5
    //     (i.e., fee impact is detectable — if fee_bps were silently zeroed,
    //     this assertion catches it)
    //
    // The exact threshold values are wide enough to be fixture-stable but
    // tight enough to catch a fee accounting regression.
    let pf_fee0 = run_tod_edge_synthetic(0.0);
    let pf_fee5 = run_tod_edge_synthetic(5.0);

    // At fee=0 the planted edge must be visible in the OOS window.
    // If this fails, the synthetic fixture geometry is wrong (not a fee bug).
    assert!(
        pf_fee0 > 1.5,
        "REGR-01 fee=0 PF too low: got {pf_fee0:.4}. \
         Synthetic edge not reaching OOS window — check fixture geometry."
    );

    // At fee=5bps/side the edge must be materially degraded.
    // If pf_fee5 >= pf_fee0 * 0.95, fee accounting is likely broken (fees
    // aren't being subtracted, so the two runs look the same).
    assert!(
        pf_fee5 < pf_fee0 * 0.90,
        "REGR-01 fee degradation not detected: fee=0 PF={pf_fee0:.4}, fee=5bps PF={pf_fee5:.4}. \
         Fee accounting may have silently changed — check fee_bps handling in wfd.rs/backtest.rs."
    );
}

#[test]
fn e2e_edges_to_verdict_smoke() {
    // REGR-02: End-to-end smoke test: edges_minimal.json → tod_edge scan →
    // WFD → six_gate_verdict → assert 6 gates + serde round-trip.
    //
    // Phase 2 status: validation_test.rs (29 passed, 0 ignored) and
    // wfd_purged_test.rs (1 passed, 0 ignored) are fully green as of Plan
    // 03-03 execution. REGR-02 is un-ignored.
    use side_engine::constants::{DEFAULT_BOOTSTRAP_N, DEFAULT_BOOTSTRAP_SEED};
    use side_engine::validation::{compute_gate_input, six_gate_verdict, PassMode, VerdictKind};
    use side_engine::wfd::{run_wfd_single, CvMode, WfdConfig};

    // STEP 1: Deserialize edges_minimal.json.
    let edges_path = fixtures_dir().join("edges_minimal.json");
    let edges_json =
        std::fs::read_to_string(&edges_path).expect("edges_minimal.json fixture present");
    let edges: Vec<side_engine::edges::Edge> =
        serde_json::from_str(&edges_json).expect("edges_minimal.json deserializes");
    assert!(
        !edges.is_empty(),
        "edges_minimal.json should contain at least one edge"
    );

    // STEP 2: Build synthetic OHLCV and run WFD for the first edge.
    let edge = &edges[0];
    let hold_h = edge.hold_h_candidates[0];

    let (open, high, low, close, volume, datetimes_ns) = synthetic_ohlcv_with_edge();

    let mut cfg = WfdConfig::default();
    cfg.cv_mode = CvMode::PurgedKFold {
        k: 5,
        embargo_days: 1,
    };
    cfg.min_oos_pf = 0.0;
    cfg.min_annual_trades = 0;
    cfg.min_wfe = 0.0;
    cfg.min_oos_win_rate = 0.0;
    cfg.fee_bps = 0.5; // 1 bps RT

    let mut params = HashMap::new();
    params.insert("entry_minute".into(), serde_json::json!(edge.entry_minute));
    params.insert("direction".into(), serde_json::json!(edge.direction));
    params.insert("hold_h".into(), serde_json::json!(hold_h));

    let wfd_result = run_wfd_single(
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

    assert_eq!(
        wfd_result.walks.len(),
        5,
        "purged 5-fold must produce 5 walks, got {}",
        wfd_result.walks.len()
    );

    // STEP 3: Build GateInput from WFD result using compute_gate_input.
    // Mirror validation_test.rs compute_gate_input_populates_all_fields.
    //
    // WalkResult has oos_equity_curve (cumulative PnL). Derive per-bar PnL as
    // first-differences: pnl[i] = equity[i] - equity[i-1], pnl[0] = equity[0].
    let fold_pnls: Vec<Vec<f64>> = wfd_result
        .walks
        .iter()
        .map(|w| {
            let eq = &w.oos_equity_curve;
            if eq.is_empty() {
                vec![]
            } else {
                let mut pnl = Vec::with_capacity(eq.len());
                pnl.push(eq[0]);
                for i in 1..eq.len() {
                    pnl.push(eq[i] - eq[i - 1]);
                }
                pnl
            }
        })
        .collect();
    let fold_pfs: Vec<f64> = wfd_result.walks.iter().map(|w| w.oos_pf).collect();
    // Phase 56 BUG-03: surface per-fold trade counts to filter zero-trade folds.
    let trades_per_fold: Vec<usize> = wfd_result.walks.iter().map(|w| w.oos_trades).collect();
    // Full PnL = concatenation of all fold OOS per-bar PnL.
    let full_pnl: Vec<f64> = fold_pnls.iter().flat_map(|v| v.iter().copied()).collect();

    // Derive t_stat from full_pnl: t = mean / (std / sqrt(n)).
    // If full_pnl is empty fall back to 0.0 (REGR-02 only checks gates.len()).
    let t_stat = {
        let n = full_pnl.len() as f64;
        if n < 2.0 {
            0.0_f64
        } else {
            let mean = full_pnl.iter().sum::<f64>() / n;
            let var = full_pnl.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / (n - 1.0);
            let std = var.sqrt();
            if std == 0.0 {
                0.0
            } else {
                (mean / (std / n.sqrt())).abs()
            }
        }
    };
    let dsr_pvalue = wfd_result.dsr_pvalue;
    // mean_pip: mean per-bar OOS PnL across all folds.
    let mean_pip = if full_pnl.is_empty() {
        0.0
    } else {
        full_pnl.iter().sum::<f64>() / full_pnl.len() as f64
    };
    let cost_pip = cfg.fee_bps * 2.0; // RT cost for gate 3 comparison

    let gate_input = compute_gate_input(
        t_stat,
        dsr_pvalue,
        mean_pip,
        cost_pip,
        &fold_pnls,
        &fold_pfs,
        &trades_per_fold,
        &full_pnl,
        DEFAULT_BOOTSTRAP_N,
        DEFAULT_BOOTSTRAP_SEED,
    );

    // STEP 4: Run six_gate_verdict.
    let verdict = six_gate_verdict(&gate_input, PassMode::Strict);

    // Core assertion: must have exactly 6 gates regardless of Pass/Fail.
    assert_eq!(
        verdict.gates.len(),
        6,
        "6-gate verdict must have exactly 6 gates"
    );

    // verdict.kind should be well-formed (Pass or Fail — not uninitialized).
    // We don't assert Pass because the synthetic data may not clear all gates.
    let _ = match &verdict.verdict {
        VerdictKind::Pass => "Pass",
        VerdictKind::Fail { .. } => "Fail",
    };

    // STEP 5: Serde round-trip sanity — report.rs depends on this.
    let serialized = serde_json::to_string(&verdict).expect("Verdict serializes");
    let _round_trip: side_engine::validation::Verdict =
        serde_json::from_str(&serialized).expect("Verdict round-trips");
}

#[test]
fn regr_03_embargo_bars_1m() {
    // REGR-03: VAL-02 — timeframe-correct embargo bars for 1m data.
    //
    // Two assertions:
    //   1. bars_per_day_from_datetimes_ns returns 1440 for 1m-spaced timestamps.
    //   2. run_wfd_single produces exactly 5 walks for 1m-frequency synthetic
    //      OHLCV (43200 bars = 30 days) with PurgedKFold { k: 5, embargo_days: 1 }.
    //
    // run_wfd_single uses bars_per_day_from_datetimes_ns internally (wfd.rs:524)
    // so assertion 2 also validates that the embargo is correctly scaled to
    // 1440 bars/day rather than the wrong hardcoded 24 bars/day.
    //
    // N=43200 (30 days): each fold covers 6 days OOS + 1 day embargo at 1m resolution.
    // Using 7200 bars (5 days) was too short — embargo purging left no training data.
    use side_engine::validation::bars_per_day_from_datetimes_ns;
    use side_engine::wfd::{run_wfd_single, CvMode, WfdConfig};

    const N: usize = 43200; // 30 days × 1440 bars/day
    const NS_PER_MINUTE: i64 = 60_000_000_000;
    // 2024-01-01T00:00:00Z
    let start_ns: i64 = 1_704_067_200_000_000_000;

    let datetimes_ns: Vec<i64> = (0..N as i64)
        .map(|i| start_ns + i * NS_PER_MINUTE)
        .collect();

    // Assertion 1: unit check — bars_per_day must be 1440 for 1m data.
    let bars_per_day = bars_per_day_from_datetimes_ns(&datetimes_ns);
    assert_eq!(
        bars_per_day, 1440,
        "REGR-03 VAL-02: bars_per_day_from_datetimes_ns returned {bars_per_day} for 1m data, expected 1440"
    );

    // Build synthetic OHLCV with a planted edge at minute-of-day = 0.
    // Every 1440th bar (UTC midnight) has a 1-pip upward move.
    let mut open = vec![100.0f64; N];
    let mut high = vec![100.5f64; N];
    let mut low = vec![99.5f64; N];
    let mut close = vec![100.0f64; N];
    let volume = vec![1000.0f64; N];

    for i in (0..N).step_by(1440) {
        open[i] = 100.0;
        high[i] = 101.5;
        low[i] = 99.5;
        close[i] = 101.0;
    }

    let mut params = HashMap::new();
    params.insert("entry_minute".into(), serde_json::json!(0));
    params.insert("direction".into(), serde_json::json!("long"));
    params.insert("hold_h".into(), serde_json::json!(1));

    let cfg = WfdConfig {
        cv_mode: CvMode::PurgedKFold {
            k: 5,
            embargo_days: 1,
        },
        fee_bps: 0.5,
        min_oos_pf: 0.0,
        min_annual_trades: 0,
        min_wfe: 0.0,
        min_oos_win_rate: 0.0,
        ..WfdConfig::default()
    };

    // Assertion 2: integration check — run_wfd_single must produce 5 walks.
    let wfd_result = run_wfd_single(
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
        "1m",
        None,
        1,
    );

    assert_eq!(
        wfd_result.walks.len(),
        5,
        "REGR-03 VAL-02: run_wfd_single with 1m data must produce 5 walks (purged 5-fold), got {}",
        wfd_result.walks.len()
    );
}
