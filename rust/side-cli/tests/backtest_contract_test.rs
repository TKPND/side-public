use std::collections::HashSet;
use std::path::PathBuf;

use serde_json::json;
use side_cli::cmd::backtest_contract::{
    backtest_runtime_sizing_for_summary, data_fingerprint, gated_completed_output,
    gated_stopped_output, metric_json_value, parse_tod_edge_params_json, tod_edge_strategy_params,
    ungated_completed_output, BACKTEST_RESULT_SCHEMA_VERSION,
};
use side_cli::cmd::risk_gate::RiskGateSummary;
use side_engine::backtest::BacktestResult;

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("side-engine")
        .join("tests")
        .join("fixtures")
}

#[test]
fn backtest_contract_parses_tod_edge_params_exactly() {
    let params =
        parse_tod_edge_params_json(r#"{"entry_minute":0,"direction":"long","hold_h":3}"#).unwrap();
    assert_eq!(params.entry_minute, 0);
    assert_eq!(params.direction, "long");
    assert_eq!(params.hold_h, 3);
}

#[test]
fn backtest_contract_rejects_invalid_tod_edge_params() {
    for (input, expected) in [
        ("{", "params"),
        ("[]", "params"),
        (r#"{"direction":"long","hold_h":3}"#, "entry_minute"),
        (r#"{"entry_minute":0,"hold_h":3}"#, "direction"),
        (r#"{"entry_minute":0,"direction":"long"}"#, "hold_h"),
        (
            r#"{"entry_minute":0,"direction":"long","hold_h":3,"news_blackout":true}"#,
            "unknown",
        ),
        (
            r#"{"entry_minute":0.5,"direction":"long","hold_h":3}"#,
            "entry_minute",
        ),
        (
            r#"{"entry_minute":1440,"direction":"long","hold_h":3}"#,
            "entry_minute",
        ),
        (
            r#"{"entry_minute":0,"direction":"flat","hold_h":3}"#,
            "direction",
        ),
        (
            r#"{"entry_minute":0,"direction":"long","hold_h":0}"#,
            "hold_h",
        ),
        (
            r#"{"entry_minute":0,"direction":"long","hold_h":10}"#,
            "hold_h",
        ),
        (
            r#"{"entry_minute":0,"direction":"long","hold_h":3.5}"#,
            "hold_h",
        ),
    ] {
        let err = parse_tod_edge_params_json(input).unwrap_err();
        assert!(
            format!("{err:#}").contains(expected),
            "expected {expected:?} in error for input {input}, got {err:#}"
        );
    }
}

#[test]
fn backtest_contract_tod_edge_strategy_params_has_exact_keys() {
    let params =
        parse_tod_edge_params_json(r#"{"entry_minute":0,"direction":"short","hold_h":9}"#).unwrap();
    let strategy_params = tod_edge_strategy_params(&params);
    let keys = strategy_params
        .keys()
        .map(String::as_str)
        .collect::<HashSet<_>>();

    assert_eq!(keys, HashSet::from(["entry_minute", "direction", "hold_h"]));
    assert_eq!(strategy_params["entry_minute"], json!(0));
    assert_eq!(strategy_params["direction"], json!("short"));
    assert_eq!(strategy_params["hold_h"], json!(9));
}

#[test]
fn backtest_contract_finite_metric_values_serialize_as_numbers() {
    assert_eq!(metric_json_value(1.25), json!(1.25));
}

#[test]
fn backtest_contract_non_finite_metric_values_serialize_as_null() {
    for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        assert_eq!(metric_json_value(value), serde_json::Value::Null);
    }
}

#[test]
fn backtest_contract_fixture_fingerprint_matches_hash_lock() {
    let fingerprint = data_fingerprint(&fixtures_dir().join("usdjpy_1h_sample.parquet")).unwrap();
    assert_eq!(
        fingerprint,
        "sha256:14def03e6037df2108b4c0faba9da0a71306bc8ff5259541bfeff9b8f24dd0b0"
    );
}

#[test]
fn backtest_contract_ungated_completed_output_has_stable_shape() {
    let params =
        parse_tod_edge_params_json(r#"{"entry_minute":0,"direction":"long","hold_h":3}"#).unwrap();
    let result = BacktestResult {
        total_return: 0.42,
        sharpe_ratio: 99.0,
        max_drawdown: -0.1,
        win_rate: 0.5,
        num_trades: 7,
        gross_profit: 0.5,
        gross_loss: 0.0,
        profit_factor: f64::INFINITY,
        equity_curve: vec![1.0, 1.42],
        timestamps: vec![0, 1],
    };

    let output = ungated_completed_output(
        "USDJPY".to_string(),
        "1h".to_string(),
        params,
        "rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet".to_string(),
        "sha256:14def03e6037df2108b4c0faba9da0a71306bc8ff5259541bfeff9b8f24dd0b0".to_string(),
        1.0,
        &result,
    );
    let value = serde_json::to_value(output).unwrap();
    let object = value.as_object().unwrap();
    let keys = object.keys().map(String::as_str).collect::<HashSet<_>>();

    assert_eq!(
        keys,
        HashSet::from([
            "schema_version",
            "risk_gate_enabled",
            "run_status",
            "asset",
            "strategy",
            "timeframe",
            "params",
            "data_ref",
            "data_fingerprint",
            "fee_bps",
            "metrics",
            "risk_gate",
            "cap_parity",
            "backtest_execution",
        ])
    );
    assert_eq!(value["schema_version"], BACKTEST_RESULT_SCHEMA_VERSION);
    assert_eq!(value["risk_gate_enabled"], false);
    assert_eq!(value["run_status"], "completed");
    assert_eq!(value["strategy"], "tod_edge");
    assert_eq!(value["metrics"]["profit_factor"], serde_json::Value::Null);
    assert_eq!(value["metrics"]["num_trades"], 7);
    assert_eq!(value["metrics"]["total_return"], json!(0.42));
    assert_eq!(value["risk_gate"], serde_json::Value::Null);
    assert_eq!(value["cap_parity"]["status"], "not_applicable");
    assert_eq!(value["backtest_execution"]["status"], "run");
    assert_eq!(
        value["backtest_execution"]["reason"],
        serde_json::Value::Null
    );

    for absent in [
        "sharpe_ratio",
        "max_drawdown",
        "win_rate",
        "gross_profit",
        "gross_loss",
        "equity_curve",
        "timestamps",
    ] {
        assert!(value["metrics"].get(absent).is_none(), "{absent} leaked");
    }
}

#[test]
fn backtest_contract_completed_output_serializes_non_finite_metrics_as_null_with_keys_present() {
    for (metric, non_finite_value) in [
        ("profit_factor", f64::NAN),
        ("profit_factor", f64::INFINITY),
        ("profit_factor", f64::NEG_INFINITY),
        ("total_return", f64::NAN),
        ("total_return", f64::INFINITY),
        ("total_return", f64::NEG_INFINITY),
    ] {
        let mut result = finite_sample_backtest_result();
        match metric {
            "profit_factor" => result.profit_factor = non_finite_value,
            "total_return" => result.total_return = non_finite_value,
            _ => unreachable!("unexpected metric case"),
        }

        let output = ungated_completed_output(
            "USDJPY".to_string(),
            "1h".to_string(),
            sample_params(),
            "rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet".to_string(),
            sample_fingerprint(),
            1.0,
            &result,
        );
        let value = serde_json::to_value(output).unwrap();
        let metrics = value["metrics"].as_object().unwrap();
        let metric_keys = metrics.keys().map(String::as_str).collect::<HashSet<_>>();

        assert_eq!(
            metric_keys,
            HashSet::from(["profit_factor", "num_trades", "total_return"])
        );
        assert_eq!(metrics.get(metric), Some(&serde_json::Value::Null));
        assert_eq!(metrics["num_trades"], 7);
        if metric == "profit_factor" {
            assert_eq!(metrics["total_return"], json!(0.42));
        } else {
            assert_eq!(metrics["profit_factor"], json!(1.75));
        }
    }
}

#[test]
fn backtest_contract_gated_stopped_output_has_risk_stop_shape() {
    let output = gated_stopped_output(
        "USDJPY".to_string(),
        "1h".to_string(),
        sample_params(),
        "rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet".to_string(),
        sample_fingerprint(),
        1.0,
        &sample_summary("block", 0.0),
    )
    .unwrap();

    let value = serde_json::to_value(output).unwrap();

    assert_eq!(value["risk_gate_enabled"], true);
    assert_eq!(value["run_status"], "stopped");
    assert_eq!(value["metrics"], serde_json::Value::Null);
    assert_eq!(value["risk_gate"]["decision_class"], "block");
    assert_eq!(value["risk_gate"]["execution_state"], "stopped");
    assert_eq!(value["cap_parity"]["status"], "not_applicable");
    assert_eq!(value["backtest_execution"]["status"], "not_run");
    assert_eq!(value["backtest_execution"]["reason"], "risk_gate_stop");
}

#[test]
fn backtest_contract_gated_completed_output_uses_same_non_finite_metric_null_contract() {
    let mut result = finite_sample_backtest_result();
    result.profit_factor = f64::NEG_INFINITY;
    result.total_return = f64::NAN;

    let output = gated_completed_output(
        "USDJPY".to_string(),
        "1h".to_string(),
        sample_params(),
        "rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet".to_string(),
        sample_fingerprint(),
        1.0,
        &sample_summary("size", 1.0),
        &result,
        None,
    )
    .unwrap();

    let value = serde_json::to_value(output).unwrap();
    let metrics = value["metrics"].as_object().unwrap();

    assert_eq!(value["run_status"], "completed");
    assert_eq!(metrics.get("profit_factor"), Some(&serde_json::Value::Null));
    assert_eq!(metrics.get("total_return"), Some(&serde_json::Value::Null));
    assert_eq!(metrics["num_trades"], 7);
    assert_eq!(value["risk_gate"]["decision_class"], "size");
    assert_eq!(value["backtest_execution"]["status"], "run");
}

#[test]
fn backtest_contract_gated_completed_size_output_runs_with_metrics() {
    let output = gated_completed_output(
        "USDJPY".to_string(),
        "1h".to_string(),
        sample_params(),
        "rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet".to_string(),
        sample_fingerprint(),
        1.0,
        &sample_summary("size", 1.0),
        &sample_backtest_result(),
        None,
    )
    .unwrap();

    let value = serde_json::to_value(output).unwrap();

    assert_eq!(value["risk_gate_enabled"], true);
    assert_eq!(value["run_status"], "completed");
    assert_eq!(value["metrics"]["num_trades"], 7);
    assert_eq!(value["risk_gate"]["decision_class"], "size");
    assert_eq!(value["risk_gate"]["execution_state"], "continued");
    assert_eq!(value["cap_parity"]["status"], "not_applicable");
    assert_eq!(value["backtest_execution"]["status"], "run");
    assert_eq!(
        value["backtest_execution"]["reason"],
        serde_json::Value::Null
    );
}

#[test]
fn backtest_contract_gated_completed_cap_output_marks_applied_runtime_sizing() {
    let summary = sample_summary("cap", 0.25);
    let runtime_sizing = backtest_runtime_sizing_for_summary(&summary).unwrap();
    let output = gated_completed_output(
        "USDJPY".to_string(),
        "1h".to_string(),
        sample_params(),
        "rust/side-engine/tests/fixtures/usdjpy_1h_sample.parquet".to_string(),
        sample_fingerprint(),
        1.0,
        &summary,
        &sample_backtest_result(),
        Some(&runtime_sizing),
    )
    .unwrap();

    let value = serde_json::to_value(output).unwrap();

    assert_eq!(value["run_status"], "completed");
    assert_eq!(value["metrics"]["num_trades"], 7);
    assert_eq!(value["risk_gate"]["decision_class"], "cap");
    assert_eq!(value["risk_gate"]["execution_state"], "continued");
    assert_eq!(value["risk_gate"]["requested_size"], 1.0);
    assert_eq!(
        value["risk_gate"]["requested_size_basis"],
        "unit_backtest_run"
    );
    assert_eq!(value["risk_gate"]["allowed_size"], 0.25);
    assert_eq!(value["risk_gate"]["effective_size"], 0.25);
    assert_eq!(value["risk_gate"]["application_status"], "applied");
    assert_eq!(value["risk_gate"]["runtime_sizing_applied"], true);
    assert_eq!(value["risk_gate"]["sizing_effect"], "reduced");
    assert_eq!(value["cap_parity"]["status"], "not_applicable");
    assert_eq!(value["backtest_execution"]["status"], "run");
    assert_eq!(
        value["backtest_execution"]["reason"],
        serde_json::Value::Null
    );
}

#[test]
fn backtest_contract_runtime_sizing_marks_non_binding_cap_as_applied_none() {
    let runtime_sizing = backtest_runtime_sizing_for_summary(&sample_summary("cap", 1.0)).unwrap();

    assert_eq!(runtime_sizing.requested_size, 1.0);
    assert_eq!(runtime_sizing.requested_size_basis, "unit_backtest_run");
    assert_eq!(runtime_sizing.allowed_size, 1.0);
    assert_eq!(runtime_sizing.effective_size, 1.0);
    assert_eq!(runtime_sizing.application_status, "applied");
    assert!(runtime_sizing.runtime_sizing_applied);
    assert_eq!(runtime_sizing.sizing_effect, "none");
}

#[test]
fn backtest_contract_runtime_sizing_rejects_invalid_cap_sizes() {
    for allowed_size in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 0.0, -0.25, 1.25] {
        let err =
            backtest_runtime_sizing_for_summary(&sample_summary("cap", allowed_size)).unwrap_err();
        assert!(
            format!("{err:#}").contains("invalid backtest cap allowed_size"),
            "unexpected error for {allowed_size}: {err:#}"
        );
    }
}

fn sample_params() -> side_cli::cmd::backtest_contract::TodEdgeParams {
    parse_tod_edge_params_json(r#"{"entry_minute":0,"direction":"long","hold_h":3}"#).unwrap()
}

fn sample_fingerprint() -> String {
    "sha256:14def03e6037df2108b4c0faba9da0a71306bc8ff5259541bfeff9b8f24dd0b0".to_string()
}

fn sample_summary(decision_class: &str, allowed_size: f64) -> RiskGateSummary {
    RiskGateSummary {
        decision_class: decision_class.to_string(),
        allowed_size,
        binding_rule: format!("risk-policy.v1.{decision_class}"),
        fail_close_reason: "insufficient_validation_power".to_string(),
        policy_version: "risk-policy.v1.test".to_string(),
        candidate_id: "backtest.USDJPY.1h.tod_edge.p8f14d2c0".to_string(),
        artifact_path: format!(
            "reports/v5.8/risk_gate/{decision_class}/backtest.USDJPY.1h.tod_edge.p8f14d2c0.json"
        ),
        schema_version: None,
        contract_version: None,
        validator_result_schema_version: None,
        validated_schema_ref: None,
    }
}

fn sample_backtest_result() -> BacktestResult {
    BacktestResult {
        total_return: 0.42,
        sharpe_ratio: 99.0,
        max_drawdown: -0.1,
        win_rate: 0.5,
        num_trades: 7,
        gross_profit: 0.5,
        gross_loss: 0.0,
        profit_factor: f64::INFINITY,
        equity_curve: vec![1.0, 1.42],
        timestamps: vec![0, 1],
    }
}

fn finite_sample_backtest_result() -> BacktestResult {
    BacktestResult {
        total_return: 0.42,
        sharpe_ratio: 99.0,
        max_drawdown: -0.1,
        win_rate: 0.5,
        num_trades: 7,
        gross_profit: 0.5,
        gross_loss: 0.2,
        profit_factor: 1.75,
        equity_curve: vec![1.0, 1.42],
        timestamps: vec![0, 1],
    }
}
