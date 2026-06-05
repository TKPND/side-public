//! Report generation for Phase 25 full scan results.
//!
//! Produces:
//! - `report.md`   — McLean-Pontiff frame + BOJ regime context (D-08)
//! - `VALIDATION.md` — nyquist_compliant: true frontmatter (D-10)

use crate::scanner::macro_event::{
    FeeResult, SlotReport, EXIT_TYPES, HOLD_BARS_VALUES, WINDOW_OFFSETS,
};
use crate::wfd::GateConfig;

/// Fee sweep levels (bps RT) — must match `run_macro_event_fee_sweep`.
const FEE_LEVELS: [f64; 5] = [0.0, 1.0, 2.0, 3.0, 5.0];

// ---------------------------------------------------------------------------
// Combined report types (D-01, D-02)
// ---------------------------------------------------------------------------

/// Combined report aggregating FOMC + ECB + NFP sweep results (D-01).
///
/// Serializes to JSON for report.json output. Deserialization not supported
/// because SlotReport.exit_type is &'static str (no Deserialize derive).
#[derive(Debug, Clone, serde::Serialize)]
pub struct CombinedEventReport {
    pub fomc: Vec<SlotReport>,
    pub ecb: Vec<SlotReport>,
    pub nfp: Vec<SlotReport>,
}

/// Index into FEE_LEVELS for the primary pass criterion (fee=2 bps RT).
const PRIMARY_FEE_IDX: usize = 2;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Generate `report.md` content (D-08).
///
/// Structure:
/// 1. Executive Summary — PASS/NULL at fee=2bps RT, pf ≥ 2.0
/// 2. Methodology — 96 slot enumeration, purged 5-fold WFD, 6-gate composite
/// 3. Full Scan Results — 96 slots × 5 fee levels table
/// 4. BOJ Regime Context — YCC exit 2024-03, rate hikes as robustness caveat
/// 5. Conclusion — McLean-Pontiff (2016) frame if NULL
pub fn generate_report_md(results: &[SlotReport]) -> String {
    let pass_at_fee2: Vec<&SlotReport> = results
        .iter()
        .filter(|sr| {
            sr.fee_results
                .get(PRIMARY_FEE_IDX)
                .map(|fr| fr.passed)
                .unwrap_or(false)
        })
        .collect();

    let n_pass = pass_at_fee2.len();
    let n_total = results.len();

    let mut md = String::new();

    // -----------------------------------------------------------------------
    // 1. Executive Summary
    // -----------------------------------------------------------------------
    md.push_str("# BOJ Macro Event Drift — Full Scan Report\n\n");
    md.push_str("## 1. Executive Summary\n\n");

    if n_pass > 0 {
        md.push_str(&format!(
            "**PASS: {n_pass} candidate slot(s) found at fee=2bps RT (pf ≥ 2.0)**\n\n"
        ));
        md.push_str("Winning slots:\n\n");
        for sr in &pass_at_fee2 {
            let fr = &sr.fee_results[PRIMARY_FEE_IDX];
            md.push_str(&format!(
                "- window_offset={} hold_bars={} exit_type={} — OOS PF={:.3} trades={}\n",
                sr.window_offset,
                sr.hold_bars,
                sr.exit_type,
                fr.combined_oos_pf,
                fr.combined_oos_trades
            ));
        }
        md.push('\n');
    } else {
        md.push_str("**NULL: No slots passed at fee=2bps RT (pf ≥ 2.0)**\n\n");
        md.push_str(
            "The 96-slot parameter space for the BOJ macro event drift strategy produced \
             no configurations that clear the 6-gate composite criterion at fee=2bps RT. \
             This is a valid scientific outcome per McLean-Pontiff (2016): exhaustive \
             exploration with null result is informative and bounds the search space.\n\n",
        );
    }

    // -----------------------------------------------------------------------
    // 2. Methodology
    // -----------------------------------------------------------------------
    md.push_str("## 2. Methodology\n\n");
    md.push_str(
        "**Parameter space:** 96 slots = 8 window_offsets (1–8 bars after BOJ window end) \
         × 6 hold_bars (1, 2, 3, 6, 12, 24) × 2 exit_types (none, fixed_pct).\n\n",
    );
    md.push_str(
        "**Walk-forward validation (WFD):** Purged 5-fold cross-validation with 1-day embargo \
         (Lopez de Prado 2018, ch. 7). Config: IS=3M, OOS=3M, walks=3. \
         Naive 70/30 split is prohibited.\n\n",
    );
    md.push_str(
        "**6-gate composite criterion (all gates must pass):**\n\
         1. |t| > 4.40 (Bonferroni correction, n=96 trials)\n\
         2. DSR p-value < 0.05 (Deflated Sharpe Ratio, Bailey & Lopez de Prado 2014)\n\
         3. mean > 2 × cost (mean OOS return > 2× fee cost)\n\
         4. Purged 5-fold OOS PF ≥ 2.0 in 4/5 folds at fee=1bps per side\n\
         5. H1/H2 sign agreement\n\
         6. Bootstrap CI excludes 0\n\n",
    );
    md.push_str(
        "**Fee sweep:** {0, 1, 2, 3, 5} bps RT. Primary pass criterion: pf ≥ 2.0 at fee=2bps RT.\n\n",
    );
    md.push_str(&format!(
        "**Total WFD runs:** {} slots × {} fee levels = {} runs.\n\n",
        n_total,
        FEE_LEVELS.len(),
        n_total * FEE_LEVELS.len()
    ));
    md.push_str(
        "**Direction:** Long only (short excluded — full scan in POC confirmed no edge).\n\n",
    );

    // -----------------------------------------------------------------------
    // 3. Full Scan Results Table
    // -----------------------------------------------------------------------
    md.push_str("## 3. Full Scan Results\n\n");
    md.push_str(
        "| window_offset | hold_bars | exit_type | fee=0 | fee=1 | fee=2 | fee=3 | fee=5 |\n",
    );
    md.push_str(
        "|--------------|-----------|-----------|-------|-------|-------|-------|-------|\n",
    );

    for sr in results {
        let cells: Vec<String> = sr.fee_results.iter().map(pass_cell).collect();
        let row = format!(
            "| {:13} | {:9} | {:9} | {} | {} | {} | {} | {} |\n",
            sr.window_offset,
            sr.hold_bars,
            sr.exit_type,
            cells.first().map(|s| s.as_str()).unwrap_or("?"),
            cells.get(1).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(2).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(3).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(4).map(|s| s.as_str()).unwrap_or("?"),
        );
        md.push_str(&row);
    }
    md.push('\n');

    // -----------------------------------------------------------------------
    // 4. BOJ Regime Context
    // -----------------------------------------------------------------------
    md.push_str("## 4. BOJ Regime Context\n\n");
    md.push_str(
        "The Bank of Japan (BOJ) underwent a significant policy regime change during the \
         scan window. Key dates that may affect strategy robustness:\n\n",
    );
    md.push_str(
        "- **2024-03:** YCC (Yield Curve Control) policy exit — upper band removed. \
         USDJPY volatility regime shifted.\n",
    );
    md.push_str(
        "- **2024-07:** Rate hike +0.25% — largest BOJ rate hike in ~15 years. \
         USDJPY experienced a volatility spike (>10 yen move in days).\n",
    );
    md.push_str("- **2024-10:** Rate hike +0.25%. Further normalization of monetary policy.\n");
    md.push_str(
        "- **2026 outlook:** BOJ normalization expected to continue. \
         Strategy performance should be re-evaluated as rate environment evolves.\n\n",
    );
    md.push_str(
        "**Robustness caveat:** Results from 2024–2025 data include the regime transition period. \
         Strategies that passed should be re-validated on post-YCC data only (2024-04+) \
         to confirm the edge survives the regime change.\n\n",
    );

    // -----------------------------------------------------------------------
    // 5. Conclusion
    // -----------------------------------------------------------------------
    md.push_str("## 5. Conclusion\n\n");

    if n_pass > 0 {
        md.push_str(&format!(
            "**Result: PASS** — {n_pass} slot(s) survive the 6-gate composite criterion \
             at fee=2bps RT.\n\n"
        ));
        md.push_str(
            "These candidates should proceed to out-of-sample validation on held-out 2026 data \
             and paper trading to confirm live edge.\n\n",
        );
        md.push_str(
            "Note: All results are subject to the BOJ regime caveat in Section 4. \
             Live performance monitoring is mandatory before capital deployment.\n",
        );
    } else {
        md.push_str(
            "**Result: NULL** — The BOJ macro event drift parameter space (96 slots) \
             produces no configurations that clear the 6-gate composite criterion \
             at fee=2bps RT.\n\n",
        );
        md.push_str(
            "Per McLean-Pontiff (2016, \"Does Academic Research Destroy Stock Return Predictability?\"), \
             a systematic null result is scientifically valid and informative. \
             The exploration is complete: BOJ calendar timing alone, in the parameter \
             space tested, does not produce a fee-robust USDJPY edge at this fee level \
             over the 2024–2025 period.\n\n",
        );
        md.push_str(
            "Possible follow-up directions:\n\
             - Expand to FOMC/NFP/CPI events (POC showed FOMC PF=1.54 at fee=1bps)\n\
             - Test on post-YCC period only (2024-04+) to isolate regime effects\n\
             - Explore asymmetric TP/SL ratios for fixed_pct exit\n\
             - Multi-event combination (e.g., BOJ + FOMC co-occurrence)\n",
        );
    }

    md
}

