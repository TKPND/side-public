//! Handler for GET /health — returns server liveness status.

use axum::{extract::State, Json};
use chrono::Utc;
use serde::Serialize;
use std::sync::Arc;

use crate::server::state::AppState;

/// Response body for the /health endpoint.
#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub status: &'static str,
    pub uptime_secs: u64,
    pub last_fetch_at: Option<String>,
    pub pairs: Vec<String>,
}

/// GET /health
///
/// Returns server uptime, last successful fetch timestamp, and configured pairs.
pub async fn handle(State(state): State<Arc<AppState>>) -> Json<HealthResponse> {
    let uptime = Utc::now()
        .signed_duration_since(state.start_time)
        .num_seconds()
        .max(0) as u64;

    let last_fetch_at = {
        let guard = state.last_fetch_at.read().await;
        guard.map(|dt| dt.to_rfc3339())
    };

    let pairs = state
        .config
        .pairs
        .iter()
        .map(|p| p.symbol.clone())
        .collect();

    Json(HealthResponse {
        status: "ok",
        uptime_secs: uptime,
        last_fetch_at,
        pairs,
    })
}
