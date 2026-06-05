"""scripts/v4.13/emit_degeneracy_proof.py — Phase 105 Wave 2 Resolution A 単一 entry-point.

Purpose:
    RESEARCH.md commit fcff705 で立証された「全 480 cell degenerate」を artifact 化。
    Phase 104 出力 (`data/v4.13/diagnosis_v413.parquet`) を読み、in-place で
    schema bump (v4.13.0 → v4.13.1) + `failure_mode='degenerate'` 列追加 +
    `hurdle_gap` 全 NULL に変更し、long-format histogram + sidecar JSON 2 件を emit する。

Output (5 artifact / Resolution A):
    - data/v4.13/diagnosis_v413.parquet                       (in-place 上書き、12→13 列)
    - data/v4.13/diagnosis_v413.parquet.phase104_backup       (W5: 1-shot backup)
    - data/v4.13/diagnosis_v413_failure_modes.parquet         (long-format, 5 列, 166 行)
    - data/v4.13/diagnosis_v413_degeneracy_evidence.json      (D-105-06 causal chain)
    - data/v4.13/diagnosis_v413_failure_modes_sources.json    (SHA256 chain pin)

Invariants:
    - D-17: scripts/v4.11/, scripts/v4.12/, scripts/v4.13/aggregate_diagnosis_v413.py,
      scripts/v4.13/diagnosis_decoders.py を import / modify しない (B2)。
    - W5: backup は 1-shot (再 emit で上書き禁止)。
    - D-105-06: 4 milestone の causal_fields + intended_threshold_scale を CONTEXT.md
      LOCKED 表 literal で hard-pin (B4)。
    - D-V413-07: canonical bytes (sort_keys=True, indent=2, ensure_ascii=False, 末尾 \n)。
    - Idempotent: 2 回連続実行で 4 artifact 全て byte-identical (W5、
      tests/v4_13/test_phase_105_idempotent.py で機械固定)。

Cardinality (Errata 2026-04-27 で 136→166 訂正):
    sum_over_milestone(sum_over_dim(N_unique(dim_value, milestone)))
        v4.9:  1+1+16+1+12 = 31
        v4.10: 1+1+16+1+12 = 31
        v4.11: 1+1+64+1+1  = 68
        v4.12: 1+1+32+1+1  = 36
        合計: 166 (literal pin in failure_modes_sources.json)

Usage:
    uv run python -m scripts.v4_13.emit_degeneracy_proof
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import polars as pl

# ──────────────────────────────────────────────────────────────────────────────
# Path constants
# ──────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("data/v4.13")
DIAGNOSIS_PARQUET = DATA_DIR / "diagnosis_v413.parquet"
PHASE104_BACKUP = DATA_DIR / "diagnosis_v413.parquet.phase104_backup"
FAILURE_MODES_PARQUET = DATA_DIR / "diagnosis_v413_failure_modes.parquet"
EVIDENCE_JSON = DATA_DIR / "diagnosis_v413_degeneracy_evidence.json"
FAILURE_MODES_SOURCES_JSON = DATA_DIR / "diagnosis_v413_failure_modes_sources.json"

# Resolution A 1-mode 定数
DEGENERATE_MODE = "degenerate"
SCHEMA_VERSION_NEW = "v4.13.1"
RESEARCH_COMMIT = "fcff705"
RESEARCH_REF = ".planning/phases/105-hurdle-gap-analysis/105-RESEARCH.md"
EXPECTED_ROW_COUNT_FAILURE_MODES = 166  # Errata 2026-04-27 (元 136、転記エラー訂正)

# 5 次元 ablation (D-105-03 forensic completeness)
DIMENSIONS = ["pair", "fee_bps", "window", "regime_cuts", "sizing"]
MILESTONES = ["v4.9", "v4.10", "v4.11", "v4.12"]


# ──────────────────────────────────────────────────────────────────────────────
# Local helpers (B2: aggregate_diagnosis_v413.py からの import 禁止、独立実装)
# ──────────────────────────────────────────────────────────────────────────────


def _atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    """tmp file + os.replace で atomic write (parquet)。

    polars.write_parquet で tmp に書いてから os.replace で原子的に rename。
    途中失敗で半端な parquet が残らないことを保証 (T-105-05 mitigation)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)


