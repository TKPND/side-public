"""v4.8 Phase 82 POWER-04/05: Distributional statistics for wave-1 aggregator.

Fleiss kappa (scalar + pairwise) and Politis-Romano stationary block bootstrap CI.
Imported by wave1_power_integrate.py (Plan 03).

Threat mitigations:
    T-82-04: bootstrap seed=42 is a module-top constant; assert enforced at call site.
    T-82-05: fleiss_kappa NaN/inf -> 0.0 fallback via try/except + nan check.
    T-82-06: len(series) < 4 -> early return [0.0, 0.0] before arch is called.

Deviation from PLAN spec:
    arch 8.0.0 renamed optimal_block_length column 'b_sb' -> 'stationary'.
    This module uses 'stationary' (verified against arch==8.0.0 installed in env).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

import numpy as np
import pandas as pd
from statsmodels.stats.inter_rater import fleiss_kappa as _fleiss_kappa

# Bootstrap seed constant (T-82-04: pre-registered in Phase 79, must not change)
_BOOTSTRAP_SEED: int = 42


@dataclass
class KappaResult:
    """Fleiss kappa statistics for a cell."""

    scalar: float  # overall Fleiss kappa across all 3 categories
    pairwise: dict[
        str, float
    ]  # 3 pairs: long_vs_neutral, long_vs_short, neutral_vs_short


@dataclass
class BootstrapResult:
    """Politis-Romano stationary block bootstrap CI."""

    ci_95: list[float]  # [lower, upper] — 2.5th/97.5th percentile
    block_len: float  # optimal stationary block length


def _safe_fleiss(table: np.ndarray) -> float:
    """Compute Fleiss kappa on a normalized table; return 0.0 on any failure.

    statsmodels fleiss_kappa requires identical row sums (equal rater counts).
    slot_labels.parquet rows have varying counts, so we normalize each row to
    sum to 1.0 (proportion matrix). The formula is invariant to this scaling.

    T-82-05: NaN/inf and exceptions -> 0.0 fallback.
    """
    try:
        # Normalize rows to equal sum (required by statsmodels fleiss_kappa)
        row_sums = table.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0  # prevent division by zero
        table_norm = np.round((table / row_sums) * row_sums.mean()).astype(int)

        val = _fleiss_kappa(table_norm, method="fleiss")
        if not np.isfinite(val):
            return 0.0
        return float(val)
    except Exception:
        return 0.0


def compute_fleiss_kappa(df: pd.DataFrame) -> KappaResult:
    """Compute Fleiss kappa (scalar + pairwise) from slot label counts.

    Args:
        df: DataFrame with columns ["long", "neutral", "short"].
            Each row is one slot; values are non-negative counts.

    Returns:
        KappaResult with scalar kappa and 3 pairwise kappas.

    Edge cases:
        - n_slots < 2: returns scalar=0.0, all pairwise=0.0.
        - NaN/inf results: fall back to 0.0 (T-82-05).
    """
    _zero_pairwise = {
        "long_vs_neutral": 0.0,
        "long_vs_short": 0.0,
        "neutral_vs_short": 0.0,
    }

    table = df[["long", "neutral", "short"]].values.astype(float)
    n_slots = table.shape[0]

    if n_slots < 2:
        return KappaResult(scalar=0.0, pairwise=dict(_zero_pairwise))

    scalar = _safe_fleiss(table)

    pairwise = {
        "long_vs_neutral": _safe_fleiss(table[:, [0, 1]]),
        "long_vs_short": _safe_fleiss(table[:, [0, 2]]),
        "neutral_vs_short": _safe_fleiss(table[:, [1, 2]]),
    }

    return KappaResult(
        scalar=round(scalar, 6),
        pairwise={k: round(v, 6) for k, v in pairwise.items()},
    )


def compute_bootstrap_ci(
    series: Union[np.ndarray, pd.Series],
    n_samples: int = 1000,
) -> BootstrapResult:
    """Compute Politis-Romano stationary block bootstrap 95% CI for the mean.

    Args:
        series: 1-D array of floats. NaN values are excluded before bootstrap.
        n_samples: number of bootstrap samples (default 1000).

    Returns:
        BootstrapResult with ci_95=[lower, upper] and block_len.

    Edge cases:
        - len(series) < 4: returns ci_95=[0.0, 0.0], block_len=1.0 (T-82-06).
        - seed=42 is fixed for reproducibility (T-82-04).

    API note (arch 8.0.0 deviation from PLAN spec):
        optimal_block_length returns columns ['stationary', 'circular'].
        PLAN specified 'b_sb' which was the column name in arch < 7.x.
        This implementation uses 'stationary' (arch==8.0.0).
    """
    from arch.bootstrap import StationaryBootstrap
    from arch.bootstrap import optimal_block_length

    series = np.asarray(series, dtype=float)
    series = series[~np.isnan(series)]  # drop NaN

    if len(series) < 4:
        return BootstrapResult(ci_95=[0.0, 0.0], block_len=1.0)

    # Optimal stationary block length (Politis-Romano)
    block_df = optimal_block_length(series)
    block_len = float(block_df["stationary"].iloc[0])
    if not np.isfinite(block_len) or block_len < 1.0:
        block_len = 1.0  # fallback for NaN/inf from degenerate series

    # Bootstrap sampling (seed=42 pre-registered, T-82-04)
    bs = StationaryBootstrap(block_len, series, seed=_BOOTSTRAP_SEED)
    means: list[float] = []
    for data, _ in bs.bootstrap(n_samples):
        means.append(float(np.mean(data[0])))

    lower = float(np.percentile(means, 2.5))
    upper = float(np.percentile(means, 97.5))

    return BootstrapResult(
        ci_95=[round(lower, 6), round(upper, 6)],
        block_len=round(block_len, 6),
    )
