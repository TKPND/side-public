//! Serve subcommand — HTTP server with background fetch loop.

use chrono::Utc;
use std::sync::Arc;

use crate::server::state::AppState;

/// Arguments for the serve subcommand.
#[derive(clap::Args)]
pub struct ServeArgs {
    /// TCP port to listen on.
    #[arg(long, default_value = "8080")]
    pub port: u16,
}

/// Run the serve subcommand.
///
/// 1. Runs an initial fetch pass (failures are logged but do not abort startup,
///    since data may already exist from prior runs).
/// 2. Spawns a background tokio task that re-runs the fetch loop every
///    `config.global.interval_minutes` minutes.
/// 3. Starts the axum HTTP server on the configured port.
pub async fn run(args: ServeArgs, config: crate::config::MirrorConfig) -> anyhow::Result<()> {
    let config = Arc::new(config);

    let state = Arc::new(AppState::new(config.clone()));

    // Run an initial fetch before starting the server so data is available
    // immediately. Failures are non-fatal — data may already be cached.
    tracing::info!("running initial fetch pass");
    match crate::fetch::run_fetch(&config).await {
        Ok(()) => {
            let mut guard = state.last_fetch_at.write().await;
            *guard = Some(Utc::now());
            tracing::info!("initial fetch complete");
        }
        Err(e) => {
            tracing::warn!(error = %e, "initial fetch failed; serving from cached data if available");
        }
    }

    // Spawn background fetch loop
    {
        let state_bg = state.clone();
        let interval_secs = config.global.interval_minutes * 60;

        tokio::spawn(async move {
            loop {
                tokio::time::sleep(tokio::time::Duration::from_secs(interval_secs)).await;
                tracing::info!("running scheduled fetch pass");
                match crate::fetch::run_fetch(&state_bg.config).await {
                    Ok(()) => {
                        let mut guard = state_bg.last_fetch_at.write().await;
                        *guard = Some(Utc::now());
                        tracing::info!("scheduled fetch complete");
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, "scheduled fetch failed; will retry next cycle");
                    }
                }
            }
        });
    }

    // Build router and start HTTP server
    let app = crate::server::build_router(state);
    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{}", args.port)).await?;
    tracing::info!(port = args.port, "HTTP server listening");
    axum::serve(listener, app).await?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use crate::config::{GlobalConfig, MirrorConfig, PairConfig};
    use crate::server::state::AppState;
    use chrono::Utc;
    use std::sync::Arc;
    use tempfile::TempDir;
    use tokio::sync::RwLock;

    /// Create a minimal MirrorConfig pointing to a temp directory.
    fn make_config(data_dir: &str) -> MirrorConfig {
        MirrorConfig {
            global: GlobalConfig {
                data_dir: data_dir.to_string(),
                interval_minutes: 60,
                backfill_days: 365,
            },
            pairs: vec![PairConfig {
                symbol: "USDJPY".to_string(),
                timeframes: vec!["1h".to_string()],
            }],
        }
    }

    /// Write a minimal Parquet with the given number of rows to
    /// `dir/USDJPY_1h.parquet` (Phase 97 D-01: CSV → Parquet handler migration).
    fn write_test_parquet(dir: &TempDir, rows: usize) {
        use arrow::array::{Float64Array, TimestampMillisecondArray};
        use arrow::datatypes::{DataType, Field, Schema, TimeUnit};
        use arrow::record_batch::RecordBatch;
        use chrono::NaiveDate;
        use parquet::arrow::ArrowWriter;
        use parquet::basic::Compression;
        use parquet::file::properties::WriterProperties;
        use std::sync::Arc;

        let path = dir.path().join("USDJPY_1h.parquet");
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

        let ms: Vec<i64> = (0..rows)
            .map(|i| {
                NaiveDate::from_ymd_opt(2026, 3, 24)
                    .unwrap()
                    .and_hms_opt(i as u32, 0, 0)
                    .unwrap()
                    .and_utc()
                    .timestamp_millis()
            })
            .collect();
        let open = vec![150.0_f64; rows];
        let high = vec![150.5_f64; rows];
        let low = vec![149.5_f64; rows];
        let close = vec![150.2_f64; rows];
        let volume = vec![1000.0_f64; rows];

        let batch = RecordBatch::try_new(
            schema.clone(),
            vec![
                Arc::new(TimestampMillisecondArray::from(ms).with_timezone("UTC".to_string())),
                Arc::new(Float64Array::from(open)),
                Arc::new(Float64Array::from(high)),
                Arc::new(Float64Array::from(low)),
                Arc::new(Float64Array::from(close)),
                Arc::new(Float64Array::from(volume)),
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

    /// Build a test AppState without a fetch loop.
    fn make_state(config: MirrorConfig) -> Arc<AppState> {
        Arc::new(AppState {
            config: Arc::new(config),
            start_time: Utc::now(),
            last_fetch_at: Arc::new(RwLock::new(None)),
        })
    }

    #[tokio::test]
    async fn health_endpoint_returns_200_with_ok_status() {
        let dir = TempDir::new().unwrap();
        write_test_parquet(&dir, 3);
        let config = make_config(dir.path().to_str().unwrap());
        let state = make_state(config);
        let app = crate::server::build_router(state);

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();

        tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });

        let url = format!("http://{}/health", addr);
        let resp = reqwest::get(&url).await.unwrap();
        assert_eq!(resp.status(), 200);
        let body: serde_json::Value = resp.json().await.unwrap();
        assert_eq!(body["status"], "ok");
        assert!(body.get("uptime_secs").is_some());
        assert!(body.get("pairs").is_some());
    }

    #[tokio::test]
    async fn ohlcv_endpoint_returns_200_with_bars() {
        let dir = TempDir::new().unwrap();
        write_test_parquet(&dir, 3);
        let config = make_config(dir.path().to_str().unwrap());
        let state = make_state(config);
        let app = crate::server::build_router(state);

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();

        tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });

        let url = format!("http://{}/ohlcv/usdjpy?tf=1h", addr);
        let resp = reqwest::get(&url).await.unwrap();
        assert_eq!(resp.status(), 200);
        let body: serde_json::Value = resp.json().await.unwrap();
        assert_eq!(body["asset"], "USDJPY");
        assert_eq!(body["tf"], "1h");
        let bars = body["bars"].as_array().unwrap();
        assert_eq!(bars.len(), 3);
    }

    #[tokio::test]
    async fn ohlcv_endpoint_returns_404_for_unknown_asset() {
        let dir = TempDir::new().unwrap();
        write_test_parquet(&dir, 1);
        let config = make_config(dir.path().to_str().unwrap());
        let state = make_state(config);
        let app = crate::server::build_router(state);

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();

        tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });

        let url = format!("http://{}/ohlcv/NONEXIST?tf=1h", addr);
        let resp = reqwest::get(&url).await.unwrap();
        assert_eq!(resp.status(), 404);
    }

    #[tokio::test]
    async fn meta_endpoint_returns_200_with_entry() {
        let dir = TempDir::new().unwrap();
        write_test_parquet(&dir, 3);
        let config = make_config(dir.path().to_str().unwrap());
        let state = make_state(config);
        let app = crate::server::build_router(state);

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();

        tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });

        let url = format!("http://{}/meta", addr);
        let resp = reqwest::get(&url).await.unwrap();
        assert_eq!(resp.status(), 200);
        let body: serde_json::Value = resp.json().await.unwrap();
        let entries = body.as_array().unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0]["asset"], "USDJPY");
    }
}
