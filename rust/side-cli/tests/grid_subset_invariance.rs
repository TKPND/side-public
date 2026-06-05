//! Integration test for GRID-01 subset invariance (Phase 76 Plan 01).
//!
//! Spawns `side wfd-rerun --pair usdjpy --event fomc` with the Phase 76
//! 192-slot `WINDOW_OFFSETS` (= `[1..=16]`), filters `slots[]` where
//! `window_offset <= 8`, and compares the backtest-math fields against the
//! v4.6 golden report at
//! `docs/reports/v4.6-verdict-resolution/per-pair/usdjpy/fomc/report.json`.
//!
//! Compared fields (engine-level backtest invariants that MUST survive the
//! WINDOW_OFFSETS 8→16 bump) for non-zero-trade slots:
//!
//! - Slot identity: `window_offset`, `hold_bars`, `exit_type`
//! - Per-fee payloads: `fee_bps`, `combined_oos_pf`, `combined_oos_sharpe`,
//!   `combined_oos_trades`, `combined_oos_max_dd`, `passed`
//!
//! Semantic exception:
//!
//! - FOMC `window_offset=1` self-prunes to zero trades because entering on the
//!   announcement bar would introduce look-ahead bias. The v4.6 golden preserved
//!   obsolete zero-trade semantics (`combined_oos_pf=null`, `passed=true`). The
//!   current contract is `combined_oos_pf=0.0`, `passed=false`; those slots are
//!   asserted explicitly instead of copied from the historical golden.
//!
//! Excluded fields (legitimately differ by design — D-02 lockstep):
//!
//! - `dsr_pvalue` — Bonferroni correction denominator changes with `dsr_n_trials`
//! - `dsr_n_trials` — Phase 76 D-02 bump 96→192 is the lockstep pre-registered
//!   consequence of WINDOW_OFFSETS 8→16 (Phase 74 D-08). The v4.6 golden was
//!   frozen with `dsr_n_trials=96`.
//!
//! This is PATTERNS.md §1 Template: D-04 (gate), D-05 (1 pair × 1 event),
//! D-06 (Value field-wise, not raw bytes). PLAN step 7 fallback adopted as
//! first-pass because D-02 lockstep makes whole-`Value` bit-exact structurally
//! impossible (`dsr_n_trials` field literal 96 vs 192 + `dsr_pvalue` divergence
//! on the 96-subset).

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::Value;
use side_engine::scanner::macro_event::{EXIT_TYPES, HOLD_BARS_VALUES};
use tempfile::TempDir;

#[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd)]
struct SlotKey {
    window_offset: u64,
    hold_bars: u64,
    exit_type: String,
}

impl fmt::Display for SlotKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "off={} hold={} exit={}",
            self.window_offset, self.hold_bars, self.exit_type
        )
    }
}

/// Path to the `side` binary compiled by cargo for this test.
fn side_binary() -> &'static str {
    env!("CARGO_BIN_EXE_side")
}

/// Resolve the repository root (the directory containing the `rust/` workspace).
/// Cargo sets `CARGO_MANIFEST_DIR` to `<repo>/rust/side-cli`; the repo root is
/// two levels up.
fn repo_root() -> PathBuf {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    PathBuf::from(manifest_dir)
        .parent()
        .and_then(|p| p.parent())
        .expect("CARGO_MANIFEST_DIR has a grandparent")
        .to_path_buf()
}

