//! Dump per-trade log for all 192 macro_event_drift slots as JSON Lines.
//!
//! SEAL policy: additive only — existing wfd.rs / backtest.rs / macro_event.rs UNTOUCHED.
//! Source commit anchor: a5a1102 (Rust engine SEAL anchor, Phase 85).
//!
//! Each output line is a JSON object:
//! ```json
//! {"cell_id":"w1_h1_none","fold":0,"trade_id":0,"entry_bar":42,"entry_price":110.5,
//!  "direction":1,"atr_at_entry":0.5,"bars":[{"high":110.8,"low":110.1,"close":110.5,"atr":0.5}]}
//! ```
//!
//! The Python `gen_per_trade_log.py` calls this bin via subprocess and converts
//! the JSON Lines output to per_trade_log.parquet with the required schema.
//!
//! Fold boundary: uses `validation::purged_kfold_indices` matching the CvMode::PurgedKFold
//! used by macro_event_wfd_config() (k=3, embargo_days=1). This matches wfd.rs exactly.
//!
//! ATR: indicators::atr (Wilder, period=14, SEAL atr_window_bars).

use std::collections::HashMap;
use std::io::{BufWriter, Write};
use std::path::PathBuf;

use anyhow::Result;
use clap::Parser;
use serde::Serialize;
use side_engine::indicators;
use side_engine::parquet_loader::load_ohlcv_parquet;
use side_engine::scanner::macro_event::{macro_event_slots, macro_event_wfd_config};
use side_engine::strategies::{generate_signals, Ohlcv};
use side_engine::validation::{bars_per_day_from_datetimes_ns, purged_kfold_indices};

const ATR_PERIOD: usize = 14; // SEAL-03 atr_window_bars
const EMBARGO_DAYS: usize = 1; // macro_event_wfd_config embargo_days

/// CLI arguments.
#[derive(Parser, Debug)]
#[command(
    name = "dump_per_trade_log",
    about = "Dump per-trade bar log for all macro_event_drift slots as JSON Lines"
)]
struct Args {
    /// Output path for JSON Lines file.
    #[arg(long, default_value = "data/v4.9/per_trade_log.jsonl")]
    output: PathBuf,

    /// Path to OHLCV Parquet.
    #[arg(long, default_value = "data/ohlcv/usdjpy_1h_2022_2026.parquet")]
    data: PathBuf,

    /// Run smoke mode: only the first slot, first OOS fold.
    #[arg(long)]
    smoke: bool,
}

#[derive(Serialize)]
struct BarOut {
    high: f64,
    low: f64,
    close: f64,
    atr: f64,
}

#[derive(Serialize)]
struct TradeOut {
    cell_id: String,
    fold: usize,
    trade_id: u64,
    entry_bar: usize, // absolute bar index in the full OHLCV array
    entry_price: f64,
    direction: i8,
    atr_at_entry: f64,
    bars: Vec<BarOut>,
}

/// Extract entry events from signals within OOS range [oos_start..oos_end).
///
/// Signal encoding (from position_to_signal = diff(positions).clip(-1,1)):
///   +1: position changed from 0 to 1 (new long entry)
///   -1: position changed from 1 to 0 (close/flatten)
///    0: no change
///
/// backtest.rs applies shift(1): position[i] is driven by signals[i-1].
/// So to replicate: for bar i in OOS, effective signal is signals[i-1].
///
/// Returns (entry_bar_abs, entry_price, direction).
fn extract_oos_entries(
    signals: &[i8],
    open: &[f64],
    oos_start: usize,
    oos_end: usize,
) -> Vec<(usize, f64, i8)> {
    let mut entries = Vec::new();
    let mut pos: i8 = 0;

    for i in oos_start..oos_end {
        // shift(1): effective signal at bar i is signals[i-1] (if i>0)
        let sig = if i > 0 { signals[i - 1] } else { 0i8 };

        let new_pos = if sig == 2 || sig == -2 {
            0i8
        } else if sig != 0 {
            sig
        } else {
            pos
        };

        // Long-only (macro_event_drift mode=1)
        let new_pos = new_pos.max(0);

        if new_pos != pos && new_pos > 0 {
            // New entry at bar i; entry_price = open[i]
            entries.push((i, open[i], new_pos));
        }
        pos = new_pos;
    }
    entries
}

