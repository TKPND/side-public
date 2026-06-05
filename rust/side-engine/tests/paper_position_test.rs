use side_engine::paper::db::PaperDb;
use side_engine::paper::position::PositionManager;
use side_engine::paper::risk::{PaperCostModel, PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET};
use side_engine::paper::RuntimeAccountingMode;

#[test]
fn test_no_position_signal_1_opens() {
    let db = PaperDb::open_in_memory().unwrap();
    let mut mgr = PositionManager::new(10000.0);
    let action = mgr
        .process_signal(&db, "slot1", 1, 150.0, Some(25.0), 2000.0, 1.0)
        .unwrap();
    assert_eq!(action, "entry");
    assert!(db.get_position("slot1").unwrap().is_some());
}

#[test]
fn test_has_position_signal_0_closes() {
    let db = PaperDb::open_in_memory().unwrap();
    let mut mgr = PositionManager::new(10000.0);
    mgr.process_signal(&db, "slot1", 1, 150.0, Some(25.0), 2000.0, 1.0)
        .unwrap();
    let action = mgr
        .process_signal(&db, "slot1", 0, 153.0, Some(26.0), 2000.0, 1.0)
        .unwrap();
    assert_eq!(action, "exit");
    assert!(db.get_position("slot1").unwrap().is_none());
}

#[test]
fn test_has_position_signal_1_holds() {
    let db = PaperDb::open_in_memory().unwrap();
    let mut mgr = PositionManager::new(10000.0);
    mgr.process_signal(&db, "slot1", 1, 150.0, None, 2000.0, 1.0)
        .unwrap();
    let action = mgr
        .process_signal(&db, "slot1", 1, 151.0, None, 2000.0, 1.0)
        .unwrap();
    assert_eq!(action, "hold");
}

#[test]
fn test_no_position_signal_0_does_nothing() {
    let db = PaperDb::open_in_memory().unwrap();
    let mut mgr = PositionManager::new(10000.0);
    let action = mgr
        .process_signal(&db, "slot1", 0, 150.0, None, 2000.0, 1.0)
        .unwrap();
    assert_eq!(action, "none");
}

#[test]
fn test_equity_tracking() {
    let db = PaperDb::open_in_memory().unwrap();
    let mut mgr = PositionManager::new(10000.0);
    mgr.process_signal(&db, "slot1", 1, 150.0, None, 2000.0, 1.0)
        .unwrap();
    mgr.process_signal(&db, "slot1", 0, 153.0, None, 2000.0, 1.0)
        .unwrap();
    let eq = mgr.slot_equity("slot1");
    // (153-150)/150 * 2000 * 1.0 = 40.0 pnl; equity = 2000 + 40 = 2040
    assert!((eq - 2040.0).abs() < 0.01);
}