def _atomic_write_canonical_json(d: dict, path: Path) -> None:
    """canonical bytes (D-V413-07) で atomic write (JSON sidecar)。

    canonical 規約: sort_keys=True, indent=2, ensure_ascii=False, 末尾 \\n。
    aggregate_diagnosis_v413.py / emit_kill_switch_v412.py と同一規約 (D-17 read-only ref)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _sha256_of_file(path: Path) -> str:
    """SHA256 hex digest (file 全体)。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup_phase104_once(parquet_path: Path) -> None:
    """W5: backup が無ければ shutil.copy2 で 1-shot 作成、既存ならば skip。

    Phase 104 raw hurdle_gap データの recovery 経路を確保 (T-105-16 mitigation)。
    """
    backup = parquet_path.with_suffix(parquet_path.suffix + ".phase104_backup")
    if not backup.exists():
        shutil.copy2(parquet_path, backup)


# ──────────────────────────────────────────────────────────────────────────────
# Core transforms
# ──────────────────────────────────────────────────────────────────────────────


def upgrade_diagnosis_to_v413_1(df: pl.DataFrame) -> pl.DataFrame:
    """Phase 104 (12 列, v4.13.0) を Phase 105 (13 列, v4.13.1) に bump。

    変更:
        - hurdle_gap: 既存値を捨てて全 NULL Float64 に上書き (D-105-04)
        - failure_mode: 'degenerate' (Utf8) を全 480 行に追加 (D-105-02)
        - schema_version: v4.13.0 → v4.13.1 (D-105-05)

    deterministic sort: idempotent emit のため milestone + 全 dim 列で sort 固定。
    """
    upgraded = df.with_columns(
        [
            pl.lit(None).cast(pl.Float64).alias("hurdle_gap"),
            pl.lit(DEGENERATE_MODE).alias("failure_mode"),
            pl.lit(SCHEMA_VERSION_NEW).alias("schema_version"),
        ]
    )
    # idempotent guarantee: row order を固定
    sort_keys = [c for c in ("milestone", *DIMENSIONS) if c in upgraded.columns]
    return upgraded.sort(sort_keys)


def emit_failure_modes_histogram(df: pl.DataFrame) -> pl.DataFrame:
    """5 次元 long-format histogram を構築 (D-105-03 / Resolution A 1-mode)。

    schema:
        milestone (Utf8) | dimension (Utf8) | dim_value (Utf8) | mode (Utf8) | count (UInt32)

    cardinality (per RESEARCH.md Finding 5, Errata 2026-04-27):
        v4.9: 1+1+16+1+12 = 31, v4.10: 31, v4.11: 68, v4.12: 36 → 合計 166

    forensic completeness のため cardinality=1 dimension (pair, fee_bps, regime_cuts) も
    保持する (Phase 106 ablation 計画見直しのトレース用)。
    """
    out_frames: list[pl.DataFrame] = []
    for dim in DIMENSIONS:
        # group_by(milestone, dim, failure_mode) → count
        agg = (
            df.group_by(["milestone", dim, "failure_mode"])
            .agg(pl.len().cast(pl.UInt32).alias("count"))
            .rename({dim: "dim_value", "failure_mode": "mode"})
            .with_columns(
                [
                    pl.lit(dim).alias("dimension"),
                    pl.col("dim_value").cast(pl.Utf8),  # Int64 等が混ざる可能性
                ]
            )
            .select(["milestone", "dimension", "dim_value", "mode", "count"])
        )
        out_frames.append(agg)

    hist = pl.concat(out_frames, how="vertical")
    # deterministic sort: milestone → dimension → dim_value (W5 idempotent)
    return hist.sort(["milestone", "dimension", "dim_value"])


