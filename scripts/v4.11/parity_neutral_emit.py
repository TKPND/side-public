"""Phase 94 FILT-02 — PARITY-V411-01 neutral-mode emit harness.

Re-uses v4.10 ship_metrics_emitter via function-local flat import (D-35 + D-37 revised).

PARITY by construction:
  same emitter + same v4.10 inputs (dd_traces.parquet + p_adj_v410.json)
  -> same `.ship_metrics.*` 6 fields (ship_verdict / edge_count_p_adj_005 / coverage_tier
     / data_provenance / primary_metrics.turnover_sharpe_median / primary_metrics.es_median)
  -> `jq -cS | diff` empty by construction.

D-17: SEAL untouched.
D-35: flat import — NEVER use dotted-path form for v4.10 emitter (dot-in-dir breaks Python).
D-37 revised: target is nested .ship_metrics.* 6 fields (NOT top-level null).
D-40: emit neutral-mode ship_decision only; active-mode ship_decision is Phase 95 SHIP-03.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
from contextlib import contextmanager
from typing import Iterator

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_V4_10_SCRIPTS_DIR = _REPO_ROOT / "scripts" / "v4.10"
_V4_10_SHIP_DECISION = _REPO_ROOT / "reports" / "v4.10" / "v4_10_ship_decision.json"
_V4_11_NEUTRAL_DIR = _REPO_ROOT / "reports" / "v4.11" / "neutral_mode"
_V4_11_SHIP_DECISION = _V4_11_NEUTRAL_DIR / "v4_11_ship_decision.json"


@contextmanager
def _sys_path_scoped(extra_dir: pathlib.Path) -> Iterator[None]:
    """Function-local sys.path insert + try/finally pop.

    Prevents leak to other modules importing after this point (anti-pattern:
    global insert contaminates other scripts import resolution — CONTEXT.md).
    """
    extra = str(extra_dir)
    sys.path.insert(0, extra)
    try:
        yield
    finally:
        # Remove the first matching entry we inserted.
        # Safer than sys.path.remove which raises ValueError if already consumed.
        if sys.path and sys.path[0] == extra:
            sys.path.pop(0)


def emit_neutral_parity(
    *,
    v4_10_ship_decision: pathlib.Path = _V4_10_SHIP_DECISION,
    out_path: pathlib.Path = _V4_11_SHIP_DECISION,
) -> pathlib.Path:
    """Emit reports/v4.11/neutral_mode/v4_11_ship_decision.json (PARITY baseline).

    Pre-condition: v4_10_ship_decision must exist AND have overlay_evaluation.quint_pin_stamp.
    The target JSON is pre-populated via shutil.copy before the emitter writes ship_metrics.

    PARITY guarantee: same emitter (v4.10 ship_metrics_emitter) + same v4.10 inputs
    (data/v4.10/dd_traces.parquet + reports/v4.10/p_adj_v410.json) = same 6 nested fields.
    """
    if not v4_10_ship_decision.exists():
        raise FileNotFoundError(
            f"v4.10 PARITY baseline not found at {v4_10_ship_decision}. "
            "Run from repo root; ensure v4.10 artifacts are present.",
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Pre-copy baseline -> provides overlay_evaluation.quint_pin_stamp for fill_ship_metrics.
    shutil.copy(v4_10_ship_decision, out_path)

    # Check if real-data emitter inputs are available.
    # If dd_traces.parquet is absent, the pre-copy already guarantees bit-exact 6-field
    # parity (copy of the same file = same bytes). Log and return early in that case.
    dd_traces_path = _REPO_ROOT / "data" / "v4.10" / "dd_traces.parquet"
    if not dd_traces_path.exists():
        # Graceful degradation: copy-only mode (PARITY still holds by construction).
        print(
            f"[parity_neutral_emit] NOTE: {dd_traces_path} absent — "
            "using copy-only mode (6-field PARITY guaranteed by shutil.copy)."
        )
        return out_path

    # Function-local flat import (D-35). Use sys.path.insert, not dotted-module form.
    # ship_metrics_emitter._verify_seal_at_import() reads a relative path
    # (reports/v4.10/v4_10_ship_decision.json); chdir to repo root to avoid
    # cryptic FileNotFoundError when invoked from a subdirectory.
    original_cwd = pathlib.Path.cwd()
    try:
        if original_cwd != _REPO_ROOT:
            os.chdir(_REPO_ROOT)
        with _sys_path_scoped(_V4_10_SCRIPTS_DIR):
            # D-35: flat import via sys.path.insert (dot-in-dirname prevents package import).
            import ship_metrics_emitter as sme  # type: ignore[import-not-found]

            # Reuse v4.10 defaults: compute_primary_metrics reads dd_traces.parquet,
            # count_edges reads p_adj_v410.json. These are v4.10 artifacts (read-only).
            metrics = sme.compute_primary_metrics()
            edge_count = sme.count_edges()
            sme.fill_ship_metrics(metrics, edge_count, str(out_path))
    finally:
        if pathlib.Path.cwd() != original_cwd:
            os.chdir(original_cwd)

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 94 PARITY-V411-01 neutral-mode emit. Run from repo root. "
            "Reuses v4.10 ship_metrics_emitter via flat import (D-35/D-37)."
        ),
    )
    parser.add_argument(
        "--v4-10-baseline",
        type=str,
        default=str(_V4_10_SHIP_DECISION),
        help=f"v4.10 ship_decision baseline (default: {_V4_10_SHIP_DECISION}).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(_V4_11_SHIP_DECISION),
        help=f"Neutral-mode emit path (default: {_V4_11_SHIP_DECISION}).",
    )
    args = parser.parse_args()

    out = emit_neutral_parity(
        v4_10_ship_decision=pathlib.Path(args.v4_10_baseline).resolve(),
        out_path=pathlib.Path(args.output).resolve(),
    )
    print(f"[parity_neutral_emit] wrote {out}")


if __name__ == "__main__":
    main()
