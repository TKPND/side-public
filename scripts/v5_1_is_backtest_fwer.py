"""v5.1 imbalance IS backtest and Holm FWER artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from statsmodels.stats.multitest import multipletests

import v5_1_generate_imbalance_features as features

PAIRS = features.PAIRS
PAIR_TABLE_MAP = features.PAIR_TABLE_MAP
SCORE_FAMILIES = features.SCORE_FAMILIES
LOOKBACK_SECONDS = features.LOOKBACK_SECONDS
THRESHOLDS = features.THRESHOLDS
DIRECTIONS = features.DIRECTIONS
HORIZON_SECONDS = features.HORIZON_SECONDS
FWER_DENOMINATOR = features.FWER_DENOMINATOR

DEFAULT_PROJECT = features.DEFAULT_PROJECT
DEFAULT_DATASET = features.DEFAULT_DATASET
IS_START = features.IS_START
IS_END_EXCLUSIVE = features.IS_END_EXCLUSIVE
ENTRY_GRANULARITY = "30s_bar"

CLAIM_DOC = Path("docs/v5.1_tick_imbalance_claim.md")
FEATURE_SIDECAR = Path("reports/v5.1/imbalance_feature_sidecar.json")
REPORT_DIR = Path("reports/v5.1")
SUMMARY_JSON = REPORT_DIR / "is_backtest_fwer_summary.json"
SUMMARY_MD = REPORT_DIR / "is_backtest_fwer_summary.md"
DEFAULT_MATERIALIZED_TABLE_PREFIX = "v5_1_is_backtest_fwer_cell_summary"
DEFAULT_ANCHOR_TABLE_PREFIX = "v5_1_is_backtest_fwer_anchor"
DEFAULT_RAW_QUOTE_STAGE_TABLE_PREFIX = "v5_1_is_backtest_fwer_raw_quote_stage"
DEFAULT_QUOTE_ANCHOR_TABLE_PREFIX = "v5_1_is_backtest_fwer_quote_anchor"
DEFAULT_QUOTE_ANCHOR_SHARD_TABLE_PREFIX = "v5_1_is_backtest_fwer_quote_anchor_shard"

ALPHA = 0.05
PF_HURDLE = 1.5
FEE_BPS_ROUNDTRIP = 70.0
SLIPPAGE_BPS_ROUNDTRIP = 2.0
RAW_PVALUE_SEED = 515115
RAW_PVALUE_SAMPLES = 1000
_EPS = 1e-12


def parse_pairs(value: str | None) -> tuple[str, ...]:
    if not value:
        return PAIRS
    pairs = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    unsupported = sorted(set(pairs) - set(PAIRS))
    if unsupported:
        raise ValueError(f"unsupported pairs: {unsupported}")
    if not pairs:
        raise ValueError("at least one pair is required")
    return pairs


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


def load_feature_sidecar(path: Path = FEATURE_SIDECAR) -> dict[str, Any]:
    if not path.exists():
        return {
            "phase114_blocked": True,
            "blocker_reason": f"missing feature sidecar: {path}",
            "destination_tables": {},
        }
    return json.loads(path.read_text())


def stance_for_event(direction: str, signal_side: str) -> int:
    if direction == "momentum" and signal_side == "positive_extreme":
        return 1
    if direction == "momentum" and signal_side == "negative_extreme":
        return -1
    if direction == "mean_reversion" and signal_side == "positive_extreme":
        return -1
    if direction == "mean_reversion" and signal_side == "negative_extreme":
        return 1
    raise ValueError(f"unsupported direction/signal_side: {direction}/{signal_side}")


def quote_side_pnl_bps(
    stance: int,
    entry_bid: float,
    entry_ask: float,
    exit_bid: float,
    exit_ask: float,
    fee_bps_roundtrip: float = FEE_BPS_ROUNDTRIP,
    slippage_bps_roundtrip: float = SLIPPAGE_BPS_ROUNDTRIP,
) -> dict[str, float]:
    if stance not in (-1, 1):
        raise ValueError(f"unsupported stance: {stance}")
    if min(entry_bid, entry_ask, exit_bid, exit_ask) <= 0.0:
        return {
            "entry_price": float("nan"),
            "exit_price": float("nan"),
            "pnl_bps_gross": float("nan"),
            "pnl_bps_net": float("nan"),
        }

    entry_price = entry_ask if stance == 1 else entry_bid
    exit_price = exit_bid if stance == 1 else exit_ask
    gross = stance * (exit_price - entry_price) / entry_price * 10_000.0
    explicit_cost = fee_bps_roundtrip + slippage_bps_roundtrip
    return {
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "pnl_bps_gross": float(gross),
        "pnl_bps_net": float(gross - explicit_cost),
    }


def profit_factor(pnl_bps: list[float] | np.ndarray) -> dict[str, Any]:
    arr = np.asarray(pnl_bps, dtype=float)
    arr = arr[np.isfinite(arr)]
    gross_profit = float(np.sum(arr[arr > 0.0])) if len(arr) else 0.0
    gross_loss = float(abs(np.sum(arr[arr < 0.0]))) if len(arr) else 0.0
    is_infinite = bool(gross_loss <= _EPS and gross_profit > 0.0)
    if len(arr) == 0:
        pf: float | None = None
        pf_value = 0.0
    elif is_infinite:
        pf = None
        pf_value = float("inf")
    elif gross_loss > 0.0:
        pf = float(gross_profit / gross_loss)
        pf_value = pf
    else:
        pf = 0.0
        pf_value = 0.0
    return {
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_profit_factor": pf,
        "net_profit_factor_value": pf_value,
        "net_profit_factor_is_infinite": is_infinite,
    }


def sparse_gate(num_trades: int, horizon_seconds: int) -> dict[str, Any]:
    min_trades = max(30, 10 * (int(horizon_seconds) // 60))
    passed = int(num_trades) >= min_trades
    return {
        "passed": passed,
        "min_trades": min_trades,
        "num_trades": int(num_trades),
        "reason": "passed" if passed else "sparse_cell",
    }


def raw_pvalue(pnl_bps: list[float] | np.ndarray) -> float:
    arr = np.asarray(pnl_bps, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 4:
        return 1.0
    observed = float(np.mean(arr))
    if abs(observed) <= _EPS:
        return 1.0
    centered = arr - observed
    rng = np.random.default_rng(RAW_PVALUE_SEED)
    means = np.array(
        [
            np.mean(rng.choice(centered, size=len(centered), replace=True))
            for _ in range(RAW_PVALUE_SAMPLES)
        ]
    )
    return float(np.mean(np.abs(means) >= abs(observed)))


def _cell_id(cell: dict[str, Any]) -> str:
    if cell.get("cell_id"):
        return str(cell["cell_id"])
    return (
        f"{cell['pair']}_{cell['score_family']}_l{cell['lookback_seconds']}_"
        f"{cell['threshold']}_{cell['direction']}_h{cell['horizon_seconds']}"
    )


def cell_metric_from_trades(
    cell: dict[str, Any],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    pnl = np.array([float(t["pnl_bps_net"]) for t in trades], dtype=float)
    fail_reasons: list[str] = []
    sparse = sparse_gate(len(pnl), int(cell["horizon_seconds"]))
    if not sparse["passed"]:
        fail_reasons.append("sparse_cell")
    if len(pnl) and not np.all(np.isfinite(pnl)):
        fail_reasons.append("non_finite_pnl")

    pf = profit_factor(pnl)
    p_raw = 1.0 if fail_reasons else raw_pvalue(pnl)
    pass_net_pf = bool(
        not fail_reasons
        and len(pnl) > 0
        and (
            pf["net_profit_factor_is_infinite"]
            or float(pf["net_profit_factor"] or 0.0) >= PF_HURDLE
        )
    )

    return {
        **cell,
        "cell_id": _cell_id(cell),
        "is_start": IS_START,
        "is_end_exclusive": IS_END_EXCLUSIVE,
        "entry_granularity": ENTRY_GRANULARITY,
        "num_trades": int(len(pnl)),
        "sparse_gate": sparse,
        "sparse_fail_close": not sparse["passed"],
        "gross_profit": pf["gross_profit"],
        "gross_loss": pf["gross_loss"],
        "net_profit_factor": pf["net_profit_factor"],
        "net_profit_factor_value": pf["net_profit_factor_value"],
        "net_profit_factor_is_infinite": pf["net_profit_factor_is_infinite"],
        "pf_hurdle": PF_HURDLE,
        "pass_net_pf": pass_net_pf,
        "p_raw": p_raw,
        "p_adj_holm": None,
        "pass_fwer": False,
        "is_eligible_for_phase116": False,
        "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "slippage_bps_roundtrip": SLIPPAGE_BPS_ROUNDTRIP,
        "mean_pnl_bps_net": float(np.mean(pnl)) if len(pnl) else None,
        "fail_reasons": fail_reasons,
    }


def _event_table_ref(sidecar: dict[str, Any], pair: str) -> str:
    raw = sidecar.get("destination_tables", {}).get(pair)
    if not raw:
        table = features.destination_table(
            pair,
            DEFAULT_DATASET,
            features.DEFAULT_DESTINATION_TABLE_PREFIX,
        )
        return f"{DEFAULT_PROJECT}.{table}"
    return str(raw).replace(":", ".")


def build_is_trade_sql(
    pair: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    sidecar: dict[str, Any] | None = None,
) -> str:
    sidecar = sidecar or load_feature_sidecar()
    event_ref = _event_table_ref(sidecar, pair)
    raw_ref = f"{project}.{dataset}.{PAIR_TABLE_MAP[pair]}"
    return f"""
