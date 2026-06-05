//! `side sign-forensics` — methodological audit (config drift / DST / intersection re-agg).
//!
//! Phase 61 thin orchestrator (per CONTEXT.md D-02). Heavy logic lives in
//! `scripts/v4.4/audit.py`; this controller validates inputs, builds the
//! subprocess invocation, and surfaces exit status.
//!
//! Analog: `cmd::cross_report_4` (separation-cmd precedent).

use anyhow::Context;
use clap::Args;

#[derive(Args, Debug)]
pub struct SignForensicsArgs {
    /// Path(s) to v4.1 report.json (2022-23 epoch). Repeatable.
    #[arg(long, value_name = "PATH")]
    pub v41_report: Vec<String>,

    /// Path(s) to v4.2 report.json (2024-26 epoch). Repeatable.
    #[arg(long, value_name = "PATH")]
    pub v42_report: Vec<String>,

    /// Output directory for audit_matrix.{json,md} + drift_detected.json.
    #[arg(
        long,
        value_name = "PATH",
        default_value = "docs/reports/v4.4-sign-forensics"
    )]
    pub output: String,

    /// Path to Python audit script (subprocess target).
    #[arg(long, value_name = "PATH", default_value = "scripts/v4.4/audit.py")]
    pub script: String,

    /// If set, bypass user confirmation gate when drift is detected (CONFIG-05 override).
    #[arg(long, default_value_t = false)]
    pub force: bool,

    /// Also run Phase 62 breakdown (sign_breakdown.py) after Phase 61 audit.
    #[arg(long, default_value_t = false)]
    pub breakdown: bool,

    /// Path to Phase 62 breakdown Python script.
    #[arg(
        long,
        value_name = "PATH",
        default_value = "scripts/v4.4/sign_breakdown.py"
    )]
    pub breakdown_script: String,

    /// RNG seed for Politis-Romano bootstrap reproducibility (Phase 62, D-12).
    #[arg(long, default_value_t = 42)]
    pub seed: u64,

    /// Also run Phase 63 structural interpretation report (scripts/v4.4/report.py)
    /// after Phase 61 audit and Phase 62 breakdown artifacts are produced.
    /// Mutually exclusive with --breakdown (D-27: 1 flag 1 phase).
    #[arg(long, default_value_t = false)]
    pub report: bool,

    /// Path to Phase 63 report Python script.
    #[arg(long, value_name = "PATH", default_value = "scripts/v4.4/report.py")]
    pub report_script: String,
}

