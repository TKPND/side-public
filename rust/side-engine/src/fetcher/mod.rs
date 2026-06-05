pub mod aux;
pub mod cache;
pub mod dukascopy;
pub mod dukascopy_csv;
pub mod fred;
pub mod mirror;
pub mod types;
pub mod yahoo;

#[cfg(test)]
mod integration_tests {
    use super::*;

    #[tokio::test]
    #[ignore]
    async fn test_fetch_dukascopy_and_align_aux() {
        // Fetch 3 days of USDJPY
        let bars = dukascopy::fetch_ohlcv("USDJPY", 3, types::Timeframe::H1)
            .await
            .unwrap();
        assert!(!bars.is_empty());

        // Get target timestamps
        let target_ms: Vec<i64> = bars
            .iter()
            .map(|b| b.datetime.and_utc().timestamp_millis())
            .collect();

        // Fetch and align VIX
        let vix = aux::fetch_aligned_aux("yf:^VIX", &target_ms, 30)
            .await
            .unwrap();
        assert_eq!(vix.len(), bars.len());
        assert!(vix[0] > 0.0, "VIX should be positive");

        println!(
            "Bars: {}, VIX values: {}, first VIX: {:.2}",
            bars.len(),
            vix.len(),
            vix[0]
        );
    }
}