WITH is_events AS (
  SELECT
    *,
    CASE
      WHEN direction = 'momentum' AND signal_side = 'positive_extreme' THEN 1
      WHEN direction = 'momentum' AND signal_side = 'negative_extreme' THEN -1
      WHEN direction = 'mean_reversion' AND signal_side = 'positive_extreme' THEN -1
      WHEN direction = 'mean_reversion' AND signal_side = 'negative_extreme' THEN 1
      ELSE 0
    END AS stance
  FROM `{event_ref}`
  WHERE entry_timestamp >= TIMESTAMP('{IS_START}')
    AND entry_timestamp < TIMESTAMP('{IS_END_EXCLUSIVE}')
),
entry_quotes AS (
  SELECT
    e.*,
    q.timestamp AS actual_entry_timestamp,
    q.bidPrice AS entry_bid,
    q.askPrice AS entry_ask,
    ROW_NUMBER() OVER (
      PARTITION BY e.pair, e.entry_timestamp, e.score_family, e.lookback_seconds,
                   e.threshold, e.direction, e.horizon_seconds
      ORDER BY q.timestamp
    ) AS entry_rn
  FROM is_events e
  JOIN `{raw_ref}` q
    ON q.timestamp >= e.entry_anchor_timestamp
   AND q.timestamp < TIMESTAMP_ADD(e.entry_anchor_timestamp, INTERVAL 1 MINUTE)
),
exit_quotes AS (
  SELECT
    e.*,
    q.timestamp AS actual_exit_timestamp,
    q.bidPrice AS exit_bid,
    q.askPrice AS exit_ask,
    ROW_NUMBER() OVER (
      PARTITION BY e.pair, e.entry_timestamp, e.score_family, e.lookback_seconds,
                   e.threshold, e.direction, e.horizon_seconds
      ORDER BY q.timestamp
    ) AS exit_rn
  FROM entry_quotes e
  JOIN `{raw_ref}` q
    ON q.timestamp >= e.exit_anchor_timestamp
   AND q.timestamp < TIMESTAMP_ADD(e.exit_anchor_timestamp, INTERVAL 1 MINUTE)
  WHERE e.entry_rn = 1
)
SELECT
  pair,
  entry_timestamp,
  score_family,
  lookback_seconds,
  threshold,
  threshold_value,
  direction,
  horizon_seconds,
  imbalance_score,
  signal_side,
  stance,
  bucket_start,
  bucket_end,
  entry_anchor_timestamp,
  exit_anchor_timestamp,
  actual_entry_timestamp,
  actual_exit_timestamp,
  entry_bid,
  entry_ask,
  exit_bid,
  exit_ask,
  source_table
FROM exit_quotes
WHERE exit_rn = 1
ORDER BY pair, score_family, lookback_seconds, threshold, direction, horizon_seconds, entry_timestamp
""".strip()


def build_is_cell_summary_sql(
    pair: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    sidecar: dict[str, Any] | None = None,
) -> str:
    trade_sql = build_is_trade_sql(pair, project, dataset, sidecar)
    return f"""
WITH trade_rows AS (
{trade_sql}
),
pnl_rows AS (
  SELECT
    pair,
    score_family,
    lookback_seconds,
    threshold,
    direction,
    horizon_seconds,
    CASE
      WHEN stance = 1 THEN entry_ask
      WHEN stance = -1 THEN entry_bid
      ELSE NULL
    END AS entry_price,
    CASE
      WHEN stance = 1 THEN exit_bid
      WHEN stance = -1 THEN exit_ask
      ELSE NULL
    END AS exit_price,
    {FEE_BPS_ROUNDTRIP} AS fee_bps_roundtrip,
    {SLIPPAGE_BPS_ROUNDTRIP} AS slippage_bps_roundtrip,
    (
      stance
      * (
        (CASE WHEN stance = 1 THEN exit_bid ELSE exit_ask END)
        - (CASE WHEN stance = 1 THEN entry_ask ELSE entry_bid END)
      )
      / NULLIF((CASE WHEN stance = 1 THEN entry_ask ELSE entry_bid END), 0)
      * 10000.0
    ) AS pnl_bps_gross,
    (
      stance
      * (
        (CASE WHEN stance = 1 THEN exit_bid ELSE exit_ask END)
        - (CASE WHEN stance = 1 THEN entry_ask ELSE entry_bid END)
      )
      / NULLIF((CASE WHEN stance = 1 THEN entry_ask ELSE entry_bid END), 0)
      * 10000.0
      - {FEE_BPS_ROUNDTRIP}
      - {SLIPPAGE_BPS_ROUNDTRIP}
    ) AS pnl_bps_net
  FROM trade_rows
),
aggregated AS (
  SELECT
    pair,
    score_family,
    lookback_seconds,
    threshold,
    direction,
    horizon_seconds,
    COUNT(*) AS num_trades,
    SUM(IF(pnl_bps_net > 0, pnl_bps_net, 0)) AS gross_profit,
    ABS(SUM(IF(pnl_bps_net < 0, pnl_bps_net, 0))) AS gross_loss,
    AVG(pnl_bps_net) AS mean_pnl_bps_net,
    STDDEV_SAMP(pnl_bps_net) AS stddev_pnl_bps_net,
    {FEE_BPS_ROUNDTRIP} AS fee_bps_roundtrip,
    {SLIPPAGE_BPS_ROUNDTRIP} AS slippage_bps_roundtrip
  FROM pnl_rows
  WHERE pnl_bps_net IS NOT NULL
  GROUP BY pair, score_family, lookback_seconds, threshold, direction, horizon_seconds
)
SELECT
  *,
  SAFE_DIVIDE(gross_profit, gross_loss) AS net_profit_factor,
  gross_loss <= 0 AND gross_profit > 0 AS net_profit_factor_is_infinite,
  CASE
    WHEN num_trades < 4 THEN 1.0
    WHEN stddev_pnl_bps_net IS NULL OR stddev_pnl_bps_net <= 0 THEN 1.0
    ELSE LEAST(
      1.0,
      EXP(-0.5 * POW(ABS(mean_pnl_bps_net / (stddev_pnl_bps_net / SQRT(num_trades))), 2))
    )
  END AS p_raw
FROM aggregated
ORDER BY pair, score_family, lookback_seconds, threshold, direction, horizon_seconds
""".strip()


def quote_anchor_table(
    pair: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    prefix: str = DEFAULT_QUOTE_ANCHOR_TABLE_PREFIX,
) -> str:
    return f"{project}.{dataset}.{prefix}_{pair.lower()}"


def anchor_table(
    pair: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    prefix: str = DEFAULT_ANCHOR_TABLE_PREFIX,
) -> str:
    return f"{project}.{dataset}.{prefix}_{pair.lower()}"


def raw_quote_stage_table(
    pair: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    prefix: str = DEFAULT_RAW_QUOTE_STAGE_TABLE_PREFIX,
) -> str:
    return f"{project}.{dataset}.{prefix}_{pair.lower()}"


def build_raw_quote_layout_inspection_sql(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
) -> str:
    return f"""
SELECT
  table_name,
  column_name,
  data_type,
  is_partitioning_column,
  clustering_ordinal_position
FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name IN ('btcusd_ticks', 'ethusd_ticks')
  AND column_name IN ('timestamp', 'bidPrice', 'askPrice')
ORDER BY table_name, ordinal_position
""".strip()


def build_materialize_anchor_table_sql(
    pair: str,
    destination_table: str,
    sidecar: dict[str, Any] | None = None,
) -> str:
    sidecar = sidecar or load_feature_sidecar()
    event_ref = _event_table_ref(sidecar, pair)
    return f"""
CREATE OR REPLACE TABLE `{destination_table}`
PARTITION BY DATE(anchor_timestamp)
CLUSTER BY anchor_timestamp AS
WITH is_events AS (
  SELECT
    entry_anchor_timestamp,
    exit_anchor_timestamp
  FROM `{event_ref}`
  WHERE entry_timestamp >= TIMESTAMP('{IS_START}')
    AND entry_timestamp < TIMESTAMP('{IS_END_EXCLUSIVE}')
),
anchor_timestamps AS (
  SELECT DISTINCT entry_anchor_timestamp AS anchor_timestamp
  FROM is_events
  WHERE entry_anchor_timestamp IS NOT NULL
  UNION DISTINCT
  SELECT DISTINCT exit_anchor_timestamp AS anchor_timestamp
  FROM is_events
  WHERE exit_anchor_timestamp IS NOT NULL
)
SELECT
  anchor_timestamp
FROM anchor_timestamps
WHERE anchor_timestamp < TIMESTAMP_ADD(TIMESTAMP('{IS_END_EXCLUSIVE}'), INTERVAL 6 MINUTE)
""".strip()


def build_anchor_grid_validation_sql(source_anchor_table: str) -> str:
    return f"""
SELECT
  COUNT(*) AS anchor_count,
  COUNTIF(MOD(UNIX_MICROS(anchor_timestamp), 30000000) != 0) AS off_grid_anchor_count
FROM `{source_anchor_table}`
""".strip()


def build_materialize_raw_quote_stage_sql(
    pair: str,
    destination_table: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
) -> str:
    raw_ref = f"{project}.{dataset}.{PAIR_TABLE_MAP[pair]}"
    return f"""
CREATE OR REPLACE TABLE `{destination_table}`
PARTITION BY DATE(actual_quote_timestamp)
CLUSTER BY actual_quote_timestamp AS
SELECT
  timestamp AS actual_quote_timestamp,
  bidPrice AS bid,
  askPrice AS ask
FROM `{raw_ref}`
WHERE timestamp >= TIMESTAMP('{IS_START}')
  AND timestamp < TIMESTAMP_ADD(TIMESTAMP('{IS_END_EXCLUSIVE}'), INTERVAL 6 MINUTE)
""".strip()


def build_materialize_quote_anchor_from_grid_sql(
    pair: str,
    destination_table: str,
    anchor_source_table: str,
    raw_stage_table: str,
) -> str:
    return f"""
CREATE OR REPLACE TABLE `{destination_table}`
PARTITION BY DATE(anchor_timestamp)
CLUSTER BY anchor_timestamp AS
WITH candidate_quotes AS (
  SELECT
    a.anchor_timestamp,
    q.actual_quote_timestamp,
    q.bid,
    q.ask
  FROM `{raw_stage_table}` q
  CROSS JOIN UNNEST([
    TIMESTAMP_MICROS(DIV(UNIX_MICROS(q.actual_quote_timestamp), 30000000) * 30000000),
    TIMESTAMP_SUB(
      TIMESTAMP_MICROS(DIV(UNIX_MICROS(q.actual_quote_timestamp), 30000000) * 30000000),
      INTERVAL 30 SECOND
    )
  ]) AS candidate_anchor_timestamp
  JOIN `{anchor_source_table}` a
    ON a.anchor_timestamp = candidate_anchor_timestamp
  WHERE q.actual_quote_timestamp >= candidate_anchor_timestamp
    AND q.actual_quote_timestamp < TIMESTAMP_ADD(candidate_anchor_timestamp, INTERVAL 1 MINUTE)
),
selected_quotes AS (
  SELECT
    anchor_timestamp,
    ARRAY_AGG(STRUCT(actual_quote_timestamp, bid, ask)
      ORDER BY actual_quote_timestamp, bid, ask
      LIMIT 1
    )[OFFSET(0)] AS quote
  FROM candidate_quotes
  GROUP BY anchor_timestamp
)
SELECT
  a.anchor_timestamp,
  q.quote.actual_quote_timestamp,
  q.quote.bid,
  q.quote.ask
