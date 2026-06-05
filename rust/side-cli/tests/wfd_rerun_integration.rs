//! Integration tests for `side wfd-rerun` CLI (Phase 70 Plan 03).
//!
//! These tests spawn the compiled `side` binary via `CARGO_BIN_EXE_side-cli`,
//! invoke `wfd-rerun --pair usdjpy --event fomc --output-dir <tempdir>`, and
//! assert the shape of the resulting `report.json`:
//!
//! 1. `wfd_rerun_report_stamps_provenance` — `data_provenance` matches
//!    `^fresh-wfd-rerun-\d{4}-\d{2}-\d{2}-[0-9a-f]{7,}(-dirty)?$`.
//! 2. `wfd_rerun_report_stamps_grid` — `grid_provenance.window_offsets`
//!    equals `WINDOW_OFFSETS` (runtime-derived; Phase 76: [1..=16]);
//!    `hold_bars_values` equals `HOLD_BARS_VALUES` ([1,2,3,6,12,24]);
//!    `exit_types` equals `EXIT_TYPES` (["none","fixed_pct"]).
//! 3. `wfd_rerun_report_has_expected_slot_count` —
//!    `slots.len() == WINDOW_OFFSETS.len() × HOLD_BARS_VALUES.len() × EXIT_TYPES.len()`
//!    (Phase 76 D-09: runtime-derived, no numeric hardcode).
//!
//! The mirror CSV at `rust/data/mirror/USDJPY_1h_2022_2023.csv` is a
//! precondition (Plan 02 deliverable). These tests fail fast with a clear
//! message if it is missing.

use std::path::{Path, PathBuf};
use std::process::Command;

use regex::Regex;
use serde_json::Value;
use side_engine::scanner::macro_event::{EXIT_TYPES, HOLD_BARS_VALUES, WINDOW_OFFSETS};
use tempfile::TempDir;

/// Path to the `side` binary compiled by cargo for this test.
fn side_binary() -> &'static str {
    env!("CARGO_BIN_EXE_side")
}

/// Resolve the repository root (the directory containing the `rust/` workspace).
///
/// Cargo sets `CARGO_MANIFEST_DIR` to `<repo>/rust/side-cli`; the repo root is
/// two levels up.
fn repo_root() -> PathBuf {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    PathBuf::from(manifest_dir)
        .parent()
        .and_then(|p| p.parent())
        .expect("CARGO_MANIFEST_DIR has a grandparent")
        .to_path_buf()
}

