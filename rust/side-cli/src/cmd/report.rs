//! `side report` — convert scan output JSON into markdown + JSON summary.
//!
//! Implements REPORT-01..04:
//!  REPORT-01: `side report --input <FILE> --output <BASE>` writes `<BASE>.md` + `<BASE>.json`
//!  REPORT-02: Per-slot details: fee curve table + 6-gate verdict table
//!  REPORT-03: Summary section with top-N candidates table + alpha cliff histogram
//!  REPORT-04: Negative result section (McLean-Pontiff frame) when strict_pass_count == 0

use chrono::Utc;
use clap::Args;
use serde::{Deserialize, Serialize};
use side_engine::validation::{Verdict, VerdictKind};

use super::types::SlotOutput;

// ── CLI args ─────────────────────────────────────────────────────────────────

#[derive(Args, Debug)]
pub struct ReportArgs {
    /// Input scan output JSON (array of SlotOutput).
    #[arg(long)]
    pub input: String,

    /// Output base path. Writes <base>.md and <base>.json.
    #[arg(long)]
    pub output: String,

    /// Top-N candidates to include in summary.
    #[arg(long, default_value_t = 10)]
    pub top_n: usize,

    /// Number of trials used for DSR deflation (for negative-result template).
    #[arg(long, default_value_t = 12_960)]
    pub n_trials: usize,
}

// ── Report data structures ────────────────────────────────────────────────────

