use side_engine::backtest::BacktestResult;
use side_engine::portfolio::{build_portfolio, SlotInput, WeightMethod};

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

fn make_backtest_result(equity_curve: Vec<f64>, timestamps: Vec<i64>) -> BacktestResult {
    BacktestResult {
        total_return: *equity_curve.last().unwrap_or(&1.0) - 1.0,
        sharpe_ratio: 0.0,
        max_drawdown: 0.0,
        win_rate: 0.0,
        num_trades: 0,
        gross_profit: 0.0,
        gross_loss: 0.0,
        profit_factor: 0.0,
        equity_curve,
        timestamps,
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[test]
fn test_build_portfolio_3_slots_equal_weight() {
    let n = 100usize;
    let base_ns: i64 = 1_700_000_000_000_000_000;
    let step: i64 = 3_600_000_000_000; // 1h in nanoseconds
    let ts: Vec<i64> = (0..n as i64).map(|i| base_ns + i * step).collect();

    // Slot A: steady uptrend 1.0 → 1.5 (linear)
    let eq_a: Vec<f64> = (0..n)
        .map(|i| 1.0 + i as f64 * 0.5 / (n - 1) as f64)
        .collect();
    // Slot B: volatile sideways oscillating around 1.0
    let eq_b: Vec<f64> = (0..n)
        .map(|i| 1.0 + 0.05 * (i as f64 * 0.3).sin())
        .collect();
    // Slot C: moderate uptrend 1.0 → 1.2
    let eq_c: Vec<f64> = (0..n)
        .map(|i| 1.0 + i as f64 * 0.2 / (n - 1) as f64)
        .collect();

    let r_a = make_backtest_result(eq_a, ts.clone());
    let r_b = make_backtest_result(eq_b, ts.clone());
    let r_c = make_backtest_result(eq_c, ts.clone());

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
        SlotInput {
            label: "C".to_string(),
            result: &r_c,
            timeframe: "1h",
        },
    ];

    let result = build_portfolio(&slots, WeightMethod::Equal).expect("build_portfolio failed");

    // Weights must be [1/3, 1/3, 1/3]
    assert_eq!(result.weights.len(), 3);
    for &w in &result.weights {
        assert!((w - 1.0 / 3.0).abs() < 1e-12, "weight {w} != 1/3");
    }

    // Curve lengths
    assert_eq!(result.equity_curve.len(), n);
    assert_eq!(result.timestamps.len(), n);

    // Metrics are finite
    assert!(
        result.sharpe.is_finite(),
        "sharpe not finite: {}",
        result.sharpe
    );
    assert!(result.cagr.is_finite(), "cagr not finite: {}", result.cagr);
    assert!(
        result.calmar.is_finite(),
        "calmar not finite: {}",
        result.calmar
    );
    assert!(
        result.mean_correlation.is_finite(),
        "mean_correlation not finite: {}",
        result.mean_correlation
    );

    // Drawdown is non-positive
    assert!(
        result.max_drawdown <= 0.0,
        "max_drawdown should be <= 0, got {}",
        result.max_drawdown
    );

    // Slot labels
    assert_eq!(result.slot_labels, vec!["A", "B", "C"]);
}

#[test]
fn test_build_portfolio_risk_parity() {
    let n = 100usize;
    let base_ns: i64 = 1_700_000_000_000_000_000;
    let step: i64 = 3_600_000_000_000;
    let ts: Vec<i64> = (0..n as i64).map(|i| base_ns + i * step).collect();

    // High-vol slot: equity swings ~20% (large oscillation)
    let eq_high: Vec<f64> = (0..n)
        .map(|i| 1.0 + 0.20 * (i as f64 * 0.4).sin())
        .collect();
    // Low-vol slot: equity swings ~2% (small oscillation)
    let eq_low: Vec<f64> = (0..n)
        .map(|i| 1.0 + 0.02 * (i as f64 * 0.4).sin())
        .collect();

    let r_high = make_backtest_result(eq_high, ts.clone());
    let r_low = make_backtest_result(eq_low, ts.clone());

    let slots = vec![
        SlotInput {
            label: "HighVol".to_string(),
            result: &r_high,
            timeframe: "1h",
        },
        SlotInput {
            label: "LowVol".to_string(),
            result: &r_low,
            timeframe: "1h",
        },
    ];

    let result = build_portfolio(&slots, WeightMethod::RiskParity).expect("build_portfolio failed");

    // Low-vol slot (index 1) should receive higher weight than high-vol (index 0)
    assert!(
        result.weights[1] > result.weights[0],
        "low-vol weight {} should be > high-vol weight {}",
        result.weights[1],
        result.weights[0]
    );

    // Weights sum to 1.0
    let sum: f64 = result.weights.iter().sum();
    assert!((sum - 1.0).abs() < 1e-10, "weights sum={sum} != 1.0");
}

#[test]
fn test_build_portfolio_gap_aligned_timestamps() {
    let base_ns: i64 = 1_700_000_000_000_000_000;
    let step: i64 = 3_600_000_000_000;

    // Slot A: 100 timestamps (every hour from base)
    let n_a = 100usize;
    let ts_a: Vec<i64> = (0..n_a as i64).map(|i| base_ns + i * step).collect();
    let eq_a: Vec<f64> = (0..n_a).map(|i| 1.0 + i as f64 * 0.005).collect();

    // Slot B: 80 timestamps starting from base + 10h, ending at base + 89h
    // so first 10 and last 10 of A are absent in B
    let ts_b: Vec<i64> = (10..90i64).map(|i| base_ns + i * step).collect();
    let eq_b: Vec<f64> = (0..80).map(|i| 1.0 + i as f64 * 0.003).collect();

    let r_a = make_backtest_result(eq_a, ts_a.clone());
    let r_b = make_backtest_result(eq_b, ts_b.clone());

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

    let result = build_portfolio(&slots, WeightMethod::Equal).expect("build_portfolio failed");

    // Intersection is smaller than full A (only bars 10..90 overlap)
    assert!(
        result.equity_curve.len() < n_a,
        "intersection length {} should be < {}",
        result.equity_curve.len(),
        n_a
    );
    assert_eq!(
        result.timestamps.len(),
        result.equity_curve.len(),
        "timestamps.len() must equal equity_curve.len()"
    );

    // All result timestamps must exist in both A and B
    let ts_a_set: std::collections::HashSet<i64> = ts_a.iter().copied().collect();
    let ts_b_set: std::collections::HashSet<i64> = ts_b.iter().copied().collect();
    for &t in &result.timestamps {
        assert!(
            ts_a_set.contains(&t),
            "result timestamp {t} missing from slot A"
        );
        assert!(
            ts_b_set.contains(&t),
            "result timestamp {t} missing from slot B"
        );
    }
}

#[test]
fn test_build_portfolio_single_slot() {
    let n = 20usize;
    let base_ns: i64 = 1_700_000_000_000_000_000;
    let step: i64 = 3_600_000_000_000;
    let ts: Vec<i64> = (0..n as i64).map(|i| base_ns + i * step).collect();
    let eq: Vec<f64> = (0..n).map(|i| 1.0 + i as f64 * 0.01).collect();

    let r = make_backtest_result(eq, ts);
    let slots = vec![SlotInput {
        label: "Solo".to_string(),
        result: &r,
        timeframe: "1h",
    }];

    let result = build_portfolio(&slots, WeightMethod::Equal).expect("build_portfolio failed");

    // Single slot: weight = 1.0
    assert_eq!(result.weights, vec![1.0]);

    // Per D-09: mean_correlation = 0.0 (NaN from single slot sanitized to 0.0)
    assert_eq!(
        result.mean_correlation, 0.0,
        "single slot mean_correlation should be 0.0"
    );

    assert_eq!(result.slot_labels, vec!["Solo"]);
}
