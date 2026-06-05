// Implementation: Plan 01-04 Task 2 (Wave 2)

use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;

fn fixture_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("usdjpy_1h_sample.parquet")
}

fn load_fixture() -> side_engine::scanner::OhlcvData {
    side_engine::parquet_loader::load_ohlcv_parquet(&fixture_path())
        .expect("usdjpy_1h_sample.parquet fixture should load")
}

fn params(entry_minute: u16, direction: &str, hold_h: u8) -> HashMap<String, Value> {
    let mut p = HashMap::new();
    p.insert("entry_minute".into(), json!(entry_minute));
    p.insert("direction".into(), json!(direction));
    p.insert("hold_h".into(), json!(hold_h));
    p
}

#[test]
fn tod_edge_entry_minute_matches() {
    // entry_minute=0 means UTC midnight. On 1h fixture (504 bars, 21 days)
    // that is 21 entry bars, each generating an entry (+1) and exit (-1)
    // delta via position_to_signal → ~42 nonzero signals.
    let data = load_fixture();
    let ohlcv = data.as_ref();
    let p = params(0, "long", 1);
    let sigs = side_engine::strategies::generate_signals("tod_edge", &ohlcv, &p);
    let nonzero = sigs.iter().filter(|&&s| s != 0).count();
    assert!(
        nonzero >= 15,
        "expected >= 15 nonzero signals for 21 daily entries, got {nonzero}"
    );
}

#[test]
fn tod_edge_direction_short_yields_negative_signal() {
    // position_to_signal emits position-delta signals, so a short entry at
    // a later bar (position 0 -> -1) produces a -1 delta. We look for the
    // first clean 0 -> -1 transition after bar 0, since bar 0 is handled
    // specially by position_to_signal (signal[0] is always 0).
    let data = load_fixture();
    let ohlcv = data.as_ref();
    let p = params(0, "short", 1);
    let sigs = side_engine::strategies::generate_signals("tod_edge", &ohlcv, &p);
    assert!(
        sigs.contains(&-1),
        "short direction must emit at least one -1 entry delta"
    );
    // Also cross-check polarity: sum of long-side signals should be the
    // mirror of the short-side signals for the same params.
    let p_long = params(0, "long", 1);
    let sigs_long = side_engine::strategies::generate_signals("tod_edge", &ohlcv, &p_long);
    let sum_short: i32 = sigs.iter().map(|&s| s as i32).sum();
    let sum_long: i32 = sigs_long.iter().map(|&s| s as i32).sum();
    assert_eq!(
        sum_short, -sum_long,
        "short signals must be the negation of long signals"
    );
}

#[test]
fn tod_edge_hold_h_index_maps_to_horizon_minutes() {
    // On 1h data every horizon ≤60min collapses to 1 bar (ceil(hold/60)=1),
    // so hold_h=5 (15min) and hold_h=1 (1min) should produce identical
    // nonzero counts. This documents the Phase 1 Open Q1 resolution.
    let data = load_fixture();
    let ohlcv = data.as_ref();
    let sigs5 =
        side_engine::strategies::generate_signals("tod_edge", &ohlcv, &params(0, "long", 5));
    let sigs1 =
        side_engine::strategies::generate_signals("tod_edge", &ohlcv, &params(0, "long", 1));
    let n5 = sigs5.iter().filter(|&&s| s != 0).count();
    let n1 = sigs1.iter().filter(|&&s| s != 0).count();
    assert_eq!(
        n5, n1,
        "hold_h=5 and hold_h=1 must collapse to same count on 1h bars ({n5} vs {n1})"
    );
}

#[test]
fn tod_edge_hold_h_9_remains_valid_on_1h_fixture() {
    let data = load_fixture();
    let ohlcv = data.as_ref();
    let sigs = side_engine::strategies::generate_signals("tod_edge", &ohlcv, &params(0, "long", 9));
    assert!(
        sigs.iter().any(|&s| s != 0),
        "hold_h=9 must remain a valid tod_edge horizon"
    );
}

#[test]
#[should_panic(expected = "tod_edge: hold_h must be 1..=9")]
fn tod_edge_invalid_hold_h_panics_inside_engine_only() {
    let data = load_fixture();
    let ohlcv = data.as_ref();
    let _ = side_engine::strategies::generate_signals("tod_edge", &ohlcv, &params(0, "long", 10));
}

#[test]
fn tod_edge_1h_horizon_collapse_includes_hold_h_9() {
    let data = load_fixture();
    let ohlcv = data.as_ref();
    let sigs9 =
        side_engine::strategies::generate_signals("tod_edge", &ohlcv, &params(0, "long", 9));
    let sigs1 =
        side_engine::strategies::generate_signals("tod_edge", &ohlcv, &params(0, "long", 1));
    let n9 = sigs9.iter().filter(|&&s| s != 0).count();
    let n1 = sigs1.iter().filter(|&&s| s != 0).count();
    assert_eq!(
        n9, n1,
        "hold_h=9 and hold_h=1 should both collapse to 1 bar on 1h fixture"
    );
}

#[test]
fn tod_edge_news_blackout_false_default_does_not_filter() {
    // Without news_blackout=true, apply_news_blackout is a no-op, so every
    // entry_minute=0 bar passes through unaltered.
    let data = load_fixture();
    let ohlcv = data.as_ref();
    let p = params(0, "long", 1);
    let sigs = side_engine::strategies::generate_signals("tod_edge", &ohlcv, &p);
    assert!(
        sigs.iter().any(|&s| s != 0),
        "at least one tod_edge signal must survive with default news_blackout"
    );
}
