//! side-engine CLI binary.
//!
//! Entry point for the fee-aware FX strategy discovery engine.
//! Exposes a `scan` subcommand with --event-source <boj|fomc|ecb|nfp>.

use std::path::Path;

use clap::{Parser, Subcommand};
use side_engine::pair::Pair;
use side_engine::parquet_loader::load_ohlcv_parquet;
use side_engine::report::{self, CombinedEventReport};
use side_engine::scanner::macro_event::{
    run_calendar_anomaly_fee_sweep, run_ecb_event_fee_sweep, run_fomc_event_fee_sweep,
    run_macro_event_fee_sweep, run_nfp_event_fee_sweep, MacroEventSlot,
};
use side_engine::scanner::run_macro_event_path;
use side_engine::wfd::{CvMode, GateConfig, WfdConfig};

// ---------------------------------------------------------------------------
// CLI types
// ---------------------------------------------------------------------------

#[derive(Parser, Debug)]
#[command(
    name = "side",
    about = "Fee-aware FX strategy discovery engine (USDJPY)"
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Run a fee sweep scan for a given event source.
    Scan {
        /// Currency pair to scan (default: usdjpy).
        #[arg(long, value_enum, default_value = "usdjpy")]
        pair: Pair,

        /// Event source to scan: boj (Bank of Japan) or fomc (Federal Reserve).
        #[arg(long, value_enum)]
        event_source: EventSource,

        /// Path to OHLCV CSV fixture (header: datetime_ns,open,high,low,close,volume).
        #[arg(long)]
        data: Option<String>,

        /// Output directory for report.json, report.md, VALIDATION.md (default: ./report).
        #[arg(long, default_value = "./report")]
        output: String,

        /// WFD in-sample months (default: 3 for macro_event low-freq preset).
        #[arg(long, default_value_t = 3)]
        wfd_is_months: usize,

        /// WFD out-of-sample months (default: 3).
        #[arg(long, default_value_t = 3)]
        wfd_oos_months: usize,

        /// Number of WFD walks (default: 3).
        #[arg(long, default_value_t = 3)]
        wfd_num_walks: usize,

        /// Optional: restrict to a single slot — window offset bars after event window end.
        /// Must be >= 1 (look-ahead protection, D-04).
        #[arg(long)]
        window_offset: Option<u32>,

        /// Optional: hold duration in bars for single-slot mode.
        #[arg(long)]
        hold_bars: Option<u32>,

        /// Optional: exit type for single-slot mode ("none" or "fixed_pct").
        #[arg(long)]
        exit_type: Option<String>,
    },
}

/// Event source selector for the scan subcommand.
#[derive(Debug, Clone, clap::ValueEnum)]
enum EventSource {
    /// Bank of Japan (BOJ) event windows.
    Boj,
    /// Federal Open Market Committee (FOMC) event windows.
    Fomc,
    /// European Central Bank (ECB) event windows.
    Ecb,
    /// Non-Farm Payrolls (NFP) release windows.
    Nfp,
    /// Combined: FOMC + ECB + NFP sweep (Phase 36, D-05).
    Combined,
    /// Calendar anomaly (day-of-week × month-position edges from Phase 44).
    Calendar,
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Scan {
            pair,
            event_source,
            data,
            output,
            wfd_is_months,
            wfd_oos_months,
            wfd_num_walks,
            window_offset,
            hold_bars,
            exit_type,
        } => run_scan(
            pair,
            event_source,
            data,
            output,
            wfd_is_months,
            wfd_oos_months,
            wfd_num_walks,
            window_offset,
            hold_bars,
            exit_type,
        ),
    }
}

