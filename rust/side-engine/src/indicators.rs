use ordered_float::OrderedFloat;
use std::collections::BTreeMap;

/// Exponential Moving Average (adjust=False, pandas互換)
pub fn ema(data: &[f64], span: usize) -> Vec<f64> {
    let alpha = 2.0 / (span as f64 + 1.0);
    let mut result = vec![f64::NAN; data.len()];
    if data.is_empty() {
        return result;
    }
    result[0] = data[0];
    for i in 1..data.len() {
        result[i] = alpha * data[i] + (1.0 - alpha) * result[i - 1];
    }
    result
}

/// Simple Moving Average
pub fn sma(data: &[f64], period: usize) -> Vec<f64> {
    let mut result = vec![f64::NAN; data.len()];
    if period == 0 || data.len() < period {
        return result;
    }
    let mut sum: f64 = data[..period].iter().sum();
    result[period - 1] = sum / period as f64;
    for i in period..data.len() {
        sum += data[i] - data[i - period];
        result[i] = sum / period as f64;
    }
    result
}

/// True Range
pub fn true_range(high: &[f64], low: &[f64], close: &[f64]) -> Vec<f64> {
    let n = high.len();
    let mut tr = vec![f64::NAN; n];
    if n == 0 {
        return tr;
    }
    tr[0] = high[0] - low[0];
    for i in 1..n {
        let hl = high[i] - low[i];
        let hc = (high[i] - close[i - 1]).abs();
        let lc = (low[i] - close[i - 1]).abs();
        tr[i] = hl.max(hc).max(lc);
    }
    tr
}

/// Average True Range (SMA of TR)
pub fn atr(high: &[f64], low: &[f64], close: &[f64], period: usize) -> Vec<f64> {
    let tr = true_range(high, low, close);
    sma(&tr, period)
}

/// Rolling Standard Deviation (sample, ddof=1, pandas互換)
/// Uses Welford's online algorithm with sliding window for O(n) complexity
/// and excellent numerical stability even with large values (e.g., BTC prices).
pub fn rolling_std(data: &[f64], period: usize) -> Vec<f64> {
    let n = data.len();
    let mut result = vec![f64::NAN; n];
    if period < 2 || n < period {
        return result;
    }

    let p = period as f64;

    // Compute first window using Welford's algorithm
    let mut mean: f64 = 0.0;
    let mut m2: f64 = 0.0;
    for (j, &x) in data.iter().enumerate().take(period) {
        let delta = x - mean;
        mean += delta / (j + 1) as f64;
        let delta2 = x - mean;
        m2 += delta * delta2;
    }
    result[period - 1] = (m2 / (p - 1.0)).max(0.0).sqrt();

    // Slide window: update mean and m2 when removing old_val and adding new_val
    // Based on the formula for updating Welford's when the window slides:
    //   new_mean = old_mean + (new_val - old_val) / period
    //   m2 += (new_val - old_val) * (new_val - new_mean + old_val - old_mean)
    for i in period..n {
        let new_val = data[i];
        let old_val = data[i - period];
        let old_mean = mean;
        mean = old_mean + (new_val - old_val) / p;
        m2 += (new_val - old_val) * (new_val - mean + old_val - old_mean);
        result[i] = (m2 / (p - 1.0)).max(0.0).sqrt();
    }

    result
}

/// Rolling Max
pub fn rolling_max(data: &[f64], period: usize) -> Vec<f64> {
    let mut result = vec![f64::NAN; data.len()];
    if period == 0 || data.len() < period {
        return result;
    }
    for i in (period - 1)..data.len() {
        result[i] = data[i + 1 - period..=i]
            .iter()
            .cloned()
            .fold(f64::NEG_INFINITY, f64::max);
    }
    result
}

/// Rolling Min
pub fn rolling_min(data: &[f64], period: usize) -> Vec<f64> {
    let mut result = vec![f64::NAN; data.len()];
    if period == 0 || data.len() < period {
        return result;
    }
    for i in (period - 1)..data.len() {
        result[i] = data[i + 1 - period..=i]
            .iter()
            .cloned()
            .fold(f64::INFINITY, f64::min);
    }
    result
}

