//! HTTP server module for side-mirror: router, state, Parquet reader, and handlers.

pub mod handlers;
pub mod parquet_reader;
pub mod state;

use axum::{routing::get, Router};
use std::sync::Arc;
use tower_http::trace::TraceLayer;

use state::AppState;

/// Build the axum router with all three endpoints and shared application state.
///
/// Routes:
/// - `GET /ohlcv/{asset}?tf=<tf>[&days=<N>]` — OHLCV bars for an asset/timeframe
/// - `GET /health` — server liveness and uptime
/// - `GET /meta` — metadata for all available Parquet files
pub fn build_router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/ohlcv/{asset}", get(handlers::ohlcv::handle))
        .route("/health", get(handlers::health::handle))
        .route("/meta", get(handlers::meta::handle))
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}