def build_degeneracy_evidence(df: pl.DataFrame) -> dict:
    """4 milestone causal_fields + intended_threshold_scale dict を構築 (D-105-06、B4 LOCKED)。

    causal_fields literal: CONTEXT.md D-105-06 + RESEARCH.md Findings 1-5。
    intended_threshold_scale literal: CONTEXT.md D-105-06 LOCKED 表 (B4)。
    """
    # per-milestone row count (parquet 実測値)
    counts = {ms: int(df.filter(pl.col("milestone") == ms).height) for ms in MILESTONES}

    milestones_dict = {
        "v4.9": {
            "row_count": counts["v4.9"],
            "causal_fields": [
                "f_star_min=0.0 (all 192 cells)",
                "robust_pass=false (0/192)",
                "fwer_threshold=NULL (intentional, decoder line 200-203)",
            ],
            "intended_threshold_scale": "TBD (Kelly fraction natural unit candidate)",
        },
        "v4.10": {
            "row_count": counts["v4.10"],
            "causal_fields": [
                "fwer_threshold=NULL (deferred join unimplemented, decoder line 200-203)",
                "all cells fail WFD pf_median",
            ],
            "intended_threshold_scale": "pf_median=1.0",
        },
        "v4.11": {
            "row_count": counts["v4.11"],
            "causal_fields": [
                "all 64 cells = __padded_slot_NN__",
                "n_tested=0 / n_padded=64",
                "null_percentiles all 0.0 (milestone-aggregate broadcast)",
            ],
            "intended_threshold_scale": "edge_count_p_adj_005=m_prime=64",
        },
        "v4.12": {
            "row_count": counts["v4.12"],
            "causal_fields": [
                "all 32 cells = __padded_slot_NN__",
                "n_tested=0 / n_padded=32 / m_prime=32",
                "null_percentiles all 0.0",
            ],
            "intended_threshold_scale": "edge_count_p_adj_005=m_prime=32",
        },
    }

    return {
        "schema_version": SCHEMA_VERSION_NEW,
        "research_ref": RESEARCH_REF,
        "research_commit": RESEARCH_COMMIT,
        "summary": "All 480 cells degenerate by Phase 104 emit; normalization not computable.",
        "milestones": milestones_dict,
    }


def build_failure_modes_sources(
    parent_path: Path,
    self_path: Path,
    evidence_path: Path,
) -> dict:
    """SHA256 chain (parent + self + evidence) を pin する sources sidecar。"""
    return {
        "schema_version": SCHEMA_VERSION_NEW,
        "parent_diagnosis_v413_sha256": _sha256_of_file(parent_path),
        "self_sha256": _sha256_of_file(self_path),
        "evidence_sha256": _sha256_of_file(evidence_path),
        "expected_row_count_failure_modes": EXPECTED_ROW_COUNT_FAILURE_MODES,
        "expected_row_count_formula": (
            "sum_over_milestone(sum_over_dim(N_unique(dim_value, milestone) * 1))"
        ),
        "expected_row_count_breakdown": {
            "v4.9": "1+1+16+1+12 = 31",
            "v4.10": "1+1+16+1+12 = 31",
            "v4.11": "1+1+64+1+1 = 68",
            "v4.12": "1+1+32+1+1 = 36",
        },
        "research_ref": RESEARCH_REF,
        "research_commit": RESEARCH_COMMIT,
    }


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────


def main() -> None:
    """5 artifact (4 emit + 1 backup) を atomic に書く。"""
    # 1. W5: Phase 104 raw 版を 1-shot backup (in-place 上書きの直前)
    _backup_phase104_once(DIAGNOSIS_PARQUET)

    # 2. read parquet (Phase 104, 12 列)
    df_phase104 = pl.read_parquet(DIAGNOSIS_PARQUET)
    assert df_phase104.height == 480, (
        f"Phase 104 parquet row count must be 480: got {df_phase104.height}"
    )

    # 3. upgrade to v4.13.1 (13 列) + in-place atomic write
    df_upgraded = upgrade_diagnosis_to_v413_1(df_phase104)
    assert df_upgraded.shape == (480, 13), (
        f"upgraded shape must be (480, 13): got {df_upgraded.shape}"
    )
    _atomic_write_parquet(df_upgraded, DIAGNOSIS_PARQUET)

    # 4. failure_modes histogram emit
    hist = emit_failure_modes_histogram(df_upgraded)
    assert hist.height == EXPECTED_ROW_COUNT_FAILURE_MODES, (
        f"histogram row count must be {EXPECTED_ROW_COUNT_FAILURE_MODES}: got {hist.height}"
    )
    _atomic_write_parquet(hist, FAILURE_MODES_PARQUET)

    # 5. evidence sidecar
    evidence = build_degeneracy_evidence(df_upgraded)
    _atomic_write_canonical_json(evidence, EVIDENCE_JSON)

    # 6. sources sidecar (SHA256 chain は emit 済み artifact から再 hash)
    sources = build_failure_modes_sources(
        parent_path=DIAGNOSIS_PARQUET,
        self_path=FAILURE_MODES_PARQUET,
        evidence_path=EVIDENCE_JSON,
    )
    _atomic_write_canonical_json(sources, FAILURE_MODES_SOURCES_JSON)


if __name__ == "__main__":
    main()
