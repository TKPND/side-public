/// Currency pair selector for the scan subcommand.
///
/// Promoted from `main.rs` (Phase 39) so that `strategies.rs` and
/// `scanner/macro_event.rs` can reference it without depending on the binary.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum Pair {
    /// USD/JPY (Bank of Japan primary pair).
    Usdjpy,
    /// EUR/USD (FOMC/ECB/NFP compatible pair).
    Eurusd,
    /// AUD/USD (FOMC/NFP compatible pair; ECB excluded per D-08).
    Audusd,
    /// EUR/JPY (FOMC/ECB/NFP/Combined compatible pair).
    Eurjpy,
    /// BTC/USD (crypto, 24/7).
    Btcusd,
    /// ETH/USD (crypto, 24/7).
    Ethusd,
}

impl Pair {
    pub fn as_str(&self) -> &'static str {
        match self {
            Pair::Usdjpy => "USDJPY",
            Pair::Eurusd => "EURUSD",
            Pair::Audusd => "AUDUSD",
            Pair::Eurjpy => "EURJPY",
            Pair::Btcusd => "BTCUSD",
            Pair::Ethusd => "ETHUSD",
        }
    }

    /// Returns true if this pair trades 24/7 (crypto).
    pub fn is_24_7(&self) -> bool {
        matches!(self, Pair::Btcusd | Pair::Ethusd)
    }
}

impl std::str::FromStr for Pair {
    type Err = anyhow::Error;
    fn from_str(s: &str) -> anyhow::Result<Self> {
        match s {
            "USDJPY" => Ok(Pair::Usdjpy),
            "EURUSD" => Ok(Pair::Eurusd),
            "AUDUSD" => Ok(Pair::Audusd),
            "EURJPY" => Ok(Pair::Eurjpy),
            "BTCUSD" => Ok(Pair::Btcusd),
            "ETHUSD" => Ok(Pair::Ethusd),
            other => Err(anyhow::anyhow!("unknown pair: {}", other)),
        }
    }
}
