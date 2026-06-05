//! Integration tests for the `news_blackout` post-filter.
//!
//! Background: BigQuery tick analysis on USDJPY 2025 (34M ticks) found that
//! specific UTC windows consistently exhibit extreme volatility due to macro
//! releases (12:30, 13:30, 14:00, 22:00-10 NY close, 00:00 UTC roll, 15:00 ISM,
//! 00:50-59 Tokyo fixing).
//!
//! Windows re-validated 2026-04-08 with Iglewicz-Hoaglin modified z-score +
//! Bonferroni correction (|Z|>4.14) and H1/H2 split-half reproducibility.
//! The 22:* window was narrowed from the whole hour to 22:00-10 (whole-hour
//! median was 0.64x baseline, only the first 11 minutes are elevated).
//! The 00:50-59 Tokyo fix cluster was added (rank 1 slot, Z=5.80, H1=H2).
//!
//! See `project_bigquery_tick_findings.md` memory for full analysis.

use std::collections::HashMap;

use chrono::{TimeZone, Utc};
use serde_json::{json, Value};
use side_engine::strategies::{self, generate_signals, Ohlcv};

fn ts_ns(year: i32, month: u32, day: u32, hour: u32, minute: u32) -> i64 {
    Utc.with_ymd_and_hms(year, month, day, hour, minute, 0)
        .unwrap()
        .timestamp_nanos_opt()
        .unwrap()
}

fn sma_cross_params(news_blackout: Option<bool>) -> HashMap<String, Value> {
    let mut p = HashMap::new();
    p.insert("short_window".into(), json!(2));
    p.insert("long_window".into(), json!(3));
    if let Some(v) = news_blackout {
        p.insert("news_blackout".into(), json!(v));
    }
    p
}

fn make_ohlcv<'a>(close: &'a [f64], ts: Option<&'a [i64]>) -> Ohlcv<'a> {
    Ohlcv {
        open: close,
        high: close,
        low: close,
        close,
        volume: close,
        datetimes_ns: ts,
        aux_close: None,
    }
}

/// Prices chosen so sma_cross(2,3) gives:
///   signals = [0, 0, 0, +1, 0, 0, 0, -1, 0, 0]
/// (entry long at bar 3, reversal at bar 7)
fn baseline_close() -> Vec<f64> {
    vec![
        100.0, 100.0, 100.0, 110.0, 120.0, 120.0, 121.0, 80.0, 70.0, 60.0,
    ]
}

#[test]
fn is_news_blackout_matches_known_windows() {
    // In-window samples (should be blackout)
    let in_window = [
        (12, 25), // European/UK/Canada data
        (12, 30),
        (12, 35),
        (13, 25), // US 8:30 ET (CPI/PPI/retail, weekly jobless claims)
        (13, 30),
        (13, 55), // early window for US 9:00 ET
        (14, 0),
        (14, 10),
        (14, 55), // early window for US 10:00 ET (ISM/JOLTS)
        (15, 5),
        (22, 0), // NY close thin liquidity — now narrowed to 22:00-10
        (22, 5),
        (22, 10),
        (0, 0), // UTC date roll
        (0, 10),
        (0, 50), // Tokyo fixing (9:55 JST) — new 2026-04-08 finding
        (0, 54),
        (0, 55),
        (0, 59),
    ];
    for (h, m) in in_window {
        let dt = Utc.with_ymd_and_hms(2025, 4, 8, h, m, 0).unwrap();
        assert!(
            strategies::is_news_blackout(&dt),
            "expected {h:02}:{m:02} UTC to be in blackout"
        );
    }

    // Out-of-window samples (should NOT be blackout)
    let out_window = [
        (9, 0),
        (10, 30),
        (11, 0),
        (12, 24), // just before 12:25
        (12, 36), // just after 12:35
        (13, 0),
        (13, 24),
        (13, 36),
        (13, 54),
        (14, 11),
        (14, 54),
        (15, 11),
        (16, 0),
        (17, 0),
        (18, 0),
        (21, 59),
        (22, 11), // just after narrowed 22:00-10 — no longer blackout
        (22, 30), // hour-22 dead zone
        (22, 59),
        (23, 0),
        (0, 11),
        (0, 49), // just before Tokyo fix window
        (1, 0),
    ];
    for (h, m) in out_window {
        let dt = Utc.with_ymd_and_hms(2025, 4, 8, h, m, 0).unwrap();
        assert!(
            !strategies::is_news_blackout(&dt),
            "expected {h:02}:{m:02} UTC to be outside blackout"
        );
    }
}

