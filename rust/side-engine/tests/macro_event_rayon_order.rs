//! Determinism guard for `run_fomc_event_fee_sweep` under rayon parallelization.
//!
//! Phase 70 Plan 01 Wave 0 (RED): locks in the canonical order invariant
//! BEFORE `slots.into_iter()` is switched to `slots.into_par_iter()`. Both tests
//! must remain GREEN after Task 2 applies rayon, proving that `.into_par_iter().collect()`
//! preserves input order (rayon contract) and produces bit-exact repeat results.
//! Phase 76 D-03: slot count is runtime-derived
//! (`WINDOW_OFFSETS.len() × HOLD_BARS_VALUES.len() × EXIT_TYPES.len()`; 192 after Wave-1).
//!
//! - `rayon_order_fomc_is_canonical`: returned `Vec<SlotReport>` must match
//!   `macro_event_slots()` index-for-index on `(window_offset, hold_bars, exit_type)`.
//! - `rayon_order_fomc_is_stable`: two identical calls must produce identical slot
//!   triples (guards against rayon non-deterministic thread scheduling leaking into
//!   output ordering).
//!
//! Fixture: synthetic 40d × 1h OHLCV (960 bars) starting 2024-01-01 UTC — deliberately
//! smaller than `fomc_event_smoke::synthetic_ohlcv_13mo_1h` so the determinism tests
//! are not `#[ignore]`. 40d covers the 2024-01-31 FOMC announcement, which exercises
//! the event-drift code path at least once.

use side_engine::pair::Pair;
use side_engine::scanner::macro_event::{
    macro_event_slots, run_fomc_event_fee_sweep, SlotReport, EXIT_TYPES, HOLD_BARS_VALUES,
    WINDOW_OFFSETS,
};
use side_engine::scanner::OhlcvData;

/// Build a tiny synthetic 1h OHLCV dataset (40 days = 960 bars) starting 2024-01-01 UTC.
///
/// Covers 2024-01-31 FOMC (bar ≈ 720). Smaller than the 13mo fixture in
/// `fomc_event_smoke.rs` so the determinism tests are not `#[ignore]`.
fn tiny_fixture_ohlcv() -> OhlcvData {
    let n = 960usize; // 40 days × 24 h
    let start_ns: i64 = 1_704_067_200i64 * 1_000_000_000; // 2024-01-01 00:00 UTC
    let hour_ns: i64 = 3_600i64 * 1_000_000_000;

    let base_price = 150.0f64; // USDJPY ballpark — matches Pair::Usdjpy in tests
    let mut close = Vec::with_capacity(n);
    let mut open = Vec::with_capacity(n);
    let mut high = Vec::with_capacity(n);
    let mut low = Vec::with_capacity(n);
    let mut volume = Vec::with_capacity(n);
    let mut datetimes_ns = Vec::with_capacity(n);

    // Deterministic LCG (same constants as fomc_event_smoke.rs).
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

fn slot_triple(r: &SlotReport) -> (u32, u32, &'static str) {
    (r.window_offset, r.hold_bars, r.exit_type)
}

#[test]
fn rayon_order_fomc_is_canonical() {
    let ohlcv = tiny_fixture_ohlcv();
    let reports = run_fomc_event_fee_sweep(&ohlcv, Pair::Usdjpy);
    let slots = macro_event_slots();

    let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
    assert_eq!(reports.len(), expected, "expected {expected} SlotReports");
    assert_eq!(
        slots.len(),
        expected,
        "canonical slot enumeration must be {expected}"
    );

    for (i, rpt) in reports.iter().enumerate() {
        assert_eq!(
            rpt.window_offset, slots[i].window_offset,
            "slot {i} window_offset mismatch"
        );
        assert_eq!(
            rpt.hold_bars, slots[i].hold_bars,
            "slot {i} hold_bars mismatch"
        );
        assert_eq!(
            rpt.exit_type, slots[i].exit_type,
            "slot {i} exit_type mismatch"
        );
    }
}

#[test]
fn rayon_order_fomc_is_stable() {
    let ohlcv = tiny_fixture_ohlcv();
    let a = run_fomc_event_fee_sweep(&ohlcv, Pair::Usdjpy);
    let b = run_fomc_event_fee_sweep(&ohlcv, Pair::Usdjpy);

    assert_eq!(a.len(), b.len(), "report count must match across runs");
    for (x, y) in a.iter().zip(b.iter()) {
        assert_eq!(
            slot_triple(x),
            slot_triple(y),
            "slot triple must match across runs"
        );
    }
}
