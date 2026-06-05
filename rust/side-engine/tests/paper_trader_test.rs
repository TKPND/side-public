use chrono::NaiveDateTime;
use side_engine::fetcher::types::Bar;
use side_engine::paper::db::PaperDb;
use side_engine::paper::risk::{
    PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO, PAPER_PNL_SEMANTICS_LEGACY_GROSS,
    PAPER_RUNTIME_NET_PNL_CONTRACT_STATUS, PAPER_RUNTIME_NET_PNL_INTEGRATION_STATUS,
    PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET,
};
use side_engine::paper::{PaperConfig, PaperTrader};
use std::collections::HashMap;

fn bars_from_closes(closes: &[f64]) -> Vec<Bar> {
    closes
        .iter()
        .enumerate()
        .map(|(i, close)| Bar {
            datetime: NaiveDateTime::parse_from_str(
                &format!("2026-03-20 {:02}:00:00", i % 24),
                "%Y-%m-%d %H:%M:%S",
            )
            .unwrap(),
            open: *close,
            high: *close + 0.1,
            low: *close - 0.1,
            close: *close,
            volume: 1000.0,
        })
        .collect()
}

fn count_rows(db: &PaperDb, table: &str) -> i64 {
    db.conn()
        .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))
        .unwrap()
}

fn set_cap_apply_health_summary(trader: &mut PaperTrader, claim_allowed: bool) {
    let evidence = serde_json::json!({
        "decision_class": "cap",
        "runtime_sizing_applied": true,
        "cap_application_status": "applied",
        "cap_runtime_sizing_dependency_status": "pnl_source_independent_proven",
        "cap_depends_on_runtime_pnl": false,
        "cap_runtime_sizing_claim_allowed": claim_allowed,
        "cap_runtime_sizing_claim_block_reason": if claim_allowed {
            serde_json::Value::Null
        } else {
            serde_json::Value::String("missing_or_zero_cost_model".to_string())
        },
        "paper_fee_model_status": if claim_allowed {
            "explicit_nonzero_cost_model"
        } else {
            "missing_or_zero_cost_model"
        },
        "cost_model_fingerprint": "sha256:v7_3_cap"
    });
    trader.set_last_risk_summary_from_evidence(
        "apply",
        "reports/v7.3/cap/evidence.json",
        "evaluated",
        None,
        &evidence,
    );
}

fn tick_sma_entry(trader: &mut PaperTrader) {
    let mut entry_closes = vec![100.0; 49];
    entry_closes.push(120.0);
    let mut entry_data = HashMap::new();
    entry_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&entry_closes), None),
    );
    trader.tick_with_data(&entry_data).unwrap();
}

fn current_position_size(trader: &PaperTrader) -> f64 {
    trader
        .db()
        .conn()
        .query_row(
            "SELECT size FROM positions WHERE slot_id = 'USD/JPY/sma_cross#1'",
            [],
            |r| r.get(0),
        )
        .unwrap()
}

#[test]
fn test_tick_with_mock_data() {
    let json = r#"{
        "slots": [
            {"asset": "USD/JPY", "strategy_name": "keltner", "params": {"ema_period": 20, "atr_period": 14, "atr_multiplier": 2.0}, "timeframe": "1h"}
        ],
        "initial_capital": 10000,
        "data_lookback_bars": 100
    }"#;
    let config: PaperConfig = serde_json::from_str(json).unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    let bars: Vec<Bar> = (0..100)
        .map(|i| {
            let price = 150.0 + i as f64 * 0.1;
            Bar {
                datetime: NaiveDateTime::parse_from_str(
                    &format!("2026-03-20 {:02}:00:00", i % 24),
                    "%Y-%m-%d %H:%M:%S",
                )
                .unwrap(),
                open: price - 0.05,
                high: price + 0.2,
                low: price - 0.2,
                close: price,
                volume: 1000.0,
            }
        })
        .collect();

    let mut data = HashMap::new();
    data.insert("USD/JPY".to_string(), (bars, None::<Vec<f64>>));

    let result = trader.tick_with_data(&data);
    assert!(result.is_ok());
    let count: i64 = trader
        .db()
        .conn()
        .query_row("SELECT COUNT(*) FROM ticks", [], |r| r.get(0))
        .unwrap();
    assert!(count >= 1);
}