fn main() -> Result<()> {
    let args = Args::parse();

    eprintln!("[dump_per_trade_log] loading OHLCV from {:?}", args.data);
    let ohlcv = load_ohlcv_parquet(&args.data)?;

    let wfd_cfg = macro_event_wfd_config();
    let slots = macro_event_slots();
    let slots_to_run: &[_] = if args.smoke { &slots[..1] } else { &slots[..] };

    let n_bars = ohlcv.close.len();

    // Build fold boundaries using PurgedKFold (matches wfd.rs CvMode::PurgedKFold).
    let bars_per_day = bars_per_day_from_datetimes_ns(&ohlcv.datetimes_ns);
    let embargo_bars = EMBARGO_DAYS * bars_per_day;
    let k = if let side_engine::wfd::CvMode::PurgedKFold { k, .. } = wfd_cfg.cv_mode {
        k
    } else {
        3 // fallback
    };
    let fold_splits = purged_kfold_indices(n_bars, k, embargo_bars)?;

    // OOS ranges: (oos_start, oos_end, fold_idx)
    let oos_ranges: Vec<(usize, usize, usize)> = fold_splits
        .iter()
        .map(|fs| {
            let oos_start = *fs.oos_indices.first().unwrap();
            let oos_end = fs.oos_indices.last().unwrap() + 1;
            (oos_start, oos_end, fs.fold_idx)
        })
        .collect();

    let folds_to_run: &[(usize, usize, usize)] = if args.smoke {
        &oos_ranges[..1.min(oos_ranges.len())]
    } else {
        &oos_ranges[..]
    };

    eprintln!(
        "[dump_per_trade_log] running {} slots × {} folds (smoke={})",
        slots_to_run.len(),
        folds_to_run.len(),
        args.smoke
    );

    // Pre-compute full ATR on all data (Wilder, period=14).
    let full_atr = indicators::atr(&ohlcv.high, &ohlcv.low, &ohlcv.close, ATR_PERIOD);

    if let Some(parent) = args.output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = std::fs::File::create(&args.output)?;
    let mut writer = BufWriter::new(file);

    let mut global_trade_id: u64 = 0;
    let mut total_trades = 0usize;

    for slot in slots_to_run {
        let cell_id = format!(
            "w{}_h{}_{}",
            slot.window_offset, slot.hold_bars, slot.exit_type
        );

        // Build params for generate_signals.
        let mut params: HashMap<String, serde_json::Value> = HashMap::new();
        params.insert(
            "window_offset".to_string(),
            serde_json::Value::Number(serde_json::Number::from(slot.window_offset)),
        );
        params.insert(
            "hold_bars".to_string(),
            serde_json::Value::Number(serde_json::Number::from(slot.hold_bars)),
        );
        params.insert(
            "exit_type".to_string(),
            serde_json::Value::String(slot.exit_type.to_string()),
        );
        if slot.exit_type == "fixed_pct" {
            params.insert(
                "tp_pct".to_string(),
                serde_json::Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
            );
            params.insert(
                "sl_pct".to_string(),
                serde_json::Value::Number(serde_json::Number::from_f64(0.003).expect("finite")),
            );
        }

        let full_ohlcv = Ohlcv {
            open: &ohlcv.open,
            high: &ohlcv.high,
            low: &ohlcv.low,
            close: &ohlcv.close,
            volume: &ohlcv.volume,
            datetimes_ns: Some(&ohlcv.datetimes_ns),
            aux_close: None,
        };
        // Generate signals on full data (same as wfd.rs does per-fold for OOS slice,
        // but we use full data here so bar indices are absolute).
        let signals = generate_signals("macro_event_drift", &full_ohlcv, &params);

        for &(oos_start, oos_end, fold_idx) in folds_to_run {
            let entries = extract_oos_entries(&signals, &ohlcv.open, oos_start, oos_end);

            for (entry_bar_abs, entry_price, direction) in entries {
                let atr_at_entry = full_atr.get(entry_bar_abs).copied().unwrap_or(f64::NAN);
                if atr_at_entry.is_nan() {
                    continue; // skip trades where ATR not yet seeded
                }

                // Bars: from entry_bar_abs to oos_end (fold_end sentinel convention).
                let bars: Vec<BarOut> = (entry_bar_abs..oos_end)
                    .map(|bi| BarOut {
                        high: ohlcv.high[bi],
                        low: ohlcv.low[bi],
                        close: ohlcv.close[bi],
                        atr: full_atr.get(bi).copied().unwrap_or(atr_at_entry),
                    })
                    .collect();

                if bars.is_empty() {
                    continue;
                }

                let trade = TradeOut {
                    cell_id: cell_id.clone(),
                    fold: fold_idx,
                    trade_id: global_trade_id,
                    entry_bar: entry_bar_abs,
                    entry_price,
                    direction,
                    atr_at_entry,
                    bars,
                };
                let line = serde_json::to_string(&trade)?;
                writeln!(writer, "{}", line)?;
                global_trade_id += 1;
                total_trades += 1;
            }
        }
    }

    writer.flush()?;
    eprintln!(
        "[dump_per_trade_log] wrote {} trades to {:?}",
        total_trades, args.output
    );
    Ok(())
}