/// Run `side wfd-rerun --pair usdjpy --event fomc --output-dir <tmp>` from the
/// repo root (so the default mirror CSV path resolves correctly) and return
/// the parsed `report.json`.
fn run_wfd_rerun_usdjpy_fomc() -> (TempDir, Value) {
    let root = repo_root();
    let mirror_csv = root.join("rust/data/mirror/USDJPY_1h_2022_2023.csv");
    assert!(
        mirror_csv.exists(),
        "precondition missing: {} (Plan 02 deliverable)",
        mirror_csv.display()
    );

    let tmp = tempfile::tempdir().expect("tempdir");
    let output_dir = tmp.path().to_string_lossy().into_owned();

    let output = Command::new(side_binary())
        .current_dir(&root)
        .args([
            "wfd-rerun",
            "--pair",
            "usdjpy",
            "--event",
            "fomc",
            "--output-dir",
            &output_dir,
        ])
        .output()
        .expect("spawn side wfd-rerun");

    assert!(
        output.status.success(),
        "side wfd-rerun exited non-zero (status={:?}).\nstdout:\n{}\nstderr:\n{}",
        output.status,
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );

    let report_path = tmp.path().join("usdjpy").join("fomc").join("report.json");
    let text = std::fs::read_to_string(&report_path).unwrap_or_else(|e| {
        panic!(
            "failed to read report.json at {}: {e}\nstdout:\n{}\nstderr:\n{}",
            report_path.display(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        )
    });
    let value: Value = serde_json::from_str(&text).expect("report.json is valid JSON");
    (tmp, value)
}

/// Guard: only run the full suite once the binary is built. Called from each
/// test (cheap — it just returns a pre-cached path).
fn assert_binary_exists() {
    let bin = Path::new(side_binary());
    assert!(bin.exists(), "side binary not built: {}", bin.display());
}

#[test]
fn wfd_rerun_default_output_stages_outside_protected_v46_archive() {
    assert_binary_exists();
    let output = Command::new(side_binary())
        .current_dir(repo_root())
        .args(["wfd-rerun", "--help"])
        .output()
        .expect("spawn side wfd-rerun --help");

    assert!(
        output.status.success(),
        "side wfd-rerun --help exited non-zero (status={:?}).\nstdout:\n{}\nstderr:\n{}",
        output.status,
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("target/wfd-rerun"),
        "default output should stage under target/wfd-rerun, help was:\n{stdout}"
    );
    assert!(
        !stdout.contains("docs/reports/v4.6-verdict-resolution"),
        "default output must not point at the protected v4.6 archive, help was:\n{stdout}"
    );
}

#[test]
fn wfd_rerun_rejects_protected_v46_output_without_explicit_flag() {
    assert_binary_exists();
    let root = repo_root();
    let mirror_csv = root.join("rust/data/mirror/USDJPY_1h_2022_2023.csv");
    assert!(
        mirror_csv.exists(),
        "precondition missing: {} (Plan 02 deliverable)",
        mirror_csv.display()
    );

    let tmp = tempfile::tempdir().expect("tempdir");
    let protected_like_output = tmp
        .path()
        .join("docs/reports/v4.6-verdict-resolution/per-pair");
    let output_dir = protected_like_output.to_string_lossy().into_owned();

    let output = Command::new(side_binary())
        .current_dir(&root)
        .args([
            "wfd-rerun",
            "--pair",
            "usdjpy",
            "--event",
            "fomc",
            "--output-dir",
            &output_dir,
        ])
        .output()
        .expect("spawn side wfd-rerun with protected-like output dir");

    assert!(
        !output.status.success(),
        "side wfd-rerun must reject protected v4.6 output without an explicit override.\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("protected v4.6 report archive"),
        "stderr should explain the protected output guard, got:\n{stderr}"
    );
    assert!(
        !protected_like_output
            .join("usdjpy")
            .join("fomc")
            .join("report.json")
            .exists(),
        "protected output guard must fail before writing report.json"
    );
}

#[test]
fn wfd_rerun_rejects_parent_dir_bypass_to_protected_v46_output() {
    assert_binary_exists();
    let root = repo_root();
    let tmp = tempfile::tempdir().expect("tempdir");
    let protected_like_output = tmp
        .path()
        .join("docs/reports/not-v46/../v4.6-verdict-resolution/per-pair");
    let output_dir = protected_like_output.to_string_lossy().into_owned();
    let missing_csv = tmp
        .path()
        .join("missing.csv")
        .to_string_lossy()
        .into_owned();

    let output = Command::new(side_binary())
        .current_dir(&root)
        .args([
            "wfd-rerun",
            "--pair",
            "usdjpy",
            "--event",
            "fomc",
            "--output-dir",
            &output_dir,
            "--tick-csv-glob",
            &missing_csv,
        ])
        .output()
        .expect("spawn side wfd-rerun with parent-dir protected-like output dir");

    assert!(
        !output.status.success(),
        "side wfd-rerun must reject parent-dir protected v4.6 output without an explicit override.\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("protected v4.6 report archive"),
        "stderr should come from the output guard, not later CSV loading, got:\n{stderr}"
    );
}

#[cfg(unix)]
#[test]
fn wfd_rerun_rejects_symlink_alias_to_protected_v46_output() {
    use std::os::unix::fs::symlink;

    assert_binary_exists();
    let root = repo_root();
    let tmp = tempfile::tempdir().expect("tempdir");
    let protected_root = tmp.path().join("docs/reports/v4.6-verdict-resolution");
    std::fs::create_dir_all(&protected_root).expect("create protected-like root");
    let alias = tmp.path().join("alias-to-v46");
    symlink(&protected_root, &alias).expect("create symlink alias to protected-like root");
    let output_dir = alias.join("per-pair").to_string_lossy().into_owned();
    let missing_csv = tmp
        .path()
        .join("missing.csv")
        .to_string_lossy()
        .into_owned();

    let output = Command::new(side_binary())
        .current_dir(&root)
        .args([
            "wfd-rerun",
            "--pair",
            "usdjpy",
            "--event",
            "fomc",
            "--output-dir",
            &output_dir,
            "--tick-csv-glob",
            &missing_csv,
        ])
        .output()
        .expect("spawn side wfd-rerun with symlink alias protected-like output dir");

    assert!(
        !output.status.success(),
        "side wfd-rerun must reject symlink aliases to protected v4.6 output without an explicit override.\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("protected v4.6 report archive"),
        "stderr should come from the output guard, not later CSV loading, got:\n{stderr}"
    );
}

#[cfg(unix)]
#[test]
fn wfd_rerun_rejects_pair_symlink_under_allowed_output_root_to_protected_v46_output() {
    use std::os::unix::fs::symlink;

    assert_binary_exists();
    let root = repo_root();
    let tmp = tempfile::tempdir().expect("tempdir");
    let protected_pair = tmp
        .path()
        .join("docs/reports/v4.6-verdict-resolution/per-pair/usdjpy");
    std::fs::create_dir_all(&protected_pair).expect("create protected-like pair root");
    let output_root = tmp.path().join("staging");
    std::fs::create_dir_all(&output_root).expect("create staging output root");
    symlink(&protected_pair, output_root.join("usdjpy"))
        .expect("create pair symlink under allowed output root");
    let output_dir = output_root.to_string_lossy().into_owned();
    let missing_csv = tmp
        .path()
        .join("missing.csv")
        .to_string_lossy()
        .into_owned();

    let output = Command::new(side_binary())
        .current_dir(&root)
        .args([
            "wfd-rerun",
            "--pair",
            "usdjpy",
            "--event",
            "fomc",
            "--output-dir",
            &output_dir,
            "--tick-csv-glob",
            &missing_csv,
        ])
        .output()
        .expect("spawn side wfd-rerun with pair symlink under output root");

    assert!(
        !output.status.success(),
        "side wfd-rerun must reject pair symlinks that target protected v4.6 output.\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("protected v4.6 report archive"),
        "stderr should come from the output guard, not later CSV loading, got:\n{stderr}"
    );
}

#[cfg(unix)]
#[test]
fn wfd_rerun_rejects_report_symlink_under_allowed_output_root_to_protected_v46_output() {
    use std::os::unix::fs::symlink;

    assert_binary_exists();
    let root = repo_root();
    let tmp = tempfile::tempdir().expect("tempdir");
    let protected_report = tmp
        .path()
        .join("docs/reports/v4.6-verdict-resolution/per-pair/usdjpy/fomc/report.json");
    std::fs::create_dir_all(protected_report.parent().expect("protected report parent"))
        .expect("create protected-like report parent");
    std::fs::write(
        &protected_report,
        "protected golden must not be overwritten",
    )
    .expect("seed protected-like report file");
    let output_event = tmp.path().join("staging/usdjpy/fomc");
    std::fs::create_dir_all(&output_event).expect("create staging output event dir");
    symlink(&protected_report, output_event.join("report.json"))
        .expect("create report.json symlink under allowed output root");
    let output_dir = tmp.path().join("staging").to_string_lossy().into_owned();
    let missing_csv = tmp
        .path()
        .join("missing.csv")
        .to_string_lossy()
        .into_owned();

    let output = Command::new(side_binary())
        .current_dir(&root)
        .args([
            "wfd-rerun",
            "--pair",
            "usdjpy",
            "--event",
            "fomc",
            "--output-dir",
            &output_dir,
            "--tick-csv-glob",
            &missing_csv,
        ])
        .output()
        .expect("spawn side wfd-rerun with report symlink under output root");

    assert!(
        !output.status.success(),
        "side wfd-rerun must reject report.json symlinks that target protected v4.6 output.\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("protected v4.6 report archive"),
        "stderr should come from the output guard, not later CSV loading, got:\n{stderr}"
    );
    assert_eq!(
        std::fs::read_to_string(&protected_report).expect("read protected-like report"),
        "protected golden must not be overwritten",
        "protected-like report symlink target must remain untouched"
    );
}

#[cfg(unix)]
#[test]
fn wfd_rerun_rejects_dangling_report_symlink_under_allowed_output_root_to_protected_v46_output() {
    use std::os::unix::fs::symlink;

    assert_binary_exists();
    let root = repo_root();
    let tmp = tempfile::tempdir().expect("tempdir");
    let protected_report = tmp
        .path()
        .join("docs/reports/v4.6-verdict-resolution/per-pair/usdjpy/fomc/report.json");
    std::fs::create_dir_all(protected_report.parent().expect("protected report parent"))
        .expect("create protected-like report parent");
    assert!(
        !protected_report.exists(),
        "precondition: protected-like report target must be absent"
    );
    let output_event = tmp.path().join("staging/usdjpy/fomc");
    std::fs::create_dir_all(&output_event).expect("create staging output event dir");
    symlink(&protected_report, output_event.join("report.json"))
        .expect("create dangling report.json symlink under allowed output root");
    let output_dir = tmp.path().join("staging").to_string_lossy().into_owned();
    let missing_csv = tmp
        .path()
        .join("missing.csv")
        .to_string_lossy()
        .into_owned();

    let output = Command::new(side_binary())
        .current_dir(&root)
        .args([
            "wfd-rerun",
            "--pair",
            "usdjpy",
            "--event",
            "fomc",
            "--output-dir",
            &output_dir,
            "--tick-csv-glob",
            &missing_csv,
        ])
        .output()
        .expect("spawn side wfd-rerun with dangling report symlink under output root");

    assert!(
        !output.status.success(),
        "side wfd-rerun must reject dangling report.json symlinks that target protected v4.6 output.\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("protected v4.6 report archive"),
        "stderr should come from the output guard, not later CSV loading, got:\n{stderr}"
    );
    assert!(
        !protected_report.exists(),
        "protected-like dangling report target must remain absent"
    );
}

#[test]
fn wfd_rerun_report_stamps_provenance() {
    assert_binary_exists();
    let (_tmp, v) = run_wfd_rerun_usdjpy_fomc();
    let dp = v["data_provenance"]
        .as_str()
        .expect("data_provenance is a string");
    let re = Regex::new(r"^fresh-wfd-rerun-\d{4}-\d{2}-\d{2}-[0-9a-f]{7,}(-dirty)?$")
        .expect("regex compiles");
    assert!(re.is_match(dp), "data_provenance={dp} did not match regex");
}

#[test]
fn wfd_rerun_report_stamps_grid() {
    assert_binary_exists();
    let (_tmp, v) = run_wfd_rerun_usdjpy_fomc();
    let grid = &v["grid_provenance"];

    let window_offsets: Vec<u64> = grid["window_offsets"]
        .as_array()
        .expect("window_offsets is an array")
        .iter()
        .map(|x| x.as_u64().expect("window_offsets entry is u64"))
        .collect();
    let expected_window_offsets: Vec<u64> = WINDOW_OFFSETS.iter().map(|&v| v as u64).collect();
    assert_eq!(
        window_offsets, expected_window_offsets,
        "window_offsets must match WINDOW_OFFSETS const (runtime-derived per Phase 76 D-09)",
    );

    let hold_bars_values: Vec<u64> = grid["hold_bars_values"]
        .as_array()
        .expect("hold_bars_values is an array")
        .iter()
        .map(|x| x.as_u64().expect("hold_bars_values entry is u64"))
        .collect();
    assert_eq!(
        hold_bars_values,
        vec![1u64, 2, 3, 6, 12, 24],
        "hold_bars_values must match HOLD_BARS_VALUES const",
    );

    let exit_types: Vec<String> = grid["exit_types"]
        .as_array()
        .expect("exit_types is an array")
        .iter()
        .map(|x| {
            x.as_str()
                .expect("exit_types entry is a string")
                .to_string()
        })
        .collect();
    assert_eq!(
        exit_types,
        vec!["none".to_string(), "fixed_pct".to_string()],
        "exit_types must match EXIT_TYPES const",
    );
}

#[test]
fn wfd_rerun_report_has_expected_slot_count() {
    assert_binary_exists();
    let (_tmp, v) = run_wfd_rerun_usdjpy_fomc();
    let slots = v["slots"].as_array().expect("slots is an array");
    let expected = WINDOW_OFFSETS.len() * HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
    assert_eq!(
        slots.len(),
        expected,
        "slots must have exactly {expected} entries \
         (runtime-derived: WINDOW_OFFSETS × HOLD_BARS_VALUES × EXIT_TYPES per Phase 76 D-09)",
    );
}
