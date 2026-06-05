"""SHIP-V412-05 / D-03: standalone kill_switch_v412.json emitter.

Phase 102 cells_post_compound_filter.parquet を read-only で consume し、
新規 data/v4.12/kill_switch_v412.json を emit する。Phase 102 parquet には
書き戻さず、Phase 102/103 責務境界を明確化 (D-03)。

Aggregation logic (Phase 101 nyquist_audit_v412 整合):
- HAWK/DOV stance × vol bucket = compound strata。NEUT は kill_set=True で除外
- 各 compound stratum 内で n_active = count(rows where kill_set=False, stance ∈ {HAWK, DOV})
- kill_switch_fired = TRUE if any compound stratum has n_active < 20

Schema fallback (Phase 102 D-18 mode().first() pivot 経由で vol_bucket 系列が
parquet に保持されていない場合):
  - vol_bucket / bucket 列が存在 → group_by(["bucket", "stance"])
  - 列不在 → group_by("stance") のみ (n_compound_strata = 2 上限)
  - kill_set 列が parquet に存在しない場合 → pass_flag を kill_set proxy として使う
    (pass_flag=False == kill_set=True の意味論、Phase 102 D-09)
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_CELLS_POST_FILTER: Path = (
    _REPO_ROOT / "data" / "v4.12" / "cells_post_compound_filter.parquet"
)
_OUTPUT: Path = _REPO_ROOT / "data" / "v4.12" / "kill_switch_v412.json"

_SCHEMA_VERSION: str = "v4.12"
_DATA_PROVENANCE: str = "macro-stance-v412-9160234"  # signal_commit_v412[:7]
_NYQUIST_THRESHOLD: int = 20  # Phase 101 nyquist_audit_v412 整合
_EXPECTED_SOURCE_SHA256: str = (
    "1f4b31c953a7ca183b46953f6852d7849b49a66d1e7fb40e1edda035f6206b79"
)
_SHA256_OVERRIDE_ENV: str = "V412_SOURCE_SHA256_OVERRIDE"
_ACTIVE_STANCES = ["HAWK", "DOV"]  # NEUT は kill_set 扱いで除外


def _expected_sha256() -> str:
    """Resolve expected SHA256, allowing env override for re-locked parquet.

    CR-02: hardcoded constant breaks if Phase 102 re-locks parquet (e.g.,
    upstream re-emit with same logical content but different bytes).
    Override pattern: ``V412_SOURCE_SHA256_OVERRIDE=<hex>`` in env to bypass
    the canonical lock without editing the source file. Default is the v4.12
    canonical Phase 102 lock; override is for documented re-lock scenarios
    only (must be paired with a SEAL re-stamp upstream).
    """
    override = os.environ.get(_SHA256_OVERRIDE_ENV, "").strip()
    return override if override else _EXPECTED_SOURCE_SHA256


def _verify_source_sha256(path: Path) -> str:
    """Phase 102 SHIPPED parquet の SHA256 を verify (lock 一致確認、T-103-04 mitigate)。

    CR-02: ``_expected_sha256()`` allows ``V412_SOURCE_SHA256_OVERRIDE`` env
    to override the hardcoded constant for documented re-lock scenarios.
    """
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = _expected_sha256()
    if actual != expected:
        raise RuntimeError(
            f"Phase 102 parquet SHA256 mismatch: expected {expected}, got {actual}"
        )
    return actual


def _select_kill_set_column(cells: pl.DataFrame) -> pl.Series:
    """kill_set boolean column を schema-tolerant に取得。

    - kill_set 列があればそれを使う
    - 無ければ pass_flag を inverse で kill_set proxy として使う (Phase 102 D-09 意味論)
    - どちらも無ければ全 False (= 全 active 扱い、保守的)
    """
    if "kill_set" in cells.columns:
        return cells["kill_set"]
    if "pass_flag" in cells.columns:
        # pass_flag == False == kill_set proxy
        return cells["pass_flag"].not_()
    # fallback: 全 False
    return pl.Series("kill_set", [False] * len(cells))


def _select_bucket_column_name(cells: pl.DataFrame) -> str | None:
    """vol_bucket / bucket 列名を解決 (schema fallback)。"""
    for candidate in ("vol_bucket", "bucket"):
        if candidate in cells.columns:
            return candidate
    return None


def emit_kill_switch_decision() -> dict:
    """Phase 102 parquet → kill_switch_fired determination."""
    source_sha256 = _verify_source_sha256(_CELLS_POST_FILTER)
    cells = pl.read_parquet(_CELLS_POST_FILTER)
    n_total = len(cells)

    kill_set_col = _select_kill_set_column(cells)
    cells_with_kill = cells.with_columns(kill_set_col.alias("__kill_set"))

    # Active = stance ∈ {HAWK, DOV} かつ __kill_set == False
    active = cells_with_kill.filter(
        (~pl.col("__kill_set")) & (pl.col("stance").is_in(_ACTIVE_STANCES))
    )
    n_active = len(active)
    n_kill_set = n_total - n_active

    # Compound strata aggregation
    bucket_col = _select_bucket_column_name(cells)
    schema_fallback = bucket_col is None

    if n_active == 0:
        # graceful degrade: no active rows → strata empty。
        # Phase 101 nyquist は「全 stratum で n_active < 20」相当で kill_switch_fired=True
        n_compound_strata = 0
        min_n_active = 0
        kill_switch_fired = True
    else:
        if not schema_fallback:
            per_stratum = active.group_by([bucket_col, "stance"]).agg(
                pl.len().alias("n_active")
            )
        else:
            per_stratum = active.group_by("stance").agg(pl.len().alias("n_active"))
        n_compound_strata = len(per_stratum)
        min_n_active = int(per_stratum["n_active"].min())
        kill_switch_fired = bool(min_n_active < _NYQUIST_THRESHOLD)

    doc = {
        "schema_version": _SCHEMA_VERSION,
        "kill_switch_fired": kill_switch_fired,
        "source": "cells_post_compound_filter.parquet",
        "source_sha256": source_sha256,
        "n_cells_total": n_total,
        "n_cells_kill_set": n_kill_set,
        "n_cells_active": n_active,
        "n_compound_strata": n_compound_strata,
        "min_n_active_per_stratum": min_n_active,
        "nyquist_threshold": _NYQUIST_THRESHOLD,
        "schema_fallback_no_vol_bucket": schema_fallback,
        "data_provenance": _DATA_PROVENANCE,
        "emitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return doc


def main() -> None:
    doc = emit_kill_switch_decision()
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    print(
        f"Wrote {_OUTPUT} "
        f"(kill_switch_fired={doc['kill_switch_fired']}, "
        f"n_active={doc['n_cells_active']}, "
        f"min_n_active_per_stratum={doc['min_n_active_per_stratum']})"
    )


if __name__ == "__main__":
    main()
