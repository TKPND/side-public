"""v5.1 BTCUSD/ETHUSD top-of-book imbalance feature generator.

Phase 114 consumes the Phase 113 claim SEAL literally. The feature helpers in
this module are intentionally deterministic and leakage-safe: feature windows
are half-open and must end before the entry decision.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PAIRS = ("BTCUSD", "ETHUSD")
PAIR_TABLE_MAP = {"BTCUSD": "btcusd_ticks", "ETHUSD": "ethusd_ticks"}
SCORE_FAMILIES = (
    "volume_ratio_imbalance",
    "mid_price_tick_direction_imbalance",
)
LOOKBACK_SECONDS = (30, 60, 300)
THRESHOLDS = ("p80", "p90", "p95")
DIRECTIONS = ("mean_reversion", "momentum")
HORIZON_SECONDS = (60, 180, 300)
FWER_DENOMINATOR = 216
INTERPRETATION = "top-of-book quote imbalance proxy"

DEFAULT_PROJECT = "example-gcp-project"
DEFAULT_DATASET = "fx_tick_data"
DEFAULT_DESTINATION_TABLE_PREFIX = "v5_1_imbalance_events"
IS_START = "2024-05-01T00:00:00Z"
IS_END_EXCLUSIVE = "2025-11-01T00:00:00Z"
FULL_END_EXCLUSIVE = "2026-05-01T00:00:00Z"
ENTRY_BUCKET_SECONDS = 30
DATA_CONTRACT_REPORT = Path("reports/v5.1/tick_data_contract_report.json")
DEFAULT_SIDECAR = Path("reports/v5.1/imbalance_feature_sidecar.json")
DEFAULT_REPORT = Path("reports/v5.1/imbalance_feature_report.md")


@dataclass(frozen=True)
class Tick:
    timestamp: datetime
    bid_price: float
    ask_price: float
    bid_volume: float
    ask_volume: float

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def volume_ratio_imbalance(
    bid_volume: float, ask_volume: float
) -> tuple[float | None, str | None]:
    denominator = bid_volume + ask_volume
    if denominator <= 0.0:
        return None, "zero_volume_denominator"
    return (bid_volume - ask_volume) / denominator, None


def mid_price_tick_direction_imbalance(
    mid_prices: list[float],
) -> tuple[float | None, str | None]:
    up_ticks = 0
    down_ticks = 0
    for previous, current in zip(mid_prices, mid_prices[1:], strict=False):
        if current > previous:
            up_ticks += 1
        elif current < previous:
            down_ticks += 1

    total = up_ticks + down_ticks
    if total == 0:
        return None, "zero_mid_tick_denominator"
    return (up_ticks - down_ticks) / total, None


def ticks_in_feature_window(
    ticks: list[Tick],
    bucket_start: datetime,
    bucket_end: datetime,
) -> list[Tick]:
    """Return ticks in the half-open feature window `[bucket_start, bucket_end)`."""
    return [
        tick
        for tick in ticks
        if bucket_start <= tick.timestamp < bucket_end
    ]


def feature_scores(
    ticks: list[Tick],
) -> tuple[dict[str, float | None], str | None]:
    bid_volume = sum(tick.bid_volume for tick in ticks)
    ask_volume = sum(tick.ask_volume for tick in ticks)
    volume_score, volume_reason = volume_ratio_imbalance(bid_volume, ask_volume)

    mid_prices = [tick.mid_price for tick in ticks]
    direction_score, direction_reason = mid_price_tick_direction_imbalance(mid_prices)

    reasons = [reason for reason in (volume_reason, direction_reason) if reason]
    return (
        {
            "volume_ratio_imbalance": volume_score,
            "mid_price_tick_direction_imbalance": direction_score,
        },
        ";".join(reasons) if reasons else None,
    )


def _table_ref(project: str, dataset: str, table: str) -> str:
    return f"`{project}.{dataset}.{table}`"


def destination_table(pair: str, dataset: str, prefix: str) -> str:
    return f"{dataset}.{prefix}_{pair.lower()}"


def build_feature_sql(
    pair: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
) -> str:
    """Build deterministic SQL for v5.1 event materialization.

    The query emits event rows only. Threshold values are derived from IS rows
    and then applied to the full IS+OOS span without changing the sealed family.
    """
    table = PAIR_TABLE_MAP[pair]
    ref = _table_ref(project, dataset, table)
    horizons = ", ".join(str(v) for v in HORIZON_SECONDS)
    feature_chunks = []
    for lookback in LOOKBACK_SECONDS:
        preceding_rows = (lookback // ENTRY_BUCKET_SECONDS) - 1
        feature_chunks.extend(
            [
                f"""
  SELECT
    pair,
    entry_timestamp,
    'volume_ratio_imbalance' AS score_family,
    {lookback} AS lookback_seconds,
    TIMESTAMP_SUB(entry_timestamp, INTERVAL {lookback} SECOND) AS bucket_start,
    entry_timestamp AS bucket_end,
    SAFE_DIVIDE(
      SUM(bidVolume) OVER lookback_window - SUM(askVolume) OVER lookback_window,
      SUM(bidVolume) OVER lookback_window + SUM(askVolume) OVER lookback_window
    ) AS imbalance_score,
    source_table,
    IF(
      SUM(bidVolume) OVER lookback_window + SUM(askVolume) OVER lookback_window <= 0,
      'zero_volume_denominator',
      NULL
    ) AS missing_reason
  FROM scored_ticks
  WINDOW lookback_window AS (
    ORDER BY timestamp_micros
    ROWS BETWEEN {preceding_rows} PRECEDING AND CURRENT ROW
  )
