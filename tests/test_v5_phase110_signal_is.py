"""Phase 110 signal/backtest/FWER invariants."""

from __future__ import annotations

import sys

import polars as pl

sys.path.insert(0, "scripts")
import v5_phase110_signal_is as phase110  # noqa: E402


def _ns(ts: str) -> int:
    return int(pl.Series([ts]).str.to_datetime(time_zone="UTC")[0].timestamp() * 1_000_000_000)


def _fixture(minutes: int = 330) -> pl.DataFrame:
    start = _ns("2024-05-01T20:55:00Z")
    rows = []
    for i in range(minutes):
        price = 100.0
        if i == 5:
            price = 100.0  # 21:00 reference
        if i == 10:
            price = 100.1  # 21:05 entry for 10 bps positive move
        if i == 15:
            price = 99.9  # 21:10 entry for negative move vs reference
        if i == 70:
            price = 99.8  # 60 bars after 21:05
        if i == 250:
            price = 99.6  # 240 bars after 21:05
        rows.append(
            {
                "datetime_ns": start + i * 60 * 1_000_000_000,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1,
            }
        )
    return pl.DataFrame(rows)


def test_constants_match_sealed_grid() -> None:
    assert phase110.PAIRS == ("BTCUSD", "ETHUSD")
    assert phase110.WINDOW_MIN == (5, 10)
    assert phase110.THRESHOLD_BPS == (1.0, 2.0)
    assert phase110.EXIT_HORIZON_MIN == (60, 240)
    assert phase110.M_PRIME == 20
    assert phase110.FEE_BPS_ROUNDTRIP == 70.0


def test_signal_generation_uses_2100_utc_anchor() -> None:
    cell = phase110.generate_cells("BTCUSD")[0]
    trades = phase110.generate_signals_for_cell(_fixture(), cell)
    assert cell["window_anchor_utc"] == "21:00:00"
    assert trades[0]["window_anchor_utc"] == "21:00:00"


def test_mean_reversion_direction_mapping() -> None:
    assert phase110.direction_for_move_bps(2.0, threshold_bps=1.0) == -1
    assert phase110.direction_for_move_bps(-2.0, threshold_bps=1.0) == 1
    assert phase110.direction_for_move_bps(0.0, threshold_bps=1.0) == 0
    assert phase110.direction_for_move_bps(0.5, threshold_bps=1.0) == 0


def test_fixed_horizon_exit_indices() -> None:
    df = _fixture()
    cell_60 = {
        **phase110.generate_cells("BTCUSD")[0],
        "window_min": 5,
        "threshold_bps": 1.0,
        "exit_horizon_min": 60,
    }
    cell_240 = {**cell_60, "exit_horizon_min": 240}

    trade_60 = phase110.generate_signals_for_cell(df, cell_60)[0]
    trade_240 = phase110.generate_signals_for_cell(df, cell_240)[0]

    assert trade_60["exit_index"] - trade_60["entry_index"] == 60
    assert trade_240["exit_index"] - trade_240["entry_index"] == 240
    assert 480 not in phase110.EXIT_HORIZON_MIN


def test_holm_padding_uses_m_prime_20() -> None:
    rows = [{"p_raw": p, "cell_id": f"c{i}"} for i, p in enumerate([0.01] * 8)]
    metadata = phase110.apply_holm_per_pair(rows)
    assert metadata["m_prime"] == 20
    assert metadata["n_tested"] == 8
    assert metadata["n_padded"] == 12
    assert metadata["p_raw_padded"][8:] == [1.0] * 12


def test_per_pair_artifacts_do_not_pool_assets() -> None:
    summary = phase110.build_summary_doc(
        {
            "BTCUSD": {"rows": [], "holm": {"phase110_is_kill_failed": True}},
            "ETHUSD": {"rows": [], "holm": {"phase110_is_kill_failed": True}},
        }
    )
    assert set(summary["pairs"]) == {"BTCUSD", "ETHUSD"}
    encoded = str(summary).lower()
    assert "pooled" not in encoded
    assert "portfolio" not in encoded
    assert "cross_asset" not in encoded


def test_best_profit_factor_prefers_infinite_pf() -> None:
    rows = [
        {
            "cell_id": "finite",
            "profit_factor": 4.0,
            "profit_factor_is_infinite": False,
            "p_adj_holm": 0.5,
        },
        {
            "cell_id": "infinite",
            "profit_factor": None,
            "profit_factor_is_infinite": True,
            "p_adj_holm": 0.6,
        },
    ]
    summary = phase110.build_summary_doc(
        {
            "BTCUSD": {
                "rows": rows,
                "holm": {"phase110_is_kill_failed": False},
            }
        }
    )
    assert summary["pairs"]["BTCUSD"]["best_profit_factor_cell"] == "infinite"


def test_no_asian_window_cells_emitted() -> None:
    cells = phase110.generate_cells("BTCUSD") + phase110.generate_cells("ETHUSD")
    anchors = {c["window_anchor_utc"] for c in cells}
    assert anchors == {"21:00:00"}
    assert "00:00:00" not in anchors
    assert "15:00:00" not in anchors


def test_fee_inflated_pf_gate_fields() -> None:
    row = phase110.run_cell_backtest(_fixture(), phase110.generate_cells("BTCUSD")[0])
    for key in (
        "fee_bps_roundtrip",
        "profit_factor",
        "pass_is_pf",
        "profit_factor_is_infinite",
    ):
        assert key in row
