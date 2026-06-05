"""macro_stance_estimator.py — Phase 101 Wave 1: FinBERT zero-shot + RoBERTa fine-tune.

Public API for HAWK/DOV/NEUT macro-stance classification of central-bank statements.

Citations:
    D-61 — FinBERT 3-class -> binary mapping (no retrain)
    D-69 — LABEL_MAP_FINBERT exact: positive→DOV / negative→HAWK / neutral→NEUT
    CLASS-V412-01 — predict() returns 3-class label
    CLASS-V412-05 — selected classifier locked in macro_classifier_spec.json
    101-04-PLAN.md — Wave 1 task spec

Determinism:
    - HF model bytes pinned via revision=<commit_sha> from HF_COMMIT_SHA.json
    - RoBERTa fine-tune seeded (torch / random / numpy)
    - LABEL_MAP applied byte-exact (D-69)

Gate constraints (grep_gates_v412.sh):
    - gate #14 (network egress): no HTTP-client imports
    - gate #15 (LLM API runtime): no remote-LLM SDK imports
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import random
from datetime import datetime, timezone
from typing import Any

# D-69 LABEL_MAP_FINBERT — byte-exact frozen mapping.
LABEL_MAP_FINBERT: dict[str, str] = {
    "positive": "DOV",
    "negative": "HAWK",
    "neutral": "NEUT",
}

# Allowed final labels (HAWK + DOV are F1-evaluated; NEUT is kill-set).
_ALLOWED_LABELS: tuple[str, ...] = ("HAWK", "DOV", "NEUT")
_F1_LABELS: tuple[str, ...] = ("HAWK", "DOV")  # NEUT excluded from F1 (Hint 4)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_HF_COMMIT_SHA_JSON = _REPO_ROOT / "scripts" / "v4.12" / "HF_COMMIT_SHA.json"
_LABELS_METADATA_JSON = _REPO_ROOT / "data" / "v4.12" / "labels" / "labels_metadata.json"
_ROBERTA_MODEL_DIR = _REPO_ROOT / "scripts" / "v4.12" / "models" / "roberta_macro_stance_v412"


def _load_hf_commit_sha() -> dict[str, dict[str, str]]:
    """Read HF_COMMIT_SHA.json (Plan 101-01 output) for revision pinning."""
    with _HF_COMMIT_SHA_JSON.open() as f:
        return json.load(f)


_hf_pin = _load_hf_commit_sha()
FINBERT_REPO_ID: str = _hf_pin["finbert"]["repo_id"]
FINBERT_SHA: str = _hf_pin["finbert"]["sha"]
ROBERTA_REPO_ID: str = _hf_pin["roberta"]["repo_id"]
ROBERTA_SHA: str = _hf_pin["roberta"]["sha"]

# Lazy pipeline cache (one-shot model load per process).
_FINBERT_PIPE: Any = None
_ROBERTA_INFER: Any = None  # (model, tokenizer, id2label_binary)


def _get_finbert_pipeline() -> Any:
    """Build FinBERT text-classification pipeline at pinned commit_sha (CPU)."""
    global _FINBERT_PIPE
    if _FINBERT_PIPE is not None:
        return _FINBERT_PIPE

    from transformers import (  # type: ignore[import-untyped]
        AutoModelForSequenceClassification,
        AutoTokenizer,
        pipeline,
    )

    tokenizer = AutoTokenizer.from_pretrained(FINBERT_REPO_ID, revision=FINBERT_SHA)
    model = AutoModelForSequenceClassification.from_pretrained(
        FINBERT_REPO_ID, revision=FINBERT_SHA
    )
    _FINBERT_PIPE = pipeline(
        "text-classification",
        model=model,
        tokenizer=tokenizer,
        device=-1,  # CPU
        truncation=True,
        max_length=512,
    )
    return _FINBERT_PIPE


def _predict_finbert(texts: list[str]) -> list[str]:
    """FinBERT zero-shot 3-class -> HAWK/DOV/NEUT via LABEL_MAP_FINBERT (D-69)."""
    pipe = _get_finbert_pipeline()
    raw = pipe(texts)
    out: list[str] = []
    for row in raw:
        raw_label = row["label"].lower()
        mapped = LABEL_MAP_FINBERT[raw_label]
        out.append(mapped)
    return out


def _predict_roberta(texts: list[str]) -> list[str]:
    """RoBERTa fine-tuned binary head HAWK/DOV (NEUT not emitted; kill-set)."""
    global _ROBERTA_INFER
    if _ROBERTA_INFER is None:
        if not _ROBERTA_MODEL_DIR.exists():
            raise RuntimeError(
                f"RoBERTa fine-tuned model not found at {_ROBERTA_MODEL_DIR}. "
                "Run fine_tune_roberta() first (Task 2 driver does this)."
            )
        from transformers import (  # type: ignore[import-untyped]
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        tokenizer = AutoTokenizer.from_pretrained(_ROBERTA_MODEL_DIR)
        model = AutoModelForSequenceClassification.from_pretrained(_ROBERTA_MODEL_DIR)
        model.eval()
        # Binary head: id2label = {0: "HAWK", 1: "DOV"} (set during fine-tune).
        id2label = {int(k): v for k, v in model.config.id2label.items()}
        _ROBERTA_INFER = (model, tokenizer, id2label)

    import torch  # type: ignore[import-untyped]

    model, tokenizer, id2label = _ROBERTA_INFER
    out: list[str] = []
    with torch.no_grad():
        for text in texts:
            enc = tokenizer(
                text, return_tensors="pt", truncation=True, max_length=512
            )
            logits = model(**enc).logits
            pred_id = int(logits.argmax(-1).item())
            out.append(id2label[pred_id])
    return out


def predict(texts: list[str], classifier: str = "finbert") -> list[str]:
    """Predict HAWK/DOV/NEUT for each input text.

    Args:
        texts: list of input strings (statement_text); use list even for single input.
        classifier: "finbert" (zero-shot) or "roberta" (fine-tuned binary head).

    Returns:
        list[str] of length len(texts), each ∈ {HAWK, DOV, NEUT}.
        RoBERTa never emits NEUT (binary head, kill-set fallback).

    Determinism:
        Pinned commit_sha + greedy argmax => bit-stable predictions.
    """
    if not isinstance(texts, list):
        raise TypeError(f"texts must be list[str], got {type(texts).__name__}")
    if len(texts) == 0:
        return []

    if classifier == "finbert":
        return _predict_finbert(texts)
    if classifier == "roberta":
        return _predict_roberta(texts)
    raise ValueError(f"unknown classifier: {classifier!r}")


def fine_tune_roberta(
    train_texts: list[str],
    train_labels: list[str],
    seed: int = 20260426,
    output_dir: pathlib.Path | None = None,
    epochs: int = 3,
    batch_size: int = 8,
    learning_rate: float = 2e-5,
) -> pathlib.Path:
    """Fine-tune cardiffnlp RoBERTa with binary HAWK/DOV head.

    Drops NEUT rows (kill-set per Hint 3 / D-61). Returns model dir.

    Determinism: torch / random / numpy seeded; greedy argmax at inference.
    Caller MUST pass only training-split rows; held-out rows forbidden (leakage).
    """
    if len(train_texts) != len(train_labels):
        raise ValueError(
            f"len(train_texts)={len(train_texts)} != len(train_labels)={len(train_labels)}"
        )
    # Drop NEUT (binary head).
    pairs = [(t, l) for t, l in zip(train_texts, train_labels) if l != "NEUT"]
    if not pairs:
        raise ValueError("no HAWK/DOV rows in training set after NEUT drop")
    texts_bin = [p[0] for p in pairs]
    labels_bin = [p[1] for p in pairs]

    out_dir = output_dir if output_dir is not None else _ROBERTA_MODEL_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Seed all RNG layers.
    import numpy as np  # type: ignore[import-untyped]
    import torch  # type: ignore[import-untyped]

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    os.environ.setdefault("PYTHONHASHSEED", str(seed))

    from transformers import (  # type: ignore[import-untyped]
        AutoModelForSequenceClassification,
        AutoTokenizer,
        Trainer,
        TrainingArguments,
    )

    label2id = {"HAWK": 0, "DOV": 1}
    id2label = {0: "HAWK", 1: "DOV"}

    tokenizer = AutoTokenizer.from_pretrained(ROBERTA_REPO_ID, revision=ROBERTA_SHA)
    model = AutoModelForSequenceClassification.from_pretrained(
        ROBERTA_REPO_ID,
        revision=ROBERTA_SHA,
        num_labels=2,
        id2label=id2label,
        label2id=label2id,
        ignore_mismatched_sizes=True,
    )

    encodings = tokenizer(
        texts_bin, truncation=True, padding=True, max_length=512
    )
    label_ids = [label2id[l] for l in labels_bin]

    class _ListDataset(torch.utils.data.Dataset):
        def __init__(self, encodings: dict[str, list[Any]], labels: list[int]) -> None:
            self.encodings = encodings
            self.labels = labels

        def __len__(self) -> int:
            return len(self.labels)

        def __getitem__(self, idx: int) -> dict[str, Any]:
            item = {k: torch.tensor(v[idx]) for k, v in self.encodings.items()}
            item["labels"] = torch.tensor(self.labels[idx])
            return item

    train_ds = _ListDataset(dict(encodings), label_ids)

    args = TrainingArguments(
        output_dir=str(out_dir / "_train_tmp"),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        learning_rate=learning_rate,
        seed=seed,
        data_seed=seed,
        save_strategy="no",
        logging_strategy="no",
        report_to=[],
        disable_tqdm=True,
    )

    trainer = Trainer(model=model, args=args, train_dataset=train_ds)
    trainer.train()

    model.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)
    return out_dir


def evaluate_f1(predictions: list[str], gold_labels: list[str]) -> dict[str, Any]:
    """macro F1 (HAWK + DOV avg, NEUT excluded per Hint 4) + per-label F1 + 3x3 CM.

    Returns:
        {"macro_f1": float, "per_label_f1": {HAWK,DOV,NEUT: float}, "confusion_matrix": [[3x3]]}
    """
    if len(predictions) != len(gold_labels):
        raise ValueError(
            f"len(predictions)={len(predictions)} != len(gold_labels)={len(gold_labels)}"
        )

    from sklearn.metrics import (  # type: ignore[import-untyped]
        confusion_matrix,
        f1_score,
    )

    labels_3 = list(_ALLOWED_LABELS)  # ["HAWK","DOV","NEUT"] for CM
    labels_2 = list(_F1_LABELS)  # ["HAWK","DOV"] for macro F1

    macro_f1 = float(
        f1_score(
            gold_labels,
            predictions,
            labels=labels_2,
            average="macro",
            zero_division=0,
        )
    )

    per_label_arr = f1_score(
        gold_labels,
        predictions,
        labels=labels_3,
        average=None,
        zero_division=0,
    )
    per_label_f1 = {label: float(score) for label, score in zip(labels_3, per_label_arr)}

    cm = confusion_matrix(gold_labels, predictions, labels=labels_3).tolist()

    return {
        "macro_f1": macro_f1,
        "per_label_f1": per_label_f1,
        "confusion_matrix": cm,
    }


def _load_labels_metadata() -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Load labels_metadata.json + macro_stance_labels.csv rows."""
    import polars as pl  # type: ignore[import-untyped]

    meta = json.loads(_LABELS_METADATA_JSON.read_text())
    csv_path = _REPO_ROOT / meta["csv_path"]
    df = pl.read_csv(csv_path)
    rows = df.to_dicts()
    return meta, rows


