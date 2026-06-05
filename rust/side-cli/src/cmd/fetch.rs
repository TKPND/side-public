use clap::Parser;
use side_engine::fetcher::{cache, dukascopy, types::Timeframe};
use std::path::PathBuf;

#[derive(Parser)]
#[command(about = "Fetch market data from various sources")]
pub struct FetchArgs {
    /// Asset to fetch (e.g., EURUSD, BTCUSD)
    #[arg(short, long)]
    pub asset: String,

    /// Data source (e.g., dukascopy, binance)
    #[arg(short, long, default_value = "dukascopy")]
    pub source: String,

    /// Number of days to fetch
    #[arg(short, long, default_value = "30")]
    pub days: u32,

    /// Timeframe (e.g., 1h, 4h, 1d)
    #[arg(short, long, default_value = "1h")]
    pub timeframe: String,

    /// Output file path
    #[arg(short, long)]
    pub output: Option<String>,

    /// Skip cache, fetch fresh data
    #[arg(long)]
    pub no_cache: bool,
}

pub async fn run(args: FetchArgs) -> anyhow::Result<()> {
    tracing::info!(
        "fetch: asset={}, source={}, days={}, timeframe={}",
        args.asset,
        args.source,
        args.days,
        args.timeframe
    );

    // 1. Parse timeframe
    let tf = Timeframe::parse(&args.timeframe)?;

    // 2. Check cache (unless --no-cache)
    let cache_dir = PathBuf::from("data/cache");
    let cache_key = format!("{}_{}_{}", args.asset, args.timeframe, args.days);
    let ttl_hours = 24u64;

    if !args.no_cache {
        if let Some(bars) = cache::load_csv(&cache_dir, &cache_key, ttl_hours)? {
            println!("Loaded {} bars from cache (key: {})", bars.len(), cache_key);
            return Ok(());
        }
    }

    // 3. Fetch via dukascopy
    tracing::info!("fetching {} bars from dukascopy...", args.asset);
    let bars = dukascopy::fetch_ohlcv(&args.asset, args.days, tf).await?;

    // 4. Save to cache
    cache::save_csv(&cache_dir, &cache_key, &bars)?;

    // 5. Print result count
    println!(
        "Fetched {} bars for {} ({}, {} days)",
        bars.len(),
        args.asset,
        args.timeframe,
        args.days
    );

    Ok(())
}
