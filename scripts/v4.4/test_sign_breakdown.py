"""Phase 62 unit tests for scripts/v4.4/sign_breakdown.py.

Covers BREAK-01..05 + ATTR-01..03 + integration per 62-RESEARCH.md
§Phase Requirements → Test Map. Tests are RED until Wave 1 (Plans 02-06)
implements sign_breakdown.py.

Run:
    uv run pytest scripts/v4.4/test_sign_breakdown.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Deliberately imported at module level. Wave 0: this import FAILS (module doesn't
# exist). Wave 1+: this import MUST succeed and individual test assertions take over.
import sign_breakdown  # noqa: E402  — RED in Wave 0 by design

FIXTURES = SCRIPT_DIR / "fixtures"
PAIRS = ("usdjpy", "eurusd", "audusd", "eurjpy")
EVENTS = ("fomc", "ecb", "nfp")


def _slot(
    *,
    window_offset: int = 0,
    hold_bars: int = 1,
    exit_type: str = "none",
    fee_bps: float = 0.0,
    sign_value: int = 1,
    verdict: str = "Pass",
) -> dict:
    """Adapted from test_audit.py::_cell — single-slot builder."""
    return {
        "window_offset": window_offset,
        "hold_bars": hold_bars,
        "exit_type": exit_type,
        "fee_results": [{"fee_bps": fee_bps, "sign": sign_value, "verdict": verdict}],
    }


def _fixture_inputs() -> list[Path]:
    return [FIXTURES / f"v4.2_{pair}_subset.json" for pair in PAIRS]


# ─── BREAK-01 ──────────────────────────────────────────────────────────────
def test_per_slot_tally_shape_is_4x3x96():
    """BREAK-01: raw per-pair × per-event × per-slot sign tally.

    Expected shape: {pair: {event: {slot_key: {long, short, neutral}}}}
    """
    inputs = _fixture_inputs()
    tally = sign_breakdown.build_per_pair_event_slot_tally(inputs)
    assert set(tally.keys()) == set(PAIRS)
    for pair in PAIRS:
        assert set(tally[pair].keys()) == set(EVENTS)
        for event in EVENTS:
            assert len(tally[pair][event]) == 96, (
                f"{pair}/{event} has {len(tally[pair][event])} slot entries, expected 96"
            )
            sample = next(iter(tally[pair][event].values()))
            assert set(sample.keys()) == {"long", "short", "neutral"}


# ─── BREAK-02 ──────────────────────────────────────────────────────────────
def test_pass_conditional_filter_excludes_fail():
    """BREAK-02: pass-conditional tally must drop verdict=Fail slots.

    Fixture audusd/fomc slot 2 has verdict=Fail at fee=2 → raw total > pass total.
    """
    inputs = _fixture_inputs()
    raw = sign_breakdown.build_per_pair_event_slot_tally(inputs)
    passed = sign_breakdown.build_pass_conditional_tally(inputs)
    diffs = []
    for pair in PAIRS:
        for event in EVENTS:
            for slot_key, raw_counts in raw[pair][event].items():
                p_counts = passed[pair][event][slot_key]
                raw_total = sum(raw_counts.values())
                pass_total = sum(p_counts.values())
                if raw_total > pass_total:
                    diffs.append((pair, event, slot_key, raw_total, pass_total))
    assert diffs, (
        "No Fail slot filtered — fixture must contain at least one verdict=Fail"
    )


# ─── BREAK-03a: Clopper-Pearson exact CI (pair level, n=4) ─────────────────
def test_exact_pair_ci_k_equals_n_is_wide():
    """BREAK-03a: k=n=4 → Clopper-Pearson upper=1.0, lower ≈ 0.398 for alpha=0.05."""
    point, lo, hi = sign_breakdown.exact_pair_agreement_ci(
        observed_signs=np.array([1, 1, 1, 1]), alpha=0.05
    )
    assert point == 1.0
    assert hi == 1.0
    assert 0.39 < lo < 0.41, (
        f"Clopper-Pearson lower for k=4 n=4 expected ~0.398, got {lo}"
    )


# ─── BREAK-03b: Bootstrap reproducibility ──────────────────────────────────
def test_bootstrap_reproducible_same_seed():
    """BREAK-03b: Politis-Romano bootstrap with same seed → identical CI."""
    series = np.array([1, 0, 1, 1, 0, 1, 0, 1, 1, 0] * 5)  # N=50
    rng1 = np.random.default_rng(42)
    rng2 = np.random.default_rng(42)
    ci1 = sign_breakdown.politis_romano_bootstrap_ci(series, n_resamples=1000, rng=rng1)
    ci2 = sign_breakdown.politis_romano_bootstrap_ci(series, n_resamples=1000, rng=rng2)
    assert ci1 == ci2


# ─── BREAK-03c: Degenerate all-ones ────────────────────────────────────────
def test_bootstrap_degenerate_all_ones():
    """BREAK-03c: all-ones series → CI collapses to (1.0, 1.0)."""
    series = np.ones(50, dtype=int)
    rng = np.random.default_rng(42)
    lo, hi = sign_breakdown.politis_romano_bootstrap_ci(
        series, n_resamples=1000, rng=rng
    )
    assert lo == 1.0 and hi == 1.0


# ─── BREAK-03d: Block length heuristic ─────────────────────────────────────
def test_block_len_heuristic_matches_ceil_cuberoot():
    """BREAK-03d: block_len = ceil(n^(1/3)), floor at 1."""
    assert sign_breakdown.block_len_heuristic(27) == 3
    assert sign_breakdown.block_len_heuristic(64) == 4
    assert sign_breakdown.block_len_heuristic(20) == 3
    assert sign_breakdown.block_len_heuristic(1) == 1


# ─── BREAK-04 ──────────────────────────────────────────────────────────────
def test_pairwise_pvalue_matches_scipy_binomtest():
    """BREAK-04: pairwise agreement p-value matches scipy.stats.binomtest."""
    from scipy.stats import binomtest

    pv = sign_breakdown.pairwise_agreement_pvalue(k=10, n=10)
    expected = binomtest(10, 10, 0.5, alternative="two-sided").pvalue
    assert abs(pv - expected) < 1e-12


# ─── BREAK-05a: Fleiss kappa canonical ─────────────────────────────────────
def test_fleiss_kappa_canonical_table():
    """BREAK-05a: Fleiss κ on canonical 4-subject × 2-category table."""
    table = np.array([[4, 0], [3, 1], [2, 2], [0, 4]])
    kappa = sign_breakdown.fleiss_kappa_wrapper(table)
    # Reference value from statsmodels 0.14.6
    assert abs(kappa - 0.4074074074074074) < 1e-6 or (0.35 < kappa < 0.50)


# ─── BREAK-05b: Cohen kappa perfect ────────────────────────────────────────
def test_cohen_kappa_perfect_is_one():
    """BREAK-05b: perfectly diagonal contingency → Cohen κ = 1.0."""
    ct = np.array([[10, 0], [0, 10]])
    k = sign_breakdown.cohen_kappa_wrapper(ct)
    assert abs(k - 1.0) < 1e-9


# ─── BREAK-05c: Landis-Koch interpretation boundaries ──────────────────────
def test_landis_koch_interpretation_boundaries():
    """BREAK-05c: Landis-Koch categorical bucketing per D-16."""
    assert sign_breakdown.interpret_kappa(-0.1) == "poor"
    assert sign_breakdown.interpret_kappa(0.0) == "slight"
    assert sign_breakdown.interpret_kappa(0.20) == "slight"
    assert sign_breakdown.interpret_kappa(0.21) == "fair"
    assert sign_breakdown.interpret_kappa(0.40) == "fair"
    assert sign_breakdown.interpret_kappa(0.41) == "moderate"
    assert sign_breakdown.interpret_kappa(0.60) == "moderate"
    assert sign_breakdown.interpret_kappa(0.61) == "substantial"
    assert sign_breakdown.interpret_kappa(0.80) == "substantial"
    assert sign_breakdown.interpret_kappa(0.81) == "almost_perfect"
    assert sign_breakdown.interpret_kappa(1.0) == "almost_perfect"


# ─── ATTR-01 ───────────────────────────────────────────────────────────────
def test_stratified_3d_has_axis_event_horizon_fee_pair():
    """ATTR-01: stratified[event][horizon][fee][pair] = agreement value.

    Axis: 3 event × 6 horizon × 5 fee × 4 pair.
    """
    inputs = _fixture_inputs()
    strat = sign_breakdown.build_stratified_3d(inputs)
    assert set(strat.keys()) == set(EVENTS)
    for event in EVENTS:
        horizons = set(strat[event].keys())
        assert horizons, f"no horizon keys for event={event}"
        for horizon in horizons:
            fees = set(strat[event][horizon].keys())
            assert 5 >= len(fees) >= 1, (
                f"fee axis size {len(fees)} for ({event},{horizon})"
            )


# ─── ATTR-02 ───────────────────────────────────────────────────────────────
def test_simpson_flag_triggers_when_diff_exceeds_threshold():
    """ATTR-02: Simpson flag fires when max|stratum-pooled| > 0.3."""
    flag, diff = sign_breakdown.detect_simpson(
        pooled_agreement=0.5,
        stratified={"a": 0.5, "b": 1.0, "c": 0.4},
        threshold=0.3,
    )
    assert flag is True
    assert abs(diff - 0.5) < 1e-9
    flag2, diff2 = sign_breakdown.detect_simpson(0.5, {"a": 0.7, "b": 0.4}, 0.3)
    assert flag2 is False
    assert abs(diff2 - 0.2) < 1e-9


# ─── ATTR-03 ───────────────────────────────────────────────────────────────
def test_fee_sign_flip_detected_when_sign_reverses():
    """ATTR-03: sign flip across fee axis → entry in flips list.

    Fixture fomc/slot 0 embeds fee=0 → +1, fee=5 → -1.
    """
    inputs = _fixture_inputs()
    flips = sign_breakdown.detect_fee_sign_flip(inputs)
    assert isinstance(flips, list)
    fomc_flips = [f for f in flips if f.get("event") == "fomc"]
    assert fomc_flips, f"no fee flip detected on fomc (got {flips})"
    sample = fomc_flips[0]
    assert set(sample.keys()) >= {
        "event",
        "fee_low",
        "fee_high",
        "sign_low",
        "sign_high",
    }


# ─── Integration / E2E ────────────────────────────────────────────────────
def test_e2e_fixture_emits_schema_compliant_json(tmp_path):
    """Integration: CLI run against the 4 fixture files emits D-20 schema."""
    out = tmp_path / "sign_breakdown.json"
    cmd = [
        "uv",
        "run",
        "python",
        str(SCRIPT_DIR / "sign_breakdown.py"),
        "--output",
        str(out),
        "--seed",
        "42",
        "--n-resamples",
        "500",
    ]
    for p in _fixture_inputs():
        cmd += ["--input", str(p)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    data = json.loads(out.read_text())
    required = {
        "phase",
        "date",
        "generated_at",
        "per_pair_event_slot_tally",
        "pass_conditional_tally",
        "sign_matrix_4x4",
        "pairwise_agreement_pvalue",
        "kappa",
        "bootstrap_ci",
        "stratified_3d",
        "simpson_flag",
        "simpson_diff",
        "fee_sign_flip",
    }
    assert required <= set(data.keys()), f"missing keys: {required - set(data.keys())}"
    assert data["phase"] == 62
    assert "fleiss" in data["kappa"]
    assert "cohen_pairwise" in data["kappa"]
    assert "pair_level" in data["bootstrap_ci"]
    assert "event_level" in data["bootstrap_ci"]


# ============================================================
# Phase 66 Wave 0 RED tests — Wave 1 で _derive_sign / _load_eurusd_3subdir / schema compat 実装時に GREEN 化
# Source: CONTEXT.md D-03, D-04, D-05, D-15 ; PATTERNS.md "Target tests" section
# ============================================================


def test_derive_sign_zero_trades():
    """D-05: combined_oos_trades == 0 -> sign=0 (neutral)."""
    entry = {"combined_oos_trades": 0, "combined_oos_pf": 2.5, "fee_bps": 0.0}
    assert sign_breakdown._derive_sign(entry) == 0


def test_derive_sign_pf_ge_1():
    """D-05: trades>0 and pf>=1.0 -> sign=+1 (long)."""
    entry = {"combined_oos_trades": 42, "combined_oos_pf": 1.0, "fee_bps": 2.0}
    assert sign_breakdown._derive_sign(entry) == 1
    entry2 = {"combined_oos_trades": 10, "combined_oos_pf": 3.14, "fee_bps": 0.0}
    assert sign_breakdown._derive_sign(entry2) == 1


def test_derive_sign_pf_lt_1():
    """D-05: trades>0 and pf<1.0 -> sign=-1 (short)."""
    entry = {"combined_oos_trades": 42, "combined_oos_pf": 0.99, "fee_bps": 0.0}
    assert sign_breakdown._derive_sign(entry) == -1
    entry2 = {"combined_oos_trades": 10, "combined_oos_pf": 0.0, "fee_bps": 5.0}
    assert sign_breakdown._derive_sign(entry2) == -1


def test_derive_sign_pf_missing(caplog):
    """D-05: trades>0 and pf is None -> sign=0 + LOGGER.warning."""
    import logging as _logging

    entry = {"combined_oos_trades": 42, "fee_bps": 0.0}  # no combined_oos_pf
    with caplog.at_level(_logging.WARNING):
        result = sign_breakdown._derive_sign(entry)
    assert result == 0
    assert any("pf" in rec.getMessage().lower() for rec in caplog.records)


def test_eurusd_3subdir_aggregation(tmp_path):
    """D-03: v3.9-cross-pair/eurusd/{fomc,ecb,nfp}/report.json 3-subdir aggregation loader.

    Uses the real v3.9 shape: per-event report.json top-level is a *list* of slot
    dicts (NOT a dict with a 'slots' key). Matches the schema observed in
    docs/reports/v3.9-cross-pair/eurusd/{fomc,ecb,nfp}/report.json (len=96 list).
    """
    pair_dir = tmp_path / "eurusd"
    slot_template = [
        {
            "window_offset": 0,
            "hold_bars": 1,
            "exit_type": "none",
            "fee_results": [
                {
                    "fee_bps": 0.0,
                    "combined_oos_trades": 10,
                    "combined_oos_pf": 2.0,
                    "verdict": "Pass",
                },
                {
                    "fee_bps": 2.0,
                    "combined_oos_trades": 0,
                    "combined_oos_pf": 0.0,
                    "verdict": "Fail",
                },
            ],
        }
    ]
    for event in ("fomc", "ecb", "nfp"):
        subdir = pair_dir / event
        subdir.mkdir(parents=True)
        # Real v3.9 layout: TOP-LEVEL LIST of slot dicts (no 'slots' wrapper).
        (subdir / "report.json").write_text(json.dumps(slot_template))
    loaded = sign_breakdown._load_eurusd_3subdir(pair_dir)
    assert set(loaded.keys()) == {"fomc", "ecb", "nfp"}
    for event in ("fomc", "ecb", "nfp"):
        assert len(loaded[event]) == 1, (
            f"{event}: expected 1 slot from list-shape report, got {len(loaded[event])}"
        )
        # After _derive_sign is injected (Wave 1), fee entries carry integer sign.
        entries = loaded[event][0]["fee_results"]
        assert entries[0].get("sign") == 1  # trades=10, pf=2.0 >= 1.0
        assert entries[1].get("sign") == 0  # trades=0


def test_eurusd_3subdir_aggregation_on_real_v39_data():
    """D-03: ingest a real v3.9-cross-pair/eurusd event file and derive signs.

    Guard against regression of the Plan 66-03 Stage-1 blocker: adapter must
    handle the list-top-level shape of real v3.9 report.json (len=96 per event).
    Test is skipped if real-data snapshot is unavailable (CI-friendly).
    """
    import pytest

    real_pair_dir = Path("docs/reports/v3.9-cross-pair/eurusd")
    if not (real_pair_dir / "fomc" / "report.json").exists():
        pytest.skip("real v3.9 eurusd snapshot not present in this checkout")
    loaded = sign_breakdown._load_eurusd_3subdir(real_pair_dir)
    assert set(loaded.keys()) == {"fomc", "ecb", "nfp"}
    # At least one event must ingest non-empty slots (real v3.9 layout has 96 per event).
    assert any(len(v) > 0 for v in loaded.values()), (
        f"all events empty from real v3.9 pair_dir {real_pair_dir}"
    )
    # Fee entries on ingested slots must carry integer 'sign' (derived, not fixture).
    for event, slots in loaded.items():
        for slot in slots:
            for entry in slot.get("fee_results", []):
                assert isinstance(entry.get("sign"), int), (
                    f"{event} slot {slot.get('window_offset')} fee={entry.get('fee_bps')}:"
                    f" sign not derived ({entry.get('sign')!r})"
                )


def test_real_emit_schema_compat(tmp_path):
    """D-15 / EMIT-03: real emit sign_breakdown.json が fixture emit と top-level key 集合互換。
    Wave 1 実装後は _load_report_as_event_slots が real schema から slot を返すことを確認。
    """
    # Build a mini real-schema fixture (no 'sign' field in fee_results, flat slots at top level)
    real_src = tmp_path / "v4.2-audusd"
    real_src.mkdir()
    real_report = {
        "slots": [
            {
                "window_offset": 0,
                "hold_bars": 1,
                "exit_type": "none",
                "fee_results": [
                    {
                        "fee_bps": 0.0,
                        "combined_oos_trades": 5,
                        "combined_oos_pf": 1.5,
                        "verdict": "Pass",
                    },
                ],
            }
        ]
    }
    (real_src / "report.json").write_text(json.dumps(real_report))

    loaded_real = sign_breakdown._load_report_as_event_slots(real_src / "report.json")
    # Must return a dict[str, list] with EVENTS keys (may be empty lists), schema identical to fixture loader
    assert isinstance(loaded_real, dict)
    for event in ("fomc", "ecb", "nfp"):
        assert event in loaded_real
        assert isinstance(loaded_real[event], list)
    # Wave 1: real schema (flat slots) must produce at least one non-empty event list
    # (current pre-Wave1 impl returns all empty -> this assert is RED)
    assert any(len(v) > 0 for v in loaded_real.values()), (
        "Wave 1 must make _load_report_as_event_slots return slots from real flat schema"
    )


# ============================================================
# Phase 71 Plan 01 — Shape 4 (fresh WFD per-pair/per-event layout) loader tests
# Source: 71-CONTEXT.md D-03, D-04 ; 71-01-PLAN.md Task 1
# RED: current Shape 2 branch buckets all slots into EVENTS[0]="fomc",
# leaking ECB/NFP into the fomc bucket.
# ============================================================


def _shape4_minimal_slot(
    *,
    window_offset: int = 0,
    hold_bars: int = 6,
    exit_type: str = "MaxPnLWindow",
    fee_bps: float = 0.0,
    pf: float = 1.2,
    trades: int = 10,
) -> dict:
    """Minimal fresh-WFD-shape slot dict (no top-level sign; derived via _derive_sign)."""
    return {
        "window_offset": window_offset,
        "hold_bars": hold_bars,
        "exit_type": exit_type,
        "fee_results": [
            {
                "fee_bps": fee_bps,
                "combined_oos_pf": pf,
                "combined_oos_trades": trades,
            }
        ],
    }


def test_load_shape4_per_event_path(tmp_path):
    """D-03/D-04 Shape 4: per-event report.json with in-file `event` key.

    A fresh WFD report with `{"pair": "audusd", "event": "ecb", "slots": [...]}`
    must route slots into the `ecb` bucket. Pre-fix Shape 2 branch incorrectly
    routes them into `fomc`, leaking ECB into FOMC.
    """
    report = {
        "pair": "audusd",
        "event": "ecb",
        "slots": [_shape4_minimal_slot()],
    }
    p = tmp_path / "report.json"
    p.write_text(json.dumps(report))

    out = sign_breakdown._load_report_as_event_slots(p)

    assert isinstance(out, dict)
    assert set(out.keys()) == {"fomc", "ecb", "nfp"}
    assert len(out["ecb"]) == 1, f"expected 1 slot in ecb bucket, got {out}"
    assert out["fomc"] == [], f"fomc must be empty, got {out['fomc']}"
    assert out["nfp"] == [], f"nfp must be empty, got {out['nfp']}"


def test_load_shape4_prefers_in_file_event_key(tmp_path):
    """D-04 priority 1: in-file `event` key wins over path inference.

    File path hints `fomc` via dir layout, but JSON body declares `event=nfp`.
    Loader must respect the in-file key (slots route to nfp, NOT fomc).
    """
    # Build path that LOOKS like a fomc report by directory hint
    fake_path = tmp_path / "per-pair" / "audusd" / "fomc" / "report.json"
    fake_path.parent.mkdir(parents=True)
    # But the JSON body says nfp -- in-file key MUST win
    report = {
        "pair": "audusd",
        "event": "nfp",
        "slots": [_shape4_minimal_slot()],
    }
    fake_path.write_text(json.dumps(report))

    out = sign_breakdown._load_report_as_event_slots(fake_path)

    assert len(out["nfp"]) == 1, (
        f"in-file event=nfp must win over path hint=fomc, got nfp={out['nfp']}"
    )
    assert out["fomc"] == [], (
        f"path-hinted fomc must be empty (in-file key takes priority), got {out['fomc']}"
    )
    assert out["ecb"] == []


def test_load_shape4_fresh_audusd_ecb_real():
    """D-03 end-to-end: real fresh WFD report for audusd/ecb routes correctly.

    Smoke against the actual Phase 70 output committed at 2efa119.
    Expected: 96 slots in `ecb`, 0 in fomc/nfp.
    Skip if the snapshot is not present (CI-friendly).
    """
    import pytest

    real = Path("docs/reports/v4.6-verdict-resolution/per-pair/audusd/ecb/report.json")
    if not real.exists():
        pytest.skip(f"fresh WFD snapshot not present: {real}")

    out = sign_breakdown._load_report_as_event_slots(real)

    assert len(out["ecb"]) == 96, f"expected 96 slots in ecb, got {len(out['ecb'])}"
    assert len(out["fomc"]) == 0, (
        f"ecb report must NOT leak into fomc (got {len(out['fomc'])})"
    )
    assert len(out["nfp"]) == 0, (
        f"ecb report must NOT leak into nfp (got {len(out['nfp'])})"
    )


def test_main_emits_meta_with_input_provenance_stamp(tmp_path):
    """Phase 71 D-12: sign_breakdown.json carries meta.input_provenance_stamp.

    Single unanimous stamp -> string. seed/input_count populated.
    """
    for i, ev in enumerate(("fomc", "ecb")):
        p = tmp_path / f"r{i}.json"
        p.write_text(json.dumps({
            "data_provenance": "test-stamp-xyz",
            "pair": "usdjpy",
            "event": ev,
            "slots": [],
        }))
    out = tmp_path / "out.json"
    rc = sign_breakdown.main([
        "--input", str(tmp_path / "r0.json"),
        "--input", str(tmp_path / "r1.json"),
        "--output", str(out),
        "--seed", "42",
    ])
    assert rc == 0
    data = json.loads(out.read_text())
    assert "meta" in data
    assert data["meta"]["input_provenance_stamp"] == "test-stamp-xyz"
    assert data["meta"]["seed"] == 42
    assert data["meta"]["input_count"] == 2
