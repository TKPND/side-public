"""Tests for v5.1 IS backtest and Holm FWER artifacts."""

from __future__ import annotations

import sys

sys.path.insert(0, "scripts")
import v5_1_is_backtest_fwer as phase115  # noqa: E402


def test_constants_match_v5_1_claim_seal() -> None:
    assert phase115.PAIRS == ("BTCUSD", "ETHUSD")
    assert phase115.SCORE_FAMILIES == (
        "volume_ratio_imbalance",
        "mid_price_tick_direction_imbalance",
    )
    assert phase115.LOOKBACK_SECONDS == (30, 60, 300)
    assert phase115.THRESHOLDS == ("p80", "p90", "p95")
    assert phase115.DIRECTIONS == ("mean_reversion", "momentum")
    assert phase115.HORIZON_SECONDS == (60, 180, 300)
    assert phase115.FWER_DENOMINATOR == 216
    assert phase115.IS_START == "2024-05-01T00:00:00Z"
    assert phase115.IS_END_EXCLUSIVE == "2025-11-01T00:00:00Z"
    assert phase115.ENTRY_GRANULARITY == "30s_bar"


def test_stance_mapping_for_momentum_and_mean_reversion() -> None:
    assert phase115.stance_for_event("momentum", "positive_extreme") == 1
    assert phase115.stance_for_event("momentum", "negative_extreme") == -1
    assert phase115.stance_for_event("mean_reversion", "positive_extreme") == -1
    assert phase115.stance_for_event("mean_reversion", "negative_extreme") == 1


def test_quote_side_net_pnl_deducts_spread_slippage_and_fee() -> None:
    long_trade = phase115.quote_side_pnl_bps(
        stance=1,
        entry_bid=100.0,
        entry_ask=100.2,
        exit_bid=101.0,
        exit_ask=101.2,
        fee_bps_roundtrip=10.0,
        slippage_bps_roundtrip=2.0,
    )
    short_trade = phase115.quote_side_pnl_bps(
        stance=-1,
        entry_bid=100.0,
        entry_ask=100.2,
        exit_bid=99.0,
        exit_ask=99.2,
        fee_bps_roundtrip=10.0,
        slippage_bps_roundtrip=2.0,
    )

    assert long_trade["entry_price"] == 100.2
    assert long_trade["exit_price"] == 101.0
    assert short_trade["entry_price"] == 100.0
    assert short_trade["exit_price"] == 99.2
    assert long_trade["pnl_bps_net"] == long_trade["pnl_bps_gross"] - 12.0
    assert short_trade["pnl_bps_net"] == short_trade["pnl_bps_gross"] - 12.0


def test_sparse_cells_fail_closed_with_p_raw_one() -> None:
    cell = {
        "pair": "BTCUSD",
        "score_family": "volume_ratio_imbalance",
        "lookback_seconds": 30,
        "threshold": "p95",
        "direction": "momentum",
        "horizon_seconds": 300,
        "cell_id": "BTCUSD_volume_ratio_imbalance_l30_p95_momentum_h300",
    }
    row = phase115.cell_metric_from_trades(cell, trades=[])

    assert row["sparse_fail_close"] is True
    assert row["p_raw"] == 1.0
    assert row["pass_net_pf"] is False
    assert "sparse_cell" in row["fail_reasons"]


def test_is_trade_sql_uses_phase114_events_and_raw_quote_anchors() -> None:
    sidecar = {
        "destination_tables": {
            "BTCUSD": "example-gcp-project:fx_tick_data.v5_1_imbalance_events_btcusd"
        }
    }

    sql = phase115.build_is_trade_sql("BTCUSD", sidecar=sidecar)

    assert "`example-gcp-project.fx_tick_data.v5_1_imbalance_events_btcusd`" in sql
    assert "`example-gcp-project.fx_tick_data.btcusd_ticks`" in sql
    assert "entry_timestamp >= TIMESTAMP('2024-05-01T00:00:00Z')" in sql
    assert "entry_timestamp < TIMESTAMP('2025-11-01T00:00:00Z')" in sql
    assert "entry_bid" in sql
    assert "entry_ask" in sql
    assert "exit_bid" in sql
    assert "exit_ask" in sql
    assert "ROW_NUMBER()" in sql
    assert "APPROX_QUANTILES" not in sql


