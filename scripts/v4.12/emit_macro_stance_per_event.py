"""emit_macro_stance_per_event.py — Phase 101 Wave 2 Task 1.

Emit `data/v4.12/macro_stance_per_event.parquet` with the D-71 7-column schema
by running the SEAL-locked classifier (per macro_classifier_spec.json) over
the 4 Q1-2024 freeze-time statements and cross-joining with the 4 CONTEXT pairs.

Citations:
    CONTEXT.md L87-96 — D-71 schema (7 cols, kill_set bool, model_version short8)
    CONTEXT.md L98-102 — D-72 label_provenance enum
    CONTEXT.md L90 — pairs (USDJPY/EURUSD/AUDUSD/EURJPY)
    CLASS-V412-02 — D-71 schema enforcement
    101-05-PLAN.md — Wave 2 Task 1 spec

Determinism:
    - selected_classifier + commit_sha pinned via macro_classifier_spec.json
    - macro_stance_estimator.predict greedy argmax + pinned HF revision
    - 4 events × 4 pairs = 16 rows (CONTEXT-authoritative cardinality)

Gate constraints (grep_gates_v412.sh):
    - imports only transformers/torch/polars (via macro_stance_estimator) + stdlib
    - no HTTP-client imports (gate #14)
    - no remote-LLM SDK imports (gate #15)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Ensure scripts/v4.12 on sys.path so `import macro_stance_estimator` works
# regardless of cwd (pytest collects from repo root).
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import macro_stance_estimator as mse  # noqa: E402

_REPO_ROOT = _HERE.parents[1]

# CONTEXT.md L90 — frozen pair set (cross-product target).
PAIRS: tuple[str, ...] = ("USDJPY", "EURUSD", "AUDUSD", "EURJPY")

# CONTEXT.md L121 / 101-05-PLAN.md L14 — 2024-Q1 4 events: FOMC×2 + ECB×2.
# 16 rows (4 events × 4 pairs) is BY DESIGN: with n=16 the D-72 nyquist
# n_min≥20 gate fires kill_switch_fired=true, demonstrating the safety system.
Q1_2024_EVENT_DATES: tuple[str, ...] = (
    "2024-01-25",  # ECB
    "2024-01-31",  # FOMC
    "2024-03-07",  # ECB
    "2024-03-20",  # FOMC
)

# CONTEXT D-72 enum mapping: classifier_id -> label_provenance string.
LABEL_PROVENANCE_BY_CLASSIFIER: dict[str, str] = {
    "finbert": "frozen-llm-once-prosusai-finbert",
    "roberta": "frozen-llm-once-roberta-base",
}


def _load_spec(spec_path: pathlib.Path) -> dict[str, object]:
    """Load macro_classifier_spec.json (Wave 1 output)."""
    return json.loads(spec_path.read_text())


def _select_classifier_pin(spec: dict[str, object]) -> tuple[str, str]:
    """Return (selected_classifier, commit_sha) from spec.json."""
    selected = str(spec["selected_classifier"])
    if selected not in LABEL_PROVENANCE_BY_CLASSIFIER:
        raise ValueError(
            f"unknown selected_classifier {selected!r}; expected one of "
            f"{sorted(LABEL_PROVENANCE_BY_CLASSIFIER.keys())}"
        )
    block = spec[selected]
    if not isinstance(block, dict) or "commit_sha" not in block:
        raise ValueError(f"spec.{selected}.commit_sha missing")
    return selected, str(block["commit_sha"])


def _emit(
    csv_path: pathlib.Path,
    spec_path: pathlib.Path,
    output_path: pathlib.Path,
) -> pathlib.Path:
    """Emit D-71 parquet. Returns output_path."""
    import polars as pl

    spec = _load_spec(spec_path)
    selected, commit_sha = _select_classifier_pin(spec)
    label_provenance = LABEL_PROVENANCE_BY_CLASSIFIER[selected]
    model_version = commit_sha[:8]  # CONTEXT D-71: short8 of HF commit_sha

    # Load freeze-time fetched statements (Plan 101-02 Task 1 output).
    src = pl.read_csv(csv_path)
    required_cols = {"event_ts", "central_bank", "statement_text"}
    missing = required_cols - set(src.columns)
    if missing:
        raise ValueError(f"input CSV missing columns: {sorted(missing)}")

    # event_ts in CSV is ISO-8601 string with explicit +00:00; cast to UTC.
    src = src.with_columns(
        pl.col("event_ts")
        .str.to_datetime(time_unit="ns", time_zone="UTC")
        .alias("event_ts")
    )

    # CONTEXT L121 / PLAN L14 — filter to 2024-Q1 (4 events).
    # The labels CSV holds the full 2024 set; D-71 emit is Q1-only by design
    # so n=16 forces D-72 nyquist kill_switch (101-06 expected outcome).
    src = src.filter(
        pl.col("event_ts").dt.date().cast(pl.Utf8).is_in(list(Q1_2024_EVENT_DATES))
    )
    if src.height != 4:
        raise RuntimeError(
            f"Q1 filter expected 4 events, got {src.height}. "
            f"Check macro_stance_inference_2024.csv has rows for "
            f"{list(Q1_2024_EVENT_DATES)}"
        )

    # Predict stance per event (one inference per unique statement).
    statements: list[str] = src.get_column("statement_text").to_list()
    stances: list[str] = mse.predict(statements, classifier=selected)
    if len(stances) != src.height:
        raise RuntimeError(
            f"predict returned {len(stances)} stances for {src.height} rows"
        )

    events = src.with_columns(pl.Series("stance", stances)).select(
        ["event_ts", "central_bank", "stance"]
    )

    # Cross-product 4 events × 4 pairs = 16 rows (CONTEXT-authoritative).
    pairs_df = pl.DataFrame({"pair": list(PAIRS)})
    cross = events.join(pairs_df, how="cross")

    # D-71 7-column schema (CONTEXT L87-96).
    out = cross.with_columns(
        (pl.col("stance") == "NEUT").alias("kill_set"),
        pl.lit(label_provenance, dtype=pl.Utf8).alias("label_provenance"),
        pl.lit(model_version, dtype=pl.Utf8).alias("model_version"),
    ).select(
        [
            "event_ts",
            "pair",
            "central_bank",
            "stance",
            "kill_set",
            "label_provenance",
            "model_version",
        ]
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(output_path, compression="zstd")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Emit D-71 macro_stance_per_event.parquet (Phase 101 Wave 2 Task 1)"
    )
    parser.add_argument(
        "--input",
        type=pathlib.Path,
        default=_REPO_ROOT
        / "data"
        / "v4.12"
        / "labels"
        / "macro_stance_inference_2024.csv",
        help="freeze-time fetched statements CSV (Plan 101-02 Task 1 output)",
    )
    parser.add_argument(
        "--spec",
        type=pathlib.Path,
        default=_REPO_ROOT / "scripts" / "v4.12" / "macro_classifier_spec.json",
        help="macro_classifier_spec.json (Wave 1 output)",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=_REPO_ROOT / "data" / "v4.12" / "macro_stance_per_event.parquet",
        help="D-71 parquet output path",
    )
    ns = parser.parse_args()
    out = _emit(ns.input, ns.spec, ns.output)
    print(f"[emit] wrote {out}")


if __name__ == "__main__":
    main()
