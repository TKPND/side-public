//! Scan output audit metadata.
//!
//! Embeds binary identity (git rev / dirty / build timestamp) and runtime
//! state (argv / bars / cutoffs / event filter / config mirror) so past
//! scan outputs can be traced back to the exact binary and parameters
//! that produced them.
//!
//! See `docs/superpowers/specs/2026-04-09-scan-audit-metadata-design.md`.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use super::ScanCellResult;

/// Top-level scan output wrapper — embeds audit metadata alongside results.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScanOutput {
    pub metadata: ScanMetadata,
    pub results: Vec<ScanCellResult>,
}

/// Audit metadata captured once per `side scan` invocation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScanMetadata {
    /// Build-time HEAD SHA (full 40-char). "unknown" if git was unavailable.
    pub git_rev: String,

    /// Build-time working tree dirty flag.
    pub git_dirty: bool,

    /// Build-time UTC RFC3339 timestamp of the last FULL rebuild
    /// (incremental builds may leave this stale — acceptable trade-off).
    pub binary_built_at: String,

    /// Runtime argv (raw Vec — preserves args containing spaces).
    pub command_line: Vec<String>,

    /// Per-asset cutoff: RFC3339 timestamp of the LAST bar after all filters.
    pub cutoff_timestamps: BTreeMap<String, String>,

    /// Per-asset bar count after all filters.
    pub bars_counts: BTreeMap<String, usize>,

    /// Per-asset event filter stats. `Some` iff `--exclude-events` was passed.
    pub event_filter: Option<BTreeMap<String, EventFilterStats>>,

    /// Effective ScanConfig (runtime, post-clap-parse).
    pub config_mirror: ScanConfigMirror,
}

/// Per-asset event filter statistics.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventFilterStats {
    /// Number of FOMC + ECB windows loaded (same across assets).
    pub event_windows_count: usize,

    /// Per-asset bar count before event filter (but after days filter).
    pub bars_before: usize,

    /// Per-asset bar count after event filter.
    pub bars_after: usize,

    /// Per-asset difference (bars_before - bars_after).
    pub bars_dropped: usize,
}