#[test]
fn test_health_json_output() {
    let json = r#"{
        "slots": [{"asset": "USD/JPY", "strategy_name": "keltner", "params": {"ema_period": 20, "atr_period": 14, "atr_multiplier": 2.0}, "timeframe": "1h"}],
        "initial_capital": 10000
    }"#;
    let config: PaperConfig = serde_json::from_str(json).unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let trader = PaperTrader::new(config, db);
    let health = trader.health_json();
    assert!(health.contains("status"));
    assert!(health.contains("total_equity"));
}

#[test]
fn runtime_size_override_changes_new_position_size() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {"short_window": 2, "long_window": 3},
                "timeframe": "1h"
            }],
            "initial_capital": 10000,
            "data_lookback_bars": 100
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);
    trader
        .apply_runtime_size_override("USD/JPY/sma_cross#1", 0.25)
        .unwrap();

    let bars: Vec<Bar> = (0..50)
        .map(|i| {
            let close = if i == 49 { 120.0 } else { 100.0 };
            Bar {
                datetime: NaiveDateTime::parse_from_str(
                    &format!("2026-03-20 {:02}:00:00", i % 24),
                    "%Y-%m-%d %H:%M:%S",
                )
                .unwrap(),
                open: close,
                high: close + 0.1,
                low: close - 0.1,
                close,
                volume: 1000.0,
            }
        })
        .collect();
    let mut data = HashMap::new();
    data.insert("USD/JPY".to_string(), (bars, None::<Vec<f64>>));

    trader.tick_with_data(&data).unwrap();

    let size: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT size FROM positions WHERE slot_id = ?1 AND side = 'long'",
            ["USD/JPY/sma_cross#1"],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(size, 0.25);
}