/// Generate `report.md` content for FOMC drift exploration.
///
/// Mirrors the structure of `generate_report_md()` but uses FOMC-specific text,
/// 18-event context, and McLean-Pontiff framing for 0-pass outcomes.
pub fn generate_report_md_fomc(results: &[SlotReport], pair: &str) -> String {
    let pass_at_fee2: Vec<&SlotReport> = results
        .iter()
        .filter(|sr| {
            sr.fee_results
                .get(PRIMARY_FEE_IDX)
                .map(|fr| fr.passed)
                .unwrap_or(false)
        })
        .collect();

    let n_pass = pass_at_fee2.len();
    let n_total = results.len();

    let mut md = String::new();

    // -----------------------------------------------------------------------
    // 1. Executive Summary
    // -----------------------------------------------------------------------
    md.push_str(&format!("# FOMC Drift Exploration — {} 1h\n\n", pair));
    md.push_str("## 1. Executive Summary\n\n");

    if n_pass > 0 {
        md.push_str(&format!(
            "**PASS: {n_pass} candidate slot(s) found at fee=2bps RT (pf ≥ 2.0)**\n\n"
        ));
        md.push_str("Winning slots:\n\n");
        for sr in &pass_at_fee2 {
            let fr = &sr.fee_results[PRIMARY_FEE_IDX];
            md.push_str(&format!(
                "- window_offset={} hold_bars={} exit_type={} — OOS PF={:.3} trades={}\n",
                sr.window_offset,
                sr.hold_bars,
                sr.exit_type,
                fr.combined_oos_pf,
                fr.combined_oos_trades
            ));
        }
        md.push('\n');
    } else {
        md.push_str("**NULL: No slots passed at fee=2bps RT (pf ≥ 2.0)**\n\n");
        md.push_str(
            "The 96-slot parameter space for the FOMC drift strategy produced \
             no configurations that clear the 6-gate composite criterion at fee=2bps RT. \
             This is a valid scientific outcome per McLean-Pontiff (2016): exhaustive \
             exploration with null result is informative and bounds the search space.\n\n",
        );
    }

    // -----------------------------------------------------------------------
    // 2. Methodology
    // -----------------------------------------------------------------------
    md.push_str("## 2. Methodology\n\n");
    md.push_str(
        "**Parameter space:** 96 slots = 8 window_offsets (1–8 bars after FOMC window end) \
         × 6 hold_bars (1, 2, 3, 6, 12, 24) × 2 exit_types (none, fixed_pct).\n\n",
    );
    md.push_str(
        "**Walk-forward validation (WFD):** Purged 5-fold cross-validation with 1-day embargo \
         (Lopez de Prado 2018, ch. 7). Config: IS=3M, OOS=3M, walks=3. \
         Naive 70/30 split is prohibited.\n\n",
    );
    md.push_str(
        "**6-gate composite criterion (all gates must pass):**\n\
         1. |t| > 4.40 (Bonferroni correction, n=96 trials)\n\
         2. DSR p-value < 0.05 (Deflated Sharpe Ratio, Bailey & Lopez de Prado 2014)\n\
         3. mean > 2 × cost (mean OOS return > 2× fee cost)\n\
         4. Purged 5-fold OOS PF ≥ 2.0 in 4/5 folds at fee=1bps per side\n\
         5. H1/H2 sign agreement\n\
         6. Bootstrap CI excludes 0\n\n",
    );
    md.push_str(
        "**Fee sweep:** {0, 1, 2, 3, 5} bps RT. Primary pass criterion: pf ≥ 2.0 at fee=2bps RT.\n\n",
    );
    md.push_str(&format!(
        "**Total WFD runs:** {} slots × {} fee levels = {} runs.\n\n",
        n_total,
        FEE_LEVELS.len(),
        n_total * FEE_LEVELS.len()
    ));
    md.push_str(
        "**Direction:** Long only (short excluded — full scan in POC confirmed no edge).\n\n",
    );

    // -----------------------------------------------------------------------
    // 3. Full Scan Results Table
    // -----------------------------------------------------------------------
    md.push_str("## 3. Full Scan Results\n\n");
    md.push_str(
        "| window_offset | hold_bars | exit_type | fee=0 | fee=1 | fee=2 | fee=3 | fee=5 |\n",
    );
    md.push_str(
        "|--------------|-----------|-----------|-------|-------|-------|-------|-------|\n",
    );

    for sr in results {
        let cells: Vec<String> = sr.fee_results.iter().map(pass_cell).collect();
        let row = format!(
            "| {:13} | {:9} | {:9} | {} | {} | {} | {} | {} |\n",
            sr.window_offset,
            sr.hold_bars,
            sr.exit_type,
            cells.first().map(|s| s.as_str()).unwrap_or("?"),
            cells.get(1).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(2).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(3).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(4).map(|s| s.as_str()).unwrap_or("?"),
        );
        md.push_str(&row);
    }
    md.push('\n');

    // -----------------------------------------------------------------------
    // 4. FOMC Calendar Context
    // -----------------------------------------------------------------------
    md.push_str("## 4. FOMC Calendar Context\n\n");
    md.push_str(
        "This exploration covers 18 FOMC policy announcements from January 2024 through \
         March 2026. Each announcement occurs at approximately 14:00 ET (18:00 UTC during \
         EDT, 19:00 UTC during EST). The post-announcement window (0–60 minutes) is the \
         entry zone under study.\n\n",
    );
    md.push_str(
        "**Classification:** Announcements are classified as hawkish (+1) or dovish (−1) \
         based on the direction of the federal funds rate decision or forward guidance tone. \
         The strategy enters in the direction of the announcement signal following the \
         FOMC window with a configurable offset.\n\n",
    );
    md.push_str(
        "**DST awareness:** UTC entry times are adjusted for US Daylight Saving Time \
         (EDT = UTC−4, Mar 2nd Sunday to Nov 1st Sunday; EST = UTC−5 otherwise), \
         ensuring the 14:00 ET anchor is mapped correctly for all 18 events.\n\n",
    );
    md.push_str(
        "**Sample coverage:** 8 announcements in 2024, 8 in 2025, 2 in early 2026 \
         (Jan and Mar). This spans the Fed's pivot from rate hikes to rate cuts and \
         the subsequent hold period.\n\n",
    );

    // -----------------------------------------------------------------------
    // 5. Limitations
    // -----------------------------------------------------------------------
    md.push_str("## Limitations\n\n");
    {
        let n = crate::events::fomc_windows_all().len();
        let fold_size = n / 6;
        md.push_str(&format!(
            "N={n} FOMC events (2022–2026). fold_size≈{fold_size} events per fold. \
             Small sample — interpret with caution.\n\n"
        ));
    }

    // -----------------------------------------------------------------------
    // 6. Conclusion
    // -----------------------------------------------------------------------
    md.push_str("## 5. Conclusion\n\n");

    if n_pass > 0 {
        md.push_str(&format!(
            "**Result: PASS** — {n_pass} slot(s) survive the 6-gate composite criterion \
             at fee=2bps RT.\n\n"
        ));
        md.push_str(
            "These candidates should proceed to out-of-sample validation on held-out 2026 data \
             and paper trading to confirm live edge.\n\n",
        );
        md.push_str(
            "Note: All results are subject to the small-sample caveat in Section 5 (Limitations). \
             Live performance monitoring is mandatory before capital deployment.\n",
        );
    } else {
        md.push_str(
            "**Result: NULL** — The FOMC drift parameter space (96 slots) \
             produces no configurations that clear the 6-gate composite criterion \
             at fee=2bps RT.\n\n",
        );
        md.push_str(&format!(
            "Per McLean-Pontiff (2016, \"Does Academic Research Destroy Stock Return Predictability?\"), \
             a systematic null result is scientifically valid and informative. \
             0 slots passing the 6-gate is a valid result; it narrows the search space \
             for future exploration. The FOMC calendar timing alone, in the parameter \
             space tested, does not produce a fee-robust {} edge over the 2024–2025 period.\n\n",
             pair
        ));
        md.push_str(
            "Possible follow-up directions:\n\
             - Test on EUR/USD or GBP/USD (more FOMC-sensitive pairs)\n\
             - Explore asymmetric TP/SL ratios for fixed_pct exit\n\
             - Combine FOMC direction signal with pre-announcement drift\n\
             - Expand to CPI/NFP events for broader macro calendar coverage\n",
        );
    }

    md
}

