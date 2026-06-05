use crate::fetcher::types::{Bar, Tick, Timeframe};
use chrono::{Datelike, Timelike, Weekday};
use std::collections::BTreeMap;

/// Returns the price scale factor for a given symbol.
/// BTC/ETH crypto use 100.0 (measured: ask_raw=777800 / 100 = 7778.00 USD).
/// XAU and XAG use 1000.0; all others use 100_000.0.
pub fn price_scale(symbol: &str) -> f64 {
    if symbol.starts_with("BTC") || symbol.starts_with("ETH") {
        100.0 // 108-01 実測値: ask_raw=777800 / 100 = 7778.00 USD
    } else if symbol.starts_with("XAU") || symbol.starts_with("XAG") {
        1_000.0
    } else {
        100_000.0
    }
}

/// Decode raw bi5 tick bytes into a Vec<Tick>.
///
/// Each record is 20 bytes, big-endian:
///   u32  time_ms_offset  (ms since hour start)
///   u32  ask_raw
///   u32  bid_raw
///   f32  ask_vol
///   f32  bid_vol
///
/// `scale`        — price divisor (from `price_scale`)
/// `hour_epoch_ms` — epoch ms of the hour start
pub fn decode_bi5_ticks(data: &[u8], scale: f64, hour_epoch_ms: i64) -> Vec<Tick> {
    const RECORD_LEN: usize = 20;
    if data.len() < RECORD_LEN {
        return Vec::new();
    }

    let n = data.len() / RECORD_LEN;
    let mut ticks = Vec::with_capacity(n);

    for i in 0..n {
        let offset = i * RECORD_LEN;
        let chunk = &data[offset..offset + RECORD_LEN];

        let time_ms_offset = u32::from_be_bytes(chunk[0..4].try_into().unwrap()) as i64;
        let ask_raw = u32::from_be_bytes(chunk[4..8].try_into().unwrap()) as f64;
        let bid_raw = u32::from_be_bytes(chunk[8..12].try_into().unwrap()) as f64;
        let ask_vol = f32::from_be_bytes(chunk[12..16].try_into().unwrap()) as f64;
        let bid_vol = f32::from_be_bytes(chunk[16..20].try_into().unwrap()) as f64;

        let ask = ask_raw / scale;
        let bid = bid_raw / scale;
        let mid = (ask + bid) / 2.0;
        let volume = ask_vol + bid_vol;

        ticks.push(Tick {
            datetime_ms: hour_epoch_ms + time_ms_offset,
            price: mid,
            volume,
        });
    }

    ticks
}

/// Decompress LZMA-compressed bi5 data.
pub fn decompress_bi5(compressed: &[u8]) -> anyhow::Result<Vec<u8>> {
    let mut output = Vec::new();
    let mut cursor = std::io::Cursor::new(compressed);
    lzma_rs::lzma_decompress(&mut cursor, &mut output)?;
    Ok(output)
}

/// Build the Dukascopy URL for a given symbol/hour.
/// Month is 0-indexed in the URL (Jan = 00).
pub fn hour_url(symbol: &str, year: u32, month: u32, day: u32, hour: u32) -> String {
    format!(
        "https://datafeed.dukascopy.com/datafeed/{symbol}/{year}/{month:02}/{day:02}/{hour:02}h_ticks.bi5",
        symbol = symbol,
        year = year,
        month = month - 1,   // 0-indexed
        day = day,
        hour = hour,
    )
}

/// Aggregate a slice of ticks into OHLCV bars for the given timeframe.
///
/// Ticks are bucketed by floor(datetime_ms / period_ms) * period_ms.
/// Uses a BTreeMap so buckets are iterated in chronological order.
pub fn aggregate_ticks(ticks: &[Tick], tf: Timeframe) -> Vec<Bar> {
    let period_ms = tf.minutes() * 60 * 1_000;
    let mut buckets: BTreeMap<i64, (f64, f64, f64, f64, f64)> = BTreeMap::new();
    // bucket value: (open, high, low, close, volume)

    for tick in ticks {
        let bucket_ms = (tick.datetime_ms / period_ms) * period_ms;
        buckets
            .entry(bucket_ms)
            .and_modify(|(_, high, low, close, vol)| {
                if tick.price > *high {
                    *high = tick.price;
                }
                if tick.price < *low {
                    *low = tick.price;
                }
                *close = tick.price;
                *vol += tick.volume;
            })
            .or_insert((tick.price, tick.price, tick.price, tick.price, tick.volume));
    }

    buckets
        .into_iter()
        .map(|(bucket_ms, (open, high, low, close, volume))| {
            let datetime = chrono::DateTime::from_timestamp_millis(bucket_ms)
                .expect("valid timestamp")
                .naive_utc();
            Bar {
                datetime,
                open,
                high,
                low,
                close,
                volume,
            }
        })
        .collect()
}

