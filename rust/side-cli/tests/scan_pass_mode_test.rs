// Plan 02-06 — `side scan --pass-mode` integration tests (VAL-07).
//
// Verifies the 6-gate verdict wiring in the --edges fast path. Uses
// CARGO_BIN_EXE_side which only resolves inside the side-cli crate's tests.

use std::path::PathBuf;
use std::process::Command;

use tempfile::TempDir;

fn side_cli_binary() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_side"))
}

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("side-engine")
        .join("tests")
        .join("fixtures")
}

#[test]
fn scan_pass_mode_strict() {
    let tmp = TempDir::new().unwrap();
    let out = tmp.path().join("strict.json");
    let fixtures = fixtures_dir();
    let status = Command::new(side_cli_binary())
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--edges",
            fixtures.join("edges_minimal.json").to_str().unwrap(),
            "--pass-mode",
            "strict",
            "--output",
            out.to_str().unwrap(),
        ])
        .status()
        .expect("failed to spawn side-cli");
    assert!(
        status.success(),
        "side scan --pass-mode strict exited non-zero"
    );

    let body = std::fs::read_to_string(&out).expect("read output");
    let json: serde_json::Value = serde_json::from_str(&body).expect("parse output");
    let first = json
        .as_array()
        .and_then(|a| a.first())
        .expect("at least 1 slot in fixture");

    let verdict = first
        .get("verdict")
        .expect("verdict key must be present in strict mode");
    let gates = verdict
        .get("gates")
        .and_then(|g| g.as_array())
        .expect("gates array must be present");
    assert_eq!(
        gates.len(),
        6,
        "must have exactly 6 gates, got {}",
        gates.len()
    );
    assert_eq!(
        gates[0].get("gate").and_then(|g| g.as_str()),
        Some("abs_t_stat"),
        "gate[0] must be abs_t_stat"
    );
    // gate[1] = dsr_pvalue, gate[5] = bootstrap_ci_excludes_zero
    assert_eq!(
        gates[1].get("gate").and_then(|g| g.as_str()),
        Some("dsr_pvalue")
    );
    assert_eq!(
        gates[5].get("gate").and_then(|g| g.as_str()),
        Some("bootstrap_ci_excludes_zero")
    );
    // verdict.kind is either "Pass" or "Fail" — both valid for the tiny fixture.
    let kind = verdict
        .get("verdict")
        .and_then(|v| v.get("kind"))
        .and_then(|k| k.as_str())
        .expect("verdict.kind must be present");
    assert!(
        kind == "Pass" || kind == "Fail",
        "kind must be Pass or Fail, got {kind}"
    );
    // relaxed_pass must be absent in strict mode (skip_serializing_if).
    assert!(
        first.get("relaxed_pass").is_none() || first["relaxed_pass"].is_null(),
        "relaxed_pass must be absent in strict mode"
    );
}

#[test]
fn scan_pass_mode_relaxed() {
    let tmp = TempDir::new().unwrap();
    let out = tmp.path().join("relaxed.json");
    let fixtures = fixtures_dir();
    let status = Command::new(side_cli_binary())
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--edges",
            fixtures.join("edges_minimal.json").to_str().unwrap(),
            "--pass-mode",
            "relaxed",
            "--output",
            out.to_str().unwrap(),
        ])
        .status()
        .expect("failed to spawn side-cli");
    assert!(
        status.success(),
        "side scan --pass-mode relaxed exited non-zero"
    );

    let body = std::fs::read_to_string(&out).expect("read output");
    let json: serde_json::Value = serde_json::from_str(&body).expect("parse output");
    let first = json
        .as_array()
        .and_then(|a| a.first())
        .expect("at least 1 slot in fixture");

    // verdict must be absent in relaxed mode (skip_serializing_if).
    assert!(
        first.get("verdict").is_none() || first["verdict"].is_null(),
        "verdict must be absent in relaxed mode"
    );
    // relaxed_pass must be present and a bool.
    let relaxed_pass = first
        .get("relaxed_pass")
        .expect("relaxed_pass key must be present in relaxed mode");
    assert!(
        relaxed_pass.is_boolean(),
        "relaxed_pass must be a boolean, got {relaxed_pass:?}"
    );
}
