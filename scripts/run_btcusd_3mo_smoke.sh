#!/usr/bin/env bash
# run_btcusd_3mo_smoke.sh — BTCUSD smoke fetch via Python dukascopy-python (Phase 108 D-08)
#
# Usage:
#   bash scripts/run_btcusd_3mo_smoke.sh --pair BTCUSD --start 2026-01-28 --end 2026-04-28
#
# NOTE: side-engine CLI has no fetch subcommand (only scan). Fetch uses Python
#       dukascopy-python via scripts/fetch_ticks.py which returns bid/ask columns
#       required by the SMOKE-REPORT.md spec. (Phase 108 D-08 / advisor decision)
#
# Output:
#   data/poc/btcusd_smoke_3mo_YYYYMMDD/  — monthly tick parquet files
#   data/poc/btcusd_smoke_3mo_YYYYMMDD.log

set -euo pipefail

PAIR="BTCUSD"
START="2026-01-28"
END="2026-04-28"
DATESTAMP="$(date +%Y%m%d)"
OUT_DIR="data/poc/btcusd_smoke_3mo_${DATESTAMP}"
LOG_FILE="data/poc/btcusd_smoke_3mo_${DATESTAMP}.log"

# --- arg parse ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --pair)   PAIR="$2";  shift 2;;
    --start)  START="$2"; shift 2;;
    --end)    END="$2";   shift 2;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

mkdir -p "data/poc"
mkdir -p "${OUT_DIR}"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting smoke: pair=${PAIR} start=${START} end=${END}" | tee -a "${LOG_FILE}"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Output dir: ${OUT_DIR}" | tee -a "${LOG_FILE}"

T0=$(date +%s)

# Fetch tick parquet (monthly files) via Python dukascopy-python
# fetch_ticks.py writes monthly parquets: {out_dir}/{pair.lower()}_ticks_YYYY-MM.parquet
# NOTE: using python3 (pyenv 3.13.2) where dukascopy-python is installed.
#       uv run isolation breaks because dukascopy-python is not in project pyproject.toml.
python3 scripts/fetch_ticks.py \
    --pair "${PAIR}" \
    --start "${START}" \
    --end "${END}" \
    --source dukascopy \
    --interval tick \
    --out "${OUT_DIR}" \
    2>&1 | tee -a "${LOG_FILE}"

T1=$(date +%s)
ELAPSED=$(( T1 - T0 ))

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Fetch complete in ${ELAPSED}s" | tee -a "${LOG_FILE}"

# List output files
PARQUET_COUNT=$(find "${OUT_DIR}" -name "*.parquet" 2>/dev/null | wc -l)
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Parquet files written: ${PARQUET_COUNT}" | tee -a "${LOG_FILE}"

if [[ "${PARQUET_COUNT}" -eq 0 ]]; then
    echo "[ERROR] No parquet files produced — check log: ${LOG_FILE}" | tee -a "${LOG_FILE}"
    exit 1
fi

ls -lh "${OUT_DIR}"/*.parquet 2>/dev/null | tee -a "${LOG_FILE}"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Smoke fetch done. Output: ${OUT_DIR}" | tee -a "${LOG_FILE}"
