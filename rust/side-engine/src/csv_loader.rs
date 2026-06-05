//! CSV loader (minimal stub, Phase 98 D-10 LOCKED 2026-04-25).
//!
//! Production CSV bridge for `rust/data/mirror/*.csv` (consumed by
//! `side-cli/src/cmd/wfd_rerun.rs::mirror_csv_path`). Test fixture path
//! is fully migrated to `parquet_loader::load_ohlcv_parquet`.
//!
//! Removal scope: this file becomes deletable in Phase 99+ when
//! `rust/data/mirror/*.csv` is migrated to Parquet (D-03).

use std::path::Path;

use crate::scanner::OhlcvData;

/// Load an OHLCV CSV fixture into an owned [`OhlcvData`].
///
/// Expected header (exact): `datetime_ns,open,high,low,close,volume`.
pub fn load_ohlcv_csv(path: &Path) -> anyhow::Result<OhlcvData> {
    let mut rdr = csv::ReaderBuilder::new()
        .has_headers(true)
        .from_path(path)
        .map_err(|e| anyhow::anyhow!("failed to open {}: {}", path.display(), e))?;

    let headers: Vec<String> = rdr.headers()?.iter().map(|s| s.to_string()).collect();
    let expected = ["datetime_ns", "open", "high", "low", "close", "volume"];
    for (i, exp) in expected.iter().enumerate() {
        let actual = headers.get(i).map(String::as_str).unwrap_or("");
        if actual != *exp {
            anyhow::bail!(
                "csv header[{i}] = {actual:?}, expected {exp:?} (full header: {headers:?})"
            );
        }
    }

    let mut datetimes_ns = Vec::new();
    let mut open = Vec::new();
    let mut high = Vec::new();
    let mut low = Vec::new();
    let mut close = Vec::new();
    let mut volume = Vec::new();

    for (row_idx, record) in rdr.records().enumerate() {
        let record = record?;
        let ts: i64 = record[0]
            .parse()
            .map_err(|e| anyhow::anyhow!("row {row_idx}: bad datetime_ns: {e}"))?;
        datetimes_ns.push(ts);
        open.push(record[1].parse::<f64>()?);
        high.push(record[2].parse::<f64>()?);
        low.push(record[3].parse::<f64>()?);
        close.push(record[4].parse::<f64>()?);
        volume.push(record[5].parse::<f64>()?);
    }

    Ok(OhlcvData {
        open,
        high,
        low,
        close,
        volume,
        datetimes_ns,
        aux_close: None,
    })
}
