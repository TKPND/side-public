//! edges.json schema + parsing (DISC-02).
//!
//! Parses the output of the BigQuery discovery layer into a typed [`Edge`]
//! record. Each edge represents a statistically significant time-of-day
//! directional slot found by `bq_usdjpy_directional_windows.sql`.
//!
//! Consumed by:
//! - Wave 2 `tod_edge` strategy (Plan 01-04)
//! - Wave 3 batch scanner (Plan 01-05, 01-06)

use serde::{Deserialize, Serialize};
use std::path::Path;

/// One time-of-day directional edge discovered by the BigQuery layer.
///
/// Schema matches the D-06 contract in 01-CONTEXT.md:
/// ```json
/// {
///   "entry_minute": 0,
///   "direction": "long",
///   "hold_h_candidates": [1, 3],
///   "t_stat": 4.52,
///   "bh_q": 0.018,
///   "dsr_p": null,
///   "source_query": "bq_usdjpy_directional_windows.sql",
///   "asset": "USDJPY",
///   "timeframe": "1h"
/// }
/// ```
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Edge {
    /// Minute-of-day in UTC, 0..=1439.
    pub entry_minute: u16,
    /// Either `"long"` or `"short"`.
    pub direction: String,
    /// Candidate hold-horizon indices into `TOD_EDGE_HORIZONS_MIN` (1..=9).
    pub hold_h_candidates: Vec<u8>,
    /// Bonferroni-corrected t-statistic (absolute value should exceed 4.40).
    pub t_stat: f64,
    /// Benjamini-Hochberg adjusted q-value.
    pub bh_q: f64,
    /// Deflated Sharpe ratio p-value (optional; None when not computed).
    #[serde(default)]
    pub dsr_p: Option<f64>,
    /// Which BigQuery script produced this edge.
    pub source_query: String,
    /// Instrument, e.g. `"USDJPY"`.
    pub asset: String,
    /// Bar timeframe, e.g. `"1h"`, `"15m"`, `"1m"`.
    pub timeframe: String,
}

impl Edge {
    /// Validate field ranges. Called automatically by [`parse_str`].
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.direction != "long" && self.direction != "short" {
            anyhow::bail!(
                "invalid direction '{}': must be 'long' or 'short'",
                self.direction
            );
        }
        if self.entry_minute > 1439 {
            anyhow::bail!(
                "entry_minute {} out of range (must be 0..=1439)",
                self.entry_minute
            );
        }
        for &h in &self.hold_h_candidates {
            if !(1..=9).contains(&h) {
                anyhow::bail!("hold_h_candidate {} out of range (must be 1..=9)", h);
            }
        }
        Ok(())
    }
}

/// Parse an edges.json payload from a UTF-8 string and validate every entry.
pub fn parse_str(s: &str) -> anyhow::Result<Vec<Edge>> {
    let edges: Vec<Edge> = serde_json::from_str(s)?;
    for e in &edges {
        e.validate()?;
    }
    Ok(edges)
}

/// Read an edges.json file from disk and parse it.
pub fn parse_file(path: &Path) -> anyhow::Result<Vec<Edge>> {
    let s = std::fs::read_to_string(path)
        .map_err(|e| anyhow::anyhow!("failed to read {}: {}", path.display(), e))?;
    parse_str(&s)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_str_accepts_minimal_edge() {
        let json = r#"[{
            "entry_minute": 0,
            "direction": "long",
            "hold_h_candidates": [1],
            "t_stat": 4.5,
            "bh_q": 0.02,
            "source_query": "test.sql",
            "asset": "USDJPY",
            "timeframe": "1h"
        }]"#;
        let edges = parse_str(json).unwrap();
        assert_eq!(edges.len(), 1);
        assert!(edges[0].dsr_p.is_none());
    }

    #[test]
    fn validate_rejects_bad_direction() {
        let e = Edge {
            entry_minute: 0,
            direction: "sideways".into(),
            hold_h_candidates: vec![1],
            t_stat: 1.0,
            bh_q: 0.5,
            dsr_p: None,
            source_query: "t".into(),
            asset: "USDJPY".into(),
            timeframe: "1h".into(),
        };
        assert!(e.validate().is_err());
    }

    #[test]
    fn validate_rejects_out_of_range_hold_h() {
        let e = Edge {
            entry_minute: 0,
            direction: "long".into(),
            hold_h_candidates: vec![10],
            t_stat: 1.0,
            bh_q: 0.5,
            dsr_p: None,
            source_query: "t".into(),
            asset: "USDJPY".into(),
            timeframe: "1h".into(),
        };
        assert!(e.validate().is_err());
    }

    #[test]
    fn validate_rejects_out_of_range_entry_minute() {
        let e = Edge {
            entry_minute: 1440,
            direction: "long".into(),
            hold_h_candidates: vec![1],
            t_stat: 1.0,
            bh_q: 0.5,
            dsr_p: None,
            source_query: "t".into(),
            asset: "USDJPY".into(),
            timeframe: "1h".into(),
        };
        assert!(e.validate().is_err());
    }
}
