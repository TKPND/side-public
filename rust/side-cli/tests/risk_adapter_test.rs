use side_cli::cmd::risk_adapter::{
    build_candidate_batch, build_scan_v2_candidate_for_root, write_candidate_json, RiskCandidate,
    RiskCandidateInput,
};
use side_engine::edges::parse_file;
use std::path::{Path, PathBuf};
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
fn risk_adapter_emits_fixture_candidate_json() {
    let fixtures = fixtures_dir();
    let edges_path = fixtures.join("edges_sample.json");
    let parquet_path = fixtures.join("usdjpy_1h_sample.parquet");
    let edges = parse_file(&edges_path).unwrap();
    let fee_sweep = [2.0];

    let inputs = [
        RiskCandidateInput {
            edge: &edges[0],
            source_edge_index: 0,
            hold_h: 1,
            edges_path: &edges_path,
            fixture_parquet: Some(&parquet_path),
            spread_bps_rt: 1.5,
            commission_bps_rt: 0.5,
            fee_sweep_bps_rt: &fee_sweep,
            tod_spread_curve: false,
        },
        RiskCandidateInput {
            edge: &edges[0],
            source_edge_index: 0,
            hold_h: 3,
            edges_path: &edges_path,
            fixture_parquet: Some(&parquet_path),
            spread_bps_rt: 1.5,
            commission_bps_rt: 0.5,
            fee_sweep_bps_rt: &fee_sweep,
            tod_spread_curve: false,
        },
        RiskCandidateInput {
            edge: &edges[1],
            source_edge_index: 1,
            hold_h: 2,
            edges_path: &edges_path,
            fixture_parquet: Some(&parquet_path),
            spread_bps_rt: 1.5,
            commission_bps_rt: 0.5,
            fee_sweep_bps_rt: &fee_sweep,
            tod_spread_curve: false,
        },
    ];

    let candidates = build_candidate_batch(inputs).unwrap();
    assert_eq!(candidates.len(), 3);
    assert_eq!(
        candidates
            .iter()
            .map(|candidate| candidate.candidate_id.as_str())
            .collect::<Vec<_>>(),
        vec![
            "scan_edges.USDJPY.1h.edge0.m0.long.h1",
            "scan_edges.USDJPY.1h.edge0.m0.long.h3",
            "scan_edges.USDJPY.1m.edge1.m55.short.h2",
        ]
    );

    for candidate in &candidates {
        assert_candidate_contract(candidate);
    }

    let temp_dir = TempDir::new().unwrap();
    let out_path = std::env::var("SIDE_RISK_ADAPTER_CANDIDATE_OUT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| temp_dir.path().join("candidate.json"));
    write_candidate_json(&candidates[0], &out_path).unwrap();

    let json: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&out_path).unwrap()).unwrap();
    assert_eq!(json["strategy_id"], "scan_edges.USDJPY.1h.edge0.m0.long.h1");
    assert_eq!(json["candidate_id"], json["strategy_id"]);
    assert_eq!(
        json["artifact_path"],
        "reports/v5.7/risk_gate/scan_edges.USDJPY.1h.edge0.m0.long.h1.json"
    );
    assert_eq!(json["requested_size"], 1.0);
    assert_eq!(json["requested_size_basis"], "unit_scan_slot");
    assert_eq!(json["scan_params"]["strategy_name"], "tod_edge");
    assert_eq!(json["scan_params"]["params"]["exit_type"], "time_hold");
    assert_eq!(
        json["source_edge"]["hold_h_candidates"],
        serde_json::json!([1, 3])
    );
    assert_eq!(json["fee_refs"]["spread_bps_rt"], 1.5);
    assert_eq!(json["fee_refs"]["commission_bps_rt"], 0.5);
    assert!(json["data_refs"]["edges_path"]
        .as_str()
        .unwrap()
        .contains("edges_sample.json"));
    assert_validation_refs(json["validation_refs"].as_array().unwrap());
    assert_absent(&json, "fee_curve");
    assert_absent(&json, "verdict");
    assert_absent(&json, "relaxed_pass");
    assert_absent(&json, "verdicts_per_fee");
}

