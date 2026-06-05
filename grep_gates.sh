#!/usr/bin/env sh
# grep_gates.sh — repo root, POSIX shell
# Exit 0: all 0 matches (clean). Exit 1: violations found.
# Source: D-33
# Gates #1-7 exclude test_*.py to avoid false positives from test assertions
# referencing historical artifact paths (see Pitfall 3b).

SCOPE="scripts/v4.10"
FAIL=0

check() {
    label="$1"; pattern="$2"
    count=$(grep -rE "$pattern" "$SCOPE" --exclude="test_*.py" 2>/dev/null | wc -l)
    if [ "$count" -gt 0 ]; then
        echo "GATE FAIL [$label]: $count matches" >&2
        grep -rE "$pattern" "$SCOPE" --exclude="test_*.py" >&2
        FAIL=1
    fi
}

check "roc_auc_score"     "roc_auc_score"
check "full_kelly"        "full_kelly"
check "f_star_cap"        "f_star[[:space:]]*>[[:space:]]*0\.5"
check "m_t_cap"           "m_t[[:space:]]*>[[:space:]]*1[^0-9]"
check "p_adj_v48_ref"     "p_adj_v48"
check "p_adj_v49_ref"     "p_adj_v49"
check "regime_commit_v48" "regime_commit_v48"

# Gate 8: SEAL hash drift — uses --seal-dir + --strict (no --check flag)
SEAL_DIR=".planning/phases/88-pre-registration-seal-v4-10/88-SEAL"
python3 scripts/v4.10/compute_seal_hash_v410.py --seal-dir "$SEAL_DIR" --strict \
    || { echo "GATE FAIL [seal_drift]" >&2; FAIL=1; }

exit $FAIL
