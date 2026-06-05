//! Dump v4.8 WFD fold-level OOS PF as canonical JSON for Phase 87 parity test.
//!
//! SEAL policy: additive only — existing wfd.rs / SlotReport / WalkResult UNTOUCHED.
//! Source commit anchor: a5a1102 (Rust engine SEAL anchor, Phase 85).
//!
//! Output schema:
//! ```json
//! {
//!   "source_commit": "a5a1102",
//!   "generated_at": "<ISO8601>",
//!   "cells": {
//!     "<cell_id>": {
//!       "<fold_idx>": { "is_pf": 1.23, "oos_pf": 0.98 }
//!     }
//!   }
//! }
//! ```
//!
//! Cell ID format: `w{window_offset}_h{hold_bars}_{exit_type}` (72 cells when
//! fee_bps=0 and exit_type is restricted to "none"; full 192 otherwise).
//! This bin runs all 192 slots (16×6×2) at fee_bps=0 and produces one entry
//! per slot per walk, keyed by slot cell_id and walk fold_idx.

use std::collections::BTreeMap;
use std::path::PathBuf;

use anyhow::Result;
use chrono::Utc;
use clap::Parser;
use serde::Serialize;
use side_engine::parquet_loader::load_ohlcv_parquet;
use side_engine::scanner::macro_event::{
    macro_event_slots, macro_event_wfd_config, run_macro_event_path,
};
use side_engine::wfd::GateConfig;

/// CLI arguments.
#[derive(Parser, Debug)]
#[command(
    name = "dump_parity_pf",
    about = "Dump v4.8 fold-level IS/OOS PF as canonical JSON (Phase 87 parity reference)"
)]
struct Args {
    /// Output path for the JSON reference file.
    #[arg(long, default_value = "data/v4.8_parity_reference.json")]
    output: PathBuf,

    /// Path to OHLCV Parquet (default: data/ohlcv/usdjpy_1h_2022_2026.parquet).
    #[arg(long, default_value = "data/ohlcv/usdjpy_1h_2022_2026.parquet")]
    data: PathBuf,

    /// Fee in bps for the WFD run (default: 0.0 for parity reference).
    #[arg(long, default_value_t = 0.0)]
    fee_bps: f64,
}

#[derive(Serialize)]
struct FoldPf {
    is_pf: f64,
    oos_pf: f64,
}

#[derive(Serialize)]
struct ParityReference {
    /// cells[cell_id][fold_idx_str] = {is_pf, oos_pf}
    /// Field order is alphabetical to match Python json.dumps(sort_keys=True).
    cells: BTreeMap<String, BTreeMap<String, FoldPf>>,
    generated_at: String,
    source_commit: String,
}

fn main() -> Result<()> {
    let args = Args::parse();

    eprintln!("[dump_parity_pf] loading OHLCV from {:?}", args.data);
    let ohlcv = load_ohlcv_parquet(&args.data)?;

    let gate = GateConfig::macro_event();
    let slots = macro_event_slots();

    let mut cells: BTreeMap<String, BTreeMap<String, FoldPf>> = BTreeMap::new();

    eprintln!(
        "[dump_parity_pf] running {} slots at fee_bps={}",
        slots.len(),
        args.fee_bps
    );

    for slot in slots {
        let cell_id = format!(
            "w{}_h{}_{}",
            slot.window_offset, slot.hold_bars, slot.exit_type
        );

        let mut wfd_cfg = macro_event_wfd_config();
        wfd_cfg.fee_bps = args.fee_bps;

        let slot_results = run_macro_event_path(&ohlcv, &wfd_cfg, &gate, Some(vec![slot]));
        let result = &slot_results[0].result;

        let fold_map: BTreeMap<String, FoldPf> = result
            .walks
            .iter()
            // Skip walks where oos_pf is NaN or +inf (no OOS losses or no OOS data).
            // serde_json serializes both NaN and f64::INFINITY as JSON null (invalid in JSON spec).
            .filter(|w| w.oos_pf.is_finite())
            .map(|w| {
                (
                    w.walk_id.to_string(),
                    FoldPf {
                        is_pf: w.is_pf,
                        oos_pf: w.oos_pf,
                    },
                )
            })
            .collect();

        cells.insert(cell_id, fold_map);
    }

    let parity_ref = ParityReference {
        source_commit: "a5a1102".to_string(),
        generated_at: Utc::now().to_rfc3339(),
        cells,
    };

    // Canonical JSON: BTreeMap guarantees key sort order; no pretty-print (minimal separators).
    let json_str = serde_json::to_string(&parity_ref)?;

    // Verify canonical form matches Python's json.dumps(sort_keys=True, separators=(',',':')):
    // serde_json::to_string already produces compact JSON with ':' and ',' separators.
    // BTreeMap keys are sorted lexicographically, matching sort_keys=True.
    // No trailing newline written (write_text without newline append).

    if let Some(parent) = args.output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&args.output, json_str.as_bytes())?;

    eprintln!(
        "[dump_parity_pf] wrote {} cells to {:?}",
        parity_ref.cells.len(),
        args.output
    );
    Ok(())
}