def test_global_holm_preserves_sealed_denominator_and_eligibility() -> None:
    rows = []
    for index in range(216):
        rows.append(
            {
                "cell_id": f"cell_{index}",
                "p_raw": 0.0001 if index == 0 else 1.0,
                "pass_net_pf": index == 0,
                "sparse_fail_close": False,
            }
        )

    metadata = phase115.apply_holm_global(rows)

    assert metadata["fwer_denominator"] == 216
    assert metadata["n_tested"] == 216
    assert metadata["n_padded"] == 0
    assert rows[0]["p_adj_holm"] <= 0.05
    assert rows[0]["pass_fwer"] is True
    assert rows[0]["is_eligible_for_phase116"] is True


def test_collect_pair_cell_summaries_parses_bq_boolean_strings() -> None:
    rows = [
        {
            "pair": "BTCUSD",
            "score_family": "volume_ratio_imbalance",
            "lookback_seconds": "30",
            "threshold": "p80",
            "direction": "mean_reversion",
            "horizon_seconds": "60",
            "event_count": "50",
            "missing_entry_anchor_count": "0",
            "missing_exit_anchor_count": "0",
            "num_trades": "50",
            "gross_profit": "100.0",
            "gross_loss": "1000.0",
            "net_profit_factor": "0.1",
            "net_profit_factor_is_infinite": "false",
            "p_raw": "0.001",
            "fee_bps_roundtrip": "70.0",
            "slippage_bps_roundtrip": "2.0",
            "mean_pnl_bps_net": "-18.0",
            "stddev_pnl_bps_net": "5.0",
        }
    ]

    summaries = phase115.collect_pair_cell_summaries(rows, "BTCUSD")
    row = summaries[0]

    assert row["net_profit_factor_is_infinite"] is False
    assert row["net_profit_factor"] == 0.1
    assert row["net_profit_factor_value"] == 0.1
    assert row["pass_net_pf"] is False


def test_is_cell_summary_sql_aggregates_in_bigquery_before_python() -> None:
    sidecar = {
        "destination_tables": {
            "ETHUSD": "example-gcp-project:fx_tick_data.v5_1_imbalance_events_ethusd"
        }
    }

    sql = phase115.build_is_cell_summary_sql("ETHUSD", sidecar=sidecar)

    assert "`example-gcp-project.fx_tick_data.v5_1_imbalance_events_ethusd`" in sql
    assert "`example-gcp-project.fx_tick_data.ethusd_ticks`" in sql
    assert "pnl_bps_net" in sql
    assert "fee_bps_roundtrip" in sql
    assert "slippage_bps_roundtrip" in sql
    assert "GROUP BY" in sql
    assert "COUNT(*) AS num_trades" in sql
    assert "STDDEV_SAMP(pnl_bps_net)" in sql
    assert "ORDER BY pair, score_family, lookback_seconds" in sql


def test_materialize_cell_summary_sql_creates_pair_scoped_table() -> None:
    sidecar = {
        "destination_tables": {
            "BTCUSD": "example-gcp-project:fx_tick_data.v5_1_imbalance_events_btcusd"
        }
    }
    destination_table = phase115.materialized_cell_summary_table("BTCUSD")

    sql = phase115.build_materialize_cell_summary_sql(
        "BTCUSD",
        destination_table,
        sidecar=sidecar,
    )

    assert "CREATE OR REPLACE TABLE" in sql
    assert "v5_1_is_backtest_fwer_cell_summary_btcusd" in sql
    assert "pnl_bps_net" in sql
    assert "GROUP BY" in sql
    assert "APPROX_QUANTILES" not in sql


def test_materialize_quote_anchor_sql_uses_distinct_is_anchors() -> None:
    sidecar = {
        "destination_tables": {
            "BTCUSD": "example-gcp-project:fx_tick_data.v5_1_imbalance_events_btcusd"
        }
    }
    destination_table = phase115.quote_anchor_table("BTCUSD")

    sql = phase115.build_materialize_quote_anchor_sql(
        "BTCUSD",
        destination_table,
        sidecar=sidecar,
    )

    assert "CREATE OR REPLACE TABLE" in sql
    assert "v5_1_is_backtest_fwer_quote_anchor_btcusd" in sql
    assert "SELECT DISTINCT entry_anchor_timestamp AS anchor_timestamp" in sql
    assert "SELECT DISTINCT exit_anchor_timestamp AS anchor_timestamp" in sql
    assert "`example-gcp-project.fx_tick_data.btcusd_ticks`" in sql
    assert "q.timestamp >= a.anchor_timestamp" in sql
    assert "q.timestamp < TIMESTAMP_ADD(a.anchor_timestamp, INTERVAL 1 MINUTE)" in sql
    assert "actual_quote_timestamp" in sql