/// JSON-friendly mirror of the effective `ScanConfig`.
///
/// Field sources:
/// - `fee_bps`, `trials`, `batch_size`, `mode_i8`, `mc_sims`, `random_n`,
///   `max_pareto_candidates`, `wfd_*`: `ScanConfig` (engine-effective).
/// - `mode`, `days`, `aux`, `tick_csv_glob`, `assets`, `timeframes`,
///   `strategies`, raw `param_spaces_path`: `ScanArgs` or `ScanConfig`.
/// - `param_spaces_path_absolute`: `std::fs::canonicalize`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScanConfigMirror {
    pub assets: Vec<String>,
    pub timeframes: Vec<String>,
    pub strategies: Vec<String>,

    /// From `config.wfd_config.fee_bps` (engine-effective).
    pub fee_bps: f64,

    /// From `config.n_trials` (ScanConfig field name).
    pub trials: usize,

    pub batch_size: usize,

    /// Mode string from ScanArgs.
    pub mode: String,

    /// Engine-parsed i8 (0=both, 1=long_only, 2=short_only).
    pub mode_i8: i8,

    /// From `config.mc_simulations`.
    pub mc_sims: usize,

    /// From `config.random_benchmark_n`.
    pub random_n: usize,

    /// Raw path as configured (may be relative to CWD).
    pub param_spaces_path: String,

    /// Canonicalized absolute path (fallback: raw path on I/O failure).
    pub param_spaces_path_absolute: String,

    pub days: u32,
    pub aux: Option<String>,
    pub tick_csv_glob: Option<String>,

    /// Hardcoded `3` in scan.rs L133 — mirrored for audit.
    pub max_pareto_candidates: usize,

    /// WfdConfig fields (see `rust/side-engine/src/wfd.rs`).
    pub wfd_is_months: usize,
    pub wfd_oos_months: usize,
    pub wfd_num_walks: usize,
    pub wfd_min_oos_pf: f64,
    pub wfd_min_annual_trades: usize,
    pub wfd_min_wfe: f64,
    pub wfd_min_oos_win_rate: f64,
    pub wfd_max_oos_drawdown: f64,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_config_mirror() -> ScanConfigMirror {
        ScanConfigMirror {
            assets: vec!["USDJPY".to_string()],
            timeframes: vec!["1m".to_string()],
            strategies: vec!["time_of_day_drift".to_string()],
            fee_bps: 0.3,
            trials: 200,
            batch_size: 32,
            mode: "long_only".to_string(),
            mode_i8: 1,
            mc_sims: 100,
            random_n: 200,
            param_spaces_path: "config/param_spaces.json".to_string(),
            param_spaces_path_absolute: "/abs/config/param_spaces.json".to_string(),
            days: 500,
            aux: None,
            tick_csv_glob: Some("../data/bq_ticks/usdjpy_*.csv".to_string()),
            max_pareto_candidates: 3,
            wfd_is_months: 12,
            wfd_oos_months: 3,
            wfd_num_walks: 4,
            wfd_min_oos_pf: 1.5,
            wfd_min_annual_trades: 30,
            wfd_min_wfe: 0.5,
            wfd_min_oos_win_rate: 0.45,
            wfd_max_oos_drawdown: 0.25,
        }
    }

    fn sample_metadata() -> ScanMetadata {
        let mut cutoff = BTreeMap::new();
        cutoff.insert(
            "USDJPY".to_string(),
            "2025-12-31T23:59:00+00:00".to_string(),
        );
        let mut counts = BTreeMap::new();
        counts.insert("USDJPY".to_string(), 525_600usize);

        ScanMetadata {
            git_rev: "abcdef0123456789".to_string(),
            git_dirty: false,
            binary_built_at: "2026-04-09T12:00:00+00:00".to_string(),
            command_line: vec![
                "side".to_string(),
                "scan".to_string(),
                "--asset".to_string(),
                "USDJPY".to_string(),
            ],
            cutoff_timestamps: cutoff,
            bars_counts: counts,
            event_filter: None,
            config_mirror: sample_config_mirror(),
        }
    }

    #[test]
    fn scan_metadata_serializes_roundtrip() {
        let meta = sample_metadata();
        let json = serde_json::to_string(&meta).expect("serialize");
        let back: ScanMetadata = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(back.git_rev, meta.git_rev);
        assert_eq!(back.command_line, meta.command_line);
        assert_eq!(back.bars_counts.get("USDJPY"), Some(&525_600usize));
        assert_eq!(back.config_mirror.wfd_min_oos_pf, 1.5);
    }

    #[test]
    fn scan_output_wrapper_shape() {
        let output = ScanOutput {
            metadata: sample_metadata(),
            results: Vec::<ScanCellResult>::new(),
        };
        let json = serde_json::to_value(&output).expect("serialize");
        assert!(
            json.get("metadata").is_some(),
            "top-level `metadata` missing"
        );
        assert!(json.get("results").is_some(), "top-level `results` missing");
        assert!(
            json.get("results").unwrap().is_array(),
            "`results` is not an array"
        );
    }

    #[test]
    fn event_filter_none_serializes_as_null() {
        let meta = sample_metadata();
        let json: serde_json::Value = serde_json::to_value(&meta).unwrap();
        assert!(json.get("event_filter").unwrap().is_null());
    }

    #[test]
    fn event_filter_per_asset_map_roundtrip() {
        let mut meta = sample_metadata();
        let mut filter_map = BTreeMap::new();
        filter_map.insert(
            "USDJPY".to_string(),
            EventFilterStats {
                event_windows_count: 32,
                bars_before: 525_600,
                bars_after: 521_760,
                bars_dropped: 3_840,
            },
        );
        filter_map.insert(
            "EURUSD".to_string(),
            EventFilterStats {
                event_windows_count: 32,
                bars_before: 525_600,
                bars_after: 521_000,
                bars_dropped: 4_600,
            },
        );
        meta.event_filter = Some(filter_map);

        let json = serde_json::to_string(&meta).unwrap();
        let back: ScanMetadata = serde_json::from_str(&json).unwrap();
        let back_filter = back.event_filter.expect("event_filter populated");
        assert_eq!(back_filter.len(), 2);
        assert_eq!(back_filter.get("USDJPY").unwrap().bars_dropped, 3_840);
        assert_eq!(back_filter.get("EURUSD").unwrap().bars_dropped, 4_600);
    }

    #[test]
    fn command_line_vec_preserves_args_with_spaces() {
        let mut meta = sample_metadata();
        meta.command_line = vec![
            "side".to_string(),
            "scan".to_string(),
            "--tick-csv-glob".to_string(),
            "path with space/usdjpy_*.csv".to_string(),
        ];
        let json = serde_json::to_string(&meta).unwrap();
        let back: ScanMetadata = serde_json::from_str(&json).unwrap();
        assert_eq!(back.command_line[3], "path with space/usdjpy_*.csv");
    }

    #[test]
    fn config_mirror_preserves_all_fields() {
        let cfg = sample_config_mirror();
        let json = serde_json::to_string(&cfg).unwrap();
        let back: ScanConfigMirror = serde_json::from_str(&json).unwrap();
        assert_eq!(back.max_pareto_candidates, 3);
        assert_eq!(back.wfd_is_months, 12);
        assert_eq!(back.wfd_oos_months, 3);
        assert_eq!(back.wfd_num_walks, 4);
        assert_eq!(back.wfd_min_annual_trades, 30);
        assert_eq!(back.wfd_max_oos_drawdown, 0.25);
        assert_eq!(back.mode_i8, 1);
    }
}