#[test]
fn cap_apply_override_and_estimated_net_normal_exit_keep_claims_separate() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {"short_window": 2, "long_window": 3},
                "timeframe": "1h"
            }],
            "initial_capital": 10000,
            "data_lookback_bars": 100,
            "runtime_accounting_mode": "estimated_net",
            "paper_fee_bps": 1.5,
            "paper_spread_bps": 0.5
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    set_cap_apply_health_summary(&mut trader, true);
    trader
        .apply_runtime_size_override("USD/JPY/sma_cross#1", 0.25)
        .unwrap();
    tick_sma_entry(&mut trader);
    assert!((current_position_size(&trader) - 0.25).abs() < 1e-12);

    let mut exit_closes = vec![100.0; 46];
    exit_closes.extend([110.0, 120.0, 120.0, 80.0]);
    let mut exit_data = HashMap::new();
    exit_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&exit_closes), None),
    );
    trader.tick_with_data(&exit_data).unwrap();

    let (gross_pnl, estimated_cost, estimated_net_pnl): (f64, f64, f64) = trader
        .db()
        .conn()
        .query_row(
            "SELECT gross_pnl, estimated_cost, estimated_net_pnl \
             FROM trade_pnl_breakdowns ORDER BY id DESC LIMIT 1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .unwrap();
    let stored_trade_pnl: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl FROM trades WHERE side = 'exit' ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();

    assert!((gross_pnl - -0.08333333333333333).abs() < 1e-12);
    assert!((estimated_cost - 0.00005).abs() < 1e-12);
    assert!((estimated_net_pnl - -0.08338333333333333).abs() < 1e-12);
    assert!((stored_trade_pnl - gross_pnl).abs() < 1e-12);
    assert!((trader.db().get_todays_pnl().unwrap() - gross_pnl).abs() < 1e-12);

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(health["runtime_sizing_applied"], true);
    assert_eq!(health["cap_application_status"], "applied");
    assert_eq!(health["cap_runtime_sizing_claim_allowed"], true);
    assert_eq!(health["cap_depends_on_runtime_pnl"], false);
    assert_eq!(health["runtime_equity_uses_net_pnl"], true);
    assert_eq!(health["runtime_equity_net_accounting_claim_allowed"], true);
    assert_eq!(
        health["runtime_equity_pnl_source"],
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
}

#[test]
fn cap_apply_override_and_estimated_net_liquidation_keep_claims_separate() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {"short_window": 2, "long_window": 3},
                "timeframe": "1h"
            }],
            "initial_capital": 10000,
            "data_lookback_bars": 100,
            "runtime_accounting_mode": "estimated_net",
            "paper_fee_bps": 1.5,
            "paper_spread_bps": 0.5,
            "leverage": 500,
            "maintenance_margin_pct": 0.2
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    set_cap_apply_health_summary(&mut trader, true);
    trader
        .apply_runtime_size_override("USD/JPY/sma_cross#1", 0.25)
        .unwrap();
    tick_sma_entry(&mut trader);
    assert!((current_position_size(&trader) - 0.25).abs() < 1e-12);

    let mut liquidation_closes = vec![120.0; 49];
    liquidation_closes.push(80.0);
    let mut liquidation_data = HashMap::new();
    liquidation_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&liquidation_closes), None),
    );
    trader.tick_with_data(&liquidation_data).unwrap();

    let event_kind: String = trader
        .db()
        .conn()
        .query_row(
            "SELECT event_kind FROM runtime_accounting_events ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let (gross_pnl, estimated_cost, estimated_net_pnl): (f64, f64, f64) = trader
        .db()
        .conn()
        .query_row(
            "SELECT gross_pnl, estimated_cost, estimated_net_pnl \
             FROM trade_pnl_breakdowns ORDER BY id DESC LIMIT 1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .unwrap();

    assert_eq!(event_kind, "liquidation");
    assert!((gross_pnl - -41.666666666666664).abs() < 1e-12);
    assert!((estimated_cost - 0.025).abs() < 1e-12);
    assert!((estimated_net_pnl - -41.69166666666666).abs() < 1e-12);
    assert!((trader.db().get_todays_pnl().unwrap() - gross_pnl).abs() < 1e-12);

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(health["runtime_sizing_applied"], true);
    assert_eq!(health["cap_application_status"], "applied");
    assert_eq!(health["cap_runtime_sizing_claim_allowed"], true);
    assert_eq!(health["cap_depends_on_runtime_pnl"], false);
    assert_eq!(health["runtime_equity_uses_net_pnl"], true);
    assert_eq!(health["runtime_equity_net_accounting_claim_allowed"], true);
    assert_eq!(
        health["runtime_equity_pnl_source"],
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
}

#[test]
fn cap_apply_override_with_missing_cost_blocks_net_close_but_keeps_cap_guard_visible() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {"short_window": 2, "long_window": 3},
                "timeframe": "1h"
            }],
            "initial_capital": 10000,
            "data_lookback_bars": 100,
            "runtime_accounting_mode": "estimated_net"
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    set_cap_apply_health_summary(&mut trader, false);
    trader
        .apply_runtime_size_override("USD/JPY/sma_cross#1", 0.25)
        .unwrap();
    tick_sma_entry(&mut trader);
    assert!((current_position_size(&trader) - 0.25).abs() < 1e-12);

    let mut exit_closes = vec![100.0; 46];
    exit_closes.extend([110.0, 120.0, 120.0, 80.0]);
    let mut exit_data = HashMap::new();
    exit_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&exit_closes), None),
    );
    let result = trader.tick_with_data(&exit_data);

    assert!(result.is_err());
    assert!(result
        .unwrap_err()
        .to_string()
        .contains("estimated_net runtime accounting requires explicit nonzero cost model"));
    assert!(trader
        .db()
        .get_position("USD/JPY/sma_cross#1")
        .unwrap()
        .is_some());
    assert_eq!(count_rows(trader.db(), "trades"), 1);
    assert_eq!(count_rows(trader.db(), "trade_pnl_breakdowns"), 0);
    assert_eq!(count_rows(trader.db(), "runtime_pnl_ledger"), 0);
    assert_eq!(count_rows(trader.db(), "runtime_accounting_events"), 0);

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(health["runtime_sizing_applied"], true);
    assert_eq!(health["cap_application_status"], "applied");
    assert_eq!(health["cap_runtime_sizing_claim_allowed"], false);
    assert_eq!(
        health["cap_runtime_sizing_claim_block_reason"],
        "missing_or_zero_cost_model"
    );
    assert_eq!(health["runtime_equity_net_accounting_claim_allowed"], false);
}

