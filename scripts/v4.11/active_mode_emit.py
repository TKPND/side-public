"""Phase 94 FILT-03 — active-mode orchestrator.

Runs vol_regime_filter in ACTIVE mode (SEAL allowed_buckets=["HIGH"]),
emits:
  - data/v4.11/cells_post_filter.parquet
  - reports/v4.11/active_mode/filter_eval.json

Consumes Phase 93 kill-switch signal as audit trail
(filter_eval.json.kill_switch_consumed).

D-40: active-mode ship_decision is Phase 95 SHIP-03; NOT emitted here.
D-17: SEAL untouched.
D-35: flat import (scripts/v4.11 cannot be a Python package due to dot in name).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any

import polars as pl

# D-35 flat import.
from seal_drift_check import verify_seal_or_raise  # type: ignore[import-not-found]
import vol_regime_filter  # type: ignore[import-not-found]

verify_seal_or_raise()  # import-time fail-close.

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEFAULT_CELLS_OUT = _REPO_ROOT / "data" / "v4.11" / "cells_post_filter.parquet"
_DEFAULT_EVAL_OUT = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "filter_eval.json"
)
_DEFAULT_VOL = _REPO_ROOT / "data" / "v4.11" / "vol_per_slot.parquet"
_DEFAULT_LABELS = _REPO_ROOT / "data" / "slot_labels.parquet"
_PHASE93_VALIDATION = (
    _REPO_ROOT
    / ".planning"
    / "phases"
    / "93-vol-precompute-classifier-nyquist-audit"
    / "93-VALIDATION.md"
)
# D-40 hard prohibition: Phase 94 MUST NOT emit this.
_FORBIDDEN_SHIP_DECISION = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "v4_11_ship_decision.json"
)


def _read_phase93_kill_switch(
    validation_path: pathlib.Path = _PHASE93_VALIDATION,
) -> dict[str, Any]:
    """Extract NYQUIST_AUDIT_JSON block from 93-VALIDATION.md.

    Supports both plain marker and HTML comment marker formats:
      <!-- NYQUIST_AUDIT_JSON_BEGIN --> ... <!-- NYQUIST_AUDIT_JSON_END -->
      NYQUIST_AUDIT_JSON_BEGIN ... NYQUIST_AUDIT_JSON_END

    Returns the parsed JSON dict. Raises FileNotFoundError / ValueError on bad state.
    """
    if not validation_path.exists():
        raise FileNotFoundError(f"93-VALIDATION.md missing: {validation_path}")
    text = validation_path.read_text()
    # Match: NYQUIST_AUDIT_JSON_BEGIN (possibly in HTML comment) then optional ```json
    # then the JSON object, then NYQUIST_AUDIT_JSON_END (possibly in HTML comment).
    m = re.search(
        r"NYQUIST_AUDIT_JSON_BEGIN\s*(?:-->)?\s*(?:```json)?\s*(\{.*?\})\s*(?:```)?\s*(?:<!--\s*)?NYQUIST_AUDIT_JSON_END",
        text,
        re.DOTALL,
    )
    if not m:
        raise ValueError(
            "NYQUIST_AUDIT_JSON_BEGIN/END markers not found in 93-VALIDATION.md",
        )
    return json.loads(m.group(1))


def emit_active_mode(
    *,
    vol_parquet: pathlib.Path = _DEFAULT_VOL,
    slot_labels: pathlib.Path = _DEFAULT_LABELS,
    cells_out: pathlib.Path = _DEFAULT_CELLS_OUT,
    eval_out: pathlib.Path = _DEFAULT_EVAL_OUT,
    phase93_validation: pathlib.Path = _PHASE93_VALIDATION,
) -> tuple[pathlib.Path, pathlib.Path]:
    """Run active-mode filter + emit artifacts.

    Returns (cells_out_path, eval_out_path).

    Real-data deviation (D-35 precedent):
        Real slot_labels.parquet is a structural grid TEMPLATE (192 cells) with no
        event_ts column — same gap as Phase 93 run_phase93_smoke.py:220. When
        event_ts is absent, all cells are emitted as bucket=VOL_NA / pass_flag=False
        and a real_data_deviation block is included in filter_eval.json. This satisfies
        the SC#4 "audited-deviation" branch (pass_count != m_prime AND
        kill_switch_consumed=true AND phase93_kill_switch_fired=true).
    """
    if _FORBIDDEN_SHIP_DECISION.exists():
        raise RuntimeError(
            f"D-40 violation: {_FORBIDDEN_SHIP_DECISION} already exists. "
            "Active-mode ship_decision is Phase 95 SHIP-03 scope. Remove and retry.",
        )

    vol_df = pl.read_parquet(vol_parquet)
    labels = pl.read_parquet(slot_labels)

    kill_switch_block = _read_phase93_kill_switch(phase93_validation)
    kill_switch_consumed = bool(kill_switch_block.get("kill_switch_fired", False))

    required_cols = {"cell_id", "pair", "event_ts"}
    has_event_ts = required_cols.issubset(set(labels.columns))

    real_data_deviation: dict[str, Any] | None = None

    if not has_event_ts:
        # Phase 93 precedent (run_phase93_smoke.py:220): slot_labels is a structural
        # grid template without event_ts timestamps. Cannot perform vol-regime JOIN.
        # Emit all cells as VOL_NA / pass_flag=False as audited deviation.
        print(
            "[active_mode_emit] WARNING: slot_labels lacks event_ts column "
            "(Phase 93 precedent: grid template, not cell instances). "
            "Emitting all cells as VOL_NA / pass_flag=False (audited deviation)."
        )
        # Ensure cell_id column exists (grid template should have it from Plan 01).
        if "cell_id" not in labels.columns:
            # Synthesize cell_id if truly absent (fail-safe; should not happen).
            labels = labels.with_row_index("cell_id").with_columns(
                pl.col("cell_id").cast(pl.Utf8)
            )
        result = labels.select(["cell_id"]).with_columns(
            pl.lit(False).alias("pass_flag"),
            pl.lit("VOL_NA").alias("bucket"),
        )
        real_data_deviation = {
            "reason": "slot_labels lacks event_ts column — structural grid template, not cell instances with timestamps",
            "behavior": "emitted all cells with bucket=VOL_NA, pass_flag=False",
            "precedent": "Phase 93 run_phase93_smoke.py:220 — identical gap, same deviation documented",
        }
    else:
        result = vol_regime_filter.filter_cells(
            labels.select(["cell_id", "pair", "event_ts"]),
            vol_df,
            neutral_mode=False,
        )

    cells_out.parent.mkdir(parents=True, exist_ok=True)
    result.write_parquet(cells_out)

    # Build filter_eval.json.
    bucket_dist: dict[str, int] = {}
    for row in result.group_by("bucket").agg(pl.len().alias("n")).iter_rows(named=True):
        bucket_dist[str(row["bucket"])] = int(row["n"])

    pass_count = int(result.filter(pl.col("pass_flag") == True).height)  # noqa: E712

    payload: dict[str, Any] = {
        "_comment": (
            "Phase 94 FILT-03 active-mode filter_eval. "
            "D-40: ship_decision is Phase 95 SHIP-03."
        ),
        "data_provenance": "gate-redesign-v410-a5f7183",
        "post_filter_cell_count": pass_count,
        "total_cell_count": int(result.height),
        "bucket_distribution": bucket_dist,
        "kill_switch_consumed": kill_switch_consumed,
        "kill_switch_source": {
            "phase93_kill_switch_fired": bool(
                kill_switch_block.get("kill_switch_fired")
            ),
            "phase93_kill_switch_reason": kill_switch_block.get(
                "kill_switch_reason", ""
            ),
            "phase93_per_bucket_summary_keys": sorted(
                list((kill_switch_block.get("per_bucket") or {}).keys()),
            ),
        },
        "data_window": "2024-Q1 (2024-01-14..2024-04-29)",
    }
    if real_data_deviation is not None:
        payload["real_data_deviation"] = real_data_deviation

    eval_out.parent.mkdir(parents=True, exist_ok=True)
    eval_out.write_text(json.dumps(payload, indent=2) + "\n")

    return cells_out, eval_out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 94 FILT-03 active-mode emit. Run from repo root.",
    )
    parser.parse_args()
    cells_path, eval_path = emit_active_mode()
    print(f"[active_mode_emit] wrote {cells_path}")
    print(f"[active_mode_emit] wrote {eval_path}")


if __name__ == "__main__":
    main()