pub async fn run(args: SignForensicsArgs) -> anyhow::Result<()> {
    // Phase 61 thin orchestrator: shell out to Python audit script.
    // (cross_report_4.rs is load→Rust compute→write; we delegate compute to Python
    //  per CONTEXT.md D-02. Subprocess-based Rust→Python handoff per PATTERNS.md.)

    // D-27: 1 flag 1 phase — --breakdown and --report are mutually exclusive.
    if args.breakdown && args.report {
        anyhow::bail!(
            "--breakdown and --report are mutually exclusive (1 flag 1 phase, CONTEXT D-27). \
             Run them in two separate invocations."
        );
    }

    std::fs::create_dir_all(&args.output)
        .with_context(|| format!("failed to create output dir {}", args.output))?;

    if args.v41_report.is_empty() && args.v42_report.is_empty() {
        anyhow::bail!(
            "must provide at least one --v41-report or --v42-report path (got 0 of each)"
        );
    }

    let mut cmd = std::process::Command::new("uv");
    cmd.arg("run").arg("python").arg(&args.script);
    cmd.arg("--output").arg(&args.output);
    for p in &args.v41_report {
        cmd.arg("--v41-report").arg(p);
    }
    for p in &args.v42_report {
        cmd.arg("--v42-report").arg(p);
    }
    if args.force {
        cmd.arg("--force");
    }

    let status = cmd
        .status()
        .with_context(|| format!("failed to spawn `uv run python {}`", args.script))?;
    if !status.success() {
        anyhow::bail!("audit script exited with status {}", status);
    }

    println!("✓ audit artifacts written to {}", args.output);

    // Phase 62 breakdown subprocess — opt-in via --breakdown (D-02 orchestrator pattern).
    if args.breakdown {
        let mut bcmd = std::process::Command::new("uv");
        bcmd.arg("run").arg("python").arg(&args.breakdown_script);

        // Forward v4.2 reports as --input <path> (Phase 62 D-05 primary input).
        for p in &args.v42_report {
            bcmd.arg("--input").arg(p);
        }
        // Also forward v4.1 reports if provided — loader handles both per D-05.
        for p in &args.v41_report {
            bcmd.arg("--input").arg(p);
        }

        // args.output is a directory in Phase 61; Phase 62 expects a file path.
        let breakdown_out = format!("{}/sign_breakdown.json", args.output.trim_end_matches('/'));
        bcmd.arg("--output").arg(&breakdown_out);
        bcmd.arg("--seed").arg(args.seed.to_string());

        let bstatus = bcmd.status().with_context(|| {
            format!("failed to spawn `uv run python {}`", args.breakdown_script)
        })?;
        if !bstatus.success() {
            anyhow::bail!("breakdown script exited with status {}", bstatus);
        }
        println!("✓ sign_breakdown.json written to {}", breakdown_out);
    }

    // Phase 63 report subprocess — opt-in via --report (D-02 orchestrator pattern).
    // Consumes 4 JSON inputs from --output dir + emits report.{json,md} + VALIDATION.md.
    if args.report {
        let mut rcmd = std::process::Command::new("uv");
        rcmd.arg("run").arg("python").arg(&args.report_script);

        let out_trimmed = args.output.trim_end_matches('/');
        rcmd.arg("--audit")
            .arg(format!("{out_trimmed}/audit_matrix.json"));
        rcmd.arg("--drift")
            .arg(format!("{out_trimmed}/drift_detected.json"));
        rcmd.arg("--sign")
            .arg(format!("{out_trimmed}/sign_breakdown.json"));
        rcmd.arg("--regime")
            .arg(format!("{out_trimmed}/regime_labels.json"));
        rcmd.arg("--output-dir").arg(&args.output);
        rcmd.arg("--commit-ref").arg("8498b0e");

        let rstatus = rcmd
            .status()
            .with_context(|| format!("failed to spawn `uv run python {}`", args.report_script))?;
        if !rstatus.success() {
            anyhow::bail!("report script exited with status {}", rstatus);
        }
        println!("✓ v4.4 report artifact trio written to {}", args.output);
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;

    #[derive(Parser, Debug)]
    struct TestCli {
        #[command(flatten)]
        args: SignForensicsArgs,
    }

    #[test]
    fn report_flag_parsed() {
        let cli = TestCli::try_parse_from([
            "x",
            "--v42-report",
            "dummy.json",
            "--report",
            "--output",
            "/tmp/out",
        ])
        .expect("clap should accept --report");
        assert!(cli.args.report, "--report should parse to true");
        assert_eq!(cli.args.report_script, "scripts/v4.4/report.py");
        assert!(!cli.args.breakdown, "--breakdown should default false");
    }

    #[test]
    fn report_flag_default_false() {
        let cli = TestCli::try_parse_from(["x", "--v42-report", "dummy.json"])
            .expect("clap should accept minimal args");
        assert!(!cli.args.report, "--report should default to false");
    }

    #[test]
    fn report_script_override() {
        let cli = TestCli::try_parse_from([
            "x",
            "--v42-report",
            "dummy.json",
            "--report",
            "--report-script",
            "custom/report.py",
        ])
        .expect("clap should accept --report-script override");
        assert_eq!(cli.args.report_script, "custom/report.py");
    }
}
