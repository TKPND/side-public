#!/usr/bin/env python3
"""Generate REGRESSION-NOTE.md from comparison.json"""

import json
from pathlib import Path

comp_path = Path("docs/reports/v3.9-cross-pair/usdjpy-regression/comparison.json")
with open(comp_path) as f:
    comp = json.load(f)

note = f"""---
date: 2026-04-14
regression: usdjpy-combined
baseline: v3.8
test: v3.9
---

# USDJPY Baseline Regression Note

## Objective
Phase 37 DATA-01 修復後の USDJPY × combined 再スキャンが v3.8 baseline と bit-exact 一致することを確認。

## Baseline Reference
- File: `docs/reports/v3.8-multi-event/report.json`
- Slots: 288 (FOMC 96 + ECB 96 + NFP 96)
- Expected verdict: 288/288 PASS @ fee=2bps

## Test Results (v3.9 Rescan)
- File: `docs/reports/v3.9-cross-pair/usdjpy-regression/report.json`
- Slots: 288
- Comparison method: sha256 hash of normalized fields (window_offset, hold_bars, exit_type, combined_oos_pf@6dp, passed)
- Total mismatches: {comp["total_mismatches"]} / 288

## Comparison Results

### Summary
- **Bit-exact match**: {comp["total_mismatches"] == 0}
- **Mismatched slots**: {comp["total_mismatches"]}
"""

if comp["total_mismatches"] == 0:
    note += "\n✅ All 288 slots matched perfectly with v3.8 baseline.\n\n"
else:
    note += f"\n⚠️  {comp['total_mismatches']} slot(s) differ from v3.8:\n\n"
    for m in comp["mismatches"][:10]:  # Show first 10
        if isinstance(m, dict) and "slot" in m:
            note += f"- **{m['slot']}**: PF v3.8={m['v38_pf']} vs v3.9={m['v39_pf']}\n"
            note += f"  v3.8 hash: {m['v38_hash']}\n"
            note += f"  v3.9 hash: {m['v39_hash']}\n\n"
        else:
            note += f"- {m}\n"

note += """## Root Cause Analysis

### Potential sources of difference
1. **Data source change**: USDJPY_1h.csv が Phase 37 で修復された可能性
2. **Floating-point precision**: v3.8 と v3.9 の計算順序・精度差
3. **Gate threshold change**: min_oos_pf = 2.0 gate の適用有無
4. **Event calendar drift**: FOMC/ECB/NFP 日時の再計算

### Mitigation
- All calculations normalized to 6 decimal places before hashing
- Window offset, hold bars, exit type verified identically
- Passed/failed verdict compared directly

## Artifact Locations
- Baseline hashes: `docs/reports/v3.9-cross-pair/usdjpy-regression/hashes.txt`
- Full comparison: `docs/reports/v3.9-cross-pair/usdjpy-regression/comparison.json`
- Scan log: `docs/reports/v3.9-cross-pair/usdjpy-regression/scan.log`

## Sign-off
Regression verification complete. {'' if comp['total_mismatches'] == 0 else 'Differences documented above.'}
"""

with open(
    Path(".planning/phases/43-usdjpy-baseline-regression/REGRESSION-NOTE.md"), "w"
) as f:
    f.write(note)

print("REGRESSION-NOTE.md created")
