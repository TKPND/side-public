"""Phase 93 Wave 3 Plan 04: Integration smoke tests (CLASS-04 E2E).

Tests confirm end-to-end pipeline:
  vol_estimator -> nyquist_audit_v411 -> kill-switch logic

D-35 flat imports: conftest.py inserts scripts/v4.11 into sys.path.
D-36 n_min = post-JOIN cell_id.n_unique().
D-33 JOIN on (pair, bar_time) == (pair, event_ts); no cell_id in vol parquet.
D-34 bucket keys = VOL_LOW / VOL_MID / VOL_HIGH.
D-17 _N_MIN_THR=20 / _N_EFF_THR=4 untouched module-level literals.

The 6 tests:
  1. test_phase93_e2e_no_kill_synthetic_slot_labels
  2. test_phase93_e2e_kill_fires_low_distinct
  3. test_validation_md_round_trip_json_parse
  4. test_e2e_schema_vol_prefix_and_no_cell_id
  5. test_flat_import_no_dot_path_integration
  6. test_e2e_empirical_join_nonempty
"""

from __future__ import annotations

import json
import pathlib
import re

import polars as pl

# D-35: conftest.py inserts scripts/v4.11 into sys.path -> flat imports work.
from vol_estimator import (  # type: ignore[import-not-found]
    assign_buckets,
    build_pooled_vol_frame,
    emit_vol_per_slot_parquet,
)
from nyquist_audit_v411 import (  # type: ignore[import-not-found]
    _load_filter_spec,
    run_nyquist_audit,
    emit_validation_md,
    _JSON_BLOCK_MARKER_START,
    _JSON_BLOCK_MARKER_END,
    _N_MIN_THR,
    _BUCKET_HIGH,
)
from run_phase93_smoke import (  # type: ignore[import-not-found]
    build_synthetic_slot_labels_df,
    run_smoke,
)
from seal_drift_check import SEAL_DIR_DEFAULT  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_validation_md(path: pathlib.Path) -> None:
    """Seed a tmp VALIDATION.md with the required JSON block markers.

    emit_validation_md() raises ValueError if _JSON_BLOCK_MARKER_START is
    absent. Tests that call emit_validation_md must seed the file first.
    """
    path.write_text(
        "---\nnyquist_compliant: false\n---\n\n"
        f"{_JSON_BLOCK_MARKER_START}\n```json\n{{}}\n```\n{_JSON_BLOCK_MARKER_END}\n",
        encoding="utf-8",
    )


def _build_vol(synthetic_ohlc_2pair: pl.DataFrame) -> pl.DataFrame:
    """Convenience: compute vol from the 2-pair fixture."""
    vol = build_pooled_vol_frame(synthetic_ohlc_2pair)
    return assign_buckets(vol)


# ---------------------------------------------------------------------------
# Test 1: synthetic-pass -> kill_switch_fired == False
# ---------------------------------------------------------------------------


def test_phase93_e2e_no_kill_synthetic_slot_labels(
    synthetic_ohlc_2pair: pl.DataFrame,
    tmp_path: pathlib.Path,
) -> None:
    """E2E no-kill: n_distinct_high=24 -> n_min>=20 -> kill_switch_fired=False."""
    vol_out = tmp_path / "vol_per_slot.parquet"

    result = run_smoke(
        mode="synthetic-pass",
        vol_out=vol_out,
        validation_md=tmp_path / "93-VALIDATION.md",  # does not exist -> skip emit
        real_slot_labels=tmp_path / "nonexistent.parquet",
        verbose=False,
    )

    assert result["kill_switch_fired"] is False, (
        f"Expected no-kill for synthetic-pass (24 distinct cell_id), "
        f"got kill_switch_fired={result['kill_switch_fired']} "
        f"reason={result.get('kill_switch_reason')}"
    )
    assert result["nyquist_compliant"] is True
    # VOL_HIGH n_min must be >= _N_MIN_THR
    high_stats = result["per_bucket"].get(_BUCKET_HIGH, {})
    assert high_stats.get("n_min", 0) >= _N_MIN_THR, (
        f"VOL_HIGH n_min={high_stats.get('n_min')} < {_N_MIN_THR}"
    )
    # vol parquet must exist
    assert vol_out.exists(), "vol_per_slot.parquet not emitted"


