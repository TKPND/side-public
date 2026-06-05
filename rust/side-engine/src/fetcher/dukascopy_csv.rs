/// CSV-based tick loader for Dukascopy-style tick data.
///
/// Loads tick data from CSV files with columns:
///   timestamp, bidPrice, askPrice, bidVolume, askVolume
///
/// The `price` field in Tick is computed as the mid-price: (bid + ask) / 2.
/// The `volume` field is bidVolume + askVolume.
/// The `datetime_ms` is parsed from the timestamp column (RFC3339 / ISO8601 with tz offset).
use crate::fetcher::types::Tick;
use std::path::Path;

/// Load ticks from a single CSV file.
///
/// CSV format (header required):
///   timestamp,bidPrice,askPrice,bidVolume,askVolume
///
/// Timestamps are parsed as UTC milliseconds. The `+00:00` suffix and
/// microsecond precision are both handled.
pub fn load_ticks_from_csv(path: &Path) -> anyhow::Result<Vec<Tick>> {
    use anyhow::Context;

    let content = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read CSV: {}", path.display()))?;

    parse_csv_content(&content).with_context(|| format!("failed to parse CSV: {}", path.display()))
}

/// Load ticks from multiple CSV files matched by a glob pattern.
///
/// Files are loaded in sorted order and ticks are merged (sorted by datetime_ms).
///
/// Supports shell-style brace expansion in the pattern, e.g.:
///   `../data/ticks_{10,11,12}.csv` expands to three separate glob patterns.
pub fn load_ticks_from_csv_glob(pattern: &str) -> anyhow::Result<Vec<Tick>> {
    use anyhow::Context;

    // Expand brace expressions like `foo_{a,b,c}.csv` into individual patterns.
    let patterns = expand_braces(pattern);

    let mut sorted_paths: Vec<std::path::PathBuf> = Vec::new();

    for pat in &patterns {
        let paths = glob::glob(pat).with_context(|| format!("invalid glob pattern: {pat}"))?;
        let mut matched: Vec<std::path::PathBuf> = paths.filter_map(|entry| entry.ok()).collect();
        sorted_paths.append(&mut matched);
    }

    sorted_paths.sort();
    sorted_paths.dedup();

    let mut all_ticks: Vec<Tick> = Vec::new();
    let file_count = sorted_paths.len();

    for path in sorted_paths {
        let ticks = load_ticks_from_csv(&path)
            .with_context(|| format!("error loading {}", path.display()))?;
        all_ticks.extend(ticks);
    }

    if file_count == 0 {
        anyhow::bail!("no CSV files matched pattern: {pattern}");
    }

    all_ticks.sort_by_key(|t| t.datetime_ms);
    Ok(all_ticks)
}

/// Expand a single brace expression `prefix{a,b,c}suffix` into multiple patterns.
/// Only the first brace pair is expanded (no nested braces).
/// If no braces found, returns the original pattern unchanged.
fn expand_braces(pattern: &str) -> Vec<String> {
    if let Some(open) = pattern.find('{') {
        if let Some(close) = pattern[open..].find('}') {
            let close = open + close;
            let prefix = &pattern[..open];
            let suffix = &pattern[close + 1..];
            let choices = &pattern[open + 1..close];
            return choices
                .split(',')
                .map(|choice| format!("{prefix}{choice}{suffix}"))
                .collect();
        }
    }
    vec![pattern.to_string()]
}

