/// Raw tick from Dukascopy bi5 file.
#[derive(Debug, Clone)]
pub struct Tick {
    pub datetime_ms: i64,
    pub price: f64,
    pub volume: f64,
}

/// OHLCV bar.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Bar {
    pub datetime: chrono::NaiveDateTime,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

/// Timeframe for OHLCV aggregation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Timeframe {
    M1,
    M5,
    M15,
    M30,
    H1,
    H4,
    D1,
}

impl Timeframe {
    pub fn minutes(&self) -> i64 {
        match self {
            Timeframe::M1 => 1,
            Timeframe::M5 => 5,
            Timeframe::M15 => 15,
            Timeframe::M30 => 30,
            Timeframe::H1 => 60,
            Timeframe::H4 => 240,
            Timeframe::D1 => 1440,
        }
    }
    pub fn as_str(&self) -> &'static str {
        match self {
            Timeframe::M1 => "1m",
            Timeframe::M5 => "5m",
            Timeframe::M15 => "15m",
            Timeframe::M30 => "30m",
            Timeframe::H1 => "1h",
            Timeframe::H4 => "4h",
            Timeframe::D1 => "1d",
        }
    }
    pub fn parse(s: &str) -> anyhow::Result<Self> {
        match s {
            "1m" => Ok(Self::M1),
            "5m" => Ok(Self::M5),
            "15m" => Ok(Self::M15),
            "30m" => Ok(Self::M30),
            "1h" => Ok(Self::H1),
            "4h" => Ok(Self::H4),
            "1d" => Ok(Self::D1),
            _ => anyhow::bail!("unknown timeframe: {s}"),
        }
    }
}
