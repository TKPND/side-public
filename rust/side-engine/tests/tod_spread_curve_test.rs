// Wave 2 implementation — see 01-05-PLAN.md
// Tests tod_multiplier buckets + run_backtest_with_tod wrapper.

#![allow(clippy::needless_range_loop)]

use chrono::{DateTime, TimeZone, Utc};
use std::path::PathBuf;

fn dt(year: i32, month: u32, day: u32, hour: u32, min: u32) -> DateTime<Utc> {
    Utc.with_ymd_and_hms(year, month, day, hour, min, 0)
        .unwrap()
}

fn fixture_parquet() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("usdjpy_1h_sample.parquet")
}

#[test]
fn tod_multiplier_tokyo_bucket_returns_0_8() {
    assert_eq!(
        side_engine::backtest::tod_multiplier(&dt(2024, 1, 1, 0, 30)),
        0.8
    );
    assert_eq!(
        side_engine::backtest::tod_multiplier(&dt(2024, 1, 1, 1, 59)),
        0.8
    );
}

#[test]
fn tod_multiplier_london_bucket_returns_1_0() {
    assert_eq!(
        side_engine::backtest::tod_multiplier(&dt(2024, 1, 1, 7, 30)),
        1.0
    );
}

#[test]
fn tod_multiplier_ny_rollover_bucket_returns_2_0() {
    assert_eq!(
        side_engine::backtest::tod_multiplier(&dt(2024, 1, 1, 21, 30)),
        2.0
    );
}

#[test]
fn tod_multiplier_other_hours_returns_1_0() {
    assert_eq!(
        side_engine::backtest::tod_multiplier(&dt(2024, 1, 1, 14, 0)),
        1.0
    );
    assert_eq!(
        side_engine::backtest::tod_multiplier(&dt(2024, 1, 1, 3, 0)),
        1.0
    );
    assert_eq!(
        side_engine::backtest::tod_multiplier(&dt(2024, 1, 1, 12, 0)),
        1.0
    );
}

#[test]
fn run_backtest_with_tod_curve_applies_multiplier_to_fee() {
    let data = side_engine::parquet_loader::load_ohlcv_parquet(&fixture_parquet()).unwrap();
    let n = data.close.len();
    assert!(n >= 24, "fixture must have at least 24 bars");

    // Inject entry/exit signals at several bars, including NY-rollover (hour 21)
    // to ensure the TOD multiplier (2.0x) diverges from the vanilla backtest.
    let mut signals = vec![0i8; n];
    for i in 0..n.saturating_sub(1) {
        // Alternate long/flat every 4 bars; guarantees position changes across multiple hours.
        if i % 4 == 0 {
            signals[i] = 1;
        } else if i % 4 == 2 {
            signals[i] = -1;
        }
    }

    // Fee is a raw fraction per position-unit change (not bps). 0.001 = 10 bps.
    // Small enough to keep equity positive, large enough to make the TOD delta visible.
    let (fee, ppy, mode) = side_engine::backtest::backtest_call_args(0.001, "1h");

    let base = side_engine::backtest::run_backtest(
        &data.close,
        &signals,
        fee,
        ppy,
        mode,
        &data.datetimes_ns,
    );
    let tod = side_engine::backtest::run_backtest_with_tod(
        &data.close,
        &signals,
        fee,
        ppy,
        mode,
        &data.datetimes_ns,
    );

    // With NY-rollover bars receiving 2.0x fee and Tokyo bars 0.8x,
    // the total cost (and therefore total_return) must differ from vanilla.
    assert_ne!(
        base.total_return, tod.total_return,
        "TOD curve should modify total_return when signals cross non-1.0x buckets"
    );
    // Equity curves must differ.
    assert_ne!(
        base.equity_curve.last().copied(),
        tod.equity_curve.last().copied(),
        "TOD curve should modify final equity"
    );
}