/// Spawn `side wfd-rerun --pair usdjpy --event fomc --output-dir <tmp>` and
/// return the parsed `report.json` alongside the owning `TempDir`.
fn run_wfd_rerun_usdjpy_fomc(root: &Path) -> (TempDir, Value) {
    let tmp = tempfile::tempdir().expect("tempdir");
    let output_dir = tmp.path().to_string_lossy().into_owned();

    let output = Command::new(side_binary())
        .current_dir(root)
        .args([
            "wfd-rerun",
            "--pair",
            "usdjpy",
            "--event",
            "fomc",
            "--output-dir",
            &output_dir,
        ])
        .output()
        .expect("spawn side wfd-rerun");

    assert!(
        output.status.success(),
        "side wfd-rerun exited non-zero (status={:?}).\nstdout:\n{}\nstderr:\n{}",
        output.status,
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );

    let report_path = tmp.path().join("usdjpy").join("fomc").join("report.json");
    let text = std::fs::read_to_string(&report_path).unwrap_or_else(|e| {
        panic!(
            "failed to read report.json at {}: {e}\nstdout:\n{}\nstderr:\n{}",
            report_path.display(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        )
    });
    let value: Value = serde_json::from_str(&text).expect("report.json is valid JSON");
    (tmp, value)
}

fn slot_key(slot: &Value) -> SlotKey {
    SlotKey {
        window_offset: slot["window_offset"]
            .as_u64()
            .expect("slot.window_offset is u64"),
        hold_bars: slot["hold_bars"].as_u64().expect("slot.hold_bars is u64"),
        exit_type: slot["exit_type"]
            .as_str()
            .expect("slot.exit_type is string")
            .to_string(),
    }
}

fn slots_by_key<'a, I>(slots: I) -> BTreeMap<SlotKey, &'a Value>
where
    I: IntoIterator<Item = &'a Value>,
{
    let mut out = BTreeMap::new();
    for slot in slots {
        let key = slot_key(slot);
        if out.insert(key.clone(), slot).is_some() {
            panic!("duplicate slot key in report: {key}");
        }
    }
    out
}

fn fee_results(slot: &Value) -> &[Value] {
    slot["fee_results"]
        .as_array()
        .expect("fee_results is an array")
}

fn is_zero_trade_slot(slot: &Value) -> bool {
    let fees = fee_results(slot);
    assert!(
        !fees.is_empty(),
        "zero-trade classification requires at least one fee result"
    );
    fees.iter()
        .all(|fr| fr["combined_oos_trades"].as_u64() == Some(0))
}

#[test]
#[should_panic(expected = "zero-trade classification requires at least one fee result")]
fn zero_trade_classifier_rejects_empty_fee_results() {
    let slot = serde_json::json!({ "fee_results": [] });

    let _ = is_zero_trade_slot(&slot);
}

/// Extract the tuple of backtest-math fields we assert invariance over.
fn backtest_invariants(slot: &Value) -> Value {
    let fee_projected: Vec<Value> = fee_results(slot)
        .iter()
        .map(|fr| {
            serde_json::json!({
                "fee_bps": fr["fee_bps"].clone(),
                "combined_oos_pf": fr["combined_oos_pf"].clone(),
                "combined_oos_sharpe": fr["combined_oos_sharpe"].clone(),
                "combined_oos_trades": fr["combined_oos_trades"].clone(),
                "combined_oos_max_dd": fr["combined_oos_max_dd"].clone(),
                "passed": fr["passed"].clone(),
            })
        })
        .collect();

    serde_json::json!({
        "window_offset": slot["window_offset"].clone(),
        "hold_bars": slot["hold_bars"].clone(),
        "exit_type": slot["exit_type"].clone(),
        "fee_results": fee_projected,
    })
}

fn assert_current_zero_trade_contract(key: &SlotKey, slot: &Value) {
    assert_eq!(
        key.window_offset, 1,
        "only FOMC window_offset=1 self-pruned slots may use the zero-trade semantic exception"
    );
    let fees = fee_results(slot);
    assert_eq!(
        fees.len(),
        5,
        "{key} zero-trade semantic exception must still cover all fee levels"
    );
    let fee_bps: Vec<f64> = fees
        .iter()
        .map(|fr| fr["fee_bps"].as_f64().expect("fee_bps is f64"))
        .collect();
    assert_eq!(
        fee_bps,
        vec![0.0, 1.0, 2.0, 3.0, 5.0],
        "{key} zero-trade semantic exception must preserve fee sweep levels"
    );
    for (idx, fr) in fees.iter().enumerate() {
        assert_eq!(
            fr["combined_oos_trades"].as_u64(),
            Some(0),
            "{key} fee_result[{idx}] must remain zero-trade"
        );
        assert_eq!(
            fr["combined_oos_pf"].as_f64(),
            Some(0.0),
            "{key} fee_result[{idx}] zero-trade PF must follow the current WfdSingleResult contract"
        );
        assert_eq!(
            fr["passed"].as_bool(),
            Some(false),
            "{key} fee_result[{idx}] zero-trade slot must not pass the gate"
        );
    }
}

fn assert_obsolete_golden_zero_trade_shape(key: &SlotKey, slot: &Value) {
    let fees = fee_results(slot);
    assert_eq!(
        fees.len(),
        5,
        "{key} v4.6 golden zero-trade slot must still cover all fee levels"
    );
    let fee_bps: Vec<f64> = fees
        .iter()
        .map(|fr| fr["fee_bps"].as_f64().expect("fee_bps is f64"))
        .collect();
    assert_eq!(
        fee_bps,
        vec![0.0, 1.0, 2.0, 3.0, 5.0],
        "{key} v4.6 golden zero-trade slot must preserve fee sweep levels"
    );
    for (idx, fr) in fees.iter().enumerate() {
        assert_eq!(
            fr["combined_oos_trades"].as_u64(),
            Some(0),
            "{key} v4.6 golden fee_result[{idx}] must be the zero-trade slot being retired"
        );
        assert!(
            fr["combined_oos_pf"].is_null(),
            "{key} v4.6 golden fee_result[{idx}] should preserve the obsolete null PF shape"
        );
        assert_eq!(
            fr["passed"].as_bool(),
            Some(true),
            "{key} v4.6 golden fee_result[{idx}] should preserve the obsolete false-pass shape"
        );
    }
}

/// GRID-01 gate: the 96-subset carved out of the 192-slot expanded grid must
/// preserve v4.6 non-zero-trade backtest math, while the FOMC `window_offset=1`
/// zero-trade slots must follow the current fail-closed semantic contract.
/// (Excludes `dsr_pvalue` / `dsr_n_trials` per D-02 lockstep; see module doc.)
#[test]
fn subset_of_192_slots_preserves_v46_nonzero_invariants_and_zero_trade_contract() {
    let root = repo_root();

    // ----- Precondition guards ---------------------------------------------
    let mirror_csv = root.join("rust/data/mirror/USDJPY_1h_2022_2023.csv");
    assert!(
        mirror_csv.exists(),
        "precondition missing: {} (Plan 02 deliverable)",
        mirror_csv.display(),
    );
    let golden_path =
        root.join("docs/reports/v4.6-verdict-resolution/per-pair/usdjpy/fomc/report.json");
    assert!(
        golden_path.exists(),
        "golden file missing: {} (v4.6 shipped artifact, pinned by D-04)",
        golden_path.display(),
    );
    let bin = Path::new(side_binary());
    assert!(bin.exists(), "side binary not built: {}", bin.display());

    // ----- Run the new (192-slot) engine ----------------------------------
    let (_tmp, v_new) = run_wfd_rerun_usdjpy_fomc(&root);

    // ----- Load golden ----------------------------------------------------
    let golden_text = std::fs::read_to_string(&golden_path)
        .unwrap_or_else(|e| panic!("failed to read golden {}: {e}", golden_path.display()));
    let golden: Value =
        serde_json::from_str(&golden_text).expect("v4.6 golden report.json is valid JSON");

    // ----- Filter window_offset <= 8 (D-04: 96-subset) --------------------
    let slots_new = v_new["slots"]
        .as_array()
        .expect("new report.slots is an array");
    let subset: Vec<&Value> = slots_new
        .iter()
        .filter(|s| s["window_offset"].as_u64().unwrap_or(0) <= 8)
        .collect();
    let slots_golden = golden["slots"]
        .as_array()
        .expect("golden report.slots is an array");

    assert_eq!(
        subset.len(),
        slots_golden.len(),
        "96-subset count must match v4.6 golden: new={} golden={}",
        subset.len(),
        slots_golden.len(),
    );

    let subset_by_key = slots_by_key(subset.iter().copied());
    let golden_by_key = slots_by_key(slots_golden.iter());
    let subset_keys: Vec<SlotKey> = subset_by_key.keys().cloned().collect();
    let golden_keys: Vec<SlotKey> = golden_by_key.keys().cloned().collect();
    assert_eq!(
        subset_keys, golden_keys,
        "96-subset slot key set must match v4.6 golden before field-wise comparison"
    );

    // ----- Field-wise semantic comparison (D-06) ---------------------------
    let expected_zero_trade_semantic_slots = HOLD_BARS_VALUES.len() * EXIT_TYPES.len();
    let mut zero_trade_semantic_slots = 0usize;
    let mut mismatches: Vec<String> = Vec::new();

    for (key, new_slot) in &subset_by_key {
        let gold_slot = golden_by_key
            .get(key)
            .expect("slot key set was asserted equal");
        let new_zero = is_zero_trade_slot(new_slot);
        let gold_zero = is_zero_trade_slot(gold_slot);

        if key.window_offset == 1 && new_zero && gold_zero {
            assert_current_zero_trade_contract(key, new_slot);
            assert_obsolete_golden_zero_trade_shape(key, gold_slot);
            zero_trade_semantic_slots += 1;
            continue;
        }

        if new_zero || gold_zero {
            mismatches.push(format!(
                "{key} has unexpected zero-trade classification: new_zero={new_zero} golden_zero={gold_zero}; \
                 only FOMC window_offset=1 self-pruned slots may bypass literal v4.6 comparison"
            ));
            continue;
        }

        let new_proj = backtest_invariants(new_slot);
        let gold_proj = backtest_invariants(gold_slot);
        if new_proj != gold_proj {
            mismatches.push(format!(
                "{key} diverged:\n  NEW    = {}\n  GOLDEN = {}",
                serde_json::to_string(&new_proj).unwrap_or_default(),
                serde_json::to_string(&gold_proj).unwrap_or_default(),
            ));
        }
    }

    assert_eq!(
        zero_trade_semantic_slots, expected_zero_trade_semantic_slots,
        "exactly window_offset=1 × HOLD_BARS_VALUES × EXIT_TYPES should be classified as the retired v4.6 zero-trade false-pass shape"
    );
    assert!(
        mismatches.is_empty(),
        "{} non-zero slot(s) diverged from v4.6 golden on backtest-math fields:\n{}",
        mismatches.len(),
        mismatches.join("\n\n"),
    );
}
