use std::collections::HashMap;

use anyhow::Result;

use super::db::{EstimatedNetRuntimeClose, PaperDb};
use super::risk::PaperCostModel;
use super::{RuntimeAccountingMode, SIGNAL_LIQUIDATION};

pub struct PositionManager {
    initial_capital: f64,
    slot_equities: HashMap<String, f64>,
    realized_pnl: f64,
    current_runtime_close: Option<EstimatedNetRuntimeClose>,
}

#[derive(Debug, Clone)]
pub struct ProcessSignalOutcome {
    pub action: &'static str,
    pub runtime_close: Option<EstimatedNetRuntimeClose>,
}

#[derive(Debug, Clone)]
pub struct MarginLiquidationOutcome {
    pub slot_id: String,
    pub runtime_close: Option<EstimatedNetRuntimeClose>,
}

impl PositionManager {
    pub fn new(initial_capital: f64) -> Self {
        Self {
            initial_capital,
            slot_equities: HashMap::new(),
            realized_pnl: 0.0,
            current_runtime_close: None,
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub fn process_signal(
        &mut self,
        db: &PaperDb,
        slot_id: &str,
        signal: i8,
        close_price: f64,
        aux_value: Option<f64>,
        allocation: f64,
        leverage: f64,
    ) -> Result<&'static str> {
        self.process_signal_with_cost_model(
            db,
            slot_id,
            signal,
            close_price,
            aux_value,
            allocation,
            leverage,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn process_signal_with_cost_model(
        &mut self,
        db: &PaperDb,
        slot_id: &str,
        signal: i8,
        close_price: f64,
        aux_value: Option<f64>,
        allocation: f64,
        leverage: f64,
        cost_model: Option<&PaperCostModel>,
    ) -> Result<&'static str> {
        Ok(self
            .process_signal_with_accounting_mode(
                db,
                slot_id,
                signal,
                close_price,
                aux_value,
                allocation,
                leverage,
                RuntimeAccountingMode::LegacyGross,
                cost_model,
            )?
            .action)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn process_signal_with_accounting_mode(
        &mut self,
        db: &PaperDb,
        slot_id: &str,
        signal: i8,
        close_price: f64,
        aux_value: Option<f64>,
        allocation: f64,
        leverage: f64,
        runtime_accounting_mode: RuntimeAccountingMode,
        cost_model: Option<&PaperCostModel>,
    ) -> Result<ProcessSignalOutcome> {
        db.record_tick(slot_id, signal, close_price, aux_value)?;
        let has_position = db.get_position(slot_id)?.is_some();
        let mut runtime_close = None;

        let action = match (has_position, signal) {
            (false, 1) => {
                db.open_position(slot_id, close_price, allocation, leverage)?;
                self.slot_equities
                    .entry(slot_id.to_string())
                    .or_insert(allocation);
                "entry"
            }
            (true, 1) => "hold",
            (true, sig) if sig != 1 => {
                let pnl = match runtime_accounting_mode {
                    RuntimeAccountingMode::EstimatedNet => {
                        let cost_model = cost_model.ok_or_else(|| {
                            anyhow::anyhow!(
                                "estimated_net runtime accounting requires explicit nonzero cost model"
                            )
                        })?;
                        let close = db.close_position_estimated_net_normal_exit(
                            slot_id,
                            close_price,
                            sig,
                            cost_model,
                        )?;
                        let pnl = close.breakdown.estimated_net_pnl;
                        runtime_close = Some(close);
                        pnl
                    }
                    RuntimeAccountingMode::LegacyGross => {
                        if let Some(cost_model) = cost_model {
                            db.close_position_with_cost_model(
                                slot_id,
                                close_price,
                                sig,
                                cost_model,
                            )?
                            .gross_pnl
                        } else {
                            db.close_position(slot_id, close_price, sig)?
                        }
                    }
                };
                self.realized_pnl += pnl;
                let eq = self
                    .slot_equities
                    .entry(slot_id.to_string())
                    .or_insert(allocation);
                *eq += pnl;
                "exit"
            }
            _ => "none",
        };

        let eq = self.slot_equity(slot_id);
        let equity_snapshot_id = db.record_equity_snapshot(slot_id, eq)?;
        if let Some(close) = &runtime_close {
            db.record_runtime_equity_source_estimated_net(equity_snapshot_id, slot_id, close)?;
            self.current_runtime_close = Some(close.clone());
        } else if runtime_accounting_mode == RuntimeAccountingMode::EstimatedNet {
            if let Some(close) = &self.current_runtime_close {
                db.record_runtime_equity_source_estimated_net(equity_snapshot_id, slot_id, close)?;
            } else {
                db.record_runtime_equity_source_legacy(equity_snapshot_id, slot_id)?;
            }
        } else {
            db.record_runtime_equity_source_legacy(equity_snapshot_id, slot_id)?;
        }
        Ok(ProcessSignalOutcome {
            action,
            runtime_close,
        })
    }

    pub fn slot_equity(&self, slot_id: &str) -> f64 {
        *self.slot_equities.get(slot_id).unwrap_or(&0.0)
    }

    pub fn portfolio_summary(&self) -> (f64, f64) {
        let total = self.initial_capital + self.realized_pnl;
        let ret_pct = (total - self.initial_capital) / self.initial_capital * 100.0;
        (total, ret_pct)
    }

    /// Check all open positions for margin maintenance violations and force-close those
    /// that breach the threshold.
    ///
    /// `prices` maps slot_id -> current price.
    /// Returns the list of slot_ids that were liquidated.
    pub fn check_margin_and_liquidate(
        &mut self,
        db: &PaperDb,
        maintenance_margin_pct: f64,
        prices: &HashMap<String, f64>,
    ) -> Result<Vec<String>> {
        self.check_margin_and_liquidate_with_cost_model(db, maintenance_margin_pct, prices, None)
    }

    pub fn check_margin_and_liquidate_with_cost_model(
        &mut self,
        db: &PaperDb,
        maintenance_margin_pct: f64,
        prices: &HashMap<String, f64>,
        cost_model: Option<&PaperCostModel>,
    ) -> Result<Vec<String>> {
        let outcomes = self.check_margin_and_liquidate_with_accounting_mode(
            db,
            maintenance_margin_pct,
            prices,
            RuntimeAccountingMode::LegacyGross,
            cost_model,
        )?;
        Ok(outcomes
            .into_iter()
            .map(|outcome| outcome.slot_id)
            .collect())
    }

    pub fn check_margin_and_liquidate_with_accounting_mode(
        &mut self,
        db: &PaperDb,
        maintenance_margin_pct: f64,
        prices: &HashMap<String, f64>,
        runtime_accounting_mode: RuntimeAccountingMode,
        cost_model: Option<&PaperCostModel>,
    ) -> Result<Vec<MarginLiquidationOutcome>> {
        let open_positions = db.get_all_open_positions()?;
        let mut liquidated = Vec::new();

        for pos in open_positions {
            let Some(&current_price) = prices.get(&pos.slot_id) else {
                // No price data for this slot — cannot check margin, skip
                continue;
            };

            let unrealized_pnl =
                (current_price - pos.entry_price) / pos.entry_price * pos.size * pos.leverage;
            let margin_ratio = (pos.size + unrealized_pnl) / pos.size;

            if margin_ratio <= maintenance_margin_pct {
                tracing::warn!(
                    slot_id = %pos.slot_id,
                    margin_ratio = %margin_ratio,
                    maintenance_margin_pct = %maintenance_margin_pct,
                    unrealized_pnl = %unrealized_pnl,
                    "Margin call: force-closing position"
                );
                let mut runtime_close = None;
                let pnl = match runtime_accounting_mode {
                    RuntimeAccountingMode::EstimatedNet => {
                        let cost_model = cost_model.ok_or_else(|| {
                            anyhow::anyhow!(
                                "estimated_net runtime accounting requires explicit nonzero cost model"
                            )
                        })?;
                        let close = db.close_position_estimated_net_liquidation(
                            &pos.slot_id,
                            current_price,
                            cost_model,
                        )?;
                        let pnl = close.breakdown.estimated_net_pnl;
                        runtime_close = Some(close);
                        pnl
                    }
                    RuntimeAccountingMode::LegacyGross => {
                        if let Some(cost_model) = cost_model {
                            db.close_position_with_cost_model(
                                &pos.slot_id,
                                current_price,
                                SIGNAL_LIQUIDATION,
                                cost_model,
                            )?
                            .gross_pnl
                        } else {
                            db.close_position(&pos.slot_id, current_price, SIGNAL_LIQUIDATION)?
                        }
                    }
                };
                self.realized_pnl += pnl;
                let eq = self
                    .slot_equities
                    .entry(pos.slot_id.clone())
                    .or_insert(pos.size);
                *eq += pnl;
                if let Some(close) = &runtime_close {
                    self.current_runtime_close = Some(close.clone());
                }
                liquidated.push(MarginLiquidationOutcome {
                    slot_id: pos.slot_id,
                    runtime_close,
                });
            }
        }

        Ok(liquidated)
    }

    pub fn record_portfolio_snapshot(&mut self, db: &PaperDb) -> Result<()> {
        let (total, pct) = self.portfolio_summary();
        let portfolio_snapshot_id = db.record_portfolio_snapshot(total, pct)?;
        if let Some(close) = &self.current_runtime_close {
            db.record_runtime_portfolio_source_estimated_net(portfolio_snapshot_id, &close)?;
        } else {
            db.record_runtime_portfolio_source_legacy(portfolio_snapshot_id)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::paper::db::PaperDb;

    /// Helper: set up a position manager with one open long position.
    fn setup_with_position(
        entry_price: f64,
        allocation: f64,
        leverage: f64,
    ) -> (PositionManager, PaperDb) {
        let db = PaperDb::open_in_memory().unwrap();
        let mut mgr = PositionManager::new(allocation);
        // Open the position
        mgr.process_signal(&db, "slot1", 1, entry_price, None, allocation, leverage)
            .unwrap();
        (mgr, db)
    }

    #[test]
    fn unrealized_pnl_correct_for_leveraged_position() {
        // entry=150.0, current=149.95, size=$2000, leverage=500
        // unrealized = (149.95 - 150.0) / 150.0 * 2000 * 500 = -333.33
        let (_, db) = setup_with_position(150.0, 2000.0, 500.0);
        let pos = db.get_position("slot1").unwrap().unwrap();
        let current_price = 149.95_f64;
        let unrealized =
            (current_price - pos.entry_price) / pos.entry_price * pos.size * pos.leverage;
        let expected = -333.333_f64;
        assert!(
            (unrealized - expected).abs() < 0.1,
            "unrealized={unrealized:.3}"
        );
    }

    #[test]
    fn margin_ratio_above_threshold_not_liquidated() {
        // entry=150.0, current=149.85 (0.1% drop), $2000 allocation, 500x leverage
        // unrealized = (149.85 - 150.0)/150.0 * 2000 * 500 = -1000
        // margin_ratio = (2000 + (-1000)) / 2000 = 0.50 — above 0.2, NOT liquidated
        let (mut mgr, db) = setup_with_position(150.0, 2000.0, 500.0);
        let mut prices = HashMap::new();
        prices.insert("slot1".to_string(), 149.85_f64);
        let liquidated = mgr.check_margin_and_liquidate(&db, 0.2, &prices).unwrap();
        assert!(
            liquidated.is_empty(),
            "should not be liquidated at 50% margin ratio"
        );
        // Position should still be open
        assert!(db.get_position("slot1").unwrap().is_some());
    }

    #[test]
    fn margin_ratio_at_threshold_triggers_liquidation() {
        // entry=150.0, current=149.76 (0.16% drop), $2000 allocation, 500x leverage
        // unrealized = (149.76 - 150.0)/150.0 * 2000 * 500 = -1600
        // margin_ratio = (2000 + (-1600)) / 2000 = 0.20 — AT 0.2, gets liquidated (<=)
        let (mut mgr, db) = setup_with_position(150.0, 2000.0, 500.0);
        let mut prices = HashMap::new();
        // slot_id is "slot1" but asset for price lookup must match position slot
        // The price key is the slot_id
        prices.insert("slot1".to_string(), 149.76_f64);
        let liquidated = mgr.check_margin_and_liquidate(&db, 0.2, &prices).unwrap();
        assert_eq!(liquidated.len(), 1, "should be liquidated");
        assert_eq!(liquidated[0], "slot1");
        // Position should be closed
        assert!(db.get_position("slot1").unwrap().is_none());
    }

    #[test]
    fn margin_below_threshold_triggers_liquidation() {
        // entry=150.0, current=148.0 — much bigger drop, margin well below 20%
        let (mut mgr, db) = setup_with_position(150.0, 2000.0, 500.0);
        let mut prices = HashMap::new();
        prices.insert("slot1".to_string(), 148.0_f64);
        let liquidated = mgr.check_margin_and_liquidate(&db, 0.2, &prices).unwrap();
        assert_eq!(liquidated.len(), 1);
    }

    #[test]
    fn liquidation_recorded_with_signal_minus_99() {
        let (mut mgr, db) = setup_with_position(150.0, 2000.0, 500.0);
        let mut prices = HashMap::new();
        prices.insert("slot1".to_string(), 149.52_f64);
        mgr.check_margin_and_liquidate(&db, 0.2, &prices).unwrap();
        // Check trades table for signal=-99
        let signal: i32 = db
            .conn()
            .query_row(
                "SELECT signal FROM trades WHERE side='exit' ORDER BY id DESC LIMIT 1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(signal, -99);
    }

    #[test]
    fn no_open_positions_nothing_liquidated() {
        let db = PaperDb::open_in_memory().unwrap();
        let mut mgr = PositionManager::new(10000.0);
        let prices = HashMap::new();
        let liquidated = mgr.check_margin_and_liquidate(&db, 0.2, &prices).unwrap();
        assert!(liquidated.is_empty());
    }

    #[test]
    fn leverage_1_needs_100_pct_loss_to_liquidate() {
        // With leverage=1, a 0.32% price drop should NOT trigger liquidation
        let (mut mgr, db) = setup_with_position(150.0, 2000.0, 1.0);
        let mut prices = HashMap::new();
        prices.insert("slot1".to_string(), 149.52_f64);
        let liquidated = mgr.check_margin_and_liquidate(&db, 0.2, &prices).unwrap();
        assert!(
            liquidated.is_empty(),
            "leverage=1 should not be liquidated on tiny drop"
        );
    }
}
