//! POWER-01 ρ̄ extrapolation + decision (Phase 76 Plan 03, Wave-2.5).
//!
//! Reads smoke report.json (usdjpy × fomc × 192 slot × 5 fee = 960 runs),
//! computes adjacent-window_offset pairwise Pearson correlation across
//! OOS sharpe series, takes median → ρ̄, then n_eff = k / (1 + (k-1) * ρ̄)
//! with k = 192. Writes decision JSON: proceed if n_eff >= gate_k, else
//! null-ship-v2. Per Phase 74 D-06/D-18, post-hoc relaxation is absolutely
//! prohibited — this gate is a hard kill-switch.

use std::collections::BTreeMap;
use std::path::PathBuf;

use anyhow::{Context, Result};
use chrono::Local;
use clap::Args;
use serde::Serialize;
use serde_json::Value;

#[derive(Args, Debug)]
pub struct Phase76PowerCheckArgs {
    /// Path to Wave-2 smoke report.json (absolute path preferred).
    #[arg(long)]
    pub smoke_report: String,

    /// Output JSON path (decision artifact).
    #[arg(
        long,
        default_value = ".planning/phases/76-grid-engine-192-slot-full-wfd-rerun/76-POWER-CHECK.json"
    )]
    pub output: String,

    /// Output Markdown path (human-readable summary).
    #[arg(
        long,
        default_value = ".planning/phases/76-grid-engine-192-slot-full-wfd-rerun/76-POWER-CHECK.md"
    )]
    pub output_md: String,

    /// Power floor (n_eff_predicted < gate_k → null-ship-v2).
    #[arg(long, default_value_t = 4)]
    pub gate_k: u32,

    /// Total slot count k used in n_eff formula.
    #[arg(long, default_value_t = 192)]
    pub k_slots: u32,
}

#[derive(Serialize)]
struct PowerCheckDecision {
    rho_bar: f64,
    k: u32,
    n_eff_predicted: f64,
    gate_k: u32,
    decision: String,
    smoke_report: String,
    generated_at: String,
    commit_sha: String,
    n_adjacent_pairs_used: usize,
    n_adjacent_pairs_skipped: usize,
    per_pair_correlations: Vec<PairCorrelation>,
}

#[derive(Serialize)]
struct PairCorrelation {
    window_offset_a: u32,
    window_offset_b: u32,
    correlation: Option<f64>,
    n_samples: usize,
}

