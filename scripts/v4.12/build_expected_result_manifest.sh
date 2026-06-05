#!/usr/bin/env bash
set -euo pipefail
OUT_DIR="${1:?usage: build_expected_result_manifest.sh OUT_DIR > expected_result_manifest_v412.json}"
SCHEMA_VERSION="v4.12"
MASK_RECIPE='jq -cS .data_provenance="<MASKED>" | sha256sum'

mask_hash() { jq -cS '.data_provenance = "<MASKED>"' "$1" | sha256sum | awk '{print $1}'; }

REPORTS=()
while IFS= read -r f; do REPORTS+=("$f"); done < <(cd "$OUT_DIR" && find . -name report.json | sort)

{
  printf '{\n'
  printf '  "schema_version": "%s",\n' "$SCHEMA_VERSION"
  printf '  "mask_recipe": %s,\n' "$(printf '%s' "$MASK_RECIPE" | jq -Rs .)"
  printf '  "generated_from_dir": %s,\n' "$(printf '%s' "$OUT_DIR" | jq -Rs .)"
  printf '  "report_json_count": %d,\n' "${#REPORTS[@]}"
  printf '  "report_json_masked_sha256_list": [\n'
  first=1
  for f in "${REPORTS[@]}"; do
      rel="${f#./}"
      h=$(mask_hash "$OUT_DIR/$rel")
      [ "$first" = "1" ] || printf ',\n'
      printf '    {"path": %s, "masked_sha256": "%s"}' "$(printf '%s' "$rel" | jq -Rs .)" "$h"
      first=0
  done
  printf '\n  ]\n'
  printf '}\n'
} | jq -cS .
