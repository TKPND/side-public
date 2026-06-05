use clap::{Parser, Subcommand};

mod cmd;

#[derive(Parser)]
#[command(
    name = "side",
    about = "Trading strategy engine CLI",
    version = "0.1.0",
    author = "Side Team"
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
// ScanArgs grew with plan 01-06 flags (edges, fee sweep, TOD curve).
// Clap dispatch happens once per CLI invocation so the size is irrelevant.
#[allow(clippy::large_enum_variant)]
enum Commands {
    /// Fetch market data from various sources
    Fetch(cmd::fetch::FetchArgs),
    /// Scan for trading opportunities across strategies
    Scan(cmd::scan::ScanArgs),
    /// Run paper (simulated) trading
    Paper(cmd::paper::PaperArgs),
    /// Backtest strategies on historical data
    Backtest(cmd::backtest::BacktestArgs),
    /// Manage and analyze trading portfolio
    Portfolio(cmd::portfolio::PortfolioArgs),
    /// Convert scan output JSON into markdown + json summary
    Report(cmd::report::ReportArgs),
    /// Generate cross-pair comparison summary from multiple report JSON files
    CrossReport(cmd::cross_report::CrossReportArgs),
    /// Generate 4-pair cross-pair comparison summary
    CrossReport4(cmd::cross_report_4::CrossReport4Args),
    /// Methodological audit (config drift / DST spot-check / intersection re-aggregation).
    SignForensics(cmd::sign_forensics::SignForensicsArgs),
    /// Fresh WFD rerun for macro_event: pair × event × configured grid slots.
    WfdRerun(cmd::wfd_rerun::WfdRerunArgs),
    /// Phase 76 POWER-01 kill-switch: ρ̄ extrapolation + n_eff gate (Wave-2.5).
    Phase76PowerCheck(cmd::phase76_power_check::Phase76PowerCheckArgs),
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing with default log level for side crate
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env().add_directive("side=info".parse()?),
        )
        .init();

    let cli = Cli::parse();

    match cli.command {
        Commands::Fetch(args) => cmd::fetch::run(args).await,
        Commands::Scan(args) => cmd::scan::run(args).await,
        Commands::Paper(args) => cmd::paper::run(args).await,
        Commands::Backtest(args) => cmd::backtest::run(args).await,
        Commands::Portfolio(args) => cmd::portfolio::run(args).await,
        Commands::Report(args) => cmd::report::run(args).await,
        Commands::CrossReport(args) => cmd::cross_report::run(args).await,
        Commands::CrossReport4(args) => cmd::cross_report_4::run(args).await,
        Commands::SignForensics(args) => cmd::sign_forensics::run(args).await,
        Commands::WfdRerun(args) => cmd::wfd_rerun::run(args).await,
        Commands::Phase76PowerCheck(args) => cmd::phase76_power_check::run(args).await,
    }
}
