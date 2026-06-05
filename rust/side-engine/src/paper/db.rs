use anyhow::Result;
use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension, Transaction};

use super::risk::{
    PaperCostModel, PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO, PAPER_PNL_SEMANTICS_ESTIMATED_NET,
    PAPER_PNL_SEMANTICS_LEGACY_GROSS, PAPER_RUNTIME_ACCOUNTING_CLAIM_SCOPE_RUNTIME_EQUITY,
    PAPER_RUNTIME_ACCOUNTING_EVENT_KIND_LIQUIDATION,
    PAPER_RUNTIME_ACCOUNTING_EVENT_KIND_NORMAL_EXIT, PAPER_RUNTIME_ACCOUNTING_MODE_ESTIMATED_NET,
    PAPER_RUNTIME_NET_PNL_LEDGER_CLAIM_BLOCK_REASON, PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET,
};

pub struct PaperDb {
    conn: Connection,
}

#[derive(Debug, Clone)]
pub struct Position {
    pub slot_id: String,
    pub side: String,
    pub entry_price: f64,
    pub entry_time: String,
    pub size: f64,
    pub leverage: f64,
}

#[derive(Debug, Clone)]
pub struct PaperRuntimePnlBreakdown {
    pub id: i64,
    pub trade_id: i64,
    pub slot_id: String,
    pub gross_pnl: f64,
    pub estimated_cost: f64,
    pub estimated_net_pnl: f64,
    pub gross_pnl_semantics: String,
    pub net_pnl_semantics: String,
    pub paper_fee_model_status: String,
    pub cost_model_schema_version: u32,
    pub fee_bps: f64,
    pub spread_bps: f64,
    pub cost_basis: String,
    pub cost_model_source: String,
    pub cost_model_fingerprint: String,
    pub cost_notional: f64,
    pub estimated_net_pnl_claim_allowed: bool,
    pub claim_block_reason: Option<String>,
    pub timestamp: String,
}

#[derive(Debug, Clone)]
pub struct PaperRuntimePnlLedgerEntry {
    pub id: i64,
    pub trade_id: i64,
    pub breakdown_id: i64,
    pub slot_id: String,
    pub gross_pnl: f64,
    pub estimated_cost: f64,
    pub estimated_net_pnl: f64,
    pub runtime_pnl_source: String,
    pub gross_pnl_semantics: String,
    pub net_pnl_semantics: String,
    pub paper_fee_model_status: String,
    pub cost_model_schema_version: u32,
    pub cost_model_fingerprint: String,
    pub estimated_net_pnl_claim_allowed: bool,
    pub runtime_net_pnl_claim_allowed: bool,
    pub claim_block_reason: Option<String>,
    pub timestamp: String,
}

#[derive(Debug, Clone)]
pub struct EstimatedNetRuntimeClose {
    pub breakdown: PaperRuntimePnlBreakdown,
    pub ledger: PaperRuntimePnlLedgerEntry,
    pub accounting_event_id: i64,
}

struct ClosePositionRecord {
    trade_id: i64,
    slot_id: String,
    gross_pnl: f64,
    size: f64,
    leverage: f64,
    timestamp: String,
}