#[allow(clippy::too_many_arguments)]
fn run_scan(
    pair: Pair,
    event_source: EventSource,
    data: Option<String>,
    output: String,
    wfd_is_months: usize,
    wfd_oos_months: usize,
    wfd_num_walks: usize,
    window_offset: Option<u32>,
    hold_bars: Option<u32>,
    exit_type: Option<String>,
) -> anyhow::Result<()> {
    // Reject incompatible pair × event_source combinations early
    if let Err(msg) = validate_pair_event_source(pair, &event_source) {
        eprintln!("error: {}", msg);
        std::process::exit(2);
    }

    // Validate window_offset >= 1 at CLI boundary (MED-04, D-04)
    if let Some(off) = window_offset {
        if off < 1 {
            eprintln!("error: --window-offset must be >= 1 (look-ahead protection)");
            std::process::exit(2);
        }
    }

    // Load OHLCV from --data path (default: rust/data/mirror/{PAIR}_1h.parquet)
    // D-05: default path auto-resolve; D-06: explicit --data takes priority
    // NOTE: `default_path` must be declared before `data_path` to ensure
    // the borrow lives long enough (borrowed value does not live long enough workaround).
    let default_path;
    let data_path = match data.as_deref() {
        Some(p) => p,
        None => {
            default_path = format!("rust/data/mirror/{}_1h.parquet", pair.as_str());
            &default_path
        }
    };
    let ohlcv = load_ohlcv_parquet(Path::new(data_path))
        .map_err(|e| anyhow::anyhow!("failed to load OHLCV from {data_path}: {e}"))?;

    // Ensure output directory exists
    std::fs::create_dir_all(&output)
        .map_err(|e| anyhow::anyhow!("failed to create output dir {output}: {e}"))?;

    // Single-slot mode vs full fee sweep
    let single_slot = match (window_offset, hold_bars, exit_type.as_deref()) {
        (Some(off), Some(hold), Some(exit)) => {
            let exit_static: &'static str = match exit {
                "none" => "none",
                "fixed_pct" => "fixed_pct",
                other => {
                    eprintln!("error: --exit-type must be \"none\" or \"fixed_pct\", got: {other}");
                    std::process::exit(2);
                }
            };
            Some(MacroEventSlot {
                window_offset: off,
                hold_bars: hold,
                exit_type: exit_static,
            })
        }
        _ => None,
    };

    if let Some(slot) = single_slot {
        // Single-slot legacy mode: run with CLI-configured WFD params, no fee sweep.
        // Uses BOJ path regardless of event_source (single-slot is diagnostic only).
        let wfd_cfg = WfdConfig {
            is_months: wfd_is_months,
            oos_months: wfd_oos_months,
            num_walks: wfd_num_walks,
            min_oos_pf: 1.0,
            min_annual_trades: 1,
            min_wfe: 0.0,
            min_oos_win_rate: 0.0,
            max_oos_drawdown: -1.0,
            fee_bps: 0.0,
            cv_mode: CvMode::PurgedKFold {
                k: wfd_num_walks,
                embargo_days: 1,
            },
        };
        let gate = GateConfig::macro_event();
        let results = run_macro_event_path(&ohlcv, &wfd_cfg, &gate, Some(vec![slot]));
        println!("Completed {} slot(s)", results.len());
        let passed_count = results.iter().filter(|r| !r.pruned).count();
        println!("Passed gate: {passed_count} / {}", results.len());
        for r in &results {
            println!(
                "  off={:2} hold={:2} exit={:<10} pf={:.3} passed={} pruned={}",
                r.slot.window_offset,
                r.slot.hold_bars,
                r.slot.exit_type,
                r.result.combined_oos_pf,
                r.result.passed,
                r.pruned,
            );
        }
    } else {
        // Full fee sweep: configured slot grid × 5 fee levels.
        // Combined source handled separately (3 × Vec<SlotReport> can't fit single sweep_results).
        if matches!(event_source, EventSource::Combined) {
            // Source: CONTEXT.md D-05, D-08, D-11
            eprintln!("Running FOMC fee sweep...");
            let fomc_results = run_fomc_event_fee_sweep(&ohlcv, pair);
            eprintln!("Running ECB fee sweep...");
            let ecb_results = run_ecb_event_fee_sweep(&ohlcv, pair);
            eprintln!("Running NFP fee sweep...");
            let nfp_results = run_nfp_event_fee_sweep(&ohlcv);

            let combined = CombinedEventReport {
                fomc: fomc_results,
                ecb: ecb_results,
                nfp: nfp_results,
            };

            std::fs::create_dir_all(&output)?;

            // JSON (borrow combined to avoid move before field access — Pitfall 2)
            let json = serde_json::to_string_pretty(&combined)?;
            std::fs::write(format!("{output}/report.json"), &json)?;
            eprintln!("Written: {output}/report.json");

            // Markdown
            let primary_fee_idx = match pair.as_str() {
                "EURJPY" => 4, // FEE_LEVELS[4] = 5.0 bps RT
                _ => 2,        // FEE_LEVELS[2] = 2.0 bps RT (default)
            };
            let md = report::generate_report_md_combined(
                &combined.fomc,
                &combined.ecb,
                &combined.nfp,
                pair.as_str(),
                primary_fee_idx,
            );
            std::fs::write(format!("{output}/report.md"), &md)?;
            eprintln!("Written: {output}/report.md");

            // VALIDATION.md — chain 3 sources into flat Vec (Pitfall 1)
            let all_slots: Vec<_> = combined
                .fomc
                .iter()
                .chain(combined.ecb.iter())
                .chain(combined.nfp.iter())
                .cloned()
                .collect();
            let val = report::generate_validation_md(&all_slots, "36-combined-report");
            std::fs::write(format!("{output}/VALIDATION.md"), &val)?;
            eprintln!("Written: {output}/VALIDATION.md");

            eprintln!("Combined report written to {output}");
            return Ok(());
        }

        // Dispatch on event_source: boj → BOJ sweep, fomc → FOMC sweep.
        let sweep_results = match event_source {
            EventSource::Boj => {
                eprintln!("Running BOJ fee sweep: configured slot grid × 5 fee levels...");
                run_macro_event_fee_sweep(&ohlcv)
            }
            EventSource::Fomc => {
                eprintln!("Running FOMC fee sweep: configured slot grid × 5 fee levels...");
                run_fomc_event_fee_sweep(&ohlcv, pair)
            }
            EventSource::Ecb => {
                eprintln!("Running ECB fee sweep: configured slot grid × 5 fee levels...");
                run_ecb_event_fee_sweep(&ohlcv, pair)
            }
            EventSource::Nfp => {
                eprintln!("Running NFP fee sweep: configured slot grid × 5 fee levels...");
                run_nfp_event_fee_sweep(&ohlcv)
            }
            EventSource::Calendar => {
                eprintln!(
                    "Running Calendar Anomaly fee sweep: configured slot grid × 5 fee levels..."
                );
                run_calendar_anomaly_fee_sweep(&ohlcv)
            }
            EventSource::Combined => unreachable!("Combined handled above"),
        };

        // Serialize to report.json
        let json_path = format!("{output}/report.json");
        let json = serde_json::to_string_pretty(&sweep_results)?;
        std::fs::write(&json_path, &json)?;
        eprintln!("Written: {json_path}");

        // Generate and write report.md
        let md = match event_source {
            EventSource::Boj => report::generate_report_md(&sweep_results),
            EventSource::Fomc => report::generate_report_md_fomc(&sweep_results, pair.as_str()),
            EventSource::Ecb => report::generate_report_md_ecb(&sweep_results, pair.as_str()),
            EventSource::Nfp => report::generate_report_md_nfp(&sweep_results, pair.as_str()),
            EventSource::Calendar => {
                // Read calendar_edges.json and compute BQ summary
                let edges_json = match std::fs::read_to_string("data/calendar_edges.json") {
                    Ok(content) => content,
                    Err(_) => {
                        eprintln!("warning: calendar_edges.json not found, using fallback");
                        "[]".to_string()
                    }
                };

                #[derive(serde::Deserialize)]
                struct CalendarEdge {
                    day_of_week: String,
                    month_position: String,
                    #[serde(default)]
                    _mean_return: f64,
                    #[serde(default)]
                    _t_stat: f64,
                    t_crit: f64,
                    #[serde(default)]
                    _sample_count: usize,
                }

                let edges: Vec<CalendarEdge> =
                    serde_json::from_str(&edges_json).unwrap_or_else(|e| {
                        eprintln!(
                            "warning: failed to parse calendar_edges.json ({}), using fallback",
                            e
                        );
                        Vec::new()
                    });

                let edge_count = edges.len();
                let dim_count = {
                    use std::collections::HashSet;
                    edges
                        .iter()
                        .map(|e| (e.day_of_week.clone(), e.month_position.clone()))
                        .collect::<HashSet<_>>()
                        .len()
                };
                let bonferroni_threshold = edges.first().map(|e| e.t_crit).unwrap_or(4.40); // Default Bonferroni for 96 trials (per D-07)

                report::generate_report_md_calendar(
                    &sweep_results,
                    edge_count,
                    dim_count,
                    bonferroni_threshold,
                )
            }
            EventSource::Combined => unreachable!("Combined handled above"),
        };
        let md_path = format!("{output}/report.md");
        std::fs::write(&md_path, &md)?;
        eprintln!("Written: {md_path}");

        // Generate and write VALIDATION.md
        let val = match event_source {
            EventSource::Boj => {
                report::generate_validation_md(&sweep_results, "25-full-scan-report")
            }
            EventSource::Fomc => {
                report::generate_validation_md(&sweep_results, "33-fomc-report-ship")
            }
            EventSource::Ecb => {
                report::generate_validation_md(&sweep_results, "35-ecb-report-ship")
            }
            EventSource::Nfp => {
                report::generate_validation_md(&sweep_results, "35-nfp-report-ship")
            }
            EventSource::Calendar => {
                report::generate_validation_md(&sweep_results, "46-calendar-anomaly")
            }
            EventSource::Combined => unreachable!("Combined handled above"),
        };
        let val_path = format!("{output}/VALIDATION.md");
        std::fs::write(&val_path, &val)?;
        eprintln!("Written: {val_path}");

        // Summary line to stdout
        let pass_count = sweep_results
            .iter()
            .filter(|sr| sr.fee_results.get(2).map(|fr| fr.passed).unwrap_or(false))
            .count();
        println!(
            "Sweep complete: {} runs. PASS slots at fee=2bps: {}",
            sweep_results.len() * 5,
            pass_count
        );
    }

    Ok(())
}

