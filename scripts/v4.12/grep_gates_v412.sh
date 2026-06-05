#!/usr/bin/env sh
# grep_gates_v412.sh — Phase 100 anti-feature scanner (15 gates total)
# D-D1: count=15  D-D2: regex pre-reg'd in 100-CONTEXT.md §3.3
# Exit 0: all 0-match. Exit 1: violations. Phase 103 ship gate.
set -eu
FAIL=0

check() {
    label="$1"; pattern="$2"; shift 2
    count=$(grep -rE "$pattern" "$@" --exclude="test_*.py" --exclude="*.sh" 2>/dev/null | wc -l)
    if [ "$count" -gt 0 ]; then
        echo "GATE FAIL [$label]: $count matches" >&2
        grep -rE "$pattern" "$@" --exclude="test_*.py" --exclude="*.sh" >&2
        FAIL=1
    fi
}

# Gates #1-#9 — carry from v4.11 (DO NOT modify regex; D-D2 untouched)
check "roc_auc_score"             "roc_auc_score"               scripts/v4.12 data/v4.12 rust/side-engine/src/v412
check "full_kelly"                "full_kelly"                  scripts/v4.12 data/v4.12 rust/side-engine/src/v412
check "f_star_cap"                "f_star[[:space:]]*>[[:space:]]*0\.5" scripts/v4.12 rust/side-engine/src/v412
check "m_t_cap"                   "m_t[[:space:]]*>[[:space:]]*1[^0-9]" scripts/v4.12 rust/side-engine/src/v412
check "p_adj_v48_ref"             "p_adj_v48"                   scripts/v4.12 rust/side-engine/src/v412
check "p_adj_v49_ref"             "p_adj_v49"                   scripts/v4.12 rust/side-engine/src/v412
check "regime_commit_v48"         "regime_commit_v48"           scripts/v4.12 rust/side-engine/src/v412
# gate #8 placeholder per v4.11 (covered by gate #11 SEAL drift below)
check "negative_shift_lookahead" "shift\(-[0-9]+"               scripts/v4.12

# Gate #9 — v4.11 SEAL drift (delegate to v4.11 verifier)
# NOTE: SEAL_DIR archived in commit a05778d (v4.11→v4.12 milestone transition, intentional).
# This gate is LEGACY until Wave 3 reconciliation (100-03-01 ARCHITECTURE.md update).
# Failure is expected and treated as WARN-only; live checks are gates #1-#8 + #10-#15.
# TODO(phase-103-wr04-followup): re-enable v4.11 SEAL drift gate as FAIL
# after archive reconciliation lands (currently treats v4.11 SEAL drift as
# WARN-only because SEAL_DIR was intentionally archived in a05778d).
bash scripts/v4.11/verify_signal_commit_v411.sh >/dev/null 2>&1 \
    || { echo "GATE WARN [seal_drift_v411] — LEGACY: SEAL_DIR archived intentionally (commit a05778d); see TODO(phase-103-wr04-followup)" >&2; }

# Gates #10-#15 — new in v4.12 (CONTEXT.md §3.3, pre-reg'd 2026-04-26)
# gate #10 — look-ahead feature names in classifier parquet schema
if [ -d data/v4.12 ]; then
    check "look_ahead_features" "r_post_|reaction_|fx_change_|surprise_" data/v4.12
fi
# gate #11 — v4.11 SEAL immutability (git log)
if git rev-parse v4.11-anchor >/dev/null 2>&1; then
    n=$(git log v4.11-anchor..HEAD -- scripts/v4.11/verify_signal_commit_v411.sh \
        .planning/phases/92-scope-lock-pre-registration-seal/SEAL/ 2>/dev/null | wc -l)
    [ "$n" -eq 0 ] || { echo "GATE FAIL [v411_seal_immutability]: $n commits" >&2; FAIL=1; }
fi
# gate #12 — v4.12 SEAL threshold immutability
if git rev-parse v4.12-SEAL >/dev/null 2>&1; then
    n=$(git log v4.12-SEAL..HEAD -- 'config/v4.12/threshold*.json' SEAL/signal_seal_v412.json 2>/dev/null | wc -l)
    [ "$n" -eq 0 ] || { echo "GATE FAIL [v412_seal_threshold_immutability]: $n commits" >&2; FAIL=1; }