def test_quote_anchor_table_names_are_pair_scoped() -> None:
    assert (
        phase115.quote_anchor_table("BTCUSD")
        == "example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_quote_anchor_btcusd"
    )
    assert (
        phase115.quote_anchor_table("ETHUSD", prefix="custom_quote_anchor")
        == "example-gcp-project.fx_tick_data.custom_quote_anchor_ethusd"
    )


def test_anchor_and_raw_quote_stage_table_names_are_pair_scoped() -> None:
    assert (
        phase115.anchor_table("BTCUSD")
        == "example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_anchor_btcusd"
    )
    assert (
        phase115.raw_quote_stage_table("ETHUSD")
        == "example-gcp-project.fx_tick_data."
        "v5_1_is_backtest_fwer_raw_quote_stage_ethusd"
    )
    assert (
        phase115.anchor_table("ETHUSD", prefix="custom_anchor")
        == "example-gcp-project.fx_tick_data.custom_anchor_ethusd"
    )


def test_raw_quote_layout_inspection_sql_reads_information_schema() -> None:
    sql = phase115.build_raw_quote_layout_inspection_sql()

    assert "`example-gcp-project.fx_tick_data.INFORMATION_SCHEMA.COLUMNS`" in sql
    assert "table_name IN ('btcusd_ticks', 'ethusd_ticks')" in sql
    assert "column_name IN ('timestamp', 'bidPrice', 'askPrice')" in sql
    assert "is_partitioning_column" in sql
    assert "clustering_ordinal_position" in sql
    assert "ORDER BY table_name, ordinal_position" in sql


def test_materialize_anchor_table_sql_builds_partitioned_anchor_table() -> None:
    sidecar = {
        "destination_tables": {
            "BTCUSD": "example-gcp-project:fx_tick_data.v5_1_imbalance_events_btcusd"
        }
    }
    sql = phase115.build_materialize_anchor_table_sql(
        "BTCUSD",
        phase115.anchor_table("BTCUSD"),
        sidecar=sidecar,
    )

    assert "CREATE OR REPLACE TABLE" in sql
    assert "PARTITION BY DATE(anchor_timestamp)" in sql
    assert "CLUSTER BY anchor_timestamp" in sql
    assert "`example-gcp-project.fx_tick_data.v5_1_imbalance_events_btcusd`" in sql
    assert "SELECT DISTINCT entry_anchor_timestamp AS anchor_timestamp" in sql
    assert "SELECT DISTINCT exit_anchor_timestamp AS anchor_timestamp" in sql
    assert "entry_timestamp >= TIMESTAMP('2024-05-01T00:00:00Z')" in sql
    assert "entry_timestamp < TIMESTAMP('2025-11-01T00:00:00Z')" in sql
    assert (
        "anchor_timestamp < TIMESTAMP_ADD(TIMESTAMP('2025-11-01T00:00:00Z'), INTERVAL 6 MINUTE)"
        in sql
    )
    assert "ORDER BY anchor_timestamp" not in sql


def test_anchor_grid_validation_sql_counts_off_grid_anchors() -> None:
    sql = phase115.build_anchor_grid_validation_sql(phase115.anchor_table("ETHUSD"))

    assert "`example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_anchor_ethusd`" in sql
    assert "COUNT(*) AS anchor_count" in sql
    assert (
        "COUNTIF(MOD(UNIX_MICROS(anchor_timestamp), 30000000) != 0) AS off_grid_anchor_count"
        in sql
    )


