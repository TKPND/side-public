#!/usr/bin/env bash
# scripts/v4.11/verify_signal_commit_v411.sh
#
# v4.11 Phase 92 SEAL drift + D-17 untouched-clause verifier.
#
# Exit codes:
#   0 = all gates pass (signal_commit_v411 matches STATE.md, SEAL JSON untouched since atomic commit)
#   1 = sha256 drift (JSON content changed after SEAL)
#   2 = D-17 violation (SEAL JSON modified post atomic commit -- git log --follow count > 1)
#   3 = STATE.md missing expected signal_commit_v411 row or sextuple-pin count wrong
#   4 = prerequisite tool missing (jq / sha256sum / git / awk)
#
# Invoked by:
#   - Phase 92 Plan 03 task 2 (smoke test at creation time)
#   - Phase 95 AUDIT-02 sextuple-pin audit (cross-phase)
#   - Any CI / pre-commit hook that wants to enforce D-17

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SEAL_DIR="$REPO_ROOT/.planning/phases/92-scope-lock-pre-registration-seal/SEAL"
STATE_MD="$REPO_ROOT/.planning/STATE.md"

echo "=== Phase 92 SEAL Integrity Verifier ==="
echo "REPO_ROOT: $REPO_ROOT"
echo "SEAL_DIR:  $SEAL_DIR"
echo "STATE_MD:  $STATE_MD"
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
    exit 4
  fi
done

if [ ! -d "$SEAL_DIR" ]; then
  echo "ERROR: SEAL_DIR missing: $SEAL_DIR"
  exit 4
fi

if [ ! -f "$STATE_MD" ]; then
  echo "ERROR: STATE_MD missing: $STATE_MD"
  exit 4
fi

SEAL_JSON_COUNT=$(ls "$SEAL_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ')
if [ "$SEAL_JSON_COUNT" -eq 0 ]; then
  echo "ERROR: No *.json files found in $SEAL_DIR"
  exit 4
fi
echo "  OK: $SEAL_JSON_COUNT SEAL JSON files found"
echo "GATE 0 PASS: prerequisites satisfied"
echo ""

# -----------------------------------------------------------------------
# Gate 1: recompute signal_commit_v411 (canonical pipeline per D-15)
# -----------------------------------------------------------------------
echo "--- Gate 1: sha256 Integrity (D-15 canonical pipeline) ---"
# Canonical pipeline: sort by filename, jq -cS (compact + sorted keys), sha256sum
COMPUTED=$(cd "$SEAL_DIR" && for f in $(ls *.json | sort); do jq -cS . "$f"; done | sha256sum | cut -d' ' -f1)
echo "computed signal_commit_v411: $COMPUTED"

# -----------------------------------------------------------------------
# Gate 2: extract recorded value from STATE.md Sealed Anchors table
# -----------------------------------------------------------------------
echo ""
echo "--- Gate 2: STATE.md Recorded Value ---"
# Pattern: | signal_commit_v411 | `<64 hex chars>` | ...
RECORDED=$(grep -E "^\| signal_commit_v411 \| \`[0-9a-f]{64}\`" "$STATE_MD" | head -1 | awk -F'`' '{print $2}')
if [ -z "$RECORDED" ]; then
  echo "ERROR: signal_commit_v411 row missing or malformed in STATE.md"
  echo "  Expected pattern: | signal_commit_v411 | \`<64-hex>\` | ..."
  exit 3
fi
echo "recorded signal_commit_v411: $RECORDED"

if [ "$COMPUTED" != "$RECORDED" ]; then
  echo ""
  echo "FAIL: sha256 drift detected"
  echo "  computed: $COMPUTED"
  echo "  recorded: $RECORDED"
  echo "  Action: SEAL JSON content has changed post atomic commit. D-17 violated."
  exit 1
fi
echo "GATE 1 PASS: sha256 match (computed == recorded)"
echo ""

# -----------------------------------------------------------------------
# Gate 3: D-17 untouched clause -- each SEAL JSON has exactly 1 git commit
# -----------------------------------------------------------------------
echo "--- Gate 3: D-17 Untouched Clause (git log --follow count = 1) ---"
GATE3_FAIL=0
for f in "$SEAL_DIR"/*.json; do
  fname=$(basename "$f")
  count=$(git -C "$REPO_ROOT" log --follow --oneline "$f" | wc -l | tr -d ' ')
  if [ "$count" -ne 1 ]; then
    echo "FAIL: D-17 violation -- $fname has $count commits (expected exactly 1 atomic SEAL commit)"
    GATE3_FAIL=1
  else
    echo "  OK: $fname -- 1 commit (atomic SEAL untouched)"
  fi
done

if [ "$GATE3_FAIL" -ne 0 ]; then
  echo "GATE 3 FAIL: D-17 untouched clause violated"
  echo "  SEAL JSON was modified after atomic SEAL commit. Null-ship-v4 path triggered."
  exit 2
fi
echo "GATE 3 PASS: D-17 untouched clause honored (all SEAL JSONs have exactly 1 commit)"
echo ""

# -----------------------------------------------------------------------
# Gate 4: sextuple-pin structural check -- STATE.md has 6 anchor rows
# -----------------------------------------------------------------------
echo "--- Gate 4: Sextuple-Pin Structural (6 anchor rows in STATE.md) ---"
# Count rows matching: | <anchor_name> | `<hex>` | ...
ANCHOR_COUNT=$(grep -cE "^\| [a-zA-Z0-9_]+ \| \`[0-9a-f]+" "$STATE_MD" || true)
echo "  anchor rows found: $ANCHOR_COUNT"
if [ "$ANCHOR_COUNT" -ne 6 ]; then
  echo "FAIL: Sealed Anchors table has $ANCHOR_COUNT rows (expected 6 for sextuple-pin)"
  echo "  Expected: threshold_commit / regime_commit / sizing_exit_commit / sizing_exit_commit_v410 / signal_commit_v411 / engine_commit"
  exit 3
fi
echo "GATE 4 PASS: sextuple-pin (6 anchor rows confirmed)"
echo ""

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
echo "========================================================"
echo "ALL GATES PASS -- Phase 92 SEAL intact"
echo "  Gate 0: prerequisites satisfied"
echo "  Gate 1: sha256 match (D-15 canonical pipeline)"
echo "  Gate 3: D-17 untouched clause honored"
echo "  Gate 4: sextuple-pin (6 anchors)"
echo ""
echo "signal_commit_v411: $COMPUTED"
echo "SEAL JSONs: $SEAL_JSON_COUNT files, each with exactly 1 atomic SEAL commit"
echo "Phase 92 atomic SEAL: INTACT"
echo "========================================================"
exit 0