/// Rolling Median (NaN-aware, pandas互換: min_periods=period)
/// BTreeMap sliding window: O(n·log(period)) instead of O(n·period·log(period))
pub fn rolling_median(data: &[f64], period: usize) -> Vec<f64> {
    let mut result = vec![f64::NAN; data.len()];
    if period == 0 || data.len() < period {
        return result;
    }

    // Dual BTreeMap approach: lower half and upper half
    // lower: max-heap semantics (largest key = max of lower half)
    // upper: min-heap semantics (smallest key = min of upper half)
    let mut lower: BTreeMap<OrderedFloat<f64>, usize> = BTreeMap::new();
    let mut upper: BTreeMap<OrderedFloat<f64>, usize> = BTreeMap::new();
    let mut lower_size: usize = 0;
    let mut upper_size: usize = 0;
    let mut valid_count: usize = 0;

    fn tree_insert(tree: &mut BTreeMap<OrderedFloat<f64>, usize>, val: OrderedFloat<f64>) {
        *tree.entry(val).or_insert(0) += 1;
    }

    fn tree_remove(tree: &mut BTreeMap<OrderedFloat<f64>, usize>, val: OrderedFloat<f64>) {
        if let Some(count) = tree.get_mut(&val) {
            *count -= 1;
            if *count == 0 {
                tree.remove(&val);
            }
        } else {
            debug_assert!(false, "tree_remove: value {:?} not found", val);
        }
    }

    fn tree_max(tree: &BTreeMap<OrderedFloat<f64>, usize>) -> OrderedFloat<f64> {
        *tree.last_key_value().unwrap().0
    }

    fn tree_min(tree: &BTreeMap<OrderedFloat<f64>, usize>) -> OrderedFloat<f64> {
        *tree.first_key_value().unwrap().0
    }

    // Rebalance so that lower_size == upper_size or lower_size == upper_size + 1
    fn rebalance(
        lower: &mut BTreeMap<OrderedFloat<f64>, usize>,
        upper: &mut BTreeMap<OrderedFloat<f64>, usize>,
        lower_size: &mut usize,
        upper_size: &mut usize,
    ) {
        while *lower_size > *upper_size + 1 {
            let val = tree_max(lower);
            tree_remove(lower, val);
            tree_insert(upper, val);
            *lower_size -= 1;
            *upper_size += 1;
        }
        while *upper_size > *lower_size {
            let val = tree_min(upper);
            tree_remove(upper, val);
            tree_insert(lower, val);
            *upper_size -= 1;
            *lower_size += 1;
        }
    }

    fn add_val(
        lower: &mut BTreeMap<OrderedFloat<f64>, usize>,
        upper: &mut BTreeMap<OrderedFloat<f64>, usize>,
        lower_size: &mut usize,
        upper_size: &mut usize,
        val: OrderedFloat<f64>,
    ) {
        if *lower_size == 0 || val <= tree_max(lower) {
            tree_insert(lower, val);
            *lower_size += 1;
        } else {
            tree_insert(upper, val);
            *upper_size += 1;
        }
        rebalance(lower, upper, lower_size, upper_size);
    }

    fn remove_val(
        lower: &mut BTreeMap<OrderedFloat<f64>, usize>,
        upper: &mut BTreeMap<OrderedFloat<f64>, usize>,
        lower_size: &mut usize,
        upper_size: &mut usize,
        val: OrderedFloat<f64>,
    ) {
        if val <= tree_max(lower) {
            tree_remove(lower, val);
            *lower_size -= 1;
        } else {
            tree_remove(upper, val);
            *upper_size -= 1;
        }
        rebalance(lower, upper, lower_size, upper_size);
    }

    for i in 0..data.len() {
        // Add new element
        if !data[i].is_nan() {
            let val = OrderedFloat(data[i]);
            add_val(
                &mut lower,
                &mut upper,
                &mut lower_size,
                &mut upper_size,
                val,
            );
            valid_count += 1;
        }

        // Remove element leaving the window
        if i >= period {
            let old = data[i - period];
            if !old.is_nan() {
                let val = OrderedFloat(old);
                remove_val(
                    &mut lower,
                    &mut upper,
                    &mut lower_size,
                    &mut upper_size,
                    val,
                );
                valid_count -= 1;
            }
        }

        // Emit median only when we have a full window and all values are valid
        if i >= period - 1 && valid_count == period {
            let total = lower_size + upper_size;
            if total % 2 == 1 {
                result[i] = tree_max(&lower).into_inner();
            } else {
                let lo = tree_max(&lower).into_inner();
                let hi = tree_min(&upper).into_inner();
                result[i] = (lo + hi) / 2.0;
            }
        }
    }
    result
}

/// Rolling Quantile (linear interpolation, NaN-aware, pandas互換: min_periods=period)
pub fn rolling_quantile(data: &[f64], period: usize, quantile: f64) -> Vec<f64> {
    let mut result = vec![f64::NAN; data.len()];
    if period == 0 || data.len() < period {
        return result;
    }
    for i in (period - 1)..data.len() {
        let mut window: Vec<f64> = data[i + 1 - period..=i]
            .iter()
            .copied()
            .filter(|v| !v.is_nan())
            .collect();
        // pandas default: min_periods=period → require all values non-NaN
        if window.len() < period {
            continue;
        }
        window.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let n = window.len();
        let pos = quantile * (n as f64 - 1.0);
        let lo = pos.floor() as usize;
        let hi = pos.ceil() as usize;
        result[i] = if lo == hi || hi >= n {
            window[lo.min(n - 1)]
        } else {
            window[lo] * (hi as f64 - pos) + window[hi] * (pos - lo as f64)
        };
    }
    result
}