#[test]
fn news_blackout_disabled_passes_through() {
    let close = baseline_close();
    let ts: Vec<i64> = (0..close.len())
        .map(|i| ts_ns(2025, 4, 8, 13, 30) + i as i64 * 3_600_000_000_000)
        .collect();

    // Sanity: with news_blackout not set at all
    let params = sma_cross_params(None);
    let signals = generate_signals("sma_cross", &make_ohlcv(&close, Some(&ts)), &params);
    assert_eq!(signals[3], 1, "baseline: entry at bar 3");
    assert_eq!(signals[7], -1, "baseline: reversal at bar 7");

    // With news_blackout=false explicitly
    let params_false = sma_cross_params(Some(false));
    let signals_false =
        generate_signals("sma_cross", &make_ohlcv(&close, Some(&ts)), &params_false);
    assert_eq!(
        signals, signals_false,
        "news_blackout=false must match baseline"
    );
}

#[test]
fn news_blackout_without_datetimes_passes_through() {
    // Filter requires datetimes_ns; without it, signals must pass through unchanged.
    let close = baseline_close();
    let params_off = sma_cross_params(None);
    let baseline = generate_signals("sma_cross", &make_ohlcv(&close, None), &params_off);

    let params_on = sma_cross_params(Some(true));
    let filtered = generate_signals("sma_cross", &make_ohlcv(&close, None), &params_on);
    assert_eq!(baseline, filtered, "no datetimes_ns → filter is a no-op");
}

#[test]
fn news_blackout_defers_entry_signal_to_next_safe_bar() {
    // Arrange timestamps so bar 3 (original entry) lands in the 13:25-35 blackout,
    // bar 4 is safe, bar 7 (original reversal) lands at 22:00 blackout, and bar 8 is safe.
    let close = baseline_close();
    let ts = vec![
        ts_ns(2025, 4, 8, 9, 0),   // bar 0 — safe
        ts_ns(2025, 4, 8, 10, 0),  // bar 1 — safe
        ts_ns(2025, 4, 8, 11, 0),  // bar 2 — safe
        ts_ns(2025, 4, 8, 13, 30), // bar 3 — BLACKOUT (13:25-35)
        ts_ns(2025, 4, 8, 16, 0),  // bar 4 — safe (deferred entry here)
        ts_ns(2025, 4, 8, 17, 0),  // bar 5 — safe
        ts_ns(2025, 4, 8, 18, 0),  // bar 6 — safe
        ts_ns(2025, 4, 8, 22, 0),  // bar 7 — BLACKOUT (22:*)
        ts_ns(2025, 4, 9, 9, 0),   // bar 8 — safe next day (deferred reversal here)
        ts_ns(2025, 4, 9, 10, 0),  // bar 9 — safe
    ];

    let params = sma_cross_params(Some(true));
    let signals = generate_signals("sma_cross", &make_ohlcv(&close, Some(&ts)), &params);

    let expected = vec![0i8, 0, 0, 0, 1, 0, 0, 0, -1, 0];
    assert_eq!(
        signals, expected,
        "entry deferred from bar 3→4, reversal deferred from bar 7→8"
    );
}

#[test]
fn news_blackout_holds_pending_across_consecutive_blackout_bars() {
    // If blackout spans multiple bars, the deferred signal is held until the
    // first safe bar, then emitted once.
    let close = baseline_close();
    let ts = vec![
        ts_ns(2025, 4, 8, 9, 0),   // 0 safe
        ts_ns(2025, 4, 8, 10, 0),  // 1 safe
        ts_ns(2025, 4, 8, 11, 0),  // 2 safe
        ts_ns(2025, 4, 8, 13, 30), // 3 BLACKOUT — entry blocked
        ts_ns(2025, 4, 8, 14, 0),  // 4 BLACKOUT (13:55-14:10) — still held
        ts_ns(2025, 4, 8, 15, 30), // 5 safe — pending entry fires here
        ts_ns(2025, 4, 8, 16, 0),  // 6 safe
        ts_ns(2025, 4, 8, 17, 0),  // 7 safe — reversal fires on time (not blackout)
        ts_ns(2025, 4, 8, 18, 0),  // 8 safe
        ts_ns(2025, 4, 8, 19, 0),  // 9 safe
    ];

    let params = sma_cross_params(Some(true));
    let signals = generate_signals("sma_cross", &make_ohlcv(&close, Some(&ts)), &params);

    let expected = vec![0i8, 0, 0, 0, 0, 1, 0, -1, 0, 0];
    assert_eq!(
        signals, expected,
        "entry held through bars 3-4, fires at bar 5; reversal untouched at bar 7"
    );
}
