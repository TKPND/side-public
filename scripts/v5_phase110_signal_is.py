"""Phase 110 NY-close signal, IS backtest, and Holm FWER artifacts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from arch.bootstrap import StationaryBootstrap, optimal_block_length
from statsmodels.stats.multitest import multipletests

PAIRS = ("BTCUSD", "ETHUSD")
WINDOW_MIN = (5, 10)
THRESHOLD_BPS = (1.0, 2.0)
EXIT_HORIZON_MIN = (60, 240)
TIMEFRAME = "1m"
IS_START = "2024-05-01T00:00:00Z"
IS_END_EXCLUSIVE = "2025-11-01T00:00:00Z"
M_PRIME = 20
ALPHA = 0.05
BOOTSTRAP_SEED = 42
N_BOOTSTRAP_SAMPLES = 1000
FEE_BPS_ROUNDTRIP = 70.0
REPORT_DIR = Path("reports/v5.0/phase110")

SIGNAL_FAMILY = "ny_close_liquidity_vacuum_mean_reversion"
CLAIM_DOC = Path("docs/v5.0_phase1_b_claim.md")
DATA_DIR = Path("data/ohlcv")


def _iso_to_ns(ts: str) -> int:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1_000_000_000)


def _ns_to_iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).isoformat()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _pair_files(pair: str) -> list[Path]:
    lower = pair.lower()
    return [
        DATA_DIR / f"{lower}_1m_2024-05_2025-04.csv",
        DATA_DIR / f"{lower}_1m_2025-05_2026-04.csv",
    ]


def load_pair_ohlcv(pair: str) -> pl.DataFrame:
    """Load split 1m OHLCV CSVs for one pair and filter to the IS window."""
    if pair not in PAIRS:
        raise ValueError(f"unsupported pair: {pair}")

    frames = []
    for path in _pair_files(pair):
        if not path.exists():
            raise FileNotFoundError(path)
        frames.append(pl.read_csv(path))

    start_ns = _iso_to_ns(IS_START)
    end_ns = _iso_to_ns(IS_END_EXCLUSIVE)
    df = (
        pl.concat(frames)
        .with_columns(pl.col("datetime_ns").cast(pl.Int64))
        .filter(
            (pl.col("datetime_ns") >= start_ns)
            & (pl.col("datetime_ns") < end_ns)
        )
        .sort("datetime_ns")
    )
    if df.is_empty():
        raise RuntimeError(f"{pair} OHLCV has no rows in IS window")
    return df


def generate_cells(pair: str) -> list[dict[str, Any]]:
    """Return the sealed eight-cell Phase 110 grid for one pair."""
    if pair not in PAIRS:
        raise ValueError(f"unsupported pair: {pair}")

    cells: list[dict[str, Any]] = []
    for window_min in WINDOW_MIN:
        for threshold_bps in THRESHOLD_BPS:
            for exit_horizon_min in EXIT_HORIZON_MIN:
                cells.append(
                    {
                        "pair": pair,
                        "timeframe": TIMEFRAME,
                        "signal_family": SIGNAL_FAMILY,
                        "window_anchor_utc": "21:00:00",
                        "window_min": window_min,
                        "threshold_bps": threshold_bps,
                        "exit_horizon_min": exit_horizon_min,
                        "cell_id": (
                            f"{pair}_1m_2100_w{window_min:02d}_"
                            f"t{threshold_bps:.1f}_x{exit_horizon_min:03d}"
                        ),
                    }
                )
    return cells


def direction_for_move_bps(move_bps: float, threshold_bps: float) -> int:
    """Map reference-window move to sealed mean-reversion direction."""
    if abs(move_bps) < threshold_bps:
        return 0
    return -1 if move_bps > 0.0 else 1


def generate_signals_for_cell(
    df: pl.DataFrame, cell: dict[str, Any]
) -> list[dict[str, Any]]:
    """Generate deterministic 21:00 UTC mean-reversion trades.

    Timestamp semantics are fixed for Phase 110:
    - select the 1m bar whose timestamp is exactly 21:00:00 UTC as reference;
    - compare the reference close with the close exactly `window_min` bars later;
    - if the move magnitude reaches `threshold_bps`, enter at that window close
      in the opposite direction;
    - exit exactly `exit_horizon_min` rows after the entry row when available.
    """
    ns = df.get_column("datetime_ns").cast(pl.Int64).to_list()
    close = df.get_column("close").cast(pl.Float64).to_list()
    index_by_ns = {int(ts_ns): i for i, ts_ns in enumerate(ns)}

    trades: list[dict[str, Any]] = []
    window_delta = int(cell["window_min"]) * 60 * 1_000_000_000
    for ref_index, ref_ns in enumerate(ns):
        dt = datetime.fromtimestamp(int(ref_ns) / 1_000_000_000, tz=timezone.utc)
        if not (dt.hour == 21 and dt.minute == 0 and dt.second == 0):
            continue
        entry_ns = int(ref_ns) + window_delta
        entry_index = index_by_ns.get(entry_ns)
        if entry_index is None:
            continue
        exit_index = entry_index + int(cell["exit_horizon_min"])
        if exit_index >= len(close):
            continue

        ref_close = float(close[ref_index])
        entry_close = float(close[entry_index])
        if ref_close <= 0.0:
            continue
        move_bps = (entry_close - ref_close) / ref_close * 10_000.0
        direction = direction_for_move_bps(move_bps, float(cell["threshold_bps"]))
        if direction == 0:
            continue
        exit_close = float(close[exit_index])
        pnl_bps_gross = direction * (exit_close - entry_close) / entry_close * 10_000.0
        pnl_bps_net = pnl_bps_gross - FEE_BPS_ROUNDTRIP
        trades.append(
            {
                "cell_id": cell["cell_id"],
                "pair": cell["pair"],
                "window_anchor_utc": cell["window_anchor_utc"],
                "reference_index": ref_index,
                "entry_index": entry_index,
                "exit_index": exit_index,
                "reference_ts": _ns_to_iso(int(ref_ns)),
                "entry_ts": _ns_to_iso(int(ns[entry_index])),
                "exit_ts": _ns_to_iso(int(ns[exit_index])),
                "direction": direction,
                "move_bps": move_bps,
                "entry_close": entry_close,
                "exit_close": exit_close,
                "pnl_bps_gross": pnl_bps_gross,
                "pnl_bps": pnl_bps_net,
            }
        )
    return trades


def bootstrap_pvalue(
    pnl_bps: np.ndarray,
    n_samples: int = N_BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> float:
    """Compute deterministic H0-centered stationary bootstrap p-value."""
    arr = np.asarray(pnl_bps, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 4:
        return 1.0

    observed = float(arr.mean())
    if abs(observed) < 1e-12:
        return 1.0

    try:
        block_df = optimal_block_length(arr)
        block_len = float(block_df["stationary"].iloc[0])
        if not np.isfinite(block_len) or block_len < 1.0:
            block_len = 1.0
    except Exception:  # noqa: BLE001
        block_len = max(1.0, int(len(arr) ** (1 / 3)))

    bs = StationaryBootstrap(block_len, arr - observed, seed=seed)
    means = np.array([data[0][0].mean() for data, _ in bs.bootstrap(n_samples)])
    return float(np.mean(np.abs(means) >= abs(observed)))


def run_cell_backtest(df: pl.DataFrame, cell: dict[str, Any]) -> dict[str, Any]:
    """Run one fixed-horizon cell and return Phase 111-consumable metrics."""
    trades = generate_signals_for_cell(df, cell)
    pnl = np.array([t["pnl_bps"] for t in trades], dtype=float)
    gross_profit = float(np.sum(pnl[pnl > 0.0])) if len(pnl) else 0.0
    gross_loss = float(abs(np.sum(pnl[pnl < 0.0]))) if len(pnl) else 0.0
    profit_factor_is_infinite = bool(gross_loss <= 1e-12 and gross_profit > 0.0)
    if len(pnl) == 0:
        profit_factor: float | None = None
    elif profit_factor_is_infinite:
        profit_factor = None
    elif gross_loss > 0.0:
        profit_factor = float(gross_profit / gross_loss)
    else:
        profit_factor = 0.0

    pass_is_pf = bool(
        len(pnl) > 0
        and (
            profit_factor_is_infinite
            or (profit_factor is not None and profit_factor >= 2.0)
        )
    )
    row = {
        **cell,
        "is_start": IS_START,
        "is_end": IS_END_EXCLUSIVE,
        "num_trades": int(len(pnl)),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "profit_factor_is_infinite": profit_factor_is_infinite,
        "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "mean_pnl_bps": float(np.mean(pnl)) if len(pnl) else None,
        "p_raw": bootstrap_pvalue(pnl),
        "p_adj_holm": None,
        "pass_is_pf": pass_is_pf,
        "pass_fwer": False,
        "provenance": {
            "claim_doc": str(CLAIM_DOC),
            "threshold_commit": "45cd8a3",
            "timeframe": TIMEFRAME,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "n_bootstrap_samples": N_BOOTSTRAP_SAMPLES,
            "git_commit": _git_commit(),
            "timestamp_semantics": (
                "21:00 UTC reference close to window close; entry at window close; "
                "fixed N 1m-bar exit"
            ),
        },
    }
    return row


def apply_holm_per_pair(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply per-pair Holm correction with fixed Phase 110 m_prime padding."""
    if len(rows) != 8:
        raise AssertionError(f"expected 8 tested cells, got {len(rows)}")
    p_raw_tested = [float(row["p_raw"]) for row in rows]
    n_padded = M_PRIME - len(rows)
    p_raw_padded = p_raw_tested + [1.0] * n_padded
    _, p_adj, _, _ = multipletests(p_raw_padded, alpha=ALPHA, method="holm")
    p_adj_list = [float(x) for x in p_adj]

    for row, p_adj_holm in zip(rows, p_adj_list[: len(rows)]):
        row["p_adj_holm"] = p_adj_holm
        row["pass_fwer"] = bool(p_adj_holm < ALPHA)

    any_pf = any(bool(row.get("pass_is_pf", False)) for row in rows)
    any_fwer = any(bool(row.get("pass_fwer", False)) for row in rows)
    return {
        "m_prime": M_PRIME,
        "n_tested": len(rows),
        "n_padded": n_padded,
        "alpha": ALPHA,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "n_bootstrap_samples": N_BOOTSTRAP_SAMPLES,
        "p_raw_padded": p_raw_padded,
        "phase110_is_pf_passed": any_pf,
        "phase110_is_fwer_passed": any_fwer,
        "phase110_is_kill_failed": not (any_pf and any_fwer),
    }


