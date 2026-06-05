use side_engine::paper::PaperConfig;

#[test]
fn test_load_paper_config() {
    let json = r#"{
        "slots": [
            {
                "asset": "USD/JPY",
                "strategy_name": "keltner",
                "params": {"ema_period": 27, "atr_period": 9, "atr_multiplier": 1.13},
                "aux_source": {"id": "yf:^VIX"},
                "timeframe": "1h"
            }
        ],
        "initial_capital": 10000,
        "weight_method": "equal",
        "data_lookback_bars": 500
    }"#;
    let config: PaperConfig = serde_json::from_str(json).unwrap();
    assert_eq!(config.slots.len(), 1);
    assert_eq!(config.slots[0].asset, "USD/JPY");
    assert_eq!(config.slots[0].strategy_name, "keltner");
    assert_eq!(config.initial_capital, 10000.0);
}

#[test]
fn test_slot_id_generation() {
    let json = r#"{
        "slots": [
            {"asset": "USD/JPY", "strategy_name": "keltner", "params": {}, "aux_source": {"id": "yf:^VIX"}, "timeframe": "1h"},
            {"asset": "USD/JPY", "strategy_name": "keltner", "params": {}, "aux_source": {"id": "yf:^GSPC"}, "timeframe": "1h"}
        ],
        "initial_capital": 10000
    }"#;
    let config: PaperConfig = serde_json::from_str(json).unwrap();
    let ids = config.slot_ids();
    assert_eq!(ids[0], "USD/JPY/keltner/^VIX#1");
    assert_eq!(ids[1], "USD/JPY/keltner/^GSPC#2");
}

#[test]
fn test_load_real_config_file() {
    let config = PaperConfig::from_file("../../config/paper_slots.json").unwrap();
    assert_eq!(config.slots.len(), 5);
    assert_eq!(config.initial_capital, 10000.0);
}

#[test]
fn test_equal_allocation() {
    let json = r#"{
        "slots": [
            {"asset": "A", "strategy_name": "s1", "params": {}, "timeframe": "1h"},
            {"asset": "B", "strategy_name": "s2", "params": {}, "timeframe": "1h"}
        ],
        "initial_capital": 10000,
        "weight_method": "equal"
    }"#;
    let config: PaperConfig = serde_json::from_str(json).unwrap();
    let allocs = config.allocations();
    assert_eq!(allocs.len(), 2);
    assert!((allocs[0] - 5000.0).abs() < 0.01);
    assert!((allocs[1] - 5000.0).abs() < 0.01);
}