FROM `{anchor_source_table}` a
LEFT JOIN selected_quotes q
  ON q.anchor_timestamp = a.anchor_timestamp
""".strip()


def build_canary_quote_anchor_grid_diff_sql(
    pair: str,
    canary_start: str,
    canary_end: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    sidecar: dict[str, Any] | None = None,
) -> str:
    sidecar = sidecar or load_feature_sidecar()
    event_ref = _event_table_ref(sidecar, pair)
    raw_ref = f"{project}.{dataset}.{PAIR_TABLE_MAP[pair]}"
    return f"""
WITH canary_events AS (
  SELECT
    entry_anchor_timestamp,
    exit_anchor_timestamp
  FROM `{event_ref}`
  WHERE entry_timestamp >= TIMESTAMP('{canary_start}')
    AND entry_timestamp < TIMESTAMP('{canary_end}')
),
anchor_timestamps AS (
  SELECT DISTINCT entry_anchor_timestamp AS anchor_timestamp
  FROM canary_events
  WHERE entry_anchor_timestamp IS NOT NULL
  UNION DISTINCT
  SELECT DISTINCT exit_anchor_timestamp AS anchor_timestamp
  FROM canary_events
  WHERE exit_anchor_timestamp IS NOT NULL
),
bounded_raw_quotes AS (
  SELECT
    timestamp,
    bidPrice,
    askPrice
  FROM `{raw_ref}`
  WHERE timestamp >= TIMESTAMP('{canary_start}')
    AND timestamp < TIMESTAMP_ADD(TIMESTAMP('{canary_end}'), INTERVAL 6 MINUTE)
),
old_candidates AS (
  SELECT
    a.anchor_timestamp,
    q.timestamp AS actual_quote_timestamp,
    q.bidPrice AS bid,
    q.askPrice AS ask,
    ROW_NUMBER() OVER (
      PARTITION BY a.anchor_timestamp
      ORDER BY q.timestamp, q.bidPrice, q.askPrice
    ) AS quote_rn
  FROM anchor_timestamps a
  LEFT JOIN bounded_raw_quotes q
    ON q.timestamp >= a.anchor_timestamp
   AND q.timestamp < TIMESTAMP_ADD(a.anchor_timestamp, INTERVAL 1 MINUTE)
),
old_range_join AS (
  SELECT
    anchor_timestamp,
    actual_quote_timestamp,
    bid,
    ask
  FROM old_candidates
  WHERE quote_rn = 1
),
new_candidates AS (
  SELECT
    a.anchor_timestamp,
    q.timestamp AS actual_quote_timestamp,
    q.bidPrice AS bid,
    q.askPrice AS ask
  FROM bounded_raw_quotes q
  CROSS JOIN UNNEST([
    TIMESTAMP_MICROS(DIV(UNIX_MICROS(q.timestamp), 30000000) * 30000000),
    TIMESTAMP_SUB(
      TIMESTAMP_MICROS(DIV(UNIX_MICROS(q.timestamp), 30000000) * 30000000),
      INTERVAL 30 SECOND
    )
  ]) AS candidate_anchor_timestamp
  JOIN anchor_timestamps a
    ON a.anchor_timestamp = candidate_anchor_timestamp
  WHERE q.timestamp >= candidate_anchor_timestamp
    AND q.timestamp < TIMESTAMP_ADD(candidate_anchor_timestamp, INTERVAL 1 MINUTE)
),
new_selected AS (
  SELECT
    anchor_timestamp,
    ARRAY_AGG(STRUCT(actual_quote_timestamp, bid, ask)
      ORDER BY actual_quote_timestamp, bid, ask
      LIMIT 1
    )[OFFSET(0)] AS quote
  FROM new_candidates
  GROUP BY anchor_timestamp
),
new_quote_grid AS (
  SELECT
    a.anchor_timestamp,
    q.quote.actual_quote_timestamp,
    q.quote.bid,
    q.quote.ask
  FROM anchor_timestamps a
  LEFT JOIN new_selected q
    ON q.anchor_timestamp = a.anchor_timestamp
),
old_minus_new AS (
  SELECT * FROM old_range_join
  EXCEPT DISTINCT
  SELECT * FROM new_quote_grid
),
new_minus_old AS (
  SELECT * FROM new_quote_grid
  EXCEPT DISTINCT
  SELECT * FROM old_range_join
)
SELECT
  (SELECT COUNT(*) FROM old_minus_new) AS old_minus_new_count,
  (SELECT COUNT(*) FROM new_minus_old) AS new_minus_old_count
""".strip()


def build_duplicate_quote_timestamp_sql(
    pair: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
) -> str:
    raw_ref = f"{project}.{dataset}.{PAIR_TABLE_MAP[pair]}"
    return f"""
SELECT
  COUNT(*) AS duplicate_quote_timestamp_group_count,
  SUM(row_count) AS duplicate_quote_row_count
FROM (
  SELECT
    timestamp,
    COUNT(*) AS row_count
  FROM `{raw_ref}`
  WHERE timestamp >= TIMESTAMP('{IS_START}')
    AND timestamp < TIMESTAMP_ADD(TIMESTAMP('{IS_END_EXCLUSIVE}'), INTERVAL 6 MINUTE)
  GROUP BY timestamp
  HAVING row_count > 1
)
""".strip()


def build_quote_anchor_integrity_sql(
    source_anchor_table: str,
    quote_anchor_source_table: str,
) -> str:
    return f"""
WITH duplicate_anchors AS (
  SELECT
    anchor_timestamp,
    COUNT(*) AS row_count
  FROM `{quote_anchor_source_table}`
  GROUP BY anchor_timestamp
  HAVING row_count > 1
),
missing_final_anchors AS (
  SELECT a.anchor_timestamp
  FROM `{source_anchor_table}` a
  LEFT JOIN `{quote_anchor_source_table}` qa
    ON qa.anchor_timestamp = a.anchor_timestamp
  WHERE qa.anchor_timestamp IS NULL
)
SELECT
  (SELECT COUNT(*) FROM `{source_anchor_table}`) AS anchor_count,
  (SELECT COUNT(*) FROM `{quote_anchor_source_table}`) AS quote_anchor_row_count,
  (SELECT COUNT(*) FROM duplicate_anchors) AS duplicate_anchor_count,
  (SELECT COUNT(*) FROM missing_final_anchors) AS missing_final_anchor_count
""".strip()


def build_false_null_quote_anchor_sql(
    quote_anchor_source_table: str,
    raw_stage_source_table: str,
) -> str:
    return f"""
SELECT
  COUNT(DISTINCT qa.anchor_timestamp) AS false_null_count
FROM `{quote_anchor_source_table}` qa
JOIN `{raw_stage_source_table}` q
  ON q.actual_quote_timestamp >= qa.anchor_timestamp
 AND q.actual_quote_timestamp < TIMESTAMP_ADD(qa.anchor_timestamp, INTERVAL 1 MINUTE)
WHERE qa.actual_quote_timestamp IS NULL
""".strip()


def _parse_utc_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _add_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    return value.replace(year=year, month=month)


def month_shards(
    start_iso: str = IS_START,
    end_iso: str = IS_END_EXCLUSIVE,
) -> list[tuple[str, str]]:
    start = _parse_utc_iso(start_iso)
    end = _parse_utc_iso(end_iso)
    if start >= end:
        raise ValueError(f"invalid shard range: {start_iso} >= {end_iso}")
    if start.day != 1 or start.hour or start.minute or start.second or start.microsecond:
        raise ValueError(f"start must be a month boundary: {start_iso}")
    if end.day != 1 or end.hour or end.minute or end.second or end.microsecond:
        raise ValueError(f"end must be a month boundary: {end_iso}")

    shards = []
    current = start
    while current < end:
        next_month = _add_month(current)
        if next_month > end:
            raise ValueError(f"end must align to a month boundary: {end_iso}")
        shards.append((_format_utc_iso(current), _format_utc_iso(next_month)))
        current = next_month
    return shards


def quote_anchor_shard_table(
    pair: str,
    shard_start: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    prefix: str = DEFAULT_QUOTE_ANCHOR_SHARD_TABLE_PREFIX,
) -> str:
    shard_month = _parse_utc_iso(shard_start).strftime("%Y%m")
    return f"{project}.{dataset}.{prefix}_{pair.lower()}_{shard_month}"


def build_materialize_quote_anchor_sql(
    pair: str,
    destination_table: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    sidecar: dict[str, Any] | None = None,
) -> str:
    sidecar = sidecar or load_feature_sidecar()
    event_ref = _event_table_ref(sidecar, pair)
    raw_ref = f"{project}.{dataset}.{PAIR_TABLE_MAP[pair]}"
    return f"""
CREATE OR REPLACE TABLE `{destination_table}` AS
WITH is_events AS (
  SELECT
    entry_anchor_timestamp,
    exit_anchor_timestamp
  FROM `{event_ref}`
  WHERE entry_timestamp >= TIMESTAMP('{IS_START}')
    AND entry_timestamp < TIMESTAMP('{IS_END_EXCLUSIVE}')
),
anchor_timestamps AS (
  SELECT DISTINCT entry_anchor_timestamp AS anchor_timestamp
  FROM is_events
  WHERE entry_anchor_timestamp IS NOT NULL
  UNION DISTINCT
  SELECT DISTINCT exit_anchor_timestamp AS anchor_timestamp
  FROM is_events
  WHERE exit_anchor_timestamp IS NOT NULL
),
quote_candidates AS (
  SELECT
    a.anchor_timestamp,
    q.timestamp AS actual_quote_timestamp,
    q.bidPrice AS bid,
    q.askPrice AS ask,
    ROW_NUMBER() OVER (
      PARTITION BY a.anchor_timestamp
      ORDER BY q.timestamp
    ) AS quote_rn
  FROM anchor_timestamps a
  LEFT JOIN `{raw_ref}` q
    ON q.timestamp >= a.anchor_timestamp
   AND q.timestamp < TIMESTAMP_ADD(a.anchor_timestamp, INTERVAL 1 MINUTE)
)
SELECT
  anchor_timestamp,
  actual_quote_timestamp,
  bid,
  ask
