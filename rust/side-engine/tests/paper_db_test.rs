use rusqlite::Connection;
use side_engine::paper::db::PaperDb;
use side_engine::paper::risk::{
    PaperCostModel, PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO,
    PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO, PAPER_PNL_SEMANTICS_ESTIMATED_NET,
    PAPER_PNL_SEMANTICS_LEGACY_GROSS, PAPER_RUNTIME_NET_PNL_INTEGRATION_STATUS,
    PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET, PAPER_RUNTIME_PNL_SOURCE_LEGACY_GROSS,
};
use tempfile::TempDir;

#[test]
fn test_create_tables() {
    let db = PaperDb::open_in_memory().unwrap();
    let tables: Vec<String> = db
        .conn()
        .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        .unwrap()
        .query_map([], |r| r.get(0))
        .unwrap()
        .filter_map(|r| r.ok())
        .collect();
    assert!(tables.contains(&"trades".to_string()));
    assert!(tables.contains(&"positions".to_string()));
    assert!(tables.contains(&"ticks".to_string()));
    assert!(tables.contains(&"equity_snapshots".to_string()));
    assert!(tables.contains(&"portfolio_snapshots".to_string()));
    assert!(tables.contains(&"trade_pnl_breakdowns".to_string()));
    assert!(tables.contains(&"runtime_pnl_ledger".to_string()));
}

#[test]
fn pnl_semantics_labels_are_canonical() {
    assert_eq!(
        PAPER_PNL_SEMANTICS_LEGACY_GROSS,
        "legacy_gross_cost_unadjusted"
    );
    assert_eq!(
        PAPER_PNL_SEMANTICS_ESTIMATED_NET,
        "estimated_net_cost_adjusted"
    );
}

#[test]
fn runtime_pnl_source_labels_are_canonical() {
    assert_eq!(
        PAPER_RUNTIME_PNL_SOURCE_LEGACY_GROSS,
        "legacy_gross_cost_unadjusted"
    );
    assert_eq!(
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET,
        "estimated_net_cost_adjusted"
    );
    assert_eq!(
        PAPER_RUNTIME_NET_PNL_INTEGRATION_STATUS,
        "additive_ledger_ready"
    );
}

#[test]
fn paper_db_enforces_foreign_keys_for_runtime_sources() {
    let db = PaperDb::open_in_memory().unwrap();
    let result = db.conn().execute(
        "INSERT INTO runtime_pnl_ledger (
            trade_id, breakdown_id, slot_id, gross_pnl, estimated_cost, estimated_net_pnl,
            runtime_pnl_source, gross_pnl_semantics, net_pnl_semantics,
            paper_fee_model_status, cost_model_schema_version, cost_model_fingerprint,
            estimated_net_pnl_claim_allowed, runtime_net_pnl_claim_allowed,
            claim_block_reason, timestamp
        ) VALUES (999, 999, 'slot1', 1.0, 0.1, 0.9,
            'estimated_net_cost_adjusted', 'legacy_gross_cost_unadjusted',
            'estimated_net_cost_adjusted', 'explicit_nonzero_cost_model', 1, 'sha256:missing',
            1, 1, NULL, '2026-05-13T00:00:00Z')",
        [],
    );
    assert!(result.is_err());
}

#[test]
fn equity_and_portfolio_snapshots_record_source_rows() {
    let db = PaperDb::open_in_memory().unwrap();
    let equity_snapshot_id = db.record_equity_snapshot("slot1", 10_000.0).unwrap();
    let portfolio_snapshot_id = db.record_portfolio_snapshot(10_000.0, 0.0).unwrap();

    db.record_runtime_equity_source_legacy(equity_snapshot_id, "slot1")
        .unwrap();
    db.record_runtime_portfolio_source_legacy(portfolio_snapshot_id)
        .unwrap();

    let equity_source_count: i64 = db
        .conn()
        .query_row("SELECT COUNT(*) FROM runtime_equity_sources", [], |r| {
            r.get(0)
        })
        .unwrap();
    let portfolio_source_count: i64 = db
        .conn()
        .query_row("SELECT COUNT(*) FROM runtime_portfolio_sources", [], |r| {
            r.get(0)
        })
        .unwrap();

    assert_eq!(equity_source_count, 1);
    assert_eq!(portfolio_source_count, 1);
}