def test_raw_quote_stage_sql_uses_six_minute_is_tail() -> None:
    sql = phase115.build_materialize_raw_quote_stage_sql(
        "BTCUSD",
        phase115.raw_quote_stage_table("BTCUSD"),
    )

    assert "CREATE OR REPLACE TABLE" in sql
    assert "PARTITION BY DATE(actual_quote_timestamp)" in sql
    assert "CLUSTER BY actual_quote_timestamp" in sql
    assert "`example-gcp-project.fx_tick_data.btcusd_ticks`" in sql
    assert "timestamp AS actual_quote_timestamp" in sql
    assert "bidPrice AS bid" in sql
    assert "askPrice AS ask" in sql
    assert "timestamp >= TIMESTAMP('2024-05-01T00:00:00Z')" in sql
    assert (
        "timestamp < TIMESTAMP_ADD(TIMESTAMP('2025-11-01T00:00:00Z'), INTERVAL 6 MINUTE)"
        in sql
    )
    assert "ORDER BY actual_quote_timestamp" not in sql


def test_quote_anchor_from_grid_sql_uses_two_candidate_anchors() -> None:
    sql = phase115.build_materialize_quote_anchor_from_grid_sql(
        "ETHUSD",
        phase115.quote_anchor_table("ETHUSD"),
        phase115.anchor_table("ETHUSD"),
        phase115.raw_quote_stage_table("ETHUSD"),
    )

    assert "CREATE OR REPLACE TABLE" in sql
    assert "PARTITION BY DATE(anchor_timestamp)" in sql
    assert "CLUSTER BY anchor_timestamp" in sql
    assert "CROSS JOIN UNNEST" in sql
    assert "TIMESTAMP_MICROS(DIV(UNIX_MICROS(q.actual_quote_timestamp), 30000000) * 30000000)" in sql
    assert "TIMESTAMP_SUB(" in sql
    assert "INTERVAL 30 SECOND" in sql
    assert "JOIN `example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_anchor_ethusd` a" in sql
    assert "a.anchor_timestamp = candidate_anchor_timestamp" in sql
    assert "q.actual_quote_timestamp >= candidate_anchor_timestamp" in sql
    assert (
        "q.actual_quote_timestamp < TIMESTAMP_ADD(candidate_anchor_timestamp, INTERVAL 1 MINUTE)"
        in sql
    )
    assert "ARRAY_AGG(STRUCT(actual_quote_timestamp, bid, ask)" in sql
    assert "ORDER BY actual_quote_timestamp, bid, ask" in sql
    assert "ORDER BY anchor_timestamp" not in sql


def test_false_null_quote_anchor_sql_checks_missing_anchors() -> None:
    sql = phase115.build_false_null_quote_anchor_sql(
        phase115.quote_anchor_table("BTCUSD"),
        phase115.raw_quote_stage_table("BTCUSD"),
    )

    assert "false_null_count" in sql
    assert "`example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_quote_anchor_btcusd`" in sql
    assert "`example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_raw_quote_stage_btcusd`" in sql
    assert "qa.actual_quote_timestamp IS NULL" in sql
    assert "q.actual_quote_timestamp >= qa.anchor_timestamp" in sql
    assert "q.actual_quote_timestamp < TIMESTAMP_ADD(qa.anchor_timestamp, INTERVAL 1 MINUTE)" in sql


def test_parse_pairs_accepts_comma_separated_subset() -> None:
    assert phase115.parse_pairs("BTCUSD") == ("BTCUSD",)
    assert phase115.parse_pairs("ethusd,btcusd") == ("ETHUSD", "BTCUSD")
    assert phase115.parse_pairs(" BTCUSD , ETHUSD ") == ("BTCUSD", "ETHUSD")


def test_canary_quote_anchor_grid_sql_compares_old_and_new_paths() -> None:
    sidecar = {
        "destination_tables": {
            "BTCUSD": "example-gcp-project:fx_tick_data.v5_1_imbalance_events_btcusd"
        }
    }
    sql = phase115.build_canary_quote_anchor_grid_diff_sql(
        "BTCUSD",
        "2024-05-01T00:00:00Z",
        "2024-05-02T00:00:00Z",
        sidecar=sidecar,
    )

    assert "old_range_join AS" in sql
    assert "new_quote_grid AS" in sql
    assert "EXCEPT DISTINCT" in sql
    assert "old_minus_new_count" in sql
    assert "new_minus_old_count" in sql
    assert "CROSS JOIN UNNEST" in sql
    assert "ORDER BY q.timestamp, q.bidPrice, q.askPrice" in sql
    assert "ORDER BY actual_quote_timestamp, bid, ask" in sql


