//! Integration tests for `side report` subcommand (REPORT-01..04).
//!
//! These tests call `cmd::report::run` as a library function (no subprocess),
//! loading the canonical `slot_output_minimal.json` fixture which has 2 slots,
//! both with `kind: "Fail"` verdicts → negative_result = true.

use side_cli::cmd::report::{run, ReportArgs};
use std::path::PathBuf;

fn fixture_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("side-engine")
        .join("tests")
        .join("fixtures")
        .join("slot_output_minimal.json")
}

/// REPORT-01: `side report` writes both `<base>.md` and `<base>.json`.
/// REPORT-04: McLean-Pontiff section appears because both fixture slots have Fail verdicts.
#[tokio::test]
async fn report_generates_md_and_json_files() {
    let tmp = tempfile::tempdir().unwrap();
    let output_base = tmp.path().join("report").to_string_lossy().to_string();

    let args = ReportArgs {
        input: fixture_path().to_string_lossy().to_string(),
        output: output_base.clone(),
        top_n: 10,
        n_trials: 12_960,
    };

    run(args).await.expect("report run failed");

    let md_path = format!("{}.md", output_base);
    let json_path = format!("{}.json", output_base);
    assert!(
        std::path::Path::new(&md_path).exists(),
        ".md not written to {md_path}"
    );
    assert!(
        std::path::Path::new(&json_path).exists(),
        ".json not written to {json_path}"
    );

    let md = std::fs::read_to_string(&md_path).unwrap();
    assert!(md.contains("# Scan Report"), "missing H1 header");
    assert!(md.contains("## Summary"), "missing Summary section");
    assert!(
        md.contains("## Slot Details"),
        "missing Slot Details section"
    );
    assert!(
        md.contains("tod_edge_540_long_h2"),
        "missing first slot name"
    );
    // Both slots fail → negative result section must render
    assert!(
        md.contains("McLean-Pontiff"),
        "missing McLean-Pontiff negative result section"
    );

    let json_str = std::fs::read_to_string(&json_path).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(&json_str).unwrap();
    assert_eq!(parsed["summary"]["total_slots"], 2, "total_slots mismatch");
    assert_eq!(
        parsed["summary"]["strict_pass_count"], 0,
        "strict_pass_count should be 0 (both slots Fail)"
    );
    assert_eq!(
        parsed["summary"]["negative_result"], true,
        "negative_result should be true"
    );
    assert_eq!(
        parsed["slots"].as_array().unwrap().len(),
        2,
        "slots array should have 2 entries"
    );
}

/// REPORT-02: Per-slot sections contain fee curve table rows and 6-gate verdict table.
#[tokio::test]
async fn report_slot_detail_contains_fee_curve_and_gates() {
    let tmp = tempfile::tempdir().unwrap();
    let output_base = tmp.path().join("report2").to_string_lossy().to_string();
    run(ReportArgs {
        input: fixture_path().to_string_lossy().to_string(),
        output: output_base.clone(),
        top_n: 5,
        n_trials: 12_960,
    })
    .await
    .unwrap();

    let md = std::fs::read_to_string(format!("{}.md", output_base)).unwrap();

    // Fee curve: fixture has fee_bps_rt=0, trades=120 for first slot
    assert!(
        md.contains("| 0 | 120 |") || md.contains("| 0.0 | 120 |"),
        "fee=0 row missing from fee curve table"
    );
    // PF value from fixture
    assert!(md.contains("3.37"), "PF 3.37 missing from fee curve");

    // 6-gate verdict table: gate name from fixture
    assert!(
        md.contains("abs_t_stat"),
        "gate name 'abs_t_stat' missing from verdict table"
    );
    // Check-mark for passed gate
    assert!(md.contains("✓"), "pass mark ✓ missing from gate table");
    // Cross for failed gate
    assert!(md.contains("✗"), "fail mark ✗ missing from gate table");
}

/// VALIDATION.md: run() writes `<base>-VALIDATION.md` with nyquist_compliant frontmatter.
#[tokio::test]
async fn report_writes_validation_md() {
    let tmp = tempfile::tempdir().unwrap();
    let output_base = tmp.path().join("report_v").to_string_lossy().to_string();

    run(ReportArgs {
        input: fixture_path().to_string_lossy().to_string(),
        output: output_base.clone(),
        top_n: 10,
        n_trials: 12_960,
    })
    .await
    .expect("report run failed");

    let validation_path = format!("{}-VALIDATION.md", output_base);
    assert!(
        std::path::Path::new(&validation_path).exists(),
        "VALIDATION.md not written to {validation_path}"
    );

    let content = std::fs::read_to_string(&validation_path).unwrap();
    assert!(
        content.contains("nyquist_compliant: true"),
        "VALIDATION.md missing nyquist_compliant: true"
    );
    // Both fixture slots have Fail verdicts → null_result
    assert!(
        content.contains("status: null_result"),
        "VALIDATION.md missing status: null_result"
    );
    assert!(
        content.contains("strict_pass_count: 0"),
        "VALIDATION.md missing strict_pass_count: 0"
    );
}

/// REPORT-03: Summary section contains top-N candidates table and alpha cliff distribution.
#[tokio::test]
async fn report_summary_has_top_n_and_alpha_cliff_distribution() {
    let tmp = tempfile::tempdir().unwrap();
    let output_base = tmp.path().join("report3").to_string_lossy().to_string();
    run(ReportArgs {
        input: fixture_path().to_string_lossy().to_string(),
        output: output_base.clone(),
        top_n: 5,
        n_trials: 12_960,
    })
    .await
    .unwrap();

    let md = std::fs::read_to_string(format!("{}.md", output_base)).unwrap();

    // Top-N section heading: with only 2 fixture slots the heading renders "Top 2 candidates"
    // (render_markdown uses the actual count, not the top_n arg)
    assert!(
        md.contains("candidates (by alpha_cliff desc)"),
        "Top-N candidates heading missing"
    );
    // Alpha cliff distribution table heading
    assert!(
        md.contains("Alpha cliff distribution"),
        "Alpha cliff distribution section missing"
    );
    // Table header
    assert!(
        md.contains("Bucket upper"),
        "Histogram column header missing"
    );
    // Fixture has alpha_cliff = 1.87 → bucket [1,2) → label 2.0
    // and alpha_cliff = 0.90 → bucket [0,1) → label 1.0
    // At least one bucket should have slot_count > 0
    assert!(
        md.contains("| 1.0 | 1 |") || md.contains("| 2.0 | 1 |"),
        "Expected histogram bucket with 1 slot not found"
    );
}
