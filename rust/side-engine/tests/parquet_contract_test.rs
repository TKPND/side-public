use std::sync::Arc;

use arrow::array::{Float64Array, Int64Array};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use parquet::arrow::ArrowWriter;
use side_engine::scanner::OhlcvData;
use tempfile::TempDir;

fn fixture_path() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("usdjpy_1h_sample.parquet")
}

fn valid_data() -> OhlcvData {
    OhlcvData {
        open: vec![100.0, 101.0, 102.0],
        high: vec![101.0, 102.0, 103.0],
        low: vec![99.0, 100.0, 101.0],
        close: vec![100.5, 101.5, 102.5],
        volume: vec![10.0, 11.0, 12.0],
        datetimes_ns: vec![0, 3_600_000_000_000, 7_200_000_000_000],
        aux_close: None,
    }
}

#[test]
fn parquet_loader_fixture_satisfies_1h_contract() {
    let data = side_engine::parquet_loader::load_ohlcv_parquet(&fixture_path()).unwrap();
    side_engine::parquet_loader::validate_ohlcv_contract(&data, "1h").unwrap();
}

#[test]
fn parquet_loader_rejects_duplicate_timestamps() {
    let mut data = valid_data();
    data.datetimes_ns[1] = data.datetimes_ns[0];

    let err = side_engine::parquet_loader::validate_ohlcv_contract(&data, "1h").unwrap_err();
    assert!(format!("{err:#}").contains("strictly increasing"));
}

#[test]
fn parquet_loader_rejects_missing_timeframe_bar() {
    let mut data = valid_data();
    data.datetimes_ns[2] += 3_600_000_000_000;

    let err = side_engine::parquet_loader::validate_ohlcv_contract(&data, "1h").unwrap_err();
    assert!(format!("{err:#}").contains("timestamp gap"));
}

#[test]
fn parquet_loader_rejects_nonpositive_close() {
    let mut data = valid_data();
    data.close[1] = 0.0;

    let err = side_engine::parquet_loader::validate_ohlcv_contract(&data, "1h").unwrap_err();
    assert!(format!("{err:#}").contains("close must be positive"));
}

#[test]
fn parquet_loader_rejects_non_finite_ohlcv() {
    let mut data = valid_data();
    data.high[1] = f64::NAN;

    let err = side_engine::parquet_loader::validate_ohlcv_contract(&data, "1h").unwrap_err();
    assert!(format!("{err:#}").contains("non-finite"));
}

#[test]
fn parquet_loader_rejects_unsupported_timeframe() {
    let err =
        side_engine::parquet_loader::validate_ohlcv_contract(&valid_data(), "2h").unwrap_err();
    assert!(format!("{err:#}").contains("unsupported timeframe"));
}

#[test]
fn parquet_loader_rejects_schema_with_wrong_field_type() {
    let tmp = TempDir::new().unwrap();
    let path = tmp.path().join("wrong_schema.parquet");
    write_wrong_schema_parquet(&path);

    let err = side_engine::parquet_loader::load_ohlcv_parquet(&path).unwrap_err();
    assert!(format!("{err:#}").contains("parquet schema"));
}

#[test]
fn parquet_loader_rejects_null_values() {
    let tmp = TempDir::new().unwrap();
    let path = tmp.path().join("null_values.parquet");
    write_null_value_parquet(&path);

    let err = side_engine::parquet_loader::load_ohlcv_parquet(&path).unwrap_err();
    assert!(format!("{err:#}").contains("parquet schema"));
}

fn write_wrong_schema_parquet(path: &std::path::Path) {
    let schema = Arc::new(Schema::new(vec![
        Field::new("datetime_ns", DataType::Float64, false),
        Field::new("open", DataType::Float64, false),
        Field::new("high", DataType::Float64, false),
        Field::new("low", DataType::Float64, false),
        Field::new("close", DataType::Float64, false),
        Field::new("volume", DataType::Float64, false),
    ]));
    let batch = RecordBatch::try_new(
        schema.clone(),
        vec![
            Arc::new(Float64Array::from(vec![0.0])),
            Arc::new(Float64Array::from(vec![100.0])),
            Arc::new(Float64Array::from(vec![101.0])),
            Arc::new(Float64Array::from(vec![99.0])),
            Arc::new(Float64Array::from(vec![100.5])),
            Arc::new(Float64Array::from(vec![10.0])),
        ],
    )
    .unwrap();
    let file = std::fs::File::create(path).unwrap();
    let mut writer = ArrowWriter::try_new(file, schema, None).unwrap();
    writer.write(&batch).unwrap();
    writer.close().unwrap();
}

fn write_null_value_parquet(path: &std::path::Path) {
    let schema = Arc::new(Schema::new(vec![
        Field::new("datetime_ns", DataType::Int64, true),
        Field::new("open", DataType::Float64, false),
        Field::new("high", DataType::Float64, false),
        Field::new("low", DataType::Float64, false),
        Field::new("close", DataType::Float64, false),
        Field::new("volume", DataType::Float64, false),
    ]));
    let batch = RecordBatch::try_new(
        schema.clone(),
        vec![
            Arc::new(Int64Array::from(vec![None::<i64>])),
            Arc::new(Float64Array::from(vec![100.0])),
            Arc::new(Float64Array::from(vec![101.0])),
            Arc::new(Float64Array::from(vec![99.0])),
            Arc::new(Float64Array::from(vec![100.5])),
            Arc::new(Float64Array::from(vec![10.0])),
        ],
    )
    .unwrap();
    let file = std::fs::File::create(path).unwrap();
    let mut writer = ArrowWriter::try_new(file, schema, None).unwrap();
    writer.write(&batch).unwrap();
    writer.close().unwrap();
}
