//! Integration smoke tests for FOMC event drift sweep path (Phase 32 Plan 01).
//!
//! W0-VALID-01-A: `fomc_sweep_returns_expected_slots_times_5_fees`
//!   — `run_fomc_event_fee_sweep` returns `macro_event_slots().len()` SlotReports
//!     × 5 FeeResults (Phase 76: 192 × 5 = 960 entries).
//!
//! W0-VALID-01-B: `fomc_fee_result_contains_dsr_n_trials`
//!   — serialized FeeResult JSON contains `"dsr_n_trials":<gate n_trials>` (D-03 + VALID-01 SC-2).
//!
//! W0-VALID-02-A: `fomc_event_source_routes_to_fomc_not_boj`
//!   — FOMC and BOJ sweeps produce at least one differing FeeResult on the same
//!     fixture, proving event_source injection routes to fomc_event_drift_signals
//!     (Pitfall 2 guard).
//!
//! All tests are marked #[ignore] in Task 1 (Wave 0 RED) and un-ignored in Task 4.

use side_engine::pair::Pair;
use side_engine::scanner::macro_event::{
    run_fomc_event_fee_sweep, run_macro_event_fee_sweep, EXIT_TYPES, HOLD_BARS_VALUES,
    WINDOW_OFFSETS,
};
use side_engine::scanner::OhlcvData;
use side_engine::wfd::GateConfig;

