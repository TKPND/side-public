//! Parquet reader for OHLCV bar files stored by the mirror daemon.
//!
//! Replaces csv_reader.rs per Phase 97 D-01/D-06.
//! Schema (97-SEAL/parquet_schema.json ohlcv_1h_schema):
//!   datetime: TIMESTAMP(MILLIS, UTC)
//!   open/high/low/close/volume: DOUBLE
//!
//! Sync reader, called from tokio via `spawn_blocking` per Phase 97 D-07 Wave 0
//! benchmark verdict (sync_p50=8.020ms / async_p50=8.710ms / diff=+8.60% < 20%).

use anyhow::Context;
use arrow::array::{Float64Array, TimestampMillisecondArray};
use chrono::{TimeZone, Utc};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use side_engine::fetcher::types::Bar;
use std::path::Path;

/// Read OHLCV bars from `<data_dir>/<ASSET>_<tf>.parquet`, filtering to the last
/// `days` calendar days.
///
/// Signature identical to `csv_reader::read_bars_filtered` — this is a drop-in
/// replacement per Phase 97 D-01.
///
/// The asset name is uppercased automatically so the caller can pass either
/// `"usdjpy"` or `"USDJPY"` and the correct file will be located on a
/// case-sensitive Linux filesystem.
///
/// If the file does not exist, returns an empty `Vec` (the caller maps this to
/// a 404 HTTP response). Bars with non-finite f64 values are silently dropped
/// before returning.
pub fn read_bars_filtered(
    data_dir: &Path,
    asset: &str,
    tf: &str,
    days: u32,
) -> anyhow::Result<Vec<Bar>> {
    let filename = format!("{}_{}.parquet", asset.to_uppercase(), tf);
    let path = data_dir.join(&filename);

    if !path.exists() {
        return Ok(vec![]);
    }

    let cutoff = Utc::now().naive_utc() - chrono::Duration::days(days as i64);

    let file = std::fs::File::open(&path)
        .with_context(|| format!("failed to open parquet: {}", path.display()))?;

    let builder = ParquetRecordBatchReaderBuilder::try_new(file)
        .with_context(|| format!("parquet builder: {}", path.display()))?;
    let reader = builder.build()?;

    let mut bars = Vec::new();
    for batch_result in reader {
        let batch = batch_result?;
        let datetime_col = batch
            .column(0)
            .as_any()
            .downcast_ref::<TimestampMillisecondArray>()
            .context("datetime column is not TimestampMillisecondArray")?;
        let open_col = batch
            .column(1)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("open column is not Float64")?;
        let high_col = batch
            .column(2)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("high column is not Float64")?;
        let low_col = batch
            .column(3)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("low column is not Float64")?;
        let close_col = batch
            .column(4)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("close column is not Float64")?;
        let vol_col = batch
            .column(5)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("volume column is not Float64")?;

        for i in 0..batch.num_rows() {
            let ms = datetime_col.value(i);
            let dt = Utc
                .timestamp_millis_opt(ms)
                .single()
                .context("invalid timestamp_millis")?
                .naive_utc();
            if dt < cutoff {
                continue;
            }
            let open = open_col.value(i);
            let high = high_col.value(i);
            let low = low_col.value(i);
            let close = close_col.value(i);
            let volume = vol_col.value(i);
            if !(open.is_finite()
                && high.is_finite()
                && low.is_finite()
                && close.is_finite()
                && volume.is_finite())
            {
                continue;
            }
            bars.push(Bar {
                datetime: dt,
                open,
                high,
                low,
                close,
                volume,
            });
        }
    }
    Ok(bars)
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{Float64Array, TimestampMillisecondArray};
    use arrow::datatypes::{DataType, Field, Schema, TimeUnit};
    use arrow::record_batch::RecordBatch;
    use chrono::NaiveDate;
    use parquet::arrow::ArrowWriter;
    use parquet::basic::Compression;
    use parquet::file::properties::WriterProperties;
    use std::sync::Arc;
    use tempfile::TempDir;

    fn make_bar(year: i32, month: u32, day: u32, hour: u32, close: f64) -> Bar {
        Bar {
            datetime: NaiveDate::from_ymd_opt(year, month, day)
                .unwrap()
                .and_hms_opt(hour, 0, 0)
                .unwrap(),
            open: close,
            high: close + 0.001,
            low: close - 0.001,
            close,
            volume: 100.0,
        }
    }

    fn write_parquet(dir: &TempDir, filename: &str, bars: &[Bar]) {
        let path = dir.path().join(filename);
        let schema = Arc::new(Schema::new(vec![
            Field::new(
                "datetime",
                DataType::Timestamp(TimeUnit::Millisecond, Some("UTC".into())),
                false,
            ),
            Field::new("open", DataType::Float64, false),
            Field::new("high", DataType::Float64, false),
            Field::new("low", DataType::Float64, false),
            Field::new("close", DataType::Float64, false),
            Field::new("volume", DataType::Float64, false),
        ]));

        let ms: Vec<i64> = bars
            .iter()
            .map(|b| b.datetime.and_utc().timestamp_millis())
            .collect();
        let batch = RecordBatch::try_new(
            schema.clone(),
            vec![
                Arc::new(TimestampMillisecondArray::from(ms).with_timezone("UTC".to_string())),
                Arc::new(Float64Array::from(
                    bars.iter().map(|b| b.open).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(
                    bars.iter().map(|b| b.high).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(
                    bars.iter().map(|b| b.low).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(
                    bars.iter().map(|b| b.close).collect::<Vec<_>>(),
                )),
                Arc::new(Float64Array::from(
                    bars.iter().map(|b| b.volume).collect::<Vec<_>>(),
                )),
            ],
        )
        .unwrap();

        let props = WriterProperties::builder()
            .set_compression(Compression::SNAPPY)
            .build();
        let file = std::fs::File::create(&path).unwrap();
        let mut writer = ArrowWriter::try_new(file, schema, Some(props)).unwrap();
        writer.write(&batch).unwrap();
        writer.close().unwrap();
    }

    #[test]
    fn returns_bars_from_existing_parquet() {
        let dir = TempDir::new().unwrap();
        let bars = vec![
            make_bar(2026, 3, 24, 10, 1.59),
            make_bar(2026, 3, 24, 11, 1.595),
            make_bar(2026, 3, 24, 12, 1.60),
        ];
        write_parquet(&dir, "USDJPY_1h.parquet", &bars);
        let result = read_bars_filtered(dir.path(), "USDJPY", "1h", 365).unwrap();
        assert_eq!(result.len(), 3);
    }

    #[test]
    fn case_insensitive_asset_name_matches_uppercase_file() {
        let dir = TempDir::new().unwrap();
        let bars = vec![make_bar(2026, 3, 24, 10, 1.59)];
        write_parquet(&dir, "USDJPY_1h.parquet", &bars);
        let result = read_bars_filtered(dir.path(), "usdjpy", "1h", 365).unwrap();
        assert_eq!(result.len(), 1);
    }

    #[test]
    fn nonexistent_asset_returns_empty_vec() {
        let dir = TempDir::new().unwrap();
        let result = read_bars_filtered(dir.path(), "NONEXIST", "1h", 365).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn old_bars_beyond_cutoff_are_filtered() {
        let dir = TempDir::new().unwrap();
        let bars = vec![
            make_bar(2020, 1, 1, 10, 1.0), // > 365 days ago
            make_bar(2026, 4, 20, 10, 1.5),
        ];
        write_parquet(&dir, "USDJPY_1h.parquet", &bars);
        let result = read_bars_filtered(dir.path(), "USDJPY", "1h", 365).unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(
            result[0].datetime.date(),
            NaiveDate::from_ymd_opt(2026, 4, 20).unwrap()
        );
    }
}