def test_month_shards_cover_is_window_without_overlap() -> None:
    shards = phase115.month_shards()

    assert shards[0] == ("2024-05-01T00:00:00Z", "2024-06-01T00:00:00Z")
    assert shards[-1] == ("2025-10-01T00:00:00Z", "2025-11-01T00:00:00Z")
    assert len(shards) == 18
    assert shards[0][0] == phase115.IS_START
    assert shards[-1][1] == phase115.IS_END_EXCLUSIVE
    for previous, current in zip(shards, shards[1:]):
        assert previous[1] == current[0]


def test_quote_anchor_shard_table_names_include_pair_and_month() -> None:
    assert (
        phase115.quote_anchor_shard_table("BTCUSD", "2024-05-01T00:00:00Z")
        == "example-gcp-project.fx_tick_data."
        "v5_1_is_backtest_fwer_quote_anchor_shard_btcusd_202405"
    )
    assert (
        phase115.quote_anchor_shard_table(
            "ETHUSD",
            "2025-10-01T00:00:00Z",
            prefix="custom_quote_anchor_shard",
        )
        == "example-gcp-project.fx_tick_data.custom_quote_anchor_shard_ethusd_202510"
    )


def test_materialize_quote_anchor_shard_sql_bounds_raw_tick_scan() -> None:
    sidecar = {
        "destination_tables": {
            "BTCUSD": "example-gcp-project:fx_tick_data.v5_1_imbalance_events_btcusd"
        }
    }
    destination_table = phase115.quote_anchor_shard_table(
        "BTCUSD",
        "2024-05-01T00:00:00Z",
    )

    sql = phase115.build_materialize_quote_anchor_shard_sql(
        "BTCUSD",
        destination_table,
        "2024-05-01T00:00:00Z",
        "2024-06-01T00:00:00Z",
        sidecar=sidecar,
    )

    assert "CREATE OR REPLACE TABLE" in sql
    assert "v5_1_is_backtest_fwer_quote_anchor_shard_btcusd_202405" in sql
    assert "entry_timestamp >= TIMESTAMP('2024-05-01T00:00:00Z')" in sql
    assert "entry_timestamp < TIMESTAMP('2024-06-01T00:00:00Z')" in sql
    assert "SELECT DISTINCT entry_anchor_timestamp AS anchor_timestamp" in sql
    assert "SELECT DISTINCT exit_anchor_timestamp AS anchor_timestamp" in sql
    assert "raw_quotes AS" in sql
    assert "JOIN raw_quotes q" in sql
    assert "q.timestamp >= a.anchor_timestamp" in sql
    assert "q.timestamp < TIMESTAMP_ADD(a.anchor_timestamp, INTERVAL 1 MINUTE)" in sql
    assert "q.timestamp >= TIMESTAMP('2024-05-01T00:00:00Z')" in sql
    assert (
        "q.timestamp < TIMESTAMP_ADD(TIMESTAMP('2024-06-01T00:00:00Z'), INTERVAL 6 MINUTE)"
        in sql
    )
    assert "ORDER BY q.timestamp, q.bidPrice, q.askPrice" in sql


def test_materialize_quote_anchor_union_sql_deduplicates_anchor_timestamps() -> None:
    sql = phase115.build_materialize_quote_anchor_union_sql(
        "ETHUSD",
        "example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_quote_anchor_ethusd",
        [
            "example-gcp-project.fx_tick_data."
            "v5_1_is_backtest_fwer_quote_anchor_shard_ethusd_202405",
            "example-gcp-project.fx_tick_data."
            "v5_1_is_backtest_fwer_quote_anchor_shard_ethusd_202406",
        ],
    )

    assert "CREATE OR REPLACE TABLE" in sql
    assert "UNION ALL" in sql
    assert "ROW_NUMBER() OVER (" in sql
    assert "PARTITION BY anchor_timestamp" in sql
    assert "ORDER BY actual_quote_timestamp IS NULL, actual_quote_timestamp, bid, ask" in sql
    assert "ORDER BY actual_quote_timestamp" in sql
    assert "AS anchor_rn" in sql
    assert "WHERE anchor_rn = 1" in sql


