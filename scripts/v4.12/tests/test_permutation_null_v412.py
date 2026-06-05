"""
test_permutation_null_v412.py — Phase 103 Wave 1 GREEN tests for SHIP-V412-03.

Cell-level HAWK <-> DOV stance label swap (B=2000, seed=20260601).
Implementation in `scripts/v4.12/permutation_null_v412.py` (Plan 103-03).

Citations: 103-03-PLAN.md Task 1, SHIP-V412-03 (D-01 stance label swap),
Lehmann & Romano 2005 §15.2 (within-cell marginal preserved).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PERMUTATION_NULL_V412 = _REPO_ROOT / "scripts" / "v4.12" / "permutation_null_v412.py"


def _load_permutation_null_v412():
    """Import permutation_null_v412 fresh each call (sys.modules pop avoids
    SEAL state leakage from sibling tests, per D-45 / 103-02-02 pattern)."""
    sys.modules.pop("permutation_null_v412", None)
    spec = importlib.util.spec_from_file_location(
        "permutation_null_v412", _PERMUTATION_NULL_V412
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_permutation_seed_20260601():
    """D-01: PERMUTATION_SEED == 20260601 (Phase 103 septuple-pin override
    of v4.11's 20260425)."""
    mod = _load_permutation_null_v412()
    assert mod.PERMUTATION_SEED == 20260601, (
        f"PERMUTATION_SEED must be 20260601 (Phase 103 septuple-pin), "
        f"got {mod.PERMUTATION_SEED}"
    )


def test_shuffle_unit_stance_not_bucket():
    """D-01: SHUFFLE_UNIT == 'stance' (NOT 'cell'/'bucket').

    v4.11 used cell-level bucket shuffle. v4.12 logic-rewrite forks to
    stance-label swap (HAWK <-> DOV per cell, NEUT excluded). Anti-feature
    grep gate `SHUFFLE_UNIT.*cell` must NOT match (Plan 103-06 enforced).
    """
    mod = _load_permutation_null_v412()
    assert mod.SHUFFLE_UNIT == "stance", (
        f"SHUFFLE_UNIT must be 'stance' (D-01 logic rewrite), got {mod.SHUFFLE_UNIT!r}"
    )
    assert mod.SHUFFLE_UNIT != "cell"
    assert mod.SHUFFLE_UNIT != "bucket"


def test_b_samples_2000():
    """D-01: B_SAMPLES == 2000 (carry from v4.11, septuple-pin fixed)."""
    mod = _load_permutation_null_v412()
    assert mod.B_SAMPLES == 2000


def test_within_cell_marginal_stance_preserved():
    """Lehmann & Romano 2005 §15.2: HAWK/DOV marginal count is post-shuffle
    invariant (permutation = relabeling within multiset, not constant swap).

    permute_one() shuffles stance labels via rng.permutation, which is a
    bijection over the input multiset. Therefore for any seed the output
    has the same HAWK count and same DOV count as the input.
    """
    mod = _load_permutation_null_v412()
    rng = np.random.default_rng(20260601)
    active_cells = ["c1", "c2", "c3", "c4"]
    stance_of = {"c1": "HAWK", "c2": "DOV", "c3": "HAWK", "c4": "DOV"}
    per_cell_p_raw = {c: 0.05 for c in active_cells}
    pre_count_hawk = sum(1 for s in stance_of.values() if s == "HAWK")
    pre_count_dov = sum(1 for s in stance_of.values() if s == "DOV")

    # permute_one returns the edge_count_p_adj_005, but we also expose the
    # shuffled stance vector via _shuffle_stance_for_test for marginal check.
    shuffled = mod._shuffle_stance_for_test(rng, active_cells, stance_of)
    post_count_hawk = sum(1 for s in shuffled if s == "HAWK")
    post_count_dov = sum(1 for s in shuffled if s == "DOV")

    assert post_count_hawk == pre_count_hawk, (
        f"HAWK marginal not preserved: pre={pre_count_hawk}, "
        f"post={post_count_hawk} (Lehmann & Romano §15.2 violation)"
    )
    assert post_count_dov == pre_count_dov, (
        f"DOV marginal not preserved: pre={pre_count_dov}, post={post_count_dov}"
    )

    # Also sanity-check permute_one returns a non-negative int (edge_count).
    edge = mod.permute_one(rng, active_cells, stance_of, per_cell_p_raw)
    assert isinstance(edge, int) and edge >= 0


def test_cr01_null_distribution_non_degenerate_under_stance_shuffle():
    """CR-01 regression: stance shuffle must drive the test statistic so
    that the null distribution has variance > 0 across permutations.

    Pre-fix bug: permute_one discarded shuffled stances and counted all
    edges in p_adj < ALPHA, which was invariant under permutation → null
    distribution collapsed to a constant value. Post-fix: stance-conditional
    HAWK-edge count varies across permutations.

    Fixture: 4 cells with mixed HAWK/DOV labels and small p_raw → non-zero
    edge counts that change as labels shuffle.
    """
    mod = _load_permutation_null_v412()
    active_cells = ["c1", "c2", "c3", "c4"]
    stance_of = {"c1": "HAWK", "c2": "DOV", "c3": "HAWK", "c4": "DOV"}
    # Mixed p_raw: c1, c2 small (edges after Holm); c3, c4 large (non-edges).
    # Edge slots are fixed at indices [0, 1]. Stance shuffle changes which
    # cells carry HAWK at those slots → HAWK-edge count varies following
    # hypergeometric(N=4, K=2 HAWK, n=2 edge slots) → support {0, 1, 2}.
    per_cell_p_raw = {"c1": 0.0001, "c2": 0.0002, "c3": 0.9, "c4": 0.9}

    rng = np.random.default_rng(20260601)
    samples = [
        mod.permute_one(rng, active_cells, stance_of, per_cell_p_raw)
        for _ in range(200)
    ]
    distinct = set(samples)
    assert len(distinct) > 1, (
        f"Null distribution is degenerate (all samples = {next(iter(distinct))}). "
        f"Stance shuffle is not driving the test statistic — CR-01 regressed."
    )
    # Sanity: HAWK count in fixture is 2, so edge count is bounded by [0, 2].
    assert all(0 <= s <= 2 for s in samples), (
        f"Edge counts out of expected [0, 2] range for 2 HAWK cells: {sorted(distinct)}"
    )
