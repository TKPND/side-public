"""One-shot LLM labeler for macro stance (Phase 101 Plan 02 Task 2).

Pipeline (D-63: Claude Opus 4.7, temperature=0 NOT exposed via CLI; pin via
prompt sha256 + model_version + retry-disabled one-shot):
    1. Read data/v4.12/labels/labels_prompt.md (frozen prompt template)
    2. Read data/v4.12/labels/macro_statements_raw.csv
    3. For each row, invoke `claude -p --no-session-persistence --json-schema ...`
       with stdin = prompt + "\\n" + statement_text. Single attempt; on parse
       error, log to stderr and skip (NO retries — retries break determinism).
    4. Write data/v4.12/labels/macro_stance_labels.csv with D-64 schema:
         event_ts, central_bank, statement_text, true_stance, label_provenance,
         annotator, snapshot_ts

Idempotency: if labels CSV exists with ≥30 rows, exit 0 (already frozen).

D-72 enum scope: label_provenance = "frozen-llm-once-claude-opus-4-7" applies
to THIS ground truth CSV. The frozen-llm-once-prosusai-finbert / -roberta-base
enum is bound to D-71 prediction parquet, NOT to this file.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = REPO_ROOT / "data" / "v4.12" / "labels" / "labels_prompt.md"
RAW_CSV = REPO_ROOT / "data" / "v4.12" / "labels" / "macro_statements_raw.csv"
OUTPUT_CSV = REPO_ROOT / "data" / "v4.12" / "labels" / "macro_stance_labels.csv"

CLAUDE_MODEL = "claude-opus-4-7"
LABEL_PROVENANCE = "frozen-llm-once-claude-opus-4-7"
ANNOTATOR = "claude-opus-4-7"
MIN_ROWS = 30  # D-59 errata: 32 expected, 2-row tolerance

JSON_SCHEMA = {
    "type": "object",
    "properties": {"stance": {"type": "string", "enum": ["HAWK", "DOV", "NEUT"]}},
    "required": ["stance"],
    "additionalProperties": False,
}

CLAUDE_FLAGS = [
    "claude",
    "-p",
    "--no-session-persistence",
    "--model",
    CLAUDE_MODEL,
    "--output-format",
    "json",
    "--json-schema",
    json.dumps(JSON_SCHEMA, separators=(",", ":")),
]


def _existing_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)  # minus header


def _call_claude_one_shot(stdin_text: str) -> str | None:
    """Single attempt. Returns 'HAWK' | 'DOV' | 'NEUT' | None on any failure.

    NO retries (D-63). Failures bubble up as None and the row is dropped.
    """
    try:
        proc = subprocess.run(
            CLAUDE_FLAGS,
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        sys.stderr.write(f"[warn] claude invocation failed: {e}\n")
        return None
    if proc.returncode != 0:
        sys.stderr.write(
            f"[warn] claude exit={proc.returncode}: {proc.stderr.strip()[:300]}\n"
        )
        return None
    try:
        outer = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        sys.stderr.write(
            f"[warn] outer JSON parse fail: {e}; head={proc.stdout[:200]!r}\n"
        )
        return None
    # --json-schema places the schema-conforming object at .structured_output
    # (.result is free-form natural-language commentary, not the schema target).
    structured = outer.get("structured_output") if isinstance(outer, dict) else None
    stance = structured.get("stance") if isinstance(structured, dict) else None
    if stance not in ("HAWK", "DOV", "NEUT"):
        sys.stderr.write(
            f"[warn] invalid stance value: {stance!r}; structured={structured!r}\n"
        )
        return None
    return stance


def main() -> int:
    if _existing_row_count(OUTPUT_CSV) >= MIN_ROWS:
        sys.stderr.write(
            f"[skip] {OUTPUT_CSV} already has ≥{MIN_ROWS} rows. Delete to re-label.\n"
        )
        return 0
    if not PROMPT_PATH.exists():
        sys.stderr.write(f"[error] prompt not found: {PROMPT_PATH}\n")
        return 2
    if not RAW_CSV.exists():
        sys.stderr.write(
            f"[error] raw CSV not found: {RAW_CSV}. Run fetch_macro_statements.py first.\n"
        )
        return 2

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    snapshot_ts = dt.datetime.now(dt.timezone.utc).isoformat()

    with RAW_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    sys.stderr.write(f"[label] {len(rows)} statements to label via {CLAUDE_MODEL}\n")

    labeled: list[dict[str, str]] = []
    dropped = 0
    for i, row in enumerate(rows, 1):
        statement = row["statement_text"]
        stdin_text = prompt_template + statement + "\n"
        stance = _call_claude_one_shot(stdin_text)
        if stance is None:
            dropped += 1
            sys.stderr.write(
                f"[drop] {i}/{len(rows)} {row['central_bank']} {row['event_ts']}\n"
            )
            continue
        labeled.append(
            {
                "event_ts": row["event_ts"],
                "central_bank": row["central_bank"],
                "statement_text": statement,
                "true_stance": stance,
                "label_provenance": LABEL_PROVENANCE,
                "annotator": ANNOTATOR,
                "snapshot_ts": snapshot_ts,
            }
        )
        sys.stderr.write(
            f"[ok] {i}/{len(rows)} {row['central_bank']} {row['event_ts']} -> {stance}\n"
        )

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "event_ts",
                "central_bank",
                "statement_text",
                "true_stance",
                "label_provenance",
                "annotator",
                "snapshot_ts",
            ],
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        writer.writerows(labeled)

    counts: dict[str, int] = {"HAWK": 0, "DOV": 0, "NEUT": 0}
    for r in labeled:
        counts[r["true_stance"]] += 1
    sys.stderr.write(
        f"[done] wrote {len(labeled)} rows ({dropped} dropped) -> {OUTPUT_CSV}\n"
        f"[dist] HAWK={counts['HAWK']} DOV={counts['DOV']} NEUT={counts['NEUT']}\n"
    )

    if len(labeled) < MIN_ROWS:
        sys.stderr.write(
            f"[error] only {len(labeled)} labeled (<{MIN_ROWS}); pre-reg freeze rejected.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
