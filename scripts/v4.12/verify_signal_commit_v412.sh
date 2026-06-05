#!/usr/bin/env bash
# scripts/v4.12/verify_signal_commit_v412.sh
#
# v4.12 Phase 101 SEAL drift + D-23-v412 untouched-clause verifier.
# 4-gate replay verifier mirroring scripts/v4.11/verify_signal_commit_v411.sh.
#
# Exit codes:
#   0 = all gates pass (signal_commit_v412 canonical hash matches the 4 sealed artifacts,
#       per-artifact sha256 drift-free, count=1 atomic, grep_gates_v412 exit 0)
#   1 = canonical sha256 drift (Gate 1 — recomputed != stored)
#   2 = per-artifact sha256 drift (Gate 2 — sealed_artifacts[i].sha256 != actual)
#   3 = D-23-v412 violation (Gate 3 — atomic count != 1)
#   4 = grep_gates_v412 regression (Gate 4 — anti-feature gates failed)
#   5 = prerequisite tool missing or files missing
#
# Invoked by:
#   - Plan 101-06 Task 2 (smoke check pre-commit; gates 1+2+4 expected pass, gate 3 expected count=0)
#   - Plan 101-06 Task 3 post-atomic-commit (all 4 gates must pass)
#   - Phase 102+ pre-flight before any change touches SEAL chain

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PHASE_DIR="$REPO_ROOT/.planning/phases/101-pre-reg-seal-signal-commit-v412-7th-anchor-macro-stance-estimator-nyquist-audit"
SEAL_DIR="$PHASE_DIR/SEAL"
ANCHOR_FILE="$SEAL_DIR/signal_commit_v412.json"
GREP_GATES="$REPO_ROOT/scripts/v4.12/grep_gates_v412.sh"

# 4 sealed artifacts (sorted lexically — must match the order canonical_sha256 was computed in)
SEALED_ARTIFACTS=(
  "macro_classifier_spec.json"
  "macro_cuts.json"
  "macro_filter_spec.json"
  "workload_spec_v412.json"
)

echo "=== Phase 101 SEAL Replay Verifier (v4.12 septuple-pin) ==="
echo "REPO_ROOT:   $REPO_ROOT"
echo "SEAL_DIR:    $SEAL_DIR"
echo "ANCHOR_FILE: $ANCHOR_FILE"
echo ""

# -----------------------------------------------------------------------
# Gate 0: prerequisites
# -----------------------------------------------------------------------
echo "--- Gate 0: Prerequisites ---"
for tool in jq sha256sum git awk grep; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "  OK: $tool found"
  else
    echo "ERROR: required tool '$tool' not found in PATH"
    exit 5
  fi
done

if [ ! -f "$ANCHOR_FILE" ]; then
  echo "ERROR: anchor file missing: $ANCHOR_FILE"
  exit 5
fi
for f in "${SEALED_ARTIFACTS[@]}"; do
  if [ ! -f "$SEAL_DIR/$f" ]; then
    echo "ERROR: sealed artifact missing: $SEAL_DIR/$f"
    exit 5
  fi
done
echo "  OK: anchor + 4 sealed artifacts present"
echo "GATE 0 PASS"
echo ""

# -----------------------------------------------------------------------
# Gate 1: canonical_sha256 over 4 sealed artifacts (D-15 v4.11 algorithm)
# -----------------------------------------------------------------------
echo "--- Gate 1: canonical_sha256 (D-15 jq -cS pipeline) ---"
COMPUTED=$(
  cd "$SEAL_DIR"
  for f in $(printf '%s\n' "${SEALED_ARTIFACTS[@]}" | sort); do
    jq -cS . "$f"
  done | sha256sum | awk '{print $1}'
)
echo "computed canonical_sha256: $COMPUTED"

RECORDED=$(jq -r '.canonical_sha256' "$ANCHOR_FILE")
echo "recorded canonical_sha256: $RECORDED"