FROM quote_candidates
WHERE quote_rn = 1
ORDER BY anchor_timestamp
""".strip()


def build_materialize_quote_anchor_shard_sql(
    pair: str,
    destination_table: str,
    shard_start: str,
    shard_end: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    sidecar: dict[str, Any] | None = None,
) -> str:
    sidecar = sidecar or load_feature_sidecar()
    event_ref = _event_table_ref(sidecar, pair)
    raw_ref = f"{project}.{dataset}.{PAIR_TABLE_MAP[pair]}"
    return f"""
CREATE OR REPLACE TABLE `{destination_table}` AS
WITH is_events AS (
  SELECT
    entry_anchor_timestamp,
    exit_anchor_timestamp
  FROM `{event_ref}`
  WHERE entry_timestamp >= TIMESTAMP('{shard_start}')
    AND entry_timestamp < TIMESTAMP('{shard_end}')
),
anchor_timestamps AS (
  SELECT DISTINCT entry_anchor_timestamp AS anchor_timestamp
  FROM is_events
  WHERE entry_anchor_timestamp IS NOT NULL
  UNION DISTINCT
  SELECT DISTINCT exit_anchor_timestamp AS anchor_timestamp
  FROM is_events
  WHERE exit_anchor_timestamp IS NOT NULL
),
raw_quotes AS (
  SELECT
    timestamp,
    bidPrice,
    askPrice
  FROM `{raw_ref}`
  WHERE timestamp >= TIMESTAMP('{shard_start}')
    AND timestamp < TIMESTAMP_ADD(TIMESTAMP('{shard_end}'), INTERVAL 6 MINUTE)
),
quote_candidates AS (
  SELECT
    a.anchor_timestamp,
    q.timestamp AS actual_quote_timestamp,
    q.bidPrice AS bid,
    q.askPrice AS ask,
    ROW_NUMBER() OVER (
      PARTITION BY a.anchor_timestamp
      ORDER BY q.timestamp, q.bidPrice, q.askPrice
    ) AS quote_rn
  FROM anchor_timestamps a
  LEFT JOIN raw_quotes q
    ON q.timestamp >= a.anchor_timestamp
   AND q.timestamp < TIMESTAMP_ADD(a.anchor_timestamp, INTERVAL 1 MINUTE)
   AND q.timestamp >= TIMESTAMP('{shard_start}')
   AND q.timestamp < TIMESTAMP_ADD(TIMESTAMP('{shard_end}'), INTERVAL 6 MINUTE)
)
SELECT
  anchor_timestamp,
  actual_quote_timestamp,
  bid,
  ask
FROM quote_candidates
WHERE quote_rn = 1
ORDER BY anchor_timestamp
""".strip()


def build_materialize_quote_anchor_union_sql(
    pair: str,
    destination_table: str,
    shard_tables: list[str],
) -> str:
    if not shard_tables:
        raise ValueError(f"no quote-anchor shard tables for {pair}")
    union_sql = "\n  UNION ALL\n".join(
        f"  SELECT anchor_timestamp, actual_quote_timestamp, bid, ask FROM `{table}`"
        for table in shard_tables
    )
    return f"""
CREATE OR REPLACE TABLE `{destination_table}` AS
WITH shard_rows AS (
{union_sql}
),
deduped AS (
  SELECT
    anchor_timestamp,
    actual_quote_timestamp,
    bid,
    ask,
    ROW_NUMBER() OVER (
      PARTITION BY anchor_timestamp
      ORDER BY actual_quote_timestamp IS NULL, actual_quote_timestamp, bid, ask
    ) AS anchor_rn
  FROM shard_rows
)
SELECT
  anchor_timestamp,
  actual_quote_timestamp,
  bid,
  ask
FROM deduped
WHERE anchor_rn = 1
ORDER BY anchor_timestamp
""".strip()


def build_is_cell_summary_from_quote_anchor_sql(
    pair: str,
    quote_anchor_source_table: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    sidecar: dict[str, Any] | None = None,
) -> str:
    sidecar = sidecar or load_feature_sidecar()
    event_ref = _event_table_ref(sidecar, pair)
    return f"""
WITH is_events AS (
  SELECT
    *,
    CASE
      WHEN direction = 'momentum' AND signal_side = 'positive_extreme' THEN 1
      WHEN direction = 'momentum' AND signal_side = 'negative_extreme' THEN -1
      WHEN direction = 'mean_reversion' AND signal_side = 'positive_extreme' THEN -1
      WHEN direction = 'mean_reversion' AND signal_side = 'negative_extreme' THEN 1
      ELSE 0
    END AS stance
  FROM `{event_ref}`
  WHERE entry_timestamp >= TIMESTAMP('{IS_START}')
    AND entry_timestamp < TIMESTAMP('{IS_END_EXCLUSIVE}')
),
pnl_rows AS (
  SELECT
    e.pair,
    e.score_family,
    e.lookback_seconds,
    e.threshold,
    e.direction,
    e.horizon_seconds,
    entry_anchor.actual_quote_timestamp AS actual_entry_timestamp,
    exit_anchor.actual_quote_timestamp AS actual_exit_timestamp,
    entry_anchor.bid AS entry_bid,
    entry_anchor.ask AS entry_ask,
    exit_anchor.bid AS exit_bid,
    exit_anchor.ask AS exit_ask,
    entry_anchor.actual_quote_timestamp IS NULL AS missing_entry_anchor,
    exit_anchor.actual_quote_timestamp IS NULL AS missing_exit_anchor,
    {FEE_BPS_ROUNDTRIP} AS fee_bps_roundtrip,
    {SLIPPAGE_BPS_ROUNDTRIP} AS slippage_bps_roundtrip,
    CASE
      WHEN entry_anchor.actual_quote_timestamp IS NULL
        OR exit_anchor.actual_quote_timestamp IS NULL
        OR entry_anchor.bid IS NULL
        OR entry_anchor.ask IS NULL
        OR exit_anchor.bid IS NULL
        OR exit_anchor.ask IS NULL
        OR e.stance = 0
      THEN NULL
      ELSE (
        e.stance
        * (
          (CASE WHEN e.stance = 1 THEN exit_anchor.bid ELSE exit_anchor.ask END)
          - (CASE WHEN e.stance = 1 THEN entry_anchor.ask ELSE entry_anchor.bid END)
        )
        / NULLIF((CASE WHEN e.stance = 1 THEN entry_anchor.ask ELSE entry_anchor.bid END), 0)
        * 10000.0
      )
    END AS pnl_bps_gross,
    CASE
      WHEN entry_anchor.actual_quote_timestamp IS NULL
        OR exit_anchor.actual_quote_timestamp IS NULL
        OR entry_anchor.bid IS NULL
        OR entry_anchor.ask IS NULL
        OR exit_anchor.bid IS NULL
        OR exit_anchor.ask IS NULL
        OR e.stance = 0
      THEN NULL
      ELSE (
        e.stance
        * (
          (CASE WHEN e.stance = 1 THEN exit_anchor.bid ELSE exit_anchor.ask END)
          - (CASE WHEN e.stance = 1 THEN entry_anchor.ask ELSE entry_anchor.bid END)
        )
        / NULLIF((CASE WHEN e.stance = 1 THEN entry_anchor.ask ELSE entry_anchor.bid END), 0)
        * 10000.0
        - {FEE_BPS_ROUNDTRIP}
        - {SLIPPAGE_BPS_ROUNDTRIP}
      )
    END AS pnl_bps_net
  FROM is_events e
  LEFT JOIN `{quote_anchor_source_table}` entry_anchor
    ON entry_anchor.anchor_timestamp = e.entry_anchor_timestamp
  LEFT JOIN `{quote_anchor_source_table}` exit_anchor
    ON exit_anchor.anchor_timestamp = e.exit_anchor_timestamp
),
aggregated AS (
  SELECT
    pair,
    score_family,
    lookback_seconds,
    threshold,
    direction,
    horizon_seconds,
    COUNT(*) AS event_count,
    COUNTIF(missing_entry_anchor) AS missing_entry_anchor_count,
    COUNTIF(missing_exit_anchor) AS missing_exit_anchor_count,
    COUNTIF(pnl_bps_net IS NOT NULL) AS num_trades,
    SUM(IF(pnl_bps_net > 0, pnl_bps_net, 0)) AS gross_profit,
    ABS(SUM(IF(pnl_bps_net < 0, pnl_bps_net, 0))) AS gross_loss,
    AVG(pnl_bps_net) AS mean_pnl_bps_net,
    STDDEV_SAMP(pnl_bps_net) AS stddev_pnl_bps_net,
    {FEE_BPS_ROUNDTRIP} AS fee_bps_roundtrip,
    {SLIPPAGE_BPS_ROUNDTRIP} AS slippage_bps_roundtrip
  FROM pnl_rows
  GROUP BY pair, score_family, lookback_seconds, threshold, direction, horizon_seconds
)
SELECT
  *,
  SAFE_DIVIDE(gross_profit, gross_loss) AS net_profit_factor,
  gross_loss <= 0 AND gross_profit > 0 AS net_profit_factor_is_infinite,
  CASE
    WHEN num_trades < 4 THEN 1.0
    WHEN stddev_pnl_bps_net IS NULL OR stddev_pnl_bps_net <= 0 THEN 1.0
    ELSE LEAST(
      1.0,
      EXP(-0.5 * POW(ABS(mean_pnl_bps_net / (stddev_pnl_bps_net / SQRT(num_trades))), 2))
    )
  END AS p_raw
FROM aggregated
ORDER BY pair, score_family, lookback_seconds, threshold, direction, horizon_seconds
""".strip()


def materialized_cell_summary_table(
    pair: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    prefix: str = DEFAULT_MATERIALIZED_TABLE_PREFIX,
) -> str:
    return f"{project}.{dataset}.{prefix}_{pair.lower()}"


def build_materialize_cell_summary_sql(
    pair: str,
    destination_table: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    sidecar: dict[str, Any] | None = None,
) -> str:
    cell_summary_sql = build_is_cell_summary_sql(pair, project, dataset, sidecar)
    return f"""
CREATE OR REPLACE TABLE `{destination_table}` AS
{cell_summary_sql}
""".strip()


def build_materialize_cell_summary_from_quote_anchor_sql(
    pair: str,
    destination_table: str,
    quote_anchor_source_table: str,
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    sidecar: dict[str, Any] | None = None,
) -> str:
    cell_summary_sql = build_is_cell_summary_from_quote_anchor_sql(
        pair,
        quote_anchor_source_table,
        project,
        dataset,
        sidecar,
    )
    return f"""