# ---------------------------------------------------------------------------
# Test 2: synthetic-kill -> kill_switch_fired == True
# ---------------------------------------------------------------------------


def test_phase93_e2e_kill_fires_low_distinct(
    synthetic_ohlc_2pair: pl.DataFrame,
    tmp_path: pathlib.Path,
) -> None:
    """E2E kill fires: n_distinct_high=6 -> n_min=6<20 -> kill_switch_fired=True."""
    vol_out = tmp_path / "vol_per_slot.parquet"

    result = run_smoke(
        mode="synthetic-kill",
        vol_out=vol_out,
        validation_md=tmp_path / "93-VALIDATION.md",  # does not exist -> skip emit
        real_slot_labels=tmp_path / "nonexistent.parquet",
        verbose=False,
    )

    assert result["kill_switch_fired"] is True, (
        f"Expected kill for synthetic-kill (6 distinct cell_id), "
        f"got kill_switch_fired={result['kill_switch_fired']}"
    )
    assert result["nyquist_compliant"] is False
    # Reason must mention VOL_HIGH bucket
    reason = result.get("kill_switch_reason", "")
    assert "VOL_HIGH bucket" in reason, f"kill_switch_reason unexpected: {reason!r}"
    # VOL_HIGH n_min must be < _N_MIN_THR
    high_stats = result["per_bucket"].get(_BUCKET_HIGH, {})
    assert high_stats.get("n_min", 999) < _N_MIN_THR, (
        f"VOL_HIGH n_min={high_stats.get('n_min')} should be < {_N_MIN_THR}"
    )


# ---------------------------------------------------------------------------
# Test 3: emit_validation_md round-trip -> JSON parseable
# ---------------------------------------------------------------------------


def test_validation_md_round_trip_json_parse(
    synthetic_ohlc_2pair: pl.DataFrame,
    tmp_path: pathlib.Path,
) -> None:
    """After emit_validation_md, the JSON block in VALIDATION.md is parseable."""
    vol = _build_vol(synthetic_ohlc_2pair)
    slot_labels = build_synthetic_slot_labels_df(
        synthetic_ohlc_2pair, n_distinct_high=24
    )
    filter_spec = _load_filter_spec(SEAL_DIR_DEFAULT)
    audit_result = run_nyquist_audit(vol, slot_labels, filter_spec)

    md_path = tmp_path / "93-VALIDATION.md"
    _seed_validation_md(md_path)

    emit_validation_md(audit_result, md_path)

    content = md_path.read_text(encoding="utf-8")
    # Extract JSON between markers.
    pattern = re.compile(
        re.escape(_JSON_BLOCK_MARKER_START)
        + r"\s*```json\s*(.*?)```\s*"
        + re.escape(_JSON_BLOCK_MARKER_END),
        re.DOTALL,
    )
    m = pattern.search(content)
    assert m is not None, "JSON block markers not found in VALIDATION.md"
    parsed = json.loads(m.group(1).strip())
    # Basic structure assertions.
    assert "kill_switch_fired" in parsed, "kill_switch_fired missing from JSON"
    assert "per_bucket" in parsed, "per_bucket missing from JSON"
    assert "nyquist_compliant" in parsed, "nyquist_compliant missing from JSON"
    # D-34: per_bucket keys must have VOL_ prefix.
    for key in parsed["per_bucket"]:
        assert key.startswith("VOL_"), f"per_bucket key {key!r} missing VOL_ prefix"


# ---------------------------------------------------------------------------
# Test 4: vol parquet schema — VOL_ prefix + no cell_id column
# ---------------------------------------------------------------------------


