use anyhow::Context;
use arrow::array::{Array, Float64Array, Int64Array};
use arrow::datatypes::DataType;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use std::path::Path;

use crate::scanner::OhlcvData;

/// Load OHLCV data from a Parquet file (Phase 97 SEAL schema).
///
/// Schema expected (D-09 canonical):
///   datetime_ns: INT64 (epoch nanoseconds, monotonic non-decreasing)
///   open/high/low/close/volume: DOUBLE
///
/// Sync reader — no spawn_blocking needed for CLI binary + test usage.
pub fn load_ohlcv_parquet(path: &Path) -> anyhow::Result<OhlcvData> {
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
    anyhow::ensure!(
        ext == "parquet",
        "expected .parquet extension, got {:?} (path: {})",
        ext,
        path.display()
    );

    let file = std::fs::File::open(path)
        .with_context(|| format!("failed to open parquet: {}", path.display()))?;

    let builder = ParquetRecordBatchReaderBuilder::try_new(file)
        .with_context(|| format!("parquet builder: {}", path.display()))?;
    validate_parquet_schema(builder.schema().as_ref())?;
    let reader = builder.build()?;

    let mut datetimes_ns: Vec<i64> = Vec::new();
    let mut open: Vec<f64> = Vec::new();
    let mut high: Vec<f64> = Vec::new();
    let mut low: Vec<f64> = Vec::new();
    let mut close: Vec<f64> = Vec::new();
    let mut volume: Vec<f64> = Vec::new();

    for batch_result in reader {
        let batch = batch_result?;
        // datetime_ns column is INT64 (epoch nanoseconds, D-09 canonical schema)
        let datetime_col = batch
            .column(0)
            .as_any()
            .downcast_ref::<Int64Array>()
            .context(
                "datetime_ns column is not Int64Array (expected INT64 epoch nanoseconds per D-09)",
            )?;
        anyhow::ensure!(
            datetime_col.null_count() == 0,
            "parquet schema column datetime_ns contains null values"
        );
        let open_col = batch
            .column(1)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("open column is not Float64")?;
        anyhow::ensure!(
            open_col.null_count() == 0,
            "parquet schema column open contains null values"
        );
        let high_col = batch
            .column(2)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("high column is not Float64")?;
        anyhow::ensure!(
            high_col.null_count() == 0,
            "parquet schema column high contains null values"
        );
        let low_col = batch
            .column(3)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("low column is not Float64")?;
        anyhow::ensure!(
            low_col.null_count() == 0,
            "parquet schema column low contains null values"
        );
        let close_col = batch
            .column(4)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("close column is not Float64")?;
        anyhow::ensure!(
            close_col.null_count() == 0,
            "parquet schema column close contains null values"
        );
        let volume_col = batch
            .column(5)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("volume column is not Float64")?;
        anyhow::ensure!(
            volume_col.null_count() == 0,
            "parquet schema column volume contains null values"
        );

        for i in 0..batch.num_rows() {
            // Already epoch nanoseconds — no conversion needed
            datetimes_ns.push(datetime_col.value(i));
            open.push(open_col.value(i));
            high.push(high_col.value(i));
            low.push(low_col.value(i));
            close.push(close_col.value(i));
            volume.push(volume_col.value(i));
        }
    }

    Ok(OhlcvData {
        open,
        high,
        low,
        close,
        volume,
        datetimes_ns,
        aux_close: None,
    })
}

