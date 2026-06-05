// Plan 01-06 — --edges fast path integration tests.

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
fn scan_edges_reads_fixture_and_emits_one_result_per_slot() {
    let tmp = TempDir::new().unwrap();
    let out = tmp.path().join("out.json");
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
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--spread-bps-rt",
            "1.5",
            "--commission-bps-rt",
            "0.5",
            "--output",
            out.to_str().unwrap(),
        ])
        .status()
        .expect("failed to spawn side-cli");
    assert!(status.success(), "side-cli scan --edges exited non-zero");
    let json: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    let arr = json.as_array().expect("expected a JSON array");
    // edges_sample.json has 2 edges: [1,3] and [2] → 3 slots total.
    assert_eq!(arr.len(), 3, "expected 3 slots, got {}", arr.len());
    for field in [
        "entry_minute",
        "direction",
        "hold_h",
        "source_query",
        "source_edge_index",
        "fee_curve",
    ] {
        assert!(
            json[0].get(field).is_some(),
            "slot[0] missing field {field}"
        );
    }
}

#[test]
fn scan_edges_rejects_simultaneous_strategies_flag() {
    let tmp = TempDir::new().unwrap();
    let fixtures = fixtures_dir();
    let out = Command::new(side_cli_binary())
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--edges",
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--strategies",
            "tod_edge",
            "--output",
            tmp.path().join("out.json").to_str().unwrap(),
        ])
        .output()
        .expect("failed to spawn side-cli");
    assert!(
        !out.status.success(),
        "side-cli should have rejected --edges + --strategies"
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("mutually exclusive"),
        "stderr was: {stderr}"
    );
}

#[test]
fn scan_edges_metadata_propagates_to_output() {
    let tmp = TempDir::new().unwrap();
    let out = tmp.path().join("out.json");
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
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--spread-bps-rt",
            "1.5",
            "--commission-bps-rt",
            "0.5",
            "--output",
            out.to_str().unwrap(),
        ])
        .status()
        .expect("failed to spawn side-cli");
    assert!(status.success());
    let json: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    assert_eq!(json[0]["source_edge_index"], 0);
    assert_eq!(json[0]["source_query"], "bq_usdjpy_directional_windows.sql");
}

#[test]
fn scan_edges_fee_sweep_entry_count() {
    let tmp = TempDir::new().unwrap();
    let fixtures = fixtures_dir();

    // 3-value sweep → fee_curve should have 3 entries
    let out3 = tmp.path().join("out3.json");
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
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--fee-sweep",
            "0,2,5",
            "--output",
            out3.to_str().unwrap(),
        ])
        .status()
        .expect("failed to spawn side-cli");
    assert!(status.success(), "side-cli scan --fee-sweep 0,2,5 failed");
    let json: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&out3).unwrap()).unwrap();
    let curve3 = json[0]["fee_curve"]
        .as_array()
        .expect("fee_curve must be array");
    assert_eq!(
        curve3.len(),
        3,
        "--fee-sweep 0,2,5 → fee_curve should have 3 entries, got {}",
        curve3.len()
    );

    // 6-value sweep → fee_curve should have 6 entries
    let out6 = tmp.path().join("out6.json");
    let status6 = Command::new(side_cli_binary())
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--edges",
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--fee-sweep",
            "0,1,2,3,5,10",
            "--output",
            out6.to_str().unwrap(),
        ])
        .status()
        .expect("failed to spawn side-cli");
    assert!(
        status6.success(),
        "side-cli scan --fee-sweep 0,1,2,3,5,10 failed"
    );
    let json6: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&out6).unwrap()).unwrap();
    let curve6 = json6[0]["fee_curve"]
        .as_array()
        .expect("fee_curve must be array");
    assert_eq!(
        curve6.len(),
        6,
        "--fee-sweep 0,1,2,3,5,10 → fee_curve should have 6 entries, got {}",
        curve6.len()
    );
}

#[test]
fn scan_edges_pass_mode_strict_verdict_field() {
    let tmp = TempDir::new().unwrap();
    let out = tmp.path().join("out.json");
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
            fixtures.join("edges_sample.json").to_str().unwrap(),
            "--fee-sweep",
            "1",
            "--pass-mode",
            "strict",
            "--output",
            out.to_str().unwrap(),
        ])
        .status()
        .expect("failed to spawn side-cli");
    assert!(status.success(), "side-cli scan --pass-mode strict failed");
    let json: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&out).unwrap()).unwrap();
    // With --pass-mode strict, each slot must contain a "verdict" object
    assert!(
        json[0].get("verdict").is_some(),
        "slot[0] missing 'verdict' field with --pass-mode strict"
    );
    // The verdict object must contain a nested "verdict" field (VerdictKind with "kind" tag)
    let verdict_obj = &json[0]["verdict"];
    assert!(
        verdict_obj.get("verdict").is_some(),
        "verdict object missing inner 'verdict' (VerdictKind) field; got: {}",
        verdict_obj
    );
    // The inner verdict must have a "kind" key of "Pass" or "Fail"
    let kind = verdict_obj["verdict"]["kind"]
        .as_str()
        .expect("verdict.verdict.kind must be a string");
    assert!(
        kind == "Pass" || kind == "Fail",
        "verdict.verdict.kind must be 'Pass' or 'Fail', got '{kind}'"
    );
}

#[test]
fn scan_tod_curve_requires_edges_flag() {
    let tmp = TempDir::new().unwrap();
    let fixtures = fixtures_dir();
    let out = Command::new(side_cli_binary())
        .args([
            "scan",
            "--asset",
            "USDJPY",
            "--timeframe",
            "1h",
            "--fixture-parquet",
            fixtures.join("usdjpy_1h_sample.parquet").to_str().unwrap(),
            "--tod-spread-curve",
            "--output",
            tmp.path().join("out.json").to_str().unwrap(),
        ])
        .output()
        .expect("failed to spawn side-cli");
    assert!(
        !out.status.success(),
        "side-cli should have rejected --tod-spread-curve without --edges"
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("--tod-spread-curve requires --edges"),
        "stderr was: {stderr}"
    );
}