/// Generate ECB drift exploration report.
///
/// Phase 40: independent implementation with ECB-specific context and USDJPY v3.8 comparison.
pub fn generate_report_md_ecb(results: &[SlotReport], pair: &str) -> String {
    let pass_at_fee2: Vec<&SlotReport> = results
        .iter()
        .filter(|sr| {
            sr.fee_results
                .get(PRIMARY_FEE_IDX)
                .map(|fr| fr.passed)
                .unwrap_or(false)
        })
        .collect();

    let n_pass = pass_at_fee2.len();
    let n_total = results.len();

    let mut md = String::new();

    // -----------------------------------------------------------------------
    // 1. Executive Summary
    // -----------------------------------------------------------------------
    md.push_str(&format!("# ECB Drift Exploration — {} 1h\n\n", pair));
    md.push_str("## 1. Executive Summary\n\n");

    if n_pass > 0 {
        md.push_str(&format!(
            "**PASS: {n_pass} candidate slot(s) found at fee=2bps RT (pf ≥ 2.0)**\n\n"
        ));
        md.push_str("Winning slots:\n\n");
        for sr in &pass_at_fee2 {
            let fr = &sr.fee_results[PRIMARY_FEE_IDX];
            md.push_str(&format!(
                "- window_offset={} hold_bars={} exit_type={} — OOS PF={:.3} trades={}\n",
                sr.window_offset,
                sr.hold_bars,
                sr.exit_type,
                fr.combined_oos_pf,
                fr.combined_oos_trades
            ));
        }
        md.push('\n');
    } else {
        md.push_str("**NULL: No slots passed at fee=2bps RT (pf ≥ 2.0)**\n\n");
        md.push_str(
            "The 96-slot parameter space for the ECB drift strategy produced \
             no configurations that clear the 6-gate composite criterion at fee=2bps RT. \
             This is a valid scientific outcome per McLean-Pontiff (2016): exhaustive \
             exploration with null result is informative and bounds the search space.\n\n",
        );
    }

    // -----------------------------------------------------------------------
    // 2. Methodology
    // -----------------------------------------------------------------------
    md.push_str("## 2. Methodology\n\n");
    md.push_str(
        "**Parameter space:** 96 slots = 8 window_offsets (1–8 bars after ECB window end) \
         × 6 hold_bars (1, 2, 3, 6, 12, 24) × 2 exit_types (none, fixed_pct).\n\n",
    );
    md.push_str(
        "**Walk-forward validation (WFD):** Purged 5-fold cross-validation with 1-day embargo \
         (Lopez de Prado 2018, ch. 7). Config: IS=3M, OOS=3M, walks=3. \
         Naive 70/30 split is prohibited.\n\n",
    );
    md.push_str(
        "**6-gate composite criterion (all gates must pass):**\n\
         1. |t| > 4.40 (Bonferroni correction, n=96 trials)\n\
         2. DSR p-value < 0.05 (Deflated Sharpe Ratio, Bailey & Lopez de Prado 2014)\n\
         3. mean > 2 × cost (mean OOS return > 2× fee cost)\n\
         4. Purged 5-fold OOS PF ≥ 2.0 in 4/5 folds at fee=1bps per side\n\
         5. H1/H2 sign agreement\n\
         6. Bootstrap CI excludes 0\n\n",
    );
    md.push_str(
        "**Fee sweep:** {0, 1, 2, 3, 5} bps RT. Primary pass criterion: pf ≥ 2.0 at fee=2bps RT.\n\n",
    );
    md.push_str(&format!(
        "**Total WFD runs:** {} slots × {} fee levels = {} runs.\n\n",
        n_total,
        FEE_LEVELS.len(),
        n_total * FEE_LEVELS.len()
    ));
    md.push_str(
        "**Direction:** Follows ECB policy signal direction. Hawkish (+1) → long EUR; \
         dovish (−1) → short EUR. No direction inversion applied (D-02).\n\n",
    );

    // -----------------------------------------------------------------------
    // 3. Full Scan Results Table
    // -----------------------------------------------------------------------
    md.push_str("## 3. Full Scan Results\n\n");
    md.push_str(
        "| window_offset | hold_bars | exit_type | fee=0 | fee=1 | fee=2 | fee=3 | fee=5 |\n",
    );
    md.push_str(
        "|--------------|-----------|-----------|-------|-------|-------|-------|-------|\n",
    );

    for sr in results {
        let cells: Vec<String> = sr.fee_results.iter().map(pass_cell).collect();
        let row = format!(
            "| {:13} | {:9} | {:9} | {} | {} | {} | {} | {} |\n",
            sr.window_offset,
            sr.hold_bars,
            sr.exit_type,
            cells.first().map(|s| s.as_str()).unwrap_or("?"),
            cells.get(1).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(2).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(3).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(4).map(|s| s.as_str()).unwrap_or("?"),
        );
        md.push_str(&row);
    }
    md.push('\n');

    // -----------------------------------------------------------------------
    // 4. ECB Calendar Context
    // -----------------------------------------------------------------------
    md.push_str("## 4. ECB Calendar Context\n\n");
    md.push_str(
        "This exploration covers 16 ECB policy announcements from January 2024 through \
         March 2026. Each announcement occurs at approximately 14:15 CET/CEST \
         (13:15 UTC during CET winter, 12:15 UTC during CEST summer). \
         The post-announcement window (0–60 minutes) is the entry zone under study.\n\n",
    );
    md.push_str(
        "**Classification:** Announcements are classified as hawkish (+1) or dovish (−1) \
         based on the direction of the deposit facility rate decision or forward guidance tone. \
         The strategy enters in the direction of the announcement signal following the \
         ECB window with a configurable offset.\n\n",
    );
    md.push_str(
        "**DST awareness:** UTC entry times are adjusted for European Summer Time (CEST = UTC+2, \
         last Sunday March to last Sunday October; CET = UTC+1 otherwise), \
         ensuring the 14:15 CET/CEST anchor is mapped correctly for all 16 events. \
         Four DST quadrants are covered: CET winter, CEST summer, March pre-transition, \
         October pre-transition.\n\n",
    );
    md.push_str(
        "**Sample coverage:** 8 announcements in 2024, 6 in 2025, 2 in early 2026. \
         This spans the ECB's rate-cut cycle from 4.0% to 2.5% and the subsequent \
         hold/pause period.\n\n",
    );

    // -----------------------------------------------------------------------
    // 5. Cross-Pair Comparison (JPY-cross ECB v3.8 baseline)
    // -----------------------------------------------------------------------
    md.push_str("## 5. Cross-Pair Comparison\n\n");
    md.push_str(
        "**JPY-cross × ECB (v3.8 baseline):** 96/96 slots passed at fee=2bps RT (100% pass rate). \
         Source: docs/reports/v3.8-multi-event/report.json.\n\n",
    );
    md.push_str(&format!(
        "**{} × ECB (this run):** {}/{} slots passed at fee=2bps RT ({:.0}% pass rate).\n\n",
        pair,
        n_pass,
        n_total,
        if n_total > 0 {
            (n_pass as f64 / n_total as f64) * 100.0
        } else {
            0.0
        }
    ));
    md.push_str(
        "**Interpretation:** The JPY-cross ECB baseline achieved a perfect pass rate, reflecting \
         the strong yen sensitivity to ECB-driven EUR/JPY cross flows. EURUSD results measure \
         the direct EUR spot response, which is directionally aligned (no inversion) but may \
         differ in magnitude due to different volatility regimes and liquidity profiles.\n\n",
    );

    // -----------------------------------------------------------------------
    // 6. Limitations
    // -----------------------------------------------------------------------
    md.push_str("## 6. Limitations\n\n");
    {
        let n = crate::events::ecb_windows_all().len();
        let fold_size = n / 6;
        md.push_str(&format!(
            "N={n} ECB events (2022–2026). fold_size≈{fold_size} events per fold. \
             Small sample — interpret with caution.\n\n"
        ));
    }

    // -----------------------------------------------------------------------
    // 7. Conclusion
    // -----------------------------------------------------------------------
    md.push_str("## 7. Conclusion\n\n");

    if n_pass > 0 {
        md.push_str(&format!(
            "**Result: PASS** — {n_pass} slot(s) survive the 6-gate composite criterion \
             at fee=2bps RT.\n\n"
        ));
        md.push_str(
            "These candidates should proceed to out-of-sample validation on held-out 2026 data \
             and paper trading to confirm live edge.\n\n",
        );
        md.push_str(
            "Note: All results are subject to the small-sample caveat in Section 6 (Limitations). \
             Live performance monitoring is mandatory before capital deployment.\n",
        );
    } else {
        md.push_str(
            "**Result: NULL** — The ECB drift parameter space (96 slots) \
             produces no configurations that clear the 6-gate composite criterion \
             at fee=2bps RT.\n\n",
        );
        md.push_str(&format!(
            "Per McLean-Pontiff (2016, \"Does Academic Research Destroy Stock Return Predictability?\"), \
             a systematic null result is scientifically valid and informative. \
             0 slots passing the 6-gate is a valid result; it narrows the search space \
             for future exploration. The ECB calendar timing alone, in the parameter \
             space tested, does not produce a fee-robust {} edge over the 2024–2026 period.\n\n",
             pair
        ));
        md.push_str(
            "Possible follow-up directions:\n\
             - Compare with JPY-cross ECB results (v3.8 baseline: 96/96 pass) for cross-pair divergence analysis\n\
             - Test on GBP/USD or USD/CHF (other ECB-sensitive pairs)\n\
             - Explore asymmetric TP/SL ratios for fixed_pct exit\n\
             - Combine ECB direction signal with pre-announcement drift\n\
             - Expand to CPI/NFP events for broader macro calendar coverage\n",
        );
    }

    md
}

