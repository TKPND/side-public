#!/usr/bin/env bash
# wfd_rerun_repro_check.sh — D-A2 masked-sha256 strict-match comparator
#
# Usage: wfd_rerun_repro_check.sh DIR1 DIR2
#   DIR1, DIR2: two output directories produced by `wfd-rerun`, each containing
#               <pair>/<event>/report.json files.
#
# Behaviour:
#   - For every report.json found under DIR1 (sorted deterministically), compute
#     the D-A2 masked-sha256: jq -cS '.data_provenance = "<MASKED>"' | sha256sum
#   - Compare against the corresponding file in DIR2.
#   - Emit:  MATCH     <rel-path>              (hashes agree)
#            MISMATCH  <rel-path>  H1 vs H2    (hashes differ)
#            MISSING   <rel-path> in DIR2      (file absent in DIR2)
#   - Final summary line: STRICT_MATCH=1 (all matched) or STRICT_MATCH=0
#   - Exit 0 if STRICT_MATCH=1, exit 1 otherwise.
#
# Design notes:
#   - jq -cS: compact + sorted-keys → canonical bytes (D-A2 recipe)
#   - .data_provenance = "<MASKED>" cancels wall-clock/sha drift
#     (P-14 / wfd_rerun.rs:108-112)
#   - File order: `find … | sort` ensures deterministic manifest
#
# Dependencies: bash, jq, sha256sum, find, sort, awk
#
# Citations: D-A2 (canonical bytes recipe), PATTERNS.md §investigate_wfd_nondeterminism.sh,
#            100-PLAN.md Task 100-00-02

set -euo pipefail

DIR1="${1:?usage: wfd_rerun_repro_check.sh DIR1 DIR2}"
DIR2="${2:?usage: wfd_rerun_repro_check.sh DIR1 DIR2}"

if [ ! -d "$DIR1" ]; then
    echo "ERROR: DIR1 not a directory: $DIR1" >&2
    exit 2
fi
if [ ! -d "$DIR2" ]; then
    echo "ERROR: DIR2 not a directory: $DIR2" >&2
    exit 2
fi

mask_hash() {
    jq -cS '.data_provenance = "<MASKED>"' "$1" | sha256sum | awk '{print $1}'
}

STRICT=1
TOTAL=0
MATCHED=0

while IFS= read -r f; do
    rel="${f#./}"
    abs1="$DIR1/$rel"
    abs2="$DIR2/$rel"
    TOTAL=$((TOTAL + 1))

    if [ ! -f "$abs2" ]; then
        echo "MISSING  $rel in $DIR2"
        STRICT=0
        continue
    fi

    h1=$(mask_hash "$abs1")
    h2=$(mask_hash "$abs2")

    if [ "$h1" = "$h2" ]; then
        echo "MATCH    $rel"
        MATCHED=$((MATCHED + 1))
    else
        echo "MISMATCH $rel  $h1 vs $h2"
        STRICT=0
    fi
done < <(cd "$DIR1" && find . -name report.json | sort)

echo "---"
echo "TOTAL=$TOTAL  MATCHED=$MATCHED"
echo "STRICT_MATCH=$STRICT"

[ "$STRICT" = "1" ] || exit 1