CREATE OR REPLACE TABLE `{destination_table}` AS
{cell_summary_sql}
""".strip()


def read_materialized_cell_summary_sql(pair: str, source_table: str) -> str:
    return f"""
SELECT *
FROM `{source_table}`
WHERE pair = '{pair}'
ORDER BY pair, score_family, lookback_seconds, threshold, direction, horizon_seconds
""".strip()


def run_bq_query(sql: str, project: str = DEFAULT_PROJECT) -> list[dict[str, Any]]:
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


def run_bq_statement(sql: str, project: str = DEFAULT_PROJECT) -> None:
    subprocess.run(
        [
            "bq",
            "query",
            f"--project_id={project}",
            "--use_legacy_sql=false",
        ],
        check=True,
        input=sql,
        text=True,
    )


def _cell_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["pair"],
        row["score_family"],
        int(row["lookback_seconds"]),
        row["threshold"],
        row["direction"],
        int(row["horizon_seconds"]),
    )


def _all_cells_for_pair(pair: str) -> list[dict[str, Any]]:
    cells = []
    for score_family in SCORE_FAMILIES:
        for lookback in LOOKBACK_SECONDS:
            for threshold in THRESHOLDS:
                for direction in DIRECTIONS:
                    for horizon in HORIZON_SECONDS:
                        cells.append(
                            {
                                "pair": pair,
                                "score_family": score_family,
                                "lookback_seconds": lookback,
                                "threshold": threshold,
                                "direction": direction,
                                "horizon_seconds": horizon,
                            }
                        )
    return cells


def _bq_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return bool(value)


def collect_pair_trades(rows: list[dict[str, Any]], pair: str) -> list[dict[str, Any]]:
    trades_by_cell: dict[tuple[Any, ...], list[dict[str, Any]]] = {
        _cell_key(cell): [] for cell in _all_cells_for_pair(pair)
    }
    for row in rows:
        stance = int(row["stance"])
        pnl = quote_side_pnl_bps(
            stance,
            float(row["entry_bid"]),
            float(row["entry_ask"]),
            float(row["exit_bid"]),
            float(row["exit_ask"]),
        )
        cell_key = _cell_key(row)
        trades_by_cell.setdefault(cell_key, []).append(
            {
                "entry_timestamp": row["entry_timestamp"],
                "exit_anchor_timestamp": row["exit_anchor_timestamp"],
                "stance": stance,
                **pnl,
                "pnl_bps": pnl["pnl_bps_net"],
                "pnl_bps_net": pnl["pnl_bps_net"],
            }
        )
    return [
        cell_metric_from_trades(cell, trades_by_cell.get(_cell_key(cell), []))
        for cell in _all_cells_for_pair(pair)
    ]


def collect_pair_cell_summaries(rows: list[dict[str, Any]], pair: str) -> list[dict[str, Any]]:
    by_key = {_cell_key(row): row for row in rows}
    output = []
    for cell in _all_cells_for_pair(pair):
        row = by_key.get(_cell_key(cell))
        if row is None:
            output.append(cell_metric_from_trades(cell, []))
            continue

        num_trades = int(row["num_trades"])
        event_count = int(row.get("event_count", num_trades))
        missing_entry_anchor_count = int(row.get("missing_entry_anchor_count", 0))
        missing_exit_anchor_count = int(row.get("missing_exit_anchor_count", 0))
        sparse = sparse_gate(num_trades, int(cell["horizon_seconds"]))
        gross_profit = float(row["gross_profit"] or 0.0)
        gross_loss = float(row["gross_loss"] or 0.0)
        is_infinite = _bq_bool(row.get("net_profit_factor_is_infinite", False))
        net_pf = None if is_infinite else (
            float(row["net_profit_factor"]) if row.get("net_profit_factor") is not None else 0.0
        )
        net_pf_value = float("inf") if is_infinite else float(net_pf or 0.0)
        fail_reasons = [] if sparse["passed"] else ["sparse_cell"]
        pass_net_pf = bool(
            sparse["passed"]
            and (
                is_infinite
                or (net_pf is not None and net_pf >= PF_HURDLE)
            )
        )
        output.append(
            {
                **cell,
                "cell_id": _cell_id(cell),
                "is_start": IS_START,
                "is_end_exclusive": IS_END_EXCLUSIVE,
                "entry_granularity": ENTRY_GRANULARITY,
                "event_count": event_count,
                "num_trades": num_trades,
                "missing_entry_anchor_count": missing_entry_anchor_count,
                "missing_exit_anchor_count": missing_exit_anchor_count,
                "sparse_gate": sparse,
                "sparse_fail_close": not sparse["passed"],
                "gross_profit": gross_profit,
                "gross_loss": gross_loss,
                "net_profit_factor": net_pf,
                "net_profit_factor_value": net_pf_value,
                "net_profit_factor_is_infinite": is_infinite,
                "pf_hurdle": PF_HURDLE,
                "pass_net_pf": pass_net_pf,
                "p_raw": 1.0 if fail_reasons else float(row.get("p_raw", 1.0)),
                "p_adj_holm": None,
                "pass_fwer": False,
                "is_eligible_for_phase116": False,
                "fee_bps_roundtrip": float(row.get("fee_bps_roundtrip", FEE_BPS_ROUNDTRIP)),
                "slippage_bps_roundtrip": float(
                    row.get("slippage_bps_roundtrip", SLIPPAGE_BPS_ROUNDTRIP)
                ),
                "mean_pnl_bps_net": (
                    float(row["mean_pnl_bps_net"])
                    if row.get("mean_pnl_bps_net") is not None
                    else None
                ),
                "stddev_pnl_bps_net": (
                    float(row["stddev_pnl_bps_net"])
                    if row.get("stddev_pnl_bps_net") is not None
                    else None
                ),
                "fail_reasons": fail_reasons,
            }
        )
    return output


def apply_holm_global(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) > FWER_DENOMINATOR:
        raise AssertionError(f"too many cells for sealed family: {len(rows)}")
    p_raw = [float(row.get("p_raw", 1.0)) for row in rows]
    n_padded = FWER_DENOMINATOR - len(p_raw)
    padded = p_raw + [1.0] * n_padded
    _, p_adj, _, _ = multipletests(padded, alpha=ALPHA, method="holm")
    for row, p_adj_holm in zip(rows, p_adj[: len(rows)]):
        row["p_adj_holm"] = float(p_adj_holm)
        row["pass_fwer"] = bool(
            row.get("p_adj_holm", 1.0) < ALPHA
            and not row.get("sparse_fail_close", True)
        )
        row["is_eligible_for_phase116"] = bool(
            row.get("pass_net_pf", False) and row.get("pass_fwer", False)
        )
    return {
        "method": "holm",
        "alpha": ALPHA,
        "fwer_denominator": FWER_DENOMINATOR,
        "n_tested": len(rows),
        "n_padded": n_padded,
        "p_raw_padded": padded,
    }


def build_summary_doc(
    rows_by_pair: dict[str, list[dict[str, Any]]],
    sidecar: dict[str, Any],
    blocked_reason: str | None = None,
    quote_anchor_materialization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [row for pair_rows in rows_by_pair.values() for row in pair_rows]
    holm = apply_holm_global(rows) if rows else {
        "method": "holm",
        "alpha": ALPHA,
        "fwer_denominator": FWER_DENOMINATOR,
        "n_tested": 0,
        "n_padded": FWER_DENOMINATOR,
        "p_raw_padded": [1.0] * FWER_DENOMINATOR,
    }
    eligible = [
        {
            "cell_id": row["cell_id"],
            "pair": row["pair"],
            "score_family": row["score_family"],
            "lookback_seconds": row["lookback_seconds"],
            "threshold": row["threshold"],
            "direction": row["direction"],
            "horizon_seconds": row["horizon_seconds"],
        }
        for row in rows
        if row.get("is_eligible_for_phase116")
    ]
    summary = {
        "schema_version": "v5.1-is-backtest-fwer-1",
        "generated_at": _now_iso(),
        "git_commit": _git_commit(),
        "phase": 115,
        "phase115_blocked": blocked_reason is not None,
        "blocker_reason": blocked_reason,
        "claim_doc": str(CLAIM_DOC),
        "feature_sidecar": str(FEATURE_SIDECAR),
        "entry_granularity": ENTRY_GRANULARITY,
        "is_start": IS_START,
        "is_end_exclusive": IS_END_EXCLUSIVE,
        "fwer_denominator": FWER_DENOMINATOR,
        "holm": holm,
        "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "slippage_bps_roundtrip": SLIPPAGE_BPS_ROUNDTRIP,
        "pairs": {
            pair: {
                "cell_count": len(pair_rows),
                "eligible_cell_count": sum(
                    1 for row in pair_rows if row.get("is_eligible_for_phase116")
                ),
                "sparse_fail_close_count": sum(
                    1 for row in pair_rows if row.get("sparse_fail_close")
                ),
            }
            for pair, pair_rows in rows_by_pair.items()
        },
        "eligible_cells": eligible,
        "feature_destination_tables": sidecar.get("destination_tables", {}),
    }
    if quote_anchor_materialization is not None:
        summary["quote_anchor_materialization"] = quote_anchor_materialization
    return summary


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# v5.1 IS Backtest + FWER Summary",
        "",
        f"- phase115_blocked: {str(summary['phase115_blocked']).lower()}",
        f"- entry_granularity: {summary['entry_granularity']}",
        f"- fwer_denominator: {summary['fwer_denominator']}",
        f"- eligible_cells: {len(summary['eligible_cells'])}",
        "",
        "| Pair | Cells | Eligible | Sparse Fail-Close |",
        "|------|-------|----------|-------------------|",
    ]
    for pair in PAIRS:
        item = summary["pairs"].get(pair, {})
        lines.append(
            "| {pair} | {cells} | {eligible} | {sparse} |".format(
                pair=pair,
                cells=item.get("cell_count", 0),
                eligible=item.get("eligible_cell_count", 0),
                sparse=item.get("sparse_fail_close_count", 0),
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_json(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_outputs(rows_by_pair: dict[str, list[dict[str, Any]]], summary: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    for pair, rows in rows_by_pair.items():
        write_json(
            {
                "pair": pair,
                "phase": 115,
                "entry_granularity": ENTRY_GRANULARITY,
                "results": rows,
            },
            REPORT_DIR / f"{pair.lower()}_is_cells.json",
        )
    write_json(summary, SUMMARY_JSON)
    SUMMARY_MD.write_text(render_markdown(summary), encoding="utf-8")


def run_live(project: str = DEFAULT_PROJECT, dataset: str = DEFAULT_DATASET) -> int:
    sidecar = load_feature_sidecar()
    if sidecar.get("phase114_blocked"):
        summary = build_summary_doc({}, sidecar, str(sidecar.get("blocker_reason")))
        write_outputs({pair: [] for pair in PAIRS}, summary)
        return 1
    rows_by_pair = {}
    for pair in PAIRS:
        rows = run_bq_query(build_is_cell_summary_sql(pair, project, dataset, sidecar), project)
        rows_by_pair[pair] = collect_pair_cell_summaries(rows, pair)
    summary = build_summary_doc(rows_by_pair, sidecar)
    write_outputs(rows_by_pair, summary)
    print(f"wrote Phase 115 artifacts to {REPORT_DIR}")
    return 0


def run_materialize_cell_summaries(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    prefix: str = DEFAULT_MATERIALIZED_TABLE_PREFIX,
    quote_anchor_prefix: str = DEFAULT_QUOTE_ANCHOR_TABLE_PREFIX,
    use_materialized_quote_anchors: bool = False,
    pairs: tuple[str, ...] = PAIRS,
) -> int:
    sidecar = load_feature_sidecar()
    if sidecar.get("phase114_blocked"):
        summary = build_summary_doc({}, sidecar, str(sidecar.get("blocker_reason")))
        write_outputs({pair: [] for pair in PAIRS}, summary)
        return 1

    for pair in pairs:
        destination_table = materialized_cell_summary_table(pair, project, dataset, prefix)
        if use_materialized_quote_anchors:
            quote_anchor_source = quote_anchor_table(
                pair,
                project,
                dataset,
                quote_anchor_prefix,
            )
            sql = build_materialize_cell_summary_from_quote_anchor_sql(
                pair,
                destination_table,
                quote_anchor_source,
                project,
                dataset,
                sidecar,
            )
        else:
            sql = build_materialize_cell_summary_sql(pair, destination_table, project, dataset, sidecar)
        print(f"materializing {pair} cell summaries to {destination_table}")
        run_bq_statement(sql, project)
    return 0


def run_materialize_quote_anchors(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    prefix: str = DEFAULT_QUOTE_ANCHOR_TABLE_PREFIX,
    pairs: tuple[str, ...] = PAIRS,
) -> int:
    sidecar = load_feature_sidecar()
    if sidecar.get("phase114_blocked"):
        summary = build_summary_doc({}, sidecar, str(sidecar.get("blocker_reason")))
        write_outputs({pair: [] for pair in PAIRS}, summary)
        return 1

    for pair in pairs:
        destination_table = quote_anchor_table(pair, project, dataset, prefix)
        sql = build_materialize_quote_anchor_sql(pair, destination_table, project, dataset, sidecar)
        print(f"materializing {pair} quote anchors to {destination_table}")
        run_bq_statement(sql, project)
    return 0


def run_materialize_quote_anchor_shards(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    shard_prefix: str = DEFAULT_QUOTE_ANCHOR_SHARD_TABLE_PREFIX,
    pairs: tuple[str, ...] = PAIRS,
) -> int:
    sidecar = load_feature_sidecar()
    if sidecar.get("phase114_blocked"):
        summary = build_summary_doc({}, sidecar, str(sidecar.get("blocker_reason")))
        write_outputs({pair: [] for pair in PAIRS}, summary)
        return 1

    for pair in pairs:
        for shard_start, shard_end in month_shards():
            destination_table = quote_anchor_shard_table(
                pair,
                shard_start,
                project,
                dataset,
                shard_prefix,
            )
            sql = build_materialize_quote_anchor_shard_sql(
                pair,
                destination_table,
                shard_start,
                shard_end,
                project,
                dataset,
                sidecar,
            )
            print(
                "materializing {pair} quote-anchor shard {start} to {table}".format(
                    pair=pair,
                    start=shard_start,
                    table=destination_table,
                )
            )
            run_bq_statement(sql, project)
    return 0


def run_materialize_quote_anchor_union(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    quote_anchor_prefix: str = DEFAULT_QUOTE_ANCHOR_TABLE_PREFIX,
    shard_prefix: str = DEFAULT_QUOTE_ANCHOR_SHARD_TABLE_PREFIX,
    pairs: tuple[str, ...] = PAIRS,
) -> int:
    sidecar = load_feature_sidecar()
    if sidecar.get("phase114_blocked"):
        summary = build_summary_doc({}, sidecar, str(sidecar.get("blocker_reason")))
        write_outputs({pair: [] for pair in PAIRS}, summary)
        return 1

    for pair in pairs:
        destination_table = quote_anchor_table(pair, project, dataset, quote_anchor_prefix)
        shard_tables = [
            quote_anchor_shard_table(pair, shard_start, project, dataset, shard_prefix)
            for shard_start, _shard_end in month_shards()
        ]
        sql = build_materialize_quote_anchor_union_sql(pair, destination_table, shard_tables)
        print(f"materializing {pair} quote-anchor union to {destination_table}")
        run_bq_statement(sql, project)
    return 0


def run_read_materialized_cell_summaries(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    prefix: str = DEFAULT_MATERIALIZED_TABLE_PREFIX,
    pairs: tuple[str, ...] = PAIRS,
) -> int:
    sidecar = load_feature_sidecar()
    if sidecar.get("phase114_blocked"):
        summary = build_summary_doc({}, sidecar, str(sidecar.get("blocker_reason")))
        write_outputs({pair: [] for pair in PAIRS}, summary)
        return 1

    rows_by_pair = {}
    for pair in pairs:
        source_table = materialized_cell_summary_table(pair, project, dataset, prefix)
        rows = run_bq_query(read_materialized_cell_summary_sql(pair, source_table), project)
        rows_by_pair[pair] = collect_pair_cell_summaries(rows, pair)
    summary = build_summary_doc(rows_by_pair, sidecar)
    write_outputs(rows_by_pair, summary)
    print(f"wrote Phase 115 artifacts from materialized cell summaries to {REPORT_DIR}")
    return 0


def _quote_anchor_grid_diagnostics(
    pairs: tuple[str, ...],
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    anchor_prefix: str = DEFAULT_ANCHOR_TABLE_PREFIX,
    raw_quote_stage_prefix: str = DEFAULT_RAW_QUOTE_STAGE_TABLE_PREFIX,
    quote_anchor_prefix: str = DEFAULT_QUOTE_ANCHOR_TABLE_PREFIX,
    validation_passed: bool = False,
) -> dict[str, Any]:
    return {
        "strategy": "quote_to_30s_anchor_grid",
        "anchor_grid": "30s",
        "validation_passed": validation_passed,
        "pairs": {
            pair: {
                "anchor_count": None,
                "off_grid_anchor_count": None,
                "duplicate_anchor_count": None,
                "false_null_count": None,
                "anchor_table": anchor_table(pair, project, dataset, anchor_prefix),
                "raw_quote_stage_table": raw_quote_stage_table(
                    pair,
                    project,
                    dataset,
                    raw_quote_stage_prefix,
                ),
                "quote_anchor_table": quote_anchor_table(
                    pair,
                    project,
                    dataset,
                    quote_anchor_prefix,
                ),
            }
            for pair in pairs
        },
    }


def _write_quote_anchor_grid_blocker(
    blocker_reason: str,
    diagnostics: dict[str, Any],
    sidecar: dict[str, Any],
) -> None:
    diagnostics["validation_passed"] = False
    rows_by_pair = {pair: [] for pair in PAIRS}
    summary = build_summary_doc(
        rows_by_pair,
        sidecar,
        blocker_reason,
        quote_anchor_materialization=diagnostics,
    )
    write_outputs(rows_by_pair, summary)


def _row_int(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    return int(value or 0)


def run_inspect_raw_quote_layout(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
) -> int:
    rows = run_bq_query(build_raw_quote_layout_inspection_sql(project, dataset), project)
    print(json.dumps(rows, indent=2, sort_keys=True))
    return 0


def run_materialize_anchor_tables(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    anchor_prefix: str = DEFAULT_ANCHOR_TABLE_PREFIX,
    pairs: tuple[str, ...] = PAIRS,
) -> int:
    sidecar = load_feature_sidecar()
    if sidecar.get("phase114_blocked"):
        summary = build_summary_doc({}, sidecar, str(sidecar.get("blocker_reason")))
        write_outputs({pair: [] for pair in PAIRS}, summary)
        return 1

    for pair in pairs:
        destination_table = anchor_table(pair, project, dataset, anchor_prefix)
        sql = build_materialize_anchor_table_sql(pair, destination_table, sidecar)
        print(f"materializing {pair} anchor table to {destination_table}")
        run_bq_statement(sql, project)
    return 0


def run_validate_anchor_grid(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    anchor_prefix: str = DEFAULT_ANCHOR_TABLE_PREFIX,
    pairs: tuple[str, ...] = PAIRS,
    diagnostics: dict[str, Any] | None = None,
) -> int:
    for pair in pairs:
        source_table = anchor_table(pair, project, dataset, anchor_prefix)
        rows = run_bq_query(build_anchor_grid_validation_sql(source_table), project)
        row = rows[0] if rows else {}
        anchor_count = _row_int(row, "anchor_count")
        off_grid_count = _row_int(row, "off_grid_anchor_count")
        if diagnostics is not None:
            pair_diag = diagnostics["pairs"][pair]
            pair_diag["anchor_count"] = anchor_count
            pair_diag["off_grid_anchor_count"] = off_grid_count
        print(
            f"{pair} anchor grid validation: "
            f"anchor_count={anchor_count} off_grid_anchor_count={off_grid_count}"
        )
        if off_grid_count != 0:
            return 1
    return 0


def run_materialize_raw_quote_stages(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    raw_quote_stage_prefix: str = DEFAULT_RAW_QUOTE_STAGE_TABLE_PREFIX,
    pairs: tuple[str, ...] = PAIRS,
) -> int:
    for pair in pairs:
        destination_table = raw_quote_stage_table(pair, project, dataset, raw_quote_stage_prefix)
        sql = build_materialize_raw_quote_stage_sql(pair, destination_table, project, dataset)
        print(f"materializing {pair} raw quote stage to {destination_table}")
        run_bq_statement(sql, project)
    return 0


def run_materialize_quote_anchors_from_grid(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    anchor_prefix: str = DEFAULT_ANCHOR_TABLE_PREFIX,
    raw_quote_stage_prefix: str = DEFAULT_RAW_QUOTE_STAGE_TABLE_PREFIX,
    quote_anchor_prefix: str = DEFAULT_QUOTE_ANCHOR_TABLE_PREFIX,
    pairs: tuple[str, ...] = PAIRS,
) -> int:
    for pair in pairs:
        destination_table = quote_anchor_table(pair, project, dataset, quote_anchor_prefix)
        sql = build_materialize_quote_anchor_from_grid_sql(
            pair,
            destination_table,
            anchor_table(pair, project, dataset, anchor_prefix),
            raw_quote_stage_table(pair, project, dataset, raw_quote_stage_prefix),
        )
        print(f"materializing {pair} quote anchors from grid to {destination_table}")
        run_bq_statement(sql, project)
    return 0


def run_validate_quote_anchor_integrity(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    anchor_prefix: str = DEFAULT_ANCHOR_TABLE_PREFIX,
    raw_quote_stage_prefix: str = DEFAULT_RAW_QUOTE_STAGE_TABLE_PREFIX,
    quote_anchor_prefix: str = DEFAULT_QUOTE_ANCHOR_TABLE_PREFIX,
    pairs: tuple[str, ...] = PAIRS,
    diagnostics: dict[str, Any] | None = None,
) -> int:
    failed = False
    for pair in pairs:
        source_anchor_table = anchor_table(pair, project, dataset, anchor_prefix)
        source_raw_quote_stage_table = raw_quote_stage_table(
            pair,
            project,
            dataset,
            raw_quote_stage_prefix,
        )
        source_quote_anchor_table = quote_anchor_table(pair, project, dataset, quote_anchor_prefix)
        integrity_rows = run_bq_query(
            build_quote_anchor_integrity_sql(source_anchor_table, source_quote_anchor_table),
            project,
        )
        false_null_rows = run_bq_query(
            build_false_null_quote_anchor_sql(
                source_quote_anchor_table,
                source_raw_quote_stage_table,
            ),
            project,
        )
        integrity = integrity_rows[0] if integrity_rows else {}
        false_null = false_null_rows[0] if false_null_rows else {}
        duplicate_anchor_count = _row_int(integrity, "duplicate_anchor_count")
        false_null_count = _row_int(false_null, "false_null_count")
        if diagnostics is not None:
            pair_diag = diagnostics["pairs"][pair]
            pair_diag["duplicate_anchor_count"] = duplicate_anchor_count
            pair_diag["false_null_count"] = false_null_count
            if pair_diag.get("anchor_count") is None:
                pair_diag["anchor_count"] = _row_int(integrity, "anchor_count")
        print(
            f"{pair} quote-anchor integrity: "
            f"duplicate_anchor_count={duplicate_anchor_count} "
            f"false_null_count={false_null_count}"
        )
        if duplicate_anchor_count != 0 or false_null_count != 0:
            failed = True
    return 1 if failed else 0


def run_canary_quote_anchor_grid(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    pairs: tuple[str, ...] = PAIRS,
    canary_start: str = IS_START,
    canary_end: str = "2024-05-02T00:00:00Z",
) -> int:
    sidecar = load_feature_sidecar()
    for pair in pairs:
        rows = run_bq_query(
            build_canary_quote_anchor_grid_diff_sql(
                pair,
                canary_start,
                canary_end,
                project,
                dataset,
                sidecar,
            ),
            project,
        )
        row = rows[0] if rows else {}
        old_minus_new = _row_int(row, "old_minus_new_count")
        new_minus_old = _row_int(row, "new_minus_old_count")
        print(
            f"{pair} quote-anchor grid canary: "
            f"old_minus_new_count={old_minus_new} new_minus_old_count={new_minus_old}"
        )
        if old_minus_new != 0 or new_minus_old != 0:
            return 1
    return 0


def run_quote_anchor_grid(
    project: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    pairs: tuple[str, ...] = PAIRS,
    anchor_prefix: str = DEFAULT_ANCHOR_TABLE_PREFIX,
    raw_quote_stage_prefix: str = DEFAULT_RAW_QUOTE_STAGE_TABLE_PREFIX,
    quote_anchor_prefix: str = DEFAULT_QUOTE_ANCHOR_TABLE_PREFIX,
    materialized_prefix: str = DEFAULT_MATERIALIZED_TABLE_PREFIX,
) -> int:
    sidecar = load_feature_sidecar()
    diagnostics = _quote_anchor_grid_diagnostics(
        pairs,
        project,
        dataset,
        anchor_prefix,
        raw_quote_stage_prefix,
        quote_anchor_prefix,
    )
    try:
        run_inspect_raw_quote_layout(project, dataset)
        run_materialize_anchor_tables(project, dataset, anchor_prefix, pairs)
        if run_validate_anchor_grid(project, dataset, anchor_prefix, pairs, diagnostics) != 0:
            blocker = "quote_anchor_grid_materialization_bq_failed:off_grid_anchor_count"
            _write_quote_anchor_grid_blocker(blocker, diagnostics, sidecar)
            return 1
        run_materialize_raw_quote_stages(project, dataset, raw_quote_stage_prefix, pairs)
        run_materialize_quote_anchors_from_grid(
            project,
            dataset,
            anchor_prefix,
            raw_quote_stage_prefix,
            quote_anchor_prefix,
            pairs,
        )
        if (
            run_validate_quote_anchor_integrity(
                project,
                dataset,
                anchor_prefix,
                raw_quote_stage_prefix,
                quote_anchor_prefix,
                pairs,
                diagnostics,
            )
            != 0
        ):
            blocker = "quote_anchor_grid_materialization_bq_failed:quote_anchor_integrity"
            _write_quote_anchor_grid_blocker(blocker, diagnostics, sidecar)
            return 1
        diagnostics["validation_passed"] = True
        run_materialize_cell_summaries(
            project,
            dataset,
            materialized_prefix,
            quote_anchor_prefix,
            use_materialized_quote_anchors=True,
            pairs=pairs,
        )
        result = run_read_materialized_cell_summaries(project, dataset, materialized_prefix, pairs)
        if SUMMARY_JSON.exists():
            summary = json.loads(SUMMARY_JSON.read_text())
            summary["quote_anchor_materialization"] = diagnostics
            write_json(summary, SUMMARY_JSON)
            SUMMARY_MD.write_text(render_markdown(summary), encoding="utf-8")
        return result
    except subprocess.CalledProcessError as exc:
        blocker = (
            "quote_anchor_grid_materialization_bq_failed:"
            f"CalledProcessError:returncode_{exc.returncode}"
        )
        _write_quote_anchor_grid_blocker(blocker, diagnostics, sidecar)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--pairs", default=",".join(PAIRS))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bq-dry-run", action="store_true")
    parser.add_argument("--materialize-cell-summaries", action="store_true")
    parser.add_argument(
        "--materialized-table-prefix",
        default=DEFAULT_MATERIALIZED_TABLE_PREFIX,
    )
    parser.add_argument("--materialize-quote-anchors", action="store_true")
    parser.add_argument(
        "--quote-anchor-table-prefix",
        default=DEFAULT_QUOTE_ANCHOR_TABLE_PREFIX,
    )
    parser.add_argument("--materialize-quote-anchor-shards", action="store_true")
    parser.add_argument(
        "--quote-anchor-shard-table-prefix",
        default=DEFAULT_QUOTE_ANCHOR_SHARD_TABLE_PREFIX,
    )
    parser.add_argument("--materialize-quote-anchor-union", action="store_true")
    parser.add_argument(
        "--anchor-table-prefix",
        default=DEFAULT_ANCHOR_TABLE_PREFIX,
    )
    parser.add_argument(
        "--raw-quote-stage-table-prefix",
        default=DEFAULT_RAW_QUOTE_STAGE_TABLE_PREFIX,
    )
    parser.add_argument("--inspect-raw-quote-layout", action="store_true")
    parser.add_argument("--materialize-anchor-tables", action="store_true")
    parser.add_argument("--validate-anchor-grid", action="store_true")
    parser.add_argument("--materialize-raw-quote-stages", action="store_true")
    parser.add_argument("--materialize-quote-anchors-from-grid", action="store_true")
    parser.add_argument("--validate-quote-anchor-integrity", action="store_true")
    parser.add_argument("--canary-quote-anchor-grid", action="store_true")
    parser.add_argument("--canary-start", default=IS_START)
    parser.add_argument("--canary-end", default="2024-05-02T00:00:00Z")
    parser.add_argument("--quote-anchor-grid", action="store_true")
    parser.add_argument("--use-materialized-quote-anchors", action="store_true")
    parser.add_argument("--read-materialized-cell-summaries", action="store_true")
    parser.add_argument(
        "--blocker-reason",
        help="Write fail-closed Phase 115 artifacts with this blocker reason",
    )
    args = parser.parse_args(argv)
    selected_pairs = parse_pairs(args.pairs)

    sidecar = load_feature_sidecar()
    if args.blocker_reason:
        rows_by_pair = {pair: [] for pair in PAIRS}
        summary = build_summary_doc(rows_by_pair, sidecar, args.blocker_reason)
        write_outputs(rows_by_pair, summary)
        print(f"wrote fail-closed Phase 115 blocker artifacts to {REPORT_DIR}")
        return 0

    if args.dry_run:
        if args.inspect_raw_quote_layout:
            print(build_raw_quote_layout_inspection_sql(args.project, args.dataset))
            return 0
        for pair in selected_pairs:
            print(f"--- DRY RUN SQL: {pair} ---")
            destination_table = materialized_cell_summary_table(
                pair,
                args.project,
                args.dataset,
                args.materialized_table_prefix,
            )
            quote_anchor_destination = quote_anchor_table(
                pair,
                args.project,
                args.dataset,
                args.quote_anchor_table_prefix,
            )
            anchor_destination = anchor_table(
                pair,
                args.project,
                args.dataset,
                args.anchor_table_prefix,
            )
            raw_quote_stage_destination = raw_quote_stage_table(
                pair,
                args.project,
                args.dataset,
                args.raw_quote_stage_table_prefix,
            )
            if args.materialize_quote_anchor_shards:
                for shard_start, shard_end in month_shards():
                    print(
                        build_materialize_quote_anchor_shard_sql(
                            pair,
                            quote_anchor_shard_table(
                                pair,
                                shard_start,
                                args.project,
                                args.dataset,
                                args.quote_anchor_shard_table_prefix,
                            ),
                            shard_start,
                            shard_end,
                            args.project,
                            args.dataset,
                            sidecar,
                        )
                    )
            elif args.materialize_quote_anchor_union:
                print(
                    build_materialize_quote_anchor_union_sql(
                        pair,
                        quote_anchor_destination,
                        [
                            quote_anchor_shard_table(
                                pair,
                                shard_start,
                                args.project,
                                args.dataset,
                                args.quote_anchor_shard_table_prefix,
                            )
                            for shard_start, _shard_end in month_shards()
                        ],
                    )
                )
            elif args.materialize_anchor_tables:
                print(
                    build_materialize_anchor_table_sql(
                        pair,
                        anchor_destination,
                        sidecar,
                    )
                )
            elif args.validate_anchor_grid:
                print(build_anchor_grid_validation_sql(anchor_destination))
            elif args.materialize_raw_quote_stages:
                print(
                    build_materialize_raw_quote_stage_sql(
                        pair,
                        raw_quote_stage_destination,
                        args.project,
                        args.dataset,
                    )
                )
            elif args.materialize_quote_anchors_from_grid:
                print(
                    build_materialize_quote_anchor_from_grid_sql(
                        pair,
                        quote_anchor_destination,
                        anchor_destination,
                        raw_quote_stage_destination,
                    )
                )
            elif args.validate_quote_anchor_integrity:
                print(build_quote_anchor_integrity_sql(anchor_destination, quote_anchor_destination))
                print(
                    build_false_null_quote_anchor_sql(
                        quote_anchor_destination,
                        raw_quote_stage_destination,
                    )
                )
            elif args.canary_quote_anchor_grid:
                print(
                    build_canary_quote_anchor_grid_diff_sql(
                        pair,
                        args.canary_start,
                        args.canary_end,
                        args.project,
                        args.dataset,
                        sidecar,
                    )
                )
            elif args.materialize_quote_anchors:
                print(
                    build_materialize_quote_anchor_sql(
                        pair,
                        quote_anchor_destination,
                        args.project,
                        args.dataset,
                        sidecar,
                    )
                )
            elif args.materialize_cell_summaries and args.use_materialized_quote_anchors:
                print(
                    build_materialize_cell_summary_from_quote_anchor_sql(
                        pair,
                        destination_table,
                        quote_anchor_destination,
                        args.project,
                        args.dataset,
                        sidecar,
                    )
                )
            elif args.materialize_cell_summaries:
                print(
                    build_materialize_cell_summary_sql(
                        pair,
                        destination_table,
                        args.project,
                        args.dataset,
                        sidecar,
                    )
                )
            elif args.read_materialized_cell_summaries:
                print(read_materialized_cell_summary_sql(pair, destination_table))
            else:
                print(build_is_cell_summary_sql(pair, args.project, args.dataset, sidecar))
        return 0

    if args.bq_dry_run:
        if shutil.which("bq") is None:
            raise RuntimeError("bq CLI unavailable")
        if args.inspect_raw_quote_layout:
            sql = build_raw_quote_layout_inspection_sql(args.project, args.dataset)
            subprocess.run(
                [
                    "bq",
                    "query",
                    f"--project_id={args.project}",
                    "--use_legacy_sql=false",
                    "--dry_run",
                ],
                check=True,
                input=sql,
                text=True,
            )
            return 0
        for pair in selected_pairs:
            destination_table = materialized_cell_summary_table(
                pair,
                args.project,
                args.dataset,
                args.materialized_table_prefix,
            )
            quote_anchor_destination = quote_anchor_table(
                pair,
                args.project,
                args.dataset,
                args.quote_anchor_table_prefix,
            )
            anchor_destination = anchor_table(
                pair,
                args.project,
                args.dataset,
                args.anchor_table_prefix,
            )
            raw_quote_stage_destination = raw_quote_stage_table(
                pair,
                args.project,
                args.dataset,
                args.raw_quote_stage_table_prefix,
            )
            if args.materialize_quote_anchor_shards:
                for shard_start, shard_end in month_shards():
                    sql = build_materialize_quote_anchor_shard_sql(
                        pair,
                        quote_anchor_shard_table(
                            pair,
                            shard_start,
                            args.project,
                            args.dataset,
                            args.quote_anchor_shard_table_prefix,
                        ),
                        shard_start,
                        shard_end,
                        args.project,
                        args.dataset,
                        sidecar,
                    )
                    subprocess.run(
                        [
                            "bq",
                            "query",
                            f"--project_id={args.project}",
                            "--use_legacy_sql=false",
                            "--dry_run",
                        ],
                        check=True,
                        input=sql,
                        text=True,
                    )
                continue
            if args.materialize_quote_anchor_union:
                sql = build_materialize_quote_anchor_union_sql(
                    pair,
                    quote_anchor_destination,
                    [
                        quote_anchor_shard_table(
                            pair,
                            shard_start,
                            args.project,
                            args.dataset,
                            args.quote_anchor_shard_table_prefix,
                        )
                        for shard_start, _shard_end in month_shards()
                    ],
                )
            elif args.materialize_anchor_tables:
                sql = build_materialize_anchor_table_sql(pair, anchor_destination, sidecar)
            elif args.validate_anchor_grid:
                sql = build_anchor_grid_validation_sql(anchor_destination)
            elif args.materialize_raw_quote_stages:
                sql = build_materialize_raw_quote_stage_sql(
                    pair,
                    raw_quote_stage_destination,
                    args.project,
                    args.dataset,
                )
            elif args.materialize_quote_anchors_from_grid:
                sql = build_materialize_quote_anchor_from_grid_sql(
                    pair,
                    quote_anchor_destination,
                    anchor_destination,
                    raw_quote_stage_destination,
                )
            elif args.validate_quote_anchor_integrity:
                for sql in (
                    build_quote_anchor_integrity_sql(anchor_destination, quote_anchor_destination),
                    build_false_null_quote_anchor_sql(
                        quote_anchor_destination,
                        raw_quote_stage_destination,
                    ),
                ):
                    subprocess.run(
                        [
                            "bq",
                            "query",
                            f"--project_id={args.project}",
                            "--use_legacy_sql=false",
                            "--dry_run",
                        ],
                        check=True,
                        input=sql,
                        text=True,
                    )
                continue
            elif args.canary_quote_anchor_grid:
                sql = build_canary_quote_anchor_grid_diff_sql(
                    pair,
                    args.canary_start,
                    args.canary_end,
                    args.project,
                    args.dataset,
                    sidecar,
                )
            elif args.materialize_quote_anchors:
                sql = build_materialize_quote_anchor_sql(
                    pair,
                    quote_anchor_destination,
                    args.project,
                    args.dataset,
                    sidecar,
                )
            elif args.materialize_cell_summaries and args.use_materialized_quote_anchors:
                sql = build_materialize_cell_summary_from_quote_anchor_sql(
                    pair,
                    destination_table,
                    quote_anchor_destination,
                    args.project,
                    args.dataset,
                    sidecar,
                )
            elif args.materialize_cell_summaries:
                sql = build_materialize_cell_summary_sql(
                    pair,
                    destination_table,
                    args.project,
                    args.dataset,
                    sidecar,
                )
            elif args.read_materialized_cell_summaries:
                sql = read_materialized_cell_summary_sql(pair, destination_table)
            else:
                sql = build_is_cell_summary_sql(pair, args.project, args.dataset, sidecar)
            subprocess.run(
                [
                    "bq",
                    "query",
                    f"--project_id={args.project}",
                    "--use_legacy_sql=false",
                    "--dry_run",
                ],
                check=True,
                input=sql,
                text=True,
            )
        return 0

    if args.materialize_quote_anchors:
        return run_materialize_quote_anchors(
            args.project,
            args.dataset,
            args.quote_anchor_table_prefix,
            selected_pairs,
        )

    if args.materialize_quote_anchor_shards:
        return run_materialize_quote_anchor_shards(
            args.project,
            args.dataset,
            args.quote_anchor_shard_table_prefix,
            selected_pairs,
        )

    if args.materialize_quote_anchor_union:
        return run_materialize_quote_anchor_union(
            args.project,
            args.dataset,
            args.quote_anchor_table_prefix,
            args.quote_anchor_shard_table_prefix,
            selected_pairs,
        )

    if args.inspect_raw_quote_layout:
        return run_inspect_raw_quote_layout(args.project, args.dataset)

    if args.materialize_anchor_tables:
        return run_materialize_anchor_tables(
            args.project,
            args.dataset,
            args.anchor_table_prefix,
            selected_pairs,
        )

    if args.validate_anchor_grid:
        return run_validate_anchor_grid(
            args.project,
            args.dataset,
            args.anchor_table_prefix,
            selected_pairs,
        )

    if args.materialize_raw_quote_stages:
        return run_materialize_raw_quote_stages(
            args.project,
            args.dataset,
            args.raw_quote_stage_table_prefix,
            selected_pairs,
        )

    if args.materialize_quote_anchors_from_grid:
        return run_materialize_quote_anchors_from_grid(
            args.project,
            args.dataset,
            args.anchor_table_prefix,
            args.raw_quote_stage_table_prefix,
            args.quote_anchor_table_prefix,
            selected_pairs,
        )

    if args.validate_quote_anchor_integrity:
        return run_validate_quote_anchor_integrity(
            args.project,
            args.dataset,
            args.anchor_table_prefix,
            args.raw_quote_stage_table_prefix,
            args.quote_anchor_table_prefix,
            selected_pairs,
        )

    if args.canary_quote_anchor_grid:
        return run_canary_quote_anchor_grid(
            args.project,
            args.dataset,
            selected_pairs,
            args.canary_start,
            args.canary_end,
        )

    if args.quote_anchor_grid:
        return run_quote_anchor_grid(
            args.project,
            args.dataset,
            selected_pairs,
            args.anchor_table_prefix,
            args.raw_quote_stage_table_prefix,
            args.quote_anchor_table_prefix,
            args.materialized_table_prefix,
        )

    if args.materialize_cell_summaries:
        return run_materialize_cell_summaries(
            args.project,
            args.dataset,
            args.materialized_table_prefix,
            args.quote_anchor_table_prefix,
            args.use_materialized_quote_anchors,
            selected_pairs,
        )

    if args.read_materialized_cell_summaries:
        return run_read_materialized_cell_summaries(
            args.project,
            args.dataset,
            args.materialized_table_prefix,
            selected_pairs,
        )

    return run_live(args.project, args.dataset)


if __name__ == "__main__":
    sys.exit(main())
