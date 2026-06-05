"""Drift-detection tests for labels_metadata.json (Phase 101 Plan 02 Task 4).

PLAN lines 220-224:
  T1: csv sha256 matches metadata.csv_sha256
  T2: prompt sha256 matches metadata.prompt_sha256
  T3: label_distribution counts match actual CSV per true_stance
  T4: train_indices ∩ eval_indices == ∅
  T5: n_train + n_eval == n_events_total

D-67: 4 SEAL artifacts pin labels_metadata.json sha256 — drift = SEAL violation.
These tests are the local trip-wire that fires before SEAL pre-flight.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import polars as pl
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LABELS_CSV = REPO_ROOT / "data" / "v4.12" / "labels" / "macro_stance_labels.csv"
PROMPT_MD = REPO_ROOT / "data" / "v4.12" / "labels" / "labels_prompt.md"
METADATA_JSON = REPO_ROOT / "data" / "v4.12" / "labels" / "labels_metadata.json"


@pytest.fixture(scope="module")
def metadata() -> dict:
    assert METADATA_JSON.exists(), f"missing: {METADATA_JSON}"
    return json.loads(METADATA_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def csv_df() -> pl.DataFrame:
    assert LABELS_CSV.exists(), f"missing: {LABELS_CSV}"
    return pl.read_csv(LABELS_CSV)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_csv_sha256_matches(metadata: dict) -> None:
    """T1: actual CSV bytes hash equals pinned csv_sha256."""
    assert metadata["csv_sha256"] == _sha256(LABELS_CSV), (
        "csv_sha256 drift — re-run build_labels_metadata.py or restore source"
    )


def test_prompt_sha256_matches(metadata: dict) -> None:
    """T2: actual prompt bytes hash equals pinned prompt_sha256."""
    assert metadata["prompt_sha256"] == _sha256(PROMPT_MD), (
        "prompt_sha256 drift — re-run build_labels_metadata.py or restore source"
    )


def test_label_distribution_matches_csv(metadata: dict, csv_df: pl.DataFrame) -> None:
    """T3: pinned label_distribution counts equal CSV true_stance value_counts."""
    actual = Counter(csv_df["true_stance"].to_list())
    pinned = metadata["label_distribution"]
    for cls in {"HAWK", "DOV", "NEUT"}:
        assert pinned.get(cls, 0) == actual.get(cls, 0), (
            f"label_distribution drift on {cls}: "
            f"pinned={pinned.get(cls, 0)} actual={actual.get(cls, 0)}"
        )


def test_train_eval_indices_disjoint(metadata: dict) -> None:
    """T4: train_indices and eval_indices share zero rows."""
    split = metadata["train_eval_split"]
    train = set(split["train_indices"])
    eval_ = set(split["eval_indices"])
    assert not (train & eval_), f"train ∩ eval non-empty: {train & eval_}"


def test_split_total_equals_n_events(metadata: dict) -> None:
    """T5: n_train + n_eval covers all rows exactly once."""
    split = metadata["train_eval_split"]
    assert split["n_train"] + split["n_eval"] == metadata["n_events_total"], (
        f"split mismatch: n_train={split['n_train']} + n_eval={split['n_eval']} "
        f"!= n_events_total={metadata['n_events_total']}"
    )
