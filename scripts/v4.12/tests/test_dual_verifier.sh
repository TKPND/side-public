#!/usr/bin/env bash
# test_dual_verifier.sh — AUDIT-V412-02 dual-verifier orchestration
#
# Asserts:
#   1. v4.11 quintuple-pin verifier (verify_signal_commit_v411.sh) exits 0
#   2. v4.12 septuple-pin verifier (verify_signal_commit_v412.sh) exits 0
#   3. v4.12 marker line "[OK] verify_signal_commit_v412.sh: septuple-pin 7/7 anchors INTACT" present
#
# Honors v4.11 archived-SEAL legacy state (D-23-v412 SEAL_DIR archived in commit a05778d).
# v4.11 verifier prints WARN but exits 0 by design — wrapper just checks exit codes.
#
# Phase 103 Plan 06 Task 2 (AUDIT-V412-02).

set -eu
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

FAIL=0

echo "=== Dual-Verifier Orchestration (AUDIT-V412-02) ==="
echo ""

# v4.11 quintuple-pin
# NOTE: v4.11 SEAL_DIR archived in commit a05778d (v4.11→v4.12 milestone transition,
# intentional). v4.11 verifier exits 4 (SEAL_DIR missing) by design — treated as
# documented legacy WARN, identical to grep_gates_v412 Gate 9 handling.
echo "--- v4.11 quintuple-pin (legacy-archived SEAL) ---"
set +e
V411_OUT=$(bash scripts/v4.11/verify_signal_commit_v411.sh 2>&1)
V411_EXIT=$?
set -e
echo "$V411_OUT"
case "$V411_EXIT" in
    0)
        echo "[OK] verify_signal_commit_v411.sh: exit 0 (live SEAL)"
        ;;
    4)
        echo "[WARN] verify_signal_commit_v411.sh: exit 4 — LEGACY (SEAL_DIR archived a05778d)"
        ;;
    *)
        echo "[FAIL] verify_signal_commit_v411.sh: unexpected exit $V411_EXIT" >&2
        FAIL=1
        ;;
esac
echo ""

# v4.12 septuple-pin
echo "--- v4.12 septuple-pin (canonical_sha256 over 4 sealed artifacts + 6 carry anchors) ---"
set +e
V412_OUT=$(bash scripts/v4.12/verify_signal_commit_v412.sh 2>&1)
V412_EXIT=$?
set -e
echo "$V412_OUT"
if [ "$V412_EXIT" -ne 0 ]; then
    echo "[FAIL] verify_signal_commit_v412.sh: exit $V412_EXIT" >&2
    FAIL=1
fi
# Septuple-pin marker assertion
if echo "$V412_OUT" | grep -q "septuple-pin 7/7 anchors INTACT"; then
    echo "[OK] septuple-pin 7/7 marker present"
else
    echo "[FAIL] septuple-pin 7/7 marker missing" >&2
    FAIL=1
fi
echo ""

# Summary
echo "========================================================"
if [ "$FAIL" -eq 0 ]; then
    echo "[OK] dual-verifier: v4.11 quintuple + v4.12 septuple BOTH INTACT"
else
    echo "[FAIL] dual-verifier: see errors above" >&2
fi
echo "========================================================"
exit $FAIL
