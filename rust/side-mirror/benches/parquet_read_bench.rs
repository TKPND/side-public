//! Wave 0 bench: sync vs async Parquet read on 8.7MB fixture.
//! Used to decide D-07 (sync + spawn_blocking vs async handler).
//!
//! Spec (per 97-00-PLAN.md Task 2):
//! - N_ITER = 10, WARMUP = 2 (median of last 8 samples)
//! - 20% threshold: diff_pct.abs() < 20.0 → sync (spawn_blocking), else async / sync
//! - Output: 4 stdout lines (sync_p50_ms, async_p50_ms, diff_pct, verdict)

use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use std::time::Instant;

const FIXTURE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../tests/fixtures/fetch/reference/usdjpy_ticks_2024-01-08.parquet"
);
const N_ITER: usize = 10;
const WARMUP: usize = 2;

fn bench_sync(path: &str) -> f64 {
    let mut times = Vec::with_capacity(N_ITER);
    for _ in 0..N_ITER {
        let t0 = Instant::now();
        let file = std::fs::File::open(path).expect("fixture open");
        let reader = ParquetRecordBatchReaderBuilder::try_new(file)
            .expect("builder")
            .build()
            .expect("reader");
        let _n: usize = reader.map(|b| b.expect("batch").num_rows()).sum();
        times.push(t0.elapsed().as_secs_f64() * 1000.0);
    }
    median(&times[WARMUP..])
}

async fn bench_async(path: &str) -> f64 {
    use futures::StreamExt;
    use parquet::arrow::async_reader::ParquetRecordBatchStreamBuilder;
    let mut times = Vec::with_capacity(N_ITER);
    for _ in 0..N_ITER {
        let t0 = Instant::now();
        let file = tokio::fs::File::open(path).await.expect("fixture open");
        let builder = ParquetRecordBatchStreamBuilder::new(file)
            .await
            .expect("builder");
        let mut stream = builder.build().expect("stream");
        let mut n = 0usize;
        while let Some(batch) = stream.next().await {
            n += batch.expect("batch").num_rows();
        }
        let _ = n;
        times.push(t0.elapsed().as_secs_f64() * 1000.0);
    }
    median(&times[WARMUP..])
}

fn median(xs: &[f64]) -> f64 {
    let mut v = xs.to_vec();
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[v.len() / 2]
}

fn main() {
    let sync_p50 = bench_sync(FIXTURE);
    let rt = tokio::runtime::Runtime::new().expect("tokio rt");
    let async_p50 = rt.block_on(bench_async(FIXTURE));
    let diff_pct = (async_p50 - sync_p50) / sync_p50 * 100.0;
    let verdict = if diff_pct.abs() < 20.0 {
        "sync (spawn_blocking)"
    } else if async_p50 < sync_p50 {
        "async"
    } else {
        "sync (async slower)"
    };
    println!("sync_p50_ms  = {:.3}", sync_p50);
    println!("async_p50_ms = {:.3}", async_p50);
    println!("diff_pct     = {:+.2}%", diff_pct);
    println!("verdict      = {}", verdict);
}
