//! v4.8 REGIME-01/02 additive-only schema guarantees.
//!
//! Phase 81 Plan 01 Task 2: None のとき既存 JSON に変化がなく、Some(...) のとき
//! Phase 79 SEAL ed enum 文字列 (0-60m / 60-120m / LOW / MID / HIGH) が emit されることを保証する。

use side_engine::scanner::macro_event::{DurationBucket, LiquidityRegime, SlotReport};

fn empty_slot() -> SlotReport {
    SlotReport {
        window_offset: 0,
        hold_bars: 1,
        exit_type: "time",
        fee_results: vec![],
        duration_bucket: None,
        liquidity_regime: None,
        per_trade_log: None,
    }
}

#[test]
fn byte_identity_when_none() {
    let s = empty_slot();
    let json = serde_json::to_string(&s).unwrap();
    assert!(
        !json.contains("duration_bucket"),
        "None should be skipped: {}",
        json
    );
    assert!(
        !json.contains("liquidity_regime"),
        "None should be skipped: {}",
        json
    );
}

#[test]
fn emits_sealed_strings_for_short_low() {
    let mut s = empty_slot();
    s.duration_bucket = Some(DurationBucket::Short);
    s.liquidity_regime = Some(LiquidityRegime::Low);
    let json = serde_json::to_string(&s).unwrap();
    assert!(json.contains("\"duration_bucket\":\"0-60m\""), "{}", json);
    assert!(json.contains("\"liquidity_regime\":\"LOW\""), "{}", json);
}

#[test]
fn emits_sealed_strings_for_long_high() {
    let mut s = empty_slot();
    s.duration_bucket = Some(DurationBucket::Long);
    s.liquidity_regime = Some(LiquidityRegime::High);
    let json = serde_json::to_string(&s).unwrap();
    assert!(json.contains("\"duration_bucket\":\"60-120m\""), "{}", json);
    assert!(json.contains("\"liquidity_regime\":\"HIGH\""), "{}", json);
}

#[test]
fn emits_sealed_strings_for_mid() {
    let mut s = empty_slot();
    s.liquidity_regime = Some(LiquidityRegime::Mid);
    let json = serde_json::to_string(&s).unwrap();
    assert!(json.contains("\"liquidity_regime\":\"MID\""), "{}", json);
}

#[test]
fn roundtrip_some() {
    // Deserialize は derive 済み。enum が文字列 SEAL value から復元できること。
    let v: DurationBucket = serde_json::from_value(serde_json::json!("0-60m")).unwrap();
    assert_eq!(v, DurationBucket::Short);
    let v: DurationBucket = serde_json::from_value(serde_json::json!("60-120m")).unwrap();
    assert_eq!(v, DurationBucket::Long);
    let v: LiquidityRegime = serde_json::from_value(serde_json::json!("LOW")).unwrap();
    assert_eq!(v, LiquidityRegime::Low);
    let v: LiquidityRegime = serde_json::from_value(serde_json::json!("MID")).unwrap();
    assert_eq!(v, LiquidityRegime::Mid);
    let v: LiquidityRegime = serde_json::from_value(serde_json::json!("HIGH")).unwrap();
    assert_eq!(v, LiquidityRegime::High);
}
