//! Phase 2 validation constants.

/// Total scan dimension: 1440 minutes × 9 horizons = 12,960.
/// Used as `n_trials` in Deflated Sharpe Ratio (Bailey & LdP 2014).
pub const DEFAULT_DSR_N_TRIALS: usize = 12_960;

/// Number of bootstrap resamples for stationary bootstrap CI.
pub const DEFAULT_BOOTSTRAP_N: usize = 1_000;

/// Random seed for reproducible bootstrap CI.
pub const DEFAULT_BOOTSTRAP_SEED: u64 = 42;
