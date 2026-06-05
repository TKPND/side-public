//! Mirror HTTP fetcher — fetches OHLCV bars from a side-mirror HTTP API.
//!
//! Reads from `GET {base}/ohlcv/{SYMBOL}?tf={tf}&days={days}`.
//! Falls back to Dukascopy when the caller wraps this in `fetch_ohlcv_with_fallback`.

use anyhow::Context as _;
use chrono::NaiveDateTime;
use serde::Deserialize;

use crate::fetcher::types::{Bar, Timeframe};

#[derive(Debug, Deserialize)]
struct MirrorBarJson {
    pub datetime: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

#[derive(Debug, Deserialize)]
struct MirrorResponse {
    pub bars: Vec<MirrorBarJson>,
}

/// Fetch OHLCV bars from the mirror HTTP API.
///
/// # Arguments
/// * `mirror_base_url` – Base URL of the mirror server (trailing `/` is trimmed automatically).
/// * `symbol` – Asset symbol (e.g. `"USDJPY"`). Uppercased before use.
/// * `days` – Number of calendar days to request.
/// * `timeframe` – OHLCV bar size.
pub async fn fetch_ohlcv(
    mirror_base_url: &str,
    symbol: &str,
    days: u32,
    timeframe: Timeframe,
) -> anyhow::Result<Vec<Bar>> {
    let base = mirror_base_url.trim_end_matches('/');
    let url = format!(
        "{}/ohlcv/{}?tf={}&days={}",
        base,
        symbol.to_uppercase(),
        timeframe.as_str(),
        days
    );

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .context("failed to build reqwest client")?;

    let resp = client
        .get(&url)
        .send()
        .await
        .with_context(|| format!("GET {url} failed"))?;

    let status = resp.status();
    if !status.is_success() {
        anyhow::bail!("mirror returned HTTP {}", status);
    }

    let body = resp
        .json::<MirrorResponse>()
        .await
        .with_context(|| format!("failed to parse JSON from {url}"))?;

    let bars: anyhow::Result<Vec<Bar>> = body
        .bars
        .into_iter()
        .map(|b| {
            let dt = NaiveDateTime::parse_from_str(&b.datetime, "%Y-%m-%dT%H:%M:%S")
                .with_context(|| format!("invalid datetime: {}", b.datetime))?;
            Ok(Bar {
                datetime: dt,
                open: b.open,
                high: b.high,
                low: b.low,
                close: b.close,
                volume: b.volume,
            })
        })
        .collect();

    bars
}

// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn parse_bars_from_json(json: &str) -> anyhow::Result<Vec<Bar>> {
        let resp: MirrorResponse = serde_json::from_str(json)?;
        resp.bars
            .into_iter()
            .map(|b| {
                let dt = NaiveDateTime::parse_from_str(&b.datetime, "%Y-%m-%dT%H:%M:%S")
                    .with_context(|| format!("invalid datetime: {}", b.datetime))?;
                Ok(Bar {
                    datetime: dt,
                    open: b.open,
                    high: b.high,
                    low: b.low,
                    close: b.close,
                    volume: b.volume,
                })
            })
            .collect()
    }

    #[test]
    fn valid_json_parses_three_bars() {
        let json = r#"{
            "asset": "USDJPY",
            "tf": "1h",
            "count": 3,
            "bars": [
                { "datetime": "2026-03-24T10:00:00", "open": 150.1, "high": 150.5, "low": 149.8, "close": 150.3, "volume": 100.0 },
                { "datetime": "2026-03-24T11:00:00", "open": 150.3, "high": 150.7, "low": 150.0, "close": 150.6, "volume": 110.0 },
                { "datetime": "2026-03-24T12:00:00", "open": 150.6, "high": 151.0, "low": 150.2, "close": 150.9, "volume": 90.0 }
            ]
        }"#;

        let bars = parse_bars_from_json(json).unwrap();
        assert_eq!(bars.len(), 3);
        assert_eq!(
            bars[0].datetime,
            NaiveDateTime::parse_from_str("2026-03-24T10:00:00", "%Y-%m-%dT%H:%M:%S").unwrap()
        );
        assert!((bars[0].open - 150.1).abs() < 1e-9);
        assert!((bars[0].close - 150.3).abs() < 1e-9);
    }

    #[test]
    fn empty_bars_array_returns_empty_vec() {
        let json = r#"{"asset": "USDJPY", "tf": "1h", "count": 0, "bars": []}"#;
        let bars = parse_bars_from_json(json).unwrap();
        assert!(bars.is_empty());
    }

    #[test]
    fn invalid_datetime_string_returns_err() {
        let json = r#"{"bars": [{"datetime": "not-a-datetime", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}]}"#;
        let result = parse_bars_from_json(json);
        assert!(result.is_err());
        let msg = result.unwrap_err().to_string();
        assert!(msg.contains("invalid datetime") || msg.contains("not-a-datetime"));
    }

    #[test]
    fn url_construction_trims_trailing_slash() {
        // Verify the trim logic directly (the actual URL is constructed inside fetch_ohlcv).
        let base = "http://host:8080/";
        let trimmed = base.trim_end_matches('/');
        let url = format!("{}/ohlcv/USDJPY?tf=1h&days=365", trimmed);
        assert_eq!(url, "http://host:8080/ohlcv/USDJPY?tf=1h&days=365");
        // No double slash
        assert!(!url.contains("//ohlcv"));
    }

    /// Full HTTP round-trip test — requires a running mirror server at SIDE_MIRROR_URL.
    #[tokio::test]
    #[ignore]
    async fn integration_fetch_from_running_mirror() {
        let mirror_url = std::env::var("SIDE_MIRROR_URL")
            .expect("SIDE_MIRROR_URL must be set for this integration test");
        let bars = fetch_ohlcv(&mirror_url, "USDJPY", 7, Timeframe::H1)
            .await
            .unwrap();
        assert!(!bars.is_empty(), "expected bars from mirror");
    }
}
