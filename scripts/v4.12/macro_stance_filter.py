"""v4.12 compound filter: macro_stance を v4.11 vol_regime filter の上に直列 stack.

Phase 102 (D-01 / D-04 / D-09 / D-11 / D-12 準拠):
  - 入力 cells_post_filter.parquet (v4.11 vol_regime 出力, pass_flag 既存) +
    macro_stance_per_event.parquet (v4.12 estimator 出力) を cell_id で left join
  - active mode: stance ∈ {DOV, HAWK} → pass_flag carry, NEUTRAL → False, NULL → carry (D-12)
  - --neutral-mode-macro (D-04 code path 分岐): stance filter bypass、pass_flag 恒等 carry
  - 出力 cells_post_compound_filter.parquet: 864 rows × 4 cols (D-09 additive)

Invariants:
  - D-01 行 drop 禁止 (pass_flag mask のみ更新)
  - D-09 出力 schema = base + stance column 直交
  - D-11 _ALLOWED_STANCE = {DOV, HAWK} hardcode
  - D-12 stance NULL は pass_flag 変更しない (NULL-safe is_in)
  - D-17 v4.11 vol_regime_filter.py / ship_metrics_emitter_v411.py 一切 import せず touch しない

Schema bridge (Option A — D-18 new decision):
  macro_stance_per_event.parquet には cell_id 列がない (schema: event_ts, pair, central_bank, stance, ...)。
  slot_labels.parquet (data/slot_labels.parquet, schema: event_type, pair, cell_id, ...) を bridge として使い
  (central_bank → event_type, pair) で inner join → cell_id を得る → cell 単位に stance を集約 (最頻値, tie-break = first)。
  D-18 集約ルール: 同一 cell_id に複数 stance が観測された場合 mode().first() を採用。
  現状 16 rows 全 HAWK のため実質的に 6 cell_id が HAWK, 残り 186 は NULL (D-12 pass-through)。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import polars as pl

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEFAULT_INPUT_CELLS = _REPO_ROOT / "data" / "v4.11" / "cells_post_filter.parquet"
_DEFAULT_INPUT_STANCE = _REPO_ROOT / "data" / "v4.12" / "macro_stance_per_event.parquet"
_DEFAULT_INPUT_SLOT_LABELS = _REPO_ROOT / "data" / "slot_labels.parquet"
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "data" / "v4.12"
_OUTPUT_FILENAME = "cells_post_compound_filter.parquet"

# SEAL macro_filter_spec.json filter_rules: [] の空欄を埋める (D-11 plan-phase 確定)
_ALLOWED_STANCE: tuple[str, ...] = ("DOV", "HAWK")


def _aggregate_stance_per_cell(
    stance_df: pl.DataFrame,
    slot_labels_df: pl.DataFrame,
) -> pl.DataFrame:
    """event 単位 stance を cell 単位に集約 (bridge join + 最頻値 / 不一致時は first)。

    macro_stance_per_event.parquet に cell_id 列がないため、slot_labels.parquet を bridge として使う。
    Bridge: (central_bank → event_type, pair) で inner join → cell_id を得る → group_by cell_id で集約。

    D-18 集約ルール (新 D-decision):
      同一 cell_id に複数 stance が観測された場合、mode().first() を採用。
      HAWK/DOV tie → first wins (deterministic 保証)。

    Returns:
        DataFrame with columns [cell_id, stance] (192 cell 粒度, 多くは NULL = left join に任せる)
    """
    if "stance" not in stance_df.columns or "central_bank" not in stance_df.columns:
        raise ValueError(
            f"stance_df must have 'central_bank' and 'stance' columns; got {stance_df.columns}"
        )
    if stance_df.is_empty():
        return pl.DataFrame(
            {
                "cell_id": pl.Series([], dtype=pl.Utf8),
                "stance": pl.Series([], dtype=pl.Utf8),
            }
        )

    # slot_labels: (event_type, pair, cell_id) の unique マッピングを用意
    slot_map = slot_labels_df.select(["event_type", "pair", "cell_id"]).unique()

    # stance: central_bank を event_type にリネームして bridge join
    bridged = stance_df.rename({"central_bank": "event_type"}).join(
        slot_map, on=["event_type", "pair"], how="inner"
    )

    if bridged.is_empty():
        return pl.DataFrame(
            {
                "cell_id": pl.Series([], dtype=pl.Utf8),
                "stance": pl.Series([], dtype=pl.Utf8),
            }
        )

    # cell 単位集約: 最頻値を取り、tie-break は first (D-18)
    return bridged.group_by("cell_id").agg(
        pl.col("stance").drop_nulls().mode().first().alias("stance")
    )


def apply_compound_filter(
    cells: pl.DataFrame,
    stance: pl.DataFrame,
    neutral_mode_macro: bool,
) -> pl.DataFrame:
    """compound filter 本体 (D-01 pass_flag mask AND-chain)。

    Args:
        cells: v4.11 cells_post_filter (cell_id, pass_flag, bucket)
        stance: cell 単位に集約済み stance (cell_id, stance)
        neutral_mode_macro: True → stance filter bypass (D-04)

    Returns:
        cells with new pass_flag and stance column attached (additive, D-09).
    """
    joined = cells.join(stance, on="cell_id", how="left")

    if neutral_mode_macro:
        # D-04: stance filter を bypass、pass_flag は恒等 carry
        return joined.select(
            [
                pl.col("cell_id"),
                pl.col("pass_flag"),  # 恒等 carry
                pl.col("bucket"),
                pl.col("stance"),
            ]
        )

    # active mode (D-11 / D-12):
    #   stance ∈ {DOV, HAWK} → AND True → carry
    #   stance == NEUTRAL → AND False → reject
    #   stance IS NULL → carry (D-12 NULL pass_flag 維持)
    stance_pass_mask = (
        pl.col("stance").is_in(list(_ALLOWED_STANCE)) | pl.col("stance").is_null()
    )
    new_pass_flag = pl.col("pass_flag") & stance_pass_mask

    return joined.select(
        [
            pl.col("cell_id"),
            new_pass_flag.alias("pass_flag"),
            pl.col("bucket"),
            pl.col("stance"),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    """Phase 102 compound filter CLI (FILT-V412-01/02). Run from repo root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-cells",
        type=pathlib.Path,
        default=_DEFAULT_INPUT_CELLS,
        help="v4.11 cells_post_filter.parquet path",
    )
    parser.add_argument(
        "--input-stance",
        type=pathlib.Path,
        default=_DEFAULT_INPUT_STANCE,
        help="v4.12 macro_stance_per_event.parquet path",
    )
    parser.add_argument(
        "--input-slot-labels",
        type=pathlib.Path,
        default=_DEFAULT_INPUT_SLOT_LABELS,
        help="slot_labels.parquet bridge path (cell_id ↔ event_type+pair マッピング, Option A D-18)",
    )
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="出力 dir (cells_post_compound_filter.parquet をここに書く)",
    )
    parser.add_argument(
        "--neutral-mode-macro",
        action="store_true",
        help="D-04: stance filter を bypass する code path 分岐 (PARITY-V412-01 用)",
    )
    args = parser.parse_args(argv)

    if not args.input_cells.exists():
        print(f"ERROR: --input-cells not found: {args.input_cells}", file=sys.stderr)
        return 1
    if not args.input_stance.exists():
        print(f"ERROR: --input-stance not found: {args.input_stance}", file=sys.stderr)
        return 1
    if not args.input_slot_labels.exists():
        print(
            f"ERROR: --input-slot-labels not found: {args.input_slot_labels}",
            file=sys.stderr,
        )
        return 1

    cells = pl.read_parquet(args.input_cells)
    stance_raw = pl.read_parquet(args.input_stance)
    slot_labels = pl.read_parquet(args.input_slot_labels)
    # Option A: slot_labels bridge join で macro_stance_per_event → cell_id 単位に集約 (D-18)
    stance = _aggregate_stance_per_cell(stance_raw, slot_labels)

    result = apply_compound_filter(cells, stance, args.neutral_mode_macro)

    # D-01 不変条件 assert: 行数維持
    assert result.height == cells.height, (
        f"row count drift detected: input={cells.height}, output={result.height} "
        f"(D-01 行 drop 禁止違反)"
    )
    # D-09 schema assert
    expected_cols = {"cell_id", "pass_flag", "bucket", "stance"}
    actual_cols = set(result.columns)
    assert actual_cols == expected_cols, (
        f"schema drift: expected {expected_cols}, got {actual_cols}"
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / _OUTPUT_FILENAME
    result.write_parquet(out_path)
    print(f"emitted: {out_path} ({result.height} rows × {len(result.columns)} cols)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