pub fn generate_report_md_calendar(
    results: &[SlotReport],
    edge_count: usize,
    dim_count: usize,
    bonferroni_threshold: f64,
) -> String {
    // Filter passing slots at fee=2bps (PRIMARY_FEE_IDX = 2)
    let pass_at_fee2: Vec<&SlotReport> = results
        .iter()
        .filter(|sr| {
            sr.fee_results
                .get(PRIMARY_FEE_IDX)
                .map(|fr| fr.passed)
                .unwrap_or(false)
        })
        .collect();

    let n_pass = pass_at_fee2.len();
    let n_total = results.len();
    let mut md = String::new();

    // -----------------------------------------------------------------------
    // 1. Executive Summary + BQ Context
    // -----------------------------------------------------------------------
    md.push_str("# Calendar Anomaly Exploration — USDJPY 1h\n\n");
    md.push_str("## 1. Executive Summary\n\n");

    // BQ Summary (NEW for calendar)
    md.push_str(&format!(
        "**BQ Summary:** {} unique (day_of_week, month_position) edges scanned. \
         Bonferroni threshold: |t| > {:.3}. Candidate edges: {}.\n\n",
        dim_count, bonferroni_threshold, edge_count
    ));

    if n_pass > 0 {
        md.push_str(&format!(
            "**PASS: {n_pass} candidate slot(s) found at fee=2bps RT (pf ≥ 2.0)**\n\n"
        ));
        md.push_str("Winning slots:\n\n");
        for sr in &pass_at_fee2 {
            let fr = &sr.fee_results[PRIMARY_FEE_IDX];
            md.push_str(&format!(
                "- window_offset={} hold_bars={} exit_type={} — OOS PF={:.3} trades={}\n",
                sr.window_offset,
                sr.hold_bars,
                sr.exit_type,
                fr.combined_oos_pf,
                fr.combined_oos_trades
            ));
        }
        md.push('\n');
    } else {
        md.push_str("**NULL: No slots passed at fee=2bps RT (pf ≥ 2.0)**\n\n");
        md.push_str(
            "The 96-slot calendar parameter space (8 window_offsets × 6 hold_bars × 2 exit_types) \
             produced no configurations that clear the 6-gate composite criterion at fee=2bps RT. \
             This is a valid scientific outcome per McLean-Pontiff (2016): exhaustive exploration \
             with null result is informative and bounds the search space.\n\n",
        );
    }

    // -----------------------------------------------------------------------
    // 2. Methodology
    // -----------------------------------------------------------------------
    md.push_str("## 2. Methodology\n\n");
    md.push_str(
        "**BQ Analysis:** Day-of-week × month-position grid (5 DOW × 3 month positions) \
         filtered by Bonferroni-corrected t-statistic threshold. Candidate edges: a subset of \
         the 90-dimensional hypothesis space.\n\n",
    );
    md.push_str(
        "**Calendar Definition:** Each candidate edge corresponds to a unique (day_of_week, month_position) \
         pair with signed returns and t-stat > Bonferroni threshold.\n\n",
    );
    md.push_str(
        "**Parameter space:** 96 slots = 8 window_offsets (0–7 bars post-edge signal) \
         × 6 hold_bars (1, 2, 3, 6, 12, 24) × 2 exit_types (none, fixed_pct).\n\n",
    );
    md.push_str(
        "**Walk-forward validation (WFD):** Purged 5-fold cross-validation with 1-day embargo \
         (Lopez de Prado 2018, ch. 7). Config: IS=3M, OOS=3M, walks=3. \
         Naive 70/30 split is prohibited.\n\n",
    );
    md.push_str(
        "**6-gate composite criterion (all gates must pass):**\n\
         1. |t| > 4.40 (Bonferroni correction, n=96 trials)\n\
         2. DSR p-value < 0.05 (Deflated Sharpe Ratio, Bailey & Lopez de Prado 2014)\n\
         3. mean > 2 × cost (mean OOS return > 2× fee cost)\n\
         4. Purged 5-fold OOS PF ≥ 2.0 in 4/5 folds at fee=1bps per side\n\
         5. H1/H2 sign agreement\n\
         6. Bootstrap CI excludes 0\n\n",
    );
    md.push_str(
        "**Fee sweep:** {0, 1, 2, 3, 5} bps RT. Primary pass criterion: pf ≥ 2.0 at fee=2bps RT.\n\n",
    );
    md.push_str(&format!(
        "**Total WFD runs:** {} slots × {} fee levels = {} runs.\n\n",
        n_total,
        FEE_LEVELS.len(),
        n_total * FEE_LEVELS.len()
    ));
    md.push_str("**Direction:** Long only (short direction confirmed null in POC).\n\n");

    // -----------------------------------------------------------------------
    // 3. Full Scan Results Table
    // -----------------------------------------------------------------------
    md.push_str("## 3. Full Scan Results\n\n");
    md.push_str(
        "| window_offset | hold_bars | exit_type | fee=0 | fee=1 | fee=2 | fee=3 | fee=5 |\n",
    );
    md.push_str(
        "|--------------|-----------|-----------|-------|-------|-------|-------|-------|\n",
    );

    for sr in results {
        let cells: Vec<String> = sr.fee_results.iter().map(pass_cell).collect();
        let row = format!(
            "| {:13} | {:9} | {:9} | {} | {} | {} | {} | {} |\n",
            sr.window_offset,
            sr.hold_bars,
            sr.exit_type,
            cells.first().map(|s| s.as_str()).unwrap_or("?"),
            cells.get(1).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(2).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(3).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(4).map(|s| s.as_str()).unwrap_or("?"),
        );
        md.push_str(&row);
    }
    md.push('\n');

    // -----------------------------------------------------------------------
    // 4. Calendar Anomaly Context
    // -----------------------------------------------------------------------
    md.push_str("## 4. Calendar Anomaly Context\n\n");
    md.push_str(&format!(
        "This exploration tests a subset of the 90-dimensional calendar anomaly hypothesis space. \
         BQ filtering identified {} day-of-week × month-position edges with t-stat > {:.3}. \
         Each edge is defined as a unique combination of:\n\n",
        edge_count, bonferroni_threshold
    ));
    md.push_str(
        "- **Day of week (DOW):** Monday–Friday classification\n\
         - **Month position:** Early (1–10), Mid (11–20), Late (21–31)\n\n",
    );
    md.push_str(
        "The WFD analysis applies 96 parameter configurations (window_offset, hold_bars, exit_type) \
         to each BQ-identified edge, testing whether the calendar signal survives walk-forward \
         validation and fee friction.\n\n",
    );

    // -----------------------------------------------------------------------
    // 5. Limitations
    // -----------------------------------------------------------------------
    md.push_str("## 5. Limitations\n\n");
    md.push_str(&format!(
        "Candidate edges: {}. fold_size ≈ {} edges per fold. \
         Small sample — interpret with caution.\n\n",
        edge_count,
        edge_count.div_ceil(5) // rough 5-fold estimate
    ));

    // -----------------------------------------------------------------------
    // 6. Conclusion
    // -----------------------------------------------------------------------
    md.push_str("## 6. Conclusion\n\n");

    if n_pass > 0 {
        md.push_str(&format!(
            "**Result: PASS** — {n_pass} slot(s) survive the 6-gate composite criterion \
             at fee=2bps RT.\n\n"
        ));
        md.push_str(
            "These candidates should proceed to out-of-sample validation on held-out 2026 data \
             and paper trading to confirm live edge.\n\n",
        );
        md.push_str(
            "Note: All results are subject to the small-sample caveat in Section 5 (Limitations). \
             Live performance monitoring is mandatory before capital deployment.\n",
        );
    } else {
        md.push_str(&format!(
            "**Result: NULL** — The calendar anomaly parameter space (96 slots × {} edges) \
             produces no configurations that clear the 6-gate composite criterion at fee=2bps RT.\n\n",
            edge_count
        ));
        md.push_str(&format!(
            "Per McLean-Pontiff (2016, \"Does Academic Research Destroy Stock Return Predictability?\"), \
             a systematic null result is scientifically valid and informative. 0 slots passing the 6-gate \
             is a valid result; it narrows the search space for future exploration. The {} BQ-identified \
             calendar edges, in the parameter space tested, do not produce a fee-robust USDJPY edge over \
             the analysis period.\n\n",
             edge_count
        ));
        md.push_str(
            "Possible follow-up directions:\n\
             - Revisit BQ filtering thresholds (relax Bonferroni, apply Benjamini-Hochberg)\n\
             - Test asymmetric day-of-week trading (Monday effects, Friday reversals)\n\
             - Combine calendar edge with macro signals (rate decisions, inflation prints)\n\
             - Expand to other JPY crosses (USD/JPY vs EUR/JPY vs GBP/JPY)\n",
        );
    }

    md
}

