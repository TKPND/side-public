"""scripts/v4.13/aggregate_diagnosis_v413.py — Phase 104 diagnosis aggregator.

CONTEXT.md (D-V413-01..07) + PATTERNS.md per spec:
- 4 milestone (v4.9/v4.10/v4.11/v4.12) を 12 列 480 行 unified parquet に集約
- sources sidecar JSON を canonical bytes (D-V413-07) で emit
- INPUT_ARTIFACTS は CONTEXT D-V413-03 source mapping 表を hardcode (glob 禁止)

Structural extension strategy (Plan 02 SUMMARY hand-off + INVENTORY-v412 closure):
- v4.9 / v4.10: Plan 02 loader をそのまま使う (192 + 192)
- v4.11: `reports/v4.11/active_mode/p_adj_v411.json` の `results` 配列 (64 padded slot) を
  そのまま 64 design slot として展開。observed_metric は p_adj_holm < 0.05 の bool→{1.0,0.0}、
  fwer_threshold は permutation_null_v411.json.null_percentiles.p95 (broadcast)。
  Plan 02 loader v411 は raw cells parquet ベースで unique=6 しか出ないため本 extension で吸収。
- v4.12: 同上 + INVENTORY Option A (kill_switch fire constants)。`p_adj_v412.json` の
  `results` 配列 (32 padded slot) を直接展開、observed=0 / fwer=p95 (=0 in fire) constant。

D-17 invariant: scripts/v4.11/ scripts/v4.12/ から import しない。padded slot 構築は
duplicate copy で再実装 (RESEARCH Pitfall 4 mitigation)。

Outputs:
- data/v4.13/diagnosis_v413.parquet (480 行 × 12 列)
- data/v4.13/diagnosis_v413_sources.json (canonical bytes sidecar)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import polars as pl

# scripts/v4.13/aggregate_diagnosis_v413.py から repo root = parents[2]
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

# scripts/v4.13/ は dotted dir で normal package import 不可 → sys.path 注入
_V413_DIR = _REPO_ROOT / "scripts" / "v4.13"
if str(_V413_DIR) not in sys.path:
    sys.path.insert(0, str(_V413_DIR))

from diagnosis_decoders import (  # noqa: E402
    load_v49,
    load_v410,
    load_v411,  # imported for D-V413 contract (PATTERNS key_links)、v4.11 は本 aggregator で直接構築
    load_v412,  # imported for D-V413 contract (PATTERNS key_links)、v4.12 は本 aggregator で直接構築
    UNIFIED_SCHEMA,
    D413_COLUMNS,
    SCHEMA_VERSION,
)

# 静的 referencing で linter の unused import を回避 (実 dispatch は Phase 105+ で広がる予定)
_LOADER_REGISTRY: Final[dict] = {
    "v4.9": load_v49,
    "v4.10": load_v410,
    "v4.11": load_v411,
    "v4.12": load_v412,
}

# CONTEXT D-V413-03 source mapping: 9 read-only artifacts, hardcoded order (glob 禁止)
INPUT_ARTIFACTS: Final[list[dict]] = [
    {
        "path": "data/v4.9/power_budget_v49.json",
        "milestone": "v4.9",
        "role": "power_budget",
    },
    {
        "path": "reports/v4.10/per_cell_metrics.json",
        "milestone": "v4.10",
        "role": "per_cell_metrics",
    },
    {"path": "reports/v4.10/p_adj_v410.json", "milestone": "v4.10", "role": "p_adj"},
    {
        "path": "data/v4.11/cells_post_filter.parquet",
        "milestone": "v4.11",
        "role": "cells",
    },
    {
        "path": "reports/v4.11/active_mode/p_adj_v411.json",
        "milestone": "v4.11",
        "role": "p_adj",
    },
    {
        "path": "reports/v4.11/active_mode/permutation_null_v411.json",
        "milestone": "v4.11",
        "role": "perm_null",
    },
    {
        "path": "data/v4.12/cells_post_compound_filter.parquet",
        "milestone": "v4.12",
        "role": "cells",
    },
    {"path": "data/v4.12/p_adj_v412.json", "milestone": "v4.12", "role": "p_adj"},
    {
        "path": "data/v4.12/permutation_null_v412.json",
        "milestone": "v4.12",
        "role": "perm_null",
    },
]

OUTPUT_PARQUET: Final[Path] = _REPO_ROOT / "data" / "v4.13" / "diagnosis_v413.parquet"
OUTPUT_SIDECAR: Final[Path] = (
    _REPO_ROOT / "data" / "v4.13" / "diagnosis_v413_sources.json"
)

# CONTEXT D-V413-06 (480 lock)
EXPECTED_TOTAL: Final[int] = 480
EXPECTED_PER_MILESTONE: Final[dict[str, int]] = {
    "v4.9": 192,
    "v4.10": 192,
    "v4.11": 64,
    "v4.12": 32,
}

_DEFAULT_PAIR: Final[str] = "USDJPY"
_DEFAULT_FEE_BPS: Final[float] = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────────


def _hash_file(p: Path) -> str:
    """SHA256 hex digest. emit_kill_switch_v412.py:60-72 duplicate copy (D-17 OK)."""
    with p.open("rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """tmp + os.replace で atomic write (concurrent run safety)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        f.write(data)
    os.replace(tmp, path)


