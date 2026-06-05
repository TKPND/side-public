"""test_macro_stance_estimator.py — Phase 101 Wave 1 Task 1 (green tests).

Replaces Wave 0 xfail skeletons with real assertions per 101-04-PLAN.md <behavior>.

Citations:
    D-69 — LABEL_MAP_FINBERT exact mapping
    CLASS-V412-01 — predict() returns 3-class label
    101-04-PLAN.md L130-137 — six required tests + <30s budget

Runtime budget: <30s. FinBERT pipeline is module-scope cached so the model
loads once per pytest process. fine_tune_roberta is NOT exercised at runtime
(>5 min CPU); structural assertion via source inspection + metadata check.
"""

from __future__ import annotations

import inspect
import json

import pytest

import macro_stance_estimator as mse


@pytest.fixture(scope="module")
def finbert_warmup() -> None:
    """Eagerly load the FinBERT pipeline once per module for fast subsequent tests."""
    mse._get_finbert_pipeline()


# --- 1. LABEL_MAP_FINBERT canonical (D-69) ---------------------------------


def test_finbert_label_map_canonical() -> None:
    """D-69 byte-exact mapping must hold."""
    assert mse.LABEL_MAP_FINBERT == {
        "positive": "DOV",
        "negative": "HAWK",
        "neutral": "NEUT",
    }


# --- 2. predict() 3-class output (zero-shot determinism, pinned sha) -------


def test_predict_returns_three_class(finbert_warmup: None) -> None:
    """predict() returns a single 3-class label for a single input.

    Contract per 101-04-PLAN.md L130: 3-class output + zero-shot determinism
    (pinned commit_sha). Direction (HAWK vs DOV) is an F1-task-2 concern;
    not asserted here. Empirical observation logged in 101-04-SUMMARY.md.
    """
    out = mse.predict(["The Fed raises rates 25bps to combat inflation"])
    assert len(out) == 1
    assert out[0] in {"HAWK", "DOV", "NEUT"}, f"unexpected label {out[0]!r}"


# --- 3. predict() preserves input length ------------------------------------


def test_predict_batch_size_invariant(finbert_warmup: None) -> None:
    """predict(N inputs) -> N outputs, every label ∈ {HAWK,DOV,NEUT}."""
    out = mse.predict(["a", "b", "c"])
    assert len(out) == 3
    for label in out:
        assert label in {"HAWK", "DOV", "NEUT"}, f"unexpected label {label!r}"


# --- 4. predict() idempotent (no nondeterminism) ----------------------------


def test_predict_idempotent(finbert_warmup: None) -> None:
    """Same input twice -> same output (greedy argmax + pinned sha)."""
    first = mse.predict(["x"])
    second = mse.predict(["x"])
    assert first == second


# --- 5. fine_tune_roberta uses only train_indices (no eval leakage) --------


def test_fine_tune_uses_train_indices_only() -> None:
    """fine_tune_roberta accepts train_texts/train_labels explicitly — never
    reads labels_metadata.json itself, and the metadata's train/eval indices
    are disjoint (caller-side leakage guard)."""
    # 5a. Function signature exposes train_texts/train_labels params (caller-supplied).
    sig = inspect.signature(mse.fine_tune_roberta)
    params = list(sig.parameters.keys())
    assert params[0] == "train_texts"
    assert params[1] == "train_labels"

    # 5b. fine_tune_roberta source does not access _LABELS_METADATA_JSON
    # (would imply the function reads eval rows itself — leakage vector).
    src = inspect.getsource(mse.fine_tune_roberta)
    assert "_LABELS_METADATA_JSON" not in src, (
        "fine_tune_roberta must not read labels_metadata.json directly; "
        "caller is responsible for passing only train rows."
    )
    assert "eval_indices" not in src, (
        "fine_tune_roberta must not reference eval_indices."
    )

    # 5c. Frozen labels_metadata.json: train_indices ∩ eval_indices = ∅.
    meta = json.loads(mse._LABELS_METADATA_JSON.read_text())
    train_idx = set(meta["train_eval_split"]["train_indices"])
    eval_idx = set(meta["train_eval_split"]["eval_indices"])
    assert train_idx.isdisjoint(eval_idx), (
        f"train_indices ∩ eval_indices = {train_idx & eval_idx}"
    )


# --- 6. evaluate_f1 returns macro + per-label + 3x3 CM ---------------------


def test_evaluate_f1_returns_macro_and_per_label() -> None:
    """Perfect predictions -> macro_f1=1.0, diagonal CM."""
    result = mse.evaluate_f1(["HAWK", "DOV"], ["HAWK", "DOV"])

    assert set(result.keys()) == {"macro_f1", "per_label_f1", "confusion_matrix"}
    assert result["macro_f1"] == pytest.approx(1.0)

    per_label = result["per_label_f1"]
    assert set(per_label.keys()) == {"HAWK", "DOV", "NEUT"}
    assert per_label["HAWK"] == pytest.approx(1.0)
    assert per_label["DOV"] == pytest.approx(1.0)
    # NEUT has no support in either pred or gold -> sklearn returns 0.0 (zero_division=0).
    assert per_label["NEUT"] == pytest.approx(0.0)

    # 3x3 confusion_matrix labels=[HAWK,DOV,NEUT].
    cm = result["confusion_matrix"]
    assert cm == [[1, 0, 0], [0, 1, 0], [0, 0, 0]]