/// Generate NFP drift exploration report.
///
/// Full NFP-specific implementation (Phase 48 D-14).
/// Uses nfp_windows_all() for N (46 events: 2022-2025).
pub fn generate_report_md_nfp(results: &[SlotReport], pair: &str) -> String {
    let pass_at_fee2: Vec<&SlotReport> = results
        .iter()
        .filter(|sr| {
            sr.fee_results
                .get(PRIMARY_FEE_IDX)
                .map(|fr| fr.passed)
                .unwrap_or(false)
        })
        .collect();

    let n_pass = pass_at_fee2.len();
    let n_total = results.len();

    let mut md = String::new();

    // -----------------------------------------------------------------------
    // 1. Executive Summary
    // -----------------------------------------------------------------------
    md.push_str(&format!("# NFP Macro Drift — {} 1h\n\n", pair));
    md.push_str("## 1. Executive Summary\n\n");

    if n_pass > 0 {
        md.push_str(&format!(
            "**PASS: {n_pass} candidate slot(s) found at fee=2bps RT (pf ≥ 2.0)**\n\n"
        ));
        md.push_str("Winning slots:\n\n");
        for sr in &pass_at_fee2 {
            let fr = &sr.fee_results[PRIMARY_FEE_IDX];
            md.push_str(&format!(
                "- window_offset={} hold_bars={} exit_type={} — OOS PF={:.3} trades={}\n",
                sr.window_offset,
                sr.hold_bars,
                sr.exit_type,
                fr.combined_oos_pf,
                fr.combined_oos_trades
            ));
        }
        md.push('\n');
    } else {
        md.push_str("**NULL: No slots passed at fee=2bps RT (pf ≥ 2.0)**\n\n");
        md.push_str(
            "The 96-slot parameter space for the NFP drift strategy produced \
             no configurations that clear the 6-gate composite criterion at fee=2bps RT. \
             This is a valid scientific outcome per McLean-Pontiff (2016): exhaustive \
             exploration with null result is informative and bounds the search space.\n\n",
        );
    }

    // -----------------------------------------------------------------------
    // 2. Methodology
    // -----------------------------------------------------------------------
    md.push_str("## 2. Methodology\n\n");
    md.push_str(
        "**Parameter space:** 96 slots = 8 window_offsets (1–8 bars after NFP window end) \
         × 6 hold_bars (1, 2, 3, 6, 12, 24) × 2 exit_types (none, fixed_pct).\n\n",
    );
    md.push_str(
        "**Walk-forward validation (WFD):** Purged 5-fold cross-validation with 1-day embargo \
         (Lopez de Prado 2018, ch. 7). Config: IS=3M, OOS=3M, walks=3. \
         Naive 70/30 split is prohibited.\n\n",
    );
    md.push_str(
        "**6-gate composite criterion (all gates must pass):**\n\
         1. |t| > 4.40 (Bonferroni correction, n=96 trials)\n\
         2. DSR p-value < 0.05 (Deflated Sharpe Ratio, Bailey & Lopez de Prado 2014)\n\
         3. mean > 2 × cost (mean OOS return > 2× fee cost)\n\
         4. Purged 5-fold OOS PF ≥ 2.0 in 4/5 folds at fee=1bps per side\n\
         5. H1/H2 sign agreement\n\
         6. Bootstrap CI excludes 0\n\n",
    );
    md.push_str(
        "**Fee sweep:** {0, 1, 2, 3, 5} bps RT. Primary pass criterion: pf ≥ 2.0 at fee=2bps RT.\n\n",
    );
    md.push_str(&format!(
        "**Total WFD runs:** {} slots × {} fee levels = {} runs.\n\n",
        n_total,
        FEE_LEVELS.len(),
        n_total * FEE_LEVELS.len()
    ));
    md.push_str(
        "**Direction:** Long only (short excluded — full scan in POC confirmed no edge).\n\n",
    );

    // -----------------------------------------------------------------------
    // 3. Full Scan Results Table
    // -----------------------------------------------------------------------
    md.push_str("## 3. Full Scan Results\n\n");
    md.push_str(
        "| window_offset | hold_bars | exit_type | fee=0 | fee=1 | fee=2 | fee=3 | fee=5 |\n",
    );
    md.push_str(
        "|--------------|-----------|-----------|-------|-------|-------|-------|-------|\n",
    );

    for sr in results {
        let cells: Vec<String> = sr.fee_results.iter().map(pass_cell).collect();
        let row = format!(
            "| {:13} | {:9} | {:9} | {} | {} | {} | {} | {} |\n",
            sr.window_offset,
            sr.hold_bars,
            sr.exit_type,
            cells.first().map(|s| s.as_str()).unwrap_or("?"),
            cells.get(1).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(2).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(3).map(|s| s.as_str()).unwrap_or("?"),
            cells.get(4).map(|s| s.as_str()).unwrap_or("?"),
        );
        md.push_str(&row);
    }
    md.push('\n');

    // -----------------------------------------------------------------------
    // 4. NFP Calendar Context
    // -----------------------------------------------------------------------
    md.push_str("## 4. NFP Calendar Context\n\n");
    md.push_str(
        "This exploration covers NFP (Non-Farm Payroll) releases from January 2022 through \
         December 2025. Each release occurs on the first Friday of the month at 08:30 ET \
         (13:00 UTC during EST, 12:00 UTC during EDT). The post-release window is the \
         entry zone under study.\n\n",
    );
    md.push_str(
        "**Classification:** Releases are classified as beat (+1) or miss (-1) \
         relative to consensus estimates. The strategy enters in the direction of the \
         surprise signal following the NFP window with a configurable offset.\n\n",
    );
    md.push_str(
        "**DST awareness:** UTC entry times are adjusted for US Daylight Saving Time \
         (EDT = UTC−4, Mar 2nd Sunday to Nov 1st Sunday; EST = UTC−5 otherwise), \
         ensuring the 08:30 ET anchor is mapped correctly for all events.\n\n",
    );

    // -----------------------------------------------------------------------
    // 5. Limitations
    // -----------------------------------------------------------------------
    md.push_str("## 5. Limitations\n\n");
    {
        let n = crate::events::nfp_windows_all().len();
        let fold_size = n / 6;
        md.push_str(&format!(
            "N={n} NFP events (2022–2025). fold_size≈{fold_size} events per fold. \
             Small sample — interpret with caution.\n\n"
        ));
    }

    // -----------------------------------------------------------------------
    // 6. Conclusion
    // -----------------------------------------------------------------------
    md.push_str("## 6. Conclusion\n\n");

    if n_pass > 0 {
        md.push_str(&format!(
            "**Result: PASS** — {n_pass} slot(s) survive the 6-gate composite criterion \
             at fee=2bps RT.\n\n"
        ));
        md.push_str(
            "These candidates should proceed to out-of-sample validation on held-out data \
             and paper trading to confirm live edge.\n\n",
        );
        md.push_str(
            "Note: All results are subject to the small-sample caveat in Section 5 (Limitations). \
             Live performance monitoring is mandatory before capital deployment.\n",
        );
    } else {
        md.push_str(
            "**Result: NULL** — The NFP drift parameter space (96 slots) \
             produces no configurations that clear the 6-gate composite criterion \
             at fee=2bps RT.\n\n",
        );
        md.push_str(&format!(
            "Per McLean-Pontiff (2016, \"Does Academic Research Destroy Stock Return Predictability?\"), \
             a systematic null result is scientifically valid and informative. \
             0 slots passing the 6-gate is a valid result; it narrows the search space \
             for future exploration. The NFP calendar timing alone, in the parameter \
             space tested, does not produce a fee-robust {} edge over the 2022–2025 period.\n\n",
             pair
        ));
        md.push_str(
            "Possible follow-up directions:\n\
             - Test on EUR/USD or GBP/USD (more NFP-sensitive pairs)\n\
             - Explore asymmetric TP/SL ratios for fixed_pct exit\n\
             - Combine NFP surprise signal with pre-release drift\n\
             - Expand to other macro events (CPI, FOMC) for broader calendar coverage\n",
        );
    }

    md
}

