"""Phase 116 OOS/permutation/DSR verdict artifacts for v5.1.

The current Phase 115 handoff has no IS-eligible cells. In that state Phase 116
must emit an honest null verdict without fabricating a normal OOS candidate set.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import v5_1_is_backtest_fwer as phase115

PAIRS = phase115.PAIRS
REPORT_DIR = Path("reports/v5.1/phase116")
PHASE115_SUMMARY = Path("reports/v5.1/is_backtest_fwer_summary.json")
DOCS_VERDICT = Path("docs/v5.1_tick_imbalance_verdict.md")
CLAIM_DOC = phase115.CLAIM_DOC

OOS_START = "2025-11-01T00:00:00Z"
OOS_END_EXCLUSIVE = "2026-05-01T00:00:00Z"
OOS_END_DISPLAY = "2026-04-30"
OOS_PF_HURDLE = 1.5
PERMUTATION_B = 2000
PERMUTATION_SEED = 515113
SHUFFLE_UNIT = "stance_label"
DSR_N_TRIALS = 216
DSR_ALPHA = 0.05
DSR_PROBABILITY_THRESHOLD = 0.95


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.write_text(json.dumps(doc, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _sealed_constants(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "fwer_denominator": int(summary["fwer_denominator"]),
        "entry_granularity": str(summary["entry_granularity"]),
        "oos_start": OOS_START,
        "oos_end_exclusive": OOS_END_EXCLUSIVE,
        "oos_pf_hurdle": OOS_PF_HURDLE,
        "permutation_b": PERMUTATION_B,
        "permutation_seed": PERMUTATION_SEED,
        "permutation_shuffle_unit": SHUFFLE_UNIT,
        "dsr_n_trials": DSR_N_TRIALS,
        "dsr_alpha": DSR_ALPHA,
        "dsr_probability_threshold": DSR_PROBABILITY_THRESHOLD,
    }


def load_phase115_summary(path: Path = PHASE115_SUMMARY) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("phase") != 115:
        raise ValueError(f"invalid Phase 115 summary phase: {path}")
    if "phase115_blocked" not in doc:
        raise ValueError(f"missing Phase 115 blocker flag: {path}")
    if not isinstance(doc.get("eligible_cells"), list):
        raise ValueError(f"missing Phase 115 eligible_cells list: {path}")
    if int(doc.get("fwer_denominator", 0)) != phase115.FWER_DENOMINATOR:
        raise ValueError(f"unexpected Phase 115 fwer_denominator: {path}")
    if doc.get("entry_granularity") != phase115.ENTRY_GRANULARITY:
        raise ValueError(f"unexpected Phase 115 entry_granularity: {path}")
    pairs = doc.get("pairs")
    if not isinstance(pairs, dict):
        raise ValueError(f"missing Phase 115 pairs block: {path}")
    for pair in PAIRS:
        if pair not in pairs:
            raise ValueError(f"missing Phase 115 pair block: {pair}")
    return doc


def _empty_pair_block(pair: str, phase115_pair: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase115_cell_count": int(phase115_pair.get("cell_count", 0)),
        "phase115_eligible_cell_count": int(phase115_pair.get("eligible_cell_count", 0)),
        "phase115_sparse_fail_close_count": int(
            phase115_pair.get("sparse_fail_close_count", 0)
        ),
        "evaluated_cell_count": 0,
        "any_oos_pf_passed": False,
        "any_permutation_passed": False,
        "any_dsr_passed": False,
        "any_cell_all_phase116_gates_passed": False,
        "normal_oos_executed": False,
        "reason": "empty_phase116_candidate_set",
        "cells": [],
        "no_pooling": f"{pair} evaluated independently; no pair pooling applied.",
    }


def build_final_verdict(
    phase115_summary: dict[str, Any],
    phase115_summary_path: str,
) -> dict[str, Any]:
    phase115_blocked = bool(phase115_summary["phase115_blocked"])
    blocker_reason = phase115_summary.get("blocker_reason")
    eligible_cells = phase115_summary["eligible_cells"]
    if not isinstance(eligible_cells, list):
        raise ValueError("Phase 115 eligible_cells must be a list")
    if eligible_cells:
        raise NotImplementedError(
            "Phase 116 normal OOS candidate path requires a dedicated plan; "
            "this artifact writer is only for the empty-candidate handoff."
        )

    pairs = {
        pair: _empty_pair_block(pair, phase115_summary["pairs"][pair])
        for pair in PAIRS
    }
    if phase115_blocked:
        reason = f"phase115_blocked:{blocker_reason or 'unknown'}"
    else:
        reason = "empty_phase116_candidate_set"
    null_ship_reasons = [reason]

    return {
        "phase": 116,
        "schema_version": "v5.1.phase116.1",
        "ship_verdict": False,
        "verdict": "null_ship",
        "null_ship_reasons": null_ship_reasons,
        "phase115_blocked": phase115_blocked,
        "phase115_blocker_reason": blocker_reason,
        "phase115_summary_path": phase115_summary_path,
        "candidate_count": 0,
        "normal_oos_executed": False,
        "oos_window": {
            "start": OOS_START,
            "end_exclusive": OOS_END_EXCLUSIVE,
            "end_display": OOS_END_DISPLAY,
        },
        "pairs": pairs,
        "provenance": {
            "git_commit": _git_commit(),
            "claim_doc": str(CLAIM_DOC),
            "phase115_summary_path": phase115_summary_path,
            "sealed_constants": _sealed_constants(phase115_summary),
            "decision_rule": (
                "ship_verdict can be true only when at least one IS-eligible cell "
                "passes OOS PF, permutation null, and DSR gates. No cells were "
                "eligible, so Phase 116 emits null_ship without normal OOS execution."
            ),
        },
    }


def _permutation_doc(verdict: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": 116,
        "permutation_b": PERMUTATION_B,
        "permutation_seed": PERMUTATION_SEED,
        "shuffle_unit": SHUFFLE_UNIT,
        "strict_rule": "observed_net_pf > null_p95",
        "candidate_count": int(verdict["candidate_count"]),
        "normal_oos_executed": bool(verdict["normal_oos_executed"]),
        "reason": verdict["null_ship_reasons"][0],
        "pairs": {pair: [] for pair in PAIRS},
    }


def _dsr_doc(verdict: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": 116,
        "dsr_n_trials": DSR_N_TRIALS,
        "dsr_alpha": DSR_ALPHA,
        "dsr_probability_threshold": DSR_PROBABILITY_THRESHOLD,
        "candidate_count": int(verdict["candidate_count"]),
        "normal_oos_executed": bool(verdict["normal_oos_executed"]),
        "reason": verdict["null_ship_reasons"][0],
        "pairs": {pair: [] for pair in PAIRS},
    }


def _markdown_verdict(verdict: dict[str, Any]) -> str:
    lines = [
        "# v5.1 Tick Imbalance Verdict",
        "",
        f"ship_verdict | {verdict['ship_verdict']}",
        f"verdict | {verdict['verdict']}",
        f"candidate_count | {verdict['candidate_count']}",
        f"normal_oos_executed | {verdict['normal_oos_executed']}",
        "",
        "## Null-Ship Reasons",
        "",
    ]
    lines.extend(f"- `{reason}`" for reason in verdict["null_ship_reasons"])
    lines.extend(
        [
            "",
            "## Per-Pair Gates",
            "",
            "| Pair | Phase115 Cells | Phase115 Eligible | OOS Evaluated | OOS PF | Permutation | DSR |",
            "|------|----------------|-------------------|---------------|--------|-------------|-----|",
        ]
    )
    for pair in PAIRS:
        item = verdict["pairs"][pair]
        lines.append(
            "| {pair} | {cell_count} | {eligible} | {evaluated} | {oos} | {perm} | {dsr} |".format(
                pair=pair,
                cell_count=item["phase115_cell_count"],
                eligible=item["phase115_eligible_cell_count"],
                evaluated=item["evaluated_cell_count"],
                oos=item["any_oos_pf_passed"],
                perm=item["any_permutation_passed"],
                dsr=item["any_dsr_passed"],
            )
        )
    lines.extend(
        [
            "",
            "Canonical source: `reports/v5.1/phase116/final_verdict.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    verdict: dict[str, Any],
    output_dir: Path = REPORT_DIR,
    docs_path: Path | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "final_verdict.json", verdict)
    _write_json(output_dir / "permutation_null.json", _permutation_doc(verdict))
    _write_json(output_dir / "dsr_summary.json", _dsr_doc(verdict))

    markdown = _markdown_verdict(verdict)
    (output_dir / "final_verdict.md").write_text(markdown, encoding="utf-8")
    if docs_path is not None:
        target_docs = docs_path
    elif output_dir == REPORT_DIR:
        target_docs = DOCS_VERDICT
    else:
        target_docs = output_dir / DOCS_VERDICT.name
    target_docs.parent.mkdir(parents=True, exist_ok=True)
    target_docs.write_text(markdown, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase115-summary", type=Path, default=PHASE115_SUMMARY)
    parser.add_argument("--output-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args(argv)

    summary = load_phase115_summary(args.phase115_summary)
    verdict = build_final_verdict(summary, phase115_summary_path=str(args.phase115_summary))
    write_outputs(verdict, output_dir=args.output_dir)
    print(f"wrote Phase 116 artifacts to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