#[test]
fn risk_adapter_emits_scan_v2_runtime_candidate_json() {
    let fixtures = fixtures_dir();
    let edges_path = fixtures.join("edges_sample.json");
    let parquet_path = fixtures.join("usdjpy_1h_sample.parquet");
    let edges = parse_file(&edges_path).unwrap();
    let fee_sweep = [2.0];

    let candidate = build_scan_v2_candidate_for_root(
        RiskCandidateInput {
            edge: &edges[0],
            source_edge_index: 0,
            hold_h: 1,
            edges_path: &edges_path,
            fixture_parquet: Some(&parquet_path),
            spread_bps_rt: 1.5,
            commission_bps_rt: 0.5,
            fee_sweep_bps_rt: &fee_sweep,
            tod_spread_curve: false,
        },
        Path::new("reports/risk-contract-v2/scan-runtime-adoption/test"),
    )
    .unwrap();

    assert_eq!(
        candidate.candidate_schema_version,
        "risk_contract.v2.candidate.v1"
    );
    assert_eq!(
        candidate.candidate_id,
        "scan_edges.USDJPY.1h.edge0.m0.long.h1"
    );
    assert_eq!(candidate.strategy_id, candidate.candidate_id);
    assert_eq!(candidate.surface.runtime_surface, "scan");
    assert_eq!(candidate.surface.surface_status, "implemented");
    assert_eq!(candidate.surface.analysis_scope, "none");
    assert_eq!(candidate.surface.analysis_scope_status, "not_applicable");
    assert_eq!(candidate.sizing.requested_size, 1.0);
    assert_eq!(candidate.sizing.requested_size_basis, "unit_scan_slot");
    assert_eq!(
        candidate.artifact_root,
        "reports/risk-contract-v2/scan-runtime-adoption/test"
    );
    assert_eq!(
        candidate.surface_payload.scan_params.strategy_name,
        "tod_edge"
    );
    for expected in [
        "risk/contracts/v2/risk_contract_v2.schema.json",
        "risk/contracts/v2/risk_contract_validator_result_v2.schema.json",
        "scripts/validate_risk_contract.py",
    ] {
        assert!(
            candidate
                .validation_refs
                .iter()
                .any(|value| value == expected),
            "missing validation ref {expected}"
        );
    }
    assert!(
        candidate
            .validation_refs
            .iter()
            .any(|value| value.contains("edges_sample.json")),
        "missing source edges validation ref"
    );
}

#[test]
fn risk_adapter_keeps_normal_scan_edges_output_unchanged() {
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
    let slots = json.as_array().expect("expected normal scan JSON array");
    assert!(!slots.is_empty(), "expected at least one scan slot");
    assert!(
        slots[0].get("fee_curve").is_some(),
        "normal scan output must keep fee_curve"
    );

    for adapter_key in [
        "candidate_id",
        "artifact_path",
        "scan_params",
        "source_edge",
        "fee_refs",
        "data_refs",
        "risk_gate",
    ] {
        assert_absent(&json, adapter_key);
    }
}

fn assert_candidate_contract(candidate: &RiskCandidate) {
    assert_eq!(candidate.strategy_id, candidate.candidate_id);
    assert_eq!(
        candidate.artifact_path,
        format!("reports/v5.7/risk_gate/{}.json", candidate.candidate_id)
    );
    assert_eq!(candidate.requested_size, 1.0);
    assert_eq!(candidate.requested_size_basis, "unit_scan_slot");
    assert_eq!(candidate.scan_params.strategy_name, "tod_edge");
    assert_eq!(candidate.scan_params.params.exit_type, "time_hold");
    assert!(candidate
        .validation_refs
        .contains(&"risk/contracts/v1/risk_contract_v1.schema.json".to_string()));
    assert!(candidate
        .validation_refs
        .contains(&"scripts/validate_risk_contract.py".to_string()));
    assert!(candidate
        .validation_refs
        .contains(&"rust/side-engine/tests/fixtures/edges_sample.json".to_string()));
}

fn assert_validation_refs(refs: &[serde_json::Value]) {
    for expected in [
        "risk/contracts/v1/risk_contract_v1.schema.json",
        "scripts/validate_risk_contract.py",
        "rust/side-engine/tests/fixtures/edges_sample.json",
    ] {
        assert!(
            refs.iter().any(|value| value.as_str() == Some(expected)),
            "missing validation ref {expected}"
        );
    }
}

fn assert_absent(value: &serde_json::Value, key: &str) {
    match value {
        serde_json::Value::Object(map) => {
            assert!(!map.contains_key(key), "unexpected key {key}");
            for nested in map.values() {
                assert_absent(nested, key);
            }
        }
        serde_json::Value::Array(items) => {
            for nested in items {
                assert_absent(nested, key);
            }
        }
        _ => {}
    }
}