/// RSI (Wilder's smoothing, pandas ewm(alpha=1/N, adjust=False) 互換)
///
/// Pandas互換: diff()[0] = NaN なので、ewm は index=1 の gain/loss を
/// 初期値として開始する。Rust側もこれに合わせて最初の delta を seed とする。
pub fn rsi(close: &[f64], period: usize) -> Vec<f64> {
    let n = close.len();
    let mut result = vec![f64::NAN; n];
    if n < 2 {
        return result;
    }

    let alpha = 1.0 / period as f64;

    // Seed with first delta (pandas ewm adjust=False: y_0 = x_0)
    let first_delta = close[1] - close[0];
    let mut avg_gain = first_delta.max(0.0);
    let mut avg_loss = (-first_delta).max(0.0);

    // i=1 already consumed as seed, start ewm update from i=2
    for i in 2..n {
        let delta = close[i] - close[i - 1];
        let gain = delta.max(0.0);
        let loss = (-delta).max(0.0);

        avg_gain = alpha * gain + (1.0 - alpha) * avg_gain;
        avg_loss = alpha * loss + (1.0 - alpha) * avg_loss;
    }

    // Need to recompute incrementally to emit per-bar RSI
    avg_gain = first_delta.max(0.0);
    avg_loss = (-first_delta).max(0.0);

    for i in 2..n {
        let delta = close[i] - close[i - 1];
        let gain = delta.max(0.0);
        let loss = (-delta).max(0.0);

        avg_gain = alpha * gain + (1.0 - alpha) * avg_gain;
        avg_loss = alpha * loss + (1.0 - alpha) * avg_loss;

        if i >= period {
            let rs = if avg_loss > 0.0 {
                avg_gain / avg_loss
            } else {
                f64::INFINITY
            };
            result[i] = 100.0 - 100.0 / (1.0 + rs);
        }
    }
    result
}

/// Rate of Change (decimal: pct_change)
pub fn roc(close: &[f64], period: usize) -> Vec<f64> {
    let mut result = vec![f64::NAN; close.len()];
    for i in period..close.len() {
        if close[i - period] != 0.0 {
            result[i] = close[i] / close[i - period] - 1.0;
        }
    }
    result
}

/// Parkinson volatility estimator
pub fn parkinson_vol(high: &[f64], low: &[f64], period: usize) -> Vec<f64> {
    let n = high.len();
    let mut hl_sq: Vec<f64> = vec![f64::NAN; n];
    let factor = 1.0 / (4.0 * 2.0_f64.ln());
    for i in 0..n {
        if high[i] > 0.0 && low[i] > 0.0 {
            let ln_hl = (high[i] / low[i]).ln();
            hl_sq[i] = ln_hl * ln_hl * factor;
        }
    }
    let means = sma(&hl_sq, period);
    means
        .iter()
        .map(|&v| if v.is_nan() { f64::NAN } else { v.sqrt() })
        .collect()
}

/// Shift array by n positions (positive = forward/right, NaN fill)
pub fn shift(data: &[f64], n: i32) -> Vec<f64> {
    let len = data.len();
    let mut result = vec![f64::NAN; len];
    if n >= 0 {
        let offset = (n as usize).min(len);
        result[offset..].copy_from_slice(&data[..len - offset]);
    } else {
        let offset = ((-n) as usize).min(len);
        let copy_len = len - offset;
        result[..copy_len].copy_from_slice(&data[offset..]);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ema_basic() {
        let data = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        let result = ema(&data, 3);
        assert!(!result[0].is_nan());
        assert_eq!(result[0], 1.0);
        // alpha = 2/(3+1) = 0.5
        // r[1] = 0.5*2 + 0.5*1 = 1.5
        assert!((result[1] - 1.5).abs() < 1e-10);
    }

    #[test]
    fn test_sma_basic() {
        let data = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        let result = sma(&data, 3);
        assert!(result[0].is_nan());
        assert!(result[1].is_nan());
        assert!((result[2] - 2.0).abs() < 1e-10);
        assert!((result[3] - 3.0).abs() < 1e-10);
        assert!((result[4] - 4.0).abs() < 1e-10);
    }

    #[test]
    fn test_shift_forward() {
        let data = vec![1.0, 2.0, 3.0];
        let result = shift(&data, 1);
        assert!(result[0].is_nan());
        assert_eq!(result[1], 1.0);
        assert_eq!(result[2], 2.0);
    }
}
