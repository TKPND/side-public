//! Fetch subcommand — downloads OHLCV bars for all configured pairs.

/// Arguments for the fetch subcommand.
#[derive(clap::Args)]
pub struct FetchArgs {}

/// Run the fetch subcommand.
pub async fn run(_args: FetchArgs, config: crate::config::MirrorConfig) -> anyhow::Result<()> {
    crate::fetch::run_fetch(&config).await
}