def test_quote_anchor_cell_summary_sql_avoids_raw_tick_join() -> None:
    sidecar = {
        "destination_tables": {
            "ETHUSD": "example-gcp-project:fx_tick_data.v5_1_imbalance_events_ethusd"
        }
    }
    quote_anchor_source = phase115.quote_anchor_table("ETHUSD")

    sql = phase115.build_is_cell_summary_from_quote_anchor_sql(
        "ETHUSD",
        quote_anchor_source,
        sidecar=sidecar,
    )

    assert "`example-gcp-project.fx_tick_data.v5_1_imbalance_events_ethusd`" in sql
    assert "`example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_quote_anchor_ethusd`" in sql
    assert "`example-gcp-project.fx_tick_data.ethusd_ticks`" not in sql
    assert "entry_anchor" in sql
    assert "exit_anchor" in sql
    assert "pnl_bps_net" in sql
    assert "COUNT(*) AS event_count" in sql


def test_quote_anchor_cell_summary_sql_counts_missing_anchors() -> None:
    sql = phase115.build_is_cell_summary_from_quote_anchor_sql(
        "BTCUSD",
        "example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_quote_anchor_btcusd",
    )

    assert "missing_entry_anchor_count" in sql
    assert "missing_exit_anchor_count" in sql
    assert "num_trades" in sql
    assert "COUNTIF(missing_entry_anchor)" in sql
    assert "COUNTIF(missing_exit_anchor)" in sql


def test_read_materialized_cell_summary_sql_orders_sealed_cell_family() -> None:
    sql = phase115.read_materialized_cell_summary_sql(
        "ETHUSD",
        "example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_cell_summary_ethusd",
    )

    assert "SELECT *" in sql
    assert "`example-gcp-project.fx_tick_data.v5_1_is_backtest_fwer_cell_summary_ethusd`" in sql
    assert "WHERE pair = 'ETHUSD'" in sql
    assert (
        "ORDER BY pair, score_family, lookback_seconds, threshold, direction, horizon_seconds"
        in sql
    )


def test_blocker_summary_is_fail_closed_and_preserves_handoff_fields() -> None:
    sidecar = {
        "phase114_blocked": False,
        "destination_tables": {"BTCUSD": "x", "ETHUSD": "y"},
    }

    summary = phase115.build_summary_doc(
        {pair: [] for pair in phase115.PAIRS},
        sidecar,
        blocked_reason="live_bq_runtime_exceeded",
    )

    assert summary["phase115_blocked"] is True
    assert summary["blocker_reason"] == "live_bq_runtime_exceeded"
    assert summary["fwer_denominator"] == 216
    assert summary["entry_granularity"] == "30s_bar"
    assert summary["eligible_cells"] == []


def test_blocker_summary_can_include_quote_anchor_grid_diagnostics() -> None:
    sidecar = {
        "phase114_blocked": False,
        "destination_tables": {"BTCUSD": "x", "ETHUSD": "y"},
    }
    diagnostics = {
        "strategy": "quote_to_30s_anchor_grid",
        "anchor_grid": "30s",
        "validation_passed": False,
        "pairs": {
            "BTCUSD": {
                "anchor_count": 10,
                "off_grid_anchor_count": 0,
                "duplicate_anchor_count": 0,
                "false_null_count": 1,
                "anchor_table": phase115.anchor_table("BTCUSD"),
                "raw_quote_stage_table": phase115.raw_quote_stage_table("BTCUSD"),
                "quote_anchor_table": phase115.quote_anchor_table("BTCUSD"),
            }
        },
    }

    summary = phase115.build_summary_doc(
        {pair: [] for pair in phase115.PAIRS},
        sidecar,
        blocked_reason="quote_anchor_grid_materialization_bq_failed:false_null_count:BTCUSD",
        quote_anchor_materialization=diagnostics,
    )

    assert summary["phase115_blocked"] is True
    assert summary["fwer_denominator"] == 216
    assert summary["entry_granularity"] == "30s_bar"
    assert summary["eligible_cells"] == []
    assert summary["quote_anchor_materialization"]["strategy"] == "quote_to_30s_anchor_grid"
    assert summary["quote_anchor_materialization"]["pairs"]["BTCUSD"]["false_null_count"] == 1
