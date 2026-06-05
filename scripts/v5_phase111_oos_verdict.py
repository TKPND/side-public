"""Phase 111 OOS, permutation-null, DSR, and final verdict artifacts."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import kurtosis, norm, skew

import v5_phase110_signal_is as phase110

PAIRS = phase110.PAIRS
REPORT_DIR = Path("reports/v5.0/phase111")
DOCS_VERDICT = Path("docs/v5.0_phase1_b_verdict.md")
CLAIM_DOC = phase110.CLAIM_DOC

OOS_START = "2025-11-01T00:00:00Z"
OOS_END_EXCLUSIVE = "2026-05-01T00:00:00Z"
OOS_END_DISPLAY = "2026-04-30"
OOS_PF_HURDLE = 1.5
PERMUTATION_B = 2000
PERMUTATION_SEED = 20260430
DSR_N_TRIALS = 20
DSR_ALPHA = 0.05
DSR_PROBABILITY_THRESHOLD = 0.95
SHUFFLE_UNIT = "stance_label"
FIXED_PERMUTATION_FIELDS = [
    "entry_ts",
    "entry_close",
    "exit_close",
    "exit_horizon_min",
]

_EPS = 1e-12


def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def _profit_factor(pnl_bps: list[float] | np.ndarray) -> dict[str, Any]:
    arr = np.asarray(pnl_bps, dtype=float)
    arr = arr[np.isfinite(arr)]
    gross_profit = float(np.sum(arr[arr > 0.0])) if len(arr) else 0.0
    gross_loss = float(abs(np.sum(arr[arr < 0.0]))) if len(arr) else 0.0
    is_infinite = bool(gross_loss <= _EPS and gross_profit > 0.0)
    if len(arr) == 0:
        pf: float | None = None
        pf_value = 0.0
    elif is_infinite:
        pf = None
        pf_value = float("inf")
    elif gross_loss > 0.0:
        pf = float(gross_profit / gross_loss)
        pf_value = pf
    else:
        pf = 0.0
        pf_value = 0.0
    return {
        "num_trades": int(len(arr)),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": pf,
        "profit_factor_value": pf_value,
        "profit_factor_is_infinite": is_infinite,
    }


def filter_oos(df: pl.DataFrame) -> pl.DataFrame:
    """Filter rows to the sealed OOS window: start inclusive, end exclusive."""
    start_ns = phase110._iso_to_ns(OOS_START)
    end_ns = phase110._iso_to_ns(OOS_END_EXCLUSIVE)
    return (
        df.with_columns(pl.col("datetime_ns").cast(pl.Int64))
        .filter((pl.col("datetime_ns") >= start_ns) & (pl.col("datetime_ns") < end_ns))
        .sort("datetime_ns")
    )


def load_pair_oos(pair: str) -> pl.DataFrame:
    df = phase110.load_pair_ohlcv.__globals__["pl"].concat(
        [pl.read_csv(path) for path in phase110._pair_files(pair)]
    )
    out = filter_oos(df)
    if out.is_empty():
        raise RuntimeError(f"{pair} OHLCV has no rows in OOS window")
    return out


def _pnl_from_direction(trade: dict[str, Any], direction: int) -> float:
    entry = float(trade["entry_close"])
    exit_ = float(trade["exit_close"])
    if entry <= 0.0:
        return float("nan")
    gross = int(direction) * (exit_ - entry) / entry * 10_000.0
    return float(gross - phase110.FEE_BPS_ROUNDTRIP)


def _pf_for_percentile(pnl_bps: list[float]) -> float:
    metrics = _profit_factor(pnl_bps)
    value = float(metrics["profit_factor_value"])
    if math.isinf(value):
        return 1e12
    return value


def build_permutation_gate(
    trades: list[dict[str, Any]],
    observed_pf: float | None = None,
    b_samples: int = PERMUTATION_B,
    seed: int = PERMUTATION_SEED,
) -> dict[str, Any]:
    """Run stance-label shuffle while keeping timestamps/prices/horizons fixed."""
    if not trades:
        return {
            "passed": False,
            "reason": "insufficient_trades",
            "permutation_b": int(b_samples),
            "permutation_seed": int(seed),
            "shuffle_unit": SHUFFLE_UNIT,
            "fixed_fields": FIXED_PERMUTATION_FIELDS,
            "observed_pf": observed_pf,
            "null_profit_factors": [],
            "null_p95": None,
        }

    directions = np.array([int(t["direction"]) for t in trades], dtype=int)
    if observed_pf is None:
        observed_pf = _profit_factor([float(t["pnl_bps"]) for t in trades])[
            "profit_factor_value"
        ]
    observed_value = 1e12 if math.isinf(float(observed_pf)) else float(observed_pf)

    rng = np.random.default_rng(seed)
    null_values: list[float] = []
    for _ in range(int(b_samples)):
        shuffled = rng.permutation(directions)
        pnl = [_pnl_from_direction(trade, direction) for trade, direction in zip(trades, shuffled)]
        null_values.append(_pf_for_percentile(pnl))

    null_p95 = float(np.percentile(null_values, 95))
    passed = bool(observed_value > null_p95)
    reason = "passed" if passed else "observed_pf_not_strictly_greater_than_null_p95"
    return {
        "passed": passed,
        "reason": reason,
        "permutation_b": int(b_samples),
        "permutation_seed": int(seed),
        "shuffle_unit": SHUFFLE_UNIT,
        "fixed_fields": FIXED_PERMUTATION_FIELDS,
        "observed_pf": _finite_or_none(observed_pf),
        "observed_pf_is_infinite": bool(math.isinf(float(observed_pf))),
        "null_p95": _finite_or_none(null_p95),
        "null_profit_factors": [_finite_or_none(v) for v in null_values],
    }


def compute_dsr_gate(
    returns_bps: list[float] | np.ndarray,
    n_trials: int = DSR_N_TRIALS,
    alpha: float = DSR_ALPHA,
) -> dict[str, Any]:
    """Compute a fail-closed Deflated Sharpe Ratio style probability gate."""
    arr = np.asarray(returns_bps, dtype=float)
    base = {
        "passed": False,
        "dsr_n_trials": int(n_trials),
        "dsr_alpha": float(alpha),
        "dsr_probability_threshold": DSR_PROBABILITY_THRESHOLD,
    }
    if len(arr) < 2:
        return {**base, "reason": "insufficient_trades", "dsr_probability": None}
    if not np.all(np.isfinite(arr)):
        return {**base, "reason": "non_finite_returns", "dsr_probability": None}

    std = float(np.std(arr, ddof=1))
    if std <= _EPS:
        return {**base, "reason": "zero_variance", "dsr_probability": None}

    n = len(arr)
    sr = float(np.mean(arr) / std)
    gamma = 0.5772156649015329
    expected_max_sr = float(
        (1.0 - gamma) * norm.ppf(1.0 - 1.0 / n_trials)
        + gamma * norm.ppf(1.0 - 1.0 / (math.e * n_trials))
    )
    sk = float(skew(arr, bias=False)) if n > 2 else 0.0
    ku = float(kurtosis(arr, fisher=False, bias=False)) if n > 3 else 3.0
    denom = math.sqrt(max(_EPS, 1.0 - sk * sr + ((ku - 1.0) / 4.0) * sr * sr))
    z = (sr - expected_max_sr) * math.sqrt(max(1, n - 1)) / denom
    probability = float(norm.cdf(z))
    passed = bool(probability > DSR_PROBABILITY_THRESHOLD)
    return {
        **base,
        "passed": passed,
        "reason": "passed" if passed else "dsr_probability_not_above_threshold",
        "num_trades": int(n),
        "sharpe_ratio": sr,
        "expected_max_sharpe": expected_max_sr,
        "skew": sk,
        "kurtosis": ku,
        "dsr_probability": probability,
    }


def run_cell_oos(df: pl.DataFrame, cell: dict[str, Any]) -> dict[str, Any]:
    trades = phase110.generate_signals_for_cell(df, cell)
    pnl = [float(t["pnl_bps"]) for t in trades]
    pf = _profit_factor(pnl)
    observed_pf = float(pf["profit_factor_value"])
    permutation_gate = build_permutation_gate(trades, observed_pf=observed_pf)
    dsr_gate = compute_dsr_gate(pnl)
    row = {
        **cell,
        "oos_start": OOS_START,
        "oos_end_exclusive": OOS_END_EXCLUSIVE,
        "oos_end_display": OOS_END_DISPLAY,
        "num_trades": pf["num_trades"],
        "gross_profit": pf["gross_profit"],
        "gross_loss": pf["gross_loss"],
        "profit_factor": pf["profit_factor"],
        "profit_factor_is_infinite": pf["profit_factor_is_infinite"],
        "oos_pf_hurdle": OOS_PF_HURDLE,
        "oos_pf_passed": bool(
            pf["num_trades"] > 0
            and (
                pf["profit_factor_is_infinite"]
                or float(pf["profit_factor"] or 0.0) >= OOS_PF_HURDLE
            )
        ),
        "fee_bps_roundtrip": phase110.FEE_BPS_ROUNDTRIP,
        "mean_pnl_bps": float(np.mean(pnl)) if pnl else None,
        "permutation_gate": permutation_gate,
        "dsr_gate": dsr_gate,
        "trades": trades,
        "provenance": {
            "claim_doc": str(CLAIM_DOC),
            "phase110_geometry": "read-only import from scripts/v5_phase110_signal_is.py",
            "git_commit": phase110._git_commit(),
            "timestamp_semantics": (
                "OOS rows are >= 2025-11-01T00:00:00Z and < "
                "2026-05-01T00:00:00Z; 2026-04-30 is fully included"
            ),
        },
    }
    return row


def run_pair(pair: str) -> dict[str, Any]:
    df = load_pair_oos(pair)
    rows = [run_cell_oos(df, cell) for cell in phase110.generate_cells(pair)]
    return {"pair": pair, "rows": rows}


def _load_phase110_summary(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("phase") != 110 or "pairs" not in doc:
        raise ValueError(f"invalid Phase 110 summary: {path}")
    for pair in PAIRS:
        item = doc["pairs"].get(pair)
        if not isinstance(item, dict):
            raise ValueError(f"missing Phase 110 pair block: {pair}")
        for key in (
            "phase110_is_pf_passed",
            "phase110_is_fwer_passed",
            "phase110_is_kill_failed",
        ):
            if key not in item:
                raise ValueError(f"missing Phase 110 {pair}.{key}")
    return doc


def _pair_gate_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "any_oos_pf_passed": any(bool(r["oos_pf_passed"]) for r in rows),
        "any_permutation_passed": any(
            bool(r["permutation_gate"]["passed"]) for r in rows
        ),
        "any_dsr_passed": any(bool(r["dsr_gate"]["passed"]) for r in rows),
        "any_cell_all_phase111_gates_passed": any(
            bool(r["oos_pf_passed"])
            and bool(r["permutation_gate"]["passed"])
            and bool(r["dsr_gate"]["passed"])
            for r in rows
        ),
        "all_cells": [
            {
                "cell_id": r["cell_id"],
                "profit_factor": r.get("profit_factor"),
                "profit_factor_is_infinite": bool(
                    r.get("profit_factor_is_infinite", False)
                ),
                "oos_pf_passed": r["oos_pf_passed"],
                "permutation_passed": r["permutation_gate"]["passed"],
                "dsr_passed": r["dsr_gate"]["passed"],
            }
            for r in rows
        ],
    }


def build_final_verdict(
    pair_results: dict[str, dict[str, Any]],
    phase110_summary: dict[str, Any],
    phase110_summary_path: str,
) -> dict[str, Any]:
    pairs: dict[str, Any] = {}
    null_ship_reasons: list[str] = []
    any_phase110_failed = False

    for pair in PAIRS:
        phase110_pair = phase110_summary["pairs"][pair]
        rows = pair_results[pair]["rows"]
        gates = _pair_gate_summary(rows)
        phase110_failed = bool(phase110_pair["phase110_is_kill_failed"])
        any_phase110_failed = any_phase110_failed or phase110_failed
        pair_block = {
            "phase110_is_pf_passed": bool(phase110_pair["phase110_is_pf_passed"]),
            "phase110_is_fwer_passed": bool(phase110_pair["phase110_is_fwer_passed"]),
            "phase110_is_kill_failed": phase110_failed,
            **gates,
        }
        pairs[pair] = pair_block

        if phase110_failed:
            null_ship_reasons.append(f"phase110_is_kill_failed:{pair}")
        if not gates["any_oos_pf_passed"]:
            null_ship_reasons.append(f"oos_pf_failed:{pair}")
        if not gates["any_permutation_passed"]:
            null_ship_reasons.append(f"permutation_failed:{pair}")
        if not gates["any_dsr_passed"]:
            null_ship_reasons.append(f"dsr_failed:{pair}")
        if not gates["any_cell_all_phase111_gates_passed"]:
            null_ship_reasons.append(f"phase111_cell_all_gates_failed:{pair}")

    ship_verdict = len(null_ship_reasons) == 0
    return {
        "phase": 111,
        "schema_version": "v5.0.phase111.1",
        "ship_verdict": ship_verdict,
        "verdict": "ship" if ship_verdict else "null_ship",
        "null_ship_reasons": null_ship_reasons,
        "phase110": {
            "summary_path": phase110_summary_path,
            "any_phase110_is_kill_failed": any_phase110_failed,
        },
        "pairs": pairs,
        "provenance": {
            "git_commit": phase110._git_commit(),
            "claim_doc": str(CLAIM_DOC),
            "phase110_summary_path": phase110_summary_path,
            "oos_start": OOS_START,
            "oos_end_exclusive": OOS_END_EXCLUSIVE,
            "oos_end_display": OOS_END_DISPLAY,
            "fee_bps_roundtrip": phase110.FEE_BPS_ROUNDTRIP,
            "permutation_b": PERMUTATION_B,
            "permutation_seed": PERMUTATION_SEED,
            "permutation_shuffle_unit": SHUFFLE_UNIT,
            "dsr_n_trials": DSR_N_TRIALS,
            "dsr_alpha": DSR_ALPHA,
            "dsr_probability_threshold": DSR_PROBABILITY_THRESHOLD,
            "no_pooling": "BTCUSD and ETHUSD gates are evaluated independently.",
        },
    }


def _pair_doc(pair: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": 111,
        "pair": pair,
        "results": result["rows"],
        "provenance": {
            "claim_doc": str(CLAIM_DOC),
            "data_files": [str(path) for path in phase110._pair_files(pair)],
            "oos_start": OOS_START,
            "oos_end_exclusive": OOS_END_EXCLUSIVE,
            "oos_end_display": OOS_END_DISPLAY,
            "no_pooling": True,
        },
    }


def _permutation_doc(pair_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "phase": 111,
        "permutation_b": PERMUTATION_B,
        "permutation_seed": PERMUTATION_SEED,
        "shuffle_unit": SHUFFLE_UNIT,
        "strict_rule": "observed_pf > null_p95",
        "pairs": {
            pair: [
                {
                    "cell_id": row["cell_id"],
                    **row["permutation_gate"],
                }
                for row in pair_results[pair]["rows"]
            ]
            for pair in PAIRS
        },
    }


def _dsr_doc(pair_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "phase": 111,
        "dsr_n_trials": DSR_N_TRIALS,
        "dsr_alpha": DSR_ALPHA,
        "dsr_probability_threshold": DSR_PROBABILITY_THRESHOLD,
        "pairs": {
            pair: [
                {
                    "cell_id": row["cell_id"],
                    **row["dsr_gate"],
                }
                for row in pair_results[pair]["rows"]
            ]
            for pair in PAIRS
        },
    }


def _markdown_verdict(verdict: dict[str, Any]) -> str:
    lines = [
        "# v5.0 Phase 1 #B Verdict",
        "",
        f"ship_verdict | {verdict['ship_verdict']}",
        f"verdict | {verdict['verdict']}",
        "",
        "## Null-Ship Reasons",
        "",
    ]
    if verdict["null_ship_reasons"]:
        lines.extend(f"- `{reason}`" for reason in verdict["null_ship_reasons"])
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Per-Pair Gates",
            "",
            "| Pair | IS PF | IS FWER | IS KILL Failed | OOS PF | Permutation | DSR |",
            "|------|-------|---------|----------------|--------|-------------|-----|",
        ]
    )
    for pair in PAIRS:
        item = verdict["pairs"][pair]
        lines.append(
            "| {pair} | {is_pf} | {is_fwer} | {is_kill} | {oos} | {perm} | {dsr} |".format(
                pair=pair,
                is_pf=item["phase110_is_pf_passed"],
                is_fwer=item["phase110_is_fwer_passed"],
                is_kill=item["phase110_is_kill_failed"],
                oos=item["any_oos_pf_passed"],
                perm=item["any_permutation_passed"],
                dsr=item["any_dsr_passed"],
            )
        )
    lines.extend(
        [
            "",
            "Canonical source: `reports/v5.0/phase111/final_verdict.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(doc, indent=2, default=_json_default, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_outputs(
    pair_results: dict[str, dict[str, Any]],
    verdict: dict[str, Any],
    output_dir: Path = REPORT_DIR,
    docs_path: Path | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for pair in PAIRS:
        _write_json(output_dir / f"{pair.lower()}_oos_cells.json", _pair_doc(pair, pair_results[pair]))

    _write_json(output_dir / "permutation_null.json", _permutation_doc(pair_results))
    _write_json(output_dir / "dsr_summary.json", _dsr_doc(pair_results))
    _write_json(output_dir / "final_verdict.json", verdict)

    markdown = _markdown_verdict(verdict)
    (output_dir / "final_verdict.md").write_text(markdown, encoding="utf-8")
    if docs_path is not None:
        target_docs = docs_path
    elif output_dir == REPORT_DIR:
        target_docs = DOCS_VERDICT
    else:
        target_docs = output_dir / DOCS_VERDICT.name
    target_docs.parent.mkdir(parents=True, exist_ok=True)
    target_docs.write_text(markdown, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase110-summary",
        type=Path,
        default=Path("reports/v5.0/phase110/is_fwer_summary.json"),
    )
    parser.add_argument("--output-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args(argv)

    phase110_summary = _load_phase110_summary(args.phase110_summary)
    pair_results = {pair: run_pair(pair) for pair in PAIRS}
    verdict = build_final_verdict(
        pair_results,
        phase110_summary,
        phase110_summary_path=str(args.phase110_summary),
    )
    write_outputs(pair_results, verdict, output_dir=args.output_dir)
    print(f"wrote Phase 111 artifacts to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