def _sha256_sorted_indices(indices: list[int]) -> str:
    """Audit hash of sorted train_indices for spec.json."""
    payload = json.dumps(sorted(indices), separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _run_eval_cli() -> None:
    """Wave 1 Task 2 driver: F1 wave-0 eval + selection + spec.json draft.

    Steps:
        1. Load frozen labels CSV + metadata.
        2. Split train / eval rows by indices (no resampling).
        3. FinBERT predict() on eval texts -> predictions_finbert.
        4. fine_tune_roberta(train) -> model_dir.
        5. RoBERTa predict() on eval texts -> predictions_roberta.
        6. evaluate_f1 both -> F1_wave0_results.json.
        7. Selection rule: higher macro_f1; |delta| < 0.02 -> finbert (reproducibility).
        8. Write macro_classifier_spec.json (draft, _note marker for Wave 2 SEAL).
    """
    meta, rows = _load_labels_metadata()
    split = meta["train_eval_split"]
    train_idx: list[int] = list(split["train_indices"])
    eval_idx: list[int] = list(split["eval_indices"])

    if not set(train_idx).isdisjoint(set(eval_idx)):
        raise RuntimeError("train_indices ∩ eval_indices is non-empty (leakage)")

    train_rows = [rows[i] for i in train_idx]
    eval_rows = [rows[i] for i in eval_idx]
    train_texts = [r["statement_text"] for r in train_rows]
    train_labels = [r["true_stance"] for r in train_rows]
    eval_texts = [r["statement_text"] for r in eval_rows]
    eval_labels = [r["true_stance"] for r in eval_rows]

    print(f"[eval] train n={len(train_texts)} (NEUT will be dropped for RoBERTa head)")
    print(f"[eval] eval  n={len(eval_texts)}")

    # FinBERT zero-shot.
    print("[eval] FinBERT predict on eval set...")
    pred_finbert = predict(eval_texts, classifier="finbert")
    f1_finbert = evaluate_f1(pred_finbert, eval_labels)
    print(f"[eval] FinBERT macro_f1 = {f1_finbert['macro_f1']:.4f}")

    # RoBERTa fine-tune + inference.
    print("[eval] RoBERTa fine-tune on train set...")
    model_dir = fine_tune_roberta(train_texts, train_labels, seed=20260426)
    print(f"[eval] RoBERTa model_dir = {model_dir}")
    print("[eval] RoBERTa predict on eval set...")
    pred_roberta = predict(eval_texts, classifier="roberta")
    f1_roberta = evaluate_f1(pred_roberta, eval_labels)
    print(f"[eval] RoBERTa macro_f1 = {f1_roberta['macro_f1']:.4f}")

    # F1_wave0_results.json.
    n_eval = len(eval_labels)
    f1_results = {
        "n_eval": n_eval,
        "eval_label_distribution": {
            l: sum(1 for x in eval_labels if x == l) for l in _ALLOWED_LABELS
        },
        "finbert": {
            "macro_f1": f1_finbert["macro_f1"],
            "per_label_f1": f1_finbert["per_label_f1"],
            "confusion_matrix": f1_finbert["confusion_matrix"],
            "predictions": pred_finbert,
        },
        "roberta": {
            "macro_f1": f1_roberta["macro_f1"],
            "per_label_f1": f1_roberta["per_label_f1"],
            "confusion_matrix": f1_roberta["confusion_matrix"],
            "predictions": pred_roberta,
        },
        "gold_labels": eval_labels,
        "labels_metadata_sha256": meta.get("csv_sha256"),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    f1_path = _REPO_ROOT / "scripts" / "v4.12" / "F1_wave0_results.json"
    f1_path.write_text(json.dumps(f1_results, indent=2, ensure_ascii=False) + "\n")
    print(f"[eval] wrote {f1_path}")

    # Selection rule (Plan 101-04 Task 2 step 7).
    delta = f1_roberta["macro_f1"] - f1_finbert["macro_f1"]
    if delta > 0.02:
        selected = "roberta"
        rationale = (
            f"roberta macro_f1 ({f1_roberta['macro_f1']:.4f}) > "
            f"finbert ({f1_finbert['macro_f1']:.4f}) by {delta:+.4f} (>0.02 threshold)"
        )
    else:
        selected = "finbert"
        rationale = (
            f"finbert preferred — delta={delta:+.4f} within ±0.02 noise band, "
            f"zero-shot reproducibility tiebreak (D-61)"
        )

    spec = {
        "selected_classifier": selected,
        "selection_rationale": rationale,
        "finbert": {
            "repo_id": FINBERT_REPO_ID,
            "commit_sha": FINBERT_SHA,
            "label_map": LABEL_MAP_FINBERT,
            "macro_f1_eval": f1_finbert["macro_f1"],
        },
        "roberta": {
            "repo_id": ROBERTA_REPO_ID,
            "commit_sha": ROBERTA_SHA,
            "fine_tune_seed": 20260426,
            "train_indices_sha256": _sha256_sorted_indices(train_idx),
            "macro_f1_eval": f1_roberta["macro_f1"],
        },
        "labels_metadata_sha256": meta.get("csv_sha256"),
        "evaluated_at": f1_results["evaluated_at"],
        "_note": "draft — Wave 2 atomic SEAL pins this file's sha256 into 4 SEAL artifacts",
    }
    spec_path = _REPO_ROOT / "scripts" / "v4.12" / "macro_classifier_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    print(f"[eval] wrote {spec_path}")
    print(f"[eval] selected_classifier = {selected}")


def main() -> None:
    parser = argparse.ArgumentParser(description="macro_stance_estimator v4.12")
    parser.add_argument(
        "--eval",
        action="store_true",
        help="Run wave-0 F1 evaluation + write macro_classifier_spec.json + F1_wave0_results.json",
    )
    args = parser.parse_args()
    if args.eval:
        _run_eval_cli()
        return
    parser.print_help()


if __name__ == "__main__":
    main()
