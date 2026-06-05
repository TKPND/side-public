use std::collections::HashMap;
use std::path::Path;

use rand::{Rng, RngExt};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::wfd::ExitConfig;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ParamDef {
    Int {
        low: i64,
        high: i64,
    },
    Float {
        low: f64,
        high: f64,
        #[serde(default)]
        step: Option<f64>,
    },
    Categorical {
        choices: Vec<Value>,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Constraint {
    /// target >= source + offset
    GreaterThan {
        target: String,
        source: String,
        #[serde(default)]
        offset: f64,
    },
}

/// Mapping from `active_session` to (entry_hour_start, entry_hour_end).
/// Used by session_momentum strategy.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionMapping {
    pub source: String,
    pub target_start: String,
    pub target_end: String,
    pub map: HashMap<String, (i64, i64)>,
}

/// Seasonal filter: active_months computed from start_month + window_size.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ComputedMonths {
    pub start_month: String,
    pub window_size: String,
    pub target: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyParamSpace {
    pub params: HashMap<String, ParamDef>,
    #[serde(default)]
    pub constraints: Vec<Constraint>,
    #[serde(default)]
    pub session_mapping: Option<SessionMapping>,
    #[serde(default)]
    pub computed_months: Option<ComputedMonths>,
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

pub fn load_param_spaces(
    path: &Path,
) -> Result<HashMap<String, StrategyParamSpace>, anyhow::Error> {
    let content = std::fs::read_to_string(path)?;
    let spaces: HashMap<String, StrategyParamSpace> = serde_json::from_str(&content)?;
    Ok(spaces)
}

// ---------------------------------------------------------------------------
// Sampling
// ---------------------------------------------------------------------------

pub fn sample_params(space: &StrategyParamSpace, rng: &mut impl Rng) -> HashMap<String, Value> {
    let mut params = HashMap::new();

    // Sample each parameter independently first
    for (name, def) in &space.params {
        let val = sample_single(def, rng);
        params.insert(name.clone(), val);
    }

    // Apply constraints (fixup dependent params)
    for constraint in &space.constraints {
        apply_constraint(&mut params, constraint, &space.params, rng);
    }

    // Apply session mapping (session_momentum)
    if let Some(ref sm) = space.session_mapping {
        if let Some(session_val) = params.get(&sm.source) {
            if let Some(key) = session_val.as_str() {
                if let Some(&(start, end)) = sm.map.get(key) {
                    params.insert(sm.target_start.clone(), Value::from(start));
                    params.insert(sm.target_end.clone(), Value::from(end));
                }
            }
        }
    }

    // Apply computed months (seasonal_filter)
    if let Some(ref cm) = space.computed_months {
        let start = params
            .get(&cm.start_month)
            .and_then(|v| v.as_i64())
            .unwrap_or(1);
        let window = params
            .get(&cm.window_size)
            .and_then(|v| v.as_i64())
            .unwrap_or(3);
        let months: Vec<Value> = (0..window)
            .map(|i| Value::from((start + i - 1) % 12 + 1))
            .collect();
        params.insert(cm.target.clone(), Value::Array(months));
    }

    params
}

fn sample_single(def: &ParamDef, rng: &mut impl Rng) -> Value {
    match def {
        ParamDef::Int { low, high } => {
            let v: i64 = rng.random_range(*low..=*high);
            Value::from(v)
        }
        ParamDef::Float { low, high, step } => {
            let v: f64 = rng.random_range(*low..=*high);
            let v = if let Some(s) = step {
                (*low + ((v - low) / s).round() * s).min(*high)
            } else {
                v
            };
            Value::from(v)
        }
        ParamDef::Categorical { choices } => {
            let idx = rng.random_range(0..choices.len());
            choices[idx].clone()
        }
    }
}

fn apply_constraint(
    params: &mut HashMap<String, Value>,
    constraint: &Constraint,
    defs: &HashMap<String, ParamDef>,
    rng: &mut impl Rng,
) {
    match constraint {
        Constraint::GreaterThan {
            target,
            source,
            offset,
        } => {
            let src_val = param_as_f64(params.get(source));
            let min_target = src_val + offset;

            let tgt_val = param_as_f64(params.get(target));
            if tgt_val < min_target {
                // Re-sample target within [min_target, high]
                if let Some(def) = defs.get(target) {
                    let high = param_def_high(def);
                    let clamped_min = min_target.max(param_def_low(def));
                    if clamped_min > high {
                        // Edge case: impossible constraint, clamp to high
                        params.insert(target.clone(), to_value(high, def));
                    } else {
                        let new_val = resample_in_range(def, clamped_min, high, rng);
                        params.insert(target.clone(), new_val);
                    }
                }
            }
        }
    }
}

fn param_as_f64(v: Option<&Value>) -> f64 {
    match v {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        _ => 0.0,
    }
}

fn param_def_high(def: &ParamDef) -> f64 {
    match def {
        ParamDef::Int { high, .. } => *high as f64,
        ParamDef::Float { high, .. } => *high,
        ParamDef::Categorical { .. } => 0.0,
    }
}

fn param_def_low(def: &ParamDef) -> f64 {
    match def {
        ParamDef::Int { low, .. } => *low as f64,
        ParamDef::Float { low, .. } => *low,
        ParamDef::Categorical { .. } => 0.0,
    }
}

fn resample_in_range(def: &ParamDef, min_val: f64, max_val: f64, rng: &mut impl Rng) -> Value {
    match def {
        ParamDef::Int { .. } => {
            let lo = min_val.ceil() as i64;
            let hi = max_val.floor() as i64;
            if lo > hi {
                Value::from(hi)
            } else {
                Value::from(rng.random_range(lo..=hi))
            }
        }
        ParamDef::Float { step, .. } => {
            let v: f64 = rng.random_range(min_val..=max_val);
            let v = if let Some(s) = step {
                (min_val + ((v - min_val) / s).round() * s).min(max_val)
            } else {
                v
            };
            Value::from(v)
        }
        ParamDef::Categorical { choices } => {
            // Shouldn't happen for GreaterThan constraints
            choices.first().cloned().unwrap_or(Value::Null)
        }
    }
}

fn to_value(v: f64, def: &ParamDef) -> Value {
    match def {
        ParamDef::Int { .. } => Value::from(v as i64),
        ParamDef::Float { .. } => Value::from(v),
        ParamDef::Categorical { .. } => Value::Null,
    }
}

// ---------------------------------------------------------------------------
// Exit space sampling
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExitType {
    None,
    FixedPct,
    AtrBased,
}

pub fn sample_exit(rng: &mut impl Rng) -> (Option<ExitConfig>, HashMap<String, Value>) {
    let choices = [ExitType::None, ExitType::FixedPct, ExitType::AtrBased];
    let exit_type = choices[rng.random_range(0..3)];

    let mut meta = HashMap::new();

    match exit_type {
        ExitType::None => {
            meta.insert("exit_type".to_string(), Value::from("none"));
            (None, meta)
        }
        ExitType::FixedPct => {
            let sl_pct: f64 = rng.random_range(0.01..=0.05);
            let tp_pct: f64 = rng.random_range(sl_pct..=0.10);
            meta.insert("exit_type".to_string(), Value::from("fixed_pct"));
            meta.insert("sl_pct".to_string(), Value::from(sl_pct));
            meta.insert("tp_pct".to_string(), Value::from(tp_pct));
            (
                Some(ExitConfig {
                    sl_pct,
                    tp_pct,
                    sl_atr: f64::NAN,
                    tp_atr: f64::NAN,
                    atr_period: 14,
                }),
                meta,
            )
        }
        ExitType::AtrBased => {
            let sl_atr: f64 = rng.random_range(0.5..=3.0);
            let tp_atr: f64 = rng.random_range(sl_atr..=5.0);
            meta.insert("exit_type".to_string(), Value::from("atr_based"));
            meta.insert("sl_atr".to_string(), Value::from(sl_atr));
            meta.insert("tp_atr".to_string(), Value::from(tp_atr));
            (
                Some(ExitConfig {
                    sl_pct: f64::NAN,
                    tp_pct: f64::NAN,
                    sl_atr,
                    tp_atr,
                    atr_period: 14,
                }),
                meta,
            )
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use rand::rngs::StdRng;
    use rand::SeedableRng;

    fn test_json() -> &'static str {
        r#"{
            "ema_atr": {
                "params": {
                    "short_ema": { "type": "int", "low": 5, "high": 50 },
                    "long_ema": { "type": "int", "low": 10, "high": 200 },
                    "atr_period": { "type": "int", "low": 7, "high": 30 },
                    "atr_multiplier": { "type": "float", "low": 0.3, "high": 2.0 }
                },
                "constraints": [
                    { "type": "greater_than", "target": "long_ema", "source": "short_ema", "offset": 5 }
                ]
            },
            "keltner": {
                "params": {
                    "ema_period": { "type": "int", "low": 10, "high": 50 },
                    "atr_period": { "type": "int", "low": 7, "high": 30 },
                    "atr_multiplier": { "type": "float", "low": 1.0, "high": 4.0 }
                }
            },
            "cross_asset_fx": {
                "params": {
                    "fast_lookback": { "type": "int", "low": 5, "high": 20 },
                    "z_entry": { "type": "float", "low": 1.0, "high": 3.0, "step": 0.25 }
                }
            },
            "vol_breakout": {
                "params": {
                    "channel_period": { "type": "int", "low": 10, "high": 60 },
                    "multiplier": { "type": "float", "low": 0.5, "high": 4.0 },
                    "vol_type": { "type": "categorical", "choices": ["atr", "std", "parkinson"] }
                }
            },
            "session_momentum": {
                "params": {
                    "active_session": { "type": "categorical", "choices": ["asian", "london", "overlap", "ny_late"] },
                    "trend_ema": { "type": "int", "low": 10, "high": 50 },
                    "use_trend_filter": { "type": "categorical", "choices": [true, false] },
                    "avoid_nfp_week": { "type": "categorical", "choices": [true, false] }
                },
                "session_mapping": {
                    "source": "active_session",
                    "target_start": "entry_hour_start",
                    "target_end": "entry_hour_end",
                    "map": {
                        "asian": [0, 8],
                        "london": [8, 13],
                        "overlap": [13, 17],
                        "ny_late": [17, 22]
                    }
                }
            },
            "seasonal_filter": {
                "params": {
                    "start_month": { "type": "int", "low": 1, "high": 12 },
                    "window_size": { "type": "int", "low": 3, "high": 9 },
                    "entry_offset": { "type": "int", "low": -5, "high": 5 }
                },
                "computed_months": {
                    "start_month": "start_month",
                    "window_size": "window_size",
                    "target": "active_months"
                }
            }
        }"#
    }

    #[test]
    fn test_parse_param_spaces() {
        let spaces: HashMap<String, StrategyParamSpace> =
            serde_json::from_str(test_json()).unwrap();
        assert_eq!(spaces.len(), 6);
        assert!(spaces.contains_key("ema_atr"));
        assert!(spaces.contains_key("keltner"));
        assert!(spaces.contains_key("vol_breakout"));

        // Check constraint parsed
        let ema = &spaces["ema_atr"];
        assert_eq!(ema.constraints.len(), 1);
    }

    #[test]
    fn test_sample_within_range_100_times() {
        let spaces: HashMap<String, StrategyParamSpace> =
            serde_json::from_str(test_json()).unwrap();
        let mut rng = StdRng::seed_from_u64(42);

        for _ in 0..100 {
            // keltner (no constraints)
            let params = sample_params(&spaces["keltner"], &mut rng);
            let ema = params["ema_period"].as_i64().unwrap();
            let atr = params["atr_period"].as_i64().unwrap();
            let mult = params["atr_multiplier"].as_f64().unwrap();
            assert!((10..=50).contains(&ema));
            assert!((7..=30).contains(&atr));
            assert!((1.0..=4.0).contains(&mult));
        }
    }

    #[test]
    fn test_constraint_enforcement() {
        let spaces: HashMap<String, StrategyParamSpace> =
            serde_json::from_str(test_json()).unwrap();
        let mut rng = StdRng::seed_from_u64(42);

        for _ in 0..200 {
            let params = sample_params(&spaces["ema_atr"], &mut rng);
            let short = params["short_ema"].as_i64().unwrap();
            let long = params["long_ema"].as_i64().unwrap();
            assert!(
                long >= short + 5,
                "long_ema ({long}) should be >= short_ema ({short}) + 5"
            );
            assert!((5..=50).contains(&short));
            assert!(long <= 200);
        }
    }

    #[test]
    fn test_step_quantization() {
        let spaces: HashMap<String, StrategyParamSpace> =
            serde_json::from_str(test_json()).unwrap();
        let mut rng = StdRng::seed_from_u64(99);

        for _ in 0..100 {
            let params = sample_params(&spaces["cross_asset_fx"], &mut rng);
            let z = params["z_entry"].as_f64().unwrap();
            // Should be quantized to 0.25 steps from 1.0
            let remainder = (z - 1.0) % 0.25;
            assert!(
                remainder.abs() < 1e-10 || (0.25 - remainder.abs()).abs() < 1e-10,
                "z_entry ({z}) should be quantized to step 0.25"
            );
            assert!((1.0..=3.0).contains(&z));
        }
    }

    #[test]
    fn test_categorical_sampling() {
        let spaces: HashMap<String, StrategyParamSpace> =
            serde_json::from_str(test_json()).unwrap();
        let mut rng = StdRng::seed_from_u64(123);
        let mut seen = std::collections::HashSet::new();

        for _ in 0..100 {
            let params = sample_params(&spaces["vol_breakout"], &mut rng);
            let vt = params["vol_type"].as_str().unwrap().to_string();
            assert!(["atr", "std", "parkinson"].contains(&vt.as_str()));
            seen.insert(vt);
        }
        // All 3 categories should appear at least once in 100 samples
        assert_eq!(seen.len(), 3);
    }

    #[test]
    fn test_session_mapping() {
        let spaces: HashMap<String, StrategyParamSpace> =
            serde_json::from_str(test_json()).unwrap();
        let mut rng = StdRng::seed_from_u64(77);

        for _ in 0..50 {
            let params = sample_params(&spaces["session_momentum"], &mut rng);
            let session = params["active_session"].as_str().unwrap();
            let start = params["entry_hour_start"].as_i64().unwrap();
            let end = params["entry_hour_end"].as_i64().unwrap();
            match session {
                "asian" => {
                    assert_eq!(start, 0);
                    assert_eq!(end, 8);
                }
                "london" => {
                    assert_eq!(start, 8);
                    assert_eq!(end, 13);
                }
                "overlap" => {
                    assert_eq!(start, 13);
                    assert_eq!(end, 17);
                }
                "ny_late" => {
                    assert_eq!(start, 17);
                    assert_eq!(end, 22);
                }
                _ => panic!("unexpected session: {session}"),
            }
        }
    }

    #[test]
    fn test_computed_months() {
        let spaces: HashMap<String, StrategyParamSpace> =
            serde_json::from_str(test_json()).unwrap();
        let mut rng = StdRng::seed_from_u64(55);

        for _ in 0..50 {
            let params = sample_params(&spaces["seasonal_filter"], &mut rng);
            let months = params["active_months"].as_array().unwrap();
            let window = params["window_size"].as_i64().unwrap() as usize;
            assert_eq!(months.len(), window);

            // All months should be 1-12
            for m in months {
                let v = m.as_i64().unwrap();
                assert!((1..=12).contains(&v));
            }
        }
    }

    #[test]
    fn test_exit_sampling() {
        let mut rng = StdRng::seed_from_u64(42);
        let mut none_count = 0;
        let mut fixed_count = 0;
        let mut atr_count = 0;

        for _ in 0..300 {
            let (ec, meta) = sample_exit(&mut rng);
            let exit_type = meta["exit_type"].as_str().unwrap();
            match exit_type {
                "none" => {
                    assert!(ec.is_none());
                    none_count += 1;
                }
                "fixed_pct" => {
                    let ec = ec.unwrap();
                    assert!((0.01..=0.05).contains(&ec.sl_pct));
                    assert!((ec.sl_pct..=0.10).contains(&ec.tp_pct));
                    assert!(ec.sl_atr.is_nan());
                    fixed_count += 1;
                }
                "atr_based" => {
                    let ec = ec.unwrap();
                    assert!((0.5..=3.0).contains(&ec.sl_atr));
                    assert!((ec.sl_atr..=5.0).contains(&ec.tp_atr));
                    assert!(ec.sl_pct.is_nan());
                    atr_count += 1;
                }
                _ => panic!("unexpected exit type: {exit_type}"),
            }
        }
        // Each type should appear at least once
        assert!(none_count > 0);
        assert!(fixed_count > 0);
        assert!(atr_count > 0);
    }

    #[test]
    fn test_load_all_17_strategies() {
        let path =
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../config/param_spaces.json");
        let spaces = load_param_spaces(&path).expect("failed to load param_spaces.json");
        let expected = [
            "ema_atr",
            "sma_cross",
            "rsi_reversal",
            "donchian_breakout",
            "bb_pctb",
            "bb_squeeze",
            "macd_hist",
            "dual_momentum",
            "momentum_roc",
            "seasonal_filter",
            "vol_breakout",
            "keltner",
            "dxy_mean_reversion",
            "cross_asset_fx",
            "session_momentum",
            "month_end_jpy",
            "cluster_pair_drift",
            "time_of_day_drift",
        ];
        for name in &expected {
            assert!(spaces.contains_key(*name), "missing strategy: {name}");
        }
        assert_eq!(spaces.len(), 18);

        // Sample each 100 times
        let mut rng = StdRng::seed_from_u64(42);
        for (name, space) in &spaces {
            for _ in 0..100 {
                let params = sample_params(space, &mut rng);
                assert!(!params.is_empty(), "empty params for {name}");
            }
        }
    }

    #[test]
    fn time_of_day_drift_hold_bars_loads_as_categorical_int() {
        // Regression lock: time_of_day_drift.hold_bars must remain a
        // categorical of integer values matching BQ-validated horizons.
        // See docs/superpowers/specs/2026-04-08-time-of-day-drift-longhold-design.md.
        let path =
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../config/param_spaces.json");
        let spaces = load_param_spaces(&path).expect("failed to load param_spaces.json");
        let tod = spaces
            .get("time_of_day_drift")
            .expect("time_of_day_drift strategy missing");
        let hold = tod
            .params
            .get("hold_bars")
            .expect("hold_bars param missing");
        match hold {
            ParamDef::Categorical { choices } => {
                let values: Vec<i64> = choices.iter().filter_map(|v| v.as_i64()).collect();
                assert_eq!(
                    values,
                    vec![1, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240],
                    "hold_bars categorical choices mismatch"
                );
            }
            other => panic!("expected Categorical, got {other:?}"),
        }

        // Sample 200 times and ensure every draw produces an i64 in the allowed set.
        let allowed: std::collections::HashSet<i64> =
            [1, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240]
                .into_iter()
                .collect();
        let mut rng = StdRng::seed_from_u64(99);
        for _ in 0..200 {
            let params = sample_params(tod, &mut rng);
            let hold_val = params["hold_bars"]
                .as_i64()
                .expect("hold_bars sample should be i64");
            assert!(
                allowed.contains(&hold_val),
                "sampled hold_bars={hold_val} not in allowed set"
            );
        }
    }
}
