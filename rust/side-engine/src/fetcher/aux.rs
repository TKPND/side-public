use tracing::info;

/// Parse aux_id format "prefix:arg1[:arg2]".
pub fn parse_aux_id(aux_id: &str) -> anyhow::Result<(&str, Vec<&str>)> {
    let parts: Vec<&str> = aux_id.splitn(3, ':').collect();
    if parts.is_empty() {
        anyhow::bail!("empty aux_id");
    }
    Ok((parts[0], parts[1..].to_vec()))
}

/// Forward-fill alignment: for each target timestamp, use the most recent source value.
pub fn align_forward_fill(source: &[(i64, f64)], target_ms: &[i64]) -> Vec<f64> {
    let mut result = Vec::with_capacity(target_ms.len());
    let mut src_idx = 0;
    let mut last_val = if source.is_empty() { 0.0 } else { source[0].1 };
    for &t in target_ms {
        while src_idx < source.len() && source[src_idx].0 <= t {
            last_val = source[src_idx].1;
            src_idx += 1;
        }
        result.push(last_val);
    }
    result
}

/// Resolve and fetch auxiliary data, returning aligned close prices.
pub async fn fetch_aligned_aux(
    aux_id: &str,
    target_ms: &[i64],
    days: u32,
) -> anyhow::Result<Vec<f64>> {
    let (prefix, args) = parse_aux_id(aux_id)?;
    let source_data: Vec<(i64, f64)> = match prefix {
        "yf" => {
            let ticker = args
                .first()
                .ok_or_else(|| anyhow::anyhow!("missing ticker"))?;
            super::yahoo::fetch_aux_close(ticker, days).await?
        }
        "fred" => {
            let series_type = args
                .first()
                .ok_or_else(|| anyhow::anyhow!("missing series"))?;
            let pair = args.get(1).copied().unwrap_or("");
            match *series_type {
                "rate_diff" => {
                    let points = super::fred::fetch_rate_diff(pair, days).await?;
                    points
                        .into_iter()
                        .map(|(d, v)| {
                            (
                                d.and_hms_opt(0, 0, 0).unwrap().and_utc().timestamp_millis(),
                                v,
                            )
                        })
                        .collect()
                }
                "t10y2y" => {
                    let points = super::fred::fetch_series("T10Y2Y", days).await?;
                    points
                        .into_iter()
                        .map(|(d, v)| {
                            (
                                d.and_hms_opt(0, 0, 0).unwrap().and_utc().timestamp_millis(),
                                v,
                            )
                        })
                        .collect()
                }
                other => anyhow::bail!("unknown FRED series type: {other}"),
            }
        }
        other => anyhow::bail!("unknown aux prefix: {other}"),
    };
    let aligned = align_forward_fill(&source_data, target_ms);
    info!(aux_id, points = aligned.len(), "Aux data aligned");
    Ok(aligned)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_aux_id() {
        let (prefix, rest) = parse_aux_id("yf:^VIX").unwrap();
        assert_eq!(prefix, "yf");
        assert_eq!(rest, vec!["^VIX"]);
        let (prefix, rest) = parse_aux_id("fred:rate_diff:USDJPY").unwrap();
        assert_eq!(prefix, "fred");
        assert_eq!(rest, vec!["rate_diff", "USDJPY"]);
    }

    #[test]
    fn test_align_aux_data() {
        let target_ms: Vec<i64> = (0..5).map(|i| 1_000 + i * 3_600_000).collect();
        let source = vec![(1_000i64, 18.5), (3_600_000 * 24 + 1_000, 19.0)];
        let aligned = align_forward_fill(&source, &target_ms);
        assert_eq!(aligned.len(), 5);
        assert!((aligned[0] - 18.5).abs() < 1e-6);
    }
}
