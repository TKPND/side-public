"""Phase 82 Plan 01 Task 1+2: wave1_power.py unit tests.

TDD RED phase — all tests reference wave1_power which does not yet exist.

Tests cover:
- CellStats dataclass fields
- load_slot_labels() schema validation
- compute_cell_stats() n_eff formula
- group_by_event_cell() groupby behavior
- group_by_event_duration() / group_by_event_liquidity() fallback functions
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

# -- import via importlib (scripts/v4.8 is not a valid Python package path) --
_SPEC = importlib.util.spec_from_file_location(
    "wave1_power", Path(__file__).parent / "wave1_power.py"
)
wave1_power = importlib.util.module_from_spec(_SPEC)
sys.modules["wave1_power"] = wave1_power
_SPEC.loader.exec_module(wave1_power)

CellStats = wave1_power.CellStats
load_slot_labels = wave1_power.load_slot_labels
compute_cell_stats = wave1_power.compute_cell_stats
group_by_event_cell = wave1_power.group_by_event_cell
group_by_event_duration = wave1_power.group_by_event_duration
group_by_event_liquidity = wave1_power.group_by_event_liquidity
GATE_K = wave1_power.GATE_K


# ---------------------------------------------------------------------------
# Task 1: CellStats dataclass
# ---------------------------------------------------------------------------


def test_cellstats_fields() -> None:
    """CellStats has rho_bar, vif, n_eff_predicted, n_nominal fields."""
    cs = CellStats(
        event_type="ECB",
        cell_id="0-60m_x_HIGH",
        n_nominal=10,
        rho_bar=0.3,
        vif=1.5,
        n_eff_predicted=4.667,
        fallback_level=0,
    )
    assert hasattr(cs, "rho_bar")
    assert hasattr(cs, "vif")
    assert hasattr(cs, "n_eff_predicted")
    assert hasattr(cs, "n_nominal")
    assert hasattr(cs, "fallback_level")
    assert cs.event_type == "ECB"
    assert cs.cell_id == "0-60m_x_HIGH"


def test_gate_k_constant() -> None:
    """GATE_K must be 4 (pre-registered power_budget.json D-29, T-82-02)."""
    assert GATE_K == 4


# ---------------------------------------------------------------------------
# Task 1: load_slot_labels()
# ---------------------------------------------------------------------------


def _make_slot_df(**kwargs: object) -> pd.DataFrame:
    """Helper: build a minimal valid slot_labels DataFrame."""
    base = {
        "event_type": ["ECB", "ECB"],
        "cell_id": ["0-60m_x_HIGH", "0-60m_x_HIGH"],
        "duration_bucket": ["0-60m", "0-60m"],
        "liquidity_regime": ["HIGH", "HIGH"],
        "pair": ["EURUSD", "GBPUSD"],
        "long": [10, 12],
        "neutral": [5, 4],
        "short": [3, 2],
    }
    base.update(kwargs)
    return pd.DataFrame(base)


def test_load_slot_labels_returns_dataframe(tmp_path: Path) -> None:
    """load_slot_labels() returns a DataFrame."""
    df = _make_slot_df()
    p = tmp_path / "slot_labels.parquet"
    df.to_parquet(p)
    result = load_slot_labels(p)
    assert isinstance(result, pd.DataFrame)


def test_load_slot_labels_has_required_columns(tmp_path: Path) -> None:
    """Returned DataFrame has [event_type, cell_id, long, neutral, short, pair]."""
    df = _make_slot_df()
    p = tmp_path / "slot_labels.parquet"
    df.to_parquet(p)
    result = load_slot_labels(p)
    for col in ["event_type", "cell_id", "long", "neutral", "short", "pair"]:
        assert col in result.columns, f"missing column: {col}"


def test_load_slot_labels_cell_id_format(tmp_path: Path) -> None:
    """cell_id values follow '{duration_bucket}_x_{liquidity_regime}' format."""
    df = _make_slot_df(cell_id=["0-60m_x_HIGH", "60-120m_x_MID"])
    p = tmp_path / "slot_labels.parquet"
    df.to_parquet(p)
    result = load_slot_labels(p)
    for cid in result["cell_id"]:
        assert "_x_" in str(cid), f"cell_id missing '_x_': {cid}"


def test_load_slot_labels_missing_column_raises(tmp_path: Path) -> None:
    """Missing required column raises ValueError."""
    df = _make_slot_df()
    df = df.drop(columns=["pair"])
    p = tmp_path / "slot_labels.parquet"
    df.to_parquet(p)
    with pytest.raises(ValueError, match="missing columns"):
        load_slot_labels(p)


# ---------------------------------------------------------------------------
# Task 1: compute_cell_stats() — n_eff formula
# ---------------------------------------------------------------------------


def _make_cell_df(
    pairs: list[str], longs: list[int], shorts: list[int]
) -> pd.DataFrame:
    """Build a cell-level DataFrame (single cell)."""
    n = len(pairs)
    return pd.DataFrame(
        {
            "event_type": ["ECB"] * n,
            "cell_id": ["0-60m_x_HIGH"] * n,
            "pair": pairs,
            "long": longs,
            "neutral": [5] * n,
            "short": shorts,
        }
    )


def test_compute_cell_stats_returns_cellstats() -> None:
    """compute_cell_stats() returns a CellStats instance."""
    df = _make_cell_df(["EURUSD", "GBPUSD"], [10, 12], [3, 2])
    result = compute_cell_stats(df, event_type="ECB", cell_id="0-60m_x_HIGH")
    assert isinstance(result, CellStats)


def test_compute_cell_stats_n_nominal() -> None:
    """n_nominal = len(df)."""
    df = _make_cell_df(["EURUSD", "GBPUSD", "USDJPY"], [10, 12, 8], [3, 2, 4])
    cs = compute_cell_stats(df, event_type="ECB", cell_id="0-60m_x_HIGH")
    assert cs.n_nominal == 3


def test_compute_cell_stats_n_eff_nonnegative() -> None:
    """n_eff_predicted >= 0.0 always."""
    df = _make_cell_df(["EURUSD", "GBPUSD"], [10, 12], [3, 2])
    cs = compute_cell_stats(df, event_type="ECB", cell_id="0-60m_x_HIGH")
    assert cs.n_eff_predicted >= 0.0


def test_compute_cell_stats_formula() -> None:
    """n_eff_predicted = (1 - rho_bar) * n_nominal / vif (D-29)."""
    df = _make_cell_df(["EURUSD", "GBPUSD"], [10, 12], [3, 2])
    cs = compute_cell_stats(df, event_type="ECB", cell_id="0-60m_x_HIGH")
    expected = (1.0 - cs.rho_bar) * cs.n_nominal / cs.vif if cs.vif > 0 else 0.0
    assert abs(cs.n_eff_predicted - max(0.0, expected)) < 1e-9


def test_compute_cell_stats_vif_inf_gives_zero_n_eff() -> None:
    """VIF=inf -> n_eff_predicted = 0.0 (T-82-03 kill-switch always fires)."""
    # All columns identical → VIF likely inf or near-inf
    df = pd.DataFrame(
        {
            "event_type": ["ECB"] * 10,
            "cell_id": ["0-60m_x_HIGH"] * 10,
            "pair": [f"P{i}" for i in range(10)],
            "long": [5] * 10,
            "neutral": [5] * 10,
            "short": [5] * 10,
        }
    )
    cs = compute_cell_stats(df, event_type="ECB", cell_id="0-60m_x_HIGH")
    assert cs.n_eff_predicted == 0.0


def test_compute_cell_stats_fallback_level() -> None:
    """fallback_level is passed through to CellStats."""
    df = _make_cell_df(["EURUSD"], [10], [3])
    cs = compute_cell_stats(df, event_type="ECB", cell_id="0-60m", fallback_level=1)
    assert cs.fallback_level == 1


# ---------------------------------------------------------------------------
# Task 2: group_by_event_cell()
# ---------------------------------------------------------------------------


def _make_multi_cell_df() -> pd.DataFrame:
    """Synthetic slot_labels with 2 cells."""
    return pd.DataFrame(
        {
            "event_type": ["ECB"] * 6,
            "cell_id": ["0-60m_x_HIGH"] * 3 + ["0-60m_x_MID"] * 3,
            "duration_bucket": ["0-60m"] * 6,
            "liquidity_regime": ["HIGH"] * 3 + ["MID"] * 3,
            "pair": ["EURUSD", "GBPUSD", "USDJPY"] * 2,
            "long": [10, 12, 8, 15, 11, 9],
            "neutral": [5, 4, 6, 3, 7, 5],
            "short": [3, 2, 4, 1, 3, 4],
        }
    )


def test_group_by_event_cell_returns_list() -> None:
    """group_by_event_cell() returns list[CellStats]."""
    df = _make_multi_cell_df()
    result = group_by_event_cell(df)
    assert isinstance(result, list)
    assert all(isinstance(s, CellStats) for s in result)


def test_group_by_event_cell_one_per_cell() -> None:
    """One CellStats per (event_type, cell_id) combination."""
    df = _make_multi_cell_df()
    result = group_by_event_cell(df)
    assert len(result) == 2


def test_group_by_event_cell_n_eff_nonneg() -> None:
    """n_eff_predicted >= 0 for all cells."""
    df = _make_multi_cell_df()
    result = group_by_event_cell(df)
    assert all(s.n_eff_predicted >= 0.0 for s in result)


def test_group_by_event_cell_formula_check() -> None:
    """n_eff_predicted matches (1 - rho_bar) * n_nominal / vif formula."""
    df = _make_multi_cell_df()
    result = group_by_event_cell(df)
    for cs in result:
        expected = (
            max(0.0, (1.0 - cs.rho_bar) * cs.n_nominal / cs.vif) if cs.vif > 0 else 0.0
        )
        assert abs(cs.n_eff_predicted - expected) < 1e-9, (
            f"formula mismatch for {cs.cell_id}"
        )


def test_group_by_event_cell_empty_skipped() -> None:
    """Empty cells are not included in results."""
    df = _make_multi_cell_df()
    # Add a row with a new cell_id but filter it out via groupby (empty after filter)
    # Simplest: pass an empty dataframe
    result = group_by_event_cell(pd.DataFrame(columns=df.columns))
    assert result == []


# ---------------------------------------------------------------------------
# Task 2: fallback groupers
# ---------------------------------------------------------------------------


def test_group_by_event_duration_fallback_level() -> None:
    """group_by_event_duration() sets fallback_level=1 on all results."""
    df = _make_multi_cell_df()
    result = group_by_event_duration(df)
    assert all(s.fallback_level == 1 for s in result)


def test_group_by_event_duration_cell_id_is_duration_bucket() -> None:
    """L1 fallback: cell_id = duration_bucket (pooled across liquidity)."""
    df = _make_multi_cell_df()
    result = group_by_event_duration(df)
    for cs in result:
        assert "_x_" not in cs.cell_id  # just duration bucket, no liquidity suffix


def test_group_by_event_liquidity_fallback_level() -> None:
    """group_by_event_liquidity() sets fallback_level=2 on all results."""
    df = _make_multi_cell_df()
    result = group_by_event_liquidity(df)
    assert all(s.fallback_level == 2 for s in result)


def test_group_by_event_liquidity_cell_id_is_liquidity_regime() -> None:
    """L2 fallback: cell_id = liquidity_regime (pooled across duration)."""
    df = _make_multi_cell_df()
    result = group_by_event_liquidity(df)
    for cs in result:
        assert cs.cell_id in ("LOW", "MID", "HIGH")


# ---------------------------------------------------------------------------
# Plan 03 Task 1: apply_hierarchical_fallback / emit_wave1_decision / build_cell_output_dict
# ---------------------------------------------------------------------------


def _get_plan03_symbols() -> tuple:
    """Import Plan 03 symbols lazily (added in plan 03 execution)."""
    import importlib.util as _ilu
    import sys as _sys
    from pathlib import Path as _Path

    # Reload to pick up new symbols
    spec = _ilu.spec_from_file_location(
        "wave1_power_p03", _Path(__file__).parent / "wave1_power.py"
    )
    mod = _ilu.module_from_spec(spec)
    _sys.modules["wave1_power_p03"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_gate_k_is_4() -> None:
    """POWER-02: GATE_K must be 4 per power_budget.json D-08, D-11."""
    assert GATE_K == 4


def test_n_eff_formula_via_compute_cell_stats() -> None:
    """POWER-01: n_eff = (1 - rho_bar) * n_nominal / VIF."""

    df = _make_multi_cell_df()
    ecb_high = df[(df["event_type"] == "ECB") & (df["cell_id"] == "0-60m_x_HIGH")]
    cs = compute_cell_stats(ecb_high, "ECB", "0-60m_x_HIGH", fallback_level=0)
    expected = (1.0 - cs.rho_bar) * cs.n_nominal / cs.vif if cs.vif > 0 else 0.0
    assert abs(cs.n_eff_predicted - max(0.0, expected)) < 1e-9


def test_kill_switch_unanimous_fail() -> None:
    """POWER-02: all cells fail gate_k → null-ship-v3."""
    mod = _get_plan03_symbols()
    emit_wave1_decision = mod.emit_wave1_decision
    failing = [CellStats("ECB", f"cell_{i}", 10, 0.95, 3.0, 0.1, 0) for i in range(6)]
    assert emit_wave1_decision(failing) == "null-ship-v3"


def test_kill_switch_partial_pass() -> None:
    """POWER-02: any cell passes gate_k → proceed."""
    mod = _get_plan03_symbols()
    emit_wave1_decision = mod.emit_wave1_decision
    mixed = [
        CellStats("ECB", "0-60m_x_HIGH", 100, 0.1, 1.0, 90.0, 0),  # passes
        CellStats("ECB", "0-60m_x_MID", 10, 0.95, 3.0, 0.1, 0),  # fails
    ]
    assert emit_wave1_decision(mixed) == "proceed"


def test_hierarchical_fallback_l1() -> None:
    """POWER-03: L1 fallback pools across liquidity → duration-only cells."""
    mod = _get_plan03_symbols()
    apply_hierarchical_fallback = mod.apply_hierarchical_fallback
    df = _make_multi_cell_df()
    stats_list, level = apply_hierarchical_fallback("L1 (pooled-across-liquidity)", df)
    assert level == 1
    assert all("_x_" not in s.cell_id for s in stats_list), (
        f"L1 cells should not contain '_x_', got: {[s.cell_id for s in stats_list]}"
    )


def test_hierarchical_fallback_l2() -> None:
    """POWER-03: L2 fallback pools across duration → liquidity-only cells."""
    mod = _get_plan03_symbols()
    apply_hierarchical_fallback = mod.apply_hierarchical_fallback

    # Need df with liquidity_regime values in LOW/MID/HIGH
    import pandas as pd
    import numpy as np

    rows = []
    for dur in ["0-60m", "60-120m"]:
        for liq in ["HIGH", "MID", "LOW"]:
            for i in range(5):
                rows.append(
                    {
                        "event_type": "ECB",
                        "duration_bucket": dur,
                        "liquidity_regime": liq,
                        "cell_id": f"{dur}_x_{liq}",
                        "pair": f"pair_{i % 3}",
                        "long": np.random.randint(5, 15),
                        "neutral": np.random.randint(2, 8),
                        "short": np.random.randint(1, 6),
                    }
                )
    df = pd.DataFrame(rows)
    stats_list, level = apply_hierarchical_fallback("L2 (pooled-across-duration)", df)
    assert level == 2
    assert all(s.cell_id in ("HIGH", "MID", "LOW") for s in stats_list), (
        f"L2 cells should be liquidity regimes, got: {[s.cell_id for s in stats_list]}"
    )


def test_json_additive_only() -> None:
    """POWER-01: update_regime_breakdown must not overwrite Phase 81 fields."""
    import json
    import tempfile
    from pathlib import Path as _Path
    import pandas as pd

    mod = _get_plan03_symbols()
    update_regime_breakdown = mod.update_regime_breakdown

    sample_json = {
        "schema_version": "v4.8-phase-81-label-only",
        "generated_at": "2026-04-21T04:44:36+00:00",
        "audit_gate": {"escalate_to": "L0"},
        "cells_by_event": {
            "ECB": [{"cell_id": "0-60m_x_HIGH", "n_nominal": 10, "empty": False}]
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_json, f)
        tmp_path = _Path(f.name)
    try:
        stats_map = {
            ("ECB", "0-60m_x_HIGH"): {
                "rho_bar": 0.5,
                "vif": 1.5,
                "n_eff_predicted": 3.3,
                "fleiss_kappa_scalar": 0.2,
                "fleiss_kappa_pairwise": {
                    "long_vs_neutral": 0.1,
                    "long_vs_short": 0.2,
                    "neutral_vs_short": 0.15,
                },
                "bootstrap_ci_95": [0.1, 0.4],
                "bootstrap_block_len": 3.0,
            }
        }
        update_regime_breakdown(tmp_path, pd.DataFrame(), stats_map, {}, 0, "proceed")
        result = json.loads(tmp_path.read_text())
        assert result["schema_version"] == "v4.8-phase-81-label-only"
        assert result["generated_at"] == "2026-04-21T04:44:36+00:00"
        assert result["wave1_decision"] == "proceed"
        assert result["cells_by_event"]["ECB"][0]["rho_bar"] == 0.5
    finally:
        tmp_path.unlink()


def test_wave1_decision_emitted() -> None:
    """POWER-02: wave1_decision field must exist in regime_breakdown.json after update."""
    import json
    import tempfile
    from pathlib import Path as _Path
    import pandas as pd

    mod = _get_plan03_symbols()
    update_regime_breakdown = mod.update_regime_breakdown

    sample_json = {
        "schema_version": "v4.8-phase-81-label-only",
        "audit_gate": {"escalate_to": None},
        "cells_by_event": {
            "ECB": [{"cell_id": "0-60m_x_HIGH", "n_nominal": 5, "empty": False}]
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_json, f)
        tmp_path = _Path(f.name)
    try:
        update_regime_breakdown(tmp_path, pd.DataFrame(), {}, {}, 0, "null-ship-v3")
        result = json.loads(tmp_path.read_text())
        assert "wave1_decision" in result
        assert result["wave1_decision"] == "null-ship-v3"
    finally:
        tmp_path.unlink()


def test_fleiss_kappa_pairwise_keys() -> None:
    """POWER-04: pairwise kappa must have exactly 3 keys."""
    import importlib.util as _ilu
    import sys as _sys
    from pathlib import Path as _Path
    import pandas as pd

    spec = _ilu.spec_from_file_location(
        "wave1_distributional_t", _Path(__file__).parent / "wave1_distributional.py"
    )
    mod = _ilu.module_from_spec(spec)
    _sys.modules["wave1_distributional_t"] = mod
    spec.loader.exec_module(mod)
    compute_fleiss_kappa = mod.compute_fleiss_kappa

    df = pd.DataFrame({"long": [10, 12, 8], "neutral": [5, 4, 6], "short": [3, 2, 4]})
    result = compute_fleiss_kappa(df)
    assert set(result.pairwise.keys()) == {
        "long_vs_neutral",
        "long_vs_short",
        "neutral_vs_short",
    }


def test_bootstrap_reproducible() -> None:
    """POWER-05: seed=42 must give identical CI on repeated calls."""
    import importlib.util as _ilu
    import sys as _sys
    from pathlib import Path as _Path
    import numpy as np

    spec = _ilu.spec_from_file_location(
        "wave1_distributional_t2", _Path(__file__).parent / "wave1_distributional.py"
    )
    mod = _ilu.module_from_spec(spec)
    _sys.modules["wave1_distributional_t2"] = mod
    spec.loader.exec_module(mod)
    compute_bootstrap_ci = mod.compute_bootstrap_ci

    series = np.array([0.5, 0.3, 0.7, 0.4, 0.6, 0.5, 0.4, 0.8, 0.3, 0.5, 0.6, 0.4])
    r1 = compute_bootstrap_ci(series)
    r2 = compute_bootstrap_ci(series)
    assert r1.ci_95 == r2.ci_95, (
        f"seed=42 must give reproducible CI: {r1.ci_95} vs {r2.ci_95}"
    )
    assert r1.block_len == r2.block_len