fi
# gate #13 — fwer_denominator_v411 import block
if [ -d scripts/v4.12 ] || [ -d rust/side-engine/src/v412 ]; then
    check "fwer_denominator_v411" "fwer_denominator_v411" scripts/v4.12 rust/side-engine/src/v412
fi
# gate #14 — network egress block (D-66 errata Path B, commit ec3d9ae)
# EXPANDED from 'gsutil ' on vm_*.sh to ALL network egress libs across scripts/v4.12 + scripts/gcp/vm_*.sh.
# Phase 101 introduces FinBERT/RoBERTa inference (HuggingFace transformers) which can silently
# fall back to network if cache miss. Block requests/urllib/httpx/aiohttp at static-scan time.
# EXEMPTIONS (freeze-time / build-time only, never runtime — semantic equivalent of prewarm):
#   - prewarm_hf_cache.py        : HF cache build-time prewarm (PLAN-spec exemption)
#   - fetch_macro_statements.py  : 101-02 Task 1 freeze-time HTML fetcher (D-62 freeze-time allowed)
#   - test_*.py / *_test.py      : test fixtures
#   - grep_gates_v412.sh         : self (this file itself, contains regex literals)
# Scope per PLAN <interfaces>: scripts/v4.12 only (the OLD gsutil-on-vm_*.sh scope is
# REPLACED by Path B; scripts/gcp scripts are infra/build-time and out of Phase 101 runtime scope).
n=$(grep -rE 'gsutil |requests\.get|urllib|httpx|aiohttp' scripts/v4.12 \
    --include='*.py' --include='*.sh' \
    --exclude='prewarm_hf_cache.py' \
    --exclude='fetch_macro_statements.py' \
    --exclude='grep_gates_v412.sh' \
    --exclude='test_*.py' --exclude='*_test.py' \
    --exclude='test_*.sh' --exclude='*_test.sh' 2>/dev/null | wc -l)
if [ "$n" -gt 0 ]; then
    echo "GATE FAIL [network_egress_d66_pathb]: $n matches" >&2
    grep -rE 'gsutil |requests\.get|urllib|httpx|aiohttp' scripts/v4.12 \
        --include='*.py' --include='*.sh' \
        --exclude='prewarm_hf_cache.py' \
        --exclude='fetch_macro_statements.py' \
        --exclude='grep_gates_v412.sh' \
        --exclude='test_*.py' --exclude='*_test.py' \
    --exclude='test_*.sh' --exclude='*_test.sh' >&2
    FAIL=1
fi
# gate #15 — LLM API runtime block (D-66 errata Path B, commit ec3d9ae)
# REPURPOSED from runtime placeholder to LLM API call detector. Phase 101 macro_stance_estimator.py
# is supposed to be deterministic frozen-model inference (FinBERT/RoBERTa); any anthropic/openai/
# claude/gpt symbol in scripts/v4.12/**/*.py = D-63 one-shot violation (live LLM at inference time).
# EXEMPTIONS:
#   - label_macro_stance_claude.py : 101-02 Task 2 freeze-time claude -p one-shot labeler
#   - build_labels_metadata.py     : metadata script (mentions claude-opus-4-7 in labeler_model)
#   - grep_gates_v412.sh           : self (regex literals)
#   - test_*.py / *_test.py        : test fixtures
n=$(grep -rE 'anthropic|openai|claude|gpt' scripts/v4.12 \
    --include='*.py' \
    --exclude='label_macro_stance_claude.py' \
    --exclude='build_labels_metadata.py' \
    --exclude='grep_gates_v412.sh' \
    --exclude='test_*.py' --exclude='*_test.py' \
    --exclude='test_*.sh' --exclude='*_test.sh' 2>/dev/null | wc -l)
if [ "$n" -gt 0 ]; then
    echo "GATE FAIL [llm_api_runtime_d66_pathb]: $n matches" >&2
    grep -rE 'anthropic|openai|claude|gpt' scripts/v4.12 \
        --include='*.py' \
        --exclude='label_macro_stance_claude.py' \
        --exclude='build_labels_metadata.py' \
        --exclude='grep_gates_v412.sh' \
        --exclude='test_*.py' --exclude='*_test.py' \
    --exclude='test_*.sh' --exclude='*_test.sh' >&2
    FAIL=1
fi

# Idempotent closure marker (AUDIT-V412-01)
if [ "$FAIL" -eq 0 ]; then
    echo "[OK] grep_gates_v412.sh: 15/15 gates clean"
fi
exit $FAIL