/// Check if a UTC timestamp falls on a FX market closed period.
/// FX market closes Friday 22:00 UTC and opens Sunday 22:00 UTC.
fn is_fx_closed(hour_ms: i64) -> bool {
    let dt = chrono::DateTime::from_timestamp_millis(hour_ms)
        .expect("valid timestamp")
        .with_timezone(&chrono::Utc);
    let weekday = dt.weekday();
    let hour = dt.hour();
    matches!(
        (weekday, hour),
        (Weekday::Fri, 22..) | (Weekday::Sat, _) | (Weekday::Sun, 0..=21)
    )
}

/// Symbol-aware market closed check.
/// Crypto pairs (BTC/ETH) trade 24/7 and are never closed.
pub fn is_fx_closed_for(symbol: &str, hour_ms: i64) -> bool {
    use std::str::FromStr;
    if let Ok(p) = crate::pair::Pair::from_str(symbol) {
        if p.is_24_7() {
            return false; // crypto は never closed
        }
    }
    is_fx_closed(hour_ms)
}

/// Fetch a single hour of tick data from Dukascopy.
/// Returns empty vec on 404/503 (weekend/holiday/maintenance).
/// Retries up to 3 times with exponential backoff on transient errors.
async fn fetch_single_hour(
    client: &reqwest::Client,
    url: &str,
    scale: f64,
    hour_epoch_ms: i64,
) -> anyhow::Result<Vec<Tick>> {
    let mut retries = 0u32;
    loop {
        match client.get(url).send().await {
            Ok(resp) => {
                let status = resp.status();
                if status == reqwest::StatusCode::NOT_FOUND
                    || status == reqwest::StatusCode::SERVICE_UNAVAILABLE
                {
                    return Ok(Vec::new());
                }
                if !status.is_success() {
                    anyhow::bail!("HTTP {} for {}", status, url);
                }
                let bytes = resp.bytes().await?;
                if bytes.is_empty() {
                    return Ok(Vec::new());
                }
                let decompressed = decompress_bi5(&bytes)?;
                let ticks = decode_bi5_ticks(&decompressed, scale, hour_epoch_ms);
                return Ok(ticks);
            }
            Err(e) if retries < 3 => {
                let delay_ms = 500u64 * (1 << retries);
                tokio::time::sleep(
                    chrono::TimeDelta::milliseconds(delay_ms as i64)
                        .to_std()
                        .unwrap(),
                )
                .await;
                retries += 1;
                tracing::warn!("retry {retries} for {url}: {e}");
            }
            Err(e) => return Err(e.into()),
        }
    }
}

/// Fetch OHLCV bars for the given symbol over the last `days` days.
///
/// Downloads hourly bi5 files from Dukascopy in parallel batches of 8,
/// aggregates all ticks into bars of the requested timeframe.
pub async fn fetch_ohlcv(
    symbol: &str,
    days: u32,
    timeframe: Timeframe,
) -> anyhow::Result<Vec<Bar>> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(15))
        .build()?;

    let now = chrono::Utc::now();
    let start = now - chrono::Duration::days(days as i64);

    // Enumerate all hour timestamps from start to now.
    // Truncate start to the hour boundary.
    let mut hour_timestamps: Vec<i64> = Vec::new();
    let mut cursor = start
        .with_minute(0)
        .and_then(|t: chrono::DateTime<chrono::Utc>| t.with_second(0))
        .and_then(|t: chrono::DateTime<chrono::Utc>| t.with_nanosecond(0))
        .unwrap_or(start);

    while cursor <= now {
        let ms = cursor.timestamp_millis();
        if !is_fx_closed_for(symbol, ms) {
            hour_timestamps.push(ms);
        }
        cursor += chrono::Duration::hours(1);
    }

    let scale = price_scale(symbol);
    let symbol = symbol.to_string();

    // Process in batches of 8
    let mut all_ticks: Vec<Tick> = Vec::new();
    for batch in hour_timestamps.chunks(8) {
        let mut handles = Vec::new();
        for &hour_ms in batch {
            let client = client.clone();
            let symbol = symbol.clone();
            let dt = chrono::DateTime::from_timestamp_millis(hour_ms)
                .expect("valid timestamp")
                .with_timezone(&chrono::Utc);
            let url = hour_url(&symbol, dt.year() as u32, dt.month(), dt.day(), dt.hour());
            handles.push(tokio::spawn(async move {
                fetch_single_hour(&client, &url, scale, hour_ms).await
            }));
        }
        for handle in handles {
            let ticks = handle.await??;
            all_ticks.extend(ticks);
        }
    }

    all_ticks.sort_by_key(|t| t.datetime_ms);
    Ok(aggregate_ticks(&all_ticks, timeframe))
}

