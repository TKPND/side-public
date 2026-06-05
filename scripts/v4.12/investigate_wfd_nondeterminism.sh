#!/usr/bin/env bash
set -euo pipefail
OUT="${1:-/tmp/wfd_nondeterminism_rca}"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

# Cost guard: real wfd-rerun execution takes ~3-5 min per run × up to 3 runs
if [ "${I_KNOW_THIS_TAKES_TIME:-0}" != "1" ] && [ "${DRY_RUN:-0}" != "1" ]; then
    echo 'ERROR: Set I_KNOW_THIS_TAKES_TIME=1 to run real wfd-rerun probes (~15 min total).' >&2
    echo '       Set DRY_RUN=1 to emit dummy summary JSON without executing cargo.' >&2
    exit 1
fi

if [ "${DRY_RUN:-0}" = "1" ]; then
    log "DRY_RUN=1: skipping real cargo execution, emitting dummy summary JSON"
    log "Would run: cargo run --release -p side-cli -- wfd-rerun --pair usdjpy --event fomc --output-dir $OUT/run1"
    log "Would run: cargo run --release -p side-cli -- wfd-rerun --pair usdjpy --event fomc --output-dir $OUT/run2"
    log "Would run: bash scripts/wfd_rerun_repro_check.sh $OUT/run1 $OUT/run2"
    log "Would conditionally run: RAYON_NUM_THREADS=1 cargo run ... --output-dir $OUT/run3_singlethread"
    printf '{"h1_h2_masked_sha256_strict_match": "DRY_RUN",\n "h3_h4_singlethread_strict_match": "DRY_RUN",\n "out_dir": "%s",\n "rca_branch_outcome": "DRY_RUN_pending_real_probe"}\n' "$OUT"
    exit 0
fi

rm -rf "$OUT" && mkdir -p "$OUT/run1" "$OUT/run2" "$OUT/run3_singlethread"

# H1+H2 probe: data_provenance string drift + serde_json key ordering — both cancelled by jq -cS mask
log "H1+H2: running wfd-rerun twice (default config) → masked-sha256 should match if H1+H2 only"
cargo run --release -p side-cli -- wfd-rerun --pair usdjpy --event fomc --output-dir "$OUT/run1"
cargo run --release -p side-cli -- wfd-rerun --pair usdjpy --event fomc --output-dir "$OUT/run2"
bash scripts/wfd_rerun_repro_check.sh "$OUT/run1" "$OUT/run2" | tee "$OUT/h1_h2_result.txt" || true
H1_H2_STRICT=$(grep -E '^STRICT_MATCH' "$OUT/h1_h2_result.txt" | tail -1 | cut -d= -f2)

# H3/H4 probe: rand thread-locality / parallel float reduction
if [ "$H1_H2_STRICT" = "0" ]; then
    log "H3/H4: re-running with RAYON_NUM_THREADS=1 to test parallel-reduction hypothesis"
    RAYON_NUM_THREADS=1 cargo run --release -p side-cli -- wfd-rerun --pair usdjpy --event fomc --output-dir "$OUT/run3_singlethread"
    bash scripts/wfd_rerun_repro_check.sh "$OUT/run1" "$OUT/run3_singlethread" | tee "$OUT/h3_h4_result.txt" || true
    H3_H4_STRICT=$(grep -E '^STRICT_MATCH' "$OUT/h3_h4_result.txt" | tail -1 | cut -d= -f2)
else
    log "H1/H2 already strict-match — H3/H4 probe skipped (no nondeterminism observed)"
    H3_H4_STRICT="N/A"
fi

# Emit summary to stdout (consumed by Task 100-02-04 docs writeup)
cat <<EOF
{"h1_h2_masked_sha256_strict_match": "$H1_H2_STRICT",
 "h3_h4_singlethread_strict_match": "$H3_H4_STRICT",
 "out_dir": "$OUT",
 "rca_branch_outcome": "$(if [ "$H1_H2_STRICT" = "1" ]; then echo "H1+H2_only_residue_resolved"; elif [ "$H3_H4_STRICT" = "1" ]; then echo "H3_or_H4_isolated_to_parallel_reduction"; else echo "H3+H4_unresolved_residue_acknowledged"; fi)"}
EOF