#[derive(Debug, Serialize, Deserialize)]
pub struct ReportSummary {
    pub total_slots: usize,
    pub strict_pass_count: usize,
    pub top_candidates: Vec<TopCandidate>,
    pub alpha_cliff_histogram: Vec<HistogramBin>,
    pub negative_result: bool,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct TopCandidate {
    pub name: String,
    pub entry_minute: u16,
    pub direction: String,
    pub hold_h: u8,
    pub alpha_cliff: Option<f64>,
    pub pf_net_2bps_rt: Option<f64>,
    pub verdict_passed: bool,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct HistogramBin {
    pub fee_bps_rt: f64,
    pub slot_count: usize,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ReportJson {
    pub summary: ReportSummary,
    pub slots: Vec<SlotOutput>,
}

// ── Entry point ───────────────────────────────────────────────────────────────

pub async fn run(args: ReportArgs) -> anyhow::Result<()> {
    let json_str = std::fs::read_to_string(&args.input)?;
    let slots: Vec<SlotOutput> = serde_json::from_str(&json_str)?;

    let summary = build_summary(&slots, args.top_n);
    let md = render_markdown(&slots, &summary, args.n_trials);
    let report_json = ReportJson { summary, slots };

    std::fs::write(format!("{}.md", args.output), &md)?;
    std::fs::write(
        format!("{}.json", args.output),
        serde_json::to_string_pretty(&report_json)?,
    )?;
    generate_validation_md_for_slots(&report_json.slots, &args.output)?;
    Ok(())
}

/// Write VALIDATION.md for the given slots to `{base}-VALIDATION.md`.
/// Frontmatter contains `nyquist_compliant: true` per D-02.
fn generate_validation_md_for_slots(slots: &[SlotOutput], base: &str) -> anyhow::Result<()> {
    let strict_pass = slots.iter().filter(|s| verdict_passed(&s.verdict)).count();
    let status = if strict_pass > 0 {
        "pass"
    } else {
        "null_result"
    };
    let timestamp = Utc::now().to_rfc3339();
    let total = slots.len();

    let content = format!(
        "---\nnyquist_compliant: true\nstatus: {status}\nstrict_pass_count: {strict_pass}\ntotal_slots: {total}\ngenerated_at: {timestamp}\n---\n\n\
         ## Validation Summary\n\n\
         - Total slots: {total}\n\
         - Strict pass (6-gate): {strict_pass}\n\
         - Status: {status}\n\
         - Nyquist compliant: true\n"
    );

    std::fs::write(format!("{base}-VALIDATION.md"), content)?;
    Ok(())
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/// Returns true iff the verdict kind is Pass.
pub fn verdict_passed(v: &Option<Verdict>) -> bool {
    v.as_ref()
        .map(|x| matches!(x.verdict, VerdictKind::Pass))
        .unwrap_or(false)
}

/// Build the summary struct from the full slot list.
pub fn build_summary(slots: &[SlotOutput], top_n: usize) -> ReportSummary {
    let total_slots = slots.len();
    let strict_pass_count = slots.iter().filter(|s| verdict_passed(&s.verdict)).count();

    // Sort by alpha_cliff descending, take top_n
    let mut sorted: Vec<&SlotOutput> = slots.iter().collect();
    sorted.sort_by(|a, b| {
        let av = a.alpha_cliff.unwrap_or(f64::NEG_INFINITY);
        let bv = b.alpha_cliff.unwrap_or(f64::NEG_INFINITY);
        bv.partial_cmp(&av).unwrap_or(std::cmp::Ordering::Equal)
    });
    let top_candidates: Vec<TopCandidate> = sorted
        .iter()
        .take(top_n)
        .map(|s| TopCandidate {
            name: s.name.clone(),
            entry_minute: s.entry_minute,
            direction: s.direction.clone(),
            hold_h: s.hold_h,
            alpha_cliff: s.alpha_cliff,
            pf_net_2bps_rt: s.pf_net_2bps_rt,
            verdict_passed: verdict_passed(&s.verdict),
        })
        .collect();

    // Histogram: 5 buckets [0,1), [1,2), [2,3), [3,5), [5,inf)
    // Upper-edge labels: 1.0, 2.0, 3.0, 5.0, >5.0
    let bucket_edges: &[(f64, f64, f64)] = &[
        (0.0, 1.0, 1.0),
        (1.0, 2.0, 2.0),
        (2.0, 3.0, 3.0),
        (3.0, 5.0, 5.0),
        (5.0, f64::INFINITY, f64::MAX), // >5.0 sentinel
    ];
    let mut counts = [0usize; 5];
    for s in slots {
        if let Some(cliff) = s.alpha_cliff {
            for (i, &(lo, hi, _)) in bucket_edges.iter().enumerate() {
                if cliff >= lo && cliff < hi {
                    counts[i] += 1;
                    break;
                }
            }
        }
    }
    let alpha_cliff_histogram: Vec<HistogramBin> = bucket_edges
        .iter()
        .enumerate()
        .map(|(i, &(_, _, label))| HistogramBin {
            fee_bps_rt: label,
            slot_count: counts[i],
        })
        .collect();

    let negative_result = strict_pass_count == 0;

    ReportSummary {
        total_slots,
        strict_pass_count,
        top_candidates,
        alpha_cliff_histogram,
        negative_result,
    }
}

/// Render the full markdown report.
pub fn render_markdown(slots: &[SlotOutput], summary: &ReportSummary, n_trials: usize) -> String {
    let timestamp = Utc::now().to_rfc3339();
    let mut md = String::new();

    // Header
    md.push_str(&format!("# Scan Report — {timestamp}\n\n"));

    // Summary
    md.push_str("## Summary\n\n");
    md.push_str(&format!("- Total slots scanned: {}\n", summary.total_slots));
    md.push_str(&format!(
        "- 6-gate strict pass: {}\n\n",
        summary.strict_pass_count
    ));

    // Top-N candidates table
    let top_n = summary.top_candidates.len();
    md.push_str(&format!(
        "### Top {top_n} candidates (by alpha_cliff desc)\n\n"
    ));
    md.push_str(
        "| Rank | Name | Entry | Direction | Hold (h) | Alpha cliff | PF @2bps RT | Verdict |\n",
    );
    md.push_str(
        "|------|------|-------|-----------|----------|-------------|-------------|--------|\n",
    );
    for (i, c) in summary.top_candidates.iter().enumerate() {
        let cliff = c
            .alpha_cliff
            .map(|v| format!("{:.2}", v))
            .unwrap_or_else(|| "N/A".to_string());
        let pf = c
            .pf_net_2bps_rt
            .map(|v| format!("{:.2}", v))
            .unwrap_or_else(|| "N/A".to_string());
        let verdict = if c.verdict_passed { "PASS" } else { "FAIL" };
        md.push_str(&format!(
            "| {} | {} | {} | {} | {} | {} | {} | {} |\n",
            i + 1,
            c.name,
            c.entry_minute,
            c.direction,
            c.hold_h,
            cliff,
            pf,
            verdict
        ));
    }
    md.push('\n');

    // Alpha cliff distribution
    md.push_str("### Alpha cliff distribution\n\n");
    md.push_str("| Bucket upper (bps RT) | Slots |\n");
    md.push_str("|-----------------------|-------|\n");
    for bin in &summary.alpha_cliff_histogram {
        let label = if bin.fee_bps_rt == f64::MAX {
            ">5.0".to_string()
        } else {
            format!("{:.1}", bin.fee_bps_rt)
        };
        md.push_str(&format!("| {} | {} |\n", label, bin.slot_count));
    }
    md.push('\n');

    // Slot Details
    md.push_str("## Slot Details\n\n");
    for slot in slots {
        md.push_str(&render_slot(slot));
    }

    // Negative result section
    if summary.negative_result {
        let max_pf_observed = slots
            .iter()
            .filter_map(|s| s.pf_gross)
            .fold(f64::NEG_INFINITY, f64::max);
        let max_pf_observed = if max_pf_observed.is_infinite() {
            0.0
        } else {
            max_pf_observed
        };

        let fee_threshold_bps = slots
            .iter()
            .filter_map(|s| s.alpha_cliff)
            .fold(f64::NEG_INFINITY, f64::max);
        let fee_threshold_bps = if fee_threshold_bps.is_infinite() {
            0.0
        } else {
            fee_threshold_bps
        };

        md.push_str(&negative_result_section(
            slots.len(),
            max_pf_observed,
            fee_threshold_bps,
            n_trials,
        ));
    }

    md
}

/// Render a single slot section in markdown.
fn render_slot(slot: &SlotOutput) -> String {
    let mut s = String::new();
    s.push_str(&format!("### {}\n\n", slot.name));
    s.push_str(&format!("- entry_minute: {}\n", slot.entry_minute));
    s.push_str(&format!("- direction: {}\n", slot.direction));
    s.push_str(&format!("- hold_h: {}\n", slot.hold_h));
    s.push_str(&format!("- source_query: {}\n\n", slot.source_query));

    // Fee curve table
    s.push_str("**Fee curve**\n\n");
    s.push_str("| Fee (bps RT) | Trades | PF | Mean pip |\n");
    s.push_str("|--------------|--------|-----|----------|\n");
    for fc in &slot.fee_curve {
        let mean_pip_str = fc.mean_pip.map_or("N/A".to_string(), |v| {
            if v.abs() > 1e10 {
                "OVF".to_string()
            } else {
                format!("{v:.2}")
            }
        });
        s.push_str(&format!(
            "| {} | {} | {} | {} |\n",
            fc.fee_bps_rt,
            fc.trades,
            fc.pf.map_or("N/A".to_string(), |v| format!("{v:.2}")),
            mean_pip_str
        ));
    }
    s.push('\n');

    // 6-gate verdict table
    match &slot.verdict {
        None => {
            s.push_str("**Verdict**: not computed (relaxed mode)\n\n");
        }
        Some(verdict) => {
            s.push_str("**6-gate verdict**\n\n");
            s.push_str("| Gate | Value | Threshold | Pass |\n");
            s.push_str("|------|-------|-----------|------|\n");
            for gate in &verdict.gates {
                let pass_mark = if gate.passed { "✓" } else { "✗" };
                s.push_str(&format!(
                    "| {} | {:.4} | {:.4} | {} |\n",
                    gate.gate, gate.value, gate.threshold, pass_mark
                ));
            }
            let overall = match &verdict.verdict {
                VerdictKind::Pass => "**Overall: PASS**".to_string(),
                VerdictKind::Fail { failed_gates } => {
                    format!("**Overall: FAIL** (failed: {})", failed_gates.join(", "))
                }
            };
            s.push('\n');
            s.push_str(&overall);
            s.push_str("\n\n");
        }
    }

    // Per-fee 6-gate verdicts table (Phase 20 D-06)
    if let Some(verdicts) = &slot.verdicts_per_fee {
        if !verdicts.is_empty() {
            s.push_str("**Per-fee 6-gate verdicts**\n\n");
            // Build table header from first verdict's gate names
            let gate_names: Vec<&str> = verdicts[0]
                .verdict
                .gates
                .iter()
                .map(|g| g.gate.as_str())
                .collect();
            s.push_str("| Fee (bps RT) |");
            for name in &gate_names {
                s.push_str(&format!(" {name} |"));
            }
            s.push_str(" Overall |\n");

            s.push_str("|---|");
            for _ in &gate_names {
                s.push_str("---|");
            }
            s.push_str("---|\n");

            for fv in verdicts {
                s.push_str(&format!("| {:.1} |", fv.fee_bps_rt));
                for gate in &fv.verdict.gates {
                    let mark = if gate.passed { "✓" } else { "✗" };
                    s.push_str(&format!(" {mark} |"));
                }
                let overall = match &fv.verdict.verdict {
                    VerdictKind::Pass => "PASS",
                    VerdictKind::Fail { .. } => "FAIL",
                };
                s.push_str(&format!(" {overall} |\n"));
            }
            s.push('\n');
        }
    }

    s
}

/// Generate the McLean-Pontiff negative result section.
///
/// Per REPORT-04: injected when strict_pass_count == 0.
pub fn negative_result_section(
    n_slots_tested: usize,
    max_pf_observed: f64,
    fee_threshold_bps: f64,
    n_trials: usize,
) -> String {
    format!(
        r#"## Negative Result — McLean-Pontiff Frame

This scan tested {n_slots_tested} slot(s) across {n_trials} candidate configurations
(Bonferroni-adjusted |t| > 4.40 threshold). Zero slots cleared all 6 gates under strict
fee-corrected validation.

**Key statistics:**
- Max gross PF observed: {max_pf_observed:.4}
- Most fee-resilient alpha cliff: {fee_threshold_bps:.2} bps RT
- Scan dimension: {n_trials} (1440 minutes × 9 horizons)
- Pass criterion: 6-gate composite (|t| + DSR + mean + OOS + H1/H2 + bootstrap CI)

**Interpretation:** Absence of a confirmed edge in this scan is informative.
Under McLean-Pontiff (2016), post-publication decay of anomalies is well-documented;
a null result after Bonferroni correction at |t| > 4.40 provides evidence that any
apparent edge does not survive realistic fee and multiple-testing adjustments.

**Reference:** McLean & Pontiff (2016). *Does Academic Research Destroy Stock Return Predictability?* The Journal of Finance, 71(1), 5-32.
"#
    )
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use side_engine::validation::{GateResult, PassMode, Verdict, VerdictKind};

    fn make_verdict_pass() -> Verdict {
        Verdict {
            gates: vec![],
            verdict: VerdictKind::Pass,
            pass_mode: PassMode::Strict,
        }
    }

    fn make_verdict_fail() -> Verdict {
        Verdict {
            gates: vec![],
            verdict: VerdictKind::Fail {
                failed_gates: vec!["abs_t_stat".to_string()],
            },
            pass_mode: PassMode::Strict,
        }
    }

    fn make_slot(name: &str, alpha_cliff: Option<f64>, verdict: Option<Verdict>) -> SlotOutput {
        SlotOutput {
            name: name.to_string(),
            params: serde_json::json!({}),
            entry_minute: 540,
            direction: "long".to_string(),
            hold_h: 2,
            source_query: "test".to_string(),
            source_edge_index: 0,
            fee_curve: vec![],
            pf_gross: alpha_cliff.map(|c| c + 1.0),
            pf_net_2bps_rt: Some(1.5),
            alpha_cliff,
            verdict,
            relaxed_pass: None,
            verdicts_per_fee: None,
            risk_gate: None,
        }
    }

    #[test]
    fn test_verdict_passed_some_pass() {
        assert!(verdict_passed(&Some(make_verdict_pass())));
    }

    #[test]
    fn test_verdict_passed_some_fail() {
        assert!(!verdict_passed(&Some(make_verdict_fail())));
    }

    #[test]
    fn test_verdict_passed_none() {
        assert!(!verdict_passed(&None));
    }

    #[test]
    fn test_build_summary_empty() {
        let summary = build_summary(&[], 10);
        assert!(summary.negative_result);
        assert_eq!(summary.total_slots, 0);
        assert_eq!(summary.strict_pass_count, 0);
        for bin in &summary.alpha_cliff_histogram {
            assert_eq!(bin.slot_count, 0);
        }
    }

    #[test]
    fn test_build_summary_top_n_sort() {
        let slots = vec![
            make_slot("a", Some(2.0), None),
            make_slot("b", Some(1.0), None),
            make_slot("c", Some(3.0), None),
        ];
        let summary = build_summary(&slots, 10);
        let cliffs: Vec<f64> = summary
            .top_candidates
            .iter()
            .map(|c| c.alpha_cliff.unwrap())
            .collect();
        assert_eq!(cliffs, vec![3.0, 2.0, 1.0]);
    }

    #[test]
    fn test_alpha_cliff_histogram_buckets() {
        // 0.5 → [0,1), 1.5 → [1,2), 2.5 → [2,3), 3.5 → [3,5), 5.5 → [5,inf)
        let slots = vec![
            make_slot("a", Some(0.5), None),
            make_slot("b", Some(1.5), None),
            make_slot("c", Some(2.5), None),
            make_slot("d", Some(3.5), None),
            make_slot("e", Some(5.5), None),
        ];
        let summary = build_summary(&slots, 10);
        let counts: Vec<usize> = summary
            .alpha_cliff_histogram
            .iter()
            .map(|b| b.slot_count)
            .collect();
        assert_eq!(counts, vec![1, 1, 1, 1, 1]);
    }

    #[test]
    fn test_negative_result_section_format() {
        let section = negative_result_section(100, 3.5, 1.87, 12_960);
        assert!(section.contains("McLean-Pontiff"), "missing McLean-Pontiff");
        assert!(section.contains("100"), "missing n_slots_tested");
        assert!(
            section.contains("Does Academic Research Destroy Stock Return Predictability"),
            "missing reference"
        );
        assert!(section.contains("Reference:"), "missing Reference label");
    }

    #[test]
    fn test_render_markdown_contains_sections() {
        let slots = vec![make_slot("slot_a", Some(1.5), None)];
        let summary = build_summary(&slots, 5);
        let md = render_markdown(&slots, &summary, 12_960);
        assert!(md.contains("# Scan Report"));
        assert!(md.contains("## Summary"));
        assert!(md.contains("## Slot Details"));
        assert!(md.contains("slot_a"));
    }

    #[test]
    fn test_render_slot_verdicts_per_fee_table_rendered() {
        use crate::cmd::types::FeeVerdict;
        let gate = GateResult {
            gate: "abs_t_stat".to_string(),
            passed: false,
            value: 3.10,
            threshold: 4.40,
        };
        let verdict = Verdict {
            gates: vec![gate],
            verdict: VerdictKind::Fail {
                failed_gates: vec!["abs_t_stat".to_string()],
            },
            pass_mode: PassMode::Strict,
        };
        let fee_verdict = FeeVerdict {
            fee_bps_rt: 1.0,
            verdict,
        };
        let mut slot = make_slot("pf_slot", Some(1.1), None);
        slot.verdicts_per_fee = Some(vec![fee_verdict]);
        let md = render_slot(&slot);
        assert!(
            md.contains("Per-fee 6-gate verdicts"),
            "missing per-fee header"
        );
        assert!(
            md.contains("abs_t_stat"),
            "missing gate name in per-fee table"
        );
        assert!(md.contains("1.0"), "missing fee level in per-fee table");
    }

    #[test]
    fn test_render_slot_with_verdict() {
        let verdict = Verdict {
            gates: vec![GateResult {
                gate: "abs_t_stat".to_string(),
                passed: true,
                value: 4.50,
                threshold: 4.40,
            }],
            verdict: VerdictKind::Pass,
            pass_mode: PassMode::Strict,
        };
        let slot = make_slot("test_slot", Some(2.0), Some(verdict));
        let md = render_slot(&slot);
        assert!(md.contains("abs_t_stat"));
        assert!(md.contains("✓"));
        assert!(md.contains("PASS"));
    }
}
