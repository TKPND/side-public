#!/usr/bin/env bash
# Phase 71 orchestrator: fresh WFD 12 report → sign_breakdown.json + audit_matrix.
# See .planning/phases/71-sign-breakdown-re-aggregation/71-CONTEXT.md D-01/D-09/D-11.
# Provenance stamp expected: fresh-wfd-rerun-2026-04-19-70303ac
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

REPORT_ROOT="docs/reports/v4.6-verdict-resolution/per-pair"
OUT_DIR="docs/reports/v4.6-verdict-resolution/sign-forensics"
SEED="${SEED:-42}"
PAIRS=(audusd eurjpy eurusd usdjpy)
EVENTS=(ecb fomc nfp)

mkdir -p "$OUT_DIR"

# Enumerate all 12 paths deterministically.
paths=()
for pair in "${PAIRS[@]}"; do
  for event in "${EVENTS[@]}"; do
    p="$REPORT_ROOT/$pair/$event/report.json"
    [[ -f "$p" ]] || { echo "missing fresh report: $p" >&2; exit 2; }
    paths+=("$p")
  done
done
if [[ "${#paths[@]}" -ne 12 ]]; then
  echo "expected exactly 12 fresh reports, got ${#paths[@]}" >&2
  exit 3
fi

echo "=== sign_breakdown.py (seed=$SEED) ==="
input_args=()
for p in "${paths[@]}"; do input_args+=(--input "$p"); done
uv run python scripts/v4.4/sign_breakdown.py \
  "${input_args[@]}" \
  --output "$OUT_DIR/sign_breakdown.json" \
  --seed "$SEED"

echo "=== audit.py (v4.6 mode) ==="
v46_args=()
for p in "${paths[@]}"; do v46_args+=(--v46-report "$p"); done
uv run python scripts/v4.4/audit.py \
  "${v46_args[@]}" \
  --output "$OUT_DIR"

echo "=== verification ==="
jq -r '.meta.input_provenance_stamp' "$OUT_DIR/sign_breakdown.json"
jq -r '.cells | length' "$OUT_DIR/audit_matrix.json"
echo "=== done ==="
