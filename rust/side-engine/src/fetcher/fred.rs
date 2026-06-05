use chrono::NaiveDate;
use reqwest::Client;
use tracing::info;

/// FRED 10Y bond counterpart series by currency pair.
pub fn counterpart_series(pair: &str) -> Option<&'static str> {
    match pair {
        "EURUSD" => Some("IRLTLT01DEm156N"),
        "GBPUSD" => Some("IRLTLT01GBm156N"),
        "USDJPY" => Some("IRLTLT01JPm156N"),
        "AUDUSD" => Some("IRLTLT01AUm156N"),
        "USDCHF" => Some("IRLTLT01CHm156N"),
        "NZDUSD" => Some("IRLTLT01NZm156N"),
        _ => None,
    }
}

/// Parse FRED API JSON response.
pub fn parse_fred_json(json: &str) -> anyhow::Result<Vec<(NaiveDate, f64)>> {
    let v: serde_json::Value = serde_json::from_str(json)?;
    let obs = v["observations"]
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("missing observations"))?;
    let mut points = Vec::new();
    for o in obs {
        let date_str = o["date"].as_str().unwrap_or("");
        let val_str = o["value"].as_str().unwrap_or(".");
        if val_str == "." {
            continue;
        }
        if let (Ok(date), Ok(val)) = (
            NaiveDate::parse_from_str(date_str, "%Y-%m-%d"),
            val_str.parse::<f64>(),
        ) {
            points.push((date, val));
        }
    }
    Ok(points)
}

/// Fetch a FRED series. Requires FRED_API_KEY env var.
pub async fn fetch_series(series_id: &str, days: u32) -> anyhow::Result<Vec<(NaiveDate, f64)>> {
    let api_key =
        std::env::var("FRED_API_KEY").map_err(|_| anyhow::anyhow!("FRED_API_KEY not set"))?;
    let start = (chrono::Utc::now() - chrono::TimeDelta::days(days as i64))
        .format("%Y-%m-%d")
        .to_string();
    let url = format!(
        "https://api.stlouisfed.org/fred/series/observations?series_id={}&api_key={}&file_type=json&observation_start={}",
        series_id, api_key, start,
    );
    let client = Client::new();
    let body = client.get(&url).send().await?.text().await?;
    let points = parse_fred_json(&body)?;
    info!(series_id, points = points.len(), "FRED fetch complete");
    Ok(points)
}

/// Fetch rate differential (US 10Y minus foreign 10Y).
pub async fn fetch_rate_diff(pair: &str, days: u32) -> anyhow::Result<Vec<(NaiveDate, f64)>> {
    let foreign_id = counterpart_series(pair)
        .ok_or_else(|| anyhow::anyhow!("no FRED counterpart for {pair}"))?;
    let (us, foreign) =
        tokio::try_join!(fetch_series("DGS10", days), fetch_series(foreign_id, days),)?;
    let foreign_map: std::collections::HashMap<_, _> = foreign.into_iter().collect();
    let diff: Vec<_> = us
        .into_iter()
        .filter_map(|(date, us_val)| foreign_map.get(&date).map(|f_val| (date, us_val - f_val)))
        .collect();
    Ok(diff)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_fred_observations() {
        let json = r#"{"observations": [
            {"date": "2025-03-20", "value": "4.25"},
            {"date": "2025-03-21", "value": "4.30"},
            {"date": "2025-03-22", "value": "."}
        ]}"#;
        let points = parse_fred_json(json).unwrap();
        assert_eq!(points.len(), 2);
        assert!((points[0].1 - 4.25).abs() < 1e-6);
    }

    #[test]
    fn test_rate_diff_counterpart() {
        assert_eq!(counterpart_series("USDJPY"), Some("IRLTLT01JPm156N"));
        assert_eq!(counterpart_series("EURUSD"), Some("IRLTLT01DEm156N"));
        assert_eq!(counterpart_series("UNKNOWN"), None);
    }
}
