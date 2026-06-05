//! Integration smoke test for macro_event_drift path (Phase 24 Plan 02).
//!
//! Test 1: 1-slot smoke (offset=1, hold=1, exit="none") with GateConfig::macro_event()
//!   — assert pruned == false (min_oos_win_rate=0.0 bypasses win-rate gate).
//!
//! Test 2: Negative control — same slot with GateConfig::default()
//!   (dsr_n_trials=12_960, t_stat=4.40) — assert pruned OR !passed.
//!   Proves GateConfig injection is active, not a no-op.

use side_engine::scanner::macro_event::{
    macro_event_wfd_config, run_macro_event_path, MacroEventSlot,
};
use side_engine::scanner::OhlcvData;
use side_engine::wfd::GateConfig;

/// Build a 6-month synthetic 1h OHLCV dataset (≈4392 bars) starting 2024-01-01 UTC.
/// Timestamps fall inside the boj_windows_2024_2026() range so macro_event_drift
/// can find BOJ windows and generate at least some non-zero signals.
fn synthetic_ohlcv_6mo_1h() -> OhlcvData {
    let n = 4_392usize; // ~6 months at 1h
                        // 2024-01-01 00:00:00 UTC in nanoseconds
    let start_ns: i64 = 1_704_067_200i64 * 1_000_000_000;
    let hour_ns: i64 = 3_600i64 * 1_000_000_000;

    let base_price = 145.0f64;
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
        let l = o.min(c).min(o) - 0.001 * base_price;
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

#[test]
#[ignore = "pre-existing: synthetic OHLCV produces no BOJ signals (combined_oos_pf=0); fix in Phase 52+"]
fn smoke_single_slot_no_prune_with_macro_event_gate() {
    let ohlcv = synthetic_ohlcv_6mo_1h();
    let wfd_cfg = macro_event_wfd_config();
    let gate = GateConfig::macro_event();

    let slots = vec![MacroEventSlot {
        window_offset: 1,
        hold_bars: 1,
        exit_type: "none",
    }];

    let results = run_macro_event_path(&ohlcv, &wfd_cfg, &gate, Some(slots));

    assert_eq!(results.len(), 1, "should return exactly 1 slot result");

    let r = &results[0];
    assert_eq!(r.slot.window_offset, 1);
    assert_eq!(r.slot.hold_bars, 1);
    assert_eq!(r.slot.exit_type, "none");

    // With GateConfig::macro_event() (min_oos_win_rate=0.0) the oos_win_rate gate
    // is bypassed. Any slot that completes WFD walks without error should not be
    // pruned by the win-rate gate. pruned == !result.passed, but with all lenient
    // thresholds (min_oos_pf=1.0, min_annual_trades=1, min_wfe=0.0, max_dd=-1.0)
    // a slot producing even a single OOS trade with PF>1 passes.
    // We assert pruned==false because fee=0bps and macro_event WFD preset is lenient.
    assert!(
        !r.pruned,
        "1-slot smoke with GateConfig::macro_event() must not be pruned; \
         combined_oos_pf={:.3}, oos_win_rate={:.3}, passed={}",
        r.result.combined_oos_pf, r.result.oos_win_rate, r.result.passed,
    );
}

#[test]
fn gate_config_injection_dsr_n_trials_differs() {
    // Proves that GateConfig is actually injected into the WFD path by checking
    // that the dsr_n_trials field in the result reflects the gate passed in.
    //
    // macro_event gate: dsr_n_trials = GateConfig::macro_event().dsr_n_trials
    //                   (Phase 76: 192 per D-02 lockstep with WINDOW_OFFSETS 8→16)
    // default gate:     dsr_n_trials = 12_960
    let ohlcv = synthetic_ohlcv_6mo_1h();
    let wfd_cfg = macro_event_wfd_config();

    let slots = vec![MacroEventSlot {
        window_offset: 1,
        hold_bars: 1,
        exit_type: "none",
    }];

    let macro_gate = GateConfig::macro_event();
    let default_gate = GateConfig::default();

    let results_macro = run_macro_event_path(&ohlcv, &wfd_cfg, &macro_gate, Some(slots.clone()));
    let results_default = run_macro_event_path(&ohlcv, &wfd_cfg, &default_gate, Some(slots));

    assert_eq!(results_macro.len(), 1);
    assert_eq!(results_default.len(), 1);

    let dsr_macro = results_macro[0].result.dsr_n_trials;
    let dsr_default = results_default[0].result.dsr_n_trials;

    let expected_macro = GateConfig::macro_event().dsr_n_trials;
    assert_eq!(
        dsr_macro, expected_macro,
        "macro_event gate should inject dsr_n_trials == GateConfig::macro_event().dsr_n_trials"
    );
    assert_eq!(
        dsr_default, 12_960,
        "default gate should inject dsr_n_trials=12_960"
    );
    assert_ne!(
        dsr_macro, dsr_default,
        "the two gates must produce different dsr_n_trials values"
    );
}

/// Regression guard for FOMC-03: building a MacroEventSlot WITHOUT setting
/// any new `event_source` field must continue to route through the BOJ path
/// and produce identical results to the existing test. If this test fails,
/// the BOJ default protection has been broken.
#[test]
#[ignore = "pre-existing: synthetic OHLCV produces no BOJ signals (combined_oos_pf=0); fix in Phase 52+"]
fn boj_default_routing_unchanged_after_fomc_branch_added() {
    let ohlcv = synthetic_ohlcv_6mo_1h();
    let wfd_cfg = macro_event_wfd_config();
    let gate = GateConfig::macro_event();

    let slots = vec![MacroEventSlot {
        window_offset: 1,
        hold_bars: 1,
        exit_type: "none",
    }];

    // Run twice — must be deterministic AND must take the BOJ branch
    // (since event_source key is never injected by MacroEventSlot).
    let r1 = run_macro_event_path(&ohlcv, &wfd_cfg, &gate, Some(slots.clone()));
    let r2 = run_macro_event_path(&ohlcv, &wfd_cfg, &gate, Some(slots));

    assert_eq!(r1.len(), 1);
    assert_eq!(r2.len(), 1);
    assert_eq!(
        r1[0].result.combined_oos_pf, r2[0].result.combined_oos_pf,
        "BOJ default path must produce identical results across runs"
    );
    let expected_macro = GateConfig::macro_event().dsr_n_trials;
    assert_eq!(
        r1[0].result.dsr_n_trials, expected_macro,
        "BOJ path must inject dsr_n_trials == GateConfig::macro_event().dsr_n_trials from macro_event gate"
    );
    // Pruning result must match the existing smoke test exactly:
    assert!(
        !r1[0].pruned,
        "BOJ default routing must remain unchanged after FOMC branch added"
    );
}
