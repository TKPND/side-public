"""v5.1 BTCUSD/ETHUSD tick data contract audit helper.

The v5.1 imbalance milestone may only claim a top-of-book quote imbalance
proxy unless source diagnostics prove stronger semantics. This helper emits
deterministic SQL, runs BQ when available, and writes a fail-closed blocker
report when live diagnostics cannot be produced.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PAIRS = ("BTCUSD", "ETHUSD")
PAIR_TABLE_MAP = {"BTCUSD": "btcusd_ticks", "ETHUSD": "ethusd_ticks"}
DEFAULT_PROJECT = "example-gcp-project"
DEFAULT_DATASET = "fx_tick_data"
IS_START = "2024-05-01T00:00:00Z"
OOS_END_EXCLUSIVE = "2026-05-01T00:00:00Z"
LOOKBACK_SECONDS = (30, 60, 300)
INTERPRETATION = "top-of-book quote imbalance proxy"


def _table_ref(project: str, dataset: str, table: str) -> str:
    return f"`{project}.{dataset}.{table}`"


def build_audit_sql(
    pair: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    table: str | None = None,
) -> str:
    """Build pair-level source diagnostics SQL."""
    table_name = table or PAIR_TABLE_MAP[pair]
    ref = _table_ref(project, dataset, table_name)
    return f"""
SELECT
  '{pair}' AS pair,
  COUNT(*) AS row_count,
  MIN(timestamp) AS min_timestamp,
  MAX(timestamp) AS max_timestamp,
  COUNTIF(timestamp IS NULL) AS null_timestamp_count,
  COUNTIF(bidPrice IS NULL) AS null_bid_count,
  COUNTIF(askPrice IS NULL) AS null_ask_count,
  COUNTIF(bidVolume IS NULL) AS null_bid_volume_count,
  COUNTIF(askVolume IS NULL) AS null_ask_volume_count,
  COUNTIF(bidVolume = 0) AS zero_bid_volume_count,
  COUNTIF(askVolume = 0) AS zero_ask_volume_count,
  COUNTIF(askPrice - bidPrice < 0) AS negative_spread_count,
  MIN(askPrice - bidPrice) AS min_spread,
  APPROX_QUANTILES(askPrice - bidPrice, 100)[OFFSET(50)] AS median_spread,
  MAX(askPrice - bidPrice) AS max_spread
FROM {ref}
WHERE timestamp >= TIMESTAMP('{IS_START}')
  AND timestamp < TIMESTAMP('{OOS_END_EXCLUSIVE}')
""".strip()


def build_month_sql(
    pair: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    table: str | None = None,
) -> str:
    """Build month-level row-count diagnostics SQL."""
    table_name = table or PAIR_TABLE_MAP[pair]
    ref = _table_ref(project, dataset, table_name)
    return f"""
SELECT
  '{pair}' AS pair,
  FORMAT_TIMESTAMP('%Y-%m', timestamp) AS month,
  COUNT(*) AS row_count,
  MIN(timestamp) AS min_timestamp,
  MAX(timestamp) AS max_timestamp
FROM {ref}
WHERE timestamp >= TIMESTAMP('{IS_START}')
  AND timestamp < TIMESTAMP('{OOS_END_EXCLUSIVE}')
GROUP BY month
ORDER BY month
""".strip()


def build_bucket_sql(
    pair: str,
    bucket_seconds: int,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    table: str | None = None,
) -> str:
    """Build non-contiguous bucket diagnostics SQL for one lookback bucket."""
    table_name = table or PAIR_TABLE_MAP[pair]
    ref = _table_ref(project, dataset, table_name)
    return f"""
WITH buckets AS (
  SELECT
    TIMESTAMP_SECONDS(DIV(UNIX_SECONDS(timestamp), {bucket_seconds}) * {bucket_seconds}) AS bucket_start,
    COUNT(*) AS tick_count
  FROM {ref}
  WHERE timestamp >= TIMESTAMP('{IS_START}')
    AND timestamp < TIMESTAMP('{OOS_END_EXCLUSIVE}')
  GROUP BY bucket_start
),
span AS (
  SELECT
    TIMESTAMP('{IS_START}') AS start_ts,
    TIMESTAMP('{OOS_END_EXCLUSIVE}') AS end_ts
)
SELECT
  '{pair}' AS pair,
  {bucket_seconds} AS bucket_seconds,
  COUNT(*) AS observed_bucket_count,
  DIV(TIMESTAMP_DIFF((SELECT end_ts FROM span), (SELECT start_ts FROM span), SECOND), {bucket_seconds}) AS expected_bucket_count,
  DIV(TIMESTAMP_DIFF((SELECT end_ts FROM span), (SELECT start_ts FROM span), SECOND), {bucket_seconds}) - COUNT(*) AS missing_bucket_count,
  COUNTIF(tick_count = 0) AS empty_observed_bucket_count,
  MIN(tick_count) AS min_ticks_per_observed_bucket,
  APPROX_QUANTILES(tick_count, 100)[OFFSET(50)] AS median_ticks_per_observed_bucket,
  MAX(tick_count) AS max_ticks_per_observed_bucket