/// Parse CSV content string into ticks. Exposed for unit testing.
fn parse_csv_content(content: &str) -> anyhow::Result<Vec<Tick>> {
    let mut lines = content.lines();

    // Parse header
    let header = lines.next().ok_or_else(|| anyhow::anyhow!("empty CSV"))?;
    let cols: Vec<&str> = header.split(',').collect();

    let ts_idx = find_col(&cols, &["timestamp"])
        .ok_or_else(|| anyhow::anyhow!("missing 'timestamp' column"))?;
    let bid_idx = find_col(&cols, &["bidprice", "bid_price", "bid"])
        .ok_or_else(|| anyhow::anyhow!("missing bid price column"))?;
    let ask_idx = find_col(&cols, &["askprice", "ask_price", "ask"])
        .ok_or_else(|| anyhow::anyhow!("missing ask price column"))?;
    let bid_vol_idx = find_col(&cols, &["bidvolume", "bid_volume"]);
    let ask_vol_idx = find_col(&cols, &["askvolume", "ask_volume"]);

    let mut ticks = Vec::new();

    for (line_no, line) in lines.enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        let fields: Vec<&str> = line.split(',').collect();
        let max_idx = [ts_idx, bid_idx, ask_idx]
            .iter()
            .chain(bid_vol_idx.iter())
            .chain(ask_vol_idx.iter())
            .copied()
            .max()
            .unwrap_or(0);

        if fields.len() <= max_idx {
            anyhow::bail!(
                "line {}: expected at least {} columns, got {}",
                line_no + 2,
                max_idx + 1,
                fields.len()
            );
        }

        let datetime_ms = parse_timestamp_ms(fields[ts_idx]).map_err(|e| {
            anyhow::anyhow!(
                "line {}: bad timestamp '{}': {}",
                line_no + 2,
                fields[ts_idx],
                e
            )
        })?;

        let bid: f64 = fields[bid_idx].parse().map_err(|e| {
            anyhow::anyhow!(
                "line {}: bad bidPrice '{}': {}",
                line_no + 2,
                fields[bid_idx],
                e
            )
        })?;
        let ask: f64 = fields[ask_idx].parse().map_err(|e| {
            anyhow::anyhow!(
                "line {}: bad askPrice '{}': {}",
                line_no + 2,
                fields[ask_idx],
                e
            )
        })?;

        let bid_vol: f64 = bid_vol_idx
            .map(|i| fields[i].parse::<f64>().unwrap_or(0.0))
            .unwrap_or(0.0);
        let ask_vol: f64 = ask_vol_idx
            .map(|i| fields[i].parse::<f64>().unwrap_or(0.0))
            .unwrap_or(0.0);

        let price = (bid + ask) / 2.0;
        let volume = bid_vol + ask_vol;

        ticks.push(Tick {
            datetime_ms,
            price,
            volume,
        });
    }

    Ok(ticks)
}

/// Find column index by name (case-insensitive).
fn find_col(cols: &[&str], names: &[&str]) -> Option<usize> {
    cols.iter().position(|c| {
        let lower = c.trim().to_lowercase();
        names.iter().any(|n| lower == *n)
    })
}

