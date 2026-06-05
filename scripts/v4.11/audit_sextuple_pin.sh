#!/usr/bin/env bash
# scripts/v4.11/audit_sextuple_pin.sh
#
# Phase 95 AUDIT-02 — sextuple-pin drift audit (D-56).
#
# Extends scripts/v4.11/verify_signal_commit_v411.sh (Gates 0-4) with:
#   Gate 5: per-anchor STATE.md short-SHA cross-reference for all 6 sextuple-pin
#           anchors, plus D-17 untouched check on Phase 93/94 carry-in artifacts.
#
# Sextuple-pin (6 anchors — D-16 engine_commit is the 6th pin):
#   threshold_commit          = 6527cbc  (v4.7 Phase 74)
#   regime_commit             = 90bf4b2  (v4.8 Phase 79)
#   sizing_exit_commit        = 8a4e49d… (v4.9 Phase 85)
#   sizing_exit_commit_v410   = a5f7183… (v4.10 Phase 88)
#   signal_commit_v411        = f8ccc8a… (v4.11 Phase 92 atomic SEAL)
#   engine_commit             = a5a1102  (v4.7 Phase 74 — D-16 6th pin)
#
# Exit codes:
#   0 = all gates pass (6 anchors match STATE.md + Phase 93/94 carry-in untouched)
#   1 = sha256 drift (Gate 1)
#   2 = D-17 violation — SEAL JSON modified post atomic commit (Gate 3)
#   3 = STATE.md structural error (Gate 2 or Gate 4)
#   4 = prerequisite tool missing (Gate 0)
#   5 = sextuple-pin anchor drift or carry-in violation (Gate 5)
#
# Env overrides (for testability):
#   AUDIT_STATE_MD  — path to STATE.md (default: $REPO_ROOT/.planning/STATE.md)
#
# Invoked by:
#   - Phase 95 Plan 4 (AUDIT-02 closure)
#   - tests/v4.11/test_audit_sextuple_pin.py
#   - any CI hook that wants to enforce sextuple-pin integrity

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SEAL_DIR="$REPO_ROOT/.planning/phases/92-scope-lock-pre-registration-seal/SEAL"
STATE_MD="${AUDIT_STATE_MD:-$REPO_ROOT/.planning/STATE.md}"

echo "=== Phase 95 Sextuple-Pin Audit (AUDIT-02, D-56) ==="
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
# Gate 1: recompute signal_commit_v411 (D-15 canonical pipeline)
# -----------------------------------------------------------------------
echo "--- Gate 1: sha256 Integrity (D-15 canonical pipeline) ---"
COMPUTED=$(cd "$SEAL_DIR" && for f in $(ls *.json | sort); do jq -cS . "$f"; done | sha256sum | cut -d' ' -f1)
echo "computed signal_commit_v411: $COMPUTED"

