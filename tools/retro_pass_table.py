#!/usr/bin/env python
"""Phase 57 retro post-processor — trades ↔ pass_count 対応表生成.

Reads docs/reports/v4.1-n-expansion-retro/report.json, aggregates per event_source
(fomc/ecb/nfp) the trade count distribution at fee=2bps and pass_count,
appends a Markdown section to report.md.

Actual report.json shape (combined scan):
  {
    "fomc": { "0": {exit_type, hold_bars, window_offset, fee_results: [...]}, "1": ..., },
    "ecb":  { ... },
    "nfp":  { ... }
  }
  fee_results[*]: {fee_bps: float, passed: bool, combined_oos_trades: int, ...}

Usage: uv run python tools/retro_pass_table.py <retro-dir>
"""

from __future__ import annotations
import json
import sys
from pathlib import Path

DEFAULT_FEE_BPS = 2.0


def load_report(report_json: Path) -> dict:
    with report_json.open() as f:
        return json.load(f)


def aggregate(report: dict, fee_bps: float = DEFAULT_FEE_BPS) -> dict[str, dict]:
    """Returns {event_source: {total_slots, pass_count, trades_min, trades_max, trades_median, slots_with_zero_trades}}.

    Supports both combined shape ({fomc: {0: slot,...}, ecb:..., nfp:...})
    and flat shape ({slots: [...]}).
    """
    # Combined shape: top-level keys are event sources
    event_sources = [k for k in report.keys() if k in ("fomc", "ecb", "nfp")]

    out: dict[str, dict] = {}

    if event_sources:
        # Combined shape: each event source value is a list of slots
        for es in event_sources:
            records: list[tuple[int, bool]] = []
            slots_val = report[es]
            # slots_val may be a list or a dict keyed by index
            if isinstance(slots_val, dict):
                slots_iter = slots_val.values()
            else:
                slots_iter = slots_val
            for slot in slots_iter:
                fee_results = slot.get("fee_results") or []
                fee_match = next(
                    (
                        fr
                        for fr in fee_results
                        if abs(fr.get("fee_bps", -1) - fee_bps) < 0.01
                    ),
                    None,
                )
                if fee_match is None:
                    continue
                trades = (
                    fee_match.get("combined_oos_trades")
                    or fee_match.get("trades")
                    or fee_match.get("num_trades")
                    or 0
                )
                passed = bool(fee_match.get("passed", False))
                records.append((int(trades), passed))
            out[es] = _summarise(records)
    else:
        # Flat shape: top-level "slots" list with event_source field per slot
        from collections import defaultdict

        bucket: dict[str, list[tuple[int, bool]]] = defaultdict(list)
        slots = report.get("slots") or report.get("slot_reports") or []
        for slot in slots:
            es = slot.get("event_source") or slot.get("source") or "unknown"
            fee_results = slot.get("fee_results") or []
            fee_match = next(
                (
                    fr
                    for fr in fee_results
                    if abs(fr.get("fee_bps", -1) - fee_bps) < 0.01
                ),
                None,
            )
            if fee_match is None:
                continue
            trades = (
                fee_match.get("combined_oos_trades")
                or fee_match.get("trades")
                or fee_match.get("num_trades")
                or 0
            )
            passed = bool(fee_match.get("passed", False))
            bucket[es].append((int(trades), passed))
        for es, records in bucket.items():
            out[es] = _summarise(records)

    return out


def _summarise(records: list[tuple[int, bool]]) -> dict:
    if not records:
        return {
            "total_slots": 0,
            "pass_count": 0,
            "trades_min": 0,
            "trades_max": 0,
            "trades_median": 0,
            "slots_with_zero_trades": 0,
        }
    trades_list = [t for t, _ in records]
    trades_sorted = sorted(trades_list)
    median = trades_sorted[len(trades_sorted) // 2]
    return {
        "total_slots": len(records),
        "pass_count": sum(1 for _, p in records if p),
        "trades_min": min(trades_list),
        "trades_max": max(trades_list),
        "trades_median": median,
        "slots_with_zero_trades": sum(1 for t in trades_list if t == 0),
    }


def render_markdown(agg: dict[str, dict], fee_bps: float = DEFAULT_FEE_BPS) -> str:
    fee_label = f"{fee_bps:g}bps"
    lines = [
        "",
        f"## Trade Count × Pass Count Correspondence @fee={fee_label}",
        "",
        "Phase 56 fix gate (`min_trades_per_fold >= 1`, commit `8498b0e`) 適用後の",
        f"event_source 別 trade-count 分布と pass_count の対応表 (fee={fee_label})。",
        "",
        f"| event_source | total_slots | pass_count @fee={fee_label} | trades_min | trades_median | trades_max | zero_trade_slots |",
        "|---|---|---|---|---|---|---|",
    ]
    for es in sorted(agg.keys()):
        r = agg[es]
        lines.append(
            f"| {es} | {r['total_slots']} | {r['pass_count']} | {r['trades_min']} | "
            f"{r['trades_median']} | {r['trades_max']} | {r['slots_with_zero_trades']} |"
        )
    lines.extend(
        [
            "",
            "**注記:** `zero_trade_slots > 0` は Phase 56 fix で Verdict::Fail として除外済み。",
            "旧 v4.1 report (`docs/reports/v4.1-n-expansion/`) では同 slot が pass 判定されていた可能性あり。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Append trade-count ↔ pass_count correspondence table to report.md"
    )
    parser.add_argument(
        "retro_dir", type=Path, help="Directory containing report.json and report.md"
    )
    parser.add_argument(
        "--fee-bps",
        type=float,
        default=DEFAULT_FEE_BPS,
        help=f"Fee level in bps (default: {DEFAULT_FEE_BPS})",
    )
    args = parser.parse_args()

    retro_dir: Path = args.retro_dir
    fee_bps: float = args.fee_bps
    report_json = retro_dir / "report.json"
    report_md = retro_dir / "report.md"
    if not report_json.exists() or not report_md.exists():
        print(f"missing report.json or report.md in {retro_dir}", file=sys.stderr)
        return 1
    agg = aggregate(load_report(report_json), fee_bps=fee_bps)
    md_block = render_markdown(agg, fee_bps=fee_bps)
    with report_md.open("a") as f:
        f.write(md_block)
    print(
        f"appended correspondence table for {len(agg)} event sources to {report_md} @fee={fee_bps:g}bps"
    )
    for es, r in sorted(agg.items()):
        print(
            f"  {es}: slots={r['total_slots']} pass_count={r['pass_count']} "
            f"trades_min={r['trades_min']} max={r['trades_max']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