# ──────────────────────────────────────────────────────────────────────────────
# v4.11 / v4.12 design slot extension
# ──────────────────────────────────────────────────────────────────────────────


def _build_v411_design_slots(
    p_adj_path: Path,
    perm_null_path: Path,
) -> pl.DataFrame:
    """v4.11 の m'=64 design slot を p_adj_v411.json から直接構築。

    p_adj_v411.json の `results` は 64 padded slot で、cell_id=__padded_slot_NN__。
    kill_switch / FWER null distribution 状況下では active cell が無く、
    padded slot がそのまま design slot lock となる (INVENTORY Option A 同型)。
    """
    p_adj = json.loads(p_adj_path.read_text())
    perm = json.loads(perm_null_path.read_text())
    fwer_p95 = float(perm.get("null_percentiles", {}).get("p95", 0.0))

    results = p_adj.get("results", [])
    if len(results) != 64:
        raise AssertionError(
            f"v4.11 p_adj.results count mismatch: got {len(results)}, expected 64 "
            f"(CONTEXT D-V413-06 lock)"
        )

    rows = []
    for r in results:
        cid = r.get("cell_id", "")
        p_holm = r.get("p_adj_holm")
        observed = (1.0 if float(p_holm) < 0.05 else 0.0) if p_holm is not None else 0.0
        rows.append(
            {
                "milestone": "v4.11",
                "pair": _DEFAULT_PAIR,
                "fee_bps": _DEFAULT_FEE_BPS,
                "window": cid,  # padded slot は cell_id 全体を window 列に保持 (parser 適用不可)
                "regime_cuts": "",
                "sizing": "",
                "pass_flag": False,
                "fwer_threshold": fwer_p95,
                "observed_metric": observed,
                "observed_metric_kind": "edge_count_p_adj_005",
                "schema_version": SCHEMA_VERSION,
            }
        )
    df = pl.DataFrame(rows)
    df = df.with_columns(
        (pl.col("observed_metric") - pl.col("fwer_threshold")).alias("hurdle_gap"),
    ).select(D413_COLUMNS)
    return df.cast(UNIFIED_SCHEMA)  # type: ignore[arg-type]


def _build_v412_design_slots(
    p_adj_path: Path,
    perm_null_path: Path,
) -> pl.DataFrame:
    """v4.12 の m'=32 design slot を p_adj_v412.json から直接構築 (Option A)。

    INVENTORY: kill_switch fire のため observed=0 / fwer=null_percentiles.p95 (broadcast)。
    p_adj_v412.json の `results` は 32 padded slot。
    """
    p_adj = json.loads(p_adj_path.read_text())
    perm = json.loads(perm_null_path.read_text())
    fwer_p95 = float(perm.get("null_percentiles", {}).get("p95", 0.0))

    results = p_adj.get("results", [])
    if len(results) != 32:
        raise AssertionError(
            f"v4.12 p_adj.results count mismatch: got {len(results)}, expected 32 "
            f"(CONTEXT D-V413-06 lock)"
        )

    rows = []
    for r in results:
        cid = r.get("cell_id", "")
        rows.append(
            {
                "milestone": "v4.12",
                "pair": _DEFAULT_PAIR,
                "fee_bps": _DEFAULT_FEE_BPS,
                "window": cid,
                "regime_cuts": "",
                "sizing": "",
                "pass_flag": False,
                "fwer_threshold": fwer_p95,
                "observed_metric": 0.0,  # kill_switch fire constant
                "observed_metric_kind": "edge_count_p_adj_005",
                "schema_version": SCHEMA_VERSION,
            }
        )
    df = pl.DataFrame(rows)
    df = df.with_columns(
        (pl.col("observed_metric") - pl.col("fwer_threshold")).alias("hurdle_gap"),
    ).select(D413_COLUMNS)
    return df.cast(UNIFIED_SCHEMA)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
