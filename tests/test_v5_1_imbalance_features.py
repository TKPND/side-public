"""Tests for v5.1 top-of-book imbalance feature generation."""

from __future__ import annotations

from datetime import datetime, timezone
import sys

sys.path.insert(0, "scripts")
import v5_1_generate_imbalance_features as features  # noqa: E402


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def test_constants_match_v5_1_claim_seal() -> None:
    assert features.PAIRS == ("BTCUSD", "ETHUSD")
    assert features.SCORE_FAMILIES == (
        "volume_ratio_imbalance",
        "mid_price_tick_direction_imbalance",
    )
    assert features.LOOKBACK_SECONDS == (30, 60, 300)
    assert features.THRESHOLDS == ("p80", "p90", "p95")
    assert features.DIRECTIONS == ("mean_reversion", "momentum")
    assert features.HORIZON_SECONDS == (60, 180, 300)
    assert features.FWER_DENOMINATOR == 216
    assert features.INTERPRETATION == "top-of-book quote imbalance proxy"


def test_toy_sequence_excludes_future_tick() -> None:
    ticks = [
        features.Tick(_ts("2024-05-01T00:00:00Z"), 100.0, 101.0, 8.0, 2.0),
        features.Tick(_ts("2024-05-01T00:00:10Z"), 101.0, 102.0, 6.0, 4.0),
        features.Tick(_ts("2024-05-01T00:00:30Z"), 80.0, 81.0, 1.0, 99.0),
    ]
    bucket_start = _ts("2024-05-01T00:00:00Z")
    bucket_end = _ts("2024-05-01T00:00:30Z")

    window = features.ticks_in_feature_window(ticks, bucket_start, bucket_end)
    leaked_window = ticks

    assert [tick.timestamp for tick in window] == [
        _ts("2024-05-01T00:00:00Z"),
        _ts("2024-05-01T00:00:10Z"),
    ]

    score, reason = features.feature_scores(window)
    leaked_score, leaked_reason = features.feature_scores(leaked_window)

    assert reason is None
    assert leaked_reason is None
    assert score["volume_ratio_imbalance"] != leaked_score["volume_ratio_imbalance"]
    assert (
        score["mid_price_tick_direction_imbalance"]
        != leaked_score["mid_price_tick_direction_imbalance"]
    )


def test_volume_ratio_zero_denominator_fails_closed() -> None:
    score, reason = features.volume_ratio_imbalance(0.0, 0.0)
    assert score is None
    assert reason == "zero_volume_denominator"


def test_mid_price_tick_direction_zero_denominator_fails_closed() -> None:
    score, reason = features.mid_price_tick_direction_imbalance([100.0, 100.0])
    assert score is None
    assert reason == "zero_mid_tick_denominator"


def test_feature_sql_contains_sealed_bq_shape_and_is_threshold_bounds() -> None:
    sql = features.build_feature_sql("BTCUSD")
    assert "`example-gcp-project.fx_tick_data.btcusd_ticks`" in sql
    assert "TIMESTAMP('2025-11-01T00:00:00Z')" in sql
    assert "TIMESTAMP_SECONDS(DIV(UNIX_SECONDS(timestamp), 30) * 30)" in sql
    assert "PARTITION BY entry_bucket_start" in sql
    assert "ROWS BETWEEN 0 PRECEDING AND CURRENT ROW" in sql
    assert "mid_price_tick_direction_imbalance" in sql
    assert "zero_mid_tick_denominator" in sql
    assert "APPROX_QUANTILES(ABS(imbalance_score), 100)[OFFSET(80)]" in sql
    assert "WHERE entry_timestamp < TIMESTAMP('2025-11-01T00:00:00Z')" in sql
    assert "JOIN thresholds USING (pair, score_family, lookback_seconds)" in sql
    assert "WHERE ABS(imbalance_score) >= threshold_value" in sql
    assert "CROSS JOIN UNNEST([0.80, 0.90, 0.95])" not in sql
    for column in (
        "entry_timestamp",
        "score_family",
        "lookback_seconds",
        "threshold",
        "threshold_value",
        "direction",
        "horizon_seconds",
        "imbalance_score",
        "signal_side",
        "bucket_start",
        "bucket_end",
        "entry_anchor_timestamp",
        "exit_anchor_timestamp",
        "missing_reason",
        "source_table",
    ):
        assert column in sql


def test_materialization_table_names_are_pair_safe() -> None:
    assert (
        features.destination_table("BTCUSD", "fx_tick_data", "v5_1_imbalance_events")
        == "fx_tick_data.v5_1_imbalance_events_btcusd"
    )
    assert (
        features.destination_table("ETHUSD", "fx_tick_data", "v5_1_imbalance_events")
        == "fx_tick_data.v5_1_imbalance_events_ethusd"
    )


def test_event_count_sql_groups_full_sealed_family() -> None:
    sql = features.build_event_count_sql(
        "example-gcp-project",
        "fx_tick_data",
        "v5_1_imbalance_events_btcusd",
    )
    for column in (
        "pair",
        "score_family",
        "lookback_seconds",
        "threshold",
        "direction",
        "horizon_seconds",
    ):
        assert column in sql
    assert "`example-gcp-project.fx_tick_data.v5_1_imbalance_events_btcusd`" in sql
    assert "GROUP BY" in sql


def test_sidecar_preserves_full_family_and_source_anchors() -> None:
    data_contract = {
        "phase114_blocked": False,
        "diagnostics": {
            "BTCUSD": {
                "source": [{"row_count": 10, "min_timestamp": "a", "max_timestamp": "b"}],
                "buckets": {"30": [{"missing_bucket_count": 1}]},
            },
            "ETHUSD": {
                "source": [{"row_count": 20, "min_timestamp": "c", "max_timestamp": "d"}],
                "buckets": {"300": [{"missing_bucket_count": 2}]},
            },
        },
    }
    sidecar = features.build_sidecar(data_contract)
    assert sidecar["schema_version"] == "v5.1-imbalance-features-1"
    assert sidecar["phase114_blocked"] is False
    assert sidecar["fwer_denominator"] == 216
    assert sidecar["source_anchors"]["BTCUSD"]["row_count"] == 10
    assert sidecar["source_anchors"]["ETHUSD"]["missing_bucket_counts"]["300"] == 2
    assert (
        sidecar["event_counts"]["BTCUSD"]["volume_ratio_imbalance"]["30"]["p80"][
            "mean_reversion"
        ]["60"]
        == 0
    )


def test_rendered_report_contains_phase_115_gate() -> None:
    md = features.render_markdown(
        features.build_sidecar({"phase114_blocked": False, "diagnostics": {}})
    )
    assert "# v5.1 Imbalance Feature Report" in md
    assert "top-of-book quote imbalance proxy" in md
    assert "## Phase 115 Gate" in md
    assert "phase114_blocked: false" in md
