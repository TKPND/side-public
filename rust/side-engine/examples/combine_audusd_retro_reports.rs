//! combine_audusd_retro_reports — Phase 58 Plan (RETRO-03) one-off script
//!
//! Reads FOMC and NFP individual report.json files from the v4.2-audusd-retro
//! directory, combines them into a CombinedEventReport (ECB = empty Vec), writes:
//!   - docs/reports/v4.2-audusd-retro/report.json
//!   - docs/reports/v4.2-audusd-retro/report.md

use std::fs;

use side_engine::report::{generate_report_md_combined, CombinedEventReport};
use side_engine::scanner::macro_event::{FeeResult, SlotReport};

/// Local deserializable mirror for FeeResult.
/// combined_oos_pf can be null in JSON when no trades occurred.
#[derive(serde::Deserialize)]
struct FeeResultDe {
    fee_bps: f64,
    combined_oos_pf: Option<f64>,
    combined_oos_sharpe: f64,
    combined_oos_trades: usize,
    combined_oos_max_dd: f64,
    passed: bool,
    dsr_pvalue: f64,
    dsr_n_trials: usize,
}

fn to_fee_result(de: FeeResultDe) -> FeeResult {
    FeeResult {
        fee_bps: de.fee_bps,
        combined_oos_pf: de.combined_oos_pf.unwrap_or(0.0),
        combined_oos_sharpe: de.combined_oos_sharpe,
        combined_oos_trades: de.combined_oos_trades,
        combined_oos_max_dd: de.combined_oos_max_dd,
        passed: de.passed,
        dsr_pvalue: de.dsr_pvalue,
        dsr_n_trials: de.dsr_n_trials,
    }
}

/// Local deserializable mirror for SlotReport.
/// SlotReport.exit_type is &'static str and cannot implement Deserialize directly.
#[derive(serde::Deserialize)]
struct SlotReportDe {
    window_offset: u32,
    hold_bars: u32,
    exit_type: String,
    fee_results: Vec<FeeResultDe>,
}

fn to_slot_report(de: SlotReportDe) -> SlotReport {
    SlotReport {
        window_offset: de.window_offset,
        hold_bars: de.hold_bars,
        exit_type: match de.exit_type.as_str() {
            "none" => "none",
            "fixed_pct" => "fixed_pct",
            other => Box::leak(other.to_string().into_boxed_str()),
        },
        fee_results: de.fee_results.into_iter().map(to_fee_result).collect(),
        duration_bucket: None,
        liquidity_regime: None,
        per_trade_log: None,
    }
}

fn main() -> anyhow::Result<()> {
    let fomc_json = fs::read_to_string("../docs/reports/v4.2-audusd-retro/fomc/report.json")?;
    let nfp_json = fs::read_to_string("../docs/reports/v4.2-audusd-retro/nfp/report.json")?;

    let fomc_de: Vec<SlotReportDe> = serde_json::from_str(&fomc_json)?;
    let nfp_de: Vec<SlotReportDe> = serde_json::from_str(&nfp_json)?;

    let fomc: Vec<SlotReport> = fomc_de.into_iter().map(to_slot_report).collect();
    let nfp: Vec<SlotReport> = nfp_de.into_iter().map(to_slot_report).collect();

    // ECB is empty — AUDUSD ECB gate blocks via validate_pair_event_source (Phase 51)
    let combined = CombinedEventReport {
        fomc,
        ecb: vec![],
        nfp,
    };

    fs::create_dir_all("../docs/reports/v4.2-audusd-retro")?;

    // Write combined report.json
    let json = serde_json::to_string_pretty(&combined)?;
    fs::write("../docs/reports/v4.2-audusd-retro/report.json", &json)?;

    // Write combined report.md (McLean-Pontiff framing)
    let primary_fee_idx = 2; // AUDUSD uses default 2bps
    let md = generate_report_md_combined(
        &combined.fomc,
        &[],
        &combined.nfp,
        "AUDUSD",
        primary_fee_idx,
    );
    fs::write("../docs/reports/v4.2-audusd-retro/report.md", &md)?;

    println!(
        "Combined report.json: {} FOMC slots, {} NFP slots, 0 ECB slots",
        combined.fomc.len(),
        combined.nfp.len()
    );
    println!("Done: docs/reports/v4.2-audusd-retro/report.{{json,md}}");
    Ok(())
}
