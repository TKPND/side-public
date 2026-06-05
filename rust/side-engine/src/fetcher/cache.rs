use crate::fetcher::types::Bar;
use std::path::Path;

/// Save bars to a CSV file under `cache_dir/<key>.csv`.
pub fn save_csv(cache_dir: &Path, key: &str, bars: &[Bar]) -> anyhow::Result<()> {
    std::fs::create_dir_all(cache_dir)?;
    let path = cache_dir.join(format!("{key}.csv"));
    let mut wtr = csv::Writer::from_path(&path)?;
    for bar in bars {
        wtr.serialize(bar)?;
    }
    wtr.flush()?;
    Ok(())
}

/// Load bars from `cache_dir/<key>.csv` if it exists and is not older than `ttl_hours`.
/// Returns `None` if the file is missing or expired.
pub fn load_csv(cache_dir: &Path, key: &str, ttl_hours: u64) -> anyhow::Result<Option<Vec<Bar>>> {
    let path = cache_dir.join(format!("{key}.csv"));
    if !path.exists() {
        return Ok(None);
    }
    let metadata = std::fs::metadata(&path)?;
    let modified = metadata.modified()?;
    let age = std::time::SystemTime::now()
        .duration_since(modified)
        .unwrap_or(std::time::Duration::MAX);
    if age > std::time::Duration::from_secs(ttl_hours * 3600) {
        return Ok(None);
    }
    let mut rdr = csv::Reader::from_path(&path)?;
    let bars: Result<Vec<Bar>, _> = rdr.deserialize().collect();
    Ok(Some(bars?))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::NaiveDate;

    fn make_test_bars() -> Vec<Bar> {
        let dt = NaiveDate::from_ymd_opt(2025, 1, 1)
            .unwrap()
            .and_hms_opt(0, 0, 0)
            .unwrap();
        vec![
            Bar {
                datetime: dt,
                open: 100.0,
                high: 105.0,
                low: 99.0,
                close: 103.0,
                volume: 10.0,
            },
            Bar {
                datetime: dt + chrono::Duration::hours(1),
                open: 103.0,
                high: 107.0,
                low: 102.0,
                close: 106.0,
                volume: 8.0,
            },
        ]
    }

    fn unique_cache_dir() -> std::path::PathBuf {
        std::path::PathBuf::from(format!(
            "/tmp/claude-1000/side_cache_test_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .subsec_nanos()
        ))
    }

    #[test]
    fn test_cache_roundtrip() {
        let dir = unique_cache_dir();
        let bars = make_test_bars();

        save_csv(&dir, "USDJPY_1h", &bars).expect("save should succeed");

        let loaded = load_csv(&dir, "USDJPY_1h", 24)
            .expect("load should not error")
            .expect("cache should be present");

        assert_eq!(loaded.len(), bars.len());
        assert!((loaded[0].open - bars[0].open).abs() < 1e-6);
        assert!((loaded[0].high - bars[0].high).abs() < 1e-6);
        assert!((loaded[0].low - bars[0].low).abs() < 1e-6);
        assert!((loaded[0].close - bars[0].close).abs() < 1e-6);
        assert!((loaded[0].volume - bars[0].volume).abs() < 1e-6);
        assert!((loaded[1].open - bars[1].open).abs() < 1e-6);

        // Clean up
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_cache_missing() {
        let dir = unique_cache_dir();
        let result = load_csv(&dir, "NONEXISTENT_1h", 24).expect("load should not error");
        assert!(result.is_none());
    }
}