FROM buckets
""".strip()


def run_bq_query(sql: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["bq", "query", "--use_legacy_sql=false", "--format=json", sql],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def blocker_report(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "v5.1-data-contract-1",
        "generated_at": _now_iso(),
        "interpretation": INTERPRETATION,
        "pairs": list(PAIRS),
        "lookback_seconds": list(LOOKBACK_SECONDS),
        "phase114_blocked": True,
        "blocker_reason": reason,
        "claims": {
            "l2_depth_claim": False,
            "market_depth_claim": False,
            "aggressor_flow_claim": False,
        },
        "diagnostics": {},
    }


def pass_report(diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "v5.1-data-contract-1",
        "generated_at": _now_iso(),
        "interpretation": INTERPRETATION,
        "pairs": list(PAIRS),
        "lookback_seconds": list(LOOKBACK_SECONDS),
        "phase114_blocked": False,
        "claims": {
            "l2_depth_claim": False,
            "market_depth_claim": False,
            "aggressor_flow_claim": False,
        },
        "diagnostics": diagnostics,
    }


def collect_diagnostics(project: str, dataset: str) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    for pair in PAIRS:
        pair_diag: dict[str, Any] = {}
        pair_diag["source"] = run_bq_query(
            build_audit_sql(pair, project=project, dataset=dataset)
        )
        pair_diag["months"] = run_bq_query(
            build_month_sql(pair, project=project, dataset=dataset)
        )
        pair_diag["buckets"] = {
            str(bucket): run_bq_query(
                build_bucket_sql(pair, bucket, project=project, dataset=dataset)
            )
            for bucket in LOOKBACK_SECONDS
        }
        diagnostics[pair] = pair_diag
    return diagnostics


def write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def render_markdown(report: dict[str, Any]) -> str:
    phase114_blocked = str(report["phase114_blocked"]).lower()
    lines = [
        "# v5.1 Tick Data Contract Report",
        "",
        "## Source Interpretation",
        "",
        f"- interpretation: {INTERPRETATION}",
        "- No exchange-native L2 order book depth is claimed.",
        "- No true aggressor trade flow is claimed.",
        "- `bidVolume` and `askVolume` are treated as Dukascopy top-of-book quote volume fields only.",
        "",
        "## BTCUSD Diagnostics",
        "",
        _render_pair(report, "BTCUSD"),
        "",
        "## ETHUSD Diagnostics",
        "",
        _render_pair(report, "ETHUSD"),
        "",
        "## Source Limitations",
        "",
        "- This report does not prove exchange-native L2 market depth.",
        "- This report does not prove aggressor-initiated trade flow.",
        "- If `bidVolume`/`askVolume` semantics later fail audit, the volume-ratio family must fail-close without shrinking the sealed FWER denominator.",
        "",
        "## Phase 114 Gate",
        "",
        f"- phase114_blocked: {phase114_blocked}",
    ]
    if report["phase114_blocked"]:
        lines.append(f"- blocker_reason: {report.get('blocker_reason', 'unknown')}")
    else:
        lines.append("- existing_bq_inputs_usable_under_contract: true")
    return "\n".join(lines) + "\n"


def _render_pair(report: dict[str, Any], pair: str) -> str:
    if report["phase114_blocked"]:
        return "- diagnostics unavailable; see Phase 114 blocker."
    pair_diag = report.get("diagnostics", {}).get(pair, {})
    source = pair_diag.get("source", [])
    if not source:
        return "- diagnostics missing; report should be treated as blocked."
    row = source[0]
    fields = [
        "row_count",
        "min_timestamp",
        "max_timestamp",
        "null_timestamp_count",
        "null_bid_count",
        "null_ask_count",
        "null_bid_volume_count",
        "null_ask_volume_count",
        "zero_bid_volume_count",
        "zero_ask_volume_count",
        "negative_spread_count",
        "min_spread",
        "median_spread",
        "max_spread",
    ]
    bullets = [f"- {field}: {row.get(field)}" for field in fields]
    months = pair_diag.get("months", [])
    buckets = pair_diag.get("buckets", {})
    bullets.append(f"- month_rows_emitted: {len(months)}")
    for bucket in LOOKBACK_SECONDS:
        bucket_rows = buckets.get(str(bucket), [])
        missing = bucket_rows[0].get("missing_bucket_count") if bucket_rows else "missing"
        bullets.append(f"- missing_bucket_count_{bucket}s: {missing}")
    return "\n".join(bullets)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report))


def dry_run_sql(project: str, dataset: str) -> str:
    chunks: list[str] = []
    for pair in PAIRS:
        chunks.append(f"-- {pair} source diagnostics")
        chunks.append(build_audit_sql(pair, project=project, dataset=dataset))
        chunks.append(f"-- {pair} month diagnostics")
        chunks.append(build_month_sql(pair, project=project, dataset=dataset))
        for bucket in LOOKBACK_SECONDS:
            chunks.append(f"-- {pair} {bucket}s bucket diagnostics")
            chunks.append(build_bucket_sql(pair, bucket, project=project, dataset=dataset))
    return "\n\n".join(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("reports/v5.1/tick_data_contract_report.json"),
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=Path("reports/v5.1/tick_data_contract_report.md"),
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        print(dry_run_sql(args.project, args.dataset))
        return 0

    if shutil.which("bq") is None:
        report = blocker_report("bq CLI unavailable; live source diagnostics not produced")
    else:
        try:
            report = pass_report(collect_diagnostics(args.project, args.dataset))
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            report = blocker_report(f"BQ diagnostics failed: {exc}")

    write_json(report, args.output_json)
    write_markdown(report, args.output_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
