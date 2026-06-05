//! Handler for GET /ohlcv/{asset} — returns OHLCV bars for a symbol/timeframe.

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    Json,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::server::state::AppState;

/// Query parameters for the /ohlcv endpoint.
#[derive(Debug, Deserialize)]
pub struct OhlcvQuery {
    /// Timeframe string, e.g. "1h", "4h", "1d".
    pub tf: String,
    /// Number of trailing calendar days to return (default: 365).
    #[serde(default = "default_days")]
    pub days: u32,
}

fn default_days() -> u32 {
    365
}

/// A single OHLCV bar in the JSON response.
#[derive(Debug, Serialize)]
pub struct BarJson {
    pub datetime: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

/// Response body for the /ohlcv endpoint.
#[derive(Debug, Serialize)]
pub struct OhlcvResponse {
    pub asset: String,
    pub tf: String,
    pub count: usize,
    pub bars: Vec<BarJson>,
}

/// GET /ohlcv/{asset}?tf=<timeframe>[&days=<N>]
///
/// Returns OHLCV bars for the given asset and timeframe. The asset name is
/// case-insensitive. Returns 404 if no data file exists for the asset/tf pair.
pub async fn handle(
    Path(asset): Path<String>,
    Query(params): Query<OhlcvQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<OhlcvResponse>, (StatusCode, String)> {
    let asset_upper = asset.to_uppercase();
    let data_dir = std::path::PathBuf::from(&state.config.global.data_dir);
    let tf = params.tf.clone();
    let days = params.days;
    let asset_for_log = asset_upper.clone();

    // spawn_blocking: Parquet read は sync CPU work。tokio runtime thread を
    // 占有しないよう別 thread に逃がす。
    // (Phase 97 D-07 Wave 0 verdict + RESEARCH §12 Pitfall 1:
    //  csv_reader 時代の blocking anti-pattern もここで解消)
    let bars = tokio::task::spawn_blocking(move || {
        crate::server::parquet_reader::read_bars_filtered(&data_dir, &asset_upper, &tf, days)
    })
    .await
    .map_err(|e| {
        tracing::error!(asset = %asset_for_log, tf = %params.tf, error = %e, "spawn_blocking join error");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!(
                "internal error reading data for {}/{}",
                asset_for_log, params.tf
            ),
        )
    })?
    .map_err(|e| {
        tracing::error!(asset = %asset_for_log, tf = %params.tf, error = %e, "parquet read error");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!(
                "internal error reading data for {}/{}",
                asset_for_log, params.tf
            ),
        )
    })?;

    if bars.is_empty() {
        return Err((
            StatusCode::NOT_FOUND,
            format!("no data for {}/{}", asset_for_log, params.tf),
        ));
    }

    let bar_jsons: Vec<BarJson> = bars
        .into_iter()
        .map(|b| BarJson {
            datetime: b.datetime.format("%Y-%m-%dT%H:%M:%S").to_string(),
            open: b.open,
            high: b.high,
            low: b.low,
            close: b.close,
            volume: b.volume,
        })
        .collect();

    let count = bar_jsons.len();
    Ok(Json(OhlcvResponse {
        asset: asset_for_log,
        tf: params.tf,
        count,
        bars: bar_jsons,
    }))
}