#[test]
fn estimated_net_normal_exit_uses_net_equity_and_gross_trade_pnl() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {"short_window": 2, "long_window": 3},
                "timeframe": "1h"
            }],
            "initial_capital": 10000,
            "data_lookback_bars": 100,
            "runtime_accounting_mode": "estimated_net",
            "paper_fee_bps": 1.5,
            "paper_spread_bps": 0.5
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    let mut entry_closes = vec![100.0; 49];
    entry_closes.push(120.0);
    let mut entry_data = HashMap::new();
    entry_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&entry_closes), None),
    );
    trader.tick_with_data(&entry_data).unwrap();

    let mut exit_closes = vec![100.0; 46];
    exit_closes.extend([110.0, 120.0, 120.0, 80.0]);
    let mut exit_data = HashMap::new();
    exit_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&exit_closes), None),
    );

    trader.tick_with_data(&exit_data).unwrap();

    assert!(trader
        .db()
        .get_position("USD/JPY/sma_cross#1")
        .unwrap()
        .is_none());
    assert_eq!(count_rows(trader.db(), "trades"), 2);
    assert_eq!(count_rows(trader.db(), "trade_pnl_breakdowns"), 1);
    assert_eq!(count_rows(trader.db(), "runtime_pnl_ledger"), 1);
    assert_eq!(count_rows(trader.db(), "runtime_accounting_events"), 1);

    let gross_pnl: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT gross_pnl FROM trade_pnl_breakdowns ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let estimated_net_pnl: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT estimated_net_pnl FROM trade_pnl_breakdowns ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let stored_trade_pnl: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl FROM trades WHERE side = 'exit' ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((stored_trade_pnl - gross_pnl).abs() < 0.01);
    assert!((trader.db().get_todays_pnl().unwrap() - gross_pnl).abs() < 0.01);

    let latest_equity: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT equity FROM equity_snapshots ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((latest_equity - (10_000.0 + estimated_net_pnl)).abs() < 0.01);
    let latest_portfolio: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT total_equity FROM portfolio_snapshots ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((latest_portfolio - (10_000.0 + estimated_net_pnl)).abs() < 0.01);

    let latest_equity_source: String = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl_source FROM runtime_equity_sources ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let latest_portfolio_source: String = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl_source FROM runtime_portfolio_sources ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(latest_equity_source, PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET);
    assert_eq!(
        latest_portfolio_source,
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(health["runtime_accounting_mode"], "estimated_net");
    assert_eq!(health["runtime_equity_uses_net_pnl"], true);
    assert_eq!(
        health["runtime_equity_pnl_source"],
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
    assert_eq!(
        health["runtime_realized_pnl_source"],
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
    assert_eq!(health["runtime_pnl_ledger_claim_allowed"], true);
    assert_eq!(health["runtime_equity_net_accounting_claim_allowed"], true);
    assert!(health["runtime_equity_net_accounting_claim_block_reason"].is_null());
    assert!(health["last_runtime_accounting_event_id"].as_i64().unwrap() > 0);
    assert!(health["last_runtime_pnl_ledger_id"].as_i64().unwrap() > 0);
    assert!(health["last_trade_pnl_breakdown_id"].as_i64().unwrap() > 0);

    let flat_closes = vec![100.0; 50];
    let mut flat_data = HashMap::new();
    flat_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&flat_closes), None),
    );
    trader.tick_with_data(&flat_data).unwrap();

    let followup_equity_source: String = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl_source FROM runtime_equity_sources ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let followup_portfolio_source: String = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl_source FROM runtime_portfolio_sources ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(
        followup_equity_source,
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
    assert_eq!(
        followup_portfolio_source,
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
    let followup_health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(followup_health["runtime_equity_uses_net_pnl"], true);
    assert_eq!(
        followup_health["runtime_equity_pnl_source"],
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
}

#[test]
fn estimated_net_missing_or_zero_exit_signal_fails_closed_without_close_mutations() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {"short_window": 2, "long_window": 3},
                "timeframe": "1h"
            }],
            "initial_capital": 10000,
            "data_lookback_bars": 100,
            "runtime_accounting_mode": "estimated_net"
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    let mut entry_closes = vec![100.0; 49];
    entry_closes.push(120.0);
    let mut entry_data = HashMap::new();
    entry_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&entry_closes), None),
    );
    trader.tick_with_data(&entry_data).unwrap();

    assert!(trader
        .db()
        .get_position("USD/JPY/sma_cross#1")
        .unwrap()
        .is_some());
    assert_eq!(count_rows(trader.db(), "trades"), 1);
    assert_eq!(count_rows(trader.db(), "equity_snapshots"), 1);

    let mut exit_closes = vec![100.0; 46];
    exit_closes.extend([110.0, 120.0, 120.0, 80.0]);
    let mut exit_data = HashMap::new();
    exit_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&exit_closes), None),
    );

    let result = trader.tick_with_data(&exit_data);

    assert!(result.is_err());
    assert!(trader
        .db()
        .get_position("USD/JPY/sma_cross#1")
        .unwrap()
        .is_some());
    assert_eq!(count_rows(trader.db(), "trades"), 1);
    assert_eq!(count_rows(trader.db(), "trade_pnl_breakdowns"), 0);
    assert_eq!(count_rows(trader.db(), "runtime_pnl_ledger"), 0);
    assert_eq!(count_rows(trader.db(), "runtime_accounting_events"), 0);
    assert_eq!(count_rows(trader.db(), "equity_snapshots"), 1);
    assert_eq!(count_rows(trader.db(), "portfolio_snapshots"), 1);
}

