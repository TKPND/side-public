"""v4.12 Phase 103 SHIP-V412-03: permutation null via STANCE label swap.

Logic-rewrite fork of v4.11 `scripts/v4.11/permutation_null.py` (D-01: shuffle
scope BUCKET → STANCE label). The v4.12 milestone null hypothesis is
"HAWK/DOV stance gating has no predictive power" — testing this directly
requires permuting the stance labels per cell while preserving outcomes
(per-cell p_raw stays the same, only the label re-assigned).

Pre-registered constants (Phase 103 septuple-pin override + carry):
    PERMUTATION_SEED = 20260601    (override of v4.11's 20260425)
    B_SAMPLES        = 2000        (carry from v4.11)
    ALPHA            = 0.05        (carry)
    SHUFFLE_UNIT     = "stance"    (D-01 logic rewrite, NOT 'cell'/'bucket')
    M_PRIME_V412     = 32          (re-imported from bootstrap_v412)

D-50 strict inequality: ship_condition_met := observed > p95 (NOT >=).
D-17 invariant: scripts/v4.11/permutation_null.py UNTOUCHED.
T-103-02 mitigation: no `from permutation_null import` — full fork, no
    bucket-shuffle vestigial code (`SHUFFLE_UNIT.*cell` grep gate clean).
NEUT exclusion: kill_set=True (NEUT-tagged) cells are excluded from the
    permutation pool. The current Phase 102 cells_post_compound_filter
    parquet does not carry a kill_set column; instead, NEUT exclusion is
    realised via pass_flag=True ∩ stance ∈ {HAWK, DOV} pre-filter.

Lehmann & Romano (2005) §15.2: permutation = relabeling within multiset
    → within-cell marginal HAWK/DOV count preserved across all B draws.

Graceful-degrade branch (Rule 3, mirrors bootstrap_v412):
    Phase 102 cells_post_compound_filter currently has 0 active cells
    (all pass_flag=False, only HAWK stance present). With zero active
    cells the permutation pool is empty; null distribution collapses to
    [0]*B and observed=0. ship_condition_met=False (0 > p95=0 is False
    by D-50 strict inequality). schema preserved; re-running this script
    after Phase 102 emits a non-degenerate parquet will produce a
    non-trivial distribution without code change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

# D-35 flat import path: scripts/v4.12 contains a dot, cannot be a package.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from bootstrap_v412 import (  # noqa: E402
    M_PRIME_V412,
    _SIGNAL_COMMIT_V412,
    apply_bonferroni_holm,
)

# ---------------------------------------------------------------------------
# Pre-registered constants (Phase 103 septuple-pin)
# ---------------------------------------------------------------------------
PERMUTATION_SEED: int = 20260601
B_SAMPLES: int = 2000
ALPHA: float = 0.05
# D-01 core logic rewrite: shuffle scope is the STANCE label per cell,
# NOT 'cell' (v4.11) and NOT 'bucket'. Plan 103-06 grep gate enforces.
SHUFFLE_UNIT: str = "stance"

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_CELLS_POST_COMPOUND_FILTER: Path = (
    _REPO_ROOT / "data" / "v4.12" / "cells_post_compound_filter.parquet"
)
_P_ADJ_V412: Path = _REPO_ROOT / "data" / "v4.12" / "p_adj_v412.json"
_OUTPUT: Path = _REPO_ROOT / "data" / "v4.12" / "permutation_null_v412.json"


# ---------------------------------------------------------------------------
# Per-cell p_raw loader (D-44 / D-46): tested-only, padded slots dropped.
# ---------------------------------------------------------------------------
def _load_per_cell_p_raw(p_adj_path: Path = _P_ADJ_V412) -> dict[str, float]:
    """Extract per-cell p_raw for *tested* cells from p_adj_v412.json.

    Padded rows (status='padded') are skipped; downstream permute_one()
    re-pads to M_PRIME_V412 each iteration. Returns {} for the full
    degenerate branch (n_tested=0).
    """
    if not p_adj_path.exists():
        return {}
    doc = json.loads(p_adj_path.read_text(encoding="utf-8"))
    return {
        r["cell_id"]: float(r["p_raw"])
        for r in doc.get("results", [])
        if r.get("status") == "tested" and r.get("p_raw") is not None
    }


# ---------------------------------------------------------------------------
# Active-cell loader (HAWK/DOV only, NEUT/kill_set excluded; pass_flag=True).
# ---------------------------------------------------------------------------
def _load_active_cells_and_stance(
    parquet_path: Path = _CELLS_POST_COMPOUND_FILTER,
) -> tuple[list[str], dict[str, str]]:
    """Read cells_post_compound_filter and emit the active permutation pool.

    Active = pass_flag == True AND stance ∈ {HAWK, DOV} AND (kill_set != True
    when the column exists). Returns (active_cells, stance_of). NEUT rows
    and kill_set=True rows are absolutely excluded (D-01 invariant).
    """
    if not parquet_path.exists():
        return [], {}
    df = pl.read_parquet(parquet_path)

    # pass_flag=True filter (column always present per Phase 102 schema).
    if "pass_flag" in df.columns:
        df = df.filter(pl.col("pass_flag") == True)  # noqa: E712

    # kill_set=True absolute exclusion (column may be absent in current
    # Phase 102 parquet; if so, NEUT exclusion falls to stance filter below).
    if "kill_set" in df.columns:
        df = df.filter(pl.col("kill_set") == False)  # noqa: E712

    # NEUT explicit drop. Only HAWK/DOV survives (D-01).
    if "stance" in df.columns:
        df = df.filter(pl.col("stance").is_in(["HAWK", "DOV"]))

    cells = df.get_column("cell_id").to_list() if "cell_id" in df.columns else []
    stances = df.get_column("stance").to_list() if "stance" in df.columns else []
    stance_of = dict(zip(cells, stances))
    return cells, stance_of


# ---------------------------------------------------------------------------
# Permutation core (D-01 stance label swap)
# ---------------------------------------------------------------------------
def _shuffle_stance_for_test(
    rng: np.random.Generator,
    active_cells: list[str],
    stance_of: dict[str, str],
) -> list[str]:
    """Test-friendly hook: returns the shuffled stance vector.

    Used by `test_within_cell_marginal_stance_preserved` to verify
    Lehmann & Romano (2005) §15.2 marginal preservation. permute_one()
    delegates the shuffle step here so that production logic and test
    logic share a single source of truth (no duplicate rng draws).
    """
    stances = [stance_of[c] for c in active_cells]
    return rng.permutation(stances).tolist()


def permute_one(
    rng: np.random.Generator,
    active_cells: list[str],
    stance_of: dict[str, str],
    per_cell_p_raw: dict[str, float],
) -> int:
    """Execute one permutation iteration with STANCE label swap (D-01).

    CR-01: stance shuffle drives the test statistic. The H0 under test is
    "HAWK/DOV stance label has no predictive power", so the test statistic
    must be stance-conditional: count cells where (p_adj_holm < ALPHA AND
    post-shuffle stance == "HAWK"). Without stance gating the test statistic
    is invariant under permutation (always returns the same edge count) and
    the null distribution collapses to a single value.

    Lehmann & Romano (2005) §15.2: permutation = relabeling within multiset.
    rng.permutation over the stance multiset preserves the marginal HAWK
    and DOV counts. Per-cell p_raw is NOT permuted — outcomes stay attached
    to cells; only the stance label is re-assigned, then we count HAWK
    edges. Padding to M_PRIME_V412 follows bootstrap_v412 H0 path (D-44).
    """
    # Stance shuffle: shuffled_stances[i] is the new label for active_cells[i].
    shuffled_stances = _shuffle_stance_for_test(rng, active_cells, stance_of)

    # Outcomes (per-cell p_raw) stay attached to cells; not shuffled.
    # Cap to M_PRIME_V412 and apply the same cap to the stance vector so that
    # the i-th p_adj entry corresponds to the i-th post-shuffle stance.
    p_raw_vec = [per_cell_p_raw.get(c, 1.0) for c in active_cells]
    stance_vec = list(shuffled_stances)
    n_active = len(p_raw_vec)
    if n_active > M_PRIME_V412:
        p_raw_vec = p_raw_vec[:M_PRIME_V412]
        stance_vec = stance_vec[:M_PRIME_V412]
        n_active = M_PRIME_V412
    p_raw_padded = p_raw_vec + [1.0] * (M_PRIME_V412 - n_active)
    p_adj = apply_bonferroni_holm(p_raw_padded)
    # CR-01 stance-conditional test statistic: count HAWK edges only.
    # Padded slots (i >= n_active) have no stance and are excluded.
    return int(
        sum(
            1
            for i, p in enumerate(p_adj)
            if i < n_active and p < ALPHA and stance_vec[i] == "HAWK"
        )
    )


def compute_observed_edge_count(
    p_adj_path: Path = _P_ADJ_V412,
    stance_of: dict[str, str] | None = None,
) -> int:
    """Count observed p_adj_holm < ALPHA entries with HAWK stance.

    CR-01: stance-conditional to match permute_one's test statistic. The
    observed count and the null distribution must use the *same* statistic;
    counting all edges in observed but only HAWK edges in null would be a
    statistical type error.
    CR-04: Filter on `status == "tested"` to exclude padded slots from the
    observed edge count (padded rows can carry p_adj_holm < ALPHA after
    Holm step-down but are not real edges).

    Args:
        p_adj_path: path to p_adj_v412.json
        stance_of: cell_id -> stance map (HAWK/DOV). If None, falls back
            to counting any tested edge (legacy behaviour, used only when
            cells_post_compound_filter parquet is unavailable). Production
            path always passes a populated stance_of from
            _load_active_cells_and_stance().
    """
    if not p_adj_path.exists():
        return 0
    doc = json.loads(p_adj_path.read_text(encoding="utf-8"))
    if stance_of is None:
        # Legacy fallback: stance map unavailable, count all tested edges.
        return int(
            sum(
                1
                for r in doc.get("results", [])
                if r.get("status") == "tested"
                and r.get("p_adj_holm") is not None
                and r["p_adj_holm"] < ALPHA
            )
        )
    return int(
        sum(
            1
            for r in doc.get("results", [])
            if r.get("status") == "tested"
            and r.get("p_adj_holm") is not None
            and r["p_adj_holm"] < ALPHA
            and stance_of.get(r.get("cell_id", "")) == "HAWK"
        )
    )


def ship_condition_met(observed: int, null_dist: list[int]) -> bool:
    """D-50: ship_condition_met := observed > p95 strict (tie → False)."""
    if not null_dist:
        return False
    p95 = float(np.percentile(null_dist, 95))
    return float(observed) > p95


def _build_null_distribution(
    active_cells: list[str],
    stance_of: dict[str, str],
    per_cell_p_raw: dict[str, float],
    b_samples: int = B_SAMPLES,
    seed: int = PERMUTATION_SEED,
) -> list[int]:
    """Run B permutations with a single numpy Generator stream."""
    rng = np.random.default_rng(seed)
    return [
        permute_one(rng, active_cells, stance_of, per_cell_p_raw)
        for _ in range(b_samples)
    ]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main(output_path: Path = _OUTPUT) -> None:
    active_cells, stance_of = _load_active_cells_and_stance(_CELLS_POST_COMPOUND_FILTER)
    per_cell_p_raw = _load_per_cell_p_raw(_P_ADJ_V412)

    n_active = len(active_cells)
    n_neut_excluded = 0
    if _CELLS_POST_COMPOUND_FILTER.exists():
        df_full = pl.read_parquet(_CELLS_POST_COMPOUND_FILTER)
        if "stance" in df_full.columns:
            n_neut_excluded = int(df_full.filter(pl.col("stance") == "NEUT").height)

    null_distribution = _build_null_distribution(
        active_cells=active_cells,
        stance_of=stance_of,
        per_cell_p_raw=per_cell_p_raw,
        b_samples=B_SAMPLES,
        seed=PERMUTATION_SEED,
    )
    observed = compute_observed_edge_count(_P_ADJ_V412, stance_of=stance_of)

    p50 = float(np.percentile(null_distribution, 50))
    p95 = float(np.percentile(null_distribution, 95))
    p99 = float(np.percentile(null_distribution, 99))
    condition_met = ship_condition_met(observed, null_distribution)

    doc = {
        "schema_version": "v4.12",
        "B": B_SAMPLES,
        "seed": PERMUTATION_SEED,
        "shuffle_unit": SHUFFLE_UNIT,
        "alpha": ALPHA,
        "m_prime": M_PRIME_V412,
        "signal_commit_v412": _SIGNAL_COMMIT_V412,
        "observed_edge_count_p_adj_005": int(observed),
        "null_distribution": null_distribution,
        "null_percentiles": {"p50": p50, "p95": p95, "p99": p99},
        "ship_condition_met": bool(condition_met),
        "ship_condition_rule": "observed > p95 (D-50 strict inequality)",
        "n_active_cells": int(n_active),
        "n_neut_cells_excluded": int(n_neut_excluded),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"[permutation_null_v412] B={B_SAMPLES} seed={PERMUTATION_SEED} "
        f"shuffle_unit={SHUFFLE_UNIT} n_active={n_active} "
        f"observed={observed} p95={p95} condition_met={condition_met} "
        f"-> {output_path}"
    )


if __name__ == "__main__":
    main()
