#!/usr/bin/env bash
set -euo pipefail
SPEC="${1:-config/v4.12/workload_spec_v412.json}"
[ -f "$SPEC" ] || { echo "MISSING: $SPEC" >&2; exit 2; }

SAVED=$(jq -r '._canonical_sha256' "$SPEC")
[ -n "$SAVED" ] && [ "$SAVED" != "null" ] || { echo "FAIL: ._canonical_sha256 missing or null in $SPEC" >&2; exit 3; }

COMPUTED=$(jq -cS 'del(._canonical_sha256)' "$SPEC" | sha256sum | awk '{print $1}')

if [ "$SAVED" = "$COMPUTED" ]; then
    echo "OK   workload_spec canonical sha256 match  saved=$SAVED  computed=$COMPUTED"
    exit 0
else
    echo "FAIL workload_spec canonical sha256 MISMATCH"
    echo "  saved   = $SAVED"
    echo "  computed= $COMPUTED"
    exit 1
fi
