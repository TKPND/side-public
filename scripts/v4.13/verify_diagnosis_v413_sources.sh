#!/usr/bin/env bash
# scripts/v4.13/verify_diagnosis_v413_sources.sh
#
# Phase 104 SHA256 round-trip verifier (DIAG-V413-03).
# data/v4.13/diagnosis_v413_sources.json の sources[] を sha256sum -c に流して全件検証。
#
# CONTEXT D-17 invariant: scripts/v4.11/ scripts/v4.12/ の verifier には触れず、
# verify_signal_commit_v412.sh の構造を duplicate copy で再実装する。
#
# exit codes:
#   0 — all sources hash-verified
#   1 — sidecar missing or sha256sum -c failed
#   5 — required tool missing in PATH

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SIDECAR="${REPO_ROOT}/data/v4.13/diagnosis_v413_sources.json"

# Gate 0: prerequisites
echo "[gate-0] checking prerequisites..."
for tool in jq sha256sum python3; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "  OK: $tool found"
  else
    echo "ERROR: required tool '$tool' not found in PATH" >&2
    exit 5
  fi
done

if [ ! -f "${SIDECAR}" ]; then
  echo "ERROR: sidecar not found: ${SIDECAR}" >&2
  exit 1
fi
echo "  OK: sidecar found at ${SIDECAR}"

# Gate 1: sha256sum -c round-trip (RESEARCH Example 3 + verify_signal_commit_v412.sh:96-117 same shape)
echo "[gate-1] sha256sum -c round-trip on $(jq '.sources | length' "${SIDECAR}") sources..."
python3 - "${SIDECAR}" <<'PYEOF' | (cd "${REPO_ROOT}" && sha256sum -c -)
import json, sys
with open(sys.argv[1]) as f:
    doc = json.load(f)
for r in doc["sources"]:
    print(f"{r['sha256']}  {r['path']}")
PYEOF

echo "[OK] verify_diagnosis_v413_sources.sh: all v4.13 source artifacts hash-verified"
exit 0
