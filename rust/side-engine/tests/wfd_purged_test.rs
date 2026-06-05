//! Phase 2 purged k-fold integration tests — Wave 0 scaffolds.
//! Wave 1 plans 02-02 and 02-06 will un-ignore and fill each test.

#![allow(clippy::field_reassign_with_default)]

use std::path::PathBuf;

fn fixture_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("usdjpy_1h_sample.parquet")
}

#[test]
fn run_wfd_single_with_purged_kfold_on_fixture() {
    use side_engine::wfd::{run_wfd_single, CvMode, WfdConfig};

    // Load the 504-bar hourly fixture (21 days)
    let data = side_engine::parquet_loader::load_ohlcv_parquet(&fixture_path())
        .expect("usdjpy_1h_sample.parquet fixture should load");

    let ohlcv = data.as_ref();
    let n = ohlcv.close.len();
    assert!(n >= 500, "fixture must have at least 500 bars, got {n}");

    let datetimes_ns = ohlcv.datetimes_ns.expect("fixture must have timestamps");

    // Use PurgedKFold with k=5, embargo_days=1 (24 bars for hourly data)
    let mut cfg = WfdConfig::default();
    cfg.cv_mode = CvMode::PurgedKFold {
        k: 5,
        embargo_days: 1,
    };
    cfg.min_oos_pf = 0.0;
    cfg.min_annual_trades = 0;
    cfg.min_wfe = 0.0;
    cfg.min_oos_win_rate = 0.0;

    let params = {
        let mut p = std::collections::HashMap::new();
        p.insert("entry_minute".into(), serde_json::json!(0));
        p.insert("direction".into(), serde_json::json!("long"));
        p.insert("hold_h".into(), serde_json::json!(1));
        p
    };

    let result = run_wfd_single(
        ohlcv.open,
        ohlcv.high,
        ohlcv.low,
        ohlcv.close,
        ohlcv.volume,
        datetimes_ns,
        None,
        "tod_edge",
        &params,
        &cfg,
        "1h",
        None,
        1,
    );

    assert_eq!(
        result.walks.len(),
        5,
        "purged 5-fold must produce exactly 5 walks, got {}",
        result.walks.len()
    );
    for (i, w) in result.walks.iter().enumerate() {
        assert!(
            w.oos_pf.is_finite() || w.oos_pf == f64::INFINITY,
            "walk {} OOS pf must be finite or infinity, got {}",
            i,
            w.oos_pf
        );
    }
}

// NOTE: Plan 02-06 moved the `edges_fast_path_verdict_has_6_gates` integration
// test into `rust/side-cli/tests/scan_pass_mode_test.rs` because the test must
// invoke the `side` binary via `CARGO_BIN_EXE_side`, which is only set for tests
// inside the binary's own crate. The library-only side-engine crate cannot
// resolve cross-crate `CARGO_BIN_EXE_*` envs without a dev-dependency on
// side-cli (which would create a dependency cycle). See 02-06-SUMMARY.md.