#[test]
fn estimated_net_liquidation_uses_net_equity_and_gross_trade_pnl() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {"short_window": 2, "long_window": 3},
                "timeframe": "1h"
            }],
            "initial_capital": 10000,
            "data_lookback_bars": 100,
            "runtime_accounting_mode": "estimated_net",
            "paper_fee_bps": 1.5,
            "paper_spread_bps": 0.5,
            "leverage": 500,
            "maintenance_margin_pct": 0.2
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    let mut entry_closes = vec![100.0; 49];
    entry_closes.push(120.0);
    let mut entry_data = HashMap::new();
    entry_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&entry_closes), None),
    );
    trader.tick_with_data(&entry_data).unwrap();

    let mut liquidation_closes = vec![120.0; 49];
    liquidation_closes.push(80.0);
    let mut liquidation_data = HashMap::new();
    liquidation_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&liquidation_closes), None),
    );

    let result = trader.tick_with_data(&liquidation_data);

    assert!(result.is_ok());
    assert!(trader
        .db()
        .get_position("USD/JPY/sma_cross#1")
        .unwrap()
        .is_none());
    assert_eq!(count_rows(trader.db(), "trades"), 2);
    assert_eq!(count_rows(trader.db(), "trade_pnl_breakdowns"), 1);
    assert_eq!(count_rows(trader.db(), "runtime_pnl_ledger"), 1);
    assert_eq!(count_rows(trader.db(), "runtime_accounting_events"), 1);

    let event_kind: String = trader
        .db()
        .conn()
        .query_row(
            "SELECT event_kind FROM runtime_accounting_events ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(event_kind, "liquidation");

    let gross_pnl: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT gross_pnl FROM trade_pnl_breakdowns ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let estimated_net_pnl: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT estimated_net_pnl FROM trade_pnl_breakdowns ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let stored_trade_pnl: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl FROM trades WHERE side = 'exit' ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((stored_trade_pnl - gross_pnl).abs() < 0.01);
    assert!((trader.db().get_todays_pnl().unwrap() - gross_pnl).abs() < 0.01);

    let latest_equity: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT equity FROM equity_snapshots ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((latest_equity - (10_000.0 + estimated_net_pnl)).abs() < 0.01);
    let latest_portfolio: f64 = trader
        .db()
        .conn()
        .query_row(
            "SELECT total_equity FROM portfolio_snapshots ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((latest_portfolio - (10_000.0 + estimated_net_pnl)).abs() < 0.01);

    let latest_equity_source: String = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl_source FROM runtime_equity_sources ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let latest_portfolio_source: String = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl_source FROM runtime_portfolio_sources ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(latest_equity_source, PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET);
    assert_eq!(
        latest_portfolio_source,
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(health["runtime_accounting_mode"], "estimated_net");
    assert_eq!(health["runtime_equity_uses_net_pnl"], true);
    assert_eq!(
        health["runtime_equity_pnl_source"],
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
    assert_eq!(
        health["runtime_realized_pnl_source"],
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
    assert_eq!(health["runtime_pnl_ledger_claim_allowed"], true);
    assert_eq!(health["runtime_equity_net_accounting_claim_allowed"], true);
    assert!(health["runtime_equity_net_accounting_claim_block_reason"].is_null());
    assert!(health["last_runtime_accounting_event_id"].as_i64().unwrap() > 0);
    assert!(health["last_runtime_pnl_ledger_id"].as_i64().unwrap() > 0);
    assert!(health["last_trade_pnl_breakdown_id"].as_i64().unwrap() > 0);
}

#[test]
fn estimated_net_missing_or_zero_liquidation_fails_closed_without_close_mutations() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {"short_window": 2, "long_window": 3},
                "timeframe": "1h"
            }],
            "initial_capital": 10000,
            "data_lookback_bars": 100,
            "runtime_accounting_mode": "estimated_net",
            "leverage": 500,
            "maintenance_margin_pct": 0.2
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    let mut entry_closes = vec![100.0; 49];
    entry_closes.push(120.0);
    let mut entry_data = HashMap::new();
    entry_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&entry_closes), None),
    );
    trader.tick_with_data(&entry_data).unwrap();

    let mut liquidation_closes = vec![120.0; 49];
    liquidation_closes.push(80.0);
    let mut liquidation_data = HashMap::new();
    liquidation_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&liquidation_closes), None),
    );

    let result = trader.tick_with_data(&liquidation_data);

    assert!(result.is_err());
    assert!(result
        .unwrap_err()
        .to_string()
        .contains("estimated_net runtime accounting requires explicit nonzero cost model"));
    assert!(trader
        .db()
        .get_position("USD/JPY/sma_cross#1")
        .unwrap()
        .is_some());
    assert_eq!(count_rows(trader.db(), "trades"), 1);
    assert_eq!(count_rows(trader.db(), "trade_pnl_breakdowns"), 0);
    assert_eq!(count_rows(trader.db(), "runtime_pnl_ledger"), 0);
    assert_eq!(count_rows(trader.db(), "runtime_accounting_events"), 0);
    assert_eq!(count_rows(trader.db(), "equity_snapshots"), 1);
    assert_eq!(count_rows(trader.db(), "portfolio_snapshots"), 1);
}

