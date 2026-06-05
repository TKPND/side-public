"""scripts/v4.13/emit_ablation_v413.py — Phase 106 ablation 5-axis + top_axis emit.

Output (3 artifact):
    - data/v4.13/diagnosis_v413_ablation.parquet           (5×4=20 行 long-format)
    - data/v4.13/ablation_score.json                       (Rich schema, D-106-04)
    - data/v4.13/diagnosis_v413_ablation_sources.json      (SHA256 chain pin)

Invariants:
    - D-17 / B2: emit_degeneracy_proof.py / aggregate_*.py / decoders.py を import しない (literal copy)
    - D-V413-07: canonical bytes (sort_keys=True, indent=2, ensure_ascii=False, 末尾 \\n)
    - RFC 8259: allow_nan=False で NaN/Infinity 混入を fail-fast (RESEARCH.md Pitfall 1)
    - Idempotent: 2 連続実行で 3 artifact byte-identical

Inputs (Phase 105 contract LOCKED):
    - data/v4.13/diagnosis_v413.parquet (13 列 × 480 行, SHA 8ca18543...b83e)

Citations:
    - 106-CONTEXT.md D-106-01..04
    - 106-RESEARCH.md Pattern 2 (Sobol analytical) / Pitfall 1 (NaN→None)
    - 106-PATTERNS.md emit_ablation_v413.py 1-6 step
    - emit_degeneracy_proof.py:52-103 (literal copy source for helpers)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import polars as pl

# ── Module-level constants (literal copy from emit_degeneracy_proof.py:52, 61, 67, 68) ──
DATA_DIR = Path("data/v4.13")
DIAGNOSIS_PARQUET = DATA_DIR / "diagnosis_v413.parquet"
ABLATION_PARQUET = DATA_DIR / "diagnosis_v413_ablation.parquet"
ABLATION_SCORE_JSON = DATA_DIR / "ablation_score.json"
ABLATION_SOURCES_JSON = DATA_DIR / "diagnosis_v413_ablation_sources.json"

SCHEMA_VERSION_NEW = "v4.13.1"
RESEARCH_REF = ".planning/phases/106-ablation-5-top-axis/106-RESEARCH.md"
EXPECTED_PARENT_SHA = "8ca18543a433a82aaaabd1adb7679f838e5d37cfc0f7a541c0dde601a7c3b83e"

DIMENSIONS = [
    "pair",
    "fee_bps",
    "window",
    "regime_cuts",
    "sizing",
]  # emit_degeneracy_proof.py:67 と一致
MILESTONES = ["v4.9", "v4.10", "v4.11", "v4.12"]  # emit_degeneracy_proof.py:68 と一致


# ── Atomic write helpers (literal copy from emit_degeneracy_proof.py:76-103, B2) ──
def _atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    """tmp file + os.replace で atomic write (parquet)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)


def _atomic_write_canonical_json(d: dict, path: Path) -> None:
    """canonical bytes (D-V413-07) で atomic write (JSON sidecar).

    allow_nan=False で NaN/Infinity 混入時に ValueError fail-fast (RFC 8259 準拠)。
    emit 側で float('nan') を None に事前変換すること (RESEARCH.md Pitfall 1)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _sha256_of_file(path: Path) -> str:
    """SHA256 hex digest (file 全体)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _research_commit(research_ref: str = RESEARCH_REF) -> str:
    """RESEARCH.md を最後に変更した commit hash を pin する (run-to-run 不変)。

    `git rev-parse HEAD` だと emit 実行時の HEAD に追従して artifact が drift する
    (Phase 106 Wave 1 で検出)。RESEARCH.md の last-modified commit を引くことで
    research_commit 値を RESEARCH.md の内容に紐付ける。
    """
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", research_ref],
            text=True,
        ).strip()
        return out or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


