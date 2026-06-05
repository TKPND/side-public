"""De-risking overlay layer 4 (Phase 90 OVERLAY-01/02).

MtMultiplier newtype: value ∈ [0.0, 1.0], NaN/<0/>1 raise ValueError.
compute_mt: m_t = min(mt_upper_cap, exp(-alpha * z_t - beta * p_shift)).
α/β loaded exclusively from overlay_spec.json (D-25: hardcode forbidden).
Module-init sha256 drift check per D-14 atomic invariant.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
from dataclasses import dataclass

# ── D-14 module-init SEAL verify ────────────────────────────────────────────
_SEAL_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / ".planning"
    / "phases"
    / "88-pre-registration-seal-v4-10"
    / "88-SEAL"
)
_OVERLAY_SPEC_PATH = _SEAL_DIR / "overlay_spec.json"
_EXPECTED_OVERLAY_SPEC_SHA256 = (
    "2daedf3ffbdb6a98ba2e51aeaf57dbccfac8309fc7fc530c04079a6643ccc326"
)


def _verify_overlay_spec_hash() -> None:
    """D-14 atomic invariant — fail-close at import time on sha256 drift."""
    raw = _OVERLAY_SPEC_PATH.read_bytes()
    data = json.loads(raw)
    canonical = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    actual = hashlib.sha256(canonical).hexdigest()
    if actual != _EXPECTED_OVERLAY_SPEC_SHA256:
        raise RuntimeError(
            f"overlay_spec.json SEAL drift detected!\n"
            f"  expected: {_EXPECTED_OVERLAY_SPEC_SHA256}\n"
            f"  actual:   {actual}"
        )


_verify_overlay_spec_hash()  # import-time (fail-close)

# ── SEAL spec load (α/β hardcode forbidden, D-25) ───────────────────────────
_overlay_spec = json.loads(_OVERLAY_SPEC_PATH.read_bytes())
_ALPHA_PRIMARY: float = float(_overlay_spec["alpha_primary"])
_BETA_PRIMARY: float = float(_overlay_spec["beta_primary"])
_MT_UPPER_CAP: float = float(_overlay_spec["mt_upper_cap"])
_ALPHA_GRID: list[float] = [float(a) for a in _overlay_spec["alpha_fragility_grid"]]
_BETA_GRID: list[float] = [float(b) for b in _overlay_spec["beta_fragility_grid"]]

# ── OVERLAY-01: MtMultiplier newtype ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MtMultiplier:
    """Layer 4 overlay multiplier; value in [0.0, 1.0].

    Structural guarantee: NaN, value < 0.0, and value > 1.0 are all rejected
    at construction time (OVERLAY-01, T-90-02).
    """

    value: float

    def __post_init__(self) -> None:
        if math.isnan(self.value):
            raise ValueError("MtMultiplier: NaN rejected")
        if self.value < 0.0:
            raise ValueError(f"MtMultiplier: value={self.value} < 0.0 rejected")
        if self.value > 1.0:
            raise ValueError(f"MtMultiplier: value={self.value} > 1.0 rejected")


# ── OVERLAY-02: compute_mt exponential formula ──────────────────────────────


def compute_mt(
    z_t: float,
    p_shift: float,
    alpha: float,
    beta: float,
    mt_upper_cap: float,
) -> MtMultiplier:
    """m_t = min(mt_upper_cap, exp(-alpha * z_t - beta * p_shift)).

    α/β MUST come from overlay_spec.json — never hardcode in callers (D-25).
    Double-bounded: min() enforces cap, MtMultiplier.__post_init__ enforces [0,1].
    """
    raw = math.exp(-alpha * z_t - beta * p_shift)
    value = min(mt_upper_cap, raw)
    return MtMultiplier(value)


# ── OVERLAY-02: vectorized column generators ─────────────────────────────────

import sys as _sys

import numpy as np
import polars as pl

_sys.path.insert(0, str(pathlib.Path(__file__).parent))
from dd_gate import DrawDown, pick_risk_multiplier  # noqa: E402


def compute_z_t(df: pl.DataFrame, window: int = 20) -> pl.DataFrame:
    """Rolling standardized drawdown per (cell_id, fold_id) group.

    Pre-window bars → z_t = 0.0 (conservative pass-through).
    window=20 per 90-RESEARCH §Pattern 3.
    Uses min_samples (polars ≥1.21) with fallback to min_periods for older versions.
    """
    try:
        df2 = (
            df.sort(["cell_id", "fold_id", "bar_ts"])
            .with_columns(
                [
                    pl.col("dd_value")
                    .rolling_mean(window_size=window, min_samples=window)
                    .over(["cell_id", "fold_id"])
                    .alias("dd_rolling_mean"),
                    pl.col("dd_value")
                    .rolling_std(window_size=window, min_samples=window)
                    .over(["cell_id", "fold_id"])
                    .alias("dd_rolling_std"),
                ]
            )
        )
    except TypeError:
        # polars <1.21 fallback
        df2 = (
            df.sort(["cell_id", "fold_id", "bar_ts"])
            .with_columns(
                [
                    pl.col("dd_value")
                    .rolling_mean(window_size=window, min_periods=window)  # type: ignore[call-arg]
                    .over(["cell_id", "fold_id"])
                    .alias("dd_rolling_mean"),
                    pl.col("dd_value")
                    .rolling_std(window_size=window, min_periods=window)  # type: ignore[call-arg]
                    .over(["cell_id", "fold_id"])
                    .alias("dd_rolling_std"),
                ]
            )
        )
    return df2.with_columns(
        [
            pl.when(
                pl.col("dd_rolling_std").is_null() | (pl.col("dd_rolling_std") == 0.0)
            )
            .then(pl.lit(0.0))
            .otherwise(
                (pl.col("dd_value") - pl.col("dd_rolling_mean"))
                / pl.col("dd_rolling_std")
            )
            .alias("z_t")
        ]
    )


def compute_p_shift(df: pl.DataFrame, n_threshold: float = 5.0) -> pl.DataFrame:
    """p_shift = min(1.0, consecutive_loss_count / n_threshold).

    D-02 resolver: CONTEXT.md 候補 pool (a) rolling win-rate / (b) rolling Sharpe /
    (c) edge count decay / (d) consecutive_loss / N から (d) を採択。
    根拠は 90-RESEARCH.md §Pattern 4 — (i) dd_traces 既存 column 使用で新規計算ゼロ、
    (ii) β·p_shift ∈ [0.0, 1.0] が β_primary=1.0 magnitude と整合、
    (iii) 負け streak 単調 ≥ 0 で de-risk direction、
    (iv) H2 real lever 不使用。n_threshold=5.0 は 5 連敗で p_shift=1.0 に上限達する設定。
    """
    return df.with_columns(
        [
            (pl.col("consecutive_loss_count").cast(pl.Float64) / n_threshold)
            .clip(0.0, 1.0)
            .alias("p_shift")
        ]
    )


def compute_mt_column(
    df: pl.DataFrame,
    alpha: float,
    beta: float,
    mt_upper_cap: float,
) -> pl.DataFrame:
    """Vectorized m_t column. α/β MUST come from overlay_spec.json (D-25)."""
    z = df["z_t"].to_numpy()
    p = df["p_shift"].to_numpy()
    mt = np.minimum(mt_upper_cap, np.exp(-alpha * z - beta * p))
    return df.with_columns(pl.Series("m_t", mt))


def compose_multipliers(
    dd: DrawDown,
    z_t: float,
    p_shift: float,
    alpha: float,
    beta: float,
    mt_upper_cap: float,
) -> float:
    """final = L3_risk_multiplier × L4_mt. D-03 multiplicative composition.

    D-03 multiplicative composition; reused in future phases when Layer 3 × Layer 4
    integration is executed (Plan 02 は grid aggregate で直接呼ばないが、Plan 03
    stress replay / future sizing pipeline で primary entry point となる forward API)。
    """
    l3 = pick_risk_multiplier(dd)  # {0.0, 0.25, 0.5, 0.75, 1.0}
    l4 = compute_mt(z_t, p_shift, alpha, beta, mt_upper_cap)
    return l3 * l4.value  # ∈ [0.0, 1.0]


# ── OVERLAY-02: fragility grid ───────────────────────────────────────────────

import itertools


def _reconstruct_overlay_equity_group(
    group_df: pl.DataFrame,
) -> pl.DataFrame:
    """Pattern 6 chain: equity_on[t] = equity_on[t-1] + m_t[t]*(equity[t]-equity[t-1])."""
    equity = group_df["equity"].to_numpy()
    m_t = group_df["m_t"].to_numpy()
    n = len(equity)
    if n == 0:
        return group_df.with_columns(pl.Series("equity_on", np.empty(0)))
    eq_on = np.empty(n, dtype=float)
    eq_on[0] = equity[0]
    for t in range(1, n):
        delta = equity[t] - equity[t - 1]
        eq_on[t] = eq_on[t - 1] + m_t[t] * delta
    return group_df.with_columns(pl.Series("equity_on", eq_on))


def _per_cell_metrics(group_df: pl.DataFrame, equity_col: str) -> tuple[float, float]:
    """Per cell-fold metrics: PF (from equity deltas) + max_DD (from running peak)."""
    eq = group_df[equity_col].to_numpy()
    if len(eq) < 2:
        return (float("nan"), 0.0)
    deltas = np.diff(eq)
    pos = deltas[deltas > 0].sum()
    neg = -deltas[deltas < 0].sum()
    pf = float("inf") if neg == 0.0 else float(pos / neg)
    running_peak = np.maximum.accumulate(eq)
    dd_series = (eq - running_peak) / np.where(running_peak != 0, running_peak, 1.0)
    max_dd = float(dd_series.min()) if len(dd_series) else 0.0
    return (pf, max_dd)


def run_fragility_grid(
    dd_traces_path: pathlib.Path,
    alpha_grid: list[float] | None = None,
    beta_grid: list[float] | None = None,
) -> list[dict]:
    """α/β 25-point fragility grid: per-cell median PF / max_DD aggregate.

    α_grid / β_grid default to _ALPHA_GRID / _BETA_GRID from overlay_spec.json (D-25).
    """
    if alpha_grid is None:
        alpha_grid = _ALPHA_GRID
    if beta_grid is None:
        beta_grid = _BETA_GRID
    base_df = pl.read_parquet(dd_traces_path)
    base_df = compute_z_t(base_df, window=20)
    base_df = compute_p_shift(base_df, n_threshold=5.0)

    results: list[dict] = []
    for alpha, beta in itertools.product(alpha_grid, beta_grid):
        df_mt = compute_mt_column(base_df, alpha, beta, _MT_UPPER_CAP)
        pfs: list[float] = []
        dds: list[float] = []
        for (_cell, _fold), group in df_mt.group_by(["cell_id", "fold_id"]):
            group_sorted = group.sort("bar_ts")
            group_eq = _reconstruct_overlay_equity_group(group_sorted)
            pf, dd = _per_cell_metrics(group_eq, equity_col="equity_on")
            if not math.isnan(pf) and math.isfinite(pf):
                pfs.append(pf)
            dds.append(dd)
        results.append(
            {
                "alpha": alpha,
                "beta": beta,
                "pf_median": float(np.median(pfs)) if pfs else float("nan"),
                "max_dd_median": float(np.median(dds)) if dds else 0.0,
            }
        )
    return results


def emit_fragility_grid_csv(
    rows: list[dict],
    output_path: pathlib.Path,
) -> None:
    """Write fragility_grid.csv with columns: alpha, beta, pf_median, max_dd_median."""
    pl.DataFrame(rows).select(["alpha", "beta", "pf_median", "max_dd_median"]).write_csv(
        output_path
    )


# ── OVERLAY-03: stress cluster identification ────────────────────────────────

from datetime import date as _date

_EXPECTED_EVENTS: list[tuple[str, _date, _date]] = [
    ("2020-03 COVID", _date(2020, 1, 1), _date(2020, 6, 30)),
    ("2022 rate hike", _date(2022, 1, 1), _date(2022, 12, 31)),
    ("2024-08 JPY carry unwind", _date(2024, 7, 1), _date(2024, 10, 31)),
]


def identify_stress_clusters(
    df: pl.DataFrame,
    vol_window: int = 20,
    vol_p99_threshold: float | None = None,
    proximity_gap: int = 5,
) -> list[dict]:
    """Data-driven 3 cluster via rolling vol p99 + temporal proximity merge (D-04).

    date_window entries are plain YYYY-MM-DD strings (date portion only) to
    avoid TZ-aware datetime format issues in downstream annotation and filter.
    """
    base = (
        df.group_by("bar_ts")
        .agg(pl.col("dd_value").abs().mean().alias("dd_abs_mean"))
        .sort("bar_ts")
    )
    try:
        agg = base.with_columns(
            pl.col("dd_abs_mean")
            .rolling_std(window_size=vol_window, min_samples=vol_window)
            .alias("rvol")
        ).drop_nulls("rvol")
    except TypeError:
        agg = base.with_columns(
            pl.col("dd_abs_mean")
            .rolling_std(window_size=vol_window, min_periods=vol_window)  # type: ignore[call-arg]
            .alias("rvol")
        ).drop_nulls("rvol")
    if agg.height == 0:
        return []
    threshold = (
        vol_p99_threshold
        if vol_p99_threshold is not None
        else float(agg["rvol"].quantile(0.99))
    )
    flagged = agg.filter(pl.col("rvol") >= threshold).sort("bar_ts")
    if flagged.height == 0:
        return []

    bar_times = flagged["bar_ts"].to_list()
    rvols = flagged["rvol"].to_list()

    # Build clusters via temporal proximity (gap in days between consecutive flagged bars)
    clusters_raw: list[list[int]] = []
    for i in range(flagged.height):
        if not clusters_raw:
            clusters_raw.append([i])
            continue
        last_idx = clusters_raw[-1][-1]
        prev_ts = bar_times[last_idx]
        curr_ts = bar_times[i]
        # Handle both datetime and date types
        diff = curr_ts - prev_ts
        delta_days = diff.days if hasattr(diff, "days") else int(diff.total_seconds() / 86400)
        if delta_days <= proximity_gap:
            clusters_raw[-1].append(i)
        else:
            clusters_raw.append([i])

    # Sort by total n_bars desc, take top 3
    clusters_sorted = sorted(clusters_raw, key=lambda c: len(c), reverse=True)[:3]
    result: list[dict] = []
    for cid, indices in enumerate(clusters_sorted):
        start = bar_times[indices[0]]
        end = bar_times[indices[-1]]
        peak = max(rvols[j] for j in indices)
        # Extract date portion only (YYYY-MM-DD) — avoids TZ-aware isoformat issues
        start_str = start.date().isoformat() if hasattr(start, "date") else str(start)[:10]
        end_str = end.date().isoformat() if hasattr(end, "date") else str(end)[:10]
        result.append(
            {
                "cluster_id": cid,
                "date_window": (start_str, end_str),
                "peak_vol": float(peak),
                "n_bars": len(indices),
                "absent_event": False,
            }
        )
    return result


def annotate_stress_cluster(cluster: dict) -> dict:
    """Post-hoc annotate cluster to expected events (90-CONTEXT.md D-04).

    date_window must be (YYYY-MM-DD, YYYY-MM-DD) plain date strings.
    """
    out = dict(cluster)
    if cluster.get("absent_event"):
        return out
    dw = cluster.get("date_window")
    if not dw:
        out["expected_event_annotation"] = "unannotated"
        return out
    start_s, end_s = dw
    # Take first 10 chars to handle both "YYYY-MM-DD" and "YYYY-MM-DDT..." formats
    start = _date.fromisoformat(str(start_s)[:10])
    end = _date.fromisoformat(str(end_s)[:10])
    for label, e_start, e_end in _EXPECTED_EVENTS:
        if not (end < e_start or start > e_end):
            out["expected_event_annotation"] = label
            return out
    out["expected_event_annotation"] = "unannotated"
    return out


def pad_absent_events(
    clusters: list[dict],
    expected_labels: list[str],
) -> list[dict]:
    """If fewer than 3 clusters found (or expected labels unmatched), pad with
    absent_event placeholders (Pitfall 5 handling, Plan 03 VERIFICATION.md).
    """
    found_labels = {c.get("expected_event_annotation") for c in clusters}
    padded = list(clusters)
    next_cid = len(clusters)
    for lbl in expected_labels:
        if lbl not in found_labels and len(padded) < 3:
            padded.append(
                {
                    "cluster_id": next_cid,
                    "date_window": None,
                    "peak_vol": 0.0,
                    "n_bars": 0,
                    "absent_event": True,
                    "expected_event_annotation": lbl,
                }
            )
            next_cid += 1
    return padded


# ── OVERLAY-03: stress replay + ship_decision emit ───────────────────────────


def _calmar(eq: np.ndarray, max_dd: float) -> float:
    """Simple Calmar: total_return / abs(max_DD). Returns 0.0 if degenerate."""
    if len(eq) < 2 or max_dd == 0.0:
        return 0.0
    total_return = (eq[-1] / eq[0]) - 1.0 if eq[0] != 0 else 0.0
    return float(total_return / abs(max_dd))


def _cluster_metrics(df: pl.DataFrame, equity_col: str) -> dict:
    """Per-cell (PF, max_DD, calmar) → 192-cell median aggregate."""
    pfs: list[float] = []
    dds: list[float] = []
    calmars: list[float] = []
    for (_cell, _fold), group in df.group_by(["cell_id", "fold_id"]):
        g = group.sort("bar_ts")
        pf, dd = _per_cell_metrics(g, equity_col=equity_col)
        eq = g[equity_col].to_numpy()
        cal = _calmar(eq, dd)
        if math.isfinite(pf) and not math.isnan(pf):
            pfs.append(pf)
        dds.append(dd)
        calmars.append(cal)
    return {
        "pf_median": float(np.median(pfs)) if pfs else float("nan"),
        "max_dd_median": float(np.median(dds)) if dds else 0.0,
        "calmar_median": float(np.median(calmars)) if calmars else 0.0,
    }


def run_stress_replay(
    dd_traces_path: pathlib.Path,
    clusters: list[dict],
    alpha: float,
    beta: float,
    mt_upper_cap: float,
) -> dict:
    """D-05: per-cluster overlay on/off aggregate. No new backtest — post-hoc filter.

    bar_ts filter uses .dt.date() comparison to avoid TZ-aware datetime issues (advisor).
    """
    df = pl.read_parquet(dd_traces_path)
    df = compute_z_t(df, window=20)
    df = compute_p_shift(df, n_threshold=5.0)
    df = compute_mt_column(df, alpha, beta, mt_upper_cap)

    # Reconstruct overlay equity per (cell_id, fold_id) globally
    recon_frames = []
    for (_cell, _fold), group in df.group_by(["cell_id", "fold_id"]):
        recon_frames.append(_reconstruct_overlay_equity_group(group.sort("bar_ts")))
    df_recon = pl.concat(recon_frames) if recon_frames else df.with_columns(
        pl.col("equity").alias("equity_on")
    )

    stress_events: list[dict] = []
    off_metrics_accum: list[dict] = []
    on_metrics_accum: list[dict] = []

    for cluster in clusters:
        entry = annotate_stress_cluster(cluster)
        if entry.get("absent_event"):
            stress_events.append(entry)
            continue
        start_s, end_s = entry["date_window"]
        start_d = _date.fromisoformat(str(start_s)[:10])
        end_d = _date.fromisoformat(str(end_s)[:10])
        # Use .dt.date() comparison for TZ-aware bar_ts columns
        sub = df_recon.filter(
            (pl.col("bar_ts").dt.date() >= start_d)
            & (pl.col("bar_ts").dt.date() <= end_d)
        )
        off = _cluster_metrics(sub, equity_col="equity")
        on_ = _cluster_metrics(sub, equity_col="equity_on")
        entry["overlay_off_metrics"] = off
        entry["overlay_on_metrics"] = on_
        stress_events.append(entry)
        off_metrics_accum.append(off)
        on_metrics_accum.append(on_)

    def _agg(ms: list[dict], key: str) -> float:
        vals = [m[key] for m in ms if math.isfinite(m[key]) and not math.isnan(m[key])]
        return float(np.median(vals)) if vals else 0.0

    overlay_off = {
        "pf_median": _agg(off_metrics_accum, "pf_median"),
        "max_dd_median": _agg(off_metrics_accum, "max_dd_median"),
        "calmar_median": _agg(off_metrics_accum, "calmar_median"),
    }
    overlay_on = {
        "pf_median": _agg(on_metrics_accum, "pf_median"),
        "max_dd_median": _agg(on_metrics_accum, "max_dd_median"),
        "calmar_median": _agg(on_metrics_accum, "calmar_median"),
    }
    return {
        "stress_events": stress_events,
        "overlay_off": overlay_off,
        "overlay_on": overlay_on,
    }


def emit_ship_decision_json(
    overlay_eval: dict,
    output_path: pathlib.Path,
    quint_pin: dict,
) -> None:
    """D-06: section-separated schema. ship_metrics=null (Phase 91 reserved, never written here).

    T-90-12: ship_metrics is hardcoded None — cannot be overwritten by caller.
    """
    doc = {
        "schema_version": "v4.10.0",
        "overlay_evaluation": {
            **overlay_eval,
            "quint_pin_stamp": quint_pin,
        },
        "ship_metrics": None,  # Phase 91 reserved — absolute invariant
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