#[test]
fn process_signal_with_cost_model_records_runtime_ledger_but_keeps_gross_equity() {
    let db = PaperDb::open_in_memory().unwrap();
    let mut mgr = PositionManager::new(10000.0);
    let cost_model = PaperCostModel::new(1.5, 0.5, "cli").unwrap();

    mgr.process_signal_with_cost_model(
        &db,
        "slot1",
        1,
        150.0,
        None,
        2000.0,
        500.0,
        Some(&cost_model),
    )
    .unwrap();
    let action = mgr
        .process_signal_with_cost_model(
            &db,
            "slot1",
            0,
            150.3,
            None,
            2000.0,
            500.0,
            Some(&cost_model),
        )
        .unwrap();

    assert_eq!(action, "exit");
    assert!((mgr.slot_equity("slot1") - 4000.0).abs() < 0.01);
    assert!((db.get_todays_pnl().unwrap() - 2000.0).abs() < 0.01);
    let ledger_count: i64 = db
        .conn()
        .query_row("SELECT COUNT(*) FROM runtime_pnl_ledger", [], |r| r.get(0))
        .unwrap();
    assert_eq!(ledger_count, 1);
    let estimated_net_pnl: f64 = db
        .conn()
        .query_row(
            "SELECT estimated_net_pnl FROM runtime_pnl_ledger ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((estimated_net_pnl - 1800.0).abs() < 0.01);
}

#[test]
fn estimated_net_normal_exit_updates_runtime_equity_on_net_basis() {
    let db = PaperDb::open_in_memory().unwrap();
    let mut mgr = PositionManager::new(2000.0);
    let cost_model = PaperCostModel::new(1.5, 0.5, "cli").unwrap();

    mgr.process_signal_with_accounting_mode(
        &db,
        "slot1",
        1,
        150.0,
        None,
        2000.0,
        500.0,
        RuntimeAccountingMode::EstimatedNet,
        Some(&cost_model),
    )
    .unwrap();
    let outcome = mgr
        .process_signal_with_accounting_mode(
            &db,
            "slot1",
            0,
            150.3,
            None,
            2000.0,
            500.0,
            RuntimeAccountingMode::EstimatedNet,
            Some(&cost_model),
        )
        .unwrap();

    assert_eq!(outcome.action, "exit");
    let close = outcome.runtime_close.unwrap();
    assert!((close.breakdown.gross_pnl - 2000.0).abs() < 0.01);
    assert!((close.breakdown.estimated_net_pnl - 1800.0).abs() < 0.01);
    assert!((mgr.slot_equity("slot1") - 3800.0).abs() < 0.01);
    let (total, _) = mgr.portfolio_summary();
    assert!((total - 3800.0).abs() < 0.01);
    assert!((db.get_todays_pnl().unwrap() - 2000.0).abs() < 0.01);

    let stored_trade_pnl: f64 = db
        .conn()
        .query_row(
            "SELECT pnl FROM trades WHERE side = 'exit' ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((stored_trade_pnl - 2000.0).abs() < 0.01);

    let latest_equity: f64 = db
        .conn()
        .query_row(
            "SELECT equity FROM equity_snapshots ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((latest_equity - 3800.0).abs() < 0.01);
    let latest_equity_source: String = db
        .conn()
        .query_row(
            "SELECT pnl_source FROM runtime_equity_sources ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(latest_equity_source, PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET);

    mgr.record_portfolio_snapshot(&db).unwrap();
    let latest_portfolio: f64 = db
        .conn()
        .query_row(
            "SELECT total_equity FROM portfolio_snapshots ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((latest_portfolio - 3800.0).abs() < 0.01);
    let latest_portfolio_source: String = db
        .conn()
        .query_row(
            "SELECT pnl_source FROM runtime_portfolio_sources ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(
        latest_portfolio_source,
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
}

#[test]
fn estimated_net_liquidation_updates_runtime_equity_on_net_basis() {
    let db = PaperDb::open_in_memory().unwrap();
    let mut mgr = PositionManager::new(2000.0);
    let cost_model = PaperCostModel::new(1.5, 0.5, "cli").unwrap();
    mgr.process_signal_with_accounting_mode(
        &db,
        "slot1",
        1,
        150.0,
        None,
        2000.0,
        500.0,
        RuntimeAccountingMode::EstimatedNet,
        Some(&cost_model),
    )
    .unwrap();

    let mut prices = std::collections::HashMap::new();
    prices.insert("slot1".to_string(), 149.76);
    let outcomes = mgr
        .check_margin_and_liquidate_with_accounting_mode(
            &db,
            0.2,
            &prices,
            RuntimeAccountingMode::EstimatedNet,
            Some(&cost_model),
        )
        .unwrap();

    assert_eq!(outcomes.len(), 1);
    assert_eq!(outcomes[0].slot_id, "slot1");
    let close = outcomes[0].runtime_close.as_ref().unwrap();
    assert!((close.breakdown.gross_pnl + 1600.0).abs() < 0.01);
    assert!((close.breakdown.estimated_net_pnl + 1800.0).abs() < 0.01);
    assert!((mgr.slot_equity("slot1") - 200.0).abs() < 0.01);
    let (total, _) = mgr.portfolio_summary();
    assert!((total - 200.0).abs() < 0.01);
    assert!((db.get_todays_pnl().unwrap() + 1600.0).abs() < 0.01);
}

#[test]
fn legacy_gross_liquidation_keeps_gross_runtime_equity() {
    let db = PaperDb::open_in_memory().unwrap();
    let mut mgr = PositionManager::new(2000.0);
    let cost_model = PaperCostModel::new(1.5, 0.5, "cli").unwrap();
    mgr.process_signal_with_cost_model(
        &db,
        "slot1",
        1,
        150.0,
        None,
        2000.0,
        500.0,
        Some(&cost_model),
    )
    .unwrap();

    let mut prices = std::collections::HashMap::new();
    prices.insert("slot1".to_string(), 149.76);
    let liquidated = mgr
        .check_margin_and_liquidate_with_cost_model(&db, 0.2, &prices, Some(&cost_model))
        .unwrap();

    assert_eq!(liquidated, vec!["slot1".to_string()]);
    assert!((mgr.slot_equity("slot1") - 400.0).abs() < 0.01);
    let (total, _) = mgr.portfolio_summary();
    assert!((total - 400.0).abs() < 0.01);
    assert!((db.get_todays_pnl().unwrap() + 1600.0).abs() < 0.01);
}

#[test]
fn test_portfolio_snapshot() {
    let db = PaperDb::open_in_memory().unwrap();
    let mut mgr = PositionManager::new(10000.0);
    mgr.process_signal(&db, "s1", 1, 100.0, None, 5000.0, 1.0)
        .unwrap();
    mgr.process_signal(&db, "s1", 0, 102.0, None, 5000.0, 1.0)
        .unwrap();
    let (total_eq, ret_pct) = mgr.portfolio_summary();
    // (102-100)/100 * 5000 * 1.0 = 100.0 pnl; total = 10000 + 100 = 10100
    assert!((total_eq - 10100.0).abs() < 0.01);
    assert!((ret_pct - 1.0).abs() < 0.01);
}