""".strip(),
                f"""
  SELECT
    pair,
    entry_timestamp,
    'mid_price_tick_direction_imbalance' AS score_family,
    {lookback} AS lookback_seconds,
    TIMESTAMP_SUB(entry_timestamp, INTERVAL {lookback} SECOND) AS bucket_start,
    entry_timestamp AS bucket_end,
    SAFE_DIVIDE(
      SUM(up_mid_tick) OVER lookback_window - SUM(down_mid_tick) OVER lookback_window,
      SUM(up_mid_tick) OVER lookback_window + SUM(down_mid_tick) OVER lookback_window
    ) AS imbalance_score,
    source_table,
    IF(
      SUM(up_mid_tick) OVER lookback_window + SUM(down_mid_tick) OVER lookback_window = 0,
      'zero_mid_tick_denominator',
      NULL
    ) AS missing_reason
  FROM scored_ticks
  WINDOW lookback_window AS (
    ORDER BY timestamp_micros
    ROWS BETWEEN {preceding_rows} PRECEDING AND CURRENT ROW
  )
""".strip(),
            ]
        )
    feature_sql = "\n  UNION ALL\n".join(feature_chunks)
    return f"""
-- v5.1 {pair} {INTERPRETATION}
-- Thresholds p80/p90/p95 are computed from IS only:
-- timestamp >= TIMESTAMP('{IS_START}') AND timestamp < TIMESTAMP('{IS_END_EXCLUSIVE}')
WITH tick_base AS (
  SELECT
    '{pair}' AS pair,
    timestamp,
    TIMESTAMP_SECONDS(DIV(UNIX_SECONDS(timestamp), {ENTRY_BUCKET_SECONDS}) * {ENTRY_BUCKET_SECONDS}) AS entry_bucket_start,
    bidPrice,
    askPrice,
    bidVolume,
    askVolume,
    (bidPrice + askPrice) / 2 AS mid_price,
    '{ref}' AS source_table
  FROM {ref}
  WHERE timestamp >= TIMESTAMP('{IS_START}')
    AND timestamp < TIMESTAMP('{FULL_END_EXCLUSIVE}')
),
raw_ticks AS (
  SELECT
    *,
    LAG(mid_price) OVER (
      PARTITION BY entry_bucket_start
      ORDER BY timestamp
    ) AS previous_mid_price
  FROM tick_base
),
horizons AS (
  SELECT horizon_seconds FROM UNNEST([{horizons}]) AS horizon_seconds
),
scored_ticks AS (
  SELECT
    pair,
    TIMESTAMP_ADD(entry_bucket_start, INTERVAL {ENTRY_BUCKET_SECONDS} SECOND) AS entry_timestamp,
    UNIX_MICROS(TIMESTAMP_ADD(entry_bucket_start, INTERVAL {ENTRY_BUCKET_SECONDS} SECOND)) AS timestamp_micros,
    SUM(bidVolume) AS bidVolume,
    SUM(askVolume) AS askVolume,
    SUM(IF(mid_price > previous_mid_price, 1, 0)) AS up_mid_tick,
    SUM(IF(mid_price < previous_mid_price, 1, 0)) AS down_mid_tick,
    ANY_VALUE(source_table) AS source_table
  FROM raw_ticks
  GROUP BY pair, entry_bucket_start
),
features AS (
{feature_sql}
),
thresholds AS (
  SELECT
    pair,
    score_family,
    lookback_seconds,
    'p80' AS threshold,
    APPROX_QUANTILES(ABS(imbalance_score), 100)[OFFSET(80)] AS threshold_value
  FROM features
  WHERE entry_timestamp < TIMESTAMP('{IS_END_EXCLUSIVE}')
    AND imbalance_score IS NOT NULL
  GROUP BY pair, score_family, lookback_seconds
  UNION ALL
  SELECT
    pair,
    score_family,
    lookback_seconds,
    'p90' AS threshold,
    APPROX_QUANTILES(ABS(imbalance_score), 100)[OFFSET(90)] AS threshold_value
  FROM features
  WHERE entry_timestamp < TIMESTAMP('{IS_END_EXCLUSIVE}')
    AND imbalance_score IS NOT NULL
  GROUP BY pair, score_family, lookback_seconds
  UNION ALL
  SELECT
    pair,
    score_family,
    lookback_seconds,
    'p95' AS threshold,
    APPROX_QUANTILES(ABS(imbalance_score), 100)[OFFSET(95)] AS threshold_value
  FROM features
  WHERE entry_timestamp < TIMESTAMP('{IS_END_EXCLUSIVE}')
    AND imbalance_score IS NOT NULL
  GROUP BY pair, score_family, lookback_seconds
)
SELECT
  f.pair,
  f.entry_timestamp,
  f.score_family,
  f.lookback_seconds,
  threshold,
  threshold_value,
  direction,
  h.horizon_seconds,
  f.imbalance_score,
  CASE WHEN f.imbalance_score >= threshold_value THEN 'positive_extreme'
       WHEN f.imbalance_score <= -threshold_value THEN 'negative_extreme'
       ELSE 'none' END AS signal_side,
  f.bucket_start,
  f.bucket_end,
  f.entry_timestamp AS entry_anchor_timestamp,
  TIMESTAMP_ADD(f.entry_timestamp, INTERVAL h.horizon_seconds SECOND) AS exit_anchor_timestamp,
  f.missing_reason,
  f.source_table
