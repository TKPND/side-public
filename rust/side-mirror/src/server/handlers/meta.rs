//! Handler for GET /meta — returns metadata for all available Parquet data files.

use anyhow::Context;
use arrow::array::TimestampMillisecondArray;
use axum::{extract::State, Json};
use chrono::{TimeZone, Utc};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use serde::Serialize;
use std::sync::Arc;

use crate::server::state::AppState;

/// Metadata entry for a single asset/timeframe Parquet file.
#[derive(Debug, Serialize)]
pub struct MetaEntry {
    pub asset: String,
    pub tf: String,
    pub first_bar: Option<String>,
    pub last_bar: Option<String>,
    pub bar_count: usize,
}

/// Extract `(bar_count, first_bar, last_bar)` from a Parquet file whose first
/// column is a `TIMESTAMP(MILLIS, UTC)` (see
/// `97-SEAL/parquet_schema.json:ohlcv_1h_schema`).
fn read_meta_from_parquet(
    path: &std::path::Path,
) -> anyhow::Result<(usize, Option<String>, Option<String>)> {
    let file = std::fs::File::open(path).with_context(|| format!("open {}", path.display()))?;
    let builder = ParquetRecordBatchReaderBuilder::try_new(file)
        .with_context(|| format!("parquet builder: {}", path.display()))?;
    let reader = builder.build()?;

    let mut bar_count = 0usize;
    let mut first_ms: Option<i64> = None;
    let mut last_ms: Option<i64> = None;

    for batch_result in reader {
        let batch = batch_result?;
        let dt_col = batch
            .column(0)
            .as_any()
            .downcast_ref::<TimestampMillisecondArray>()
            .context("datetime column is not TimestampMillisecondArray")?;
        if batch.num_rows() > 0 {
            if first_ms.is_none() {
                first_ms = Some(dt_col.value(0));
            }
            last_ms = Some(dt_col.value(batch.num_rows() - 1));
        }
        bar_count += batch.num_rows();
    }

    let format_ms = |ms: i64| -> String {
        Utc.timestamp_millis_opt(ms)
            .single()
            .map(|dt| dt.naive_utc().format("%Y-%m-%dT%H:%M:%S").to_string())
            .unwrap_or_default()
    };

    Ok((bar_count, first_ms.map(format_ms), last_ms.map(format_ms)))
}

/// GET /meta
///
/// Scans the configured data directory for `*.parquet` files and returns metadata
/// for each: asset name, timeframe, first and last bar timestamps, and total
/// bar count. (Phase 97 D-01 CSV → Parquet migration)
pub async fn handle(State(state): State<Arc<AppState>>) -> Json<Vec<MetaEntry>> {
    let data_dir = std::path::Path::new(&state.config.global.data_dir);
    let mut entries: Vec<MetaEntry> = Vec::new();

    let read_dir = match std::fs::read_dir(data_dir) {
        Ok(rd) => rd,
        Err(e) => {
            tracing::warn!(error = %e, data_dir = %data_dir.display(), "cannot read data_dir for /meta");
            return Json(entries);
        }
    };

    for entry in read_dir.flatten() {
        let path = entry.path();

        // Only process .parquet files (Phase 97 D-01)
        if path.extension().and_then(|e| e.to_str()) != Some("parquet") {
            continue;
        }

        let stem = match path.file_stem().and_then(|s| s.to_str()) {
            Some(s) => s.to_string(),
            None => continue,
        };

        // Parse filename: ASSET_TF (e.g. "USDJPY_1h")
        let (asset, tf) = match stem.rsplit_once('_') {
            Some((a, t)) => (a.to_string(), t.to_string()),
            None => {
                tracing::debug!(filename = %stem, "skipping file with unexpected name format");
                continue;
            }
        };

        let (bar_count, first_bar, last_bar) = match read_meta_from_parquet(&path) {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!(path = %path.display(), error = %e, "failed to read parquet for /meta");
                continue;
            }
        };

        entries.push(MetaEntry {
            asset,
            tf,
            first_bar,
            last_bar,
            bar_count,
        });
    }

    Json(entries)
}
