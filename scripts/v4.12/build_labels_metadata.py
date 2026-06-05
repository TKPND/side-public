"""Build labels_metadata.json with dual sha256 pin + stratified split (Phase 101 Plan 02 Task 4).

D-67: 4 SEAL artifacts (Wave 2) will pin this sha256 — drift = SEAL violation.
CONTEXT L217: dual-pin reproducibility (csv + prompt).

NEUT=1 constraint: sklearn StratifiedShuffleSplit requires n_per_class >= 2.
Custom per-class shuffle + assign:
  HAWK (31): 23 train + 8 eval (seeded shuffle, 75/25 round)
  NEUT  (1): 1 train + 0 eval (singleton → train)
  DOV   (0): 0/0
  total:     24 train + 8 eval ✓ (matches PLAN n_train=24 / n_eval=8 target)

Idempotency: re-run is a NO-OP iff csv_sha256 + prompt_sha256 both match.
Drift detection: any sha mismatch → exit 1 with explicit "DRIFT DETECTED" message.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[2]
LABELS_CSV = REPO_ROOT / "data" / "v4.12" / "labels" / "macro_stance_labels.csv"
PROMPT_MD = REPO_ROOT / "data" / "v4.12" / "labels" / "labels_prompt.md"
METADATA = REPO_ROOT / "data" / "v4.12" / "labels" / "labels_metadata.json"

SEED = 20260426


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stratified_split(labels: list[str], seed: int) -> tuple[list[int], list[int]]:
    """Per-class shuffle + 75/25 split. Singletons (class with <4 samples) → train.

    Returns (sorted train indices, sorted eval indices). Stable across runs given seed.
    """
    rng = np.random.default_rng(seed)
    by_class: dict[str, list[int]] = {}
    for i, lbl in enumerate(labels):
        by_class.setdefault(lbl, []).append(i)

    train: list[int] = []
    eval_: list[int] = []
    for cls in sorted(by_class.keys()):
        idxs = np.array(by_class[cls], dtype=int)
        rng.shuffle(idxs)
        n = len(idxs)
        n_eval = 0 if n < 4 else round(n * 0.25)
        eval_.extend(idxs[:n_eval].tolist())
        train.extend(idxs[n_eval:].tolist())
    return sorted(train), sorted(eval_)


def main() -> int:
    if not LABELS_CSV.exists() or not PROMPT_MD.exists():
        sys.stderr.write(
            f"[error] source missing: {LABELS_CSV.exists()=} {PROMPT_MD.exists()=}\n"
        )
        return 2

    csv_sha = sha256_file(LABELS_CSV)
    prompt_sha = sha256_file(PROMPT_MD)

    if METADATA.exists():
        try:
            existing = json.loads(METADATA.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = None
        if (
            isinstance(existing, dict)
            and existing.get("csv_sha256") == csv_sha
            and existing.get("prompt_sha256") == prompt_sha
        ):
            sys.stderr.write(f"[skip] {METADATA.name} drift-free (both sha256 match)\n")
            return 0
        if isinstance(existing, dict):
            sys.stderr.write(
                "[error] DRIFT DETECTED — existing metadata sha256 differs from source files.\n"
                f"  csv:    existing={(existing.get('csv_sha256') or '')[:16]}... new={csv_sha[:16]}...\n"
                f"  prompt: existing={(existing.get('prompt_sha256') or '')[:16]}... new={prompt_sha[:16]}...\n"
                f"Restore source files or delete {METADATA} to accept new freeze explicitly.\n"
            )
            return 1

    df = pl.read_csv(LABELS_CSV)
    n_total = len(df)
    labels = df["true_stance"].to_list()
    central_banks = df["central_bank"].to_list()
    provenances = df["label_provenance"].to_list()

    label_dist = {"HAWK": 0, "DOV": 0, "NEUT": 0}
    for l in labels:
        label_dist[l] = label_dist.get(l, 0) + 1

    cb_dist: dict[str, int] = {}
    for cb in central_banks:
        cb_dist[cb] = cb_dist.get(cb, 0) + 1

    prov_dist = {
        "frozen-llm-once-prosusai-finbert": 0,
        "frozen-llm-once-roberta-base": 0,
        "frozen-llm-once-claude-opus-4-7": 0,
    }
    for p in provenances:
        prov_dist[p] = prov_dist.get(p, 0) + 1

    train_idx, eval_idx = stratified_split(labels, SEED)

    metadata = {
        "csv_path": "data/v4.12/labels/macro_stance_labels.csv",
        "csv_sha256": csv_sha,
        "prompt_path": "data/v4.12/labels/labels_prompt.md",
        "prompt_sha256": prompt_sha,
        "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "period": "2022-01-01 .. 2023-12-31",
        "n_events_total": n_total,
        "label_distribution": label_dist,
        "central_bank_distribution": cb_dist,
        "label_provenance_distribution": prov_dist,
        "labeler_model": {
            "name": "claude-opus-4-7",
            "model_version": "claude-opus-4-7",
            "temperature_policy": (
                "claude -p one-shot, no retries; CLI temperature flag unsupported "
                "— determinism enforced by prompt sha256 + single-shot"
            ),
        },
        "train_eval_split": {
            "method": "per_class_shuffle_75_25",
            "method_note": (
                "sklearn StratifiedShuffleSplit fails when any class has <2 instances "
                "(NEUT=1). Custom per-class numpy shuffle assigns singleton classes to "
                "train; HAWK 23/8 stratified."
            ),
            "seed": SEED,
            "n_train": len(train_idx),
            "n_eval": len(eval_idx),
            "train_indices": train_idx,
            "eval_indices": eval_idx,
        },
        "annotator": (
            "claude-opus-4-7 (LLM) + takahiro (spot-check 10 events, 2026-04-26: "
            "8/10 confident + 2/10 borderline-but-rule-3-defendable, gate ≥80% met)"
        ),
        "label_taxonomy": {
            "HAWK": "tighter policy stance",
            "DOV": "looser policy stance",
            "NEUT": "no clear stance shift",
        },
        "constraints": {
            "DOV_count_zero_reason": (
                "2022-2023 was a global hiking cycle (FOMC: 11 hikes / 5 holds / 0 cuts; "
                "ECB: 10 hikes / 4 holds / 0 cuts). Ground truth reflects period reality, "
                "not labeling failure. DOV F1 will be undefined (zero support)."
            ),
            "NEUT_count_one_implication": (
                "RoBERTa eval set has zero NEUT support (singleton in train). Macro F1 "
                "will use sklearn zero_division behavior for the missing eval class. "
                "HAWK is the dominant evaluation class."
            ),
        },
    }

    METADATA.write_text(
        json.dumps(metadata, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    sys.stderr.write(
        f"[done] wrote {METADATA}\n"
        f"  csv_sha256:    {csv_sha}\n"
        f"  prompt_sha256: {prompt_sha}\n"
        f"  n_train={len(train_idx)} n_eval={len(eval_idx)} (seed={SEED})\n"
        f"  label_dist: {label_dist}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
