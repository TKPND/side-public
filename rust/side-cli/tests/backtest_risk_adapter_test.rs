use std::path::Path;

use serde_json::json;
use side_cli::cmd::backtest_contract::parse_tod_edge_params_json;
use side_cli::cmd::backtest_risk_adapter::{
    backtest_candidate_id, build_backtest_candidate, build_backtest_candidate_batch,
    build_backtest_v2_candidate, candidate_staging_path, canonical_json_bytes,
    validate_artifact_root, validate_backtest_candidate_component,
    validate_backtest_v2_artifact_root, write_backtest_candidate_json, BacktestCandidateInput,
    BACKTEST_CANDIDATE_SCHEMA_VERSION, BACKTEST_V2_CANDIDATE_SCHEMA_VERSION,
};
use tempfile::TempDir;

const DATA_REF: &str = "rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet";
const DATA_FINGERPRINT: &str =
    "sha256:14def03e6037df2108b4c0faba9da0a71306bc8ff5259541bfeff9b8f24dd0b0";
const ARTIFACT_ROOT: &str = "reports/v5.8/risk_gate";

fn input_with<'a>(
    params: &'a side_cli::cmd::backtest_contract::TodEdgeParams,
    fee_bps: f64,
    data_ref: &'a str,
    data_fingerprint: &'a str,
    artifact_root: &'a str,
) -> BacktestCandidateInput<'a> {
    BacktestCandidateInput {
        asset: "USDJPY",
        timeframe: "1h",
        strategy: "tod_edge",
        params,
        fee_bps,
        data_ref,
        data_fingerprint,
        artifact_root,
    }
}

