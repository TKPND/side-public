// Plan 01-06 — --fee-sweep + pf_gross/pf_net@2bps_rt/alpha_cliff tests.

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

fn run_scan(extra_args: &[&str]) -> serde_json::Value {
    let tmp = TempDir::new().unwrap();
    let out_path = tmp.path().join("out.json");
    let fixtures = fixtures_dir();
    let mut args: Vec<String> = vec![
        "scan".into(),
        "--asset".into(),
        "USDJPY".into(),
        "--timeframe".into(),
        "1h".into(),
        "--fixture-parquet".into(),
        fixtures
            .join("usdjpy_1h_sample.parquet")
            .to_str()
            .unwrap()
            .to_string(),
        "--edges".into(),
        fixtures
            .join("edges_minimal.json")
            .to_str()
            .unwrap()
            .to_string(),
        "--output".into(),
        out_path.to_str().unwrap().to_string(),
    ];
    for a in extra_args {
        args.push((*a).to_string());
    }
    let status = Command::new(side_cli_binary())
        .args(&args)
        .status()
        .expect("failed to spawn side-cli");
    assert!(status.success(), "side-cli scan failed");
    serde_json::from_str(&std::fs::read_to_string(&out_path).unwrap()).unwrap()
}

#[test]
fn fee_sweep_generates_curve_with_five_points() {
    let json = run_scan(&["--fee-sweep", "0,1,2,3,5"]);
    let curve = &json[0]["fee_curve"];
    let arr = curve.as_array().expect("fee_curve should be array");
    assert_eq!(arr.len(), 5, "expected 5 fee points, got {}", arr.len());
    assert_eq!(curve[0]["fee_bps_rt"].as_f64().unwrap(), 0.0);
    assert_eq!(curve[4]["fee_bps_rt"].as_f64().unwrap(), 5.0);
}

#[test]
fn fee_sweep_derives_pf_gross_pf_net_2bps_alpha_cliff() {
    let json = run_scan(&["--fee-sweep", "0,1,2,3,5"]);
    assert!(json[0].get("pf_gross").is_some(), "pf_gross missing");
    assert!(
        json[0].get("pf_net@2bps_rt").is_some(),
        "pf_net@2bps_rt missing"
    );
    assert!(json[0].get("alpha_cliff").is_some(), "alpha_cliff missing");
    // pf_gross must equal fee_curve[fee_bps_rt == 0].pf.
    let pf_gross = json[0]["pf_gross"].as_f64().unwrap();
    let curve_0 = json[0]["fee_curve"][0]["pf"].as_f64().unwrap();
    assert!(
        (pf_gross - curve_0).abs() < 1e-9,
        "pf_gross ({pf_gross}) != fee_curve[0].pf ({curve_0})"
    );
}

#[test]
fn fee_sweep_without_2bps_sets_pf_net_null() {
    let json = run_scan(&["--fee-sweep", "0,1,3,5"]);
    assert!(
        json[0]["pf_net@2bps_rt"].is_null(),
        "expected pf_net@2bps_rt to be null when 2 is not in sweep"
    );
}

#[test]
fn scan_spread_commission_flags_compute_per_side_fee() {
    // Without --fee-sweep: fee_curve has 1 point at fee_bps_rt = spread + commission = 2.0
    let json = run_scan(&["--spread-bps-rt", "1.5", "--commission-bps-rt", "0.5"]);
    let curve = &json[0]["fee_curve"];
    let arr = curve.as_array().expect("fee_curve should be array");
    assert_eq!(arr.len(), 1, "expected 1 fee point, got {}", arr.len());
    let fee_rt = curve[0]["fee_bps_rt"].as_f64().unwrap();
    assert!(
        (fee_rt - 2.0).abs() < 1e-9,
        "expected fee_bps_rt == 2.0 got {fee_rt}"
    );
}