#[test]
fn estimated_net_liquidation_trigger_preserves_gross_unrealized_basis() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {"short_window": 2, "long_window": 3},
                "timeframe": "1h"
            }],
            "initial_capital": 10000,
            "data_lookback_bars": 100,
            "runtime_accounting_mode": "estimated_net",
            "paper_fee_bps": 1.5,
            "paper_spread_bps": 0.5,
            "leverage": 500,
            "maintenance_margin_pct": 0.2
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    let mut entry_closes = vec![100.0; 49];
    entry_closes.push(120.0);
    let mut entry_data = HashMap::new();
    entry_data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&entry_closes), None),
    );
    trader.tick_with_data(&entry_data).unwrap();

    let mut non_liquidating_closes = vec![120.0; 49];
    non_liquidating_closes.push(120.1);
    let mut data = HashMap::new();
    data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&non_liquidating_closes), None),
    );

    trader.tick_with_data(&data).unwrap();

    assert!(trader
        .db()
        .get_position("USD/JPY/sma_cross#1")
        .unwrap()
        .is_some());
    assert_eq!(count_rows(trader.db(), "runtime_accounting_events"), 0);
}

#[test]
fn legacy_tick_records_equity_and_portfolio_source_rows() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {"short_window": 2, "long_window": 3},
                "timeframe": "1h"
            }],
            "initial_capital": 10000,
            "data_lookback_bars": 100
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    let mut entry_closes = vec![100.0; 49];
    entry_closes.push(120.0);
    let mut data = HashMap::new();
    data.insert(
        "USD/JPY".to_string(),
        (bars_from_closes(&entry_closes), None),
    );

    trader.tick_with_data(&data).unwrap();

    assert_eq!(count_rows(trader.db(), "runtime_equity_sources"), 1);
    assert_eq!(count_rows(trader.db(), "runtime_portfolio_sources"), 1);
    let equity_source: String = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl_source FROM runtime_equity_sources ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let portfolio_source: String = trader
        .db()
        .conn()
        .query_row(
            "SELECT pnl_source FROM runtime_portfolio_sources ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(equity_source, PAPER_PNL_SEMANTICS_LEGACY_GROSS);
    assert_eq!(portfolio_source, PAPER_PNL_SEMANTICS_LEGACY_GROSS);
}

#[test]
fn health_json_can_include_risk_summary_without_required_risk_mode() {
    let json = r#"{
        "slots": [{"asset": "USD/JPY", "strategy_name": "keltner", "params": {"ema_period": 20, "atr_period": 14, "atr_multiplier": 2.0}, "timeframe": "1h"}],
        "initial_capital": 10000
    }"#;
    let config: PaperConfig = serde_json::from_str(json).unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);
    let baseline: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert!(baseline.get("risk_mode").is_none());

    trader.set_last_risk_summary(
        "observe",
        "reports/v6.1/paper_risk/evidence.json",
        "observed",
        Some("input_context_ambiguous".to_string()),
        None,
        None,
    );
    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(health["risk_mode"], "observe");
    assert_eq!(health["last_risk_status"], "observed");
    assert_eq!(
        health["last_risk_evidence_path"],
        "reports/v6.1/paper_risk/evidence.json"
    );
    assert_eq!(health["skipped_reason"], "input_context_ambiguous");
}

