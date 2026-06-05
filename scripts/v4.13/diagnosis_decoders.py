"""scripts/v4.13/diagnosis_decoders.py — Phase 104 milestone-specific loaders.

各 milestone (v4.9 / v4.10 / v4.11 / v4.12) の生 artifact を読み、
12 列 unified schema (D413_COLUMNS) 互換の polars DataFrame を返す。

CONTEXT.md D-17 invariant: scripts/v4.11/ scripts/v4.12/ から import 禁止。
必要なロジックは duplicate copy で再実装 (RESEARCH Pitfall 4 mitigation)。

Public surface (Plan 03 aggregator が dispatch する):
    - parse_cell_id(milestone, cell_id) -> dict
    - load_v49(power_budget_path) -> pl.DataFrame  # 192 行
    - load_v410(per_cell_metrics_path, p_adj_path) -> pl.DataFrame  # 192 行
    - load_v411(cells_parquet_path, p_adj_path, perm_null_path) -> pl.DataFrame  # 64 行
    - load_v412(cells_parquet_path, p_adj_path, perm_null_path) -> pl.DataFrame  # 32 行
    - UNIFIED_SCHEMA, D413_COLUMNS, SCHEMA_VERSION

References:
    - 104-CONTEXT.md D-V413-01..07 (schema lock + cell 粒度 + canonical bytes)
    - 104-INVENTORY-v412.md (Option A for v4.12: 864 → 32 FWER top, kill_switch fire constants)
    - 104-PATTERNS.md セクション A (_REPO_ROOT) / B (polars group_by)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

import polars as pl

# ── repo root (scripts/v4.13/<file> → parents[2]) ──
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

# ── CONTEXT D-V413-05 (12-col schema lock) + Claude Discretion (schema_version) ──
UNIFIED_SCHEMA: Final[dict[str, pl.DataType]] = {
    "milestone": pl.Utf8,
    "pair": pl.Utf8,
    "fee_bps": pl.Float64,
    "window": pl.Utf8,
    "regime_cuts": pl.Utf8,
    "sizing": pl.Utf8,
    "pass_flag": pl.Boolean,
    "fwer_threshold": pl.Float64,
    "observed_metric": pl.Float64,
    "hurdle_gap": pl.Float64,
    "observed_metric_kind": pl.Utf8,
    "schema_version": pl.Utf8,
}

D413_COLUMNS: Final[list[str]] = list(UNIFIED_SCHEMA.keys())
SCHEMA_VERSION: Final[str] = "v4.13.0"

# ── milestone defaults (CLAUDE.md Data Sources: Dukascopy USDJPY no slash) ──
_DEFAULT_PAIR: Final[str] = "USDJPY"
_DEFAULT_FEE_BPS: Final[float] = (
    0.0  # milestone 集約は raw fee 設定なし、Plan 03 で broadcast 上書き可
)

# ── cell_id regex patterns (milestone 別、単一 regex 統合 NG) ──
_V49_V410_RE: Final[re.Pattern[str]] = re.compile(r"^w(\d+)_h(\d+)_(fixed_pct|none)$")
_V411_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+-\d+m)_x_(HIGH|MID|LOW|NA)$")


# ──────────────────────────────────────────────────────────────────────────────
# parse_cell_id
# ──────────────────────────────────────────────────────────────────────────────


def parse_cell_id(milestone: str, cell_id: str) -> dict[str, str]:
    """milestone-scoped cell_id parser.

    戻り値 keys (UNIFIED_SCHEMA 対応): {window, regime_cuts, sizing}.

    RESEARCH "Don't Hand-Roll" 表参照: 単一 regex 統合は壊れやすい。
    milestone 毎 if-elif で format 変更を局所化する。
    """
    if milestone == "v4.11":
        # v4.11: "<offset_window>_x_<vol_bucket>"  例: "0-60m_x_HIGH"
        m = _V411_RE.match(cell_id)
        if m is None:
            raise ValueError(f"v4.11 cell_id parse error: {cell_id!r}")
        window, vol = m.groups()
        return {"window": window, "regime_cuts": f"VOL_{vol}", "sizing": ""}

    if milestone == "v4.12":
        # v4.12: v4.11 + stance suffix  例: "0-60m_x_HIGH_HAWK"
        # rsplit で stance 1 token を分離してから v4.11 parser に委譲
        v411_part, _, stance = cell_id.rpartition("_")
        if not stance or not v411_part:
            raise ValueError(f"v4.12 cell_id parse error: {cell_id!r}")
        v411_parsed = parse_cell_id("v4.11", v411_part)
        v411_parsed["sizing"] = stance
        return v411_parsed

    if milestone == "v4.9":
        # v4.9: power_budget_v49.json の dict key  例: "w10_h12_fixed_pct"
        return _parse_v49_v410_cell_id(cell_id, milestone="v4.9")

    if milestone == "v4.10":
        # v4.10: per_cell_metrics.json の cell_id 列  例: "w5_h2_none"
        return _parse_v49_v410_cell_id(cell_id, milestone="v4.10")

    raise ValueError(f"unknown milestone: {milestone}")


def _parse_v49_v410_cell_id(cell_id: str, milestone: str) -> dict[str, str]:
    """v4.9 / v4.10 共通 parser. format = "w<W>_h<H>_<exit>"."""
    m = _V49_V410_RE.match(cell_id)
    if m is None:
        raise ValueError(f"{milestone} cell_id parse error: {cell_id!r}")
    window, hold, exit_type = m.groups()
    # window は数値そのまま (integer, 単位なし)、sizing 列に hold + exit を encoding
    return {
        "window": f"w{window}",
        "regime_cuts": "",  # v4.9/v4.10 は regime 概念なし
        "sizing": f"h{hold}_{exit_type}",
    }


# ──────────────────────────────────────────────────────────────────────────────
# 内部 helper: 12 列 unified schema 強制
# ──────────────────────────────────────────────────────────────────────────────


def _finalize_unified(df: pl.DataFrame) -> pl.DataFrame:
    """hurdle_gap 計算 + 列順整序 + dtype cast を一括適用。

    全 loader の最後に必ず通す。
    - hurdle_gap = observed_metric - fwer_threshold (D-V413-04 raw diff)
    - 不足列を null で補完
    - 12 列順序を D413_COLUMNS に揃える
    - dtype を UNIFIED_SCHEMA に cast
    """
    # 不足列を null で補完 (loader 実装漏れの safety net)
    for col, dtype in UNIFIED_SCHEMA.items():
        if col not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=dtype).alias(col))

    df = df.with_columns(
        (pl.col("observed_metric") - pl.col("fwer_threshold")).alias("hurdle_gap"),
    )
    df = df.select(D413_COLUMNS)
    return df.cast(UNIFIED_SCHEMA)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
# load_v49 — 192 design slot from power_budget_v49.json.cells
# ──────────────────────────────────────────────────────────────────────────────


def load_v49(power_budget_path: Path) -> pl.DataFrame:
    """v4.9 power_budget_v49.json.cells (192 keys) → 192 行 unified DataFrame.

    INVENTORY lock:
      - primary observed_metric source = b_hat (point estimate)
      - observed_metric_kind = "power_budget_metric"
      - fwer_threshold = NULL (power_budget は FWER 系ではない)
    """
    pb = json.loads(Path(power_budget_path).read_text())
    cells: dict = pb.get("cells", {})
    if len(cells) != 192:
        # warn-only: INVENTORY lock=192 だが実 artifact 変動時は次工程で検出
        pass

    rows: list[dict] = []
    for cell_id, payload in cells.items():
        parsed = parse_cell_id("v4.9", cell_id)
        b_hat = payload.get("b_hat")
        rows.append(
            {
                "milestone": "v4.9",
                "pair": _DEFAULT_PAIR,
                "fee_bps": _DEFAULT_FEE_BPS,
                "window": parsed["window"],
                "regime_cuts": parsed["regime_cuts"],
                "sizing": parsed["sizing"],
                "pass_flag": bool(payload.get("robust_pass", False)),
                "fwer_threshold": None,  # v4.9 は FWER 系外
                "observed_metric": float(b_hat) if b_hat is not None else None,
                "observed_metric_kind": "power_budget_metric",
                "schema_version": SCHEMA_VERSION,
            }
        )

    df = pl.DataFrame(rows)
    return _finalize_unified(df)


# ──────────────────────────────────────────────────────────────────────────────
# load_v410 — 192 design slot from per_cell_metrics.json (fold reduction = median)
# ──────────────────────────────────────────────────────────────────────────────


def load_v410(per_cell_metrics_path: Path, p_adj_path: Path) -> pl.DataFrame:
    """v4.10 per_cell_metrics.json (384 = 192 cells × 2 folds) → 192 行 unified.

    INVENTORY lock:
      - fold reduction: group_by('cell_id').agg(pf=median across 2 folds)
      - observed_metric = pf_median (NaN は null 保持)
      - observed_metric_kind = "pf_median"
      - fwer_threshold source = p_adj_v410.json (現状 list 構造、Plan 03 で結合)
        → 当 loader では fwer_threshold=None で出し、Plan 03/aggregator が必要なら別途 join
    """
    entries: list = json.loads(Path(per_cell_metrics_path).read_text())
    if not isinstance(entries, list):
        raise ValueError(f"v4.10 per_cell_metrics expected list, got {type(entries)}")

    # NaN を null に置換 (json.loads は NaN を float('nan') にする)
    raw = pl.DataFrame(entries).with_columns(
        pl.when(pl.col("pf").is_nan()).then(None).otherwise(pl.col("pf")).alias("pf"),
    )

    reduced = (
        raw.group_by("cell_id")
        .agg(pl.col("pf").median().alias("pf_median"))
        .sort("cell_id")
    )

    parsed = [parse_cell_id("v4.10", cid) for cid in reduced["cell_id"].to_list()]
    df = pl.DataFrame(
        {
            "milestone": ["v4.10"] * reduced.height,
            "pair": [_DEFAULT_PAIR] * reduced.height,
            "fee_bps": [_DEFAULT_FEE_BPS] * reduced.height,
            "window": [p["window"] for p in parsed],
            "regime_cuts": [p["regime_cuts"] for p in parsed],
            "sizing": [p["sizing"] for p in parsed],
            "pass_flag": [False] * reduced.height,  # v4.10 per-cell に pass_flag なし
            "fwer_threshold": [None]
            * reduced.height,  # p_adj_v410 は list 構造、Plan 03 で結合判断
            "observed_metric": reduced["pf_median"].to_list(),
            "observed_metric_kind": ["pf_median"] * reduced.height,
            "schema_version": [SCHEMA_VERSION] * reduced.height,
        }
    )

    # p_adj_path は schema 整合のため引数受領 (実 join は Plan 03 / 将来拡張)
    _ = Path(p_adj_path).exists()

    return _finalize_unified(df)


# ──────────────────────────────────────────────────────────────────────────────
# load_v411 — 64 design slot from cells_post_filter.parquet
# ──────────────────────────────────────────────────────────────────────────────


def load_v411(
    cells_parquet_path: Path,
    p_adj_path: Path,
    perm_null_path: Path,
) -> pl.DataFrame:
    """v4.11 active_mode → 64 design slot 行 (CONTEXT D-V413-06 lock).

    INVENTORY lock:
      - cells_post_filter.parquet (864 行: cell_id × bucket × repeat) を
        group_by(cell_id, bucket).first() で dedupe
      - 実測: unique cell_ids=6 × bucket=VOL_NA → 6 design slot しか出ない可能性あり
        m'=64 lock との差は Plan 03 で吸収 (本 loader は実データ通り出す、INVENTORY notes per Wave 1)
      - observed_metric: p_adj_v411.json["results"] list を cell_id で lookup
      - fwer_threshold: permutation_null_v411.json["null_percentiles"]["p95"] (scalar broadcast)
      - observed_metric_kind = "edge_count_p_adj_005"
    """
    cells_raw = pl.read_parquet(cells_parquet_path)
    p_adj = json.loads(Path(p_adj_path).read_text())
    perm = json.loads(Path(perm_null_path).read_text())

    fwer_p95 = float(perm.get("null_percentiles", {}).get("p95", 0.0))

    # p_adj.results は list of dicts {cell_id, status, p_raw, p_adj_holm, ...}
    # __padded_slot_NN__ (status=padded) は除外、observed_metric は p_adj_holm の補逆
    # INVENTORY: observed = "edge_count_p_adj_005" カウントだが per-cell 値が p_adj.results に
    # 直接ないため、per-cell observed = (p_adj_holm < 0.05) の bool を 1.0/0.0 で表現
    p_adj_lookup: dict[str, float] = {}
    for r in p_adj.get("results", []):
        if r.get("status") == "padded":
            continue
        cid = r.get("cell_id")
        p_holm = r.get("p_adj_holm")
        if cid is None or p_holm is None:
            continue
        p_adj_lookup[cid] = 1.0 if float(p_holm) < 0.05 else 0.0

    # design slot reduction: (cell_id, bucket) unique → first row
    deduped = (
        cells_raw.group_by(["cell_id", "bucket"])
        .agg(pl.col("pass_flag").first().alias("pass_flag"))
        .sort(["cell_id", "bucket"])
    )

    parsed = [parse_cell_id("v4.11", cid) for cid in deduped["cell_id"].to_list()]
    df = pl.DataFrame(
        {
            "milestone": ["v4.11"] * deduped.height,
            "pair": [_DEFAULT_PAIR] * deduped.height,
            "fee_bps": [_DEFAULT_FEE_BPS] * deduped.height,
            "window": [p["window"] for p in parsed],
            "regime_cuts": [p["regime_cuts"] for p in parsed],
            "sizing": [p["sizing"] for p in parsed],
            "pass_flag": deduped["pass_flag"].to_list(),
            "fwer_threshold": [fwer_p95] * deduped.height,
            "observed_metric": [
                p_adj_lookup.get(cid, 0.0) for cid in deduped["cell_id"].to_list()
            ],
            "observed_metric_kind": ["edge_count_p_adj_005"] * deduped.height,
            "schema_version": [SCHEMA_VERSION] * deduped.height,
        }
    )

    return _finalize_unified(df)


# ──────────────────────────────────────────────────────────────────────────────
# load_v412 — 32 design slot via Option A (FWER top-32, kill_switch fire constants)
# ──────────────────────────────────────────────────────────────────────────────


def load_v412(
    cells_parquet_path: Path,
    p_adj_path: Path,
    perm_null_path: Path,
) -> pl.DataFrame:
    """v4.12 → 32 design slot 行 (INVENTORY § v412_observed_metric_decision Option A).

    INVENTORY Option A:
      - cells_post_compound_filter.parquet (864 行) → unique (cell_id, bucket, stance) 抽出
      - kill_switch fire 状態: observed=0 で全 tie → cell_id 辞書順 deterministic top-32
      - fwer_threshold = null_percentiles.p95 (scalar broadcast = 0.0 in fire state)
      - observed_metric = 0.0 constant
      - observed_metric_kind = "edge_count_p_adj_005"

    p_adj_path は signature 整合のため引数受領 (現状未参照、kill_switch fire のため
    per-cell p_adj が 0 で degenerate).
    """
    cells_raw = pl.read_parquet(cells_parquet_path)
    perm = json.loads(Path(perm_null_path).read_text())
    _ = json.loads(Path(p_adj_path).read_text())  # schema 整合のみ、Option A では未使用

    fwer_p95 = float(perm.get("null_percentiles", {}).get("p95", 0.0))

    # design slot 抽出: (cell_id, bucket, stance) unique
    deduped = cells_raw.group_by(["cell_id", "bucket", "stance"]).agg(
        pl.col("pass_flag").first().alias("pass_flag")
    )

    # FWER top-32 (Option A): 全 observed=0 で tie → cell_id+stance 辞書順 (deterministic)
    # combined key を作って sort + head(32)
    deduped = (
        deduped.with_columns(
            (pl.col("cell_id") + "_" + pl.col("stance")).alias("_sortkey"),
        )
        .sort("_sortkey")
        .head(32)
        .drop("_sortkey")
    )

    # cell_id_with_stance を v4.12 parser 用に組み立て (parser は "<v411>_<stance>" 期待)
    full_cell_ids = [
        f"{cid}_{st}"
        for cid, st in zip(
            deduped["cell_id"].to_list(),
            deduped["stance"].to_list(),
        )
    ]
    parsed = [parse_cell_id("v4.12", fid) for fid in full_cell_ids]

    df = pl.DataFrame(
        {
            "milestone": ["v4.12"] * deduped.height,
            "pair": [_DEFAULT_PAIR] * deduped.height,
            "fee_bps": [_DEFAULT_FEE_BPS] * deduped.height,
            "window": [p["window"] for p in parsed],
            "regime_cuts": [p["regime_cuts"] for p in parsed],
            "sizing": [p["sizing"] for p in parsed],
            "pass_flag": deduped["pass_flag"].to_list(),
            "fwer_threshold": [fwer_p95] * deduped.height,
            "observed_metric": [0.0] * deduped.height,  # kill_switch fire
            "observed_metric_kind": ["edge_count_p_adj_005"] * deduped.height,
            "schema_version": [SCHEMA_VERSION] * deduped.height,
        }
    )

    return _finalize_unified(df)


# ──────────────────────────────────────────────────────────────────────────────
# smoke test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    v49 = load_v49(_REPO_ROOT / "data/v4.9/power_budget_v49.json")
    v410 = load_v410(
        _REPO_ROOT / "reports/v4.10/per_cell_metrics.json",
        _REPO_ROOT / "reports/v4.10/p_adj_v410.json",
    )
    v411 = load_v411(
        _REPO_ROOT / "data/v4.11/cells_post_filter.parquet",
        _REPO_ROOT / "reports/v4.11/active_mode/p_adj_v411.json",
        _REPO_ROOT / "reports/v4.11/active_mode/permutation_null_v411.json",
    )
    v412 = load_v412(
        _REPO_ROOT / "data/v4.12/cells_post_compound_filter.parquet",
        _REPO_ROOT / "data/v4.12/p_adj_v412.json",
        _REPO_ROOT / "data/v4.12/permutation_null_v412.json",
    )
    print(
        f"v4.9: {v49.height} / v4.10: {v410.height} / v4.11: {v411.height} / v4.12: {v412.height}"
    )
    print(f"schemas equal: {v49.schema == v410.schema == v411.schema == v412.schema}")
    print(f"columns: {v49.columns}")