FROM features f
JOIN thresholds USING (pair, score_family, lookback_seconds)
CROSS JOIN horizons h
CROSS JOIN UNNEST(['mean_reversion', 'momentum']) AS direction
WHERE ABS(imbalance_score) >= threshold_value
""".strip()


def _destination_ref(project: str, table_id: str) -> str:
    if table_id.count(".") == 2:
        return f"`{table_id}`"
    return f"`{project}.{table_id}`"


def build_event_count_sql(project: str, dataset: str, table: str) -> str:
    ref = _destination_ref(project, f"{dataset}.{table}" if "." not in table else table)
    return f"""
SELECT
  pair,
  score_family,
  lookback_seconds,
  threshold,
  direction,
  horizon_seconds,
  COUNT(*) AS event_count
FROM {ref}
GROUP BY pair, score_family, lookback_seconds, threshold, direction, horizon_seconds
ORDER BY pair, score_family, lookback_seconds, threshold, direction, horizon_seconds
""".strip()


def build_missing_count_sql(project: str, dataset: str, table: str) -> str:
    ref = _destination_ref(project, f"{dataset}.{table}" if "." not in table else table)
    return f"""
SELECT
  pair,
  COUNTIF(bucket_start IS NULL) AS missing_buckets,
  COUNTIF(entry_anchor_timestamp IS NULL) AS missing_entry_anchors,
  COUNTIF(exit_anchor_timestamp IS NULL) AS missing_exit_anchors,
  COUNTIF(imbalance_score IS NULL OR missing_reason IS NOT NULL) AS invalid_scores