fn git_short_sha() -> Result<String> {
    let out = std::process::Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .context("git rev-parse failed")?;
    if !out.status.success() {
        anyhow::bail!(
            "git rev-parse --short HEAD exited non-zero: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
    Ok(String::from_utf8(out.stdout)?.trim().to_string())
}

fn git_is_dirty() -> bool {
    std::process::Command::new("git")
        .args(["diff-index", "--quiet", "HEAD", "--"])
        .status()
        .map(|s| !s.success())
        .unwrap_or(false)
}

fn commit_sha_stamp() -> Result<String> {
    let sha = git_short_sha()?;
    if git_is_dirty() {
        Ok(format!("{sha}-dirty"))
    } else {
        Ok(sha)
    }
}

/// Pearson correlation of two equal-length series. Returns `None` when length
/// < 2, any value is non-finite, or either series has zero variance.
fn pearson(xs: &[f64], ys: &[f64]) -> Option<f64> {
    if xs.len() != ys.len() || xs.len() < 2 {
        return None;
    }
    if xs.iter().chain(ys.iter()).any(|v| !v.is_finite()) {
        return None;
    }
    let n = xs.len() as f64;
    let mean_x = xs.iter().sum::<f64>() / n;
    let mean_y = ys.iter().sum::<f64>() / n;
    let mut num = 0.0;
    let mut denom_x = 0.0;
    let mut denom_y = 0.0;
    for (&x, &y) in xs.iter().zip(ys.iter()) {
        let dx = x - mean_x;
        let dy = y - mean_y;
        num += dx * dy;
        denom_x += dx * dx;
        denom_y += dy * dy;
    }
    if denom_x <= 0.0 || denom_y <= 0.0 {
        return None;
    }
    let r = num / (denom_x.sqrt() * denom_y.sqrt());
    if r.is_finite() {
        Some(r)
    } else {
        None
    }
}

/// Median of a non-empty slice. Caller must guarantee non-empty.
fn median(values: &mut [f64]) -> f64 {
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = values.len();
    if n % 2 == 1 {
        values[n / 2]
    } else {
        0.5 * (values[n / 2 - 1] + values[n / 2])
    }
}

/// n_eff = k / (1 + (k-1) * ρ̄). ρ̄ is clamped to [0, 1] (negative correlations
/// are treated as 0 for the effective-sample calculation — a negative ρ̄ would
/// push n_eff > k, which has no physical meaning in a power floor context).
fn compute_n_eff(k: f64, rho_bar: f64) -> f64 {
    let rho = rho_bar.clamp(0.0, 1.0);
    k / (1.0 + (k - 1.0) * rho)
}

/// Returns (rho_bar, per-pair correlations, n_used, n_skipped).
fn compute_rho_bar(slots: &[Value]) -> (f64, Vec<PairCorrelation>, usize, usize) {
    // Bucket key: (hold_bars, exit_type, fee_bps_milli) → window_offset → sharpe.
    // fee_bps is f64; use fixed-point key (milli-bps, i64) for stable ordering.
    type BucketKey = (u32, String, i64);
    let mut buckets: BTreeMap<BucketKey, BTreeMap<u32, f64>> = BTreeMap::new();

    for slot in slots {
        let wo = slot["window_offset"].as_u64().unwrap_or_default() as u32;
        let hold = slot["hold_bars"].as_u64().unwrap_or_default() as u32;
        let exit = slot["exit_type"].as_str().unwrap_or_default().to_string();
        let fee_results = match slot["fee_results"].as_array() {
            Some(v) => v,
            None => continue,
        };
        for fr in fee_results {
            let fee_bps = fr["fee_bps"].as_f64().unwrap_or_default();
            let sharpe = match fr["combined_oos_sharpe"].as_f64() {
                Some(v) => v,
                None => continue,
            };
            let fee_key = (fee_bps * 1000.0).round() as i64;
            buckets
                .entry((hold, exit.clone(), fee_key))
                .or_default()
                .insert(wo, sharpe);
        }
    }

    // Determine the full window_offset axis (sorted).
    let mut wo_axis: Vec<u32> = buckets
        .values()
        .flat_map(|m| m.keys().copied())
        .collect::<std::collections::BTreeSet<u32>>()
        .into_iter()
        .collect();
    wo_axis.sort_unstable();

    let mut per_pair: Vec<PairCorrelation> = Vec::new();
    let mut correlations: Vec<f64> = Vec::new();
    let mut skipped = 0usize;

    // Adjacent pairs along the window_offset axis (wo_i, wo_{i+1}).
    for pair in wo_axis.windows(2) {
        let (a, b) = (pair[0], pair[1]);
        // Collect (sharpe_a, sharpe_b) across all buckets that contain BOTH wo's.
        let mut xs: Vec<f64> = Vec::new();
        let mut ys: Vec<f64> = Vec::new();
        for map in buckets.values() {
            if let (Some(&sa), Some(&sb)) = (map.get(&a), map.get(&b)) {
                xs.push(sa);
                ys.push(sb);
            }
        }
        let n = xs.len();
        let corr = pearson(&xs, &ys);
        match corr {
            Some(r) => correlations.push(r),
            None => skipped += 1,
        }
        per_pair.push(PairCorrelation {
            window_offset_a: a,
            window_offset_b: b,
            correlation: corr,
            n_samples: n,
        });
    }

    let rho_bar = if correlations.is_empty() {
        0.0
    } else {
        median(&mut correlations.clone())
    };
    (rho_bar, per_pair, correlations.len(), skipped)
}

fn write_markdown(path: &str, decision: &PowerCheckDecision) -> Result<()> {
    let margin = decision.n_eff_predicted - decision.gate_k as f64;
    let mut md = String::new();
    md.push_str("# Phase 76 POWER-01 Gate (Wave-2.5)\n\n");
    md.push_str(&format!("**Generated:** {}\n", decision.generated_at));
    md.push_str(&format!("**Smoke report:** {}\n", decision.smoke_report));
    md.push_str(&format!("**Commit:** {}\n\n", decision.commit_sha));

    md.push_str("## Numerical Result\n\n");
    md.push_str("| Metric | Value |\n");
    md.push_str("|---|---|\n");
    md.push_str(&format!(
        "| ρ̄ (median adjacent pairwise Pearson) | {:.6} |\n",
        decision.rho_bar
    ));
    md.push_str(&format!("| k (slots) | {} |\n", decision.k));
    md.push_str(&format!(
        "| n_eff_predicted = k / (1 + (k-1)·ρ̄) | {:.4} |\n",
        decision.n_eff_predicted
    ));
    md.push_str(&format!("| gate_k (power floor) | {} |\n", decision.gate_k));
    md.push_str(&format!("| n_eff - gate_k | {:.4} |\n", margin));
    md.push_str(&format!("| decision | **{}** |\n\n", decision.decision));

    md.push_str("## Adjacent-pair coverage\n\n");
    md.push_str(&format!(
        "- Pairs used: {}\n",
        decision.n_adjacent_pairs_used
    ));
    md.push_str(&format!(
        "- Pairs skipped (NaN / short series / zero variance): {}\n\n",
        decision.n_adjacent_pairs_skipped
    ));

    md.push_str("### Per-pair correlations\n\n");
    md.push_str("| wo_a | wo_b | correlation | n_samples |\n");
    md.push_str("|---|---|---|---|\n");
    for p in &decision.per_pair_correlations {
        let corr_str = match p.correlation {
            Some(r) => format!("{:.6}", r),
            None => "(skipped)".to_string(),
        };
        md.push_str(&format!(
            "| {} | {} | {} | {} |\n",
            p.window_offset_a, p.window_offset_b, corr_str, p.n_samples
        ));
    }
    md.push('\n');

    md.push_str("## Decision rule (D-18 absolute)\n\n");
    md.push_str("- `n_eff_predicted >= gate_k` → **proceed** (Wave-3 full run 進行)\n");
    md.push_str("- `n_eff_predicted < gate_k` → **null-ship-v2** (Wave-3 SKIP、phase close)\n");
    md.push_str("- **post-hoc relaxation 禁止** (GUARD-02 seal / Phase 74 D-06)\n");

    if let Some(parent) = PathBuf::from(path).parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to mkdir -p {}", parent.display()))?;
    }
    std::fs::write(path, md).with_context(|| format!("failed to write {path}"))?;
    Ok(())
}

pub async fn run(args: Phase76PowerCheckArgs) -> Result<()> {
    tracing::info!(
        "phase76-power-check: smoke_report={}, gate_k={}, k_slots={}",
        args.smoke_report,
        args.gate_k,
        args.k_slots
    );

    let text = std::fs::read_to_string(&args.smoke_report)
        .with_context(|| format!("failed to read smoke report: {}", args.smoke_report))?;
    let report: Value = serde_json::from_str(&text).context("failed to parse smoke report JSON")?;

    let slots = report["slots"]
        .as_array()
        .context("slots field is not an array")?;

    let (rho_bar, per_pair, n_used, n_skipped) = compute_rho_bar(slots);
    let k = args.k_slots as f64;
    let n_eff = compute_n_eff(k, rho_bar);
    let decision_str = if n_eff >= args.gate_k as f64 {
        "proceed"
    } else {
        "null-ship-v2"
    }
    .to_string();

    let commit_sha = commit_sha_stamp()?;
    let generated_at = Local::now().to_rfc3339();

    let decision = PowerCheckDecision {
        rho_bar,
        k: args.k_slots,
        n_eff_predicted: n_eff,
        gate_k: args.gate_k,
        decision: decision_str.clone(),
        smoke_report: args.smoke_report.clone(),
        generated_at,
        commit_sha,
        n_adjacent_pairs_used: n_used,
        n_adjacent_pairs_skipped: n_skipped,
        per_pair_correlations: per_pair,
    };

    // Write JSON.
    if let Some(parent) = PathBuf::from(&args.output).parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to mkdir -p {}", parent.display()))?;
    }
    let json = serde_json::to_string_pretty(&decision).context("failed to serialize decision")?;
    std::fs::write(&args.output, &json)
        .with_context(|| format!("failed to write {}", args.output))?;

    // Write Markdown.
    write_markdown(&args.output_md, &decision)?;

    tracing::info!(
        "POWER-01 gate: rho_bar={:.6}, n_eff_predicted={:.4}, decision={}",
        decision.rho_bar,
        decision.n_eff_predicted,
        decision.decision
    );
    println!(
        "rho_bar={:.6} n_eff_predicted={:.4} gate_k={} decision={}",
        decision.rho_bar, decision.n_eff_predicted, decision.gate_k, decision.decision
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rho_bar_zero_yields_n_eff_equals_k() {
        let n_eff = compute_n_eff(192.0, 0.0);
        assert!((n_eff - 192.0).abs() < 1e-9);
    }

    #[test]
    fn rho_bar_one_yields_n_eff_one() {
        let n_eff = compute_n_eff(192.0, 1.0);
        assert!((n_eff - 1.0).abs() < 1e-9);
    }

    #[test]
    fn rho_bar_half_yields_expected_value() {
        let n_eff = compute_n_eff(192.0, 0.5);
        let expected = 192.0 / (1.0 + 191.0 * 0.5);
        assert!((n_eff - expected).abs() < 1e-6);
    }

    #[test]
    fn decision_above_gate_proceeds() {
        // ρ̄ where n_eff = 5 → solve 5 = 192 / (1 + 191ρ) → ρ ≈ 0.1958
        let n_eff = compute_n_eff(192.0, 0.1958115);
        assert!(n_eff >= 4.0);
    }

    #[test]
    fn decision_below_gate_null_ships() {
        // ρ̄ = 0.5 → n_eff ≈ 1.99 < 4
        let n_eff = compute_n_eff(192.0, 0.5);
        assert!(n_eff < 4.0);
    }

    #[test]
    fn decision_at_gate_boundary_proceeds() {
        // n_eff == 4 exactly → inclusive proceed (CONTEXT.md D-18 "< 4" means strictly below fails).
        // solve 4 = 192 / (1 + 191ρ) → ρ = (192/4 - 1) / 191 = 47 / 191
        let rho = 47.0 / 191.0;
        let n_eff = compute_n_eff(192.0, rho);
        assert!((n_eff - 4.0).abs() < 1e-6);
        assert!(n_eff >= 4.0, "boundary is proceed (>=), not null-ship (<)");
    }

    #[test]
    fn pearson_constant_series_returns_none() {
        let xs = vec![0.0_f64, 0.0, 0.0, 0.0];
        let ys = vec![1.0_f64, 2.0, 3.0, 4.0];
        assert!(pearson(&xs, &ys).is_none());
    }

    #[test]
    fn pearson_identical_series_is_one() {
        let xs = vec![1.0_f64, 2.0, 3.0, 4.0, 5.0];
        let r = pearson(&xs, &xs).unwrap();
        assert!((r - 1.0).abs() < 1e-9);
    }

    #[test]
    fn pearson_short_series_returns_none() {
        let xs = vec![1.0_f64];
        let ys = vec![2.0_f64];
        assert!(pearson(&xs, &ys).is_none());
    }

    #[test]
    fn negative_rho_bar_is_clamped_to_zero() {
        // Defensive: negative ρ̄ should not inflate n_eff above k.
        let n_eff = compute_n_eff(192.0, -0.5);
        assert!((n_eff - 192.0).abs() < 1e-9);
    }

    #[test]
    fn median_odd_returns_middle() {
        let mut v = vec![3.0, 1.0, 5.0];
        assert!((median(&mut v) - 3.0).abs() < 1e-9);
    }

    #[test]
    fn median_even_returns_average_of_middle_two() {
        let mut v = vec![4.0, 2.0, 1.0, 3.0];
        assert!((median(&mut v) - 2.5).abs() < 1e-9);
    }
}
