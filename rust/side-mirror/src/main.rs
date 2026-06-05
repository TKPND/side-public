//! side-mirror: market data mirror daemon for the Side trading engine.

use clap::{Parser, Subcommand};
use std::path::PathBuf;

mod cmd;
mod config;
mod fetch;
mod server;

#[derive(Parser)]
#[command(
    name = "side-mirror",
    about = "Market data mirror daemon — fetches and caches OHLCV bars",
    version = "0.1.0"
)]
struct Cli {
    /// Path to the TOML config file.
    #[arg(short, long, default_value = "mirror.toml")]
    config: PathBuf,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Fetch OHLCV bars for all configured pairs
    Fetch(cmd::fetch::FetchArgs),
    /// Run HTTP server with background fetch
    Serve(cmd::serve::ServeArgs),
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive("side_mirror=info".parse()?),
        )
        .init();

    let cli = Cli::parse();
    let cfg = config::load_config(&cli.config)?;

    match cli.command {
        Commands::Fetch(args) => cmd::fetch::run(args, cfg).await,
        Commands::Serve(args) => cmd::serve::run(args, cfg).await,
    }
}