FROM {ref}
GROUP BY pair
ORDER BY pair
""".strip()


def load_data_contract(path: Path = DATA_CONTRACT_REPORT) -> dict[str, Any]:
    if not path.exists():
        return {
            "phase114_blocked": True,
            "blocker_reason": f"missing data contract report: {path}",
            "diagnostics": {},
        }
    return json.loads(path.read_text())


def build_sidecar(
    data_contract: dict[str, Any],
    event_counts: dict[str, Any] | None = None,
    missing_counts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    phase114_blocked = bool(data_contract.get("phase114_blocked", True))
    return {
        "schema_version": "v5.1-imbalance-features-1",
        "generated_at": _now_iso(),
        "git_commit": _git_commit(),
        "script": "scripts/v5_1_generate_imbalance_features.py",
        "query_version": "v5.1-imbalance-features-sql-1",
        "interpretation": INTERPRETATION,
        "phase114_blocked": phase114_blocked,
        "blocker_reason": data_contract.get("blocker_reason"),
        "fwer_denominator": FWER_DENOMINATOR,
        "pairs": list(PAIRS),
        "score_families": list(SCORE_FAMILIES),
        "lookback_seconds": list(LOOKBACK_SECONDS),
        "thresholds": list(THRESHOLDS),
        "directions": list(DIRECTIONS),
        "horizon_seconds": list(HORIZON_SECONDS),
        "source_tables": PAIR_TABLE_MAP,
        "source_anchors": _source_anchors(data_contract),
        "event_counts": event_counts or _empty_event_counts(),
        "missing_counts": missing_counts or _empty_missing_counts(),
    }


def _source_anchors(data_contract: dict[str, Any]) -> dict[str, Any]:
    anchors: dict[str, Any] = {}
    diagnostics = data_contract.get("diagnostics", {})
    for pair in PAIRS:
        pair_diag = diagnostics.get(pair, {})
        source_rows = pair_diag.get("source", [])
        source = source_rows[0] if source_rows else {}
        buckets = pair_diag.get("buckets", {})
        anchors[pair] = {
            "row_count": source.get("row_count"),
            "min_timestamp": source.get("min_timestamp"),
            "max_timestamp": source.get("max_timestamp"),
            "missing_bucket_counts": {
                str(bucket): (
                    (buckets.get(str(bucket)) or [{}])[0].get("missing_bucket_count")
                    if isinstance(buckets.get(str(bucket)), list)
                    else None
                )
                for bucket in LOOKBACK_SECONDS
            },
        }
    return anchors


def _empty_event_counts() -> dict[str, Any]:
    counts: dict[str, Any] = {}
    for pair in PAIRS:
        counts[pair] = {}
        for score_family in SCORE_FAMILIES:
            counts[pair][score_family] = {}
            for lookback in LOOKBACK_SECONDS:
                counts[pair][score_family][str(lookback)] = {}
                for threshold in THRESHOLDS:
                    counts[pair][score_family][str(lookback)][threshold] = {}
                    for direction in DIRECTIONS:
                        counts[pair][score_family][str(lookback)][threshold][direction] = {
                            str(horizon): 0 for horizon in HORIZON_SECONDS
                        }
    return counts


def _empty_missing_counts() -> dict[str, Any]:
    return {
        pair: {
            "missing_buckets": 0,
            "missing_entry_anchors": 0,
            "missing_exit_anchors": 0,
            "invalid_scores": 0,
        }
        for pair in PAIRS
    }


def _event_counts_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _empty_event_counts()
    for row in rows:
        pair = row["pair"]
        score_family = row["score_family"]
        lookback = str(row["lookback_seconds"])
        threshold = row["threshold"]
        direction = row["direction"]
        horizon = str(row["horizon_seconds"])
        counts[pair][score_family][lookback][threshold][direction][horizon] = int(
            row["event_count"]
        )
    return counts


def _missing_counts_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _empty_missing_counts()
    for row in rows:
        pair = row["pair"]
        counts[pair] = {
            "missing_buckets": int(row["missing_buckets"]),
            "missing_entry_anchors": int(row["missing_entry_anchors"]),
            "missing_exit_anchors": int(row["missing_exit_anchors"]),
            "invalid_scores": int(row["invalid_scores"]),
        }
    return counts


def run_bq_query(sql: str, project: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "bq",
            "query",
            f"--project_id={project}",
            "--use_legacy_sql=false",
            "--format=json",
            "--max_rows=1000000",
        ],
        check=True,
        capture_output=True,
        input=sql,
        text=True,
    )
    return json.loads(result.stdout)


def materialize_pair(
    pair: str,
    project: str,
    source_dataset: str,
    destination_dataset: str,
    destination_prefix: str,
    dry_run: bool = False,
) -> str:
    table_id = destination_table(pair, destination_dataset, destination_prefix)
    cmd = [
        "bq",
        "query",
        f"--project_id={project}",
        "--use_legacy_sql=false",
        f"--destination_table={project}:{table_id}",
        "--replace",
        "--max_rows=0",
    ]
    if dry_run:
        cmd.append("--dry_run")
    subprocess.run(
        cmd,
        check=True,
        input=build_feature_sql(pair, project, source_dataset),
        text=True,
    )
    return table_id


def collect_materialized_counts(
    project: str,
    destination_dataset: str,
    tables: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    event_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []
    for table in tables:
        event_rows.extend(
            run_bq_query(build_event_count_sql(project, destination_dataset, table), project)
        )
        missing_rows.extend(
            run_bq_query(build_missing_count_sql(project, destination_dataset, table), project)
        )
    return _event_counts_from_rows(event_rows), _missing_counts_from_rows(missing_rows)


def write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def render_markdown(sidecar: dict[str, Any]) -> str:
    phase115_gate = "blocked" if sidecar["phase114_blocked"] else "ready"
    return "\n".join(
        [
            "# v5.1 Imbalance Feature Report",
            "",
            "## Source Interpretation",
            "",
            f"- interpretation: {INTERPRETATION}",
            "- No exchange-native L2 order book depth is claimed.",
            "- No true aggressor trade flow is claimed.",
            f"- fwer_denominator: {FWER_DENOMINATOR}",
            "",
            "## Event Counts",
            "",
            "- Sidecar field: `event_counts`",
            "- Sealed family branches are retained, including zero-event cells.",
            "",
            "## Missing Counts",
            "",
            "- Sidecar field: `missing_counts`",
            "- Missing entry/exit anchors and invalid scores fail close downstream.",
            "",
            "## Phase 115 Gate",
            "",
            f"- phase115_gate: {phase115_gate}",
            f"- phase114_blocked: {str(sidecar['phase114_blocked']).lower()}",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--materialize-bq",
        action="store_true",
        help="Run live BigQuery materialization into versioned event tables",
    )
    parser.add_argument(
        "--bq-dry-run",
        action="store_true",
        help="Run BigQuery dry-run jobs for materialization SQL",
    )
    parser.add_argument(
        "--refresh-bq-counts",
        action="store_true",
        help="Refresh sidecar counts from existing destination tables",
    )
    parser.add_argument("--destination-dataset", default=DEFAULT_DATASET)
    parser.add_argument(
        "--destination-table-prefix",
        default=DEFAULT_DESTINATION_TABLE_PREFIX,
    )
    parser.add_argument("--output-sidecar", type=Path, default=DEFAULT_SIDECAR)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    if args.dry_run:
        for pair in PAIRS:
            print(f"--- DRY RUN SQL: {pair} ---")
            print(build_feature_sql(pair, args.project, args.dataset))
        return 0

    data_contract = load_data_contract()
    event_counts = None
    missing_counts = None
    destination_tables: list[str] = []

    if args.materialize_bq or args.bq_dry_run:
        if shutil.which("bq") is None:
            raise RuntimeError("bq CLI unavailable; live materialization not produced")
        for pair in PAIRS:
            table_id = materialize_pair(
                pair,
                project=args.project,
                source_dataset=args.dataset,
                destination_dataset=args.destination_dataset,
                destination_prefix=args.destination_table_prefix,
                dry_run=args.bq_dry_run,
            )
            destination_tables.append(table_id)
            print(f"materialized {pair} into {args.project}:{table_id}")

        if not args.bq_dry_run:
            event_counts, missing_counts = collect_materialized_counts(
                args.project,
                args.destination_dataset,
                destination_tables,
            )
    elif args.refresh_bq_counts:
        if shutil.which("bq") is None:
            raise RuntimeError("bq CLI unavailable; live counts not refreshed")
        destination_tables = [
            destination_table(pair, args.destination_dataset, args.destination_table_prefix)
            for pair in PAIRS
        ]
        event_counts, missing_counts = collect_materialized_counts(
            args.project,
            args.destination_dataset,
            destination_tables,
        )

    sidecar = build_sidecar(
        data_contract,
        event_counts=event_counts,
        missing_counts=missing_counts,
    )
    if destination_tables:
        sidecar["destination_tables"] = {
            pair: f"{args.project}:{table_id}"
            for pair, table_id in zip(PAIRS, destination_tables, strict=True)
        }
    write_json(sidecar, args.output_sidecar)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_markdown(sidecar))
    print(f"wrote {args.output_sidecar}")
    print(f"wrote {args.output_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
