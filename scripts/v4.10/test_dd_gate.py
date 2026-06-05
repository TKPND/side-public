"""Tests for scripts/v4.10/dd_gate.py (DD-01, DD-02, DD-03).

Phase 89 Plan 01: TestDrawDownEnum body implemented.
TestApplyStepDown / TestApplyHardCap / TestApplyAllCells / TestParquetEmit are stubs
(filled in Plan 02 / Plan 03).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

# Load dd_gate.py as module (absolute path, CWD-independent)
_MODULE_PATH = pathlib.Path(__file__).parent / "dd_gate.py"
_spec = importlib.util.spec_from_file_location("dd_gate", _MODULE_PATH)
if _spec is not None and _spec.loader is not None:
    dd_gate = importlib.util.module_from_spec(_spec)
    sys.modules["dd_gate"] = dd_gate
    try:
        _spec.loader.exec_module(dd_gate)
        _DD_GATE_AVAILABLE = True
    except Exception:
        _DD_GATE_AVAILABLE = False
else:
    dd_gate = None  # type: ignore[assignment]
    _DD_GATE_AVAILABLE = False


def _require_dd_gate() -> None:
    """Raise ImportError if dd_gate module is not available."""
    if not _DD_GATE_AVAILABLE or dd_gate is None:
        raise ImportError("dd_gate.py not available")


# ---------------------------------------------------------------------------
# DD-01: DrawDown Enum Tests
# ---------------------------------------------------------------------------


class TestDrawDownEnum:
    def test_zero_trade_fold_returns_insufficient_data(self) -> None:
        """D-24 v4.3 zero-trade regression: trade=0 fold returns InsufficientData."""
        _require_dd_gate()
        dd = dd_gate.InsufficientData()
        assert isinstance(dd, dd_gate.InsufficientData)
        assert dd_gate.pick_risk_multiplier(dd) == 0.0

    def test_ok_variant_accepts_valid_value(self) -> None:
        """DD-01 AC: Ok(value) accepted for 0.0 <= value < 0.20."""
        _require_dd_gate()
        assert dd_gate.Ok(value=0.0).value == 0.0
        assert dd_gate.Ok(value=0.10).value == 0.10
        assert dd_gate.Ok(value=0.199).value == 0.199

    def test_ok_variant_rejects_out_of_range(self) -> None:
        """DD-01 AC: Ok(value) rejects value outside [0.0, 0.20)."""
        _require_dd_gate()
        with pytest.raises(ValueError):
            dd_gate.Ok(value=0.20)
        with pytest.raises(ValueError):
            dd_gate.Ok(value=-0.01)

    def test_gate_closed_reason_strings(self) -> None:
        """DD-01 AC: GateClosed accepts all 3 defined reason strings."""
        _require_dd_gate()
        for reason in ["hard_cap_20pct", "daily_loss_3pct", "step_down_consecutive"]:
            gate = dd_gate.GateClosed(reason=reason)
            assert gate.reason == reason
            assert dd_gate.pick_risk_multiplier(gate) == 0.0

    def test_pick_risk_exhaustive(self) -> None:
        """6 case (D-07 step-down spec): 1.0 / 0.75 / 0.50 / 0.25 / 0.0 / 0.0."""
        _require_dd_gate()
        assert dd_gate.pick_risk_multiplier(dd_gate.Ok(value=0.04)) == 1.0
        assert dd_gate.pick_risk_multiplier(dd_gate.Ok(value=0.09)) == 0.75
        assert dd_gate.pick_risk_multiplier(dd_gate.Ok(value=0.14)) == 0.50
        assert dd_gate.pick_risk_multiplier(dd_gate.Ok(value=0.18)) == 0.25
        assert dd_gate.pick_risk_multiplier(dd_gate.InsufficientData()) == 0.0
        assert (
            dd_gate.pick_risk_multiplier(dd_gate.GateClosed(reason="hard_cap_20pct"))
            == 0.0
        )


# ---------------------------------------------------------------------------
# DD-02: Step-Down & Hard Cap Tests (Plan 02 で実装)
# ---------------------------------------------------------------------------


class TestApplyStepDown:
    """DD-02 AC: step_down thresholds [5%/10%/15%] -> risk [0.75/0.50/0.25] + consecutive_loss handling."""

    def test_step_down_thresholds(self) -> None:
        """D-07: fold_dd ranges map to correct risk multipliers via pick_risk_multiplier."""
        _require_dd_gate()
        cases = [
            (0.04, 1.0),
            (0.09, 0.75),
            (0.14, 0.50),
            (0.18, 0.25),
        ]
        for dd_value, expected_mul in cases:
            dd = dd_gate.apply_step_down(fold_dd=dd_value, consecutive_loss=0)
            assert isinstance(dd, dd_gate.Ok), (
                f"expected Ok for dd={dd_value}, got {dd}"
            )
            assert dd_gate.pick_risk_multiplier(dd) == expected_mul, (
                f"dd={dd_value}: expected mul={expected_mul}, got {dd_gate.pick_risk_multiplier(dd)}"
            )

    def test_consecutive_loss_triggers_rest(self) -> None:
        """D-10: consecutive_loss >= 5 -> GateClosed('step_down_consecutive')."""
        _require_dd_gate()
        for consec in [5, 6, 10]:
            dd = dd_gate.apply_step_down(fold_dd=0.05, consecutive_loss=consec)
            assert isinstance(dd, dd_gate.GateClosed)
            assert dd.reason == "step_down_consecutive"

    def test_consecutive_loss_below_gate(self) -> None:
        """D-10: consecutive_loss < 5 -> Ok (no rest)."""
        _require_dd_gate()
        for consec in [0, 1, 4]:
            dd = dd_gate.apply_step_down(fold_dd=0.05, consecutive_loss=consec)
            assert isinstance(dd, dd_gate.Ok)

    def test_win_resets_consecutive_count(self) -> None:
        """D-06: caller manages consecutive_loss; passing 0 after a win returns Ok."""
        _require_dd_gate()
        # Simulate state machine: consecutive=4 -> win -> consecutive=0 -> apply_step_down -> Ok
        dd = dd_gate.apply_step_down(fold_dd=0.05, consecutive_loss=0)
        assert isinstance(dd, dd_gate.Ok)

    def test_fold_rest_resets_consecutive_count(self) -> None:
        """D-10: 1 fold rest -> consecutive=0 in next fold (caller-managed)."""
        _require_dd_gate()
        # Caller resets consecutive on fold boundary; verify function honors the reset
        dd = dd_gate.apply_step_down(fold_dd=0.05, consecutive_loss=0)
        assert isinstance(dd, dd_gate.Ok)


class TestApplyHardCap:
    """DD-02 AC: hard_cap 20% / daily_loss 3% / event-day groupby (UTC) / hard_cap precedence."""

    def test_hard_cap_triggers_gate_closed(self) -> None:
        """D-08: fold_dd >= 0.20 -> GateClosed('hard_cap_20pct')."""
        _require_dd_gate()
        for dd_value in [0.20, 0.25, 0.50]:
            dd = dd_gate.apply_hard_cap(fold_dd=dd_value, daily_loss_sum=0.0)
            assert isinstance(dd, dd_gate.GateClosed)
            assert dd.reason == "hard_cap_20pct"

    def test_daily_loss_triggers_gate_closed(self) -> None:
        """D-09: daily_loss_sum >= 0.03 -> GateClosed('daily_loss_3pct')."""
        _require_dd_gate()
        for daily in [0.03, 0.05, 0.10]:
            dd = dd_gate.apply_hard_cap(fold_dd=0.05, daily_loss_sum=daily)
            assert isinstance(dd, dd_gate.GateClosed)
            assert dd.reason == "daily_loss_3pct"

    def test_daily_loss_below_gate(self) -> None:
        """D-09: daily_loss_sum < 0.03 -> Ok."""
        _require_dd_gate()
        dd = dd_gate.apply_hard_cap(fold_dd=0.05, daily_loss_sum=0.029)
        assert isinstance(dd, dd_gate.Ok)

    def test_hard_cap_takes_precedence_over_daily_loss(self) -> None:
        """Both triggers active -> hard_cap wins (structural failure reported)."""
        _require_dd_gate()
        dd = dd_gate.apply_hard_cap(fold_dd=0.20, daily_loss_sum=0.05)
        assert isinstance(dd, dd_gate.GateClosed)
        assert dd.reason == "hard_cap_20pct"

    def test_daily_loss_resets_next_event_day(self) -> None:
        """D-09: caller resets daily_loss_sum on event-day boundary; function honors fresh sum."""
        _require_dd_gate()
        # Day 1: loss 0.03 -> GateClosed
        day1 = dd_gate.apply_hard_cap(fold_dd=0.05, daily_loss_sum=0.03)
        assert isinstance(day1, dd_gate.GateClosed)
        # Day 2: caller resets to 0.0 -> Ok
        day2 = dd_gate.apply_hard_cap(fold_dd=0.05, daily_loss_sum=0.0)
        assert isinstance(day2, dd_gate.Ok)

    def test_event_day_of_utc_boundary(self) -> None:
        """D-05: event day = UTC 24h window. Pure UTC date, DST-safe."""
        _require_dd_gate()
        from datetime import datetime, timezone

        assert (
            dd_gate._event_day_of(datetime(2026, 4, 23, 9, 30, tzinfo=timezone.utc))
            == "2026-04-23"
        )
        assert (
            dd_gate._event_day_of(datetime(2026, 4, 23, 23, 59, tzinfo=timezone.utc))
            == "2026-04-23"
        )
        assert (
            dd_gate._event_day_of(datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc))
            == "2026-04-24"
        )
        # DST month boundary (Europe DST start 2026-03-29) -> still pure UTC
        assert (
            dd_gate._event_day_of(datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc))
            == "2026-03-29"
        )
        # ISO string input
        assert dd_gate._event_day_of("2026-04-23T12:00:00Z") == "2026-04-23"


# ---------------------------------------------------------------------------
# DD-03: 192-Cell Apply & Parquet Emit Tests
# ---------------------------------------------------------------------------


class TestApplyAllCells:
    """DD-03 AC: 192-cell entrypoint + module-init sha256 verify + sized_pnl chain."""

    def test_wrong_cell_count_raises(self, tmp_path) -> None:
        """D-11 fail-close: len(cells) != 192 -> RuntimeError with '192' in message."""
        _require_dd_gate()
        import polars as pl

        for n in [0, 100, 191, 193]:
            cells = [{"cell_id": f"c{i}"} for i in range(n)]
            with pytest.raises(RuntimeError) as exc_info:
                dd_gate.apply_all_cells(
                    cells=cells,
                    sized_pnl=pl.DataFrame(),
                    output_dir=tmp_path / f"out_{n}",
                )
            assert "192" in str(exc_info.value)

    def test_192_cells_passes_assertion(self, tmp_path) -> None:
        """D-11: 192-cell call clears the assertion (sized_pnl empty -> 0-row emit OK)."""
        _require_dd_gate()
        import polars as pl

        cells = [{"cell_id": f"cell_{i:03d}"} for i in range(192)]
        sized_pnl = pl.DataFrame(
            schema={
                "cell_id": pl.Utf8,
                "fold_id": pl.UInt8,
                "bar_ts": pl.Datetime("ms", "UTC"),
                "equity": pl.Float64,
                "pnl": pl.Float64,
            }
        )
        df = dd_gate.apply_all_cells(
            cells=cells,
            sized_pnl=sized_pnl,
            output_dir=tmp_path / "dd_traces_192",
        )
        # Empty sized_pnl -> 0 rows emitted, but assertion is the gate
        assert df.shape[0] == 0
        # Schema must match D-16 even when empty
        assert set(df.columns) == {
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

    def test_startup_sha256_verify_fails_on_drift(self, tmp_path, monkeypatch) -> None:
        """D-12 module-init fail-close: corrupted dd_cap.json -> RuntimeError on call.

        Strategy: copy dd_cap.json to tmp, mutate 1 byte, monkeypatch _SEAL_DIR,
        then call _verify_dd_cap_hash() -> RuntimeError with expected sha256
        'df81cec6ba960bcbbe14e5cf61fbf372275212de010c9153cc904c0dc963bfd1' in message.
        """
        _require_dd_gate()
        import shutil

        # Copy real SEAL dir to tmp
        real_seal_dir = (
            pathlib.Path(__file__).parent.resolve()
            / ".."
            / ".."
            / ".planning"
            / "phases"
            / "88-pre-registration-seal-v4-10"
            / "88-SEAL"
        ).resolve()
        tmp_seal = tmp_path / "88-SEAL"
        shutil.copytree(real_seal_dir, tmp_seal)
        # Corrupt 1 byte of dd_cap.json
        corrupt = tmp_seal / "dd_cap.json"
        content = corrupt.read_bytes()
        corrupt.write_bytes(b"X" + content[1:])  # flip first byte

        # Monkeypatch _SEAL_DIR + force re-verify
        monkeypatch.setattr(dd_gate, "_SEAL_DIR", tmp_seal)
        with pytest.raises(RuntimeError) as exc_info:
            dd_gate._verify_dd_cap_hash()
        msg = str(exc_info.value)
        assert "df81cec6ba960bcbbe14e5cf61fbf372275212de010c9153cc904c0dc963bfd1" in msg
        assert "drift" in msg.lower() or "expected" in msg.lower()


class TestParquetEmit:
    """DD-03 AC: 5-pin stamp parquet round-trip + partition directory layout."""

    def test_stamp_survives_partition_write_read(self, tmp_path) -> None:
        """D-17: 5-pin stamp survives polars write_parquet(partition_by=..., metadata=...)
        and is readable via pl.read_parquet_metadata."""
        _require_dd_gate()
        import polars as pl

        df = pl.DataFrame(
            {
                "cell_id": ["cell_001", "cell_001", "cell_002"],
                "fold_id": [1, 1, 2],
                "bar_ts": [None, None, None],
                "value": [0.1, 0.2, 0.3],
            },
            schema={
                "cell_id": pl.Utf8,
                "fold_id": pl.UInt8,
                "bar_ts": pl.Datetime("ms", "UTC"),
                "value": pl.Float64,
            },
        )
        stamp = dd_gate.build_quint_pin_stamp()
        out_dir = tmp_path / "stamp_round_trip"
        dd_gate.write_dd_traces_parquet(df, out_dir, stamp=stamp)

        part_files = list(out_dir.rglob("*.parquet"))
        assert len(part_files) >= 1, f"no partition files in {out_dir}"

        meta = pl.read_parquet_metadata(str(part_files[0]))
        for k, v in stamp.items():
            assert meta.get(k) == v, (
                f"stamp key {k!r} drift: expected {v!r}, got {meta.get(k)!r}"
            )

    def test_partition_directory_layout(self, tmp_path) -> None:
        """D-14: cell_id hive partition (cell_id=<id>/...parquet)."""
        _require_dd_gate()
        import polars as pl

        df = pl.DataFrame(
            {
                "cell_id": ["cell_A", "cell_B"],
                "fold_id": [1, 1],
                "bar_ts": [None, None],
                "value": [1.0, 2.0],
            },
            schema={
                "cell_id": pl.Utf8,
                "fold_id": pl.UInt8,
                "bar_ts": pl.Datetime("ms", "UTC"),
                "value": pl.Float64,
            },
        )
        stamp = dd_gate.build_quint_pin_stamp()
        out_dir = tmp_path / "layout_check"
        dd_gate.write_dd_traces_parquet(df, out_dir, stamp=stamp)

        assert (out_dir / "cell_id=cell_A").is_dir()
        assert (out_dir / "cell_id=cell_B").is_dir()

    def test_data_provenance_has_sha7_suffix(self) -> None:
        """D-18: data_provenance = 'gate-redesign-v410-<sha7>' resolved at runtime."""
        _require_dd_gate()
        stamp = dd_gate.build_quint_pin_stamp()
        assert stamp["data_provenance"].startswith("gate-redesign-v410-")
        suffix = stamp["data_provenance"].rsplit("-", 1)[-1]
        assert len(suffix) == 7, (
            f"sha7 suffix should be 7 chars, got {len(suffix)}: {suffix!r}"
        )
        # git rev-parse --short=7 returns lowercase hex
        assert all(c in "0123456789abcdef" for c in suffix), f"non-hex sha7: {suffix!r}"

    def test_sizing_exit_commit_pins_are_sha256_not_git_sha(self) -> None:
        """RESEARCH §5-Pin Stamp Cross-Verification: sizing_exit_commit fields are sha256,
        NOT git object SHAs. Verify they are 64-char lowercase hex."""
        _require_dd_gate()
        stamp = dd_gate.build_quint_pin_stamp()
        for key in ["sizing_exit_commit", "sizing_exit_commit_v410"]:
            v = stamp[key]
            assert len(v) == 64, f"{key} should be 64-char sha256, got {len(v)}: {v!r}"
            assert all(c in "0123456789abcdef" for c in v), f"{key} non-hex: {v!r}"
        # git short SHAs are 7 chars
        assert len(stamp["threshold_commit"]) == 7
        assert len(stamp["regime_commit"]) == 7