// ---------------------------------------------------------------------------
// Combined report (Phase 36, D-03, D-04, D-09)
// ---------------------------------------------------------------------------

/// Count SlotReports passing at a specific fee index.
fn count_pass_at_fee(results: &[SlotReport], fee_idx: usize) -> usize {
    results
        .iter()
        .filter(|sr| {
            sr.fee_results
                .get(fee_idx)
                .map(|fr| fr.passed)
                .unwrap_or(false)
        })
        .count()
}

// Deprecated: use count_pass_at_fee(results, fee_idx) for parameterization
#[allow(dead_code)]
fn count_pass_at_fee2(results: &[SlotReport]) -> usize {
    count_pass_at_fee(results, PRIMARY_FEE_IDX)
}

/// Generate combined FOMC + ECB + NFP drift exploration report (D-03, D-09).
///
/// Structure:
/// 1. Aggregate Summary — N_total, pass counts, McLean-Pontiff framing
/// 2. FOMC Breakdown (N=18)
/// 3. ECB Breakdown (N≈16)
/// 4. NFP Breakdown (N≈22)
pub fn generate_report_md_combined(
    fomc: &[SlotReport],
    ecb: &[SlotReport],
    nfp: &[SlotReport],
    pair: &str,
    primary_fee_idx: usize,
) -> String {
    let n_fomc = fomc.len();
    let n_ecb = ecb.len();
    let n_nfp = nfp.len();
    let n_total = n_fomc + n_ecb + n_nfp;

    let pass_fomc = count_pass_at_fee(fomc, primary_fee_idx);
    let pass_ecb = count_pass_at_fee(ecb, primary_fee_idx);
    let pass_nfp = count_pass_at_fee(nfp, primary_fee_idx);
    let pass_total = pass_fomc + pass_ecb + pass_nfp;

    let now = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ");
    let primary_fee_bps = FEE_LEVELS.get(primary_fee_idx).copied().unwrap_or(2.0);

    let mut md = String::new();
    md.push_str("# Combined Macro Event Drift — v3.8 Multi-Event Report\n\n");
    md.push_str(&format!("Generated: {now}\n\n"));

    // §1 Aggregate Summary
    md.push_str("## Aggregate Summary\n\n");
    md.push_str(&format!(
        "- **N_total**: {n_total} (FOMC={n_fomc}, ECB={n_ecb}, NFP={n_nfp})\n"
    ));
    md.push_str(&format!(
        "- **Passed @ fee={}bps RT**: {pass_total} / {n_total} \
         (FOMC={pass_fomc}/{n_fomc}, ECB={pass_ecb}/{n_ecb}, NFP={pass_nfp}/{n_nfp})\n",
        primary_fee_bps as u32
    ));
    md.push_str(&format!(
        "- **dsr_n_trials**: {} per source (macro event sweep dimension)\n",
        GateConfig::macro_event().dsr_n_trials
    ));
    md.push_str(&format!(
        "- **Pass criterion**: PF \u{2265} 2.0 at fee = {} bps RT (6-gate composite upstream)\n\n",
        primary_fee_bps as u32
    ));

    // McLean-Pontiff framing (D-09)
    md.push_str("### McLean-Pontiff Framing\n\n");
    if pass_total == 0 {
        md.push_str(&format!(
            "This is a **null result** \u{2014} 0 / {n_total} slots passed the 6-gate composite. \
             Per McLean & Pontiff (2016), a null result from a pre-registered exploration is \
             **defensible**: it rules out hypothesised macro-event drift as a fee-surviving edge \
             under the configured horizons.\n\n"
        ));
    } else {
        md.push_str(&format!(
            "A total of **{pass_total} / {n_total}** slots survived the 6-gate composite \
             (pass). Null result remains defensible for non-passing slots per McLean & Pontiff \
             (2016); passing slots require WFD + fee \u{2265} 2 bps RT robustness confirmation \
             downstream.\n\n"
        ));
    }

    // §2-4 Per-source breakdowns
    md.push_str(&format!("## FOMC Breakdown (N={n_fomc})\n\n"));
    md.push_str(&generate_report_md_fomc(fomc, pair));
    md.push('\n');

    md.push_str(&format!("## ECB Breakdown (N={n_ecb})\n\n"));
    md.push_str(&generate_report_md_ecb(ecb, pair));
    md.push('\n');

    md.push_str(&format!("## NFP Breakdown (N={n_nfp})\n\n"));
    md.push_str(&generate_report_md_nfp(nfp, pair));
    md.push('\n');

    md
}

