"""CLASS-01/02 tests for scripts/v4.11/vol_estimator.py (11 cases).

D-35: Flat import via conftest.py sys.path.insert (scripts/v4.11 is dot-in-dir).
D-33/D-34 Addendum 2 enforced at schema + bucket-value level.
"""

from __future__ import annotations

import pathlib
import warnings
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

# D-35 flat import (conftest.py already did sys.path.insert for scripts/v4.11)
from vol_estimator import (  # type: ignore[import-not-found]
    _BUCKET_HIGH,
    _BUCKET_LOW,
    _BUCKET_MID,
    _BUCKET_NA,
    assert_embargo,
    assign_buckets,
    build_pooled_vol_frame,
    compute_atr14_wilder,
    emit_vol_per_slot_parquet,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VOL_PREFIX_SET = {_BUCKET_LOW, _BUCKET_MID, _BUCKET_HIGH, _BUCKET_NA}
assert _VOL_PREFIX_SET == {"VOL_LOW", "VOL_MID", "VOL_HIGH", "VOL_NA"}, (
    "Addendum 2 D-34 prefix set must be exactly these four values"
)


def test_atr14_wilder_constant_tr() -> None:
    """ATR-14 Wilder: constant TR=0.002 -> index 0..12 NaN, 13+ == 0.002 (atol=1e-6)."""
    n = 120
    high = np.full(n, 100.002)
    low = np.full(n, 100.0)
    close = np.full(n, 100.001)
    atr = compute_atr14_wilder(high, low, close)
    assert np.all(np.isnan(atr[:13]))
    assert np.allclose(atr[13:], 0.002, atol=1e-6)


def test_embargo_runtime_assert() -> None:
    """D-21 T-93-02: assert_embargo raises RuntimeError when max_ts >= event_ts."""
    df = pl.DataFrame(
        {
            "vol_input_ts": pl.Series(
                [datetime(2024, 6, 1), datetime(2024, 6, 2)]
            ).cast(pl.Datetime("ns")),
        }
    )
    with pytest.raises(RuntimeError, match="Embargo violation"):
        assert_embargo(df, event_ts=datetime(2024, 6, 2))  # equal triggers >=


def test_embargo_negative(synthetic_ohlc_2pair: pl.DataFrame) -> None:
    """T-93-02 negative: event_ts == max bar_time must raise."""
    vol = assign_buckets(build_pooled_vol_frame(synthetic_ohlc_2pair, 90))
    max_ts = vol.filter(pl.col("vol_input_ts").is_not_null())["vol_input_ts"].max()
    with pytest.raises(RuntimeError, match="Embargo violation"):
        assert_embargo(vol, event_ts=max_ts)  # event == max triggers >=


def test_embargo_positive(synthetic_ohlc_2pair: pl.DataFrame) -> None:
    """T-93-02 positive: event_ts = max bar_time + 1day must pass (no raise)."""
    vol = assign_buckets(build_pooled_vol_frame(synthetic_ohlc_2pair, 90))
    max_ts = vol.filter(pl.col("vol_input_ts").is_not_null())["vol_input_ts"].max()
    assert_embargo(vol, event_ts=max_ts + timedelta(days=1))  # no raise


def test_rolling_quantile_bucket_assignment(
    synthetic_ohlc_2pair: pl.DataFrame,
) -> None:
    """Rolling quantile distributes non-warmup rows across >=2 distinct VOL_* buckets."""
    vol = assign_buckets(build_pooled_vol_frame(synthetic_ohlc_2pair, 90))
    non_warmup = vol.filter(pl.col("bucket") != _BUCKET_NA)
    buckets = set(non_warmup["bucket"].unique().to_list())
    assert buckets.issubset({_BUCKET_LOW, _BUCKET_MID, _BUCKET_HIGH})
    assert len(buckets) >= 2, f"Expected >=2 distinct buckets, got {buckets}"


def test_warmup_vol_na_placeholder(synthetic_ohlc_2pair: pl.DataFrame) -> None:
    """D-34: warmup rows (<14 bars per pair) -> bucket='VOL_NA', atr_14/vol_input_ts null."""
    vol = assign_buckets(build_pooled_vol_frame(synthetic_ohlc_2pair, 90))
    na_rows = vol.filter(pl.col("bucket") == _BUCKET_NA)
    assert len(na_rows) > 0
    assert na_rows["atr_14"].is_null().all()
    assert na_rows["vol_input_ts"].is_null().all()
    # Addendum 2 D-34: 4-char literal exactly "VOL_NA"
    assert _BUCKET_NA == "VOL_NA"


def test_parquet_schema_pair_bar_time_pk(
    synthetic_ohlc_2pair: pl.DataFrame, tmp_path: pathlib.Path
) -> None:
    """D-33 Addendum 2: primary key = (pair, bar_time); cell_id column is NOT emitted."""
    vol = assign_buckets(build_pooled_vol_frame(synthetic_ohlc_2pair, 90))
    out = tmp_path / "vol_per_slot.parquet"
    emit_vol_per_slot_parquet(vol, out)
    reread = pl.read_parquet(out)

    # D-33 schema column order (PK first)
    assert reread.columns == [
        "pair",
        "bar_time",
        "atr_14",
        "rolling_quantile_low",
        "rolling_quantile_high",
        "bucket",
        "vol_input_ts",
    ]
    assert "cell_id" not in reread.columns  # D-33: cell_id dropped
    assert reread["pair"].dtype == pl.Utf8
    assert reread["bar_time"].dtype == pl.Datetime("ns")

    # D-34: bucket column values are all VOL_ prefixed
    bucket_set = set(reread["bucket"].unique().to_list())
    assert bucket_set.issubset(_VOL_PREFIX_SET), (
        f"bucket values must be subset of {_VOL_PREFIX_SET}; got {bucket_set}"
    )
    assert all(b.startswith("VOL_") for b in bucket_set)


def test_parquet_joins_slot_labels_on_pair_and_event_ts(
    synthetic_ohlc_2pair: pl.DataFrame, tmp_path: pathlib.Path
) -> None:
    """D-33 empirical JOIN test: len(joined) >= 1 for injected real-key row.

    Anti-pattern: do not let JOIN-success test pass trivially with 0 rows.

    Note: data/slot_labels.parquet is an event-definition file without timestamps
    (it has pair, cell_id, etc. but no event_ts column). The Phase 94 JOIN will
    operate against time-stamped trade data. For this test, we construct a
    synthetic slot_labels_df with a (pair, event_ts) key and verify the JOIN
    succeeds for an injected matching row. This confirms D-33 dtype and JOIN
    semantics are correct, which is the test's purpose.
    """
    vol = assign_buckets(build_pooled_vol_frame(synthetic_ohlc_2pair, 90))

    # Pick a real bar_time from vol to use as event_ts in synthetic slot_labels.
    non_na_rows = vol.filter(pl.col("bucket") != _BUCKET_NA)
    assert len(non_na_rows) > 0
    real_pair = non_na_rows["pair"][0]
    real_bar_time = non_na_rows["bar_time"][0]

    # Build synthetic slot_labels_df with (pair, event_ts) key matching vol row.
    # Mirrors Phase 94 JOIN contract: slot_labels.(pair, event_ts) == vol.(pair, bar_time).
    synthetic_slot_labels = pl.DataFrame(
        {
            "pair": pl.Series([real_pair], dtype=pl.Utf8),
            "event_ts": pl.Series([real_bar_time]).cast(pl.Datetime("ns")),
            "cell_id": pl.Series(["0-60m_x_HIGH"], dtype=pl.Utf8),
        }
    )

    # Emit parquet with cast bar_time to Datetime[ns] (D-33 schema)
    vol_selected = vol.select(
        [
            "pair",
            "bar_time",
            "atr_14",
            "rolling_quantile_low",
            "rolling_quantile_high",
            "bucket",
            "vol_input_ts",
        ]
    )
    out = tmp_path / "vol_per_slot_joinable.parquet"
    emit_vol_per_slot_parquet(vol_selected, out)
    reread = pl.read_parquet(out)

    # dtype alignment assertion (JOIN precondition)
    assert reread["pair"].dtype == synthetic_slot_labels["pair"].dtype, (
        f"pair dtype mismatch: vol={reread['pair'].dtype} slot={synthetic_slot_labels['pair'].dtype}"
    )
    assert reread["bar_time"].dtype == pl.Datetime("ns"), (
        f"bar_time must be Datetime[ns], got {reread['bar_time'].dtype}"
    )
    assert synthetic_slot_labels["event_ts"].dtype == pl.Datetime("ns"), (
        f"event_ts must be Datetime[ns], got {synthetic_slot_labels['event_ts'].dtype}"
    )
    joined = reread.join(
        synthetic_slot_labels,
        left_on=["pair", "bar_time"],
        right_on=["pair", "event_ts"],
        how="inner",
    )
    # Non-trivial JOIN: must succeed for the injected real-key row
    assert len(joined) >= 1, (
        "JOIN on (pair, event_ts==bar_time) must succeed for injected real-key row"
    )


def test_smoke_e2e(synthetic_ohlc_2pair: pl.DataFrame, tmp_path: pathlib.Path) -> None:
    """D-34 E2E: all emitted buckets start with 'VOL_'; VOL_LOW and VOL_HIGH both present."""
    vol = assign_buckets(build_pooled_vol_frame(synthetic_ohlc_2pair, 90))
    out = tmp_path / "smoke_vol.parquet"
    emit_vol_per_slot_parquet(vol, out)
    reread = pl.read_parquet(out)
    assert len(reread) > 0
    buckets = set(reread["bucket"].unique().to_list())
    assert all(b.startswith("VOL_") for b in buckets), (
        f"non-VOL_ bucket found: {buckets}"
    )
    counts = dict(reread.group_by("bucket").len().iter_rows())
    assert counts.get(_BUCKET_LOW, 0) >= 1
    assert counts.get(_BUCKET_HIGH, 0) >= 1


def test_polars_api_no_deprecation(synthetic_ohlc_2pair: pl.DataFrame) -> None:
    """D-19' min_samples API: no DeprecationWarning from polars rolling_quantile."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        build_pooled_vol_frame(synthetic_ohlc_2pair, 90)


def test_flat_import_no_dot_path() -> None:
    """D-35 meta-test: no dot-path import in vol_estimator.py or this test file."""
    ve = (_REPO_ROOT / "scripts" / "v4.11" / "vol_estimator.py").read_text()
    te = pathlib.Path(__file__).read_text()
    # Build forbidden patterns at runtime to avoid self-referential string literals
    # that would cause this meta-test to falsely detect itself.
    _pfx = "scripts."
    _underscore_path = _pfx + "v4_11."  # scripts.v4_11.xxx
    _dot_path = _pfx + "v4.11."  # scripts.v4.11.xxx
    _import_kw = "from "
    forbidden = [_import_kw + _underscore_path, _import_kw + _dot_path]
    for src_name, src in [("vol_estimator.py", ve), ("test_vol_estimator.py", te)]:
        for pattern in forbidden:
            assert pattern not in src, (
                f"{src_name} contains forbidden dot-path import: {pattern!r}"
            )