/// Validate that a (Pair, EventSource) combination is supported.
///
/// Returns Err with a descriptive message for invalid combinations.
/// BOJ event windows are USDJPY-specific and cannot be used with EURUSD.
fn validate_pair_event_source(pair: Pair, event_source: &EventSource) -> Result<(), &'static str> {
    if matches!(pair, Pair::Eurusd) && matches!(event_source, EventSource::Boj) {
        return Err("--pair eurusd is not compatible with --event-source boj (BOJ windows are USDJPY-specific)");
    }
    // AUDUSD: ECB チャネルなし（EUR→AUD は直接影響なし — STATE.md Decision 8）
    if matches!(pair, Pair::Audusd) && matches!(event_source, EventSource::Ecb) {
        return Err("--pair audusd is not compatible with --event-source ecb (no EUR→AUD channel)");
    }
    // AUDUSD: Combined は ECB を含むため除外
    if matches!(pair, Pair::Audusd) && matches!(event_source, EventSource::Combined) {
        return Err(
            "--pair audusd is not compatible with --event-source combined (combined includes ECB)",
        );
    }
    // BOJ は USDJPY 専用 — AUDUSD/EURJPY 両方で reject
    if matches!(pair, Pair::Audusd) && matches!(event_source, EventSource::Boj) {
        return Err("--pair audusd is not compatible with --event-source boj (BOJ windows are USDJPY-specific)");
    }
    if matches!(pair, Pair::Eurjpy) && matches!(event_source, EventSource::Boj) {
        return Err("--pair eurjpy is not compatible with --event-source boj (BOJ windows are USDJPY-specific)");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_pair_event_source_rejects_eurusd_boj() {
        let result = validate_pair_event_source(Pair::Eurusd, &EventSource::Boj);
        assert!(result.is_err());
        let msg = result.unwrap_err();
        assert!(msg.contains("eurusd"));
        assert!(msg.contains("boj"));
    }

    #[test]
    fn validate_pair_event_source_allows_usdjpy_boj() {
        assert!(validate_pair_event_source(Pair::Usdjpy, &EventSource::Boj).is_ok());
    }

    #[test]
    fn validate_pair_event_source_allows_eurusd_fomc_ecb_nfp_combined() {
        assert!(validate_pair_event_source(Pair::Eurusd, &EventSource::Fomc).is_ok());
        assert!(validate_pair_event_source(Pair::Eurusd, &EventSource::Ecb).is_ok());
        assert!(validate_pair_event_source(Pair::Eurusd, &EventSource::Nfp).is_ok());
        assert!(validate_pair_event_source(Pair::Eurusd, &EventSource::Combined).is_ok());
    }

    #[test]
    #[ignore]
    fn test_event_source_calendar_enum() {
        todo!("Parse CLI args with --event-source calendar and verify EventSource::Calendar variant is created");
    }

    #[test]
    #[ignore]
    fn test_validate_pair_event_source_calendar() {
        todo!("Verify validate_pair_event_source accepts EventSource::Calendar for all pairs (USDJPY and EURUSD)");
    }

    #[test]
    #[ignore]
    fn test_dispatch_match_calendar() {
        todo!(
            "Confirm run_scan dispatches EventSource::Calendar to run_calendar_anomaly_fee_sweep"
        );
    }

    // D-09: AUDUSD/EURJPY validate_pair_event_source tests

    #[test]
    fn validate_pair_event_source_rejects_audusd_ecb() {
        let result = validate_pair_event_source(Pair::Audusd, &EventSource::Ecb);
        assert!(result.is_err());
        let msg = result.unwrap_err();
        assert!(msg.contains("audusd"));
        assert!(msg.contains("ecb"));
    }

    #[test]
    fn validate_pair_event_source_rejects_audusd_combined() {
        let result = validate_pair_event_source(Pair::Audusd, &EventSource::Combined);
        assert!(result.is_err());
        let msg = result.unwrap_err();
        assert!(msg.contains("audusd"));
        assert!(msg.contains("combined"));
    }

    #[test]
    fn validate_pair_event_source_rejects_audusd_boj() {
        let result = validate_pair_event_source(Pair::Audusd, &EventSource::Boj);
        assert!(result.is_err());
        let msg = result.unwrap_err();
        assert!(msg.contains("audusd"));
        assert!(msg.contains("boj"));
    }

    #[test]
    fn validate_pair_event_source_rejects_eurjpy_boj() {
        let result = validate_pair_event_source(Pair::Eurjpy, &EventSource::Boj);
        assert!(result.is_err());
        let msg = result.unwrap_err();
        assert!(msg.contains("eurjpy"));
        assert!(msg.contains("boj"));
    }

    #[test]
    fn validate_pair_event_source_allows_audusd_fomc_nfp() {
        assert!(validate_pair_event_source(Pair::Audusd, &EventSource::Fomc).is_ok());
        assert!(validate_pair_event_source(Pair::Audusd, &EventSource::Nfp).is_ok());
    }

    #[test]
    fn validate_pair_event_source_allows_eurjpy_fomc_ecb_nfp_combined() {
        assert!(validate_pair_event_source(Pair::Eurjpy, &EventSource::Fomc).is_ok());
        assert!(validate_pair_event_source(Pair::Eurjpy, &EventSource::Ecb).is_ok());
        assert!(validate_pair_event_source(Pair::Eurjpy, &EventSource::Nfp).is_ok());
        assert!(validate_pair_event_source(Pair::Eurjpy, &EventSource::Combined).is_ok());
    }
}

#[cfg(test)]
mod tests_event_source_calendar {
    #[test]
    #[ignore]
    fn test_event_source_calendar_enum() {
        todo!("Parse CLI args with --event-source calendar and verify EventSource::Calendar variant is created");
    }

    #[test]
    #[ignore]
    fn test_validate_pair_event_source_calendar() {
        todo!("Verify validate_pair_event_source accepts EventSource::Calendar for all pairs (USDJPY and EURUSD)");
    }

    #[test]
    #[ignore]
    fn test_dispatch_match_calendar() {
        todo!(
            "Confirm run_scan dispatches EventSource::Calendar to run_calendar_anomaly_fee_sweep"
        );
    }
}
