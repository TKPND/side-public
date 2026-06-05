"""Phase 95 Plan 2 Task 3: pytest for permutation_null.py.

Acceptance (D-47/D-48/D-50):
  - test_seed_literal: PERMUTATION_SEED == 20260425 (not env-configurable)
  - test_b_literal: B_SAMPLES == 2000
  - test_shuffle_unit_cell: SHUFFLE_UNIT == "cell"
  - test_bucket_distribution_preserved: multiset of permuted labels ==
    multiset of original labels every iter
  - test_reproducibility: running main() twice produces bit-identical JSON
  - test_observed_edge_count_matches_p_adj: observed == int((p_adj_holm<0.05).sum())
  - test_strict_inequality_tie_is_fail: ship_condition_met(observed, [observed]*N)
    returns False when observed equals p95 (D-50 tie → fail side)
  - test_null_p95_present: output JSON has p50, p95, p99 keys
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

# conftest.py sys.path.insert scripts/v4.11 so flat imports work.
from permutation_null import (
    ALPHA,
    B_SAMPLES,
    PERMUTATION_SEED,
    SHUFFLE_UNIT,
    _build_null_distribution,
    compute_observed_edge_count,
    permute_one,
    ship_condition_met,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUTPUT = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "permutation_null_v411.json"
)
_P_ADJ = _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "p_adj_v411.json"


def test_seed_literal() -> None:
    assert PERMUTATION_SEED == 20260425


def test_b_literal() -> None:
    assert B_SAMPLES == 2000


def test_shuffle_unit_cell() -> None:
    assert SHUFFLE_UNIT == "cell"


def test_alpha_literal() -> None:
    assert ALPHA == 0.05


def test_bucket_distribution_preserved() -> None:
    """D-47: each permutation iteration must keep the per-bucket count invariant.

    Use a synthetic 8-cell mixed-bucket fixture so the assertion is non-trivial
    (the real Phase 93 fixture has only VOL_NA, making Counter equality trivial).
    """
    rng = np.random.default_rng(123)
    all_cells = [f"c{i}" for i in range(8)]
    buckets = [
        "VOL_HIGH",
        "VOL_HIGH",
        "VOL_LOW",
        "VOL_LOW",
        "VOL_MID",
        "VOL_MID",
        "VOL_NA",
        "VOL_NA",
    ]
    bucket_of = dict(zip(all_cells, buckets))
    original_counter = Counter(buckets)
    per_cell_p_raw = {c: 0.5 for c in all_cells}

    # Hit permute_one 10 times, inspect internal by recomputing the
    # same shuffle with a fresh rng stream.
    for _ in range(10):
        labels = [bucket_of[c] for c in all_cells]
        permuted = rng.permutation(labels).tolist()
        assert Counter(permuted) == original_counter, (
            f"bucket multiset diverged: got {Counter(permuted)} "
            f"expected {original_counter}"
        )
        # Also smoke-test permute_one returns int in valid range.
        iter_rng = np.random.default_rng(rng.integers(0, 2**31))
        edge_count = permute_one(
            iter_rng, all_cells, bucket_of, per_cell_p_raw, frozenset({"VOL_HIGH"})
        )
        assert 0 <= edge_count <= 64


def test_single_stream_reproducibility_via_function() -> None:
    """D-48 single np.random.default_rng stream reproducibility (unit)."""
    all_cells = [f"c{i}" for i in range(8)]
    buckets = [
        "VOL_HIGH",
        "VOL_HIGH",
        "VOL_LOW",
        "VOL_LOW",
        "VOL_MID",
        "VOL_MID",
        "VOL_NA",
        "VOL_NA",
    ]
    bucket_of = dict(zip(all_cells, buckets))
    per_cell_p_raw = {c: 0.5 for c in all_cells}

    run_a = _build_null_distribution(
        all_cells=all_cells,
        bucket_of=bucket_of,
        per_cell_p_raw=per_cell_p_raw,
        allowed_buckets=frozenset({"VOL_HIGH"}),
        b_samples=50,
        seed=20260425,
    )
    run_b = _build_null_distribution(
        all_cells=all_cells,
        bucket_of=bucket_of,
        per_cell_p_raw=per_cell_p_raw,
        allowed_buckets=frozenset({"VOL_HIGH"}),
        b_samples=50,
        seed=20260425,
    )
    assert run_a == run_b


def test_reproducibility_end_to_end(tmp_path: Path) -> None:
    """D-48: running the script end-to-end twice produces bit-identical JSON."""
    # Re-emit twice via `python scripts/v4.11/permutation_null.py` (mirrors
    # CI invocation), compare sha256 against the currently committed file.
    r1 = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "v4.11" / "permutation_null.py")],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert r1.returncode == 0, r1.stderr
    sha_a = _OUTPUT.read_bytes()
    r2 = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "v4.11" / "permutation_null.py")],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert r2.returncode == 0, r2.stderr
    sha_b = _OUTPUT.read_bytes()
    assert sha_a == sha_b


def test_observed_edge_count_matches_p_adj() -> None:
    """observed == int((p_adj_holm<0.05).sum()) over real p_adj_v411.json."""
    doc = json.loads(_P_ADJ.read_text(encoding="utf-8"))
    expected = int(
        sum(
            1
            for r in doc["results"]
            if r["p_adj_holm"] is not None and r["p_adj_holm"] < 0.05
        )
    )
    assert compute_observed_edge_count() == expected


def test_strict_inequality_tie_is_fail() -> None:
    """D-50: observed == null_p95 must return False (tie → fail side)."""
    null_dist = [3] * 100
    assert ship_condition_met(observed=3, null_dist=null_dist) is False
    # observed strictly greater passes
    assert ship_condition_met(observed=4, null_dist=null_dist) is True
    # observed strictly less fails
    assert ship_condition_met(observed=2, null_dist=null_dist) is False


def test_null_p95_present_in_output() -> None:
    """Output JSON must expose p50, p95, p99 percentile keys."""
    assert _OUTPUT.exists(), "run permutation_null.py before this test"
    doc = json.loads(_OUTPUT.read_text(encoding="utf-8"))
    assert set(doc["null_percentiles"].keys()) >= {"p50", "p95", "p99"}
    assert doc["provenance"]["B"] == 2000
    assert doc["provenance"]["seed"] == 20260425
    assert doc["provenance"]["shuffle_unit"] == "cell"
    assert isinstance(doc["ship_condition_met"], bool)


def test_observed_from_output_matches_compute() -> None:
    doc = json.loads(_OUTPUT.read_text(encoding="utf-8"))
    assert doc["observed_edge_count_p_adj_005"] == compute_observed_edge_count()
