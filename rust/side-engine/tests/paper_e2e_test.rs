//! E2E test: config -> PaperTrader -> tick_with_data -> verify DB state.
//! No network access needed (synthetic data).

use chrono::NaiveDateTime;
use side_engine::fetcher::types::Bar;
use side_engine::paper::db::PaperDb;
use side_engine::paper::{PaperConfig, PaperTrader};
use std::collections::HashMap;

fn make_bars(n: usize, base_price: f64, trend: f64) -> Vec<Bar> {
    (0..n)
        .map(|i| {
            let price = base_price + i as f64 * trend;
            Bar {
                datetime: NaiveDateTime::parse_from_str(
                    &format!("2026-03-{:02} {:02}:00:00", 10 + i / 24, i % 24),
                    "%Y-%m-%d %H:%M:%S",
                )
                .unwrap(),
                open: price - 0.05,
                high: price + 0.3,
                low: price - 0.3,
                close: price,
                volume: 1000.0,
            }
        })
        .collect()
}

#[test]
fn test_e2e_multi_slot_tick_sequence() {
    let config_json = r#"{
        "slots": [
            {"asset": "USD/JPY", "strategy_name": "ema_atr", "params": {"short_ema": 10, "long_ema": 30, "atr_period": 14, "atr_multiplier": 1.5}, "timeframe": "1h"},
            {"asset": "USD/CHF", "strategy_name": "sma_cross", "params": {"short_window": 10, "long_window": 30}, "timeframe": "1h"}
        ],
        "initial_capital": 10000
    }"#;
    let config: PaperConfig = serde_json::from_str(config_json).unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    // 200 bars of uptrend data for both assets
    let bars_jpy = make_bars(200, 150.0, 0.05);
    let bars_chf = make_bars(200, 0.88, 0.001);

    let mut data = HashMap::new();
    data.insert("USD/JPY".to_string(), (bars_jpy, None));
    data.insert("USD/CHF".to_string(), (bars_chf, None));

    // Run 3 ticks (simulating 3 hourly ticks with same data)
    for _ in 0..3 {
        trader.tick_with_data(&data).unwrap();
    }

    // Verify DB has ticks recorded (2 slots x 3 ticks = 6)
    let tick_count: i64 = trader
        .db()
        .conn()
        .query_row("SELECT COUNT(*) FROM ticks", [], |r| r.get(0))
        .unwrap();
    assert_eq!(tick_count, 6);

    // Verify portfolio snapshots recorded (3 ticks)
    let snap_count: i64 = trader
        .db()
        .conn()
        .query_row("SELECT COUNT(*) FROM portfolio_snapshots", [], |r| r.get(0))
        .unwrap();
    assert_eq!(snap_count, 3);

    // Health JSON should be valid
    let health = trader.health_json();
    let parsed: serde_json::Value = serde_json::from_str(&health).unwrap();
    assert_eq!(parsed["status"], "running");
}