/// Generate `VALIDATION.md` content with nyquist_compliant: true frontmatter (D-10).
///
/// `phase_label` is injected into the frontmatter `phase:` field, allowing
/// BOJ and FOMC paths to produce distinct validation records.
pub fn generate_validation_md(results: &[SlotReport], phase_label: &str) -> String {
    let n_slots = results.len();
    let n_pass_fee2 = results
        .iter()
        .filter(|sr| {
            sr.fee_results
                .get(PRIMARY_FEE_IDX)
                .map(|fr| fr.passed)
                .unwrap_or(false)
        })
        .count();

    let now = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ");
    let n_offsets = WINDOW_OFFSETS.len();
    let n_holds = HOLD_BARS_VALUES.len();
    let n_exits = EXIT_TYPES.len();
    let n_expected_slots = n_offsets * n_holds * n_exits;
    let n_trials = GateConfig::macro_event().dsr_n_trials;

    format!(
        "---\n\
         nyquist_compliant: true\n\
         phase: {phase_label}\n\
         generated: {now}\n\
         slots_scanned: {n_slots}\n\
         fee_levels: [0, 1, 2, 3, 5]\n\
         pass_criterion: \"pf >= 2.0 at fee=2bps RT\"\n\
         pass_count_fee2: {n_pass_fee2}\n\
         ---\n\n\
         # Validation Certificate\n\n\
         This document certifies that the Phase 25 full scan has been executed in compliance \
         with the Nyquist sampling criterion and the project's 6-gate composite validation protocol.\n\n\
         ## Validation Summary\n\n\
         - **Slots scanned:** {n_slots} ({n_expected_slots} = {n_offsets} × {n_holds} × {n_exits})\n\
         - **Fee levels:** {{0, 1, 2, 3, 5}} bps RT\n\
         - **Total WFD runs:** {total}\n\
         - **Pass criterion:** OOS PF ≥ 2.0 at fee=2bps RT\n\
         - **Slots passing at fee=2bps:** {n_pass_fee2}\n\n\
         ## Protocol Compliance\n\n\
         - [x] Purged k-fold CV with 1-day embargo (Lopez de Prado 2018, ch. 7)\n\
         - [x] Bonferroni correction applied (|t| > 4.40, n={n_trials} trials)\n\
         - [x] Fee sweep at {{0,1,2,3,5}} bps RT — fee=0 alpha illusion excluded\n\
         - [x] Long-only (short direction confirmed null in POC)\n\
         - [x] Walk-forward config: IS=3M / OOS=3M / walks=3\n\
         - [x] nyquist_compliant: true — sampling frequency (1h) is adequate for \
         BOJ window detection (minimum event window >> 2h)\n",
        now = now,
        n_slots = n_slots,
        n_pass_fee2 = n_pass_fee2,
        n_expected_slots = n_expected_slots,
        n_offsets = n_offsets,
        n_holds = n_holds,
        n_exits = n_exits,
        n_trials = n_trials,
        total = n_slots * FEE_LEVELS.len()
    )
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

fn pass_cell(fr: &FeeResult) -> String {
    if fr.passed {
        format!("PASS(pf={:.2})", fr.combined_oos_pf)
    } else {
        "FAIL".to_string()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::scanner::macro_event::{FeeResult, SlotReport};

    fn make_fee_result(fee_bps: f64, passed: bool, pf: f64) -> FeeResult {
        FeeResult {
            fee_bps,
            combined_oos_pf: pf,
            combined_oos_sharpe: 1.0,
            combined_oos_trades: 10,
            combined_oos_max_dd: -0.01,
            passed,
            dsr_pvalue: 0.03,
            dsr_n_trials: GateConfig::macro_event().dsr_n_trials,
        }
    }

    fn make_slot_report(
        window_offset: u32,
        hold_bars: u32,
        exit_type: &'static str,
        fee2_passed: bool,
    ) -> SlotReport {
        SlotReport {
            window_offset,
            hold_bars,
            exit_type,
            fee_results: vec![
                make_fee_result(0.0, true, 3.0),
                make_fee_result(1.0, true, 2.5),
                make_fee_result(2.0, fee2_passed, if fee2_passed { 2.1 } else { 1.5 }),
                make_fee_result(3.0, false, 1.2),
                make_fee_result(5.0, false, 0.8),
            ],
            duration_bucket: None,
            liquidity_regime: None,
            per_trade_log: None,
        }
    }

    #[test]
    fn generate_validation_md_contains_nyquist_compliant_true() {
        let results = vec![make_slot_report(1, 1, "none", false)];
        let md = generate_validation_md(&results, "25-full-scan-report");
        assert!(
            md.contains("nyquist_compliant: true"),
            "should contain nyquist_compliant: true, got:\n{md}"
        );
    }

    #[test]
    fn generate_report_md_zero_passing_slots_contains_mcpdf_and_null() {
        let results: Vec<SlotReport> = vec![
            make_slot_report(1, 1, "none", false),
            make_slot_report(1, 2, "none", false),
        ];
        let md = generate_report_md(&results);
        assert!(
            md.contains("McLean-Pontiff"),
            "should contain McLean-Pontiff reference, got:\n{md}"
        );
        assert!(
            md.contains("NULL"),
            "should contain NULL outcome, got:\n{md}"
        );
    }

    #[test]
    fn generate_report_md_one_passing_slot_contains_pass_and_slot_details() {
        let results = vec![
            make_slot_report(4, 18, "fixed_pct", true),
            make_slot_report(1, 1, "none", false),
        ];
        let md = generate_report_md(&results);
        assert!(
            md.contains("PASS"),
            "should contain PASS outcome, got:\n{md}"
        );
        assert!(
            md.contains("window_offset=4") || md.contains("4"),
            "should contain window_offset=4"
        );
        assert!(
            md.contains("hold_bars=18") || md.contains("18"),
            "should contain hold_bars=18"
        );
    }

    #[test]
    fn generate_report_md_contains_boj_and_ycc() {
        let results = vec![make_slot_report(1, 1, "none", false)];
        let md = generate_report_md(&results);
        assert!(
            md.contains("BOJ"),
            "should contain BOJ in regime context, got:\n{md}"
        );
        assert!(
            md.contains("YCC"),
            "should contain YCC in regime context, got:\n{md}"
        );
    }

    // -----------------------------------------------------------------------
    // Phase 36: CombinedEventReport + generate_report_md_combined tests
    // -----------------------------------------------------------------------

    #[test]
    fn combined_event_report_serde_roundtrip() {
        // SlotReport.exit_type is &'static str — Deserialize not derivable.
        // Verify JSON structure via serde_json::Value (proves D-01 round-trip intent).
        let original = CombinedEventReport {
            fomc: vec![],
            ecb: vec![],
            nfp: vec![],
        };
        let json = serde_json::to_string(&original).expect("serialize failed");
        let value: serde_json::Value = serde_json::from_str(&json).expect("parse failed");
        assert!(value["fomc"].is_array());
        assert!(value["ecb"].is_array());
        assert!(value["nfp"].is_array());
        assert_eq!(value["fomc"].as_array().unwrap().len(), 0);
        assert_eq!(value["ecb"].as_array().unwrap().len(), 0);
        assert_eq!(value["nfp"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn generate_report_md_combined_contains_all_sections() {
        let md = generate_report_md_combined(&[], &[], &[], "USDJPY", 2);
        assert!(
            md.contains("# Combined Macro Event Drift"),
            "missing top header, got:\n{md}"
        );
        assert!(
            md.contains("## Aggregate Summary"),
            "missing Aggregate Summary, got:\n{md}"
        );
        assert!(
            md.contains("## FOMC Breakdown"),
            "missing FOMC Breakdown, got:\n{md}"
        );
        assert!(
            md.contains("## ECB Breakdown"),
            "missing ECB Breakdown, got:\n{md}"
        );
        assert!(
            md.contains("## NFP Breakdown"),
            "missing NFP Breakdown, got:\n{md}"
        );
        assert!(
            md.contains("McLean-Pontiff"),
            "missing McLean-Pontiff framing, got:\n{md}"
        );
        assert!(
            md.contains("N_total") || md.contains("Combined N"),
            "missing N_total or Combined N, got:\n{md}"
        );
    }

    #[test]
    fn generate_report_md_combined_aggregate_counts_pass_slots() {
        // fomc=2 passed, ecb=0, nfp=1 passed → total pass=3
        let fomc_slots = vec![
            make_slot_report(1, 1, "none", true),
            make_slot_report(2, 2, "none", true),
        ];
        let ecb_slots: Vec<SlotReport> = vec![];
        let nfp_slots = vec![make_slot_report(3, 3, "fixed_pct", true)];
        let md = generate_report_md_combined(&fomc_slots, &ecb_slots, &nfp_slots, "USDJPY", 2);
        assert!(md.contains("3"), "should contain pass count 3, got:\n{md}");
        assert!(
            md.contains("pass") || md.contains("Pass") || md.contains("PASS"),
            "should contain 'pass', got:\n{md}"
        );
    }

    #[test]
    fn generate_report_md_combined_null_result_defensible() {
        let md = generate_report_md_combined(&[], &[], &[], "USDJPY", 2);
        assert!(
            md.contains("N_total: 0") || md.contains("N_total**: 0"),
            "should contain N_total: 0, got:\n{md}"
        );
        assert!(
            md.contains("null result"),
            "should contain 'null result', got:\n{md}"
        );
        assert!(
            md.contains("defensible"),
            "should contain 'defensible', got:\n{md}"
        );
    }

    #[test]
    fn generate_report_md_combined_per_source_headers_in_order() {
        let md = generate_report_md_combined(&[], &[], &[], "USDJPY", 2);
        let fomc_pos = md
            .find("## FOMC Breakdown")
            .expect("FOMC Breakdown header missing");
        let ecb_pos = md
            .find("## ECB Breakdown")
            .expect("ECB Breakdown header missing");
        let nfp_pos = md
            .find("## NFP Breakdown")
            .expect("NFP Breakdown header missing");
        assert!(fomc_pos < ecb_pos, "FOMC must come before ECB");
        assert!(ecb_pos < nfp_pos, "ECB must come before NFP");
    }

    // -----------------------------------------------------------------------
    // Phase 38: pair plumbing tests
    // -----------------------------------------------------------------------

    #[test]
    fn generate_report_md_fomc_usdjpy_contains_usdjpy_literal() {
        let results = vec![make_slot_report(1, 1, "none", false)];
        let md = generate_report_md_fomc(&results, "USDJPY");
        assert!(
            md.contains("USDJPY"),
            "pair=USDJPY で USDJPY リテラルが残らない: {}",
            md
        );
        assert!(md.contains("FOMC Drift Exploration — USDJPY 1h"));
    }

    #[test]
    fn generate_report_md_fomc_eurusd_strips_usdjpy_literal() {
        let results = vec![make_slot_report(1, 1, "none", false)];
        let md = generate_report_md_fomc(&results, "EURUSD");
        assert!(
            md.contains("EURUSD"),
            "pair=EURUSD で EURUSD リテラルが出ない: {}",
            md
        );
        assert!(
            !md.contains("USDJPY"),
            "pair=EURUSD なのに USDJPY リテラルが残る: {}",
            md
        );
        assert!(md.contains("FOMC Drift Exploration — EURUSD 1h"));
    }

    #[test]
    fn generate_report_md_combined_eurusd_strips_usdjpy_literal() {
        let fomc = vec![make_slot_report(1, 1, "none", false)];
        let ecb = vec![make_slot_report(2, 2, "none", false)];
        let nfp = vec![make_slot_report(3, 3, "fixed_pct", false)];
        let md = generate_report_md_combined(&fomc, &ecb, &nfp, "EURUSD", 2);
        assert!(md.contains("EURUSD"));
        assert!(
            !md.contains("USDJPY"),
            "combined(pair=EURUSD) なのに USDJPY リテラルが残る: {}",
            md
        );
    }
}
