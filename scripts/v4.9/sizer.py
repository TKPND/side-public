"""Kelly fraction sizing layer (Phase 87 Layer 2).

Implements SEAL-02 (Jeffreys Beta + BCa bootstrap) and SIZE-01/02/03 AC.
SEAL constants loaded from .planning/phases/85-pre-registration-seal/85-SEAL/.
"""
from __future__ import annotations

import json
import math
import pathlib
from dataclasses import dataclass
from enum import Enum

import numpy as np
import polars as pl
from scipy.stats import beta, bootstrap

# ---- SEAL constants (loaded lazily; literal fallback for hash stability) ----
SIZING_EXIT_COMMIT = "8a4e49d2000b08e9e1b93b5f9f0de661d5dff7613d8dfc8339313452a3b81fab"

_SEAL_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / ".planning"
    / "phases"
    / "85-pre-registration-seal"
    / "85-SEAL"
)
_kelly_bounds = (
    json.loads((_SEAL_DIR / "kelly_bounds.json").read_text())
    if (_SEAL_DIR / "kelly_bounds.json").exists()
    else {}
)
_dd_cap = (
    json.loads((_SEAL_DIR / "dd_cap.json").read_text())
    if (_SEAL_DIR / "dd_cap.json").exists()
    else {}
)
_exit_commit = (
    json.loads((_SEAL_DIR / "exit_commit.json").read_text())
    if (_SEAL_DIR / "exit_commit.json").exists()
    else {}
)

KELLY_FRACTION_MIN: float = _kelly_bounds.get("kelly_fraction_min", 0.25)
KELLY_FRACTION_MAX: float = _kelly_bounds.get("kelly_fraction_max", 0.50)
BCA_BOOTSTRAP_N: int = _kelly_bounds.get("b_bootstrap_n", 2000)
BCA_BOOTSTRAP_SEED: int = _kelly_bounds.get("b_bootstrap_seed", 20260422)
ATR_K_PRIMARY: float = _exit_commit.get("atr_k_primary", 2.0)
# Pitfall 4: risk_pct = dd_cap.json::step_down_risk_pct[0] = 0.75 is the
# *default* (no step-down). Phase 88 will override dynamically.
DEFAULT_RISK_PCT: float = _dd_cap.get("step_down_risk_pct", [0.75])[0]


class KellyOverflow(ValueError):
    """Raised when fraction is outside [0.0, 0.5] or is NaN."""


class InsufficientData(ValueError):
    """Raised when trade count or distribution is too small to estimate (p, b)."""


@dataclass(frozen=True, slots=True)
class FractionalKelly:
    value: float

    def __post_init__(self) -> None:
        if math.isnan(self.value) or self.value < 0.0 or self.value > KELLY_FRACTION_MAX:
            raise KellyOverflow(f"fraction={self.value} outside [0.0, {KELLY_FRACTION_MAX}]")

    @classmethod
    def try_new(cls, f: float) -> "FractionalKelly":
        return cls(f)


@dataclass
class KellyInputs:
    p_lower: float
    b_lower: float
    n: int
    k: int

    def __post_init__(self) -> None:
        if self.n < 5:
            raise InsufficientData(f"min trades = 5, got n={self.n}")
        if not (0.0 < self.p_lower < 1.0):
            raise ValueError(f"p_lower={self.p_lower} outside (0, 1)")
        if self.b_lower <= 0.0:
            raise ValueError(f"b_lower={self.b_lower} not > 0")


class BindingReason(str, Enum):
    KELLY = "kelly"
    ATR_NORM = "atr_norm"
    FIXED_CAP = "fixed_cap"
    ZERO = "zero"


def _b_lower_bca(
    wins: np.ndarray,
    losses_abs: np.ndarray,
    *,
    seed: int = BCA_BOOTSTRAP_SEED,
) -> float:
    """BCa bootstrap CI lower bound for b = gross_profit / abs(gross_loss).

    Resamples on log(b); returns exp(CI.low). gross_loss==0 sentinel per Pitfall 2.
    losses_abs may be raw negative values; abs() is applied internally for robustness.
    """
    if len(wins) == 0 or len(losses_abs) == 0:
        raise InsufficientData("BCa requires both wins and losses to be non-empty")

    def log_b_stat(w: np.ndarray, l: np.ndarray) -> float:
        gp = float(np.sum(w))
        gl = float(np.sum(np.abs(l)))  # defend against raw negative input
        if gl == 0.0:
            return float(np.log(np.finfo(float).max))
        return float(np.log(gp / gl))

    res = bootstrap(
        (wins, losses_abs),
        statistic=log_b_stat,
        n_resamples=BCA_BOOTSTRAP_N,
        method="BCa",
        random_state=np.random.default_rng(seed),
        paired=False,
        vectorized=False,
    )
    return float(np.exp(res.confidence_interval.low))


def estimate_kelly_inputs(trades_df: pl.DataFrame) -> KellyInputs:
    """Compute (p_lower, b_lower) per SEAL-02 for a fold x cell trade set."""
    wins_df = trades_df.filter(pl.col("pnl") > 0)
    losses_df = trades_df.filter(pl.col("pnl") < 0)
    n = trades_df.height
    k = wins_df.height

    if n < 5:
        raise InsufficientData(f"min trades = 5, got n={n}")
    if k < 2 or losses_df.height < 2:
        raise InsufficientData(
            f"BCa bootstrap requires >=2 wins and >=2 losses; got wins={k}, losses={losses_df.height}"
        )

    p_lower = float(beta.ppf(0.05, 0.5 + k, 0.5 + n - k))
    wins = wins_df["pnl"].to_numpy()
    losses_abs = np.abs(losses_df["pnl"].to_numpy())
    b_lower = _b_lower_bca(wins, losses_abs, seed=BCA_BOOTSTRAP_SEED)
    return KellyInputs(p_lower=p_lower, b_lower=b_lower, n=n, k=k)


def compute_position_size(
    kelly_inputs: KellyInputs,
    atr_at_entry: float,
    equity: float,
    kelly_fraction: FractionalKelly,
    dd_cap_abs: float,
    *,
    risk_pct: float = DEFAULT_RISK_PCT,
    k_atr: float = ATR_K_PRIMARY,
) -> tuple[float, BindingReason]:
    if atr_at_entry <= 0 or equity <= 0:
        return 0.0, BindingReason.ZERO

    kelly_size = equity * kelly_fraction.value
    atr_size = equity * risk_pct / (k_atr * atr_at_entry)
    cap_size = equity * dd_cap_abs

    candidates = [
        (kelly_size, BindingReason.KELLY),
        (atr_size, BindingReason.ATR_NORM),
        (cap_size, BindingReason.FIXED_CAP),
    ]
    size, binding = min(candidates, key=lambda x: x[0])

    if size <= 0.0:
        return 0.0, BindingReason.ZERO
    return float(size), binding