impl PaperDb {
    pub fn open(path: &str) -> Result<Self> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL;")?;
        let db = Self { conn };
        db.create_tables()?;
        Ok(db)
    }

    pub fn open_in_memory() -> Result<Self> {
        let conn = Connection::open_in_memory()?;
        conn.execute_batch("PRAGMA foreign_keys=ON;")?;
        let db = Self { conn };
        db.create_tables()?;
        Ok(db)
    }

    pub fn conn(&self) -> &Connection {
        &self.conn
    }

    fn create_tables(&self) -> Result<()> {
        self.conn.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY,
                slot_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                signal INTEGER NOT NULL,
                pnl REAL,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trade_pnl_breakdowns (
                id INTEGER PRIMARY KEY,
                trade_id INTEGER NOT NULL UNIQUE,
                slot_id TEXT NOT NULL,
                gross_pnl REAL NOT NULL,
                estimated_cost REAL NOT NULL,
                estimated_net_pnl REAL NOT NULL,
                gross_pnl_semantics TEXT NOT NULL,
                net_pnl_semantics TEXT NOT NULL,
                paper_fee_model_status TEXT NOT NULL,
                cost_model_schema_version INTEGER NOT NULL,
                fee_bps REAL NOT NULL,
                spread_bps REAL NOT NULL,
                cost_basis TEXT NOT NULL,
                cost_model_source TEXT NOT NULL,
                cost_model_fingerprint TEXT NOT NULL,
                cost_notional REAL NOT NULL,
                estimated_net_pnl_claim_allowed INTEGER NOT NULL,
                claim_block_reason TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY(trade_id) REFERENCES trades(id)
            );
            CREATE TABLE IF NOT EXISTS runtime_pnl_ledger (
                id INTEGER PRIMARY KEY,
                trade_id INTEGER NOT NULL UNIQUE,
                breakdown_id INTEGER NOT NULL UNIQUE,
                slot_id TEXT NOT NULL,
                gross_pnl REAL NOT NULL,
                estimated_cost REAL NOT NULL,
                estimated_net_pnl REAL NOT NULL,
                runtime_pnl_source TEXT NOT NULL,
                gross_pnl_semantics TEXT NOT NULL,
                net_pnl_semantics TEXT NOT NULL,
                paper_fee_model_status TEXT NOT NULL,
                cost_model_schema_version INTEGER NOT NULL,
                cost_model_fingerprint TEXT NOT NULL,
                estimated_net_pnl_claim_allowed INTEGER NOT NULL,
                runtime_net_pnl_claim_allowed INTEGER NOT NULL,
                claim_block_reason TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY(trade_id) REFERENCES trades(id),
                FOREIGN KEY(breakdown_id) REFERENCES trade_pnl_breakdowns(id)
            );
            CREATE TABLE IF NOT EXISTS positions (
                slot_id TEXT PRIMARY KEY,
                side TEXT,
                entry_price REAL,
                entry_time TEXT,
                size REAL,
                leverage REAL DEFAULT 1.0
            );
            CREATE TABLE IF NOT EXISTS ticks (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                slot_id TEXT NOT NULL,
                signal INTEGER NOT NULL,
                close_price REAL NOT NULL,
                aux_value REAL
            );
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                slot_id TEXT NOT NULL,
                equity REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                total_equity REAL NOT NULL,
                total_return_pct REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_accounting_events (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                accounting_mode TEXT NOT NULL,
                event_kind TEXT NOT NULL,
                claim_scope TEXT NOT NULL,
                runtime_equity_net_accounting_claim_allowed INTEGER NOT NULL,
                claim_block_reason TEXT,
                trade_id INTEGER,
                breakdown_id INTEGER,
                ledger_id INTEGER,
                cost_model_fingerprint TEXT,
                FOREIGN KEY(trade_id) REFERENCES trades(id),
                FOREIGN KEY(breakdown_id) REFERENCES trade_pnl_breakdowns(id),
                FOREIGN KEY(ledger_id) REFERENCES runtime_pnl_ledger(id)
            );
            CREATE TABLE IF NOT EXISTS runtime_equity_sources (
                id INTEGER PRIMARY KEY,
                equity_snapshot_id INTEGER NOT NULL UNIQUE,
                timestamp TEXT NOT NULL,
                slot_id TEXT NOT NULL,
                pnl_source TEXT NOT NULL,
                accounting_event_id INTEGER,
                trade_id INTEGER,
                breakdown_id INTEGER,
                ledger_id INTEGER,
                cost_model_fingerprint TEXT,
                gross_pnl REAL,
                estimated_cost REAL,
                estimated_net_pnl REAL,
                FOREIGN KEY(equity_snapshot_id) REFERENCES equity_snapshots(id),
                FOREIGN KEY(accounting_event_id) REFERENCES runtime_accounting_events(id),
                FOREIGN KEY(trade_id) REFERENCES trades(id),
                FOREIGN KEY(breakdown_id) REFERENCES trade_pnl_breakdowns(id),
                FOREIGN KEY(ledger_id) REFERENCES runtime_pnl_ledger(id)
            );
            CREATE TABLE IF NOT EXISTS runtime_portfolio_sources (
                id INTEGER PRIMARY KEY,
                portfolio_snapshot_id INTEGER NOT NULL UNIQUE,
                timestamp TEXT NOT NULL,
                pnl_source TEXT NOT NULL,
                accounting_event_id INTEGER,
                cost_model_fingerprint TEXT,
                gross_pnl REAL,
                estimated_net_pnl REAL,
                FOREIGN KEY(portfolio_snapshot_id) REFERENCES portfolio_snapshots(id),
                FOREIGN KEY(accounting_event_id) REFERENCES runtime_accounting_events(id)
            );
        ",
        )?;
        // Migration for existing DBs: add leverage column if it doesn't exist yet.
        // SQLite returns an error "duplicate column name" if column already exists — we ignore it.
        let _ = self
            .conn
            .execute_batch("ALTER TABLE positions ADD COLUMN leverage REAL DEFAULT 1.0;");
        Ok(())
    }

    pub fn get_position(&self, slot_id: &str) -> Result<Option<Position>> {
        let mut stmt = self.conn.prepare(
            "SELECT slot_id, side, entry_price, entry_time, size, COALESCE(leverage, 1.0) FROM positions WHERE slot_id = ?1 AND side IS NOT NULL",
        )?;
        let mut rows = stmt.query_map(params![slot_id], |row| {
            Ok(Position {
                slot_id: row.get(0)?,
                side: row.get(1)?,
                entry_price: row.get(2)?,
                entry_time: row.get(3)?,
                size: row.get(4)?,
                leverage: row.get(5)?,
            })
        })?;
        match rows.next() {
            Some(r) => Ok(Some(r?)),
            None => Ok(None),
        }
    }

    pub fn get_all_open_positions(&self) -> Result<Vec<Position>> {
        let mut stmt = self.conn.prepare(
            "SELECT slot_id, side, entry_price, entry_time, size, COALESCE(leverage, 1.0) FROM positions WHERE side IS NOT NULL",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(Position {
                slot_id: row.get(0)?,
                side: row.get(1)?,
                entry_price: row.get(2)?,
                entry_time: row.get(3)?,
                size: row.get(4)?,
                leverage: row.get(5)?,
            })
        })?;
        rows.map(|r| r.map_err(anyhow::Error::from)).collect()
    }

    pub fn open_position(&self, slot_id: &str, price: f64, size: f64, leverage: f64) -> Result<()> {
        let now = Utc::now().to_rfc3339();
        let tx = self.conn.unchecked_transaction()?;
        tx.execute(
            "INSERT OR REPLACE INTO positions (slot_id, side, entry_price, entry_time, size, leverage) VALUES (?1, 'long', ?2, ?3, ?4, ?5)",
            params![slot_id, price, &now, size, leverage],
        )?;
        tx.execute(
            "INSERT INTO trades (slot_id, side, price, signal, pnl, timestamp) VALUES (?1, 'entry', ?2, 1, NULL, ?3)",
            params![slot_id, price, &now],
        )?;
        tx.commit()?;
        Ok(())
    }

    pub fn close_position(&self, slot_id: &str, exit_price: f64, signal: i8) -> Result<f64> {
        Ok(self
            .close_position_record(slot_id, exit_price, signal, None)?
            .gross_pnl)
    }

    pub fn close_position_with_cost_model(
        &self,
        slot_id: &str,
        exit_price: f64,
        signal: i8,
        cost_model: &PaperCostModel,
    ) -> Result<PaperRuntimePnlBreakdown> {
        let record = self.close_position_record(slot_id, exit_price, signal, Some(cost_model))?;
        let breakdown = self
            .get_trade_pnl_breakdown(record.trade_id)?
            .ok_or_else(|| {
                anyhow::anyhow!("missing pnl breakdown for trade {}", record.trade_id)
            })?;
        self.record_runtime_pnl_ledger_from_breakdown(breakdown.id)?;
        Ok(breakdown)
    }

    pub fn close_position_estimated_net_normal_exit(
        &self,
        slot_id: &str,
        exit_price: f64,
        signal: i8,
        cost_model: &PaperCostModel,
    ) -> Result<EstimatedNetRuntimeClose> {
        self.close_position_estimated_net_runtime(
            slot_id,
            exit_price,
            signal,
            cost_model,
            PAPER_RUNTIME_ACCOUNTING_EVENT_KIND_NORMAL_EXIT,
        )
    }

    pub fn close_position_estimated_net_liquidation(
        &self,
        slot_id: &str,
        exit_price: f64,
        cost_model: &PaperCostModel,
    ) -> Result<EstimatedNetRuntimeClose> {
        self.close_position_estimated_net_runtime(
            slot_id,
            exit_price,
            super::SIGNAL_LIQUIDATION,
            cost_model,
            PAPER_RUNTIME_ACCOUNTING_EVENT_KIND_LIQUIDATION,
        )
    }

    fn close_position_estimated_net_runtime(
        &self,
        slot_id: &str,
        exit_price: f64,
        signal: i8,
        cost_model: &PaperCostModel,
        event_kind: &str,
    ) -> Result<EstimatedNetRuntimeClose> {
        anyhow::ensure!(
            cost_model.status == PAPER_FEE_MODEL_STATUS_EXPLICIT_NONZERO
                && cost_model.estimated_net_pnl_claim_allowed,
            "estimated_net runtime accounting requires explicit nonzero cost model"
        );

        let pos = self
            .get_position(slot_id)?
            .ok_or_else(|| anyhow::anyhow!("no open position for {}", slot_id))?;
        let gross_pnl = (exit_price - pos.entry_price) / pos.entry_price * pos.size * pos.leverage;
        let now = Utc::now().to_rfc3339();
        let tx = self.conn.unchecked_transaction()?;
        let trade_id = close_position_tx(&tx, slot_id, exit_price, signal, gross_pnl, &now)?;
        let record = ClosePositionRecord {
            trade_id,
            slot_id: slot_id.to_string(),
            gross_pnl,
            size: pos.size,
            leverage: pos.leverage,
            timestamp: now.clone(),
        };
        let breakdown_id = insert_trade_pnl_breakdown_tx(&tx, &record, cost_model)?;
        let ledger_id =
            insert_runtime_pnl_ledger_from_record_tx(&tx, breakdown_id, &record, cost_model, None)?;
        let accounting_event_id = insert_estimated_net_accounting_event_tx(
            &tx,
            trade_id,
            breakdown_id,
            ledger_id,
            event_kind,
            &cost_model.cost_model_fingerprint,
            &now,
        )?;
        tx.commit()?;

        let breakdown = self
            .get_trade_pnl_breakdown_by_id(breakdown_id)?
            .ok_or_else(|| anyhow::anyhow!("missing pnl breakdown {}", breakdown_id))?;
        let ledger = self
            .get_runtime_pnl_ledger_entry(ledger_id)?
            .ok_or_else(|| anyhow::anyhow!("missing runtime pnl ledger entry {}", ledger_id))?;
        validate_estimated_net_runtime_close(&breakdown, &ledger, cost_model)?;

        Ok(EstimatedNetRuntimeClose {
            breakdown,
            ledger,
            accounting_event_id,
        })
    }

    fn close_position_record(
        &self,
        slot_id: &str,
        exit_price: f64,
        signal: i8,
        cost_model: Option<&PaperCostModel>,
    ) -> Result<ClosePositionRecord> {
        let pos = self
            .get_position(slot_id)?
            .ok_or_else(|| anyhow::anyhow!("no open position for {}", slot_id))?;
        let pnl = (exit_price - pos.entry_price) / pos.entry_price * pos.size * pos.leverage;
        let now = Utc::now().to_rfc3339();
        let tx = self.conn.unchecked_transaction()?;
        let trade_id = close_position_tx(&tx, slot_id, exit_price, signal, pnl, &now)?;
        let record = ClosePositionRecord {
            trade_id,
            slot_id: slot_id.to_string(),
            gross_pnl: pnl,
            size: pos.size,
            leverage: pos.leverage,
            timestamp: now,
        };
        if let Some(cost_model) = cost_model {
            insert_trade_pnl_breakdown_tx(&tx, &record, cost_model)?;
        }
        tx.commit()?;
        Ok(record)
    }

    pub fn get_trade_pnl_breakdown(
        &self,
        trade_id: i64,
    ) -> Result<Option<PaperRuntimePnlBreakdown>> {
        self.conn
            .query_row(
                "SELECT id, trade_id, slot_id, gross_pnl, estimated_cost, estimated_net_pnl,
                        gross_pnl_semantics, net_pnl_semantics, paper_fee_model_status,
                        cost_model_schema_version, fee_bps, spread_bps, cost_basis,
                        cost_model_source, cost_model_fingerprint, cost_notional,
                        estimated_net_pnl_claim_allowed, claim_block_reason, timestamp
                 FROM trade_pnl_breakdowns WHERE trade_id = ?1",
                params![trade_id],
                row_to_runtime_pnl_breakdown,
            )
            .optional()
            .map_err(anyhow::Error::from)
    }

    pub fn get_trade_pnl_breakdown_by_id(
        &self,
        breakdown_id: i64,
    ) -> Result<Option<PaperRuntimePnlBreakdown>> {
        self.conn
            .query_row(
                "SELECT id, trade_id, slot_id, gross_pnl, estimated_cost, estimated_net_pnl,
                        gross_pnl_semantics, net_pnl_semantics, paper_fee_model_status,
                        cost_model_schema_version, fee_bps, spread_bps, cost_basis,
                        cost_model_source, cost_model_fingerprint, cost_notional,
                        estimated_net_pnl_claim_allowed, claim_block_reason, timestamp
                 FROM trade_pnl_breakdowns WHERE id = ?1",
                params![breakdown_id],
                row_to_runtime_pnl_breakdown,
            )
            .optional()
            .map_err(anyhow::Error::from)
    }

    pub fn record_runtime_pnl_ledger_from_breakdown(
        &self,
        breakdown_id: i64,
    ) -> Result<PaperRuntimePnlLedgerEntry> {
        if let Some(existing) = self.get_runtime_pnl_ledger_entry_for_breakdown(breakdown_id)? {
            return Ok(existing);
        }
        let breakdown = self
            .get_trade_pnl_breakdown_by_id(breakdown_id)?
            .ok_or_else(|| anyhow::anyhow!("missing pnl breakdown {}", breakdown_id))?;
        let runtime_net_pnl_claim_allowed = breakdown.estimated_net_pnl_claim_allowed;
        let claim_block_reason = if runtime_net_pnl_claim_allowed {
            Some(PAPER_RUNTIME_NET_PNL_LEDGER_CLAIM_BLOCK_REASON.to_string())
        } else {
            breakdown.claim_block_reason.clone()
        };

        self.conn.execute(
            "INSERT INTO runtime_pnl_ledger (
                trade_id, breakdown_id, slot_id, gross_pnl, estimated_cost, estimated_net_pnl,
                runtime_pnl_source, gross_pnl_semantics, net_pnl_semantics,
                paper_fee_model_status, cost_model_schema_version, cost_model_fingerprint,
                estimated_net_pnl_claim_allowed, runtime_net_pnl_claim_allowed,
                claim_block_reason, timestamp
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
            params![
                breakdown.trade_id,
                breakdown.id,
                &breakdown.slot_id,
                breakdown.gross_pnl,
                breakdown.estimated_cost,
                breakdown.estimated_net_pnl,
                PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET,
                &breakdown.gross_pnl_semantics,
                &breakdown.net_pnl_semantics,
                &breakdown.paper_fee_model_status,
                breakdown.cost_model_schema_version,
                &breakdown.cost_model_fingerprint,
                breakdown.estimated_net_pnl_claim_allowed,
                runtime_net_pnl_claim_allowed,
                &claim_block_reason,
                &breakdown.timestamp,
            ],
        )?;
        let id = self.conn.last_insert_rowid();
        self.get_runtime_pnl_ledger_entry(id)?
            .ok_or_else(|| anyhow::anyhow!("missing runtime pnl ledger entry {}", id))
    }

    pub fn get_runtime_pnl_ledger_entry(
        &self,
        id: i64,
    ) -> Result<Option<PaperRuntimePnlLedgerEntry>> {
        self.conn
            .query_row(
                "SELECT id, trade_id, breakdown_id, slot_id, gross_pnl, estimated_cost,
                        estimated_net_pnl, runtime_pnl_source, gross_pnl_semantics,
                        net_pnl_semantics, paper_fee_model_status,
                        cost_model_schema_version, cost_model_fingerprint,
                        estimated_net_pnl_claim_allowed, runtime_net_pnl_claim_allowed,
                        claim_block_reason, timestamp
                 FROM runtime_pnl_ledger WHERE id = ?1",
                params![id],
                row_to_runtime_pnl_ledger_entry,
            )
            .optional()
            .map_err(anyhow::Error::from)
    }

    pub fn get_runtime_pnl_ledger_entry_for_breakdown(
        &self,
        breakdown_id: i64,
    ) -> Result<Option<PaperRuntimePnlLedgerEntry>> {
        self.conn
            .query_row(
                "SELECT id, trade_id, breakdown_id, slot_id, gross_pnl, estimated_cost,
                        estimated_net_pnl, runtime_pnl_source, gross_pnl_semantics,
                        net_pnl_semantics, paper_fee_model_status,
                        cost_model_schema_version, cost_model_fingerprint,
                        estimated_net_pnl_claim_allowed, runtime_net_pnl_claim_allowed,
                        claim_block_reason, timestamp
                 FROM runtime_pnl_ledger WHERE breakdown_id = ?1",
                params![breakdown_id],
                row_to_runtime_pnl_ledger_entry,
            )
            .optional()
            .map_err(anyhow::Error::from)
    }

    pub fn get_latest_runtime_pnl_ledger_entry(
        &self,
    ) -> Result<Option<PaperRuntimePnlLedgerEntry>> {
        self.conn
            .query_row(
                "SELECT id, trade_id, breakdown_id, slot_id, gross_pnl, estimated_cost,
                        estimated_net_pnl, runtime_pnl_source, gross_pnl_semantics,
                        net_pnl_semantics, paper_fee_model_status,
                        cost_model_schema_version, cost_model_fingerprint,
                        estimated_net_pnl_claim_allowed, runtime_net_pnl_claim_allowed,
                        claim_block_reason, timestamp
                 FROM runtime_pnl_ledger ORDER BY id DESC LIMIT 1",
                [],
                row_to_runtime_pnl_ledger_entry,
            )
            .optional()
            .map_err(anyhow::Error::from)
    }

    pub fn record_tick(
        &self,
        slot_id: &str,
        signal: i8,
        close_price: f64,
        aux_value: Option<f64>,
    ) -> Result<()> {
        let now = Utc::now().to_rfc3339();
        self.conn.execute(
            "INSERT INTO ticks (timestamp, slot_id, signal, close_price, aux_value) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![&now, slot_id, signal as i32, close_price, aux_value],
        )?;
        Ok(())
    }

    pub fn record_equity_snapshot(&self, slot_id: &str, equity: f64) -> Result<i64> {
        let now = Utc::now().to_rfc3339();
        self.conn.execute(
            "INSERT INTO equity_snapshots (timestamp, slot_id, equity) VALUES (?1, ?2, ?3)",
            params![&now, slot_id, equity],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    pub fn record_portfolio_snapshot(
        &self,
        total_equity: f64,
        total_return_pct: f64,
    ) -> Result<i64> {
        let now = Utc::now().to_rfc3339();
        self.conn.execute(
            "INSERT INTO portfolio_snapshots (timestamp, total_equity, total_return_pct) VALUES (?1, ?2, ?3)",
            params![&now, total_equity, total_return_pct],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    pub fn record_runtime_equity_source_legacy(
        &self,
        equity_snapshot_id: i64,
        slot_id: &str,
    ) -> Result<i64> {
        let now = Utc::now().to_rfc3339();
        self.conn.execute(
            "INSERT INTO runtime_equity_sources (
                equity_snapshot_id, timestamp, slot_id, pnl_source
            ) VALUES (?1, ?2, ?3, ?4)",
            params![
                equity_snapshot_id,
                &now,
                slot_id,
                PAPER_PNL_SEMANTICS_LEGACY_GROSS
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    pub fn record_runtime_equity_source_estimated_net(
        &self,
        equity_snapshot_id: i64,
        slot_id: &str,
        close: &EstimatedNetRuntimeClose,
    ) -> Result<i64> {
        validate_estimated_net_runtime_close_for_source(close)?;
        let now = Utc::now().to_rfc3339();
        self.conn.execute(
            "INSERT INTO runtime_equity_sources (
                equity_snapshot_id, timestamp, slot_id, pnl_source,
                accounting_event_id, trade_id, breakdown_id, ledger_id,
                cost_model_fingerprint, gross_pnl, estimated_cost, estimated_net_pnl
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                equity_snapshot_id,
                &now,
                slot_id,
                PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET,
                close.accounting_event_id,
                close.breakdown.trade_id,
                close.breakdown.id,
                close.ledger.id,
                &close.breakdown.cost_model_fingerprint,
                close.breakdown.gross_pnl,
                close.breakdown.estimated_cost,
                close.breakdown.estimated_net_pnl,
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    pub fn record_runtime_portfolio_source_legacy(
        &self,
        portfolio_snapshot_id: i64,
    ) -> Result<i64> {
        let now = Utc::now().to_rfc3339();
        self.conn.execute(
            "INSERT INTO runtime_portfolio_sources (
                portfolio_snapshot_id, timestamp, pnl_source
            ) VALUES (?1, ?2, ?3)",
            params![
                portfolio_snapshot_id,
                &now,
                PAPER_PNL_SEMANTICS_LEGACY_GROSS
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    pub fn record_runtime_portfolio_source_estimated_net(
        &self,
        portfolio_snapshot_id: i64,
        close: &EstimatedNetRuntimeClose,
    ) -> Result<i64> {
        validate_estimated_net_runtime_close_for_source(close)?;
        let now = Utc::now().to_rfc3339();
        self.conn.execute(
            "INSERT INTO runtime_portfolio_sources (
                portfolio_snapshot_id, timestamp, pnl_source, accounting_event_id,
                cost_model_fingerprint, gross_pnl, estimated_net_pnl
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                portfolio_snapshot_id,
                &now,
                PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET,
                close.accounting_event_id,
                &close.breakdown.cost_model_fingerprint,
                close.breakdown.gross_pnl,
                close.breakdown.estimated_net_pnl,
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    pub fn get_todays_pnl(&self) -> Result<f64> {
        let today = Utc::now().format("%Y-%m-%d").to_string();
        let pnl: f64 = self.conn.query_row(
            "SELECT COALESCE(SUM(pnl), 0.0) FROM trades WHERE side='exit' AND timestamp LIKE ?1",
            params![format!("{}%", today)],
            |r| r.get(0),
        )?;
        Ok(pnl)
    }
}

fn close_position_tx(
    tx: &Transaction<'_>,
    slot_id: &str,
    exit_price: f64,
    signal: i8,
    pnl: f64,
    timestamp: &str,
) -> Result<i64> {
    tx.execute(
        "UPDATE positions SET side = NULL, entry_price = NULL, entry_time = NULL, size = NULL WHERE slot_id = ?1",
        params![slot_id],
    )?;
    tx.execute(
        "INSERT INTO trades (slot_id, side, price, signal, pnl, timestamp) VALUES (?1, 'exit', ?2, ?3, ?4, ?5)",
        params![slot_id, exit_price, signal as i32, pnl, timestamp],
    )?;
    Ok(tx.last_insert_rowid())
}

fn insert_trade_pnl_breakdown_tx(
    tx: &Transaction<'_>,
    record: &ClosePositionRecord,
    cost_model: &PaperCostModel,
) -> Result<i64> {
    let estimate = cost_model.estimate(record.size, record.leverage, record.gross_pnl)?;
    tx.execute(
        "INSERT INTO trade_pnl_breakdowns (
            trade_id, slot_id, gross_pnl, estimated_cost, estimated_net_pnl,
            gross_pnl_semantics, net_pnl_semantics, paper_fee_model_status,
            cost_model_schema_version, fee_bps, spread_bps, cost_basis,
            cost_model_source, cost_model_fingerprint, cost_notional,
            estimated_net_pnl_claim_allowed, claim_block_reason, timestamp
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18)",
        params![
            record.trade_id,
            &record.slot_id,
            estimate.gross_pnl,
            estimate.estimated_cost,
            estimate.estimated_net_pnl,
            PAPER_PNL_SEMANTICS_LEGACY_GROSS,
            PAPER_PNL_SEMANTICS_ESTIMATED_NET,
            &cost_model.status,
            cost_model.schema_version,
            cost_model.fee_bps,
            cost_model.spread_bps,
            &cost_model.cost_basis,
            &cost_model.cost_model_source,
            &cost_model.cost_model_fingerprint,
            estimate.cost_notional,
            cost_model.estimated_net_pnl_claim_allowed,
            &cost_model.claim_block_reason,
            &record.timestamp,
        ],
    )?;
    Ok(tx.last_insert_rowid())
}

fn insert_runtime_pnl_ledger_from_record_tx(
    tx: &Transaction<'_>,
    breakdown_id: i64,
    record: &ClosePositionRecord,
    cost_model: &PaperCostModel,
    claim_block_reason: Option<&str>,
) -> Result<i64> {
    let estimate = cost_model.estimate(record.size, record.leverage, record.gross_pnl)?;
    tx.execute(
        "INSERT INTO runtime_pnl_ledger (
            trade_id, breakdown_id, slot_id, gross_pnl, estimated_cost, estimated_net_pnl,
            runtime_pnl_source, gross_pnl_semantics, net_pnl_semantics,
            paper_fee_model_status, cost_model_schema_version, cost_model_fingerprint,
            estimated_net_pnl_claim_allowed, runtime_net_pnl_claim_allowed,
            claim_block_reason, timestamp
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
        params![
            record.trade_id,
            breakdown_id,
            &record.slot_id,
            estimate.gross_pnl,
            estimate.estimated_cost,
            estimate.estimated_net_pnl,
            PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET,
            PAPER_PNL_SEMANTICS_LEGACY_GROSS,
            PAPER_PNL_SEMANTICS_ESTIMATED_NET,
            &cost_model.status,
            cost_model.schema_version,
            &cost_model.cost_model_fingerprint,
            cost_model.estimated_net_pnl_claim_allowed,
            cost_model.estimated_net_pnl_claim_allowed,
            claim_block_reason,
            &record.timestamp,
        ],
    )?;
    Ok(tx.last_insert_rowid())
}

fn insert_estimated_net_accounting_event_tx(
    tx: &Transaction<'_>,
    trade_id: i64,
    breakdown_id: i64,
    ledger_id: i64,
    event_kind: &str,
    cost_model_fingerprint: &str,
    timestamp: &str,
) -> Result<i64> {
    tx.execute(
        "INSERT INTO runtime_accounting_events (
            timestamp, accounting_mode, event_kind, claim_scope,
            runtime_equity_net_accounting_claim_allowed, claim_block_reason,
            trade_id, breakdown_id, ledger_id, cost_model_fingerprint
        ) VALUES (?1, ?2, ?3, ?4, 1, ?5, ?6, ?7, ?8, ?9)",
        params![
            timestamp,
            PAPER_RUNTIME_ACCOUNTING_MODE_ESTIMATED_NET,
            event_kind,
            PAPER_RUNTIME_ACCOUNTING_CLAIM_SCOPE_RUNTIME_EQUITY,
            Option::<String>::None,
            trade_id,
            breakdown_id,
            ledger_id,
            cost_model_fingerprint,
        ],
    )?;
    Ok(tx.last_insert_rowid())
}

fn validate_estimated_net_runtime_close(
    breakdown: &PaperRuntimePnlBreakdown,
    ledger: &PaperRuntimePnlLedgerEntry,
    cost_model: &PaperCostModel,
) -> Result<()> {
    anyhow::ensure!(
        ledger.trade_id == breakdown.trade_id,
        "runtime pnl ledger trade_id mismatch"
    );
    anyhow::ensure!(
        ledger.breakdown_id == breakdown.id,
        "runtime pnl ledger breakdown_id mismatch"
    );
    anyhow::ensure!(
        ledger.slot_id == breakdown.slot_id,
        "runtime pnl ledger slot_id mismatch"
    );
    anyhow::ensure!(
        ledger.runtime_pnl_source == PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET,
        "runtime pnl source must be estimated_net_cost_adjusted"
    );
    anyhow::ensure!(
        ledger.gross_pnl_semantics == PAPER_PNL_SEMANTICS_LEGACY_GROSS,
        "runtime pnl ledger gross semantics mismatch"
    );
    anyhow::ensure!(
        ledger.net_pnl_semantics == PAPER_PNL_SEMANTICS_ESTIMATED_NET,
        "runtime pnl ledger net semantics mismatch"
    );
    anyhow::ensure!(
        ledger.cost_model_fingerprint == cost_model.cost_model_fingerprint,
        "runtime pnl ledger cost model fingerprint mismatch"
    );
    anyhow::ensure!(
        ledger.estimated_net_pnl_claim_allowed && ledger.runtime_net_pnl_claim_allowed,
        "runtime pnl ledger claim is not allowed"
    );
    anyhow::ensure!(
        (ledger.gross_pnl - breakdown.gross_pnl).abs() < 0.0000001,
        "runtime pnl ledger gross pnl mismatch"
    );
    anyhow::ensure!(
        (ledger.estimated_cost - breakdown.estimated_cost).abs() < 0.0000001,
        "runtime pnl ledger estimated cost mismatch"
    );
    anyhow::ensure!(
        (ledger.estimated_net_pnl - breakdown.estimated_net_pnl).abs() < 0.0000001,
        "runtime pnl ledger estimated net pnl mismatch"
    );
    Ok(())
}

fn validate_estimated_net_runtime_close_for_source(close: &EstimatedNetRuntimeClose) -> Result<()> {
    anyhow::ensure!(
        close.ledger.trade_id == close.breakdown.trade_id,
        "runtime pnl ledger trade_id mismatch"
    );
    anyhow::ensure!(
        close.ledger.breakdown_id == close.breakdown.id,
        "runtime pnl ledger breakdown_id mismatch"
    );
    anyhow::ensure!(
        close.ledger.runtime_pnl_source == PAPER_RUNTIME_PNL_SOURCE_ESTIMATED_NET,
        "runtime pnl source must be estimated_net_cost_adjusted"
    );
    anyhow::ensure!(
        close.ledger.estimated_net_pnl_claim_allowed && close.ledger.runtime_net_pnl_claim_allowed,
        "runtime pnl ledger claim is not allowed"
    );
    anyhow::ensure!(
        close.ledger.cost_model_fingerprint == close.breakdown.cost_model_fingerprint,
        "runtime pnl ledger cost model fingerprint mismatch"
    );
    Ok(())
}

fn row_to_runtime_pnl_breakdown(
    row: &rusqlite::Row<'_>,
) -> rusqlite::Result<PaperRuntimePnlBreakdown> {
    Ok(PaperRuntimePnlBreakdown {
        id: row.get(0)?,
        trade_id: row.get(1)?,
        slot_id: row.get(2)?,
        gross_pnl: row.get(3)?,
        estimated_cost: row.get(4)?,
        estimated_net_pnl: row.get(5)?,
        gross_pnl_semantics: row.get(6)?,
        net_pnl_semantics: row.get(7)?,
        paper_fee_model_status: row.get(8)?,
        cost_model_schema_version: row.get(9)?,
        fee_bps: row.get(10)?,
        spread_bps: row.get(11)?,
        cost_basis: row.get(12)?,
        cost_model_source: row.get(13)?,
        cost_model_fingerprint: row.get(14)?,
        cost_notional: row.get(15)?,
        estimated_net_pnl_claim_allowed: row.get(16)?,
        claim_block_reason: row.get(17)?,
        timestamp: row.get(18)?,
    })
}

fn row_to_runtime_pnl_ledger_entry(
    row: &rusqlite::Row<'_>,
) -> rusqlite::Result<PaperRuntimePnlLedgerEntry> {
    Ok(PaperRuntimePnlLedgerEntry {
        id: row.get(0)?,
        trade_id: row.get(1)?,
        breakdown_id: row.get(2)?,
        slot_id: row.get(3)?,
        gross_pnl: row.get(4)?,
        estimated_cost: row.get(5)?,
        estimated_net_pnl: row.get(6)?,
        runtime_pnl_source: row.get(7)?,
        gross_pnl_semantics: row.get(8)?,
        net_pnl_semantics: row.get(9)?,
        paper_fee_model_status: row.get(10)?,
        cost_model_schema_version: row.get(11)?,
        cost_model_fingerprint: row.get(12)?,
        estimated_net_pnl_claim_allowed: row.get(13)?,
        runtime_net_pnl_claim_allowed: row.get(14)?,
        claim_block_reason: row.get(15)?,
        timestamp: row.get(16)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn position_struct_has_leverage_field() {
        let pos = Position {
            slot_id: "test".to_string(),
            side: "long".to_string(),
            entry_price: 150.0,
            entry_time: "2026-01-01T00:00:00Z".to_string(),
            size: 2000.0,
            leverage: 500.0,
        };
        assert_eq!(pos.leverage, 500.0);
    }

    #[test]
    fn open_position_stores_leverage_in_db() {
        let db = PaperDb::open_in_memory().unwrap();
        db.open_position("slot1", 150.0, 2000.0, 500.0).unwrap();
        let pos = db.get_position("slot1").unwrap().unwrap();
        assert_eq!(pos.leverage, 500.0);
        assert_eq!(pos.size, 2000.0);
        assert_eq!(pos.entry_price, 150.0);
    }

    #[test]
    fn close_position_calculates_leveraged_pnl() {
        let db = PaperDb::open_in_memory().unwrap();
        // Open with $2000 margin, 500x leverage, entry at 150.0
        db.open_position("slot1", 150.0, 2000.0, 500.0).unwrap();
        // Close at 150.3 — 0.2% gain
        // PnL = (150.3 - 150.0) / 150.0 * 2000.0 * 500.0 = 0.002 * 1_000_000 = 2000.0
        let pnl = db.close_position("slot1", 150.3, -1).unwrap();
        let expected = (150.3 - 150.0) / 150.0 * 2000.0 * 500.0;
        assert!(
            (pnl - expected).abs() < 0.01,
            "pnl={pnl}, expected={expected}"
        );
    }

    #[test]
    fn close_position_no_leverage_defaults_to_1x() {
        let db = PaperDb::open_in_memory().unwrap();
        // Open with $2000, leverage=1 (no multiplier)
        db.open_position("slot1", 150.0, 2000.0, 1.0).unwrap();
        // Close at 150.3 — 0.2% gain
        let pnl = db.close_position("slot1", 150.3, -1).unwrap();
        let expected = (150.3 - 150.0) / 150.0 * 2000.0 * 1.0;
        assert!(
            (pnl - expected).abs() < 0.01,
            "pnl={pnl}, expected={expected}"
        );
    }

    #[test]
    fn get_all_open_positions_returns_all_open() {
        let db = PaperDb::open_in_memory().unwrap();
        db.open_position("slot1", 150.0, 2000.0, 500.0).unwrap();
        db.open_position("slot2", 0.85, 2000.0, 500.0).unwrap();
        let positions = db.get_all_open_positions().unwrap();
        assert_eq!(positions.len(), 2);
    }

    #[test]
    fn get_all_open_positions_excludes_closed() {
        let db = PaperDb::open_in_memory().unwrap();
        db.open_position("slot1", 150.0, 2000.0, 500.0).unwrap();
        db.open_position("slot2", 0.85, 2000.0, 500.0).unwrap();
        db.close_position("slot1", 150.1, -1).unwrap();
        let positions = db.get_all_open_positions().unwrap();
        assert_eq!(positions.len(), 1);
        assert_eq!(positions[0].slot_id, "slot2");
    }
}