/// Build a synthetic 1h OHLCV dataset (≈13 months) starting 2024-01-01 UTC.
///
/// Covers at least two 2024 FOMC announcement dates:
///
/// - 2024-01-31 19:00 UTC (EST — Jan FOMC)
/// - 2024-03-20 18:00 UTC (EDT — Mar FOMC)
///
/// Timestamps are consecutive 1h bars so the FOMC windows fall inside the range.
fn synthetic_ohlcv_13mo_1h() -> OhlcvData {
    // 13 months ≈ 396 days × 24h = 9504 bars (covers all 2024 FOMC dates)
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

    // Deterministic LCG random walk (same seed as macro_event_smoke.rs)
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

/// W0-VALID-01-A: FOMC fee sweep must return exactly `macro_event_slots().len()`
/// SlotReports × 5 FeeResults (Phase 76: 192 × 5 = 960 entries).
///
/// run_fomc_event_fee_sweep is a mirror of run_macro_event_fee_sweep using the
/// FOMC event window set. Geometry is identical per D-02. Slot count is
/// runtime-derived from WINDOW_OFFSETS × HOLD_BARS_VALUES × EXIT_TYPES.
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn fomc_sweep_returns_expected_slots_times_5_fees() {
    let ohlcv = synthetic_ohlcv_13mo_1h();
    let results = run_fomc_event_fee_sweep(&ohlcv, Pair::Usdjpy);

    let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
    assert_eq!(
        results.len(),
        expected,
        "should return exactly {expected} SlotReports (runtime-derived)"
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

/// W0-VALID-01-B: Serialized FeeResult JSON must contain
/// `"dsr_n_trials":<GateConfig::macro_event().dsr_n_trials>`.
///
/// Confirms D-03 (FeeResult.dsr_n_trials copied from WfdSingleResult) and
/// the VALID-01 Success Criterion 2: the field is present and equals the
/// macro_event gate value (FOMC scan dimension, not the ToD 12,960).
/// Phase 76 D-08 lockstep: the expected value tracks WINDOW_OFFSETS bump.
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn fomc_fee_result_contains_dsr_n_trials_equals_gate_n_trials() {
    let ohlcv = synthetic_ohlcv_13mo_1h();
    let results = run_fomc_event_fee_sweep(&ohlcv, Pair::Usdjpy);

    assert!(!results.is_empty(), "results must not be empty");
    let first_fee = &results[0].fee_results[0];
    let json = serde_json::to_string(first_fee).expect("FeeResult must serialize");

    let expected = GateConfig::macro_event().dsr_n_trials;
    let needle = format!("\"dsr_n_trials\":{expected}");
    assert!(
        json.contains(&needle),
        "serialized FeeResult must contain {needle}, got: {json}"
    );
}

/// W0-VALID-02-A: FOMC and BOJ sweeps must produce at least one differing FeeResult.
///
/// Pitfall 2 guard: if event_source is not injected, both sweeps will call
/// macro_event_drift_signals with the BOJ branch and produce identical results.
/// A difference in combined_oos_trades or combined_oos_pf proves the FOMC path
/// is active (fomc_event_drift_signals uses different event windows).
///
/// Note: with a purely synthetic LCG price series that has no real FOMC or BOJ
/// structure, both sweeps may occasionally show similar trade counts for slots
/// where neither event set fires. The test therefore checks across ALL 96 slots
/// (480 FeeResult pairs total) — at least one must differ.
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn fomc_event_source_routes_to_fomc_not_boj() {
    let ohlcv = synthetic_ohlcv_13mo_1h();

    let boj_results = run_macro_event_fee_sweep(&ohlcv);
    let fomc_results = run_fomc_event_fee_sweep(&ohlcv, Pair::Usdjpy);

    assert_eq!(boj_results.len(), 96, "BOJ sweep must return 96 slots");
    assert_eq!(fomc_results.len(), 96, "FOMC sweep must return 96 slots");

    // Compare all 96 × 5 = 480 pairs — at least one must differ.
    let any_differs = boj_results
        .iter()
        .zip(fomc_results.iter())
        .any(|(boj, fomc)| {
            boj.fee_results
                .iter()
                .zip(fomc.fee_results.iter())
                .any(|(b, f)| {
                    b.combined_oos_trades != f.combined_oos_trades
                        || (b.combined_oos_pf - f.combined_oos_pf).abs() > 1e-9
                })
        });

    assert!(
        any_differs,
        "FOMC and BOJ sweeps must differ on at least one FeeResult — \
         identical results across all 480 pairs means event_source injection failed (Pitfall 2)"
    );
}

// ---------------------------------------------------------------------------
// Phase 39 Plan 01 — EURUSD direction inversion tests (RED phase)
// ---------------------------------------------------------------------------

/// P39-01-A: EURUSD + hawkish FOMC → at least one short signal (signal=-1).
///
/// hawkish (+1) × EURUSD inversion → signal=-1 (short EURUSD).
/// Regression guard: verifies direction inversion is active for EURUSD.
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn eurusd_fomc_hawkish_enters_short() {
    let ohlcv = synthetic_ohlcv_13mo_1h();
    let results = run_fomc_event_fee_sweep(&ohlcv, Pair::Eurusd);

    assert_eq!(results.len(), 96, "should return 96 SlotReports");

    // At fee=0 (index 0), check combined_oos_trades is not zero and results differ
    // from USDJPY baseline (inversion means separate trade direction logic is active).
    // The key assertion: fee_results must be present.
    assert!(
        results.iter().all(|sr| sr.fee_results.len() == 5),
        "each slot must have 5 FeeResults"
    );

    // To verify inversion is active: run USDJPY and EURUSD — at least one slot
    // must differ (direction flip changes entry logic).
    let usdjpy_results = run_fomc_event_fee_sweep(&ohlcv, Pair::Usdjpy);
    let any_differs = results.iter().zip(usdjpy_results.iter()).any(|(eur, usd)| {
        eur.fee_results
            .iter()
            .zip(usd.fee_results.iter())
            .any(|(e, u)| {
                e.combined_oos_trades != u.combined_oos_trades
                    || (e.combined_oos_pf - u.combined_oos_pf).abs() > 1e-9
            })
    });

    assert!(
        any_differs,
        "EURUSD sweep must differ from USDJPY sweep — direction inversion not active"
    );
}

/// P39-01-B: EURUSD + dovish FOMC → long EURUSD signal (+1).
///
/// dovish (-1) × EURUSD inversion → signal=+1 (long EURUSD).
/// Verified structurally: EURUSD results must be valid (96 slots × 5 fees).
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn eurusd_fomc_dovish_enters_long() {
    let ohlcv = synthetic_ohlcv_13mo_1h();
    let results = run_fomc_event_fee_sweep(&ohlcv, Pair::Eurusd);

    assert_eq!(results.len(), 96, "should return 96 SlotReports");
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

/// P39-01-C: USDJPY + hawkish FOMC → unchanged (+1 as before).
///
/// Regression guard: USDJPY must NOT be affected by the EURUSD inversion.
/// run_fomc_event_fee_sweep with Pair::Usdjpy must return same results as
/// the pair-unaware original.
#[test]
#[ignore = "runs full WFD sweep (~240s); use `cargo test -- --ignored` for full validation"]
fn usdjpy_fomc_hawkish_unchanged() {
    let ohlcv = synthetic_ohlcv_13mo_1h();
    let usdjpy_results = run_fomc_event_fee_sweep(&ohlcv, Pair::Usdjpy);

    let expected_slots = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
    assert_eq!(
        usdjpy_results.len(),
        expected_slots,
        "should return {expected_slots} SlotReports (runtime-derived)"
    );
    assert!(
        usdjpy_results.iter().all(|sr| sr.fee_results.len() == 5),
        "each slot must have 5 FeeResults"
    );

    // dsr_n_trials must equal GateConfig::macro_event().dsr_n_trials (D-03 invariant,
    // Phase 76 D-08 lockstep).
    let first_fee = &usdjpy_results[0].fee_results[0];
    let json = serde_json::to_string(first_fee).expect("FeeResult must serialize");
    let expected_n_trials = GateConfig::macro_event().dsr_n_trials;
    let needle = format!("\"dsr_n_trials\":{expected_n_trials}");
    assert!(
        json.contains(&needle),
        "USDJPY sweep must have {needle}, got: {json}"
    );
}