#[test]
fn test_open_and_get_position() {
    let db = PaperDb::open_in_memory().unwrap();
    assert!(db.get_position("slot1").unwrap().is_none());
    db.open_position("slot1", 150.0, 2000.0, 1.0).unwrap();
    let pos = db.get_position("slot1").unwrap().unwrap();
    assert_eq!(pos.entry_price, 150.0);
    assert_eq!(pos.size, 2000.0);
    assert_eq!(pos.side, "long");
    assert_eq!(pos.leverage, 1.0);
}

#[test]
fn test_close_position() {
    let db = PaperDb::open_in_memory().unwrap();
    db.open_position("slot1", 150.0, 2000.0, 1.0).unwrap();
    let pnl = db.close_position("slot1", 153.0, 0).unwrap();
    // (153-150)/150 * 2000 * 1.0 = 40.0
    assert!((pnl - 40.0).abs() < 0.01);
    assert!(db.get_position("slot1").unwrap().is_none());
}

#[test]
fn close_position_keeps_legacy_gross_without_runtime_breakdown() {
    let db = PaperDb::open_in_memory().unwrap();
    db.open_position("slot1", 150.0, 2000.0, 1.0).unwrap();

    let pnl = db.close_position("slot1", 153.0, 0).unwrap();

    assert!((pnl - 40.0).abs() < 0.01);
    let stored_pnl: f64 = db
        .conn()
        .query_row(
            "SELECT pnl FROM trades WHERE side='exit' ORDER BY id DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((stored_pnl - 40.0).abs() < 0.01);
    let breakdown_count: i64 = db
        .conn()
        .query_row("SELECT COUNT(*) FROM trade_pnl_breakdowns", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert_eq!(breakdown_count, 0);
    assert!((db.get_todays_pnl().unwrap() - 40.0).abs() < 0.01);
}

#[test]
fn close_position_with_zero_cost_model_records_blocked_breakdown() {
    let db = PaperDb::open_in_memory().unwrap();
    db.open_position("slot1", 150.0, 2000.0, 1.0).unwrap();
    let model = PaperCostModel::missing_or_zero();

    let breakdown = db
        .close_position_with_cost_model("slot1", 153.0, 0, &model)
        .unwrap();

    assert!(breakdown.id > 0);
    assert!(breakdown.trade_id > 0);
    assert_eq!(breakdown.slot_id, "slot1");
    assert!((breakdown.gross_pnl - 40.0).abs() < 0.01);
    assert!((breakdown.estimated_cost - 0.0).abs() < 0.01);
    assert!((breakdown.estimated_net_pnl - 40.0).abs() < 0.01);
    assert_eq!(
        breakdown.gross_pnl_semantics,
        "legacy_gross_cost_unadjusted"
    );
    assert_eq!(breakdown.net_pnl_semantics, "estimated_net_cost_adjusted");
    assert_eq!(
        breakdown.paper_fee_model_status,
        PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO
    );
    assert!(!breakdown.estimated_net_pnl_claim_allowed);
    assert_eq!(
        breakdown.claim_block_reason.as_deref(),
        Some("missing_or_zero_cost_model")
    );

    let stored_pnl: f64 = db
        .conn()
        .query_row(
            "SELECT pnl FROM trades WHERE id = ?1",
            [breakdown.trade_id],
            |r| r.get(0),
        )
        .unwrap();
    assert!((stored_pnl - breakdown.gross_pnl).abs() < 0.01);
    assert!((db.get_todays_pnl().unwrap() - 40.0).abs() < 0.01);

    let stored = db
        .get_trade_pnl_breakdown(breakdown.trade_id)
        .unwrap()
        .unwrap();
    assert_eq!(stored.id, breakdown.id);
    assert_eq!(stored.cost_model_fingerprint, model.cost_model_fingerprint);
    let ledger_count: i64 = db
        .conn()
        .query_row(
            "SELECT COUNT(*) FROM runtime_pnl_ledger WHERE breakdown_id = ?1",
            [breakdown.id],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(ledger_count, 1);
}

#[test]
fn close_position_with_explicit_nonzero_model_records_estimated_net_breakdown() {
    let db = PaperDb::open_in_memory().unwrap();
    db.open_position("slot1", 150.0, 2000.0, 500.0).unwrap();
    let model = PaperCostModel::new(1.5, 0.5, "cli").unwrap();

    let breakdown = db
        .close_position_with_cost_model("slot1", 150.3, -1, &model)
        .unwrap();

    assert!((breakdown.gross_pnl - 2000.0).abs() < 0.01);
    assert!((breakdown.estimated_cost - 200.0).abs() < 0.01);
    assert!((breakdown.estimated_net_pnl - 1800.0).abs() < 0.01);
    assert_eq!(
        breakdown.paper_fee_model_status,
        PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO
    );
    assert_eq!(breakdown.fee_bps, 1.5);
    assert_eq!(breakdown.spread_bps, 0.5);
    assert!(breakdown.estimated_net_pnl_claim_allowed);
    assert_eq!(
        breakdown.claim_block_reason.as_deref(),
        Some("runtime_net_pnl_not_integrated")
    );
    assert_eq!(
        breakdown.cost_model_fingerprint,
        model.cost_model_fingerprint
    );
    let ledger = db
        .get_runtime_pnl_ledger_entry_for_breakdown(breakdown.id)
        .unwrap()
        .unwrap();
    assert_eq!(ledger.trade_id, breakdown.trade_id);
    assert!((ledger.estimated_net_pnl - breakdown.estimated_net_pnl).abs() < 0.01);
    assert!(ledger.runtime_net_pnl_claim_allowed);
    assert!((db.get_todays_pnl().unwrap() - 2000.0).abs() < 0.01);
}

#[test]
fn runtime_ledger_from_zero_cost_breakdown_blocks_net_runtime_claim() {
    let db = PaperDb::open_in_memory().unwrap();
    db.open_position("slot1", 150.0, 2000.0, 1.0).unwrap();
    let model = PaperCostModel::missing_or_zero();
    let breakdown = db
        .close_position_with_cost_model("slot1", 153.0, 0, &model)
        .unwrap();

    let ledger = db
        .record_runtime_pnl_ledger_from_breakdown(breakdown.id)
        .unwrap();

    assert!(ledger.id > 0);
    assert_eq!(ledger.trade_id, breakdown.trade_id);
    assert_eq!(ledger.breakdown_id, breakdown.id);
    assert_eq!(ledger.slot_id, "slot1");
    assert!((ledger.gross_pnl - breakdown.gross_pnl).abs() < 0.01);
    assert!((ledger.estimated_cost - breakdown.estimated_cost).abs() < 0.01);
    assert!((ledger.estimated_net_pnl - breakdown.estimated_net_pnl).abs() < 0.01);
    assert_eq!(
        ledger.runtime_pnl_source,
        PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET
    );
    assert_eq!(ledger.gross_pnl_semantics, PAPER_PNL_SEMANTICS_LEGACY_GROSS);
    assert_eq!(ledger.net_pnl_semantics, PAPER_PNL_SEMANTICS_ESTIMATED_NET);
    assert_eq!(
        ledger.paper_fee_model_status,
        PAPER_FEE_MODEL_STATUS_MISSING_OR_ZERO
    );
    assert!(!ledger.estimated_net_pnl_claim_allowed);
    assert!(!ledger.runtime_net_pnl_claim_allowed);
    assert_eq!(
        ledger.claim_block_reason.as_deref(),
        Some("missing_or_zero_cost_model")
    );

    let stored = db.get_runtime_pnl_ledger_entry(ledger.id).unwrap().unwrap();
    assert_eq!(stored.trade_id, breakdown.trade_id);
    assert_eq!(stored.cost_model_fingerprint, model.cost_model_fingerprint);
    assert!((db.get_todays_pnl().unwrap() - 40.0).abs() < 0.01);
}

#[test]
fn runtime_ledger_from_explicit_nonzero_breakdown_allows_estimated_net_ledger_claim_only() {
    let db = PaperDb::open_in_memory().unwrap();
    db.open_position("slot1", 150.0, 2000.0, 500.0).unwrap();
    let model = PaperCostModel::new(1.5, 0.5, "cli").unwrap();
    let breakdown = db
        .close_position_with_cost_model("slot1", 150.3, -1, &model)
        .unwrap();

    let ledger = db
        .record_runtime_pnl_ledger_from_breakdown(breakdown.id)
        .unwrap();

    assert_eq!(ledger.trade_id, breakdown.trade_id);
    assert!((ledger.gross_pnl - 2000.0).abs() < 0.01);
    assert!((ledger.estimated_cost - 200.0).abs() < 0.01);
    assert!((ledger.estimated_net_pnl - 1800.0).abs() < 0.01);
    assert_eq!(
        ledger.paper_fee_model_status,
        PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO
    );
    assert!(ledger.estimated_net_pnl_claim_allowed);
    assert!(ledger.runtime_net_pnl_claim_allowed);
    assert_eq!(
        ledger.claim_block_reason.as_deref(),
        Some("runtime_equity_not_net_integrated")
    );
    assert_eq!(ledger.cost_model_fingerprint, model.cost_model_fingerprint);

    let stored_trade_pnl: f64 = db
        .conn()
        .query_row(
            "SELECT pnl FROM trades WHERE id = ?1",
            [ledger.trade_id],
            |r| r.get(0),
        )
        .unwrap();
    assert!((stored_trade_pnl - ledger.gross_pnl).abs() < 0.01);
    assert!((db.get_todays_pnl().unwrap() - 2000.0).abs() < 0.01);
}

#[test]
fn estimated_net_normal_exit_records_event_and_preserves_gross_trade_pnl() {
    let db = PaperDb::open_in_memory().unwrap();
    db.open_position("slot1", 150.0, 2000.0, 500.0).unwrap();
    let model = PaperCostModel::new(1.5, 0.5, "cli").unwrap();

    let close = db
        .close_position_estimated_net_normal_exit("slot1", 150.3, -1, &model)
        .unwrap();

    assert!((close.breakdown.gross_pnl - 2000.0).abs() < 0.01);
    assert!((close.breakdown.estimated_cost - 200.0).abs() < 0.01);
    assert!((close.breakdown.estimated_net_pnl - 1800.0).abs() < 0.01);
    assert_eq!(close.ledger.trade_id, close.breakdown.trade_id);
    assert_eq!(close.ledger.breakdown_id, close.breakdown.id);
    assert_eq!(close.ledger.slot_id, "slot1");
    assert!(close.ledger.runtime_net_pnl_claim_allowed);
    assert!(close.accounting_event_id > 0);

    let stored_trade_pnl: f64 = db
        .conn()
        .query_row(
            "SELECT pnl FROM trades WHERE id = ?1",
            [close.breakdown.trade_id],
            |r| r.get(0),
        )
        .unwrap();
    assert!((stored_trade_pnl - 2000.0).abs() < 0.01);
    assert!((db.get_todays_pnl().unwrap() - 2000.0).abs() < 0.01);

    let event: (String, String, String, i64, i64, i64, String) = db
        .conn()
        .query_row(
            "SELECT accounting_mode, event_kind, claim_scope,
                    trade_id, breakdown_id, ledger_id, cost_model_fingerprint
             FROM runtime_accounting_events WHERE id = ?1",
            [close.accounting_event_id],
            |r| {
                Ok((
                    r.get(0)?,
                    r.get(1)?,
                    r.get(2)?,
                    r.get(3)?,
                    r.get(4)?,
                    r.get(5)?,
                    r.get(6)?,
                ))
            },
        )
        .unwrap();
    assert_eq!(event.0, "estimated_net");
    assert_eq!(event.1, "normal_exit");
    assert_eq!(event.2, "runtime_equity_accounting");
    assert_eq!(event.3, close.breakdown.trade_id);
    assert_eq!(event.4, close.breakdown.id);
    assert_eq!(event.5, close.ledger.id);
    assert_eq!(event.6, model.cost_model_fingerprint);
}

#[test]
fn estimated_net_liquidation_records_event_and_preserves_gross_trade_pnl() {
    let db = PaperDb::open_in_memory().unwrap();
    db.open_position("slot1", 150.0, 2000.0, 500.0).unwrap();
    let model = PaperCostModel::new(1.5, 0.5, "cli").unwrap();

    let close = db
        .close_position_estimated_net_liquidation("slot1", 149.76, &model)
        .unwrap();

    assert!((close.breakdown.gross_pnl + 1600.0).abs() < 0.01);
    assert!((close.breakdown.estimated_cost - 200.0).abs() < 0.01);
    assert!((close.breakdown.estimated_net_pnl + 1800.0).abs() < 0.01);
    assert_eq!(close.ledger.trade_id, close.breakdown.trade_id);
    assert_eq!(close.ledger.breakdown_id, close.breakdown.id);
    assert_eq!(close.ledger.slot_id, "slot1");
    assert!(close.ledger.runtime_net_pnl_claim_allowed);

    let stored_trade: (i32, f64) = db
        .conn()
        .query_row(
            "SELECT signal, pnl FROM trades WHERE id = ?1",
            [close.breakdown.trade_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .unwrap();
    assert_eq!(stored_trade.0, -99);
    assert!((stored_trade.1 + 1600.0).abs() < 0.01);
    assert!((db.get_todays_pnl().unwrap() + 1600.0).abs() < 0.01);

    let event: (String, String, String, i64, i64, i64, String) = db
        .conn()
        .query_row(
            "SELECT accounting_mode, event_kind, claim_scope,
                    trade_id, breakdown_id, ledger_id, cost_model_fingerprint
             FROM runtime_accounting_events WHERE id = ?1",
            [close.accounting_event_id],
            |r| {
                Ok((
                    r.get(0)?,
                    r.get(1)?,
                    r.get(2)?,
                    r.get(3)?,
                    r.get(4)?,
                    r.get(5)?,
                    r.get(6)?,
                ))
            },
        )
        .unwrap();
    assert_eq!(event.0, "estimated_net");
    assert_eq!(event.1, "liquidation");
    assert_eq!(event.2, "runtime_equity_accounting");
    assert_eq!(event.3, close.breakdown.trade_id);
    assert_eq!(event.4, close.breakdown.id);
    assert_eq!(event.5, close.ledger.id);
    assert_eq!(event.6, model.cost_model_fingerprint);
}

#[test]
fn estimated_net_source_rows_link_to_accounting_event() {
    let db = PaperDb::open_in_memory().unwrap();
    db.open_position("slot1", 150.0, 2000.0, 500.0).unwrap();
    let model = PaperCostModel::new(1.5, 0.5, "cli").unwrap();
    let close = db
        .close_position_estimated_net_normal_exit("slot1", 150.3, -1, &model)
        .unwrap();
    let equity_snapshot_id = db.record_equity_snapshot("slot1", 3800.0).unwrap();
    let portfolio_snapshot_id = db.record_portfolio_snapshot(3800.0, 90.0).unwrap();

    db.record_runtime_equity_source_estimated_net(equity_snapshot_id, "slot1", &close)
        .unwrap();
    db.record_runtime_portfolio_source_estimated_net(portfolio_snapshot_id, &close)
        .unwrap();

    let equity_source: (String, i64, i64, i64, i64, String, f64, f64, f64) = db
        .conn()
        .query_row(
            "SELECT pnl_source, accounting_event_id, trade_id, breakdown_id, ledger_id,
                    cost_model_fingerprint, gross_pnl, estimated_cost, estimated_net_pnl
             FROM runtime_equity_sources WHERE equity_snapshot_id = ?1",
            [equity_snapshot_id],
            |r| {
                Ok((
                    r.get(0)?,
                    r.get(1)?,
                    r.get(2)?,
                    r.get(3)?,
                    r.get(4)?,
                    r.get(5)?,
                    r.get(6)?,
                    r.get(7)?,
                    r.get(8)?,
                ))
            },
        )
        .unwrap();
    assert_eq!(equity_source.0, PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET);
    assert_eq!(equity_source.1, close.accounting_event_id);
    assert_eq!(equity_source.2, close.breakdown.trade_id);
    assert_eq!(equity_source.3, close.breakdown.id);
    assert_eq!(equity_source.4, close.ledger.id);
    assert_eq!(equity_source.5, model.cost_model_fingerprint);
    assert!((equity_source.6 - 2000.0).abs() < 0.01);
    assert!((equity_source.7 - 200.0).abs() < 0.01);
    assert!((equity_source.8 - 1800.0).abs() < 0.01);

    let portfolio_source: (String, i64, String, f64, f64) = db
        .conn()
        .query_row(
            "SELECT pnl_source, accounting_event_id, cost_model_fingerprint,
                    gross_pnl, estimated_net_pnl
             FROM runtime_portfolio_sources WHERE portfolio_snapshot_id = ?1",
            [portfolio_snapshot_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
        )
        .unwrap();
    assert_eq!(portfolio_source.0, PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET);
    assert_eq!(portfolio_source.1, close.accounting_event_id);
    assert_eq!(portfolio_source.2, model.cost_model_fingerprint);
    assert!((portfolio_source.3 - 2000.0).abs() < 0.01);
    assert!((portfolio_source.4 - 1800.0).abs() < 0.01);
}

#[test]
fn opening_existing_db_adds_breakdown_table_without_changing_trade_pnl() {
    let tmp = TempDir::new().unwrap();
    let db_path = tmp.path().join("paper.db");
    {
        let conn = Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                slot_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                signal INTEGER NOT NULL,
                pnl REAL,
                timestamp TEXT NOT NULL
            );
            INSERT INTO trades (slot_id, side, price, signal, pnl, timestamp)
            VALUES ('slot1', 'exit', 153.0, 0, 40.0, '2026-05-13T00:00:00Z');
            ",
        )
        .unwrap();
    }

    let db = PaperDb::open(db_path.to_str().unwrap()).unwrap();

    let stored_pnl: f64 = db
        .conn()
        .query_row("SELECT pnl FROM trades WHERE id = 1", [], |r| r.get(0))
        .unwrap();
    assert!((stored_pnl - 40.0).abs() < 0.01);
    let count: i64 = db
        .conn()
        .query_row("SELECT COUNT(*) FROM trade_pnl_breakdowns", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert_eq!(count, 0);
    let ledger_count: i64 = db
        .conn()
        .query_row("SELECT COUNT(*) FROM runtime_pnl_ledger", [], |r| r.get(0))
        .unwrap();
    assert_eq!(ledger_count, 0);
}

#[test]
fn test_record_tick() {
    let db = PaperDb::open_in_memory().unwrap();
    db.record_tick("slot1", 1, 150.5, Some(25.3)).unwrap();
    let count: i64 = db
        .conn()
        .query_row("SELECT COUNT(*) FROM ticks", [], |r| r.get(0))
        .unwrap();
    assert_eq!(count, 1);
}

#[test]
fn test_record_equity_snapshot() {
    let db = PaperDb::open_in_memory().unwrap();
    db.record_equity_snapshot("slot1", 2050.0).unwrap();
    let eq: f64 = db
        .conn()
        .query_row(
            "SELECT equity FROM equity_snapshots WHERE slot_id='slot1'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!((eq - 2050.0).abs() < 0.01);
}

#[test]
fn test_record_portfolio_snapshot() {
    let db = PaperDb::open_in_memory().unwrap();
    db.record_portfolio_snapshot(10500.0, 5.0).unwrap();
    let total: f64 = db
        .conn()
        .query_row("SELECT total_equity FROM portfolio_snapshots", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert!((total - 10500.0).abs() < 0.01);
}

#[test]
fn test_get_todays_pnl() {
    let db = PaperDb::open_in_memory().unwrap();
    db.open_position("slot1", 100.0, 1000.0, 1.0).unwrap();
    db.close_position("slot1", 110.0, 0).unwrap();
    let pnl = db.get_todays_pnl().unwrap();
    // (110-100)/100 * 1000 * 1.0 = 100.0
    assert!((pnl - 100.0).abs() < 0.01);
}

#[test]
fn test_close_position_no_position_errors() {
    let db = PaperDb::open_in_memory().unwrap();
    let result = db.close_position("nonexistent", 100.0, 0);
    assert!(result.is_err());
}