fn sample_params() -> side_cli::cmd::backtest_contract::TodEdgeParams {
    parse_tod_edge_params_json(r#"{"entry_minute":0,"direction":"long","hold_h":3}"#).unwrap()
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

#[test]
fn candidate_id_is_stable_across_param_key_order() {
    let params_a =
        parse_tod_edge_params_json(r#"{"entry_minute":0,"direction":"long","hold_h":3}"#).unwrap();
    let params_b =
        parse_tod_edge_params_json(r#"{"hold_h":3,"direction":"long","entry_minute":0}"#).unwrap();

    let id_a = backtest_candidate_id(&input_with(
        &params_a,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    let id_b = backtest_candidate_id(&input_with(
        &params_b,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();

    assert_eq!(id_a, id_b);
    assert!(id_a.starts_with("backtest.USDJPY.1h.tod_edge.p"));
    assert_eq!(id_a.rsplit_once('p').unwrap().1.len(), 12);
}

#[test]
fn candidate_id_changes_for_fee_bps() {
    let params = sample_params();
    let id_a = backtest_candidate_id(&input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    let id_b = backtest_candidate_id(&input_with(
        &params,
        2.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    assert_ne!(id_a, id_b);
}

#[test]
fn candidate_id_changes_for_data_fingerprint() {
    let params = sample_params();
    let id_a = backtest_candidate_id(&input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    let id_b = backtest_candidate_id(&input_with(
        &params,
        1.0,
        DATA_REF,
        "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        ARTIFACT_ROOT,
    ))
    .unwrap();
    assert_ne!(id_a, id_b);
}

#[test]
fn candidate_id_ignores_data_ref_path() {
    let params = sample_params();
    let id_a = backtest_candidate_id(&input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    let id_b = backtest_candidate_id(&input_with(
        &params,
        1.0,
        "/tmp/different/usdjpy_1h_sample.parquet",
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    assert_eq!(id_a, id_b);
}

#[test]
fn candidate_id_canonical_json_bytes_are_sorted_and_compact() {
    let bytes = canonical_json_bytes(&json!({
        "z": 2,
        "a": 1,
        "nested": {
            "z": "last",
            "a": null
        }
    }))
    .unwrap();
    assert_eq!(
        String::from_utf8(bytes).unwrap(),
        r#"{"a":1,"nested":{"a":null,"z":"last"},"z":2}"#
    );
}

#[test]
fn candidate_id_canonical_json_bytes_preserve_string_number_and_null_representation() {
    let bytes = canonical_json_bytes(&json!({
        "s": "line\nquote\"",
        "n": 1.5,
        "z": null
    }))
    .unwrap();
    assert_eq!(
        String::from_utf8(bytes).unwrap(),
        r#"{"n":1.5,"s":"line\nquote\"","z":null}"#
    );
}

#[test]
fn candidate_contract_contains_backtest_fields() {
    let params = sample_params();
    let candidate = build_backtest_candidate(input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    let value = serde_json::to_value(&candidate).unwrap();

    assert_eq!(
        value["candidate_schema_version"],
        BACKTEST_CANDIDATE_SCHEMA_VERSION
    );
    assert_eq!(value["strategy_id"], value["candidate_id"]);
    assert_eq!(value["symbol_or_universe"], "USDJPY");
    assert_eq!(value["timeframe"], "1h");
    assert_eq!(value["requested_size"], 1.0);
    assert_eq!(value["requested_size_basis"], "unit_backtest_run");
    assert_eq!(value["backtest_params"]["entry_minute"], 0);
    assert_eq!(value["backtest_params"]["direction"], "long");
    assert_eq!(value["backtest_params"]["hold_h"], 3);
    assert_eq!(value["backtest_params"]["strategy"], "tod_edge");
    assert_eq!(value["backtest_params"]["fee_bps"], 1.0);
    assert_eq!(value["backtest_params"]["data_ref"], DATA_REF);
    assert_eq!(
        value["backtest_params"]["data_fingerprint"],
        DATA_FINGERPRINT
    );
    assert_eq!(value["artifact_root"], ARTIFACT_ROOT);
}

#[test]
fn backtest_risk_adapter_smoke_builds_candidate() {
    let params = sample_params();
    let candidate = build_backtest_candidate(input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    assert!(candidate
        .candidate_id
        .starts_with("backtest.USDJPY.1h.tod_edge.p"));
}

#[test]
fn candidate_contract_omits_decision_artifact_path() {
    let params = sample_params();
    let candidate = build_backtest_candidate(input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    let value = serde_json::to_value(&candidate).unwrap();
    for key in [
        "artifact_path",
        "source_edge",
        "fee_refs",
        "scan_params",
        "fee_curve",
        "verdict",
        "relaxed_pass",
        "verdicts_per_fee",
    ] {
        assert_absent(&value, key);
    }
}

#[test]
fn candidate_contract_json_written_with_trailing_newline() {
    let params = sample_params();
    let candidate = build_backtest_candidate(input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    let tmp = TempDir::new().unwrap();
    let path = tmp.path().join("candidate.json");
    write_backtest_candidate_json(&candidate, &path).unwrap();

    let contents = std::fs::read_to_string(path).unwrap();
    assert!(contents.ends_with('\n'));
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&contents).unwrap()["candidate_id"],
        candidate.candidate_id
    );
}

#[test]
fn candidate_contract_write_rejects_existing_file_without_overwrite() {
    let params = sample_params();
    let candidate = build_backtest_candidate(input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    let tmp = TempDir::new().unwrap();
    let path = tmp.path().join("candidate.json");
    std::fs::write(&path, b"sentinel\n").unwrap();

    let err = write_backtest_candidate_json(&candidate, &path).unwrap_err();

    assert!(format!("{err:#}").contains("backtest risk candidate artifact already exists"));
    assert_eq!(std::fs::read(&path).unwrap(), b"sentinel\n");
}

#[test]
fn candidate_contract_validation_refs_include_schema_validator_data_and_fingerprint() {
    let params = sample_params();
    let candidate = build_backtest_candidate(input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        ARTIFACT_ROOT,
    ))
    .unwrap();
    for expected in [
        "risk/contracts/v1/risk_contract_v1.schema.json",
        "scripts/validate_risk_contract.py",
        DATA_REF,
        DATA_FINGERPRINT,
    ] {
        assert!(
            candidate
                .validation_refs
                .iter()
                .any(|actual| actual == expected),
            "missing validation ref {expected}"
        );
    }
}

#[test]
fn v2_candidate_contract_contains_backtest_surface_sizing_and_refs() {
    let params = sample_params();
    let candidate = build_backtest_v2_candidate(input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        "target/test-risk-contract-v2",
    ))
    .unwrap();
    let value = serde_json::to_value(&candidate).unwrap();

    assert_eq!(
        value["candidate_schema_version"],
        BACKTEST_V2_CANDIDATE_SCHEMA_VERSION
    );
    assert_eq!(value["strategy_id"], value["candidate_id"]);
    assert_eq!(value["surface"]["runtime_surface"], "backtest");
    assert_eq!(value["surface"]["surface_status"], "implemented");
    assert_eq!(value["surface"]["analysis_scope"], "none");
    assert_eq!(value["surface"]["analysis_scope_status"], "not_applicable");
    assert_eq!(value["sizing"]["requested_size"], 1.0);
    assert_eq!(value["sizing"]["requested_size_basis"], "unit_backtest_run");
    assert_eq!(
        value["surface_payload"]["backtest_params"]["strategy"],
        "tod_edge"
    );
    assert_eq!(value["surface_payload"]["backtest_params"]["fee_bps"], 1.0);
    assert!(value["validation_refs"]
        .as_array()
        .unwrap()
        .iter()
        .any(|actual| actual.as_str() == Some("risk/contracts/v2/risk_contract_v2.schema.json")));
    assert!(value["validation_refs"]
        .as_array()
        .unwrap()
        .iter()
        .any(|actual| {
            actual.as_str()
                == Some("risk/contracts/v2/risk_contract_validator_result_v2.schema.json")
        }));
}

#[test]
fn v2_candidate_id_is_distinct_from_v1_candidate_id() {
    let params = sample_params();
    let input = input_with(
        &params,
        1.0,
        DATA_REF,
        DATA_FINGERPRINT,
        "target/test-risk-contract-v2",
    );
    let v1_id = backtest_candidate_id(&input).unwrap();
    let v2_id = build_backtest_v2_candidate(input).unwrap().candidate_id;

    assert_ne!(v1_id, v2_id);
    assert!(v2_id.starts_with("backtest.USDJPY.1h.tod_edge.p"));
}

#[test]
fn adapter_rejects_unsafe_candidate_components() {
    for value in [
        "../USDJPY",
        "USD/JPY",
        "",
        ".",
        "..",
        "USD\\JPY",
        "USD\0JPY",
    ] {
        let err = validate_backtest_candidate_component("asset", value).unwrap_err();
        assert!(format!("{err:#}").contains("unsafe candidate component"));
    }
}

#[test]
fn adapter_rejects_unsafe_artifact_roots() {
    for root in [
        "/tmp/side-risk-gate",
        "reports/../risk_gate",
        "reports\\risk_gate",
        "",
        ".",
    ] {
        let err = validate_artifact_root(root).unwrap_err();
        assert!(format!("{err:#}").contains("unsafe artifact_root"));
    }
    validate_artifact_root("reports/v5.8/risk_gate").unwrap();
}

#[test]
fn v2_adapter_rejects_protected_artifact_roots() {
    for root in [
        "reports/v5.7/risk_gate",
        "reports/v5.8/risk_gate",
        "reports/v4.6/risk_gate",
        "reports/v8.3/risk_gate",
        ".planning/milestones/v4/archive",
        "docs/reports/v4/archive",
        "data/v4/archive",
        "risk/contracts/v2/runtime",
    ] {
        let err = validate_backtest_v2_artifact_root(root).unwrap_err();
        assert!(format!("{err:#}").contains("unsafe v2 artifact_root"));
    }
    validate_backtest_v2_artifact_root("target/test-risk-contract-v2").unwrap();
}

#[test]
fn adapter_rejects_duplicate_candidate_ids() {
    let params = sample_params();
    let err = build_backtest_candidate_batch([
        input_with(
            &params,
            1.0,
            DATA_REF,
            DATA_FINGERPRINT,
            "reports/v5.8/risk_gate/a",
        ),
        input_with(
            &params,
            1.0,
            DATA_REF,
            DATA_FINGERPRINT,
            "reports/v5.8/risk_gate/b",
        ),
    ])
    .unwrap_err();
    assert!(format!("{err:#}").contains("duplicate candidate_id"));
}

#[test]
fn adapter_rejects_duplicate_staging_outputs() {
    let params = sample_params();
    let err = build_backtest_candidate_batch([
        input_with(&params, 1.0, DATA_REF, DATA_FINGERPRINT, ARTIFACT_ROOT),
        input_with(&params, 1.0, DATA_REF, DATA_FINGERPRINT, ARTIFACT_ROOT),
    ])
    .unwrap_err();
    assert!(format!("{err:#}").contains("duplicate candidate staging output"));
}

#[test]
fn adapter_rejects_candidate_staging_path_rejects_unsafe_candidate_id() {
    let err = candidate_staging_path(Path::new(ARTIFACT_ROOT), "../evil").unwrap_err();
    assert!(format!("{err:#}").contains("unsafe candidate component"));
}