pub fn validate_ohlcv_contract(data: &OhlcvData, timeframe: &str) -> anyhow::Result<()> {
    let expected_delta = timeframe_delta_ns(timeframe)?;
    let len = data.datetimes_ns.len();
    anyhow::ensure!(len > 0, "empty OHLCV data");

    for (name, field_len) in [
        ("open", data.open.len()),
        ("high", data.high.len()),
        ("low", data.low.len()),
        ("close", data.close.len()),
        ("volume", data.volume.len()),
    ] {
        anyhow::ensure!(
            field_len == len,
            "OHLCV length mismatch: datetimes_ns={} {name}={}",
            len,
            field_len
        );
    }

    for pair in data.datetimes_ns.windows(2) {
        let delta = pair[1] - pair[0];
        anyhow::ensure!(
            delta > 0,
            "datetimes_ns must be strictly increasing: {} then {}",
            pair[0],
            pair[1]
        );
        anyhow::ensure!(
            delta == expected_delta,
            "timestamp gap for timeframe {timeframe}: expected {expected_delta}ns, got {delta}ns"
        );
    }

    for (name, values) in [
        ("open", data.open.as_slice()),
        ("high", data.high.as_slice()),
        ("low", data.low.as_slice()),
        ("close", data.close.as_slice()),
        ("volume", data.volume.as_slice()),
    ] {
        for &value in values {
            anyhow::ensure!(
                value.is_finite(),
                "non-finite OHLCV value in {name}: {value}"
            );
        }
    }

    for &close in &data.close {
        anyhow::ensure!(close > 0.0, "close must be positive: {close}");
    }

    Ok(())
}

fn timeframe_delta_ns(timeframe: &str) -> anyhow::Result<i64> {
    match timeframe {
        "1m" => Ok(60_000_000_000),
        "5m" => Ok(300_000_000_000),
        "15m" => Ok(900_000_000_000),
        "30m" => Ok(1_800_000_000_000),
        "1h" => Ok(3_600_000_000_000),
        "4h" => Ok(14_400_000_000_000),
        "1d" => Ok(86_400_000_000_000),
        other => anyhow::bail!("unsupported timeframe: {other}"),
    }
}

fn validate_parquet_schema(schema: &arrow::datatypes::Schema) -> anyhow::Result<()> {
    let expected = [
        ("datetime_ns", DataType::Int64),
        ("open", DataType::Float64),
        ("high", DataType::Float64),
        ("low", DataType::Float64),
        ("close", DataType::Float64),
        ("volume", DataType::Float64),
    ];

    anyhow::ensure!(
        schema.fields().len() == expected.len(),
        "parquet schema field count mismatch: expected {}, got {}",
        expected.len(),
        schema.fields().len()
    );

    for (idx, (expected_name, expected_type)) in expected.iter().enumerate() {
        let field = schema.field(idx);
        anyhow::ensure!(
            field.name() == expected_name,
            "parquet schema field {idx} name mismatch: expected {expected_name}, got {}",
            field.name()
        );
        anyhow::ensure!(
            field.data_type() == expected_type,
            "parquet schema field {expected_name} type mismatch: expected {expected_type:?}, got {:?}",
            field.data_type()
        );
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn fixture_path() -> std::path::PathBuf {
        let manifest = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
        std::path::Path::new(&manifest).join("tests/fixtures/usdjpy_1h_sample.parquet")
    }

    #[test]
    fn loads_fixture_rows() {
        let path = fixture_path();
        let data = load_ohlcv_parquet(&path).expect("should load parquet");
        assert_eq!(data.open.len(), 504, "expected 504 rows");
        assert!(data.open[0] > 0.0, "open must be positive");
        assert!(data.datetimes_ns[0] > 0, "datetime must be positive");
        assert!(data.aux_close.is_none(), "aux_close must be None");
    }

    #[test]
    fn schema_mismatch_returns_error() {
        // A non-existent / non-parquet file should return an error
        let bad_path = Path::new("/tmp/nonexistent_schema_mismatch_test.parquet");
        let result = load_ohlcv_parquet(bad_path);
        assert!(result.is_err(), "expected error for missing/bad parquet");
    }

    #[test]
    fn rejects_csv_extension() {
        // D-03 fail-fast: .csv extension must be rejected before opening file
        let csv_path = Path::new("tests/fixtures/usdjpy_1h_sample.csv");
        let result = load_ohlcv_parquet(csv_path);
        assert!(result.is_err(), "expected error for .csv extension");
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("parquet"),
            "error message should mention 'parquet', got: {err}"
        );
    }
}
