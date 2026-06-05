use side_engine::backtest::{run_backtest, run_backtest_sltp};

#[test]
fn test_run_backtest_timestamps_len_matches_equity_curve() {
    let n = 100usize;
    let close: Vec<f64> = (0..n).map(|i| 100.0 + i as f64 * 0.1).collect();
    let signals: Vec<i8> = (0..n).map(|i| if i % 10 < 5 { 1 } else { -1 }).collect();
    let base_ns: i64 = 1_700_000_000_000_000_000;
    let ts: Vec<i64> = (0..n as i64)
        .map(|i| base_ns + i * 3_600_000_000_000)
        .collect();

    let result = run_backtest(&close, &signals, 0.001, 8760.0, 0, &ts);

    assert_eq!(
        result.timestamps.len(),
        result.equity_curve.len(),
        "timestamps.len() must equal equity_curve.len()"
    );
    assert_eq!(
        result.timestamps.len(),
        n,
        "timestamps must have one entry per bar"
    );
    assert_eq!(
        result.timestamps[0], base_ns,
        "first timestamp must match input"
    );
}

#[test]
fn test_run_backtest_empty_timestamps_is_valid() {
    let n = 20usize;
    let close: Vec<f64> = (0..n).map(|i| 100.0 + i as f64 * 0.1).collect();
    let signals: Vec<i8> = vec![1i8; n];

    // Per D-02: passing &[] is valid for call sites that don't need timestamps
    let result = run_backtest(&close, &signals, 0.001, 8760.0, 0, &[]);

    assert_eq!(
        result.timestamps.len(),
        0,
        "empty input timestamps must produce empty result.timestamps"
    );
    assert!(
        !result.equity_curve.is_empty(),
        "equity_curve must still be populated"
    );
}

#[test]
fn test_run_backtest_sltp_timestamps_len_matches_equity_curve() {
    let n = 50usize;
    let close: Vec<f64> = (0..n).map(|i| 100.0 + i as f64 * 0.1).collect();
    let high: Vec<f64> = close.iter().map(|c| c * 1.001).collect();
    let low: Vec<f64> = close.iter().map(|c| c * 0.999).collect();
    let atr: Vec<f64> = vec![0.5; n];
    let signals: Vec<i8> = (0..n).map(|i| if i % 8 < 4 { 1 } else { -1 }).collect();
    let base_ns: i64 = 1_700_000_000_000_000_000;
    let ts: Vec<i64> = (0..n as i64)
        .map(|i| base_ns + i * 3_600_000_000_000)
        .collect();

    let result = run_backtest_sltp(
        &close, &high, &low, &atr, &signals, 0.001, 8760.0, 0, 2.0, 3.0, 0.0, 0.0, &ts,
    );

    assert_eq!(
        result.timestamps.len(),
        result.equity_curve.len(),
        "SLTP: timestamps.len() must equal equity_curve.len()"
    );
}
