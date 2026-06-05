#!/usr/bin/env bash
# Phase 72 orchestrator: sign_breakdown.json -> report.json (verdict classification).
# See .planning/phases/72-verdict-classification/72-CONTEXT.md D-01/D-02/D-08.
# Provenance stamp expected: fresh-wfd-rerun-2026-04-19-70303ac
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

SIGN_INPUT="docs/reports/v4.6-verdict-resolution/sign-forensics/sign_breakdown.json"
OUT_DIR="docs/reports/v4.6-verdict-resolution"
THRESHOLD_COMMIT="${THRESHOLD_COMMIT:-432a885}"

[[ -f "$SIGN_INPUT" ]] || { echo "missing Phase 71 output: $SIGN_INPUT" >&2; exit 2; }

mkdir -p "$OUT_DIR"

echo "=== verdict_classifier.py ==="
uv run python scripts/v4.6/verdict_classifier.py \
  --sign "$SIGN_INPUT" \
  --output-dir "$OUT_DIR" \
  --threshold-commit "$THRESHOLD_COMMIT"

echo "=== verification ==="
jq -r '.verdict' "$OUT_DIR/report.json"
jq -r '.meta.input_provenance_stamp' "$OUT_DIR/report.json"
jq -r '.fleiss_kappa | type' "$OUT_DIR/report.json"   # D-10: must be "number"
echo "=== done ==="
