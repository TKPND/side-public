"""nyquist_audit_v412.py — Phase 101 Wave 2 Plan 101-05 Task 2.

D-72 compound-cell n-per-cell audit. Joins macro_stance_per_event.parquet
with vol_per_slot.parquet on (event_ts → bar_time, pair), groups by
(vol_bucket × stance), enforces n_min ≥ 20 per cell, and writes
reports/v4.12/nyquist_audit_v412.json.

Expected outcome on Phase 101 Wave 2 dataset (16 rows, 4 Q1 events × 4 pairs):
    kill_switch_fired = true
This is BY DESIGN — the safety system proves itself before Phase 102
collects more events to clear the threshold.

CLI:
    uv run python scripts/v4.12/nyquist_audit_v412.py [--check]
    --check : exit 0 even when kill_switch fires (audit-mode default; the
              kill_switch state is recorded in JSON but does not exit !=0).

Citations:
    CONTEXT.md L98-110 — D-72 nyquist audit schema (n_min=20 threshold,
        kill_switch_fired bool, *_sha256 pins)
    101-05-PLAN.md Task 2 — driver spec
    scripts/v4.12/n_per_cell_audit.py — Phase 100 D-C1 analog (polars + JSON)
    CLASS-V412-03 — kill_switch enforcement
    T-101-04b — kill_switch suppression mitigated by GREEN test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

import polars as pl

_HERE = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]

# CONTEXT D-72 — n_min threshold per compound cell.
N_MIN_THRESHOLD: int = 20

# vol_per_slot path search (PLAN: try v4.12 first, fall back to v4.11).
VOL_PER_SLOT_CANDIDATES: tuple[pathlib.Path, ...] = (
    _REPO_ROOT / "data" / "v4.12" / "vol_per_slot.parquet",
    _REPO_ROOT / "data" / "v4.11" / "vol_per_slot.parquet",
)


def _resolve_vol_per_slot(explicit: pathlib.Path | None) -> pathlib.Path:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(f"vol_per_slot not found: {explicit}")
        return explicit
    for candidate in VOL_PER_SLOT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"vol_per_slot.parquet missing — searched {VOL_PER_SLOT_CANDIDATES}"
    )


def _sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def compute_compound_cells(
    stance_df: pl.DataFrame, vol_df: pl.DataFrame
) -> pl.DataFrame:
    """Join D-71 stance ⨝ vol_per_slot, group by (vol_bucket × stance), count.

    vol_per_slot.bar_time is naive UTC; D-71 event_ts is tz-aware UTC. Cast
    the latter to naive before joining (alias to bar_time for clarity).
    Returns columns [vol_bucket, stance, n, sufficient].
    """
    if "bar_time" not in vol_df.columns:
        raise ValueError("vol_per_slot missing bar_time column")
    if "bucket" not in vol_df.columns:
        raise ValueError("vol_per_slot missing bucket column")

    left = stance_df.with_columns(
        pl.col("event_ts").dt.replace_time_zone(None).alias("bar_time")
    ).select(["bar_time", "pair", "stance"])

    joined = left.join(
        vol_df.select(["bar_time", "pair", "bucket"]),
        on=["bar_time", "pair"],
        how="inner",
    )

    cells = (
        joined.group_by(["bucket", "stance"])
        .agg(pl.len().alias("n"))
        .rename({"bucket": "vol_bucket"})
        .with_columns((pl.col("n") >= N_MIN_THRESHOLD).alias("sufficient"))
        .sort(["vol_bucket", "stance"])
    )
    return cells


def build_audit_report(
    stance_df: pl.DataFrame,
    vol_df: pl.DataFrame,
    *,
    macro_classifier_spec_path: pathlib.Path,
    labels_metadata_path: pathlib.Path,
    vol_per_slot_path: pathlib.Path,
) -> dict[str, object]:
    """Produce D-72 audit dict (ready to JSON-serialize)."""
    cells = compute_compound_cells(stance_df, vol_df)

    compound_cells: list[dict[str, object]] = [
        {
            "vol_bucket": row["vol_bucket"],
            "stance": row["stance"],
            "n": int(row["n"]),
            "sufficient": bool(row["sufficient"]),
        }
        for row in cells.to_dicts()
    ]

    n_cells_total = len(compound_cells)
    n_cells_sufficient = sum(1 for c in compound_cells if c["sufficient"])
    n_cells_insufficient = n_cells_total - n_cells_sufficient
    kill_switch_fired = n_cells_insufficient > 0 or n_cells_total == 0

    if n_cells_total == 0:
        reason = (
            "0 compound cells produced by JOIN — no D-71 rows overlap "
            "vol_per_slot. kill_switch fires (Phase 102 will fix)."
        )
    elif kill_switch_fired:
        reason = (
            f"{n_cells_insufficient}/{n_cells_total} compound cells fall "
            f"below n_min={N_MIN_THRESHOLD} (Phase 101 wave-2 sample of "
            f"{stance_df.height} stance rows is insufficient — Phase 102 "
            f"will collect more events)."
        )
    else:
        reason = (
            f"all {n_cells_total} compound cells meet n_min="
            f"{N_MIN_THRESHOLD} threshold."
        )

    return {
        "audit_at": datetime.now(tz=timezone.utc).isoformat(),
        "n_min_threshold": N_MIN_THRESHOLD,
        "compound_cells": compound_cells,
        "n_total_events": int(stance_df.height),
        "n_cells_total": n_cells_total,
        "n_cells_sufficient": n_cells_sufficient,
        "n_cells_insufficient": n_cells_insufficient,
        "kill_switch_fired": kill_switch_fired,
        "kill_switch_reason": reason,
        "macro_classifier_spec_sha256": _sha256_of(macro_classifier_spec_path),
        "labels_metadata_sha256": _sha256_of(labels_metadata_path),
        "vol_per_slot_path": str(vol_per_slot_path),
    }


def _run(
    *,
    parquet_path: pathlib.Path,
    spec_path: pathlib.Path,
    labels_metadata_path: pathlib.Path,
    vol_per_slot_path: pathlib.Path | None,
    output_path: pathlib.Path,
) -> dict[str, object]:
    if not parquet_path.exists():
        raise FileNotFoundError(f"D-71 parquet missing: {parquet_path}")
    if not spec_path.exists():
        raise FileNotFoundError(f"macro_classifier_spec missing: {spec_path}")
    if not labels_metadata_path.exists():
        raise FileNotFoundError(f"labels_metadata missing: {labels_metadata_path}")

    resolved_vol = _resolve_vol_per_slot(vol_per_slot_path)
    stance_df = pl.read_parquet(parquet_path)
    vol_df = pl.read_parquet(resolved_vol)

    report = build_audit_report(
        stance_df,
        vol_df,
        macro_classifier_spec_path=spec_path,
        labels_metadata_path=labels_metadata_path,
        vol_per_slot_path=resolved_vol,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="D-72 nyquist audit (kill_switch on n_min<20)"
    )
    parser.add_argument(
        "--parquet",
        type=pathlib.Path,
        default=_REPO_ROOT / "data" / "v4.12" / "macro_stance_per_event.parquet",
    )
    parser.add_argument(
        "--spec",
        type=pathlib.Path,
        default=_REPO_ROOT / "scripts" / "v4.12" / "macro_classifier_spec.json",
    )
    parser.add_argument(
        "--labels-metadata",
        type=pathlib.Path,
        default=_REPO_ROOT / "data" / "v4.12" / "labels" / "labels_metadata.json",
    )
    parser.add_argument(
        "--vol-per-slot",
        type=pathlib.Path,
        default=None,
        help="explicit vol_per_slot.parquet path (default: search v4.12 → v4.11)",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=_REPO_ROOT / "reports" / "v4.12" / "nyquist_audit_v412.json",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="audit-mode default (exit 0 even when kill_switch fires)",
    )
    ns = parser.parse_args(argv)

    report = _run(
        parquet_path=ns.parquet,
        spec_path=ns.spec,
        labels_metadata_path=ns.labels_metadata,
        vol_per_slot_path=ns.vol_per_slot,
        output_path=ns.output,
    )
    print(
        f"kill_switch={report['kill_switch_fired']} | "
        f"n_cells={report['n_cells_total']} | "
        f"sufficient={report['n_cells_sufficient']} | "
        f"wrote {ns.output}"
    )
    # --check is audit-mode: exit 0 regardless. Without --check, fail fast on
    # kill_switch so wrappers can chain. Plan 101-06 SEAL pin uses sha256 of
    # this JSON so the kill_switch state is itself part of the seal.
    if not ns.check and report["kill_switch_fired"]:
        sys.exit(3)


if __name__ == "__main__":
    main()
