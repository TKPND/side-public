//! Shared application state passed to all HTTP handlers.

use chrono::{DateTime, Utc};
use std::sync::Arc;
use tokio::sync::RwLock;

/// Shared state for the HTTP server.
///
/// Wrapped in `Arc` so all handlers can clone cheaply. The `last_fetch_at`
/// field uses `tokio::sync::RwLock` because it is written from an async
/// background task.
#[derive(Clone)]
pub struct AppState {
    /// Mirror daemon configuration (read-only after startup).
    pub config: Arc<crate::config::MirrorConfig>,
    /// When the server process started.
    pub start_time: DateTime<Utc>,
    /// Timestamp of the most recent successful fetch pass, or `None` if the
    /// initial fetch has not yet completed.
    pub last_fetch_at: Arc<RwLock<Option<DateTime<Utc>>>>,
}

impl AppState {
    /// Create a new `AppState` with the given config.
    pub fn new(config: Arc<crate::config::MirrorConfig>) -> Self {
        Self {
            config,
            start_time: Utc::now(),
            last_fetch_at: Arc::new(RwLock::new(None)),
        }
    }
}