/// Fetch OHLCV bars with disk-based caching.
///
/// Loads cached bars from `cache_dir/<symbol>_<timeframe>.csv`, then fetches
/// only the delta (hours after the last cached bar) from Dukascopy.
/// Confirmed bars (all except the last 2) are cached for subsequent calls.
pub async fn fetch_ohlcv_cached(
    symbol: &str,
    days: u32,
    timeframe: Timeframe,
    cache_dir: &std::path::Path,
) -> anyhow::Result<Vec<Bar>> {
    use super::cache;
    use tracing::info;

    let cache_key = format!("{}_{}", symbol, timeframe.as_str());
    let now = chrono::Utc::now();
    let start = now - chrono::Duration::days(days as i64);

    // Load cached bars (TTL = 30 days — we trim by date anyway)
    let cached_bars = cache::load_csv(cache_dir, &cache_key, 30 * 24)
        .unwrap_or(None)
        .unwrap_or_default();

    // Find the cutoff: fetch hours after the last cached bar's timestamp.
    // Keep a 2-bar overlap to handle partially-formed bars.
    let fetch_from = if cached_bars.len() >= 2 {
        let last_cached = cached_bars[cached_bars.len() - 2].datetime;
        last_cached.and_utc()
    } else {
        start
            .with_minute(0)
            .and_then(|t: chrono::DateTime<chrono::Utc>| t.with_second(0))
            .and_then(|t: chrono::DateTime<chrono::Utc>| t.with_nanosecond(0))
            .unwrap_or(start)
    };

    // Filter cached bars to the requested window
    let start_naive = start.naive_utc();
    let mut result_bars: Vec<Bar> = cached_bars
        .into_iter()
        .filter(|b| b.datetime >= start_naive && b.datetime < fetch_from.naive_utc())
        .collect();

    // Enumerate delta hours
    let mut hour_timestamps: Vec<i64> = Vec::new();
    let mut cursor = fetch_from;
    while cursor <= now {
        let ms = cursor.timestamp_millis();
        if !is_fx_closed_for(symbol, ms) {
            hour_timestamps.push(ms);
        }
        cursor += chrono::Duration::hours(1);
    }

    if hour_timestamps.is_empty() {
        info!(
            symbol,
            cached = result_bars.len(),
            delta_hours = 0,
            "cache hit, no delta needed"
        );
        return Ok(result_bars);
    }

    info!(
        symbol,
        cached = result_bars.len(),
        delta_hours = hour_timestamps.len(),
        "fetching delta from Dukascopy"
    );

    // Fetch delta ticks
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(15))
        .build()?;
    let scale = price_scale(symbol);
    let symbol_owned = symbol.to_string();

    let mut all_ticks: Vec<Tick> = Vec::new();
    for batch in hour_timestamps.chunks(8) {
        let mut handles = Vec::new();
        for &hour_ms in batch {
            let client = client.clone();
            let sym = symbol_owned.clone();
            let dt = chrono::DateTime::from_timestamp_millis(hour_ms)
                .expect("valid timestamp")
                .with_timezone(&chrono::Utc);
            let url = hour_url(&sym, dt.year() as u32, dt.month(), dt.day(), dt.hour());
            handles.push(tokio::spawn(async move {
                fetch_single_hour(&client, &url, scale, hour_ms).await
            }));
        }
        for handle in handles {
            let ticks = handle.await??;
            all_ticks.extend(ticks);
        }
    }

    all_ticks.sort_by_key(|t| t.datetime_ms);
    let delta_bars = aggregate_ticks(&all_ticks, timeframe);
    result_bars.extend(delta_bars);

    // Deduplicate by datetime (overlap region)
    result_bars.sort_by_key(|b| b.datetime);
    result_bars.dedup_by_key(|b| b.datetime);

    // Save confirmed bars to cache (exclude last 2 which may be incomplete)
    let confirmed_count = result_bars.len().saturating_sub(2);
    if confirmed_count > 0 {
        let confirmed = &result_bars[..confirmed_count];
        if let Err(e) = cache::save_csv(cache_dir, &cache_key, confirmed) {
            tracing::warn!(error = %e, "failed to save cache");
        }
    }

    Ok(result_bars)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_decode_bi5_tick() {
        let mut data = Vec::new();
        data.extend_from_slice(&1000u32.to_be_bytes());
        data.extend_from_slice(&11050000u32.to_be_bytes());
        data.extend_from_slice(&11049500u32.to_be_bytes());
        data.extend_from_slice(&1.5f32.to_be_bytes());
        data.extend_from_slice(&2.0f32.to_be_bytes());
        let ticks = decode_bi5_ticks(&data, 100_000.0, 0);
        assert_eq!(ticks.len(), 1);
        assert_eq!(ticks[0].datetime_ms, 1000);
        let expected_mid = (110.50 + 110.495) / 2.0;
        assert!((ticks[0].price - expected_mid).abs() < 1e-6);
        assert!((ticks[0].volume - 3.5).abs() < 1e-6);
    }

    #[test]
    fn test_decode_bi5_empty() {
        assert!(decode_bi5_ticks(&[], 100_000.0, 0).is_empty());
    }

    #[test]
    fn test_decode_bi5_metals_scale() {
        let mut data = Vec::new();
        data.extend_from_slice(&0u32.to_be_bytes());
        data.extend_from_slice(&1950500u32.to_be_bytes());
        data.extend_from_slice(&1950000u32.to_be_bytes());
        data.extend_from_slice(&0.1f32.to_be_bytes());
        data.extend_from_slice(&0.2f32.to_be_bytes());
        let ticks = decode_bi5_ticks(&data, 1_000.0, 0);
        let expected_mid = (1950.5 + 1950.0) / 2.0;
        assert!((ticks[0].price - expected_mid).abs() < 1e-6);
    }

    #[test]
    fn test_price_scale() {
        assert_eq!(price_scale("USDJPY"), 100_000.0);
        assert_eq!(price_scale("XAUUSD"), 1_000.0);
        assert_eq!(price_scale("XAGUSD"), 1_000.0);
        assert_eq!(price_scale("EURUSD"), 100_000.0);
    }

    #[test]
    fn test_aggregate_ticks_to_bars_1h() {
        use chrono::NaiveDate;
        let base = NaiveDate::from_ymd_opt(2025, 3, 20)
            .unwrap()
            .and_hms_opt(10, 0, 0)
            .unwrap()
            .and_utc()
            .timestamp_millis();
        let ticks = vec![
            Tick {
                datetime_ms: base,
                price: 150.0,
                volume: 1.0,
            },
            Tick {
                datetime_ms: base + 60_000,
                price: 151.0,
                volume: 2.0,
            },
            Tick {
                datetime_ms: base + 120_000,
                price: 149.0,
                volume: 1.5,
            },
            Tick {
                datetime_ms: base + 3_599_000,
                price: 150.5,
                volume: 0.5,
            },
            Tick {
                datetime_ms: base + 3_600_000,
                price: 152.0,
                volume: 3.0,
            },
        ];
        let bars = aggregate_ticks(&ticks, Timeframe::H1);
        assert_eq!(bars.len(), 2);
        assert!((bars[0].open - 150.0).abs() < 1e-6);
        assert!((bars[0].high - 151.0).abs() < 1e-6);
        assert!((bars[0].low - 149.0).abs() < 1e-6);
        assert!((bars[0].close - 150.5).abs() < 1e-6);
        assert!((bars[0].volume - 5.0).abs() < 1e-6);
        assert!((bars[1].open - 152.0).abs() < 1e-6);
    }

    #[test]
    fn test_is_fx_closed() {
        use chrono::TimeZone;
        // Saturday 12:00 UTC → closed
        let sat = chrono::Utc.with_ymd_and_hms(2026, 3, 21, 12, 0, 0).unwrap();
        assert!(is_fx_closed(sat.timestamp_millis()));
        // Sunday 21:00 UTC → closed
        let sun_21 = chrono::Utc.with_ymd_and_hms(2026, 3, 22, 21, 0, 0).unwrap();
        assert!(is_fx_closed(sun_21.timestamp_millis()));
        // Sunday 22:00 UTC → open
        let sun_22 = chrono::Utc.with_ymd_and_hms(2026, 3, 22, 22, 0, 0).unwrap();
        assert!(!is_fx_closed(sun_22.timestamp_millis()));
        // Friday 21:00 UTC → open
        let fri_21 = chrono::Utc.with_ymd_and_hms(2026, 3, 20, 21, 0, 0).unwrap();
        assert!(!is_fx_closed(fri_21.timestamp_millis()));
        // Friday 22:00 UTC → closed
        let fri_22 = chrono::Utc.with_ymd_and_hms(2026, 3, 20, 22, 0, 0).unwrap();
        assert!(is_fx_closed(fri_22.timestamp_millis()));
        // Wednesday 10:00 UTC → open
        let wed = chrono::Utc.with_ymd_and_hms(2026, 3, 18, 10, 0, 0).unwrap();
        assert!(!is_fx_closed(wed.timestamp_millis()));
    }

    #[tokio::test]
    #[ignore]
    async fn test_fetch_dukascopy_live() {
        let bars = fetch_ohlcv("USDJPY", 3, Timeframe::H1).await.unwrap();
        assert!(!bars.is_empty());
        assert!(bars[0].open > 100.0 && bars[0].open < 200.0);
    }
}
