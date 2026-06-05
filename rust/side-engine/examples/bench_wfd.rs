//! Quick bench: WFD single on synthetic 5000-bar data
use serde_json::Value;
use side_engine::wfd::{run_wfd_single, WfdConfig};
use std::collections::HashMap;
use std::hint::black_box;
use std::time::Instant;

fn main() {
    let n = 20000usize; // ~2.3 years of hourly bars
                        // Generate synthetic price data (random walk with trend)
    let mut rng_seed: u64 = 42;
    let mut close = vec![100.0f64; n];
    for i in 1..n {
        rng_seed = rng_seed.wrapping_mul(6364136223846793005).wrapping_add(1);
        let r = ((rng_seed >> 33) as f64 / (1u64 << 31) as f64) - 0.5;
        close[i] = close[i - 1] * (1.0 + r * 0.02);
    }
    let open: Vec<f64> = close.iter().map(|c| c * 0.999).collect();
    let high: Vec<f64> = close.iter().map(|c| c * 1.005).collect();
    let low: Vec<f64> = close.iter().map(|c| c * 0.995).collect();
    let volume: Vec<f64> = vec![1000.0; n];
    let datetimes_ns: Vec<i64> = (0..n as i64)
        .map(|i| 1700000000000000000 + i * 3_600_000_000_000)
        .collect();

    let config_json = r#"{"is_months":6,"oos_months":3,"num_walks":5,"min_oos_pf":1.0,"min_annual_trades":10,"min_wfe":0.0,"min_oos_win_rate":0.0,"max_oos_drawdown":1.0}"#;
    let config: WfdConfig = serde_json::from_str(config_json).unwrap();

    // --- sma_cross ---
    let mut params = HashMap::new();
    params.insert("short_window".to_string(), Value::from(10));
    params.insert("long_window".to_string(), Value::from(30));

    // Warmup
    black_box(run_wfd_single(
        &open,
        &high,
        &low,
        &close,
        &volume,
        &datetimes_ns,
        None,
        "sma_cross",
        &params,
        &config,
        "1h",
        None,
        1,
    ));

    let iterations = 100;
    let start = Instant::now();
    for _ in 0..iterations {
        black_box(run_wfd_single(
            &open,
            &high,
            &low,
            &close,
            &volume,
            &datetimes_ns,
            None,
            "sma_cross",
            &params,
            &config,
            "1h",
            None,
            1,
        ));
    }
    let elapsed = start.elapsed();
    let per_iter = elapsed / iterations;

    // Check result details
    let result = run_wfd_single(
        &open,
        &high,
        &low,
        &close,
        &volume,
        &datetimes_ns,
        None,
        "sma_cross",
        &params,
        &config,
        "1h",
        None,
        1,
    );
    println!("=== WFD Single Benchmark ===");
    println!("Bars: {n}");
    println!("Strategy: sma_cross");
    println!("Actual walks: {}", result.walks.len());
    println!("OOS trades: {}", result.combined_oos_trades);
    println!("Passed: {}", result.passed);
    println!("Iterations: {iterations}");
    println!("Total: {elapsed:.2?}");
    println!("Per iteration: {per_iter:.2?}");

    // --- ema_atr ---
    let mut params2 = HashMap::new();
    params2.insert("short_ema".to_string(), Value::from(12));
    params2.insert("long_ema".to_string(), Value::from(26));
    params2.insert("atr_period".to_string(), Value::from(14));
    params2.insert("atr_multiplier".to_string(), Value::from(1.5));

    black_box(run_wfd_single(
        &open,
        &high,
        &low,
        &close,
        &volume,
        &datetimes_ns,
        None,
        "ema_atr",
        &params2,
        &config,
        "1h",
        None,
        1,
    ));

    let start2 = Instant::now();
    for _ in 0..iterations {
        black_box(run_wfd_single(
            &open,
            &high,
            &low,
            &close,
            &volume,
            &datetimes_ns,
            None,
            "ema_atr",
            &params2,
            &config,
            "1h",
            None,
            1,
        ));
    }
    let elapsed2 = start2.elapsed();
    let per_iter2 = elapsed2 / iterations;

    println!("\n=== WFD Single (ema_atr) ===");
    println!("Per iteration: {per_iter2:.2?}");
}