if [ "$COMPUTED" != "$RECORDED" ]; then
  echo ""
  echo "FAIL: canonical_sha256 drift detected"
  echo "  Action: One or more sealed_artifacts changed post-SEAL. D-23-v412 violated."
  exit 1
fi
echo "GATE 1 PASS: canonical_sha256 match"
echo ""

# -----------------------------------------------------------------------
# Gate 2: per-artifact sha256 drift
# -----------------------------------------------------------------------
echo "--- Gate 2: per-artifact sha256 drift ---"
GATE2_FAIL=0
for f in "${SEALED_ARTIFACTS[@]}"; do
  actual=$(jq -cS . "$SEAL_DIR/$f" | sha256sum | awk '{print $1}')
  recorded=$(jq -r --arg p "SEAL/$f" '.sealed_artifacts[] | select(.path == $p) | .sha256' "$ANCHOR_FILE")
  if [ -z "$recorded" ]; then
    echo "FAIL: $f — no sealed_artifacts row for path SEAL/$f"
    GATE2_FAIL=1
  elif [ "$actual" != "$recorded" ]; then
    echo "FAIL: $f drift"
    echo "  actual:   $actual"
    echo "  recorded: $recorded"
    GATE2_FAIL=1
  else
    echo "  OK: $f sha256=$actual"
  fi
done
if [ "$GATE2_FAIL" -ne 0 ]; then
  exit 2
fi
echo "GATE 2 PASS: all 4 artifacts drift-free"
echo ""

# -----------------------------------------------------------------------
# Gate 3: D-23-v412 untouched clause — anchor file count=1 in git log
# -----------------------------------------------------------------------
echo "--- Gate 3: D-23-v412 atomic invariant (git log count=1) ---"
COUNT=$(git -C "$REPO_ROOT" log --follow --oneline -- "$ANCHOR_FILE" 2>/dev/null | wc -l | tr -d ' ')
echo "  git log count for signal_commit_v412.json: $COUNT"
if [ "$COUNT" -ne 1 ]; then
  echo "FAIL: D-23-v412 violation — anchor file commit count = $COUNT (expected 1)"
  echo "  Pre-atomic-commit: count=0 is expected; run Task 3 atomic commit."
  echo "  Post-atomic-commit count > 1: SEAL re-edited; null-ship-v4 path triggered."
  exit 3
fi
echo "GATE 3 PASS: atomic SEAL untouched"
echo ""

# -----------------------------------------------------------------------
# Gate 4: grep_gates_v412.sh propagation (anti-feature gates green)
# -----------------------------------------------------------------------
echo "--- Gate 4: grep_gates_v412.sh propagation ---"
if [ ! -x "$GREP_GATES" ]; then
  echo "ERROR: $GREP_GATES not executable"
  exit 5
fi
if bash "$GREP_GATES" >/tmp/gate4.log 2>&1; then
  echo "  OK: grep_gates_v412.sh exit 0"
  echo "GATE 4 PASS"
else
  rc=$?
  echo "FAIL: grep_gates_v412.sh exit $rc"
  echo "  --- tail /tmp/gate4.log ---"
  tail -20 /tmp/gate4.log
  exit 4
fi
echo ""

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
echo "========================================================"
echo "ALL GATES PASS — Phase 101 septuple-pin SEAL intact"
echo "  Gate 0: prerequisites"
echo "  Gate 1: canonical_sha256 match (D-15)"
echo "  Gate 2: per-artifact sha256 drift-free"
echo "  Gate 3: atomic count=1 (D-23-v412)"
echo "  Gate 4: grep_gates_v412 exit 0"
echo ""
echo "signal_commit_v412 canonical_sha256: $COMPUTED"
echo "Phase 101 SEAL: INTACT"
echo "========================================================"
echo "[OK] verify_signal_commit_v412.sh: septuple-pin 7/7 anchors INTACT"
exit 0