# -----------------------------------------------------------------------
# Gate 2: extract recorded value from STATE.md Sealed Anchors table
# -----------------------------------------------------------------------
echo ""
echo "--- Gate 2: STATE.md Recorded Value ---"
RECORDED=$(grep -E "^\| signal_commit_v411 \| \`[0-9a-f]{64}\`" "$STATE_MD" | head -1 | awk -F'`' '{print $2}')
if [ -z "$RECORDED" ]; then
  echo "ERROR: signal_commit_v411 row missing or malformed in STATE.md"
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
# Gate 3: D-17 untouched clause — each SEAL JSON has exactly 1 git commit (--follow OK)
# -----------------------------------------------------------------------
echo "--- Gate 3: D-17 Untouched Clause (SEAL JSON, git log --follow count = 1) ---"
GATE3_FAIL=0
for f in "$SEAL_DIR"/*.json; do
  fname=$(basename "$f")
  count=$(git -C "$REPO_ROOT" log --follow --oneline -- "$f" | wc -l | tr -d ' ')
  if [ "$count" -ne 1 ]; then
    echo "FAIL: D-17 violation -- $fname has $count commits (expected exactly 1 atomic SEAL commit)"
    GATE3_FAIL=1
  else
    echo "  OK: $fname -- 1 commit (atomic SEAL untouched)"
  fi
done

if [ "$GATE3_FAIL" -ne 0 ]; then
  echo "GATE 3 FAIL: D-17 untouched clause violated"
  exit 2
fi
echo "GATE 3 PASS: D-17 untouched clause honored"
echo ""

# -----------------------------------------------------------------------
# Gate 4: sextuple-pin structural (6 anchor rows in STATE.md)
# -----------------------------------------------------------------------
echo "--- Gate 4: Sextuple-Pin Structural (6 anchor rows in STATE.md) ---"
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
# Gate 5: Sextuple-Pin per-anchor drift audit (D-56, Phase 95 AUDIT-02)
# -----------------------------------------------------------------------
echo "--- Gate 5: Sextuple-Pin Per-Anchor Drift Audit ---"
GATE5_FAIL=0

declare_anchor() {
  name="$1"
  expected_short="$2"
  actual=$(grep -E "^\| $name \| \`" "$STATE_MD" | head -1 | awk -F'`' '{print $2}')
  if [ -z "$actual" ]; then
    echo "FAIL: anchor $name missing in STATE.md"
    GATE5_FAIL=1
    return
  fi
  actual_short=$(echo "$actual" | cut -c1-7)
  if [ "$actual_short" != "$expected_short" ]; then
    echo "FAIL: $name STATE.md mismatch (expected $expected_short, got $actual_short)"
    GATE5_FAIL=1
  else
    echo "  OK: $name = $actual_short (STATE.md match)"
  fi
}

declare_anchor threshold_commit 6527cbc
declare_anchor regime_commit 90bf4b2
declare_anchor sizing_exit_commit 8a4e49d
declare_anchor sizing_exit_commit_v410 a5f7183
declare_anchor signal_commit_v411 f8ccc8a
declare_anchor engine_commit a5a1102

# Phase 93/94 carry-in artifacts — D-17 untouched check.
# NOTE: `git log` WITHOUT --follow is intentional. --follow walks through rename
# history via content similarity, which for regenerated artifacts (e.g. the
# neutral_mode ship_decision.json that inherits similarity-links from Phase 90/91
# v4.10 emits) produces false-positive commit counts. The literal question is:
# "was this exact path modified during Phase 95?" — for that, plain `git log`
# on the fixed path is correct. Gate 3 keeps --follow because SEAL JSON paths
# have no rename ancestry (they were born at the pre-reg atomic commit).
for p in \
    "data/v4.11/vol_per_slot.parquet" \
    "data/v4.11/cells_post_filter.parquet" \
    "reports/v4.11/neutral_mode/v4_11_ship_decision.json"; do
  count=$(git -C "$REPO_ROOT" log --oneline -- "$p" | wc -l | tr -d ' ')
  if [ "$count" -gt 1 ]; then
    echo "FAIL: D-17 carry-in violation -- $p has $count commits at this literal path (expected 1)"
    GATE5_FAIL=1
  else
    echo "  OK: $p -- $count commit(s) at literal path (carry-in untouched)"
  fi
done

if [ "$GATE5_FAIL" -ne 0 ]; then
  echo "GATE 5 FAIL: sextuple-pin anchor drift or carry-in violation detected"
  exit 5
fi
echo "GATE 5 PASS: all 6 anchors match STATE.md, Phase 93/94 carry-in untouched"
echo ""

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
echo "========================================================"
echo "ALL GATES PASS -- Sextuple-pin INTACT"
echo "  Gate 0: prerequisites satisfied"
echo "  Gate 1: sha256 match (D-15 canonical pipeline)"
echo "  Gate 2: STATE.md recorded value matches"
echo "  Gate 3: D-17 untouched clause honored (SEAL JSONs --follow=1)"
echo "  Gate 4: sextuple-pin structural (6 rows)"
echo "  Gate 5: per-anchor drift audit + carry-in D-17 check"
echo ""
echo "signal_commit_v411: $COMPUTED"
echo "Anchors verified: threshold_commit=6527cbc / regime_commit=90bf4b2 /"
echo "                  sizing_exit_commit=8a4e49d / sizing_exit_commit_v410=a5f7183 /"
echo "                  signal_commit_v411=f8ccc8a / engine_commit=a5a1102"
echo "Carry-in untouched: vol_per_slot.parquet / cells_post_filter.parquet /"
echo "                    neutral_mode/v4_11_ship_decision.json"
echo "Phase 95 AUDIT-02: SATISFIED"
echo "========================================================"
exit 0
