//! Fetch orchestration — iterates configured pairs x timeframes, calls side-engine's
//! fetch_ohlcv_cached with retry and error isolation, displays ETA, and logs a summary.

use crate::config::MirrorConfig;
use side_engine::fetcher::dukascopy::fetch_ohlcv_cached;
use side_engine::fetcher::types::{Bar, Timeframe};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

/// Maximum number of retry attempts per pair x timeframe slot.
const MAX_RETRIES: u32 = 3;

/// Run a full fetch pass for all configured pairs and timeframes.
///
/// Iterates all pair x timeframe combinations sequentially. Each slot calls
/// `fetch_ohlcv_cached` which handles delta fetching — only bars after the last
/// cached timestamp are downloaded from Dukascopy.
///
/// Per-slot failures are isolated: one pair failing does not block others.
/// ETA is recalculated after each completed slot.
/// A final summary log reports total bars, error count, and elapsed time.
pub async fn run_fetch(config: &MirrorConfig) -> anyhow::Result<()> {
    let total_slots: usize = config.pairs.iter().map(|p| p.timeframes.len()).sum();

    tracing::info!(
        pairs = config.pairs.len(),
        total_slots,
        "starting mirror fetch"
    );

    let data_dir = PathBuf::from(&config.global.data_dir);
    let mut errors: Vec<(String, String, String)> = Vec::new();
    let mut total_bars: usize = 0;
    let mut completed_slots: usize = 0;
    let start_time = Instant::now();

    for pair in &config.pairs {
        for tf_str in &pair.timeframes {
            let tf = match Timeframe::parse(tf_str) {
                Ok(tf) => tf,
                Err(e) => {
                    tracing::warn!(
                        symbol = %pair.symbol,
                        tf = tf_str,
                        error = %e,
                        "unknown timeframe, skipping"
                    );
                    errors.push((pair.symbol.clone(), tf_str.clone(), e.to_string()));
                    completed_slots += 1;
                    continue;
                }
            };

            let slot_start = Instant::now();

            match fetch_with_retry(&pair.symbol, config.global.backfill_days, tf, &data_dir).await {
                Ok(bars) => {
                    total_bars += bars.len();
                    completed_slots += 1;

                    let slot_elapsed = slot_start.elapsed();

                    // ETA calculation (D-13): avg time per completed slot * remaining slots
                    let avg_per_slot = start_time.elapsed().as_secs_f64() / completed_slots as f64;
                    let remaining_slots = total_slots.saturating_sub(completed_slots);
                    let eta_secs = avg_per_slot * remaining_slots as f64;

                    tracing::info!(
                        symbol = %pair.symbol,
                        tf = tf_str,
                        bars = bars.len(),
                        slot_secs = format!("{:.1}", slot_elapsed.as_secs_f64()),
                        eta_secs = format!("{:.0}", eta_secs),
                        progress = format!("[{}/{}]", completed_slots, total_slots),
                        "fetched"
                    );
                }
                Err(e) => {
                    tracing::warn!(
                        symbol = %pair.symbol,
                        tf = tf_str,
                        error = %e,
                        "fetch failed"
                    );
                    errors.push((pair.symbol.clone(), tf_str.clone(), e.to_string()));
                    completed_slots += 1;
                }
            }
        }
    }

    // Final summary log (D-12)
    let elapsed = start_time.elapsed();
    tracing::info!(
        total_bars,
        total_slots,
        errors = errors.len(),
        elapsed_secs = format!("{:.1}", elapsed.as_secs_f64()),
        "fetch complete"
    );

    // Log individual errors for diagnostics
    for (symbol, tf, msg) in &errors {
        tracing::warn!(symbol = %symbol, tf = %tf, error = %msg, "pair error detail");
    }

    Ok(())
}

/// Fetch OHLCV bars for one symbol x timeframe slot with exponential backoff retry.
///
/// Retries up to MAX_RETRIES times. Backoff strategy (per D-11):
/// - 503 / Service Unavailable: base_ms = 60_000 (Dukascopy maintenance window)
/// - Other errors (timeout, connection): base_ms = 2_000
/// - Delay doubles on each attempt: delay_ms = base_ms * (1 << attempt)
async fn fetch_with_retry(
    symbol: &str,
    days: u32,
    tf: Timeframe,
    cache_dir: &Path,
) -> anyhow::Result<Vec<Bar>> {
    let mut attempt = 0u32;
    loop {
        match fetch_ohlcv_cached(symbol, days, tf, cache_dir).await {
            Ok(bars) => return Ok(bars),
            Err(e) if attempt < MAX_RETRIES => {
                let error_str = e.to_string();
                let base_ms: u64 =
                    if error_str.contains("503") || error_str.contains("Service Unavailable") {
                        60_000 // long wait for Dukascopy maintenance
                    } else {
                        2_000 // short wait for transient network errors
                    };
                let delay_ms = base_ms * (1u64 << attempt);

                tracing::warn!(
                    attempt,
                    delay_ms,
                    error = %e,
                    symbol,
                    "retrying"
                );

                tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                attempt += 1;
            }
            Err(e) => return Err(e),
        }
    }
}
