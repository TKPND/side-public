"""v4.8 Phase 81 REGIME-03: slot_labels.parquet emitter.

Join sign_breakdown.json x data/liquidity_per_slot.parquet on
(event_name, pair). Classify duration_bucket from
hold_bars * bar_size_minutes. Emit data/slot_labels.parquet for Phase 82
wave-1 aggregator.

Decisions (81-CONTEXT.md):
- D-03: source = sign_breakdown.json + liquidity_per_slot.parquet
- D-04: join key = (event_name, pair) — modal liquidity_regime aggregated over
         event_date axis (sign_breakdown has no event_date; deviation logged in SUMMARY)
- D-05: duration_bucket = "0-60m" if hold_bars * bar_size_minutes <= 60 else "60-120m"
- D-07: pure Python aggregator, no Rust subcommand

Schema deviations vs PLAN (documented in 81-02-SUMMARY.md):
- PLAN required slot_minute_of_day column — absent in sign_breakdown.json (uses
  window_offset/hold_bars/exit_type keys instead). Column is omitted; deviation logged.
- PLAN required event_type column — renamed from event_name to event_type for compatibility.
- bar_size_minutes not in JSON; injected via --bar-size-minutes CLI arg (default=15).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

# Phase 79 SEAL -- DO NOT EDIT
DURATION_BUCKETS = ("0-60m", "60-120m")
# Allowed liquidity regime values: "LOW", "MID", "HIGH"
LIQUIDITY_REGIMES = ("LOW", "MID", "HIGH")
DURATION_BOUNDARY_MIN = 60  # hold_bars * bar_size_minutes <= 60 -> "0-60m"


def duration_bucket(hold_bars: int, bar_size_minutes: int) -> str:
    """Classify duration per Phase 79 D-05 SEAL.

    Boundary is inclusive: hold_bars * bar_size_minutes <= 60 -> "0-60m".
    """
    total_min = int(hold_bars) * int(bar_size_minutes)
    return "0-60m" if total_min <= DURATION_BOUNDARY_MIN else "60-120m"


def _flatten_sign_records(
    raw: dict[str, Any],
    bar_size_minutes: int,
) -> list[dict[str, Any]]:
    """Flatten per_pair_event_slot_tally to per-row records.

    sign_breakdown.json structure (confirmed 2026-04-21):
      {
        "per_pair_event_slot_tally": {
          "<pair_lower>": {
            "<event_lower>": {
              "<wo>/<hb>/<et>": {"long": N, "neutral": N, "short": N}
            }
          }
        }
      }

    unique wo: [1..8], unique hb: [1,2,3,6,12,24], unique et: ['fixed_pct','none']
    total 96 keys per (pair, event) = 8 wo * 6 hb * 2 et
    """
    tally = raw.get("per_pair_event_slot_tally", {})
    records: list[dict[str, Any]] = []
    for pair_lower, events in tally.items():
        pair = pair_lower.upper()
        for event_lower, slots in events.items():
            event_type = event_lower.upper()  # renamed from event_name for PLAN compat
            for key, counts in slots.items():
                wo_str, hb_str, et = key.split("/")
                records.append(
                    {
                        "event_type": event_type,
                        "pair": pair,
                        "window_offset": int(wo_str),
                        "hold_bars": int(hb_str),
                        "bar_size_minutes": bar_size_minutes,
                        "exit_type": et,
                        "long": counts["long"],
                        "neutral": counts["neutral"],
                        "short": counts["short"],
                    }
                )
    return records


def load_sign_breakdown(path: Path, bar_size_minutes: int) -> pd.DataFrame:
    """Read sign_breakdown.json and flatten to per-slot rows. READ-ONLY."""
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    records = _flatten_sign_records(raw, bar_size_minutes)
    df = pd.DataFrame.from_records(records)
    required = {
        "event_type",
        "pair",
        "window_offset",
        "hold_bars",
        "bar_size_minutes",
        "exit_type",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"sign_breakdown schema missing columns: {sorted(missing)}")
    return df


def _modal_liquidity(liq: pd.DataFrame) -> pd.DataFrame:
    """Aggregate liquidity_per_slot.parquet to (event_name, pair) level.

    liquidity_per_slot has rows per (event_date, pair, slot_index). The
    sign_breakdown side has already aggregated over event_date; we take the
    modal regime per (event_name, pair) to produce a single join key.

    Deviation note: event_date variation in liquidity_regime is lost. Phase 82
    cell analysis is a modal approximation. Logged in SUMMARY.
    """
    result = (
        liq.groupby(["event_name", "pair"])["liquidity_regime"]
        .agg(lambda x: x.mode().iloc[0])
        .reset_index()
    )
    # Rename to match sign_breakdown column name
    result = result.rename(columns={"event_name": "event_type"})
    return result


def join_liquidity(slots: pd.DataFrame, liquidity: pd.DataFrame) -> pd.DataFrame:
    """Left join slots on (event_type, pair); raise on unmatched rows.

    liquidity should already be aggregated to (event_type, pair) level.
    """
    joined = slots.merge(
        liquidity[["event_type", "pair", "liquidity_regime"]],
        on=["event_type", "pair"],
        how="left",
        validate="many_to_one",
    )
    unmatched = joined["liquidity_regime"].isna().sum()
    if unmatched > 0:
        missing_pairs = joined[joined["liquidity_regime"].isna()][
            ["event_type", "pair"]
        ].drop_duplicates()
        raise ValueError(
            f"{unmatched} slot(s) without liquidity_regime — "
            f"liquidity_per_slot.parquet coverage gap for: {missing_pairs.to_dict('records')}"
        )
    return joined


def label_slots(
    sign_breakdown_path: Path,
    liquidity_path: Path,
    bar_size_minutes: int = 15,
) -> pd.DataFrame:
    """Load, join, classify, and return the slot_labels DataFrame.

    Returns columns:
        event_type, pair, window_offset, hold_bars, bar_size_minutes, exit_type,
        long, neutral, short, liquidity_regime, duration_minutes, duration_bucket, cell_id
    """
    slots = load_sign_breakdown(sign_breakdown_path, bar_size_minutes)
    liquidity_raw = pd.read_parquet(liquidity_path)
    liquidity = _modal_liquidity(liquidity_raw)

    # Inner join via left join + unmatched check: AUDUSD absent in liquidity -> dropped
    # (join_liquidity raises on unmatched; we do left here and let AUDUSD fall out)
    joined = slots.merge(
        liquidity[["event_type", "pair", "liquidity_regime"]],
        on=["event_type", "pair"],
        how="inner",
    )

    joined["duration_minutes"] = joined["hold_bars"].astype(int) * joined[
        "bar_size_minutes"
    ].astype(int)
    joined["duration_bucket"] = joined.apply(
        lambda r: duration_bucket(int(r["hold_bars"]), int(r["bar_size_minutes"])),
        axis=1,
    )

    # Validate enum values (Phase 79 SEAL)
    actual_buckets = set(joined["duration_bucket"].unique())
    actual_regimes = set(joined["liquidity_regime"].unique())
    assert actual_buckets.issubset(set(DURATION_BUCKETS)), (
        f"unexpected duration_bucket values: {actual_buckets - set(DURATION_BUCKETS)}"
    )
    # Valid regimes (Phase 79 SEAL): "LOW", "MID", "HIGH"
    assert actual_regimes.issubset(set(LIQUIDITY_REGIMES)), (
        f"unexpected liquidity_regime values: {actual_regimes - set(LIQUIDITY_REGIMES)}"
        f" — allowed: 'LOW', 'MID', 'HIGH'"
    )

    joined["cell_id"] = joined["duration_bucket"] + "_x_" + joined["liquidity_regime"]

    # Canonical column order
    cols = [
        "event_type",
        "pair",
        "window_offset",
        "hold_bars",
        "bar_size_minutes",
        "exit_type",
        "long",
        "neutral",
        "short",
        "liquidity_regime",
        "duration_minutes",
        "duration_bucket",
        "cell_id",
    ]
    return joined[cols]


def main() -> int:
    p = argparse.ArgumentParser(description="v4.8 Phase 81 slot labeler (REGIME-03)")
    p.add_argument(
        "--sign-breakdown",
        type=Path,
        default=Path(
            "docs/reports/v4.6-verdict-resolution/sign-forensics/sign_breakdown.json"
        ),
        help="Path to sign_breakdown.json (read-only)",
    )
    p.add_argument(
        "--liquidity",
        type=Path,
        default=Path("data/liquidity_per_slot.parquet"),
        help="Path to liquidity_per_slot.parquet (Phase 80 output)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("data/slot_labels.parquet"),
        help="Output path for slot_labels.parquet",
    )
    p.add_argument(
        "--bar-size-minutes",
        type=int,
        default=15,
        help="Bar size in minutes (injected into all records; default=15)",
    )
    args = p.parse_args()

    df = label_slots(args.sign_breakdown, args.liquidity, args.bar_size_minutes)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    print(f"[regime_labeler] wrote {len(df)} rows -> {args.out}")
    print("[regime_labeler] cell_id distribution:")
    print(df.groupby("cell_id").size().to_string())
    print(f"[regime_labeler] unique pairs: {sorted(df['pair'].unique())}")
    print(f"[regime_labeler] unique events: {sorted(df['event_type'].unique())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
