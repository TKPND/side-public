"""Phase 83 VERDICT-02 RED tests (TDD Wave 0).

Tests cover:
- check_config_drift: SCOPE-03 abort on git commit prefix mismatch
- bootstrap_pvalue: seed reproducibility, range, underpowered / zero-observed edge cases
- compute_vif_bar: clamping, nominal value
- apply_bonferroni_holm: method literal, length, monotonicity
- build_canonical_pvalue_array: length=72, canonical ordering, padded sentinel
- run_fwer_only: output dict keys + literal field values (integration smoke)
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# -- importlib load pattern (scripts/v4.8 is not a valid Python package path) --
_SPEC = importlib.util.spec_from_file_location(
    "verdict_fwer", Path(__file__).parent / "verdict_fwer.py"
)
verdict_fwer = importlib.util.module_from_spec(_SPEC)
sys.modules["verdict_fwer"] = verdict_fwer
_SPEC.loader.exec_module(verdict_fwer)

M_HYPOTHESES = verdict_fwer.M_HYPOTHESES
ALPHA = verdict_fwer.ALPHA
REGIME_COMMIT = verdict_fwer.REGIME_COMMIT
PADDED_PAIR_SENTINEL = verdict_fwer.PADDED_PAIR_SENTINEL
bootstrap_pvalue = verdict_fwer.bootstrap_pvalue
check_config_drift = verdict_fwer.check_config_drift
compute_vif_bar = verdict_fwer.compute_vif_bar
apply_bonferroni_holm = verdict_fwer.apply_bonferroni_holm
build_canonical_pvalue_array = verdict_fwer.build_canonical_pvalue_array
run_fwer_only = verdict_fwer.run_fwer_only


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_EVENTS = ("ECB", "FOMC", "NFP")
_PAIRS = ("EURJPY", "EURUSD", "USDJPY")
_L0_CELLS = (
    "0-60m_x_HIGH",
    "0-60m_x_LOW",
    "0-60m_x_MID",
    "60-120m_x_HIGH",
    "60-120m_x_LOW",
    "60-120m_x_MID",
)


def _make_slot_df(n_slots_per_combo: int = 10) -> pd.DataFrame:
    """Minimal synthetic slot_labels DataFrame for unit tests.

    Generates 3 events × 6 L0 cells × 3 pairs × n_slots rows.
    long/neutral/short are one-hot dummies (long=1 for all rows).
    """
    rows = []
    for event in _EVENTS:
        for cell in _L0_CELLS:
            for pair in _PAIRS:
                for _ in range(n_slots_per_combo):
                    rows.append(
                        {
                            "event_type": event,
                            "cell_id": cell,
                            "pair": pair,
                            "long": 1,
                            "neutral": 0,
                            "short": 0,
                        }
                    )
    return pd.DataFrame(rows)


def _make_regime_breakdown() -> dict:
    """Minimal synthetic regime_breakdown dict mimicking Phase 82 output."""
    cells_by_event_pooled = {}
    for event in _EVENTS:
        cells_by_event_pooled[event] = [
            {
                "pool_cell_id": "0-60m",
                "vif": 1.021,
                "rho_bar": 0.70,
                "n_eff_predicted": 14.0,
                "bootstrap_ci": {"lower": 0.01, "upper": 0.50},
                "sign_ratio": 0.55,
            },
            {
                "pool_cell_id": "60-120m",
                "vif": 1.021,
                "rho_bar": 0.70,
                "n_eff_predicted": 16.0,
                "bootstrap_ci": {"lower": 0.02, "upper": 0.48},
                "sign_ratio": 0.52,
            },
        ]
    return {
        "wave1_decision": "proceed",
        "wave1_fallback_level": 1,
        "cells_by_event_pooled": cells_by_event_pooled,
    }


# ---------------------------------------------------------------------------
# check_config_drift
# ---------------------------------------------------------------------------


def test_config_drift_abort(tmp_path: Path) -> None:
    """check_config_drift exits 1 when git log prefix != REGIME_COMMIT (SCOPE-03)."""
    bad_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="deadbeef some other commit\n", stderr=""
    )
    with patch("subprocess.run", return_value=bad_result):
        with pytest.raises(SystemExit) as exc_info:
            check_config_drift("data/regime_cuts.json")
    assert exc_info.value.code == 1


def test_config_drift_pass(tmp_path: Path) -> None:
    """check_config_drift returns None when git log prefix matches REGIME_COMMIT."""
    good_result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{REGIME_COMMIT} docs(79): seal v4.8 pre-registration anchor\n",
        stderr="",
    )
    with patch("subprocess.run", return_value=good_result):
        result = check_config_drift("data/regime_cuts.json")
    assert result is None


def test_config_drift_message_scope03(tmp_path: Path) -> None:
    """SCOPE-03 mismatch error message contains 'SCOPE-03 violation'."""
    bad_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="deadbeef unrelated commit\n", stderr=""
    )
    import io

    captured = io.StringIO()
    with patch("subprocess.run", return_value=bad_result):
        with patch("sys.stderr", captured):
            with pytest.raises(SystemExit):
                check_config_drift("data/regime_cuts.json")
    assert "SCOPE-03 violation" in captured.getvalue()


# ---------------------------------------------------------------------------
# bootstrap_pvalue
# ---------------------------------------------------------------------------


def test_bootstrap_pvalue_reproducible() -> None:
    """Same 12-element sign array → identical p-value on two calls (seed=42 fixed)."""
    arr = np.array([1, -1, 1, 1, -1, 1, -1, 1, 1, -1, 1, -1], dtype=float)
    p1 = bootstrap_pvalue(arr)
    p2 = bootstrap_pvalue(arr)
    assert p1 == p2, f"bootstrap_pvalue not reproducible: {p1} != {p2}"


def test_bootstrap_pvalue_range() -> None:
    """bootstrap_pvalue returns a value in [0.0, 1.0]."""
    arr = np.array([1, -1, 1, 1, -1, 1, 1, -1, 1, -1, 1, 1], dtype=float)
    p = bootstrap_pvalue(arr)
    assert 0.0 <= p <= 1.0, f"p={p} out of range"


def test_bootstrap_pvalue_underpowered() -> None:
    """len(arr) < 4 → conservative p == 1.0."""
    arr = np.array([1, -1, 1], dtype=float)
    assert bootstrap_pvalue(arr) == 1.0


def test_bootstrap_pvalue_observed_zero_returns_one() -> None:
    """Observed mean exactly 0 → p == 1.0 (no detectable signal)."""
    arr = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0], dtype=float)
    # mean = 0.0 → return 1.0 conservatively
    assert bootstrap_pvalue(arr) == 1.0


def test_bootstrap_pvalue_positive_observed_returns_low_p() -> None:
    """Positive-mean array: H0-centered bootstrap should yield p << 0.5.
    Regression test for WR-01 (arr_h0 = arr - observed centering).
    """
    rng = np.random.default_rng(42)
    arr = np.concatenate([rng.normal(0.1, 0.05, 50), rng.normal(-0.1, 0.05, 20)])
    p = bootstrap_pvalue(arr, seed=42)
    assert p < 0.5, f"Expected p < 0.5 for positive-mean arr, got {p}"


def test_bootstrap_pvalue_negative_observed_returns_low_p() -> None:
    """Negative-mean array: two-sided H0-centered bootstrap should yield p << 0.5.
    Regression test for WR-01 (symmetric with positive case).
    """
    rng = np.random.default_rng(42)
    arr = np.concatenate([rng.normal(-0.1, 0.05, 50), rng.normal(0.1, 0.05, 20)])
    p = bootstrap_pvalue(arr, seed=42)
    assert p < 0.5, f"Expected p < 0.5 for negative-mean arr, got {p}"


# ---------------------------------------------------------------------------
# compute_vif_bar
# ---------------------------------------------------------------------------


def test_compute_vif_bar_clamp_lower() -> None:
    """VIF all zero/invalid → m_eff == 1.0 (lower clamp)."""
    cells = {"ECB": [{"pool_cell_id": "0-60m", "vif": 0.0}]}
    vif_bar, m_eff = compute_vif_bar(cells)
    assert m_eff == 1.0, f"expected m_eff=1.0 for zero VIF, got {m_eff}"


def test_compute_vif_bar_clamp_upper() -> None:
    """VIF all 1.0 → m_eff == 72.0 (at upper bound M_HYPOTHESES)."""
    cells = {
        "ECB": [
            {"pool_cell_id": "0-60m", "vif": 1.0},
            {"pool_cell_id": "60-120m", "vif": 1.0},
        ],
        "FOMC": [
            {"pool_cell_id": "0-60m", "vif": 1.0},
            {"pool_cell_id": "60-120m", "vif": 1.0},
        ],
        "NFP": [
            {"pool_cell_id": "0-60m", "vif": 1.0},
            {"pool_cell_id": "60-120m", "vif": 1.0},
        ],
    }
    vif_bar, m_eff = compute_vif_bar(cells)
    assert m_eff == float(M_HYPOTHESES), f"expected m_eff={M_HYPOTHESES}, got {m_eff}"


def test_compute_vif_bar_nominal() -> None:
    """VIF_bar ≈ 1.021 → m_eff ≈ 72/1.021 ≈ 70.52 (rel tol 1e-4)."""
    cells = _make_regime_breakdown()["cells_by_event_pooled"]
    vif_bar, m_eff = compute_vif_bar(cells)
    expected_m_eff = 72.0 / 1.021
    assert abs(m_eff - expected_m_eff) / expected_m_eff < 1e-4, (
        f"m_eff={m_eff}, expected≈{expected_m_eff}"
    )


def test_m_eff_clamp() -> None:
    """m_eff is always in [1.0, 72.0]."""
    for vif_val in [0.0, 0.5, 1.0, 1.021, 5.0, 100.0]:
        cells = {"ECB": [{"pool_cell_id": "0-60m", "vif": vif_val}]}
        _, m_eff = compute_vif_bar(cells)
        assert 1.0 <= m_eff <= float(M_HYPOTHESES), (
            f"m_eff={m_eff} out of [1.0, {M_HYPOTHESES}] for vif={vif_val}"
        )


# ---------------------------------------------------------------------------
# apply_bonferroni_holm
# ---------------------------------------------------------------------------


def test_apply_bonferroni_holm_literal_method() -> None:
    """apply_bonferroni_holm output dict has method == 'Bonferroni-Holm' (SCOPE-03)."""
    p_vals = [0.01] * 72
    result = apply_bonferroni_holm(p_vals)
    assert result["method"] == "Bonferroni-Holm", (
        f"method literal violated: {result['method']}"
    )


def test_apply_bonferroni_holm_length() -> None:
    """p_adj length == 72 and m == 72."""
    p_vals = list(np.linspace(0.001, 0.999, 72))
    result = apply_bonferroni_holm(p_vals)
    assert len(result["p_adj"]) == 72, f"len(p_adj)={len(result['p_adj'])}"
    assert result["m"] == 72, f"m={result['m']}"


def test_apply_bonferroni_holm_monotonic() -> None:
    """Holm step-down property: p_adj in argsort-of-raw order is non-decreasing."""
    rng = np.random.default_rng(0)
    p_raw = list(rng.uniform(0.0, 1.0, 72))
    result = apply_bonferroni_holm(p_raw)
    p_adj = np.array(result["p_adj"])
    order = np.argsort(p_raw)
    p_adj_sorted = p_adj[order]
    # Non-decreasing (with floating-point tolerance)
    diffs = np.diff(p_adj_sorted)
    assert np.all(diffs >= -1e-12), (
        f"p_adj not monotone in sorted order: min diff={diffs.min()}"
    )


# ---------------------------------------------------------------------------
# build_canonical_pvalue_array
# ---------------------------------------------------------------------------


def test_build_canonical_pvalue_array_length() -> None:
    """build_canonical_pvalue_array returns p_values len==72 and slot_keys len==72."""
    df = _make_slot_df()
    result = build_canonical_pvalue_array(df)
    assert "p_values" in result, "missing 'p_values' key"
    assert "slot_keys" in result, "missing 'slot_keys' key"
    assert len(result["p_values"]) == 72, f"len(p_values)={len(result['p_values'])}"
    assert len(result["slot_keys"]) == 72, f"len(slot_keys)={len(result['slot_keys'])}"


def test_build_canonical_pvalue_array_ordering() -> None:
    """slot_keys follow canonical order: event → l0_cell → pair_slot (lexicographic)."""
    df = _make_slot_df()
    result = build_canonical_pvalue_array(df)
    slot_keys = [tuple(k) for k in result["slot_keys"]]

    # Build the expected canonical ordering explicitly
    real_pairs_sorted = sorted(_PAIRS)  # ["EURJPY", "EURUSD", "USDJPY"]
    pair_slots = real_pairs_sorted + [PADDED_PAIR_SENTINEL]
    expected = [
        (event, l0_cell, pair)
        for event in sorted(_EVENTS)
        for l0_cell in sorted(_L0_CELLS)
        for pair in pair_slots
    ]
    assert len(expected) == 72
    assert slot_keys == expected, (
        f"First mismatch at index "
        f"{next(i for i, (a, b) in enumerate(zip(slot_keys, expected)) if a != b)}"
    )


def test_padded_pair_pvalue_one() -> None:
    """Slots with PADDED_PAIR_SENTINEL have p_values == 1.0 (Q2 A padding)."""
    df = _make_slot_df()
    result = build_canonical_pvalue_array(df)
    for i, key in enumerate(result["slot_keys"]):
        k = tuple(key)
        if k[2] == PADDED_PAIR_SENTINEL:
            assert result["p_values"][i] == 1.0, (
                f"padded slot {k} has p={result['p_values'][i]} != 1.0"
            )


# ---------------------------------------------------------------------------
# run_fwer_only — integration smoke (uses tmp_path fixtures)
# ---------------------------------------------------------------------------


def test_fwer_correction_field(tmp_path: Path) -> None:
    """run_fwer_only returns dict with all required keys."""
    rb_path = tmp_path / "regime_breakdown.json"
    rb_path.write_text(json.dumps(_make_regime_breakdown()))

    sl_path = tmp_path / "slot_labels.parquet"
    _make_slot_df().to_parquet(sl_path, index=False)

    # Use sealed spec path for config_drift check (mocked to pass)
    sealed_path = (
        Path(__file__).parents[2]
        / ".planning/milestones/v4.8-phases/79-scope-lock-pre-registration/regime_cuts.json"
    )
    good_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=f"{REGIME_COMMIT} docs(79): seal\n", stderr=""
    )
    with patch("subprocess.run", return_value=good_result):
        out = run_fwer_only(
            str(rb_path), str(sl_path), regime_cuts_path=str(sealed_path)
        )

    required_keys = {
        "method",
        "alpha",
        "m",
        "m_eff",
        "VIF_bar",
        "p_adj",
        "reject",
        "spec_source",
        "regime_commit",
        "slot_keys",
    }
    missing = required_keys - set(out.keys())
    assert not missing, f"Missing keys: {missing}"


def test_fwer_correction_field_values(tmp_path: Path) -> None:
    """run_fwer_only output has correct literal field values (SCOPE-03 anchors)."""
    rb_path = tmp_path / "regime_breakdown.json"
    rb_path.write_text(json.dumps(_make_regime_breakdown()))

    sl_path = tmp_path / "slot_labels.parquet"
    _make_slot_df().to_parquet(sl_path, index=False)

    sealed_path = (
        Path(__file__).parents[2]
        / ".planning/milestones/v4.8-phases/79-scope-lock-pre-registration/regime_cuts.json"
    )
    good_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=f"{REGIME_COMMIT} docs(79): seal\n", stderr=""
    )
    with patch("subprocess.run", return_value=good_result):
        out = run_fwer_only(
            str(rb_path), str(sl_path), regime_cuts_path=str(sealed_path)
        )

    assert out["method"] == "Bonferroni-Holm", f"method={out['method']}"
    assert out["alpha"] == 0.05, f"alpha={out['alpha']}"
    assert out["m"] == 72, f"m={out['m']}"
    assert out["regime_commit"] == "90bf4b2", f"regime_commit={out['regime_commit']}"
    assert str(out["spec_source"]).endswith("fwer_correction_spec.md"), (
        f"spec_source={out['spec_source']}"
    )


# ===========================================================================
# Plan 02 — Wave 2 RED tests (VERDICT-01 / VERDICT-04)
# ===========================================================================

# ---------------------------------------------------------------------------
# Additional helpers for Plan 02
# ---------------------------------------------------------------------------

_POOL_CELLS = ("0-60m", "60-120m")


def _make_slot_df_with_directions(
    simpson_reverse_events: list | None = None,
) -> pd.DataFrame:
    """Synthetic slot_labels df with direction column.

    For events in simpson_reverse_events: within pool cells long is majority,
    but when aggregated at event level short dominates (Simpson reversal).
    """
    rows = []
    reverse_set = set(simpson_reverse_events or [])
    for event in _EVENTS:
        for cell in _L0_CELLS:
            pcid = cell.split("_x_")[0]
            for pair in _PAIRS:
                if event in reverse_set:
                    # 3 long rows within pool cell (majority long per cell)
                    # BUT we also add 8 short rows flagged as an additional
                    # synthetic "overflow" event group outside any cell —
                    # we model this by varying long/short one-hot differently.
                    # Long: long=1, short=0; Short: long=0, short=1
                    for _ in range(3):
                        rows.append({
                            "event_type": event,
                            "cell_id": cell,
                            "pair": pair,
                            "long": 1, "neutral": 0, "short": 0,
                            "direction": "long",
                        })
                    # Extra short rows so pooled aggregate flips
                    for _ in range(12):
                        rows.append({
                            "event_type": event,
                            "cell_id": cell,
                            "pair": pair,
                            "long": 0, "neutral": 0, "short": 1,
                            "direction": "short",
                        })
                else:
                    # Normal: long majority in cells AND pooled
                    for _ in range(7):
                        rows.append({
                            "event_type": event,
                            "cell_id": cell,
                            "pair": pair,
                            "long": 1, "neutral": 0, "short": 0,
                            "direction": "long",
                        })
                    for _ in range(3):
                        rows.append({
                            "event_type": event,
                            "cell_id": cell,
                            "pair": pair,
                            "long": 0, "neutral": 0, "short": 1,
                            "direction": "short",
                        })
    return pd.DataFrame(rows)


def _make_regime_breakdown_with_stats(pass_configs: dict | None = None) -> dict:
    """Build synthetic regime_breakdown with explicit stats per pool cell.

    pass_configs: dict[event][pool_cell_id] = {"n_eff": float, "ci": [lo, hi],
                                                "vif": float, "rho_bar": float}
    Defaults to PASS-friendly values for all cells.
    NOTE: sign_ratio is NOT included — must come from compute_pool_sign_ratios.
    """
    default_cfg = {"n_eff": 15.0, "ci": [0.10, 0.50], "vif": 1.021, "rho_bar": 0.70}
    cells_by_event_pooled = {}
    for event in _EVENTS:
        cells_by_event_pooled[event] = []
        for pcid in _POOL_CELLS:
            cfg = (pass_configs or {}).get(event, {}).get(pcid, default_cfg)
            cells_by_event_pooled[event].append({
                "pool_cell_id": pcid,
                "vif": cfg.get("vif", 1.021),
                "rho_bar": cfg.get("rho_bar", 0.70),
                "n_eff_predicted": cfg.get("n_eff", 15.0),
                "bootstrap_ci_95": cfg.get("ci", [0.10, 0.50]),
                # sign_ratio は意図的に省略 (Blocker 2 防止)
            })
    return {
        "wave1_decision": "proceed",
        "wave1_fallback_level": 1,
        "cells_by_event_pooled": cells_by_event_pooled,
    }


def _make_pool_signs(mapping: dict) -> dict:
    """Build pool_signs dict[event][pool_cell_id] = float directly."""
    return {ev: dict(pcids) for ev, pcids in mapping.items()}


# ---------------------------------------------------------------------------
# compute_pool_sign_ratios
# ---------------------------------------------------------------------------


def test_compute_pool_sign_ratios_basic() -> None:
    """compute_pool_sign_ratios returns 2-level dict with correct (direction=='long').mean."""
    compute_pool_sign_ratios = verdict_fwer.compute_pool_sign_ratios
    df = _make_slot_df_with_directions()
    result = compute_pool_sign_ratios(df)
    # Structure: {event: {pool_cell_id: float}}
    for event in _EVENTS:
        assert event in result, f"event {event} missing"
        for pcid in _POOL_CELLS:
            assert pcid in result[event], f"pcid {pcid} missing under {event}"
            assert isinstance(result[event][pcid], float), "value must be float"
    # Normal events: direction is long 7/10 = 0.7
    ratio = result["ECB"]["0-60m"]
    assert 0.0 <= ratio <= 1.0, f"ratio out of range: {ratio}"


def test_compute_pool_sign_ratios_no_direction_column() -> None:
    """Falls back to long one-hot mean when direction column is absent."""
    compute_pool_sign_ratios = verdict_fwer.compute_pool_sign_ratios
    df = _make_slot_df()  # no direction column, long=1 for all rows
    result = compute_pool_sign_ratios(df)
    for event in _EVENTS:
        for pcid in _POOL_CELLS:
            val = result[event][pcid]
            # long=1 for all rows → mean should be 1.0
            assert abs(val - 1.0) < 1e-9, f"expected 1.0, got {val}"


# ---------------------------------------------------------------------------
# classify_fail_candidate
# ---------------------------------------------------------------------------


def test_classify_fail_candidate_sampling_noise() -> None:
    classify_fail_candidate = verdict_fwer.classify_fail_candidate
    cell = {"n_eff_predicted": 2.1, "bootstrap_ci_95": [-0.2, 0.3]}
    assert classify_fail_candidate(cell, sign_ratio=0.5) == "sampling_noise"


def test_classify_fail_candidate_structural_ci() -> None:
    classify_fail_candidate = verdict_fwer.classify_fail_candidate
    cell = {"n_eff_predicted": 10.0, "bootstrap_ci_95": [-0.1, 0.4]}
    assert classify_fail_candidate(cell, sign_ratio=0.6) == "structural"


def test_classify_fail_candidate_structural_signratio() -> None:
    """sign_ratio == 0.0 → structural (D-12 字義通り sr <= 0.0)."""
    classify_fail_candidate = verdict_fwer.classify_fail_candidate
    cell = {"n_eff_predicted": 10.0, "bootstrap_ci_95": [0.05, 0.4]}
    assert classify_fail_candidate(cell, sign_ratio=0.0) == "structural"


def test_classify_fail_candidate_structural_signratio_negative() -> None:
    """Negative sign_ratio → structural (sr <= 0.0 includes negative)."""
    classify_fail_candidate = verdict_fwer.classify_fail_candidate
    cell = {"n_eff_predicted": 10.0, "bootstrap_ci_95": [0.05, 0.4]}
    assert classify_fail_candidate(cell, sign_ratio=-0.3) == "structural"


def test_classify_fail_candidate_bug_fallback() -> None:
    """All conditions clear but FAIL cell → bug fallback (direct call bypasses PASS guard)."""
    classify_fail_candidate = verdict_fwer.classify_fail_candidate
    # n_eff OK, CI fully positive, sr > 0.0 — direct call returns "bug"
    cell = {"n_eff_predicted": 10.0, "bootstrap_ci_95": [0.1, 0.3]}
    assert classify_fail_candidate(cell, sign_ratio=0.01) == "bug"


# ---------------------------------------------------------------------------
# build_cell_verdicts
# ---------------------------------------------------------------------------


def test_pass_cell_no_candidate() -> None:
    """PASS cell: verdict==PASS AND candidate is None."""
    build_cell_verdicts = verdict_fwer.build_cell_verdicts
    rd = _make_regime_breakdown_with_stats()
    pool_signs = _make_pool_signs({
        ev: {"0-60m": 0.7, "60-120m": 0.6} for ev in _EVENTS
    })
    result = build_cell_verdicts(rd["cells_by_event_pooled"], pool_signs)
    for ev in _EVENTS:
        for pcid in _POOL_CELLS:
            entry = result[ev][pcid]
            assert entry["verdict"] == "PASS", f"{ev}/{pcid} should be PASS"
            assert entry["candidate"] is None, f"{ev}/{pcid} candidate should be None"


def test_pass_cell_borderline_sr_exactly_zero() -> None:
    """sr==0.0 is NOT PASS (strict >0.0 required by D-12)."""
    build_cell_verdicts = verdict_fwer.build_cell_verdicts
    rd = _make_regime_breakdown_with_stats()
    pool_signs = _make_pool_signs({
        ev: {"0-60m": 0.0, "60-120m": 0.0} for ev in _EVENTS
    })
    result = build_cell_verdicts(rd["cells_by_event_pooled"], pool_signs)
    for ev in _EVENTS:
        entry = result[ev]["0-60m"]
        assert entry["verdict"] == "FAIL", "sr=0.0 must FAIL"
        assert entry["candidate"] == "structural", "sr=0.0 → structural"


def test_build_cell_verdicts_l1_grain() -> None:
    """build_cell_verdicts returns 6 entries (3 events × 2 pool_cell_ids)."""
    build_cell_verdicts = verdict_fwer.build_cell_verdicts
    rd = _make_regime_breakdown_with_stats()
    pool_signs = _make_pool_signs({
        ev: {"0-60m": 0.7, "60-120m": 0.6} for ev in _EVENTS
    })
    result = build_cell_verdicts(rd["cells_by_event_pooled"], pool_signs)
    total = sum(len(cells) for cells in result.values())
    assert total == 6, f"expected 6 L1 entries, got {total}"
    for ev in _EVENTS:
        for pcid in _POOL_CELLS:
            entry = result[ev][pcid]
            for key in ("verdict", "candidate", "n_eff_predicted", "sign_ratio", "ci_95", "vif", "rho_bar"):
                assert key in entry, f"missing key {key} in {ev}/{pcid}"


def test_build_cell_verdicts_key_structure() -> None:
    """cell_verdicts['ECB']['0-60m'] is accessible (2-level dict)."""
    build_cell_verdicts = verdict_fwer.build_cell_verdicts
    rd = _make_regime_breakdown_with_stats()
    pool_signs = _make_pool_signs({ev: {"0-60m": 0.7, "60-120m": 0.6} for ev in _EVENTS})
    result = build_cell_verdicts(rd["cells_by_event_pooled"], pool_signs)
    entry = result["ECB"]["0-60m"]
    assert "verdict" in entry


def test_build_cell_verdicts_reads_sign_ratio_from_pool_signs() -> None:
    """sign_ratio is taken from pool_signs, not from cells_by_event_pooled."""
    build_cell_verdicts = verdict_fwer.build_cell_verdicts
    # cells_by_event_pooled has no sign_ratio field
    rd = _make_regime_breakdown_with_stats()
    pool_signs = _make_pool_signs({ev: {"0-60m": 0.7, "60-120m": 0.7} for ev in _EVENTS})
    result = build_cell_verdicts(rd["cells_by_event_pooled"], pool_signs)
    # sr=0.7 > 0.0 AND n_eff=15>=4 AND ci_lo=0.1>0 → PASS
    assert result["ECB"]["0-60m"]["verdict"] == "PASS"
    assert abs(result["ECB"]["0-60m"]["sign_ratio"] - 0.7) < 1e-9


# ---------------------------------------------------------------------------
# detect_simpson_paradox
# ---------------------------------------------------------------------------


def test_simpson_negative_flag() -> None:
    """Both pool cells long majority AND pooled long > short → detected==False."""
    detect_simpson_paradox = verdict_fwer.detect_simpson_paradox
    rd = _make_regime_breakdown_with_stats()
    pooled = rd["cells_by_event_pooled"]
    pool_signs = _make_pool_signs({ev: {"0-60m": 0.7, "60-120m": 0.6} for ev in _EVENTS})
    slot_df = _make_slot_df_with_directions()  # normal: long majority at all levels
    result = detect_simpson_paradox(pooled, pool_signs, slot_df)
    assert result["detected"] is False
    assert result["affected_events"] == []
    assert result["cell_summary"] == {}


def test_simpson_detected_reversal() -> None:
    """FOMC cells majority-long but pooled short → detected==True, FOMC in affected."""
    detect_simpson_paradox = verdict_fwer.detect_simpson_paradox
    rd = _make_regime_breakdown_with_stats()
    pooled = rd["cells_by_event_pooled"]
    # pool_signs: FOMC cells are both long-majority (sr > 0.5)
    pool_signs = _make_pool_signs({
        "ECB": {"0-60m": 0.7, "60-120m": 0.6},
        "FOMC": {"0-60m": 0.6, "60-120m": 0.7},  # majority positive
        "NFP": {"0-60m": 0.7, "60-120m": 0.6},
    })
    # slot_df: FOMC pooled flips to short (many short rows)
    slot_df = _make_slot_df_with_directions(simpson_reverse_events=["FOMC"])
    result = detect_simpson_paradox(pooled, pool_signs, slot_df)
    assert result["detected"] is True
    assert "FOMC" in result["affected_events"]
    assert result["cell_summary"]["FOMC"]["pooled_sign_positive"] is False


def test_simpson_event_independence() -> None:
    """Only ECB reversed → affected_events == ['ECB'], FOMC/NFP not included."""
    detect_simpson_paradox = verdict_fwer.detect_simpson_paradox
    rd = _make_regime_breakdown_with_stats()
    pooled = rd["cells_by_event_pooled"]
    pool_signs = _make_pool_signs({
        "ECB": {"0-60m": 0.6, "60-120m": 0.7},  # majority positive
        "FOMC": {"0-60m": 0.7, "60-120m": 0.6},
        "NFP": {"0-60m": 0.7, "60-120m": 0.6},
    })
    slot_df = _make_slot_df_with_directions(simpson_reverse_events=["ECB"])
    result = detect_simpson_paradox(pooled, pool_signs, slot_df)
    assert "ECB" in result["affected_events"]
    assert "FOMC" not in result["affected_events"]
    assert "NFP" not in result["affected_events"]


def test_simpson_signature_requires_slot_df_and_pool_signs() -> None:
    """detect_simpson_paradox(cells_by_event_pooled) alone raises TypeError."""
    detect_simpson_paradox = verdict_fwer.detect_simpson_paradox
    rd = _make_regime_breakdown_with_stats()
    with pytest.raises(TypeError):
        detect_simpson_paradox(rd["cells_by_event_pooled"])


def test_detect_simpson_pooled_sr_matches_pool_sign_ratios() -> None:
    """detect_simpson_paradox pooled SR must use same denominator as compute_pool_sign_ratios.
    Regression test for WR-02: long/(long+short) vs (direction=='long').mean().
    With neutral rows, these differ; after fix they must match.
    """
    import pandas as pd

    # 4 long, 2 short, 2 neutral rows for ECB
    rows = (
        [{"event_type": "ECB", "direction": "long", "cell_id": "0-60m", "long": 1, "short": 0}] * 4
        + [{"event_type": "ECB", "direction": "short", "cell_id": "0-60m", "long": 0, "short": 1}] * 2
        + [{"event_type": "ECB", "direction": "neutral", "cell_id": "0-60m", "long": 0, "short": 0}] * 2
    )
    slot_df = pd.DataFrame(rows)
    # Expected pooled_sr = 4/8 = 0.5 (direction=="long").mean()
    # Bug would compute: long_mean=0.5, short_mean=0.25 → 0.5/(0.5+0.25) = 0.667 → pooled_sign_positive=True
    # After fix: pooled_sr=0.5 → pooled_sign_positive=False
    # cell majority: pool_sign for "0-60m" = 0.5, so s > 0.5 is False → cell_majority_positive=False
    # With fix: both False → no inversion → detected=False
    pooled = {"ECB": [{"pool_cell_id": "0-60m"}]}
    pool_signs = {"ECB": {"0-60m": 0.5}}
    result = verdict_fwer.detect_simpson_paradox(pooled, pool_signs, slot_df)
    assert result["detected"] is False, (
        f"With pooled_sr=0.5 (unified denominator) and cell_majority_positive=False, "
        f"no inversion expected. Got: {result}"
    )


# ---------------------------------------------------------------------------
# run_verdict_fwer — integration tests
# ---------------------------------------------------------------------------


def _write_tmp_inputs(tmp_path: Path, *, reverse_events: list | None = None) -> tuple:
    """Write regime_breakdown.json + slot_labels.parquet to tmp_path."""
    rd = _make_regime_breakdown_with_stats()
    rb_path = tmp_path / "regime_breakdown.json"
    rb_path.write_text(json.dumps(rd))
    slot_df = _make_slot_df_with_directions(simpson_reverse_events=reverse_events)
    sl_path = tmp_path / "slot_labels.parquet"
    slot_df.to_parquet(sl_path, index=False)
    rc_path = tmp_path / "regime_cuts.json"
    rc_path.write_text(json.dumps({"low_upper": 1e-8, "high_lower": 3e-8}))
    out_path = tmp_path / "report.json"
    return rb_path, sl_path, rc_path, out_path


def _mock_git_pass():
    return {"subprocess.run": lambda *a, **kw: subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout=f"{REGIME_COMMIT} docs(79): seal\n", stderr=""
    )}


def test_report_json_top_level_keys(tmp_path: Path, monkeypatch) -> None:
    """run_verdict_fwer returns dict with required top-level keys."""
    run_verdict_fwer = verdict_fwer.run_verdict_fwer
    monkeypatch.setattr(verdict_fwer, "check_config_drift", lambda p: None)
    rb, sl, rc, out = _write_tmp_inputs(tmp_path)
    result = run_verdict_fwer(str(rb), str(sl), regime_cuts_path=str(rc), out_path=str(out))
    expected_keys = {"schema_version", "generated_at", "provenance", "fwer_correction",
                     "cell_verdicts", "simpson_paradox"}
    assert set(result.keys()) == expected_keys, f"keys mismatch: {set(result.keys())}"


def test_report_json_written_to_disk(tmp_path: Path, monkeypatch) -> None:
    """report.json is written to disk and is valid JSON with schema_version."""
    run_verdict_fwer = verdict_fwer.run_verdict_fwer
    monkeypatch.setattr(verdict_fwer, "check_config_drift", lambda p: None)
    rb, sl, rc, out = _write_tmp_inputs(tmp_path)
    run_verdict_fwer(str(rb), str(sl), regime_cuts_path=str(rc), out_path=str(out))
    assert out.exists(), "report.json not written"
    parsed = json.loads(out.read_text())
    assert parsed["schema_version"] == "v4.8-regime-v2-verdict"


def test_report_json_provenance_fields(tmp_path: Path, monkeypatch) -> None:
    """provenance contains required fields with correct commit hashes."""
    run_verdict_fwer = verdict_fwer.run_verdict_fwer
    monkeypatch.setattr(verdict_fwer, "check_config_drift", lambda p: None)
    rb, sl, rc, out = _write_tmp_inputs(tmp_path)
    run_verdict_fwer(str(rb), str(sl), regime_cuts_path=str(rc), out_path=str(out))
    parsed = json.loads(out.read_text())
    prov = parsed["provenance"]
    required = {"regime_commit", "threshold_commit", "input_regime_breakdown",
                "input_slot_labels", "generated_at"}
    assert required.issubset(set(prov.keys())), f"missing: {required - set(prov.keys())}"
    assert prov["regime_commit"] == "90bf4b2"
    assert prov["threshold_commit"] == "6527cbc"


def test_run_verdict_fwer_includes_fwer(tmp_path: Path, monkeypatch) -> None:
    """fwer_correction.m == 72 and method == 'Bonferroni-Holm'."""
    run_verdict_fwer = verdict_fwer.run_verdict_fwer
    monkeypatch.setattr(verdict_fwer, "check_config_drift", lambda p: None)
    rb, sl, rc, out = _write_tmp_inputs(tmp_path)
    result = run_verdict_fwer(str(rb), str(sl), regime_cuts_path=str(rc), out_path=str(out))
    fwer = result["fwer_correction"]
    assert fwer["m"] == 72
    assert fwer["method"] == "Bonferroni-Holm"


def test_run_verdict_fwer_cell_verdicts_count(tmp_path: Path, monkeypatch) -> None:
    """cell_verdicts total == 6 (L1 grain: 2 pool × 3 events)."""
    run_verdict_fwer = verdict_fwer.run_verdict_fwer
    monkeypatch.setattr(verdict_fwer, "check_config_drift", lambda p: None)
    rb, sl, rc, out = _write_tmp_inputs(tmp_path)
    result = run_verdict_fwer(str(rb), str(sl), regime_cuts_path=str(rc), out_path=str(out))
    total = sum(len(cells) for cells in result["cell_verdicts"].values())
    assert total == 6, f"expected 6 verdicts, got {total}"