def run_pair(pair: str) -> dict[str, Any]:
    df = load_pair_ohlcv(pair)
    rows = [run_cell_backtest(df, cell) for cell in generate_cells(pair)]
    holm = apply_holm_per_pair(rows)
    return {"pair": pair, "rows": rows, "holm": holm}


def build_summary_doc(results_by_pair: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build canonical machine summary without cross-pair aggregation metrics."""
    pairs: dict[str, Any] = {}
    for pair, result in results_by_pair.items():
        rows = result["rows"]
        best_pf = (
            max(
                rows,
                key=lambda r: (
                    float("inf")
                    if r["profit_factor_is_infinite"]
                    else float(r["profit_factor"] or 0.0)
                ),
            )
            if rows
            else None
        )
        best_fwer = min(rows, key=lambda r: float(r["p_adj_holm"])) if rows else None
        holm = result["holm"]
        pairs[pair] = {
            "m_prime": holm.get("m_prime", M_PRIME),
            "n_tested": holm.get("n_tested", len(rows)),
            "n_padded": holm.get("n_padded", M_PRIME - len(rows)),
            "alpha": holm.get("alpha", ALPHA),
            "phase110_is_pf_passed": holm.get("phase110_is_pf_passed", False),
            "phase110_is_fwer_passed": holm.get("phase110_is_fwer_passed", False),
            "phase110_is_kill_failed": holm["phase110_is_kill_failed"],
            "best_profit_factor_cell": best_pf["cell_id"] if best_pf else None,
            "best_fwer_cell": best_fwer["cell_id"] if best_fwer else None,
            "best_fwer_p_adj_holm": best_fwer["p_adj_holm"] if best_fwer else None,
        }
    return {
        "phase": 110,
        "scope": "IS-side KILL status only",
        "is_start": IS_START,
        "is_end": IS_END_EXCLUSIVE,
        "pairs": pairs,
        "provenance": {
            "claim_doc": str(CLAIM_DOC),
            "m_prime": M_PRIME,
            "n_tested_per_pair": 8,
            "n_padded_per_pair": 12,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "n_bootstrap_samples": N_BOOTSTRAP_SAMPLES,
            "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
            "git_commit": _git_commit(),
        },
    }


def _pair_doc(pair: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "pair": pair,
        "phase": 110,
        "results": result["rows"],
        "provenance": {
            "claim_doc": str(CLAIM_DOC),
            "data_files": [str(p) for p in _pair_files(pair)],
            **result["holm"],
        },
    }


def _markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# Phase 110 IS Backtest Summary",
        "",
        "Scope: IS-side KILL status only. Phase 111 owns OOS, permutation null, DSR, and final verdict.",
        "",
        f"IS window: `{IS_START}` inclusive through `{IS_END_EXCLUSIVE}` exclusive",
        f"FWER: Holm per pair with `m_prime={M_PRIME}` (`8` tested + `12` padded)",
        f"Fee: `{FEE_BPS_ROUNDTRIP}` bps round-trip",
        "",
        "| Pair | PF Gate | FWER Gate | IS KILL Failed | Best FWER Cell | Best p_adj_holm |",
        "|------|---------|-----------|----------------|----------------|-----------------|",
    ]
    for pair in PAIRS:
        item = summary["pairs"][pair]
        lines.append(
            "| {pair} | {pf} | {fwer} | {kill} | `{cell}` | {p_adj:.6f} |".format(
                pair=pair,
                pf=item["phase110_is_pf_passed"],
                fwer=item["phase110_is_fwer_passed"],
                kill=item["phase110_is_kill_failed"],
                cell=item["best_fwer_cell"],
                p_adj=float(item["best_fwer_p_adj_holm"]),
            )
        )
    lines.extend(
        [
            "",
            "Machine-readable artifacts in this directory are canonical for Phase 111.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    results_by_pair: dict[str, dict[str, Any]],
    output_dir: Path = REPORT_DIR,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for pair in PAIRS:
        path = output_dir / f"{pair.lower()}_is_cells.json"
        path.write_text(
            json.dumps(_pair_doc(pair, results_by_pair[pair]), indent=2) + "\n",
            encoding="utf-8",
        )

    summary = build_summary_doc(results_by_pair)
    (output_dir / "is_fwer_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "phase110_is_summary.md").write_text(
        _markdown_summary(summary),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args(argv)

    results = {pair: run_pair(pair) for pair in PAIRS}
    write_outputs(results, args.output_dir)
    print(f"wrote Phase 110 artifacts to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
