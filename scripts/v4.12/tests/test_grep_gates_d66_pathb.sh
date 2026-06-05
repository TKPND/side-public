#!/usr/bin/env bash
# Meta-test for grep_gates_v412.sh: D-66 errata Path B invariants (Phase 101 Plan 03 Task 2).
#
# PLAN behavior spec (lines 109-114):
#   T1: gate header count == 15 (D-66 cap)
#   T2: gate #14 pattern contains 'aiohttp' (semantic expansion proof)
#   T3: gate #15 pattern contains 'anthropic' (placeholder repurpose proof)
#   T4: prewarm_hf_cache.py exempted from gate #14 (build-time exception)
#   T5: bash scripts/v4.12/grep_gates_v412.sh exits 0 against current tree
#
# Exit 0 only if all 5 cases pass. Tap-like output.
# This file runs as the post-task hook trip-wire that fires before SEAL pre-flight (D-67).

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
GATE_FILE="$REPO_ROOT/scripts/v4.12/grep_gates_v412.sh"

PASS=0
FAIL=0
N=0

ok()    { N=$((N+1)); echo "ok $N - $1"; PASS=$((PASS+1)); }
nok()   { N=$((N+1)); echo "not ok $N - $1"; FAIL=$((FAIL+1)); }

# T1: gate count invariant — D-66 cap is 15. We assert two-way:
#   (a) script header advertises "15 gates" (claim)
#   (b) no `gate #16` reference exists anywhere (cap not silently expanded)
if grep -q '15 gates' "$GATE_FILE" && ! grep -qE '\bgate[[:space:]]*#?16\b' "$GATE_FILE"; then
    ok "gate count == 15 (D-66 cap immutable, no #16 sneak)"
else
    nok "gate count drift detected (header claim or #16 presence)"
fi

# T2: gate #14 pattern must include aiohttp (semantic expansion from gsutil-only)
if grep -E 'gsutil .*aiohttp|aiohttp.*gsutil' "$GATE_FILE" >/dev/null; then
    ok "gate #14 pattern contains 'aiohttp' (Path B semantic expansion)"
else
    nok "gate #14 pattern missing 'aiohttp' — Path B regression"
fi

# T3: gate #15 pattern must include anthropic (LLM API repurpose)
if grep -E "'[^']*anthropic[^']*'" "$GATE_FILE" >/dev/null; then
    ok "gate #15 pattern contains 'anthropic' (Path B placeholder repurpose)"
else
    nok "gate #15 pattern missing 'anthropic' — placeholder still empty"
fi

# T4: prewarm_hf_cache.py exempted from gate #14
if grep -E "exclude=.?prewarm_hf_cache\.py" "$GATE_FILE" >/dev/null; then
    ok "prewarm_hf_cache.py exempted from gate #14 (build-time exception)"
else
    nok "prewarm_hf_cache.py NOT exempted — build-time prewarm will trip the gate"
fi

# T5: end-to-end run must exit 0 against current tree
if (cd "$REPO_ROOT" && bash scripts/v4.12/grep_gates_v412.sh >/dev/null 2>&1); then
    ok "grep_gates_v412.sh exits 0 against current tree"
else
    nok "grep_gates_v412.sh exits non-zero — gate violation in current tree"
fi

echo "1..$N"
echo "# pass=$PASS fail=$FAIL"

[ "$FAIL" -eq 0 ]
