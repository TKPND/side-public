use side_engine::fetcher::dukascopy::{is_fx_closed_for, price_scale};

#[test]
fn btcusd_saturday_is_open() {
    // 2026-01-03 00:00 UTC = Saturday (epoch: 1767398400)
    let saturday_ms: i64 = 1_767_398_400_000;
    assert!(!is_fx_closed_for("BTCUSD", saturday_ms));
}

#[test]
fn ethusd_saturday_is_open() {
    let saturday_ms: i64 = 1_767_398_400_000;
    assert!(!is_fx_closed_for("ETHUSD", saturday_ms));
}

#[test]
fn usdjpy_saturday_is_closed() {
    let saturday_ms: i64 = 1_767_398_400_000;
    assert!(is_fx_closed_for("USDJPY", saturday_ms));
}

#[test]
fn btcusd_price_scale_differs_from_fx() {
    let btc_scale = price_scale("BTCUSD");
    let fx_scale = price_scale("USDJPY");
    assert_ne!(btc_scale, fx_scale);
    assert!(btc_scale > 0.0);
    assert_eq!(btc_scale, 100.0);
}

#[test]
fn ethusd_price_scale_is_100() {
    assert_eq!(price_scale("ETHUSD"), 100.0);
}
