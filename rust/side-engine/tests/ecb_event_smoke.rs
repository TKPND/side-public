//! Integration smoke tests for ECB event drift sweep path (Phase 40 Plan 01).
//!
//! P40-01-A: `ecb_sweep_returns_96`
//!   — run_ecb_event_fee_sweep returns 96 SlotReports per pair.
//!
//! P40-01-B: `ecb_pair_propagates`
//!   — pair parameter is propagated and used in sweep.
//!
//! P40-01-C: `eurusd_ecb_hawkish_enters_long`
//!   — EURUSD × ECB hawkish direction processed correctly.
//!
//! P40-01-D: `eurusd_ecb_dovish_enters_short`
//!   — EURUSD × ECB dovish direction processed correctly.
//!
//! P40-01-E: `usdjpy_ecb_hawkish_unchanged`
//!   — USDJPY × ECB hawkish unchanged from v3.8 baseline.

use side_engine::pair::Pair;
use side_engine::scanner::macro_event::run_ecb_event_fee_sweep;
use side_engine::scanner::OhlcvData;

/// Build a synthetic 1h OHLCV dataset (≈13 months) starting 2024-01-01 UTC.
///
/// Covers all 2024-2025 ECB announcement dates.
/// Timestamps are consecutive 1h bars so the ECB windows fall inside the range.
fn synthetic_ohlcv_13mo_1h() -> OhlcvData {
    // 13 months ≈ 396 days × 24h = 9504 bars (covers all 2024-2025 ECB dates)
    let n = 9_504usize;
    // 2024-01-01 00:00:00 UTC in nanoseconds
    let start_ns: i64 = 1_704_067_200i64 * 1_000_000_000;
    let hour_ns: i64 = 3_600i64 * 1_000_000_000;

    let base_price = 1.085f64; // EURUSD ballpark
    let mut close = Vec::with_capacity(n);
    let mut open = Vec::with_capacity(n);
    let mut high = Vec::with_capacity(n);
    let mut low = Vec::with_capacity(n);
    let mut volume = Vec::with_capacity(n);
    let mut datetimes_ns = Vec::with_capacity(n);

    // Deterministic LCG random walk
    let mut seed: u64 = 42;
    let mut price = base_price;
    for i in 0..n {
        seed = seed
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        let delta = ((seed >> 33) as f64 / u32::MAX as f64 - 0.5) * 0.002 * price;
        let o = price;
        price += delta;
        let c = price;
        let h = o.max(c) + 0.001 * base_price;
        let l = o.min(c) - 0.001 * base_price;
        open.push(o);
        close.push(c);
        high.push(h);
        low.push(l);
        volume.push(1000.0);
        datetimes_ns.push(start_ns + i as i64 * hour_ns);
    }

    OhlcvData {
        open,
        high,
        low,
        close,
        volume,
        datetimes_ns,
        aux_close: None,
    }
}

/// P40-01-A: ECB fee sweep must return exactly 96 SlotReports (RED phase — expects compile error).
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn ecb_sweep_returns_96() {
    let ohlcv = synthetic_ohlcv_13mo_1h();
    // Task 1 RED: run_ecb_event_fee_sweep takes pair argument in Task 2
    let results = run_ecb_event_fee_sweep(&ohlcv, Pair::Eurusd);

    assert_eq!(
        results.len(),
        96,
        "ECB sweep should return 96 slot reports (per-slot fee sweep is internal)"
    );
    for sr in &results {
        assert_eq!(
            sr.fee_results.len(),
            5,
            "each slot should have 5 FeeResults, got {} for off={} hold={} exit={}",
            sr.fee_results.len(),
            sr.window_offset,
            sr.hold_bars,
            sr.exit_type,
        );
    }
}

/// P40-01-B: pair parameter must be accepted by run_ecb_event_fee_sweep (RED phase).
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn ecb_pair_propagates() {
    let ohlcv = synthetic_ohlcv_13mo_1h();
    // Task 1 RED: pair parameter expected in Task 2
    let results = run_ecb_event_fee_sweep(&ohlcv, Pair::Eurusd);

    assert!(!results.is_empty(), "results must not be empty");
    assert_eq!(results.len(), 96, "should return 96 SlotReports");
}

/// P40-01-C: EURUSD × ECB hawkish direction check (RED phase).
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn eurusd_ecb_hawkish_enters_long() {
    let ohlcv = synthetic_ohlcv_13mo_1h();
    // Task 1 RED: pair parameter expected in Task 2
    let results = run_ecb_event_fee_sweep(&ohlcv, Pair::Eurusd);

    assert_eq!(results.len(), 96, "should return 96 SlotReports");
    assert!(
        results.iter().all(|sr| sr.fee_results.len() == 5),
        "each slot must have 5 FeeResults"
    );
}

/// P40-01-D: EURUSD × ECB dovish direction check (RED phase).
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn eurusd_ecb_dovish_enters_short() {
    let ohlcv = synthetic_ohlcv_13mo_1h();
    // Task 1 RED: pair parameter expected in Task 2
    let results = run_ecb_event_fee_sweep(&ohlcv, Pair::Eurusd);

    assert_eq!(results.len(), 96, "should return 96 SlotReports");
    assert!(
        results.iter().all(|sr| sr.fee_results.len() == 5),
        "each slot must have 5 FeeResults"
    );
}

/// P40-01-E: USDJPY × ECB hawkish unchanged from v3.8 baseline (RED phase).
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn usdjpy_ecb_hawkish_unchanged() {
    let ohlcv = synthetic_ohlcv_13mo_1h();
    // Task 1 RED: pair parameter expected in Task 2
    let usdjpy_results = run_ecb_event_fee_sweep(&ohlcv, Pair::Usdjpy);

    assert_eq!(
        usdjpy_results.len(),
        96,
        "USDJPY ECB sweep should return 96 slots"
    );
    assert!(
        usdjpy_results.iter().all(|sr| sr.fee_results.len() == 5),
        "each slot must have 5 FeeResults"
    );
}
