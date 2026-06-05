use crate::fetcher::types::Bar;
use chrono::NaiveDate;
use std::process::Command;
use tracing::{info, warn};

/// Resolve the path to `scripts/yf_fetch.py` relative to the workspace root.
///
/// Resolution order:
/// 1. `$CARGO_MANIFEST_DIR/../../scripts/yf_fetch.py` — set by `cargo run`, fastest path.
/// 2. Walk up from `current_exe()` until a parent directory contains `scripts/yf_fetch.py`
///    — covers direct binary invocation (`./target/release/side`) from any CWD.
/// 3. CWD-relative `scripts/yf_fetch.py` — last resort fallback.
fn yf_script_path() -> std::path::PathBuf {
    if let Ok(manifest_dir) = std::env::var("CARGO_MANIFEST_DIR") {
        let candidate = std::path::PathBuf::from(manifest_dir).join("../../scripts/yf_fetch.py");
        if candidate.exists() {
            return candidate;
        }
    }

    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent();
        while let Some(d) = dir {
            let candidate = d.join("scripts/yf_fetch.py");
            if candidate.exists() {
                return candidate;
            }
            dir = d.parent();
        }
    }

    std::path::PathBuf::from("scripts/yf_fetch.py")
}

/// Parse CSV lines (date,open,high,low,close,volume) into Bars.
fn parse_csv_lines(output: &str) -> anyhow::Result<Vec<Bar>> {
    let mut bars = Vec::new();
    for line in output.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() < 6 {
            continue;
        }
        let date = NaiveDate::parse_from_str(cols[0], "%Y-%m-%d")?;
        let dt = date.and_hms_opt(0, 0, 0).unwrap();
        bars.push(Bar {
            datetime: dt,
            open: cols[1].parse()?,
            high: cols[2].parse()?,
            low: cols[3].parse()?,
            close: cols[4].parse()?,
            volume: cols[5].parse().unwrap_or(0.0),
        });
    }
    Ok(bars)
}

/// Fetch daily bars from Yahoo Finance via yfinance subprocess.
pub async fn fetch_daily(ticker: &str, days: u32) -> anyhow::Result<Vec<Bar>> {
    let script_path = yf_script_path();
    let ticker_owned = ticker.to_string();
    let days_str = days.to_string();

    let output = tokio::task::spawn_blocking(move || {
        Command::new("uv")
            .args([
                "run",
                "--with",
                "yfinance",
                "python",
                script_path.to_str().unwrap_or("scripts/yf_fetch.py"),
                &ticker_owned,
                &days_str,
            ])
            .output()
    })
    .await??;

    let ticker_owned = ticker.to_string();
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        warn!(ticker = ticker_owned.as_str(), %stderr, "yfinance subprocess failed");
        anyhow::bail!("yfinance failed for {}: {}", ticker_owned, stderr);
    }

    let stdout = String::from_utf8(output.stdout)?;
    let bars = parse_csv_lines(&stdout)?;
    info!(
        ticker = ticker_owned.as_str(),
        bars = bars.len(),
        "Yahoo Finance fetch complete (yfinance)"
    );
    Ok(bars)
}

/// Fetch and return close prices as (datetime_ms, close) pairs for aux_close injection.
pub async fn fetch_aux_close(ticker: &str, days: u32) -> anyhow::Result<Vec<(i64, f64)>> {
    let bars = fetch_daily(ticker, days).await?;
    Ok(bars
        .into_iter()
        .map(|b| (b.datetime.and_utc().timestamp_millis(), b.close))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_csv_lines() {
        let csv = "2025-03-20,18.5,19.2,18.1,18.8,100000\n\
                   2025-03-21,18.9,20.0,18.5,19.5,120000\n";
        let bars = parse_csv_lines(csv).unwrap();
        assert_eq!(bars.len(), 2);
        assert!((bars[0].open - 18.5).abs() < 1e-6);
        assert!((bars[1].close - 19.5).abs() < 1e-6);
    }

    #[test]
    fn test_yf_script_path_resolves() {
        // In test context, CARGO_MANIFEST_DIR is set, so the path should resolve
        let path = yf_script_path();
        assert!(path.to_str().unwrap().contains("yf_fetch.py"));
    }
}