# ── Ablation core ─────────────────────────────────────────────────────────────
def compute_ablation(df: pl.DataFrame) -> pl.DataFrame:
    """5 dim × 4 milestone の ablation 計算 → 20 行 long-format DataFrame.

    D-106-01: input は diagnosis_v413.parquet (cell-level, 13 列 × 480 行)。
    - baseline_pass_count = df.filter(milestone).select(pass_flag).sum()  (=0 全 milestone)
    - ablated_pass_count  = df.filter(milestone & pass_flag).select(other 4 dims).unique().height (=0)
    - delta               = baseline_pass_count - ablated_pass_count (=0 全シナリオ)
    """
    rows = []
    for m in MILESTONES:
        df_m = df.filter(pl.col("milestone") == m)
        assert df_m.height > 0, f"milestone {m} not found in parquet"
        baseline = int(
            df_m.filter(pl.col("pass_flag")).height
        )  # = 0 (Phase 105 forensic)
        for dim in DIMENSIONS:
            other = [d for d in DIMENSIONS if d != dim]
            ablated = int(
                df_m.filter(pl.col("pass_flag")).select(other).unique().height
            )
            rows.append(
                {
                    "milestone": m,
                    "dimension": dim,
                    "baseline_pass_count": baseline,
                    "ablated_pass_count": ablated,
                    "delta": baseline - ablated,
                }
            )
    return pl.DataFrame(
        rows,
        schema={
            "milestone": pl.Utf8,
            "dimension": pl.Utf8,
            "baseline_pass_count": pl.Int64,
            "ablated_pass_count": pl.Int64,
            "delta": pl.Int64,
        },
    )


def compute_sobol_analytical(
    df_long: pl.DataFrame,
) -> tuple[dict, dict]:
    """Sobol analytical first/total-order on 20 ablation observations (D-106-03).

    Var[Y]=0 (全 ablated_pass_count=0) → 全 axis None (RFC 8259 / Pitfall 1)。

    Formula:
        S_i   = Var[E[Y|X_i]]   / Var[Y]
        S_T,i = E[Var[Y|X_~i]]  / Var[Y]

    Note (advisor 指摘 — 2026-04-27):
        else-branch は v4.14+ scope。現 Phase 106 は Var[Y]=0 退化解前提なので
        到達不可能。万一 Var[Y]>0 になる data に変わったら、proper conditional
        grouping (Saltelli sample design) を別途実装する必要があるため、
        landmine 防止に NotImplementedError で fail-fast。
    """
    Y = df_long["ablated_pass_count"].to_numpy()
    var_Y = float(np.var(Y, ddof=0))
    if var_Y == 0.0:
        return (
            {a: None for a in DIMENSIONS},
            {a: None for a in DIMENSIONS},
        )

    # 到達禁止: Phase 106 data (全 ablated_pass_count=0) では Var[Y]=0 が確実
    raise NotImplementedError(
        "v4.14+ scope: Var[Y]>0 case requires proper conditional grouping "
        "(Saltelli sample design). Current Phase 106 data is degenerate "
        f"(Var[Y]=0); got Var[Y]={var_Y!r}."
    )


def compute_axis_cardinality(parent_df: pl.DataFrame) -> dict:
    """各 axis の n_unique を全 480 行を対象に計算 (D-106-02)."""
    return {dim: int(parent_df[dim].n_unique()) for dim in DIMENSIONS}


def select_top_axes(axis_cardinality: dict) -> tuple[str, str]:
    """Hybrid tie-breaker: cardinality 降順 + alphabetical fallback (D-106-02)."""
    sorted_axes = sorted(axis_cardinality.items(), key=lambda kv: (-kv[1], kv[0]))
    return sorted_axes[0][0], sorted_axes[1][0]


def build_milestone_breakdown(df_long: pl.DataFrame) -> dict:
    """4 milestone × {baseline_pass_count, ablated_pass_count{axis}, delta_by_axis{axis}}."""
    out = {}
    for m in MILESTONES:
        df_m = df_long.filter(pl.col("milestone") == m)
        baseline = int(df_m["baseline_pass_count"][0])  # 同 milestone 内で全 dim 共通
        ablated_per_axis = {
            row["dimension"]: int(row["ablated_pass_count"])
            for row in df_m.iter_rows(named=True)
        }
        delta_per_axis = {
            row["dimension"]: int(row["delta"]) for row in df_m.iter_rows(named=True)
        }
        out[m] = {
            "baseline_pass_count": baseline,
            "ablated_pass_count": ablated_per_axis,
            "delta_by_axis": delta_per_axis,
        }
    return out