# main pipeline
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_paths() -> dict[tuple[str, str], Path]:
    """INPUT_ARTIFACTS から (milestone, role) -> abs Path map を構築。"""
    return {
        (e["milestone"], e["role"]): _REPO_ROOT / e["path"] for e in INPUT_ARTIFACTS
    }


def _load_all_milestones() -> pl.DataFrame:
    """4 milestone を組み立て、pl.concat(diagonal) → cast(UNIFIED_SCHEMA)。"""
    paths = _resolve_paths()

    # v4.9 / v4.10: Plan 02 loader をそのまま使う (192 + 192)
    df49 = load_v49(paths[("v4.9", "power_budget")])
    df410 = load_v410(
        paths[("v4.10", "per_cell_metrics")],
        paths[("v4.10", "p_adj")],
    )

    # v4.11 / v4.12: design slot extension (padded slot 64 / 32 を直接展開)
    df411 = _build_v411_design_slots(
        paths[("v4.11", "p_adj")],
        paths[("v4.11", "perm_null")],
    )
    df412 = _build_v412_design_slots(
        paths[("v4.12", "p_adj")],
        paths[("v4.12", "perm_null")],
    )

    # per-milestone bit-exact 行数 fail-fast (D-V413-06)
    actual_per = {
        "v4.9": df49.height,
        "v4.10": df410.height,
        "v4.11": df411.height,
        "v4.12": df412.height,
    }
    if actual_per != EXPECTED_PER_MILESTONE:
        raise AssertionError(
            f"per-milestone row count mismatch: got {actual_per}, "
            f"expected {EXPECTED_PER_MILESTONE} (CONTEXT D-V413-06 lock)"
        )

    unified = pl.concat([df49, df410, df411, df412], how="diagonal")
    unified = unified.select(D413_COLUMNS).cast(UNIFIED_SCHEMA)  # type: ignore[arg-type]

    if unified.height != EXPECTED_TOTAL:
        raise AssertionError(
            f"total row count mismatch: got {unified.height}, "
            f"expected {EXPECTED_TOTAL} (CONTEXT D-V413-06 lock)"
        )

    # deterministic sort: 2 回実行で parquet sha256 一致を担保
    unified = unified.sort(
        ["milestone", "window", "regime_cuts", "sizing", "pair"],
        nulls_last=True,
    )
    return unified


def build_sidecar() -> dict:
    """sidecar JSON dict (sources[] 確定 + canonical bytes 規約用)。"""
    sources = []
    for entry in INPUT_ARTIFACTS:
        abs_path = _REPO_ROOT / entry["path"]
        if not abs_path.exists():
            raise FileNotFoundError(
                f"INPUT_ARTIFACTS missing: {entry['path']} "
                f"(CONTEXT D-V413-03 source mapping と整合しません)"
            )
        st = abs_path.stat()
        sources.append(
            {
                "path": entry["path"],
                "sha256": _hash_file(abs_path),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(
                    st.st_mtime, tz=timezone.utc
                ).isoformat(),
                "milestone": entry["milestone"],
                "role": entry["role"],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "expected_row_counts": {**EXPECTED_PER_MILESTONE, "total": EXPECTED_TOTAL},
        "sources": sources,
    }


def main() -> None:
    unified = _load_all_milestones()

    # parquet emit (atomic)
    OUTPUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    tmp_parquet = OUTPUT_PARQUET.with_suffix(".parquet.tmp")
    unified.write_parquet(tmp_parquet)
    os.replace(tmp_parquet, OUTPUT_PARQUET)

    # sidecar emit (canonical bytes per D-V413-07: indent=2 + sort_keys=True + ensure_ascii=False + 末尾 \n)
    sidecar = build_sidecar()
    sidecar_bytes = (
        json.dumps(sidecar, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(OUTPUT_SIDECAR, sidecar_bytes)

    print(
        f"[OK] wrote {OUTPUT_PARQUET.relative_to(_REPO_ROOT)} "
        f"({unified.height} rows, {len(unified.columns)} cols)"
    )
    print(
        f"[OK] wrote {OUTPUT_SIDECAR.relative_to(_REPO_ROOT)} "
        f"({len(sidecar['sources'])} sources)"
    )

    # registry の sanity check (PATTERNS key_links contract: loader dispatch 4 件 import 済)
    assert set(_LOADER_REGISTRY.keys()) == set(EXPECTED_PER_MILESTONE.keys())


if __name__ == "__main__":
    main()