/// Parse a timestamp string to epoch milliseconds.
///
/// Handles:
///   - "2025-09-30 15:00:00.080000+00:00"  (Python datetime with microseconds)
///   - "2025-09-30T15:00:00.080+00:00"      (ISO8601)
///   - "2025-09-30 15:00:00+00:00"          (space-separated, no micros)
fn parse_timestamp_ms(s: &str) -> anyhow::Result<i64> {
    use chrono::{DateTime, FixedOffset};

    // Normalise: replace space separator with T for chrono parsing
    let normalised = s.replace(' ', "T");

    let dt = DateTime::<FixedOffset>::parse_from_rfc3339(&normalised)
        .map_err(|e| anyhow::anyhow!("rfc3339 parse error: {e}"))?;

    Ok(dt.timestamp_millis())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    fn make_csv(content: &str) -> NamedTempFile {
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(content.as_bytes()).unwrap();
        f
    }

    #[test]
    fn test_load_basic_csv() {
        let csv = "timestamp,bidPrice,askPrice,bidVolume,askVolume\n\
                   2025-10-01 08:00:00.000000+00:00,147.500,147.510,1.0,2.0\n\
                   2025-10-01 08:00:01.000000+00:00,147.510,147.520,1.5,1.5\n";
        let f = make_csv(csv);
        let ticks = load_ticks_from_csv(f.path()).unwrap();
        assert_eq!(ticks.len(), 2);

        // First tick: mid = (147.500 + 147.510) / 2 = 147.505
        assert!((ticks[0].price - 147.505).abs() < 1e-6);
        assert!((ticks[0].volume - 3.0).abs() < 1e-6);

        // Second tick
        assert!((ticks[1].price - 147.515).abs() < 1e-6);
        assert!((ticks[1].volume - 3.0).abs() < 1e-6);

        // Timestamps: 2025-10-01 08:00:00 UTC
        let expected_ms = chrono::DateTime::parse_from_rfc3339("2025-10-01T08:00:00+00:00")
            .unwrap()
            .timestamp_millis();
        assert_eq!(ticks[0].datetime_ms, expected_ms);
    }

    #[test]
    fn test_load_csv_empty_lines_skipped() {
        let csv = "timestamp,bidPrice,askPrice,bidVolume,askVolume\n\
                   2025-10-01 08:00:00.000000+00:00,147.500,147.510,1.0,2.0\n\
                   \n\
                   2025-10-01 08:00:02.000000+00:00,147.520,147.530,2.0,1.0\n";
        let f = make_csv(csv);
        let ticks = load_ticks_from_csv(f.path()).unwrap();
        assert_eq!(ticks.len(), 2);
    }

    #[test]
    fn test_load_csv_missing_file() {
        let result = load_ticks_from_csv(Path::new("/nonexistent/path.csv"));
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("failed to read CSV"));
    }

    #[test]
    fn test_load_csv_bad_timestamp() {
        let csv = "timestamp,bidPrice,askPrice,bidVolume,askVolume\n\
                   not-a-date,147.500,147.510,1.0,2.0\n";
        let f = make_csv(csv);
        let result = load_ticks_from_csv(f.path());
        assert!(result.is_err());
    }

    #[test]
    fn test_load_csv_real_schema() {
        // Match the actual data/bq_ticks/ schema exactly
        let csv = "timestamp,bidPrice,askPrice,bidVolume,askVolume\n\
                   2025-09-30 15:00:00.080000+00:00,147.686,147.69,1.11,2.7\n\
                   2025-09-30 15:00:00.237000+00:00,147.688,147.692,1.11,1.2\n";
        let f = make_csv(csv);
        let ticks = load_ticks_from_csv(f.path()).unwrap();
        assert_eq!(ticks.len(), 2);
        // mid = (147.686 + 147.69) / 2 = 147.688
        assert!((ticks[0].price - 147.688).abs() < 1e-4);
        assert!((ticks[0].volume - 3.81).abs() < 1e-4);
    }

    #[test]
    fn test_glob_no_match_errors() {
        let result = load_ticks_from_csv_glob("/nonexistent/dir/*.csv");
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("no CSV files matched"));
    }

    #[test]
    fn test_glob_multiple_files_sorted() {
        // Create two temp files; glob is done via explicit paths so we test merge/sort logic
        let csv_a = "timestamp,bidPrice,askPrice,bidVolume,askVolume\n\
                     2025-10-01 08:00:01.000000+00:00,147.500,147.510,1.0,2.0\n";
        let csv_b = "timestamp,bidPrice,askPrice,bidVolume,askVolume\n\
                     2025-10-01 08:00:00.000000+00:00,147.490,147.500,1.0,1.0\n";

        let dir = tempfile::tempdir().unwrap();
        let path_a = dir.path().join("ticks_b.csv"); // intentionally 'b' so name-sort reverses
        let path_c = dir.path().join("ticks_a.csv"); // 'a' sorts first
        std::fs::write(&path_a, csv_a).unwrap();
        std::fs::write(&path_c, csv_b).unwrap();

        let pattern = format!("{}/{}", dir.path().display(), "ticks_*.csv");
        let ticks = load_ticks_from_csv_glob(&pattern).unwrap();
        assert_eq!(ticks.len(), 2);
        // Result must be sorted by datetime_ms regardless of file order
        assert!(ticks[0].datetime_ms <= ticks[1].datetime_ms);
    }
}
