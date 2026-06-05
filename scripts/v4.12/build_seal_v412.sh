#!/usr/bin/env bash
# scripts/v4.12/build_seal_v412.sh
#
# Phase 101 Plan 06 Task 1: assemble 4 SEAL artifacts with cross-pinned sha256.
#
# Output (idempotent):
#   .planning/phases/101-.../SEAL/macro_classifier_spec.json
#   .planning/phases/101-.../SEAL/macro_cuts.json
#   .planning/phases/101-.../SEAL/macro_filter_spec.json
#   .planning/phases/101-.../SEAL/workload_spec_v412.json
#
# Each artifact's canonical_sha256 is `jq -cS . FILE | sha256sum` (D-15 v4.11 algorithm).
# Re-running this script when artifacts already match current pins is a no-op.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PHASE_DIR="$REPO_ROOT/.planning/phases/101-pre-reg-seal-signal-commit-v412-7th-anchor-macro-stance-estimator-nyquist-audit"
SEAL_DIR="$PHASE_DIR/SEAL"

LABELS_METADATA="$REPO_ROOT/data/v4.12/labels/labels_metadata.json"
HF_COMMIT_SHA="$REPO_ROOT/scripts/v4.12/HF_COMMIT_SHA.json"
NYQUIST_AUDIT="$REPO_ROOT/reports/v4.12/nyquist_audit_v412.json"
CLASSIFIER_DRAFT="$REPO_ROOT/scripts/v4.12/macro_classifier_spec.json"
WORKLOAD_SPEC="$REPO_ROOT/config/v4.12/workload_spec_v412.json"

mkdir -p "$SEAL_DIR"

canonical_sha256() {
  jq -cS . "$1" | sha256sum | awk '{print $1}'
}

# Frozen-at timestamp: stable across re-runs to keep canonical_sha256 idempotent.
# Use the first run's timestamp if any SEAL artifact already has one; otherwise now.
existing_frozen_at() {
  local f="$1"
  if [ -f "$f" ]; then
    jq -r '.frozen_at // empty' "$f" 2>/dev/null || true
  fi
}

NOW_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# -----------------------------------------------------------------------
# 1. SEAL/macro_classifier_spec.json — finalize, drop draft _note
# -----------------------------------------------------------------------
LABELS_SHA="$(canonical_sha256 "$LABELS_METADATA")"
HF_SHA="$(canonical_sha256 "$HF_COMMIT_SHA")"

CLASSIFIER_OUT="$SEAL_DIR/macro_classifier_spec.json"
CLASSIFIER_FROZEN_AT="$(existing_frozen_at "$CLASSIFIER_OUT")"
[ -z "$CLASSIFIER_FROZEN_AT" ] && CLASSIFIER_FROZEN_AT="$NOW_UTC"

jq --arg labels "$LABELS_SHA" \
   --arg hf "$HF_SHA" \
   --arg frozen "$CLASSIFIER_FROZEN_AT" \
   '. + {
      labels_metadata_sha256: $labels,
      hf_commit_sha_pin_sha256: $hf,
      frozen_at: $frozen
    } | del(._note)' \
   "$CLASSIFIER_DRAFT" > "$CLASSIFIER_OUT.tmp"
mv "$CLASSIFIER_OUT.tmp" "$CLASSIFIER_OUT"

# -----------------------------------------------------------------------
# 2. SEAL/macro_cuts.json — draft skeleton, pins nyquist_audit_v412
# -----------------------------------------------------------------------
NYQUIST_SHA="$(canonical_sha256 "$NYQUIST_AUDIT")"

CUTS_OUT="$SEAL_DIR/macro_cuts.json"
CUTS_FROZEN_AT="$(existing_frozen_at "$CUTS_OUT")"
[ -z "$CUTS_FROZEN_AT" ] && CUTS_FROZEN_AT="$NOW_UTC"

jq -n \
  --arg nyquist "$NYQUIST_SHA" \
  --arg frozen "$CUTS_FROZEN_AT" \
  '{
     phase: 101,
     _note: "draft — Phase 102 will finalize per-event kill rules; this skeleton exists so signal_commit_v412 can pin sha256",
     kill_rules: [],
     nyquist_audit_sha256: $nyquist,
     frozen_at: $frozen
   }' > "$CUTS_OUT.tmp"
mv "$CUTS_OUT.tmp" "$CUTS_OUT"

# -----------------------------------------------------------------------
# 3. SEAL/macro_filter_spec.json — draft skeleton, pins macro_classifier_spec
# -----------------------------------------------------------------------
CLASSIFIER_SHA="$(canonical_sha256 "$CLASSIFIER_OUT")"

FILTER_OUT="$SEAL_DIR/macro_filter_spec.json"
FILTER_FROZEN_AT="$(existing_frozen_at "$FILTER_OUT")"
[ -z "$FILTER_FROZEN_AT" ] && FILTER_FROZEN_AT="$NOW_UTC"

jq -n \
  --arg classifier "$CLASSIFIER_SHA" \
  --arg frozen "$FILTER_FROZEN_AT" \
  '{
     phase: 101,
     _note: "draft — Phase 102 will finalize HAWK/DOV regime filter activation rules",
     filter_rules: [],
     macro_classifier_spec_sha256: $classifier,
     frozen_at: $frozen
   }' > "$FILTER_OUT.tmp"
mv "$FILTER_OUT.tmp" "$FILTER_OUT"

# -----------------------------------------------------------------------
# 4. SEAL/workload_spec_v412.json — copy from Phase 100 config, add phase_101 pin
# -----------------------------------------------------------------------
WORKLOAD_OUT="$SEAL_DIR/workload_spec_v412.json"
WORKLOAD_FROZEN_AT="$(existing_frozen_at "$WORKLOAD_OUT")"
# The source already has _canonical_sha256 from Phase 100; the SEAL copy just adds phase_101_pinned_at.
PHASE_101_PINNED_AT="$(jq -r '.phase_101_pinned_at // empty' "$WORKLOAD_OUT" 2>/dev/null || true)"
[ -z "$PHASE_101_PINNED_AT" ] && PHASE_101_PINNED_AT="$NOW_UTC"

jq --arg pinned "$PHASE_101_PINNED_AT" \
   '. + {phase_101_pinned_at: $pinned}' \
   "$WORKLOAD_SPEC" > "$WORKLOAD_OUT.tmp"
mv "$WORKLOAD_OUT.tmp" "$WORKLOAD_OUT"

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
echo "=== build_seal_v412.sh complete ==="
echo "SEAL_DIR: $SEAL_DIR"
for f in macro_classifier_spec.json macro_cuts.json macro_filter_spec.json workload_spec_v412.json; do
  sha="$(canonical_sha256 "$SEAL_DIR/$f")"
  echo "  $f  canonical_sha256=$sha"
done
echo ""
echo "Pins recorded:"
echo "  labels_metadata_sha256:           $LABELS_SHA"
echo "  hf_commit_sha_pin_sha256:         $HF_SHA"
echo "  nyquist_audit_sha256:             $NYQUIST_SHA"
echo "  macro_classifier_spec_sha256:     $CLASSIFIER_SHA"
