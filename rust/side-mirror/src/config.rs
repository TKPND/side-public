//! Configuration loading for side-mirror daemon.

use anyhow::Context;
use serde::Deserialize;
use std::path::Path;

/// Top-level configuration for the mirror daemon.
#[derive(Debug, Deserialize)]
pub struct MirrorConfig {
    pub global: GlobalConfig,
    pub pairs: Vec<PairConfig>,
}

/// Global settings applied to all pairs.
#[derive(Debug, Deserialize)]
pub struct GlobalConfig {
    /// Directory where cached OHLCV data is stored.
    pub data_dir: String,
    /// How often the daemon polls for new bars (minutes).
    pub interval_minutes: u64,
    /// Number of days to backfill on first run.
    #[serde(default = "default_backfill_days")]
    pub backfill_days: u32,
}

fn default_backfill_days() -> u32 {
    365
}

/// Per-pair configuration.
#[derive(Debug, Deserialize)]
pub struct PairConfig {
    /// Symbol string passed to Dukascopy fetcher (e.g. "USDJPY").
    pub symbol: String,
    /// Timeframe strings to fetch (e.g. ["1h", "4h", "1d"]).
    pub timeframes: Vec<String>,
}

/// Load and parse mirror.toml from the given path.
pub fn load_config(path: &Path) -> anyhow::Result<MirrorConfig> {
    let content = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read config file: {}", path.display()))?;
    let config: MirrorConfig = toml::from_str(&content)
        .with_context(|| format!("failed to parse config file: {}", path.display()))?;
    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    fn write_temp_toml(content: &str) -> NamedTempFile {
        let mut f = NamedTempFile::new().expect("failed to create temp file");
        f.write_all(content.as_bytes())
            .expect("failed to write temp file");
        f
    }

    #[test]
    fn load_config_with_valid_toml_parses_correctly() {
        let toml = r#"
[global]
data_dir = "data/mirror"
interval_minutes = 60
backfill_days = 180

[[pairs]]
symbol = "USDJPY"
timeframes = ["1h", "4h", "1d"]

[[pairs]]
symbol = "EURUSD"
timeframes = ["1h"]
"#;
        let f = write_temp_toml(toml);
        let cfg = load_config(f.path()).expect("should parse valid TOML");

        assert_eq!(cfg.global.data_dir, "data/mirror");
        assert_eq!(cfg.global.interval_minutes, 60);
        assert_eq!(cfg.global.backfill_days, 180);
        assert_eq!(cfg.pairs.len(), 2);
        assert_eq!(cfg.pairs[0].symbol, "USDJPY");
        assert_eq!(cfg.pairs[0].timeframes, vec!["1h", "4h", "1d"]);
        assert_eq!(cfg.pairs[1].symbol, "EURUSD");
        assert_eq!(cfg.pairs[1].timeframes, vec!["1h"]);
    }

    #[test]
    fn load_config_missing_file_returns_descriptive_error() {
        let path = Path::new("/nonexistent/path/mirror.toml");
        let err = load_config(path).expect_err("should fail for missing file");
        let msg = err.to_string();
        assert!(
            msg.contains("failed to read config file"),
            "expected descriptive error, got: {msg}"
        );
    }

    #[test]
    fn load_config_invalid_toml_returns_parse_error() {
        let toml = "this is not valid TOML!!!===";
        let f = write_temp_toml(toml);
        let err = load_config(f.path()).expect_err("should fail for invalid TOML");
        let msg = err.to_string();
        assert!(
            msg.contains("failed to parse config file"),
            "expected parse error, got: {msg}"
        );
    }

    #[test]
    fn pair_config_timeframes_are_string_vec() {
        let toml = r#"
[global]
data_dir = "data"
interval_minutes = 30

[[pairs]]
symbol = "GBPUSD"
timeframes = ["1h", "4h"]
"#;
        let f = write_temp_toml(toml);
        let cfg = load_config(f.path()).expect("should parse");
        let tf = &cfg.pairs[0].timeframes;
        assert_eq!(tf[0], "1h");
        assert_eq!(tf[1], "4h");
    }

    #[test]
    fn global_config_default_backfill_days_is_365_when_omitted() {
        let toml = r#"
[global]
data_dir = "data"
interval_minutes = 60

[[pairs]]
symbol = "USDJPY"
timeframes = ["1h"]
"#;
        let f = write_temp_toml(toml);
        let cfg = load_config(f.path()).expect("should parse");
        assert_eq!(
            cfg.global.backfill_days, 365,
            "default backfill_days should be 365"
        );
    }
}
