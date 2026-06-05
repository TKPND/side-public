#!/usr/bin/env sh
# grep_gates_v411.sh — scripts/v4.11/ scope, POSIX shell
# Phase 95 SHIP-02 anti-feature scanner. Cite: D-55 (v4.11 scope).
# Exit 0: all 0 matches (clean). Exit 1: violations found.
# Gates #1-9 exclude test_*.py to avoid false positives from test assertions
# referencing historical artifact paths (see Pitfall 3b).

SCOPE="scripts/v4.11"
FAIL=0

check() {
    label="$1"; pattern="$2"
    count=$(grep -rE "$pattern" "$SCOPE" --exclude="test_*.py" --exclude="*.sh" 2>/dev/null | wc -l)
    if [ "$count" -gt 0 ]; then
        echo "GATE FAIL [$label]: $count matches" >&2
        grep -rE "$pattern" "$SCOPE" --exclude="test_*.py" --exclude="*.sh" >&2
        FAIL=1
    fi
}

check "roc_auc_score"             "roc_auc_score"
check "full_kelly"                "full_kelly"
check "f_star_cap"                "f_star[[:space:]]*>[[:space:]]*0\.5"
check "m_t_cap"                   "m_t[[:space:]]*>[[:space:]]*1[^0-9]"
check "p_adj_v48_ref"             "p_adj_v48"
check "p_adj_v49_ref"             "p_adj_v49"
check "regime_commit_v48"         "regime_commit_v48"

# Gate 8 placeholder — no pattern needed (covered by Gate 10 SEAL drift below)

# Gate 9: negative-shift look-ahead (D-55, v4.11 SHIP-02 additional pattern)
check "negative_shift_lookahead" "shift\(-[0-9]+"

# Gate 10: SEAL hash drift — Phase 92 v4.11 sextuple-pin anchor
bash scripts/v4.11/verify_signal_commit_v411.sh >/dev/null 2>&1 \
    || { echo "GATE FAIL [seal_drift_v411]" >&2; FAIL=1; }

exit $FAIL
