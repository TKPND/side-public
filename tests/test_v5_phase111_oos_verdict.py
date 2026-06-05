"""Phase 111 OOS, permutation-null, DSR, and verdict invariants."""

from __future__ import annotations

import json
import sys

import polars as pl

sys.path.insert(0, "scripts")
import v5_phase111_oos_verdict as phase111  # noqa: E402


def _ns(ts: str) -> int:
    return int(
        pl.Series([ts]).str.to_datetime(time_zone="UTC")[0].timestamp()
        * 1_000_000_000
    )


def test_filter_oos_uses_sealed_inclusive_exclusive_boundaries() -> None:
    df = pl.DataFrame(
        {
            "datetime_ns": [
                _ns("2025-10-31T23:59:00Z"),
                _ns("2025-11-01T00:00:00Z"),
                _ns("2026-04-30T23:59:00Z"),
                _ns("2026-05-01T00:00:00Z"),
            ],
            "close": [1.0, 2.0, 3.0, 4.0],
        }
    )

    out = phase111.filter_oos(df)

    assert phase111.OOS_START == "2025-11-01T00:00:00Z"
    assert phase111.OOS_END_EXCLUSIVE == "2026-05-01T00:00:00Z"
    assert out.get_column("close").to_list() == [2.0, 3.0]


def test_permutation_gate_uses_pinned_stance_shuffle_and_strict_tie_failure() -> None:
    trades = [
        {
            "entry_ts": "2025-11-01T21:05:00+00:00",
            "entry_close": 100.0,
            "exit_close": 102.0,
            "exit_horizon_min": 60,
            "direction": 1,
            "pnl_bps": 130.0,
        },
        {
            "entry_ts": "2025-11-02T21:05:00+00:00",
            "entry_close": 100.0,
            "exit_close": 98.0,
            "exit_horizon_min": 60,
            "direction": -1,
            "pnl_bps": 130.0,
        },
        {
            "entry_ts": "2025-11-03T21:05:00+00:00",
            "entry_close": 100.0,
            "exit_close": 101.0,
            "exit_horizon_min": 60,
            "direction": -1,
            "pnl_bps": -170.0,
        },
    ]

    gate = phase111.build_permutation_gate(
        trades,
        observed_pf=1.0,
        b_samples=8,
        seed=phase111.PERMUTATION_SEED,
    )
    tie = phase111.build_permutation_gate(
        trades,
        observed_pf=gate["null_p95"],
        b_samples=8,
        seed=phase111.PERMUTATION_SEED,
    )

    assert phase111.PERMUTATION_B == 2000
    assert phase111.PERMUTATION_SEED == 20260430
    assert gate["permutation_b"] == 8
    assert gate["permutation_seed"] == 20260430
    assert gate["shuffle_unit"] == "stance_label"
    assert gate["fixed_fields"] == [
        "entry_ts",
        "entry_close",
        "exit_close",
        "exit_horizon_min",
    ]
    assert len(gate["null_profit_factors"]) == 8
    assert tie["passed"] is False
    assert tie["reason"] == "observed_pf_not_strictly_greater_than_null_p95"


def test_dsr_gate_fails_closed_for_undefensible_return_series() -> None:
    assert phase111.DSR_N_TRIALS == 20
    assert phase111.DSR_ALPHA == 0.05
    assert phase111.DSR_PROBABILITY_THRESHOLD == 0.95

    cases = [
        ([], "insufficient_trades"),
        ([1.0], "insufficient_trades"),
        ([1.0, 1.0, 1.0], "zero_variance"),
        ([1.0, float("nan"), 2.0], "non_finite_returns"),
        ([1.0, float("inf"), 2.0], "non_finite_returns"),
    ]
    for returns, reason in cases:
        gate = phase111.compute_dsr_gate(returns)
        assert gate["passed"] is False
        assert gate["reason"] == reason
        assert gate["dsr_n_trials"] == 20


def test_final_verdict_propagates_phase110_failure_and_writes_from_canonical_json(
    tmp_path,
) -> None:
    phase110_summary = {
        "phase": 110,
        "pairs": {
            "BTCUSD": {
                "phase110_is_pf_passed": False,
                "phase110_is_fwer_passed": False,
                "phase110_is_kill_failed": True,
            },
            "ETHUSD": {
                "phase110_is_pf_passed": True,
                "phase110_is_fwer_passed": True,
                "phase110_is_kill_failed": False,
            },
        },
    }
    pair_results = {
        pair: {
            "pair": pair,
            "rows": [
                {
                    "cell_id": f"{pair}_cell",
                    "oos_pf_passed": True,
                    "permutation_gate": {"passed": True},
                    "dsr_gate": {"passed": True},
                }
            ],
        }
        for pair in ("BTCUSD", "ETHUSD")
    }

    verdict = phase111.build_final_verdict(
        pair_results,
        phase110_summary,
        phase110_summary_path="reports/v5.0/phase110/is_fwer_summary.json",
    )
    phase111.write_outputs(pair_results, verdict, output_dir=tmp_path)

    final_json = json.loads((tmp_path / "final_verdict.json").read_text())
    final_md = (tmp_path / "final_verdict.md").read_text()
    docs_md = (tmp_path / "v5.0_phase1_b_verdict.md").read_text()

    assert verdict["ship_verdict"] is False
    assert verdict["phase110"]["any_phase110_is_kill_failed"] is True
    assert "phase110_is_kill_failed:BTCUSD" in verdict["null_ship_reasons"]
    assert final_json == verdict
    assert "ship_verdict | False" in final_md
    assert "ship_verdict | False" in docs_md
    assert set(final_json["pairs"]) == {"BTCUSD", "ETHUSD"}


def test_final_verdict_requires_same_cell_to_pass_all_phase111_gates() -> None:
    phase110_summary = {
        "phase": 110,
        "pairs": {
            pair: {
                "phase110_is_pf_passed": True,
                "phase110_is_fwer_passed": True,
                "phase110_is_kill_failed": False,
            }
            for pair in ("BTCUSD", "ETHUSD")
        },
    }
    pair_results = {
        pair: {
            "pair": pair,
            "rows": [
                {
                    "cell_id": f"{pair}_oos_only",
                    "oos_pf_passed": True,
                    "permutation_gate": {"passed": False},
                    "dsr_gate": {"passed": False},
                },
                {
                    "cell_id": f"{pair}_perm_only",
                    "oos_pf_passed": False,
                    "permutation_gate": {"passed": True},
                    "dsr_gate": {"passed": False},
                },
                {
                    "cell_id": f"{pair}_dsr_only",
                    "oos_pf_passed": False,
                    "permutation_gate": {"passed": False},
                    "dsr_gate": {"passed": True},
                },
            ],
        }
        for pair in ("BTCUSD", "ETHUSD")
    }

    verdict = phase111.build_final_verdict(
        pair_results,
        phase110_summary,
        phase110_summary_path="phase110.json",
    )

    assert verdict["ship_verdict"] is False
    assert "phase111_cell_all_gates_failed:BTCUSD" in verdict["null_ship_reasons"]
    assert "phase111_cell_all_gates_failed:ETHUSD" in verdict["null_ship_reasons"]
