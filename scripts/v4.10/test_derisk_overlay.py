"""Tests for derisk_overlay.py (Phase 90 Layer 4 overlay).

TestMtMultiplier / TestComputeMt: implemented (Task 2 makes them GREEN).
TestFragilityGrid / TestStressReplay / TestShipDecisionJson: stubs for Plan 02/03.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

# ── Load derisk_overlay.py as module (absolute path, CWD-independent) ──────
_MODULE_PATH = pathlib.Path(__file__).parent / "derisk_overlay.py"
_spec = importlib.util.spec_from_file_location("derisk_overlay", _MODULE_PATH)
if _spec is not None and _spec.loader is not None:
    derisk_overlay = importlib.util.module_from_spec(_spec)
    sys.modules["derisk_overlay"] = derisk_overlay
    try:
        _spec.loader.exec_module(derisk_overlay)
        _DERISK_OVERLAY_AVAILABLE = True
    except Exception:
        _DERISK_OVERLAY_AVAILABLE = False
else:
    derisk_overlay = None
    _DERISK_OVERLAY_AVAILABLE = False


def _require_derisk_overlay() -> None:
    if not _DERISK_OVERLAY_AVAILABLE or derisk_overlay is None:
        raise ImportError("derisk_overlay.py not available")


# ── SEAL spec path for test_spec_driven (D-25: no hardcoded numeric literals) ──
_SEAL_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / ".planning"
    / "phases"
    / "88-pre-registration-seal-v4-10"
    / "88-SEAL"
)


class TestMtMultiplier:
    """OVERLAY-01: MtMultiplier newtype invariants."""

    def test_rejects_above_one(self) -> None:
        """MtMultiplier rejects value > 1.0."""
        _require_derisk_overlay()
        with pytest.raises(ValueError):
            derisk_overlay.MtMultiplier(1.01)

    def test_rejects_negative(self) -> None:
        """MtMultiplier rejects value < 0.0."""
        _require_derisk_overlay()
        with pytest.raises(ValueError):
            derisk_overlay.MtMultiplier(-0.01)

    def test_rejects_nan(self) -> None:
        """MtMultiplier rejects NaN."""
        _require_derisk_overlay()
        with pytest.raises(ValueError):
            derisk_overlay.MtMultiplier(float("nan"))

    def test_accepts_valid(self) -> None:
        """MtMultiplier accepts boundary and interior values in [0.0, 1.0]."""
        _require_derisk_overlay()
        for v in (0.0, 0.5, 1.0):
            assert derisk_overlay.MtMultiplier(v).value == v


class TestComputeMt:
    """OVERLAY-02: compute_mt exponential formula."""

    def test_bounded(self) -> None:
        """compute_mt(z_t=0, p_shift=0) returns MtMultiplier(1.0)."""
        _require_derisk_overlay()
        mt = derisk_overlay.compute_mt(
            z_t=0.0, p_shift=0.0, alpha=1.5, beta=1.0, mt_upper_cap=1.0
        )
        assert isinstance(mt, derisk_overlay.MtMultiplier)
        assert 0.0 <= mt.value <= 1.0
        assert mt.value == 1.0  # exp(0) = 1.0 → min(1.0, 1.0) = 1.0

    def test_spec_driven(self) -> None:
        """Module constants loaded from overlay_spec.json, not hardcoded."""
        _require_derisk_overlay()
        spec = json.loads((_SEAL_DIR / "overlay_spec.json").read_bytes())
        # Verify module constants match SEAL spec (D-25: no numeric literal in test)
        assert derisk_overlay._ALPHA_PRIMARY == spec["alpha_primary"]
        assert derisk_overlay._BETA_PRIMARY == spec["beta_primary"]
        assert derisk_overlay._MT_UPPER_CAP == spec["mt_upper_cap"]
        assert derisk_overlay._ALPHA_GRID == [
            float(a) for a in spec["alpha_fragility_grid"]
        ]
        assert derisk_overlay._BETA_GRID == [
            float(b) for b in spec["beta_fragility_grid"]
        ]


class TestZt:
    """OVERLAY-02: z_t rolling standardized drawdown (Plan 02)."""

    def test_prewindow_zero(self) -> None:
        """90-RESEARCH §Pattern 3: pre-window bars (1..window-1) → z_t=0.0."""
        _require_derisk_overlay()
        import polars as pl

        df = pl.DataFrame(
            {
                "cell_id": ["c1"] * 25,
                "fold_id": [1] * 25,
                "bar_ts": [f"2024-01-{i + 1:02d}" for i in range(25)],
                "dd_value": [float(i) for i in range(25)],
            }
        )
        out = derisk_overlay.compute_z_t(df, window=20)
        # first 19 rows z_t == 0.0
        assert (out["z_t"].head(19) == 0.0).all()


class TestPshift:
    """OVERLAY-02: p_shift consecutive_loss surrogate (Plan 02)."""

    def test_monotone_clipped(self) -> None:
        """p_shift = consecutive_loss_count / 5.0, clipped to [0, 1]."""
        _require_derisk_overlay()
        import polars as pl

        df = pl.DataFrame({"consecutive_loss_count": [0, 1, 5, 10]})
        out = derisk_overlay.compute_p_shift(df, n_threshold=5.0)
        assert out["p_shift"].to_list() == [0.0, 0.2, 1.0, 1.0]


class TestComposeMultipliers:
    """OVERLAY-02: D-03 multiplicative composition (Plan 02)."""

    def test_gate_closed_is_fail_close(self) -> None:
        """D-03: L3=GateClosed → final=0.0 (fail-close inherited)."""
        _require_derisk_overlay()
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        from dd_gate import GateClosed  # type: ignore[import-not-found]

        result = derisk_overlay.compose_multipliers(
            GateClosed(reason="test"),
            z_t=0.0,
            p_shift=0.0,
            alpha=derisk_overlay._ALPHA_PRIMARY,
            beta=derisk_overlay._BETA_PRIMARY,
            mt_upper_cap=derisk_overlay._MT_UPPER_CAP,
        )
        assert result == 0.0


class TestFragilityGrid:
    """OVERLAY-02: α/β 5×5=25 fragility grid (Plan 02 implements)."""

    def test_25_cells(self, tmp_path: pathlib.Path) -> None:
        """OVERLAY-02: α_grid × β_grid = 25 points all produce valid aggregate."""
        _require_derisk_overlay()
        import polars as pl

        rows = derisk_overlay.run_fragility_grid(
            pathlib.Path("data/v4.10/dd_traces.parquet"),
        )
        assert len(rows) == 25, f"expected 25, got {len(rows)}"
        # Every row has alpha/beta pulled from _ALPHA_GRID × _BETA_GRID (no hardcode leak)
        seen_alpha = {r["alpha"] for r in rows}
        seen_beta = {r["beta"] for r in rows}
        assert seen_alpha == set(derisk_overlay._ALPHA_GRID)
        assert seen_beta == set(derisk_overlay._BETA_GRID)
        # CSV emit
        out = tmp_path / "fragility_grid.csv"
        derisk_overlay.emit_fragility_grid_csv(rows, out)
        reloaded = pl.read_csv(out)
        assert reloaded.height == 25
        assert set(reloaded.columns) >= {"alpha", "beta", "pf_median", "max_dd_median"}

    def test_chain_reference_coherence(self) -> None:
        """Pitfall 2 guard: per-cell overlay equity uses equity_on[t-1] chain (not equity[t-1])."""
        _require_derisk_overlay()
        import polars as pl

        # Force m_t = 0.5 everywhere in a synthetic group; check equity_on grows slower
        df = pl.DataFrame(
            {
                "bar_ts": [f"2024-01-{i + 1:02d}" for i in range(5)],
                "equity": [100.0, 110.0, 120.0, 130.0, 140.0],
                "m_t": [1.0, 0.5, 0.5, 0.5, 0.5],
            }
        )
        out = derisk_overlay._reconstruct_overlay_equity_group(df)
        eq_on = out["equity_on"].to_list()
        # equity_on[1] = 100 + 0.5*10 = 105; equity_on[2] = 105 + 0.5*10 = 110 (chain)
        # NOT 100 + 0.5*10 = 105 at t=2 (would be non-chain)
        assert eq_on[0] == 100.0
        assert eq_on[1] == 105.0
        assert eq_on[2] == 110.0
        assert eq_on[3] == 115.0
        assert eq_on[4] == 120.0


class TestStressCluster:
    """OVERLAY-03: identify_stress_clusters / annotate_stress_cluster / pad_absent_events (Task 1)."""

    def test_identify_merges_proximity(self) -> None:
        """Temporal proximity merge: adjacent high-vol bars → single cluster."""
        _require_derisk_overlay()
        import polars as pl
        from datetime import date, timedelta

        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(60)]
        # high dd at indices 15-19, 35-39, 55-59
        dd_vals = [0.01] * 60
        for i in list(range(15, 20)) + list(range(35, 40)) + list(range(55, 60)):
            dd_vals[i] = 1.0
        df = pl.DataFrame(
            {
                "bar_ts": dates * 2,
                "cell_id": ["c1"] * 60 + ["c2"] * 60,
                "fold_id": [1] * 120,
                "dd_value": dd_vals * 2,
            }
        )
        clusters = derisk_overlay.identify_stress_clusters(
            df, vol_window=5, proximity_gap=3
        )
        # top-3 clusters expected
        assert 1 <= len(clusters) <= 3

    def test_annotate_covid(self) -> None:
        """annotate_stress_cluster maps 2020-03 date_window to '2020-03 COVID'."""
        _require_derisk_overlay()
        cluster = {
            "cluster_id": 0,
            "date_window": ("2020-03-10", "2020-03-25"),
            "peak_vol": 0.5,
            "n_bars": 16,
            "absent_event": False,
        }
        annotated = derisk_overlay.annotate_stress_cluster(cluster)
        assert annotated["expected_event_annotation"] == "2020-03 COVID"

    def test_pad_absent(self) -> None:
        """pad_absent_events fills up to 3 entries with absent_event placeholders."""
        _require_derisk_overlay()
        clusters = [
            {
                "cluster_id": 0,
                "date_window": ("2022-02-01", "2022-10-30"),
                "peak_vol": 0.3,
                "n_bars": 100,
                "absent_event": False,
                "expected_event_annotation": "2022 rate hike",
            }
        ]
        padded = derisk_overlay.pad_absent_events(
            clusters,
            expected_labels=[
                "2020-03 COVID",
                "2022 rate hike",
                "2024-08 JPY carry unwind",
            ],
        )
        assert len(padded) == 3
        absent = [c for c in padded if c.get("absent_event")]
        assert len(absent) == 2


class TestStressReplay:
    """OVERLAY-03: Stress event replay (Plan 03 implements)."""

    def test_three_events(self) -> None:
        """OVERLAY-03: 3 stress events (2020-03 / 2022 / 2024-08) produce stress_events entries."""
        _require_derisk_overlay()
        import polars as pl

        df = pl.read_parquet("data/v4.10/dd_traces.parquet")
        raw = derisk_overlay.identify_stress_clusters(df)
        padded = derisk_overlay.pad_absent_events(
            [derisk_overlay.annotate_stress_cluster(c) for c in raw],
            expected_labels=[
                "2020-03 COVID",
                "2022 rate hike",
                "2024-08 JPY carry unwind",
            ],
        )
        assert len(padded) == 3
        labels = {p["expected_event_annotation"] for p in padded}
        assert labels == {
            "2020-03 COVID",
            "2022 rate hike",
            "2024-08 JPY carry unwind",
        } or any(p.get("absent_event") for p in padded)

    def test_dd_improvement(self) -> None:
        """OVERLAY-03: overlay-on produces computable metrics vs overlay-off (observational)."""
        _require_derisk_overlay()
        import math
        import pathlib

        import polars as pl

        df_path = pathlib.Path("data/v4.10/dd_traces.parquet")
        raw = derisk_overlay.identify_stress_clusters(pl.read_parquet(df_path))
        padded = derisk_overlay.pad_absent_events(
            [derisk_overlay.annotate_stress_cluster(c) for c in raw],
            expected_labels=[
                "2020-03 COVID",
                "2022 rate hike",
                "2024-08 JPY carry unwind",
            ],
        )
        result = derisk_overlay.run_stress_replay(
            df_path,
            padded,
            alpha=derisk_overlay._ALPHA_PRIMARY,
            beta=derisk_overlay._BETA_PRIMARY,
            mt_upper_cap=derisk_overlay._MT_UPPER_CAP,
        )
        # Structural assertions (observational direction documented in VERIFICATION.md)
        assert "overlay_off" in result
        assert "overlay_on" in result
        assert "pf_median" in result["overlay_on"]
        assert "max_dd_median" in result["overlay_on"]
        assert math.isfinite(result["overlay_on"]["max_dd_median"])


class TestShipDecisionJson:
    """OVERLAY-03: v4_10_ship_decision.json emit (Plan 03 implements)."""

    def test_overlay_evaluation(self, tmp_path: pathlib.Path) -> None:
        """OVERLAY-03: ship_decision.json has overlay_evaluation section with required keys."""
        _require_derisk_overlay()
        out = tmp_path / "ship_decision.json"
        overlay_eval = {
            "stress_events": [],
            "overlay_off": {
                "pf_median": 1.0,
                "max_dd_median": -0.1,
                "calmar_median": 0.2,
            },
            "overlay_on": {
                "pf_median": 1.2,
                "max_dd_median": -0.05,
                "calmar_median": 0.4,
            },
            "fragility_grid": {"alpha_beta_25pt": []},
        }
        stamp = {
            "threshold_commit": "abc",
            "regime_commit": "def",
            "sizing_exit_commit": "ghi",
            "sizing_exit_commit_v410": "jkl",
            "phase_commit": "mno",
        }
        derisk_overlay.emit_ship_decision_json(overlay_eval, out, stamp)
        loaded = json.loads(out.read_text())
        assert loaded["schema_version"] == "v4.10.0"
        assert "overlay_evaluation" in loaded
        assert loaded["overlay_evaluation"]["quint_pin_stamp"] == stamp
        assert set(loaded["overlay_evaluation"].keys()) >= {
            "stress_events",
            "overlay_off",
            "overlay_on",
            "fragility_grid",
            "quint_pin_stamp",
        }

    def test_ship_metrics_null(self, tmp_path: pathlib.Path) -> None:
        """D-06: ship_metrics MUST be null literal in Phase 90 output (Phase 91 reserved)."""
        _require_derisk_overlay()
        out = tmp_path / "ship_decision.json"
        derisk_overlay.emit_ship_decision_json(
            {
                "stress_events": [],
                "overlay_off": {},
                "overlay_on": {},
                "fragility_grid": {"alpha_beta_25pt": []},
            },
            out,
            {
                "threshold_commit": "x",
                "regime_commit": "y",
                "sizing_exit_commit": "z",
                "sizing_exit_commit_v410": "w",
                "phase_commit": "v",
            },
        )
        loaded = json.loads(out.read_text())
        assert loaded["ship_metrics"] is None

    def test_real_ship_decision_file_exists(self) -> None:
        """OVERLAY-03 + Phase 91-02 D-06: Actual reports/v4.10/v4_10_ship_decision.json.

        Phase 90 emitted ship_metrics=null. Phase 91-02 performed D-06 section-level
        merge, populating ship_metrics with gate-redesign-v410-a5f7183 provenance.
        overlay_evaluation must remain byte-identical to Phase 90 emit.
        """
        _require_derisk_overlay()
        p = pathlib.Path("reports/v4.10/v4_10_ship_decision.json")
        assert p.exists(), "CLI emission step must produce ship_decision.json"
        loaded = json.loads(p.read_text())
        assert loaded["schema_version"] == "v4.10.0"

        # overlay_evaluation: byte-identical from Phase 90 (D-06 invariant)
        oe = loaded["overlay_evaluation"]
        assert len(oe["stress_events"]) == 3
        assert len(oe["fragility_grid"]["alpha_beta_25pt"]) == 25
        assert set(oe["quint_pin_stamp"].keys()) >= {
            "threshold_commit",
            "regime_commit",
            "sizing_exit_commit",
            "sizing_exit_commit_v410",
        }

        # ship_metrics: populated by Phase 91-02 (D-06 section-level merge)
        sm = loaded["ship_metrics"]
        assert sm is not None, "Phase 91-02 must have populated ship_metrics"
        assert sm["data_provenance"] == "gate-redesign-v410-a5f7183"
        assert sm["coverage_tier"] == "inconclusive-2024-2025-only"
        assert isinstance(sm["ship_verdict"], bool)
        assert isinstance(sm["edge_count_p_adj_005"], int)
        pm = sm["primary_metrics"]
        assert set(pm.keys()) >= {
            "pf_cost_adj_median",
            "calmar_median",
            "es_median",
            "turnover_sharpe_median",
        }
        # All 4 primary metrics must be finite floats
        import math

        for key in (
            "pf_cost_adj_median",
            "calmar_median",
            "es_median",
            "turnover_sharpe_median",
        ):
            assert math.isfinite(pm[key]), f"{key} must be finite, got {pm[key]}"


class TestDdTracesSchema:
    """Phase 90 Plan 01 blocker resolved: dd_traces.parquet schema verification."""

    _DD_TRACES_DIR = (
        pathlib.Path(__file__).resolve().parents[2]
        / "data"
        / "v4.10"
        / "dd_traces.parquet"
    )

    def test_partitions_exist(self) -> None:
        """192 cell_id hive partitions written by gen_dd_traces.py."""
        if not self._DD_TRACES_DIR.exists():
            pytest.skip("dd_traces.parquet not yet generated — run gen_dd_traces.py")
        partitions = list(self._DD_TRACES_DIR.glob("cell_id=*/00000000.parquet"))
        assert len(partitions) == 192, f"Expected 192 partitions, got {len(partitions)}"

    def test_schema_columns(self) -> None:
        """Each partition has the D-16 schema columns."""
        import polars as pl

        if not self._DD_TRACES_DIR.exists():
            pytest.skip("dd_traces.parquet not yet generated — run gen_dd_traces.py")
        partitions = list(self._DD_TRACES_DIR.glob("cell_id=*/00000000.parquet"))
        if not partitions:
            pytest.skip("No partitions found")
        df = pl.read_parquet(partitions[0])
        required = {
            "cell_id",
            "fold_id",
            "bar_ts",
            "equity",
            "dd_value",
            "dd_state",
            "risk_multiplier",
            "rest_flag",
            "consecutive_loss_count",
            "daily_loss_sum",
        }
        missing = required - set(df.columns)
        assert not missing, f"Missing columns: {missing}"

    def test_bar_ts_dtype(self) -> None:
        """bar_ts is Datetime(ms, UTC) — required by apply_all_cells sort."""
        import polars as pl

        if not self._DD_TRACES_DIR.exists():
            pytest.skip("dd_traces.parquet not yet generated — run gen_dd_traces.py")
        partitions = list(self._DD_TRACES_DIR.glob("cell_id=*/00000000.parquet"))
        if not partitions:
            pytest.skip("No partitions found")
        df = pl.read_parquet(partitions[0])
        assert df["bar_ts"].dtype == pl.Datetime(time_unit="ms", time_zone="UTC"), (
            f"Unexpected bar_ts dtype: {df['bar_ts'].dtype}"
        )
