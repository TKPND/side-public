#!/usr/bin/env bash
# Phase 94 PARITY-V411-01 check — nested 6 fields bit-exact diff.
#
# Compares:
#   reports/v4.10/v4_10_ship_decision.json (baseline)
#   reports/v4.11/neutral_mode/v4_11_ship_decision.json (neutral-mode PARITY emit)
#
# On 6-field match: exit 0 (PARITY pass).
# On diff non-empty or files missing: exit 1 + stderr diff hunk (CI/pre-commit gate).
#
# D-37 revised: compare nested .ship_metrics.* 6 fields (NOT top-level null).
# D-40: v4.11 target is neutral_mode/v4_11_ship_decision.json (active_mode is Phase 95).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
V410="${REPO_ROOT}/reports/v4.10/v4_10_ship_decision.json"
V411="${REPO_ROOT}/reports/v4.11/neutral_mode/v4_11_ship_decision.json"

if [[ ! -f "${V410}" ]]; then
    echo "ERROR: v4.10 baseline missing: ${V410}" >&2
    exit 1
fi
if [[ ! -f "${V411}" ]]; then
    echo "ERROR: v4.11 neutral-mode emit missing: ${V411}" >&2
    echo "Run: uv run python scripts/v4.11/parity_neutral_emit.py" >&2
    exit 1
fi

# D-37 revised: nested 6 fields selector (canonical sorted via -cS).
JQ_EXPR='{
  ec: .ship_metrics.edge_count_p_adj_005,
  sv: .ship_metrics.ship_verdict,
  ct: .ship_metrics.coverage_tier,
  dp: .ship_metrics.data_provenance,
  ts: .ship_metrics.primary_metrics.turnover_sharpe_median,
  es: .ship_metrics.primary_metrics.es_median
}'

V410_CANONICAL="$(jq -cS "${JQ_EXPR}" "${V410}")"
V411_CANONICAL="$(jq -cS "${JQ_EXPR}" "${V411}")"

if [[ "${V410_CANONICAL}" == "${V411_CANONICAL}" ]]; then
    echo "[parity_v411_01_check] PASS: nested 6 fields bit-exact"
    echo "[parity_v411_01_check] canonical: ${V410_CANONICAL}"
    exit 0
fi

echo "[parity_v411_01_check] FAIL: diff below" >&2
diff <(echo "${V410_CANONICAL}" | jq .) <(echo "${V411_CANONICAL}" | jq .) >&2 || true
exit 1