def build_ablation_score(
    df_long: pl.DataFrame,
    parent_df: pl.DataFrame,
) -> dict:
    """ablation_score.json dict (D-106-04 Rich schema, 11 top-level keys)."""
    first_order, total_order = compute_sobol_analytical(df_long)
    axis_cardinality = compute_axis_cardinality(parent_df)
    top_axis, secondary_axis = select_top_axes(axis_cardinality)
    all_delta_zero = bool((df_long["delta"] == 0).all())
    all_first_none = all(v is None for v in first_order.values())
    all_total_none = all(v is None for v in total_order.values())
    trivial = bool(all_delta_zero and all_first_none and all_total_none)
    return {
        "schema_version": SCHEMA_VERSION_NEW,
        "research_ref": RESEARCH_REF,
        "research_commit": _research_commit(),
        "axes": list(DIMENSIONS),
        "axis_cardinality": axis_cardinality,
        "first_order": first_order,
        "total_order": total_order,
        "top_axis": top_axis,
        "secondary_axis": secondary_axis,
        "trivial_baseline_pathway": trivial,
        "milestone_breakdown": build_milestone_breakdown(df_long),
    }


def build_ablation_sources(
    parent_path: Path,
    ablation_parquet: Path,
    ablation_score: Path,
) -> dict:
    """SHA256 chain (parent + self + sibling score) を pin する sources sidecar."""
    return {
        "schema_version": SCHEMA_VERSION_NEW,
        "parent_diagnosis_v413_sha256": _sha256_of_file(parent_path),
        "ablation_parquet_sha256": _sha256_of_file(ablation_parquet),
        "ablation_score_sha256": _sha256_of_file(ablation_score),
        "expected_row_count_ablation": 20,
        "expected_row_count_formula": "len(DIMENSIONS) * len(MILESTONES)",
        "research_ref": RESEARCH_REF,
        "research_commit": _research_commit(),
    }


# ── main() — 6 step flow (PATTERNS.md §6) ─────────────────────────────────────
def main() -> None:
    # 1. parent SHA fail-fast + read
    actual_parent_sha = _sha256_of_file(DIAGNOSIS_PARQUET)
    assert actual_parent_sha == EXPECTED_PARENT_SHA, (
        f"parent diagnosis_v413.parquet SHA drift: "
        f"expected {EXPECTED_PARENT_SHA}, got {actual_parent_sha}"
    )
    parent_df = pl.read_parquet(DIAGNOSIS_PARQUET)
    assert parent_df.shape == (480, 13), f"expected (480, 13), got {parent_df.shape}"

    # 2. per-milestone × per-axis ablation loop → 20 行 long-format
    df_long = compute_ablation(parent_df)
    assert df_long.shape == (20, 5), f"expected (20, 5), got {df_long.shape}"

    # 3. Sobol + 4. cardinality + tie-breaker は build_ablation_score 内で実行
    score_dict = build_ablation_score(df_long, parent_df)

    # 5. atomic write parquet (advisor 指摘: write 直前で明示 sort で行順序を決定的化)
    df_long = df_long.sort(["milestone", "dimension"])
    _atomic_write_parquet(df_long, ABLATION_PARQUET)
    _atomic_write_canonical_json(score_dict, ABLATION_SCORE_JSON)

    # 6. atomic write sources JSON (sibling SHA chain)
    sources_dict = build_ablation_sources(
        DIAGNOSIS_PARQUET, ABLATION_PARQUET, ABLATION_SCORE_JSON
    )
    _atomic_write_canonical_json(sources_dict, ABLATION_SOURCES_JSON)

    print(
        f"emit_ablation_v413: wrote {ABLATION_PARQUET}, "
        f"{ABLATION_SCORE_JSON}, {ABLATION_SOURCES_JSON}"
    )


if __name__ == "__main__":
    main()