#[test]
fn health_json_includes_paper_cost_summary() {
    let config: PaperConfig = serde_json::from_str(
        r#"{"slots":[{"asset":"USD/JPY","strategy_name":"sma_cross","params":{}}]}"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);
    trader.set_last_risk_summary(
        "observe",
        "reports/v6.5/explicit_nonzero_cost/paper_risk_evidence/evidence/example.json",
        "observed",
        None,
        Some("explicit_nonzero_cost_model".to_string()),
        Some("sha256:abc".to_string()),
    );
    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(
        health["paper_fee_model_status"],
        "explicit_nonzero_cost_model"
    );
    assert_eq!(health["cost_model_fingerprint"], "sha256:abc");
}

#[test]
fn health_json_does_not_emit_cap_claim_fields_for_non_cap_evidence() {
    let config: PaperConfig = serde_json::from_str(
        r#"{"slots":[{"asset":"USD/JPY","strategy_name":"sma_cross","params":{}}]}"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);
    let evidence = serde_json::json!({
        "decision_class": "size",
        "runtime_sizing_applied": true,
        "cap_application_status": "applied",
        "cap_runtime_sizing_dependency_status": "pnl_source_independent_proven",
        "cap_depends_on_runtime_pnl": false,
        "cap_runtime_sizing_claim_allowed": true,
        "cap_runtime_sizing_claim_block_reason": "not_a_cap_decision",
        "paper_fee_model_status": "explicit_nonzero_cost_model",
        "cost_model_fingerprint": "sha256:size"
    });

    trader.set_last_risk_summary_from_evidence(
        "apply",
        "reports/v7.3/size/evidence.json",
        "evaluated",
        None,
        &evidence,
    );

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(health["last_risk_decision_class"], "size");
    assert_eq!(health["runtime_sizing_applied"], true);
    assert_eq!(
        health["paper_fee_model_status"],
        "explicit_nonzero_cost_model"
    );
    assert_eq!(health["cost_model_fingerprint"], "sha256:size");
    assert!(health.get("cap_application_status").is_none());
    assert!(health.get("cap_runtime_sizing_dependency_status").is_none());
    assert!(health.get("cap_depends_on_runtime_pnl").is_none());
    assert!(health.get("cap_runtime_sizing_claim_allowed").is_none());
    assert!(health
        .get("cap_runtime_sizing_claim_block_reason")
        .is_none());
}

#[test]
fn health_json_includes_cap_claim_summary_separate_from_runtime_equity_claim() {
    let config: PaperConfig = serde_json::from_str(
        r#"{
            "slots": [{
                "asset": "USD/JPY",
                "strategy_name": "sma_cross",
                "params": {}
            }],
            "runtime_accounting_mode": "estimated_net"
        }"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);
    let evidence = serde_json::json!({
        "decision_class": "cap",
        "runtime_sizing_applied": true,
        "cap_application_status": "applied",
        "cap_runtime_sizing_dependency_status": "pnl_source_independent_proven",
        "cap_depends_on_runtime_pnl": false,
        "cap_runtime_sizing_claim_allowed": true,
        "cap_runtime_sizing_claim_block_reason": null,
        "paper_fee_model_status": "explicit_nonzero_cost_model",
        "cost_model_fingerprint": "sha256:cap"
    });

    trader.set_last_risk_summary_from_evidence(
        "apply",
        "reports/v7.3/cap/evidence.json",
        "evaluated",
        None,
        &evidence,
    );

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(health["risk_mode"], "apply");
    assert_eq!(health["last_risk_status"], "evaluated");
    assert_eq!(health["last_risk_decision_class"], "cap");
    assert_eq!(health["runtime_sizing_applied"], true);
    assert_eq!(health["cap_application_status"], "applied");
    assert_eq!(
        health["cap_runtime_sizing_dependency_status"],
        "pnl_source_independent_proven"
    );
    assert_eq!(health["cap_depends_on_runtime_pnl"], false);
    assert_eq!(health["cap_runtime_sizing_claim_allowed"], true);
    assert!(health["cap_runtime_sizing_claim_block_reason"].is_null());
    assert_eq!(health["runtime_accounting_mode"], "estimated_net");
    assert_eq!(health["runtime_equity_net_accounting_claim_allowed"], false);
    assert_eq!(health["runtime_equity_uses_net_pnl"], false);
}

#[test]
fn health_json_always_includes_legacy_accounting_contract_fields() {
    let config: PaperConfig = serde_json::from_str(
        r#"{"slots":[{"asset":"USD/JPY","strategy_name":"sma_cross","params":{}}]}"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let trader = PaperTrader::new(config, db);

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(health["runtime_accounting_mode"], "legacy_gross");
    assert_eq!(health["runtime_equity_uses_net_pnl"], false);
    assert_eq!(
        health["runtime_equity_pnl_source"],
        PAPER_PNL_SEMANTICS_LEGACY_GROSS
    );
    assert_eq!(
        health["runtime_realized_pnl_source"],
        PAPER_PNL_SEMANTICS_LEGACY_GROSS
    );
    assert_eq!(health["runtime_equity_net_accounting_claim_allowed"], false);
    assert_eq!(
        health["runtime_equity_net_accounting_claim_block_reason"],
        "runtime_net_accounting_deferred_v7_1"
    );
}

#[test]
fn health_json_includes_runtime_net_pnl_contract_summary() {
    let config: PaperConfig = serde_json::from_str(
        r#"{"slots":[{"asset":"USD/JPY","strategy_name":"sma_cross","params":{}}]}"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    trader.set_last_runtime_pnl_summary(
        7,
        "reports/v6.6/explicit_nonzero_cost_runtime/runtime_pnl_breakdown.json",
        PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO,
        "sha256:abc",
        true,
    );

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(health["last_trade_pnl_breakdown_id"], 7);
    assert_eq!(
        health["last_trade_pnl_breakdown_path"],
        "reports/v6.6/explicit_nonzero_cost_runtime/runtime_pnl_breakdown.json"
    );
    assert_eq!(
        health["runtime_net_pnl_contract_status"],
        PAPER_RUNTIME_NET_PNL_CONTRACT_STATUS
    );
    assert_eq!(health["runtime_pnl_ledger_claim_allowed"], true);
    assert_eq!(health["runtime_equity_net_accounting_claim_allowed"], false);
    assert_eq!(
        health["runtime_equity_net_accounting_claim_block_reason"],
        "runtime_net_accounting_deferred_v7_1"
    );
    assert_eq!(health["runtime_equity_uses_net_pnl"], false);
    assert_eq!(
        health["paper_fee_model_status"],
        PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO
    );
    assert_eq!(health["cost_model_fingerprint"], "sha256:abc");
}

#[test]
fn health_json_includes_runtime_pnl_source_labels_and_latest_ledger() {
    let config: PaperConfig = serde_json::from_str(
        r#"{"slots":[{"asset":"USD/JPY","strategy_name":"sma_cross","params":{}}]}"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    trader.set_last_runtime_pnl_summary(
        7,
        "reports/v6.6/explicit_nonzero_cost_runtime/runtime_pnl_breakdown.json",
        PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO,
        "sha256:abc",
        true,
    );
    trader
        .set_last_runtime_pnl_ledger_summary(11, PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET, true)
        .unwrap();

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert_eq!(
        health["runtime_net_pnl_integration_status"],
        PAPER_RUNTIME_NET_PNL_INTEGRATION_STATUS
    );
    assert_eq!(
        health["runtime_equity_pnl_source"],
        PAPER_PNL_SEMANTICS_LEGACY_GROSS
    );
    assert_eq!(
        health["runtime_realized_pnl_source"],
        PAPER_PNL_SEMANTICS_LEGACY_GROSS
    );
    assert_eq!(
        health["runtime_pnl_source"],
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
    assert_eq!(health["runtime_equity_uses_net_pnl"], false);
    assert_eq!(health["last_runtime_pnl_ledger_id"], 11);
    assert_eq!(health["runtime_pnl_ledger_claim_allowed"], true);
    assert_eq!(health["runtime_equity_net_accounting_claim_allowed"], false);
    assert_eq!(
        health["runtime_equity_net_accounting_claim_block_reason"],
        "runtime_net_accounting_deferred_v7_1"
    );
}

#[test]
fn runtime_pnl_ledger_summary_requires_existing_runtime_summary() {
    let config: PaperConfig = serde_json::from_str(
        r#"{"slots":[{"asset":"USD/JPY","strategy_name":"sma_cross","params":{}}]}"#,
    )
    .unwrap();
    let db = PaperDb::open_in_memory().unwrap();
    let mut trader = PaperTrader::new(config, db);

    assert!(trader
        .set_last_runtime_pnl_ledger_summary(11, PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET, true)
        .is_err());

    let health: serde_json::Value = serde_json::from_str(&trader.health_json()).unwrap();
    assert!(health.get("last_runtime_pnl_ledger_id").is_none());
    assert!(health.get("runtime_pnl_source").is_none());
}