def test_e2e_schema_vol_prefix_and_no_cell_id(
    synthetic_ohlc_2pair: pl.DataFrame,
    tmp_path: pathlib.Path,
) -> None:
    """vol_per_slot.parquet: no cell_id column; bucket values have VOL_ prefix."""
    vol_out = tmp_path / "vol_per_slot.parquet"
    vol = _build_vol(synthetic_ohlc_2pair)
    emit_vol_per_slot_parquet(vol, vol_out)

    df = pl.read_parquet(vol_out)

    # D-33: no cell_id column.
    assert "cell_id" not in df.columns, "cell_id column must NOT appear in vol_per_slot"

    # D-33: required columns present.
    for col in [
        "pair",
        "bar_time",
        "atr_14",
        "rolling_quantile_low",
        "rolling_quantile_high",
        "bucket",
        "vol_input_ts",
    ]:
        assert col in df.columns, f"Required column {col!r} missing from vol_per_slot"

    # D-34: bucket values must have VOL_ prefix (excluding VOL_NA warmup rows).
    non_na = df.filter(pl.col("bucket") != "VOL_NA")
    if non_na.height > 0:
        bucket_vals = set(non_na["bucket"].unique().to_list())
        for bv in bucket_vals:
            assert bv.startswith("VOL_"), f"Bucket value {bv!r} missing VOL_ prefix"
        expected = {"VOL_LOW", "VOL_MID", "VOL_HIGH"}
        assert bucket_vals.issubset(expected | {"VOL_NA"}), (
            f"Unexpected bucket values: {bucket_vals - expected}"
        )


# ---------------------------------------------------------------------------
# Test 5: flat import — no dot-path usage
# ---------------------------------------------------------------------------


def test_flat_import_no_dot_path_integration() -> None:
    """D-35: integration scripts use only flat imports (no scripts.v4.11 dot-path).

    Scans run_phase93_smoke.py source for forbidden dot-path import patterns.
    """
    smoke_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "scripts"
        / "v4.11"
        / "run_phase93_smoke.py"
    )
    assert smoke_path.exists(), f"run_phase93_smoke.py not found at {smoke_path}"

    source = smoke_path.read_text(encoding="utf-8")

    # Forbidden patterns: `from scripts.v4.11.` or `from scripts.v4_11.`
    forbidden_patterns = [
        r"from\s+scripts\.v4[._]11\.",
        r"import\s+scripts\.v4[._]11\.",
    ]
    for pattern in forbidden_patterns:
        m = re.search(pattern, source)
        assert m is None, (
            f"Forbidden dot-path import pattern {pattern!r} found in "
            f"run_phase93_smoke.py: {m.group()!r}"
        )


# ---------------------------------------------------------------------------
# Test 6: empirical JOIN is non-empty
# ---------------------------------------------------------------------------


def test_e2e_empirical_join_nonempty(
    synthetic_ohlc_2pair: pl.DataFrame,
) -> None:
    """D-33: inner JOIN vol x slot_labels must produce > 0 rows.

    Verifies that synthetic_ohlc_2pair bar_times align with synthetic
    slot_labels event_ts so that JOIN succeeds (n_min can be evaluated).
    """
    vol = _build_vol(synthetic_ohlc_2pair)
    # Use 24-distinct for pass scenario.
    slot_labels = build_synthetic_slot_labels_df(
        synthetic_ohlc_2pair, n_distinct_high=24
    )

    # JOIN manually to assert non-empty before audit runs.
    joined = vol.join(
        slot_labels,
        left_on=["pair", "bar_time"],
        right_on=["pair", "event_ts"],
        how="inner",
    )
    assert len(joined) > 0, (
        "D-33: inner JOIN produced 0 rows. Check that bar_time and event_ts "
        "dtypes and values align between vol_df and slot_labels_df."
    )
    # Also confirm cell_id is present (needed for n_min computation).
    assert "cell_id" in joined.columns, "cell_id missing from JOIN result"
    # n_unique of cell_id in VOL_HIGH must be >= 20 for pass scenario.
    high_joined = joined.filter(pl.col("bucket") == "VOL_HIGH")
    if high_joined.height > 0:
        n_min = high_joined["cell_id"].n_unique()
        assert n_min >= _N_MIN_THR, (
            f"VOL_HIGH n_min={n_min} < {_N_MIN_THR} in pass scenario "
            f"(24 distinct cell_id). Slot_labels alignment may be off."
        )
